from datetime import date
from pathlib import Path

import pytest

from fussball_api.cache import FetchedResponse
from fussball_api.schedule import (
    detect_surface,
    get_team_schedule,
    is_cancelled_status,
    parse_schedule_html,
    split_venue,
)

FULL_KR = "Kunstrasenplatz, Sportplatz Buchenbach (KR), Burger Straße 8, 79256 Buchenbach"
FULL_RP = "Rasenplatz, Sportplatz Buchenbach (RP), Burger Straße 8, 79256 Buchenbach"
PLAIN = "Matthias-Ginter-Sportpark, Im Grünle 3, 79232 March"


@pytest.mark.parametrize("venue,expected", [
    (FULL_KR, "KUNSTRASEN"),
    (FULL_RP, "RASEN"),
    ("Sportplatz Buchenbach (RP), Burger Straße 8, 79256 Buchenbach", "RASEN"),
    ("Sportplatz Buchenbach (KR), Burger Straße 8, 79256 Buchenbach", "KUNSTRASEN"),
    (PLAIN, None),
])
def test_detect_surface(venue, expected):
    assert detect_surface(venue) == expected


def test_split_venue_strips_surface_prefix():
    assert split_venue(FULL_KR) == {
        "venue_name": "Sportplatz Buchenbach (KR)",
        "venue_street": "Burger Straße 8",
        "venue_postal_code": "79256",
        "venue_city": "Buchenbach",
    }


def test_split_venue_without_surface():
    assert split_venue(PLAIN)["venue_name"] == "Matthias-Ginter-Sportpark"
    assert split_venue(PLAIN)["venue_city"] == "March"


def test_split_venue_name_only():
    assert split_venue("Sportplatz Ebnet") == {
        "venue_name": "Sportplatz Ebnet", "venue_street": None,
        "venue_postal_code": None, "venue_city": None,
    }


@pytest.mark.parametrize("status,expected", [
    ("Absetzung", True), ("abgesagt", True), ("Spielausfall", True),
    ("verlegt", False), (None, False), ("", False),
])
def test_is_cancelled_status(status, expected):
    assert is_cancelled_status(status) is expected


FIXTURES = Path(__file__).parent / "fixtures"
HERREN = "011MIA3QP0000000VTVG0001VTR8C1K7"
E_JUN = "011MI96LHC000000VTVG0001VTR8C1K7"


def _games(name, team_id):
    return parse_schedule_html((FIXTURES / name).read_text(encoding="utf-8"), team_id)


def test_parse_herren_schedule():
    games = _games("matchplan_herren.html", HERREN)
    assert len(games) == 24
    first = games[0]
    assert first.id == "031DERJ46K000000VS5489BTVUS470OH"
    assert first.is_home is True
    assert first.home_team == "Spvgg. Buchenbach"
    assert first.opponent == "SV BW Waltershofen"
    assert first.title == "Spvgg. Buchenbach – SV BW Waltershofen"
    assert (first.date, first.time) == ("2026-10-24", "17:00")
    assert first.datetime_utc.isoformat() == "2026-10-24T15:00:00+00:00"
    assert first.competition == "Bezirksliga"
    assert first.surface == "RASEN"
    assert first.venue_name == "Sportplatz Buchenbach (RP)"


def test_parse_away_game_has_foreign_venue():
    away = _games("matchplan_herren.html", HERREN)[1]
    assert away.is_home is False
    assert away.opponent == "SG Simonswald/Obersimonswald"
    assert away.venue_city == "Simonswald"


def test_tournament_game_keeps_raw_home_side():
    games = _games("matchplan_e_junioren.html", E_JUN)
    first = games[0]
    assert first.home_team == "ESV Freiburg"
    assert first.is_home is False
    assert first.surface == "KUNSTRASEN"
    assert first.venue == "Kunstrasenplatz, Sportplatz Buchenbach (KR), Burger Straße 8, 79256 Buchenbach"


def test_game_without_venue_row():
    html = (FIXTURES / "matchplan_herren.html").read_text(encoding="utf-8")
    first_venue = html.index('row-venue')
    start = html.rfind("<tr", 0, first_venue)
    end = html.index("</tr>", first_venue) + len("</tr>")
    games = parse_schedule_html(html[:start] + html[end:], HERREN)
    assert games[0].venue is None and games[0].surface is None
    assert games[1].venue is not None


@pytest.mark.asyncio
async def test_get_team_schedule_builds_url_and_parses(monkeypatch):
    seen = {}
    html = (FIXTURES / "matchplan_herren.html").read_text(encoding="utf-8")

    def fake_fetch(url, *args, **kwargs):
        seen["url"] = url
        return FetchedResponse(url=url, status_code=200, headers={}, content=b"", text=html)

    monkeypatch.setattr("fussball_api.schedule.fetch_url", fake_fetch)
    result = await get_team_schedule(HERREN, today=date(2026, 10, 21))
    assert "datum-von/2026-10-21" in seen["url"] and "datum-bis/2027-10-21" in seen["url"]
    assert "show-venues/true" in seen["url"] and "max/200" in seen["url"]
    assert len(result.games) == 24


@pytest.mark.asyncio
async def test_get_team_schedule_failure_returns_none(monkeypatch):
    monkeypatch.setattr("fussball_api.schedule.fetch_url", lambda url, *a, **k: None)
    assert await get_team_schedule(HERREN, today=date(2026, 10, 21)) is None
