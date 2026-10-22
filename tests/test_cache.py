from datetime import datetime, timedelta, timezone

import pytest

from fussball_api import cache
from fussball_api.cache import HttpCacheEntry, fetch_url
from fussball_api.schemas import FullClubInfoResponse, Game


def test_save_and_load_club_info_cache_roundtrip(tmp_path, monkeypatch):
    """Tests that the prewarmed club cache (incl. datetimes) survives a save/load cycle."""
    monkeypatch.setattr(cache, "CACHE_DUMP_FILE", tmp_path / "fussball_cache.json")
    monkeypatch.setattr(cache.settings, "PREWARM_CLUB_ID", "club1")
    monkeypatch.setattr(cache, "http_cache", {})

    game = Game(
        id="g1",
        datetime_utc=datetime(2026, 9, 19, 8, 30, tzinfo=timezone.utc),
        competition="Kreisliga",
        home_team="Heim",
        home_logo="",
        away_team="Gast",
        away_logo="",
        duration=60,
    )
    club_info = FullClubInfoResponse(club_prev_games=[game], club_next_games=[], teams=[])
    monkeypatch.setattr(cache, "club_info_cache", {"club1": club_info})

    cache.save_caches_to_file()
    cache.club_info_cache.clear()
    cache.load_caches_from_file()

    assert cache.club_info_cache["club1"] == club_info


def test_throttle_spaces_live_requests(monkeypatch):
    clock = {"now": 100.0}
    sleeps = []
    monkeypatch.setattr("fussball_api.cache.time.monotonic", lambda: clock["now"])

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr("fussball_api.cache.time.sleep", fake_sleep)
    monkeypatch.setattr("fussball_api.cache.settings.FETCH_MIN_INTERVAL_SECONDS", 0.5)
    monkeypatch.setattr(cache, "_last_live_fetch", None)
    cache._throttle()
    cache._throttle()
    assert sleeps == [pytest.approx(0.5)]


def test_cache_hit_is_not_throttled(monkeypatch):
    url = "https://www.fussball.de/cached"
    monkeypatch.setattr(cache, "http_cache", {})
    cache.http_cache[url] = HttpCacheEntry(
        url=url, final_url=url, status_code=200, headers={}, content=None, text=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))

    def fail():
        raise AssertionError("throttled on cache hit")

    monkeypatch.setattr("fussball_api.cache._throttle", fail)
    assert fetch_url(url).status_code == 200
