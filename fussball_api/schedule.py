import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from .cache import fetch_url
from .crawler import FUSSBALL_DE_BASE_URL, _make_soup
from .schemas import ScheduleGame, TeamSchedule

logger = logging.getLogger(__name__)

_SURFACE_PREFIXES = ("Kunstrasenplatz", "Rasenplatz")
_POSTAL_CITY = re.compile(r"^(\d{5}) (.+)$")
_CANCELLED_MARKERS = ("absetzung", "abgesetzt", "abgesagt", "ausfall", "annulliert")
_TEAM_ID = re.compile(r"/team-id/([^/?#]+)")
_LOCAL_TZ = ZoneInfo("Europe/Berlin")
_MATCHPLAN_MARKER = "id-team-matchplan-table"
# A rescheduled game is listed a second time at its old slot, with the new kick-off as info text
_MOVED_TO = re.compile(r"^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$")


def detect_surface(venue: str) -> Optional[str]:
    """
    Detects the pitch surface from a fussball.de venue text.

    :param venue: The raw venue text.
    :return: "KUNSTRASEN", "RASEN" or None if the text gives no hint.
    """
    # Artificial turf first: "Kunstrasenplatz" also contains "rasenplatz".
    if venue.startswith("Kunstrasen") or "(KR)" in venue:
        return "KUNSTRASEN"
    if venue.startswith("Rasenplatz") or "(RP)" in venue:
        return "RASEN"
    return None


def split_venue(venue: str) -> Dict[str, Optional[str]]:
    """
    Splits a venue text into name, street, postal code and city.

    :param venue: The raw venue text, e.g. "Rasenplatz, Sportplatz X (RP), Straße 1, 12345 Ort".
    :return: A dict with the keys venue_name, venue_street, venue_postal_code and venue_city.
    """
    parts = [part.strip() for part in venue.split(",") if part.strip()]
    if parts and parts[0] in _SURFACE_PREFIXES:
        parts = parts[1:]

    result: Dict[str, Optional[str]] = {
        "venue_name": None,
        "venue_street": None,
        "venue_postal_code": None,
        "venue_city": None,
    }
    if len(parts) >= 2:
        match = _POSTAL_CITY.match(parts[-1])
        if match:
            result["venue_postal_code"], result["venue_city"] = match.groups()
            parts = parts[:-1]
            if len(parts) >= 2:
                result["venue_street"] = parts[-1]
                parts = parts[:-1]
    if parts:
        result["venue_name"] = ", ".join(parts)
    return result


def is_cancelled_status(status: Optional[str]) -> bool:
    """
    Checks whether a fussball.de status text marks a game as cancelled.

    :param status: The raw status text, may be None.
    :return: True for texts like "Absetzung", "abgesagt" or "Spielausfall".
    """
    if not status:
        return False
    lowered = status.lower()
    return any(marker in lowered for marker in _CANCELLED_MARKERS)


def _parse_headline(text: str) -> Optional[Dict]:
    """
    Parses a headline like "Samstag, 24.10.2026 - 17:00 Uhr | Bezirksliga".

    :param text: The headline text.
    :return: A dict with local_dt and competition, or None if it cannot be parsed.
    """
    date_part, _, rest = text.partition(" - ")
    parts = [part.strip() for part in rest.split(" | ")]
    try:
        date_str = date_part.split(", ")[1].strip()
        time_str = parts[0].replace("Uhr", "").strip()
        naive_dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
    except (IndexError, ValueError):
        return None
    competition = parts[-1] if len(parts) > 1 and parts[-1] else None
    return {"local_dt": naive_dt.replace(tzinfo=_LOCAL_TZ), "competition": competition}


def _club_side(cell) -> Optional[Dict[str, Optional[str]]]:
    """
    Reads name and team id of one club cell of a game row.

    :param cell: The td.column-club tag.
    :return: A dict with name and team_id, or None if the cell has no name.
    """
    name_tag = cell.find(class_="club-name")
    if not name_tag:
        return None
    team_id = None
    link = cell.find("a", href=True)
    if link:
        match = _TEAM_ID.search(link["href"])
        team_id = match.group(1) if match else None
    return {"name": name_tag.get_text(strip=True), "team_id": team_id}


def parse_schedule_html(html: str, team_id: str) -> List[ScheduleGame]:
    """
    Parses the fussball.de team matchplan (with venues) into schedule games.

    :param html: The HTML of ajax.team.matchplan with show-venues/true.
    :param team_id: The id of the requested team.
    :return: The games in the order fussball.de lists them.
    :raises ValueError: If the page is no matchplan or a headline or game row cannot be parsed,
        so that a broken page never looks like a shorter schedule.
    """
    if _MATCHPLAN_MARKER not in html:
        raise ValueError("Page is not a team matchplan.")
    soup = _make_soup(html)
    games: List[ScheduleGame] = []
    headline: Optional[Dict] = None
    last_game: Optional[ScheduleGame] = None

    for row in soup.find_all("tr"):
        classes = row.get("class", [])
        if "row-headline" in classes:
            headline = _parse_headline(row.get_text(" ", strip=True))
            if headline is None:
                raise ValueError(f"Could not parse schedule headline: '{row.get_text(strip=True)}'")
            last_game = None
            continue

        if "row-venue" in classes:
            venue = row.get_text(" ", strip=True)
            if last_game is not None and venue:
                last_game.venue = venue
                last_game.surface = detect_surface(venue)
                for key, value in split_venue(venue).items():
                    setattr(last_game, key, value)
            last_game = None
            continue

        score_cell = row.find("td", class_="column-score")
        if not score_cell or headline is None:
            continue

        club_cells = row.find_all("td", class_="column-club")
        sides = [_club_side(cell) for cell in club_cells]
        link = score_cell.find("a", href=True)
        if len(sides) != 2 or None in sides or not link:
            raise ValueError(f"Could not parse schedule game row for team {team_id}.")

        status_tag = score_cell.find("span", class_="info-text")
        status = status_tag.get_text(strip=True) if status_tag else None
        if status and _MOVED_TO.match(status):
            # Old slot of a rescheduled game; the game is listed again at its new slot.
            last_game = None
            continue

        home, away = sides
        is_home = home["team_id"] == team_id
        local_dt = headline["local_dt"]

        last_game = ScheduleGame(
            id=link["href"].strip("/").split("/")[-1],
            team_id=team_id,
            is_home=is_home,
            home_team=home["name"],
            away_team=away["name"],
            home_team_id=home["team_id"],
            away_team_id=away["team_id"],
            opponent=away["name"] if is_home else home["name"],
            title=f"{home['name']} – {away['name']}",
            date=local_dt.strftime("%Y-%m-%d"),
            time=local_dt.strftime("%H:%M"),
            datetime_utc=local_dt.astimezone(ZoneInfo("UTC")),
            competition=headline["competition"],
            status=status or None,
            is_cancelled=is_cancelled_status(status),
        )
        games.append(last_game)

    return games


async def get_team_schedule(team_id: str, today: Optional[date] = None) -> Optional[TeamSchedule]:
    """
    Fetches all upcoming games of a team for the next year, including venues.

    :param team_id: The unique ID of the team from fussball.de.
    :param today: The first day of the range; defaults to today in Europe/Berlin.
    :return: The schedule, or None if fussball.de could not be fetched.
    """
    date_from = today or datetime.now(_LOCAL_TZ).date()
    date_to = date_from + timedelta(days=365)
    url = (
        f"{FUSSBALL_DE_BASE_URL}/ajax.team.matchplan/-/datum-bis/{date_to.isoformat()}"
        f"/datum-von/{date_from.isoformat()}/max/200/mode/PAGE/show-venues/true/team-id/{team_id}"
    )
    logger.info(f"Fetching schedule from URL: {url}")
    response = await asyncio.to_thread(fetch_url, url)

    if response is None or response.status_code >= 400:
        status = response.status_code if response else "no response"
        logger.warning(f"Could not fetch schedule for team {team_id} ({status}).")
        return None

    # None means the cached body could not be loaded; only a real "" is an empty schedule.
    if response.text is None:
        logger.warning(f"No response text for schedule of team {team_id}.")
        return None

    html = response.text
    try:
        games = parse_schedule_html(html, team_id) if html.strip() else []
    except ValueError as e:
        logger.error(f"Could not parse schedule for team {team_id}: {e}")
        return None
    logger.info(f"Parsed {len(games)} schedule games for team {team_id}")
    return TeamSchedule(
        team_id=team_id,
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        games=games,
    )
