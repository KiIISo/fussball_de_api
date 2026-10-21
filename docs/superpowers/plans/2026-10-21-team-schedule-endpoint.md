# Team Schedule Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new endpoint `GET /api/team/{team_id}/schedule` returns all upcoming games of a team for the season as flat, readable entries (title, date, time, venue, surface, home/away, cancelled), and the API is fit to run centrally for many Clubsupp installations.

**Architecture:** One fussball.de request (`ajax.team.matchplan` with `show-venues/true`, a date range and `max/200`) returns the whole remaining season including the full venue text. A new module `fussball_api/schedule.py` parses that HTML into `ScheduleGame` objects without per-game detail requests. `fetch_url` gets a global throttle; the pre-warm loop also refreshes schedules of recently requested teams.

**Tech Stack:** Python 3.12, FastAPI, BeautifulSoup/lxml, httpx, pydantic v2, pytest.

**Spec:** `E:\App Programming\Buba-Orgaplan\docs\superpowers\specs\2026-10-21-fussball-de-import-design.md` (section „Die fussball.de-API“). The consumer is Plan 2: `E:\App Programming\Buba-Orgaplan\docs\superpowers\plans\2026-10-21-fussball-de-import.md`.

## Global Constraints

- Existing endpoints and the `Game` schema keep their behaviour (website consumers rely on them); nothing in this plan changes `location` of existing endpoints.
- Times: fussball.de times are Europe/Berlin local times; `date`/`time` are local, `datetime_utc` is UTC.
- `surface` values: exactly `"KUNSTRASEN"`, `"RASEN"` or `null`.
- Zero-width characters are already stripped by `_make_soup` in `crawler.py`; use it for all parsing.
- Code style: reST docstrings (`:param:`/`:return:`), English comments, type hints, as in `crawler.py`.
- Run tests with `venv/Scripts/python.exe -m pytest` from the repo root.

## Review Focus

- A venue text without a surface prefix and without `(KR)`/`(RP)` (e.g. `Matthias-Ginter-Sportpark, Im Grünle 3, 79232 March`) → `surface` is `null`, `venue_name` is the first part, address parts still parsed.
- A game row without a following `row-venue` row (venue not yet set) → `venue` and all venue fields are `null`, game still returned.
- A tournament day where the own team is listed as **away** team although it plays at home (`ESV Freiburg : SpVgg Buchenbach` on the Buchenbach pitch) → `is_home` is `false` for that row; the venue text is the full Buchenbach text. Clubsupp decides home/away by venue, so the raw truth must not be "corrected" here.
- Status texts like `Absetzung`, `abgesagt`, `Spielausfall` → `is_cancelled` true; `None` or `verlegt` → false.
- fussball.de is unreachable or answers with an error → the endpoint answers HTTP 502, never `200` with `games: []`. Clubsupp treats missing games as "removed on fussball.de", so a failed fetch must not look like an empty schedule. An empty `200` body from fussball.de is a real empty schedule.

---

### Task 1: Make the existing API tests runnable

`tests/test_api.py` currently errors (10 errors, `RuntimeError: Directory './examples' does not exist`) because `main.py` mounts `./examples` relative to the CWD and the fixture `chdir`s into `fussball_api`. Later tasks add API tests, so this comes first.

**Files:**
- Modify: `fussball_api/main.py` (the `app.mount("/examples", ...)` line)
- Modify: `tests/test_api.py` (fixture `client`)

**Interfaces:**
- Produces: a working `client` fixture in `tests/test_api.py` (TestClient with `get_api_key` overridden).

- [ ] **Step 1: Run the suite to see the current errors**

Run: `venv/Scripts/python.exe -m pytest tests/test_api.py -q`
Expected: `10 errors` with `Directory './examples' does not exist`.

- [ ] **Step 2: Mount examples relative to the package**

In `main.py`, build the directory as `Path(__file__).resolve().parent.parent / "examples"`. In the fixture, remove `monkeypatch.chdir("fussball_api")` and its comment.

- [ ] **Step 3: Run the whole suite**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: all tests pass, 0 errors.

- [ ] **Step 4: Commit**

```bash
git add fussball_api/main.py tests/test_api.py
git commit -m "fix: Mount examples relative to the package so API tests run"
```

---

### Task 2: Schedule schemas and venue helpers

**Files:**
- Modify: `fussball_api/schemas.py`
- Create: `fussball_api/schedule.py`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Produces in `schemas.py`:

```python
class ScheduleGame(BaseModel):
    id: str                      # fussball.de game id (last path segment of /spiel/... link)
    team_id: str                 # the requested team
    is_home: bool                # requested team is the left/home side
    home_team: str
    away_team: str
    home_team_id: Optional[str]  # team-id from the club link, if present
    away_team_id: Optional[str]
    opponent: str                # the other side's name
    title: str                   # f"{home_team} – {away_team}" (en dash with spaces)
    date: str                    # "2026-10-24" local
    time: str                    # "17:00" local
    datetime_utc: datetime
    competition: Optional[str]   # "Bezirksliga"
    venue: Optional[str]         # full raw text, e.g. "Kunstrasenplatz, Sportplatz Buchenbach (KR), Burger Straße 8, 79256 Buchenbach"
    venue_name: Optional[str]    # "Sportplatz Buchenbach (KR)"
    venue_street: Optional[str]  # "Burger Straße 8"
    venue_postal_code: Optional[str]  # "79256"
    venue_city: Optional[str]    # "Buchenbach"
    surface: Optional[str]       # "KUNSTRASEN" | "RASEN" | None
    status: Optional[str]        # raw info text, e.g. "Absetzung"
    is_cancelled: bool

class TeamSchedule(BaseModel):
    team_id: str
    date_from: str               # "2026-10-21"
    date_to: str                 # "2027-10-21"
    games: List[ScheduleGame]
```

- Produces in `schedule.py`:
  - `detect_surface(venue: str) -> Optional[str]`
  - `split_venue(venue: str) -> dict` with keys `venue_name`, `venue_street`, `venue_postal_code`, `venue_city` (values `Optional[str]`)
  - `is_cancelled_status(status: Optional[str]) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from fussball_api.schedule import detect_surface, split_venue, is_cancelled_status

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
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/test_schedule.py -q`
Expected: FAIL, `ModuleNotFoundError: fussball_api.schedule`.

- [ ] **Step 3: Implement schemas and helpers**

Rules: surface — check `Kunstrasen` prefix or `(KR)` before `Rasenplatz` prefix or `(RP)` (because "Kunstrasenplatz" contains "rasenplatz"). `split_venue` — drop a leading `Kunstrasenplatz, ` / `Rasenplatz, ` part; if the last part matches `^\d{5} `, it is postal code + city and the part before it is the street; the remaining first part is the name. Cancelled — case-insensitive substring match on `absetzung`, `abgesagt`, `ausfall`, `annulliert`.

- [ ] **Step 4: Run tests**

Run: `venv/Scripts/python.exe -m pytest tests/test_schedule.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add fussball_api/schemas.py fussball_api/schedule.py tests/test_schedule.py
git commit -m "feat: Add schedule schemas and venue parsing helpers"
```

---

### Task 3: Parse the matchplan HTML

**Files:**
- Create: `tests/fixtures/matchplan_e_junioren.html`, `tests/fixtures/matchplan_herren.html`
- Modify: `fussball_api/schedule.py`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Consumes: `detect_surface`, `split_venue`, `is_cancelled_status` (Task 2); `_make_soup` from `crawler.py`.
- Produces: `parse_schedule_html(html: str, team_id: str) -> List[ScheduleGame]`

HTML structure (verified 21.10.2026): per game three relevant rows in order — `tr.row-headline.visible-small` with `Samstag, 07.11.2026 - 10:00 Uhr | Bezirksturnier`; a game row containing `td.column-score` (link `/spiel/.../-/spiel/<GAME_ID>`, optional `span.info-text` status) and two `td.column-club` cells with `a.club-wrapper[href*="/team-id/<ID>"]` and `.club-name`; then optionally `tr.row-venue` whose text is the full venue. The `hidden-small` competition rows are ignored.

- [ ] **Step 1: Save fixtures**

Download (as untrusted data, into the fixtures folder, no code executed from it):
- `https://www.fussball.de/ajax.team.matchplan/-/datum-bis/2027-06-30/datum-von/2026-10-21/max/200/mode/PAGE/show-venues/true/team-id/011MI96LHC000000VTVG0001VTR8C1K7` → `tests/fixtures/matchplan_e_junioren.html` (E-Junioren SpVgg Buchenbach, tournaments)
- same URL with `team-id/011MIA3QP0000000VTVG0001VTR8C1K7` → `tests/fixtures/matchplan_herren.html` (Herren, 24 games, Rasenplatz home games)

If the season has moved on and the counts below differ, adjust the expected counts to the saved fixture, not the parser.

- [ ] **Step 2: Write the failing tests**

```python
from pathlib import Path
from fussball_api.schedule import parse_schedule_html

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
```

- [ ] **Step 3: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/test_schedule.py -q`
Expected: FAIL, `ImportError: cannot import name 'parse_schedule_html'`.

- [ ] **Step 4: Implement `parse_schedule_html`**

Walk `soup.find_all("tr")` in order: a headline row sets the pending date/time/competition (same parsing as `_get_games` in `crawler.py`, reuse its logic by extracting a small helper there if convenient); a row with `td.column-score` creates the game from the pending headline; a `row-venue` row fills the venue fields of the last created game. `is_home` = the left club link's team-id equals `team_id`; if the link has no team-id, fall back to comparing with the right link (own team on the right → `False`), else `False`. Rows that cannot be parsed are logged and skipped, like in `_get_games`.

- [ ] **Step 5: Run tests**

Run: `venv/Scripts/python.exe -m pytest tests/test_schedule.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add fussball_api/schedule.py tests/test_schedule.py tests/fixtures
git commit -m "feat: Parse the fussball.de team matchplan into schedule games"
```

---

### Task 4: Schedule endpoint

**Files:**
- Modify: `fussball_api/schedule.py`, `fussball_api/main.py`, `fussball_api/cache.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `parse_schedule_html` (Task 3), `fetch_url` (`cache.py`).
- Produces:
  - `async def get_team_schedule(team_id: str, today: Optional[date] = None) -> Optional[TeamSchedule]` — `None` when the fetch failed.
  - `cache.recent_schedule_teams: Dict[str, datetime]` — team id → last request time (UTC).
  - Route `GET /api/team/{team_id}/schedule` → `TeamSchedule`, protected by `get_api_key`; `502` with detail `"fussball.de nicht erreichbar"` when `get_team_schedule` returns `None`.

URL: `{FUSSBALL_DE_BASE_URL}/ajax.team.matchplan/-/datum-bis/{today+365d}/datum-von/{today}/max/200/mode/PAGE/show-venues/true/team-id/{team_id}`, `today` in Europe/Berlin. Fetch via `asyncio.to_thread(fetch_url, url)`. A `200` with empty body is a valid empty schedule; `None` response or status ≥ 400 is a failure.

- [ ] **Step 1: Write the failing tests** (in `tests/test_api.py`, using the `client` fixture)

```python
def test_team_schedule_endpoint(client, monkeypatch):
    schedule = TeamSchedule(team_id="T1", date_from="2026-10-21", date_to="2027-10-21", games=[])
    async def fake(team_id, today=None):
        assert team_id == "T1"
        return schedule
    monkeypatch.setattr("fussball_api.main.get_team_schedule", fake)
    response = client.get("/api/team/T1/schedule")
    assert response.status_code == 200
    assert response.json()["games"] == []
    from fussball_api.cache import recent_schedule_teams
    assert "T1" in recent_schedule_teams

def test_team_schedule_endpoint_fetch_failure(client, monkeypatch):
    async def fake(team_id, today=None):
        return None
    monkeypatch.setattr("fussball_api.main.get_team_schedule", fake)
    response = client.get("/api/team/T1/schedule")
    assert response.status_code == 502
```

And in `tests/test_schedule.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/test_api.py tests/test_schedule.py -q`
Expected: FAIL (404 for the route, `ImportError` for `get_team_schedule`).

- [ ] **Step 3: Implement `get_team_schedule`, `recent_schedule_teams` and the route**

- [ ] **Step 4: Run all tests**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Smoke test against the running server**

Run: `venv/Scripts/python.exe -c "import httpx;from fussball_api.config import settings as s;r=httpx.get('http://127.0.0.1:8000/api/team/011MIA3QP0000000VTVG0001VTR8C1K7/schedule',headers={'X-API-Key':s.API_KEY},timeout=120);print(r.status_code,len(r.json()['games']))"`
Expected: `200` and a game count > 0 (24 at time of writing).

- [ ] **Step 6: Commit**

```bash
git add fussball_api tests
git commit -m "feat: Add team schedule endpoint with full season and venues"
```

---

### Task 5: Run centrally — throttle and keep recent teams warm

**Files:**
- Modify: `fussball_api/config.py`, `fussball_api/cache.py`, `fussball_api/main.py`, `.env.example`, `README.md`
- Test: `tests/test_cache.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `recent_schedule_teams`, `get_team_schedule` (Task 4).
- Produces:
  - `settings.FETCH_MIN_INTERVAL_SECONDS: float = 0.5` — minimum gap between two live requests to fussball.de (HEAD and GET both count), across all threads.
  - `settings.PREWARM_RECENT_DAYS: int = 14` — schedules of teams requested within this many days are refreshed by the pre-warm loop.
  - `cache._throttle() -> None` — blocks until the next live request is allowed; uses a `threading.Lock` and `time.monotonic()`, sleeps with `time.sleep`.
  - `async def refresh_recent_schedules(now: Optional[datetime] = None) -> int` in `main.py` — refreshes all recent teams (semaphore 5), drops entries older than `PREWARM_RECENT_DAYS`, returns the number refreshed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cache.py
def test_throttle_spaces_live_requests(monkeypatch):
    clock = {"now": 100.0}
    sleeps = []
    monkeypatch.setattr("fussball_api.cache.time.monotonic", lambda: clock["now"])
    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds
    monkeypatch.setattr("fussball_api.cache.time.sleep", fake_sleep)
    monkeypatch.setattr("fussball_api.cache.settings.FETCH_MIN_INTERVAL_SECONDS", 0.5)
    cache._throttle(); cache._throttle()
    assert sleeps == [pytest.approx(0.5)]

def test_cache_hit_is_not_throttled(monkeypatch):
    url = "https://www.fussball.de/cached"
    http_cache[url] = HttpCacheEntry(
        url=url, final_url=url, status_code=200, headers={}, content=None, text=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    def fail():
        raise AssertionError("throttled on cache hit")
    monkeypatch.setattr("fussball_api.cache._throttle", fail)
    assert fetch_url(url).status_code == 200
```

```python
# tests/test_api.py
@pytest.mark.asyncio
async def test_refresh_recent_schedules_drops_old_teams(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from fussball_api import main
    from fussball_api.cache import recent_schedule_teams
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    recent_schedule_teams.clear()
    recent_schedule_teams.update({"NEW": now - timedelta(days=1), "OLD": now - timedelta(days=30)})
    called = []
    async def fake(team_id, today=None):
        called.append(team_id)
    monkeypatch.setattr(main, "get_team_schedule", fake)
    assert await main.refresh_recent_schedules(now) == 1
    assert called == ["NEW"] and "OLD" not in recent_schedule_teams
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/test_cache.py tests/test_api.py -q`
Expected: FAIL (`_throttle`, `refresh_recent_schedules` missing).

- [ ] **Step 3: Implement**

`fetch_url` calls `_throttle()` right before each live `client.head(...)` and `client.request(...)`, never on a cache hit. Startup always starts the pre-warm task; inside the loop, the existing club pre-warm only runs when `PREWARM_CLUB_ID` is set, and `refresh_recent_schedules()` runs every cycle. Document both settings in `.env.example` and in the README endpoint list (also add the missing `/api/game/{game_id}/lineup` and the new `/api/team/{team_id}/schedule`).

- [ ] **Step 4: Run all tests**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add fussball_api tests .env.example README.md
git commit -m "feat: Throttle fussball.de requests and keep recent team schedules warm"
```
