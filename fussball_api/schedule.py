import logging
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_SURFACE_PREFIXES = ("Kunstrasenplatz", "Rasenplatz")
_POSTAL_CITY = re.compile(r"^(\d{5}) (.+)$")
_CANCELLED_MARKERS = ("absetzung", "abgesagt", "ausfall", "annulliert")


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
