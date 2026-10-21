"""
Prints all teams and games of a club from a running instance of the Fussball.de API.

Usage (from the project root, with the venv activated):

    python scripts/club_overview.py Buchenbach
    python scripts/club_overview.py "SpVgg Buchenbach" --next-only
    python scripts/club_overview.py --club-id 00ES8GN9CG0000A3VV0AG08LVUPGND5I
    python scripts/club_overview.py Buchenbach --json buchenbach.json

The API key is read from the .env file (API_KEY) unless passed explicitly.
Without a search term, PREWARM_CLUB_ID from the .env file is used.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

import httpx
from pydantic import TypeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fussball_api.config import settings  # noqa: E402
from fussball_api.schemas import (  # noqa: E402
    ClubSearchResult,
    FullClubInfoResponse,
    Game,
    TeamWithDetails,
)

LOCAL_TZ = ZoneInfo("Europe/Berlin")
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def clean(text: Optional[str]) -> str:
    """
    Removes zero-width spaces that fussball.de puts into some names.

    :param text: The text to clean.
    :return: The cleaned text.
    """
    return (text or "").replace("​", "")


def format_game(game: Game, with_location: bool) -> str:
    """
    Formats a game as a single line.

    :param game: The game to format.
    :param with_location: Whether to append the location.
    :return: The formatted line.
    """
    local = game.datetime_utc.astimezone(LOCAL_TZ)
    when = f"{WEEKDAYS[local.weekday()]} {local:%d.%m.%y %H:%M}"
    if game.home_score is not None and game.away_score is not None:
        score = f"{game.home_score}:{game.away_score}"
    else:
        score = "-:-"
    line = (
        f"  {when} | {clean(game.home_team)} {score} {clean(game.away_team)}"
        f" ({clean(game.competition)})"
    )
    if game.status:
        line += f" [{game.status}]"
    if with_location and game.location:
        line += f"\n{'':20}@ {clean(game.location)}"
    return line


def table_position(team: TeamWithDetails, club_name: str) -> str:
    """
    Finds the team's row in its league table.

    :param team: The team including its table.
    :param club_name: The club name, used as fallback for matching the row.
    :return: A short description like "Platz 4/16, 15 Pkt.", or an empty string.
    """
    if not team.table or not team.table.entries:
        return ""
    # Team names look like "Herren - Spvgg. Buchenbach 2"; the part after the
    # dash is the name used in the table.
    own_name = clean(team.name).split(" - ", 1)[-1].strip().lower()
    entries = team.table.entries
    row = next((e for e in entries if clean(e.team).strip().lower() == own_name), None)
    if row is None:
        keyword = clean(club_name).split()[-1].lower()
        row = next((e for e in entries if keyword in clean(e.team).lower()), None)
    if row is None:
        return ""
    return f"Platz {row.place}/{len(entries)}, {row.points} Pkt., {row.games} Sp."


def find_club(client: httpx.Client, query: str) -> Optional[ClubSearchResult]:
    """
    Searches for a club and lets the user pick one if there are several matches.

    :param client: The HTTP client.
    :param query: The search term.
    :return: The selected club, or None if nothing was found.
    """
    response = client.get("/api/search/clubs", params={"query": query})
    response.raise_for_status()
    clubs = TypeAdapter(List[ClubSearchResult]).validate_python(response.json())
    if not clubs:
        return None
    if len(clubs) == 1 or not sys.stdin.isatty():
        return clubs[0]

    for i, club in enumerate(clubs, 1):
        print(f"  {i:2}. {clean(club.name)} ({clean(club.city)})")
    choice = input("Verein wählen [1]: ").strip() or "1"
    return clubs[int(choice) - 1]


def print_overview(info: FullClubInfoResponse, club_name: str, args) -> None:
    """
    Prints the overview of all teams and their games.

    :param info: The full club info from the API.
    :param club_name: The club name for the header.
    :param args: The parsed command line arguments.
    """
    print(f"\n=== {clean(club_name)} - {len(info.teams)} Teams ===")
    for team in info.teams:
        position = table_position(team, club_name)
        print(f"\n### {clean(team.name)}" + (f" - {position}" if position else ""))
        if not args.next_only:
            print(" Letzte Spiele:")
            for game in team.prev_games or []:
                print(format_game(game, with_location=False))
            if not team.prev_games:
                print("  -")
        print(" Nächste Spiele:")
        for game in team.next_games or []:
            print(format_game(game, with_location=args.locations))
        if not team.next_games:
            print("  -")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zeigt alle Teams und Spiele eines Vereins."
    )
    parser.add_argument("query", nargs="?", help="Suchbegriff für den Verein")
    parser.add_argument("--club-id", help="Vereins-ID statt Suche")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=settings.API_KEY)
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="Timeout in Sekunden (der erste Aufruf crawlt alle Teams und dauert lange)",
    )
    parser.add_argument(
        "--next-only", action="store_true", help="Nur kommende Spiele anzeigen"
    )
    parser.add_argument(
        "--locations", action="store_true", help="Spielort bei kommenden Spielen anzeigen"
    )
    parser.add_argument("--json", type=Path, help="Rohdaten zusätzlich als JSON speichern")
    args = parser.parse_args()

    # Windows consoles default to cp1252, which cannot print all crawled names.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    client = httpx.Client(
        base_url=args.base_url,
        headers={"X-API-Key": args.api_key},
        timeout=args.timeout,
    )

    try:
        club_id = args.club_id
        club_name = club_id or ""
        if not club_id and args.query:
            club = find_club(client, args.query)
            if club is None:
                sys.exit(f"Kein Verein gefunden für '{args.query}'.")
            club_id, club_name = club.id, club.name
        elif not club_id:
            club_id = club_name = settings.PREWARM_CLUB_ID
        if not club_id:
            parser.error("Suchbegriff oder --club-id angeben.")

        print(f"Lade {clean(club_name)} ({club_id}) ...", flush=True)
        response = client.get(f"/api/club/{club_id}")
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        sys.exit(f"API-Fehler {e.response.status_code}: {e.response.text[:200]}")
    except httpx.HTTPError as e:
        sys.exit(f"Anfrage fehlgeschlagen: {e!r}")

    data = response.json()
    if args.json:
        args.json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Rohdaten gespeichert in {args.json}")

    info = FullClubInfoResponse.model_validate(data)
    print_overview(info, club_name, args)


if __name__ == "__main__":
    main()
