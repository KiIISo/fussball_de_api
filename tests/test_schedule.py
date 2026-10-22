import pytest

from fussball_api.schedule import detect_surface, is_cancelled_status, split_venue

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
