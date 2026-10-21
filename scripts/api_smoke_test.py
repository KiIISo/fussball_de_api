"""
Smoke test against a running instance of the Fussball.de API.

Walks through all endpoints in a chain (club search -> club -> team -> game),
validates each response against the Pydantic schemas and prints a summary.

Usage (from the project root, with the venv activated):

    python scripts/api_smoke_test.py
    python scripts/api_smoke_test.py --query "Werder Bremen"
    python scripts/api_smoke_test.py --club-id 00ES8GN8VS000030VV0AG08LVUPGND5I
    python scripts/api_smoke_test.py --base-url http://127.0.0.1:8000 --api-key secret

API key and default club ID are read from the .env file (API_KEY, PREWARM_CLUB_ID)
unless passed explicitly.
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Any, List, Optional

import httpx
from pydantic import TypeAdapter, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fussball_api.config import settings  # noqa: E402
from fussball_api.schemas import (  # noqa: E402
    ClubInfoResponse,
    ClubSearchResult,
    FullClubInfoResponse,
    Game,
    Lineup,
    Table,
    Team,
    TeamInfoResponse,
)

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


class SmokeTest:
    """
    Executes requests against the API and records the results.

    :ivar client: The HTTP client used for all requests.
    :ivar results: Tuples of (status, label) for the summary.
    """

    def __init__(self, base_url: str, api_key: str, timeout: float):
        self.client = httpx.Client(
            base_url=base_url, headers={"X-API-Key": api_key}, timeout=timeout
        )
        self.results: List[tuple] = []

    def check(
        self,
        path: str,
        schema: Any = None,
        expected_status: int = 200,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        """
        Requests a path, checks the status code and validates the body.

        :param path: The endpoint path, e.g. "/api/team/123/table".
        :param schema: Type to validate the JSON body against (optional).
        :param expected_status: The expected HTTP status code.
        :param headers: Headers overriding the client defaults.
        :param params: Query parameters.
        :return: The validated data, or None if the check failed.
        """
        label = path + (f"?{httpx.QueryParams(params)}" if params else "")
        start = time.perf_counter()
        try:
            response = self.client.get(path, headers=headers, params=params)
        except httpx.HTTPError as e:
            self._record(False, label, f"request failed: {e!r}")
            return None
        elapsed = (time.perf_counter() - start) * 1000

        if response.status_code != expected_status:
            self._record(
                False,
                label,
                f"status {response.status_code} (expected {expected_status}) "
                f"{response.text[:200]}",
            )
            return None

        data = response.json()
        if schema is not None:
            try:
                data = TypeAdapter(schema).validate_python(data)
            except ValidationError as e:
                self._record(False, label, f"schema error: {e}")
                return None

        self._record(True, label, f"{response.status_code} in {elapsed:.0f} ms")
        return data

    def skip(self, label: str, reason: str) -> None:
        """
        Records a check that could not be executed.

        :param label: The name of the skipped check.
        :param reason: Why it was skipped.
        """
        self.results.append((None, label))
        print(f"{YELLOW}SKIP{RESET} {label} - {reason}")

    def _record(self, ok: bool, label: str, detail: str) -> None:
        self.results.append((ok, label))
        tag = f"{GREEN}OK  {RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"{tag} {label} - {detail}")

    def summary(self) -> bool:
        """
        Prints the summary.

        :return: True if no check failed.
        """
        passed = sum(1 for ok, _ in self.results if ok is True)
        failed = [label for ok, label in self.results if ok is False]
        skipped = sum(1 for ok, _ in self.results if ok is None)
        print(
            f"\n{passed} passed, {len(failed)} failed, {skipped} skipped "
            f"({len(self.results)} total)"
        )
        for label in failed:
            print(f"  {RED}FAIL{RESET} {label}")
        return not failed


def run(args: argparse.Namespace) -> bool:
    """
    Runs the complete endpoint chain.

    :param args: The parsed command line arguments.
    :return: True if all checks passed.
    """
    test = SmokeTest(args.base_url, args.api_key, args.timeout)

    print("== General ==")
    test.check("/")
    test.check(
        "/api/search/clubs",
        params={"query": args.query},
        headers={"X-API-Key": "invalid-key"},
        expected_status=401,
    )
    test.check("/api/search/clubs", params={"query": "ab"}, expected_status=422)

    print("\n== Club ==")
    clubs = test.check(
        "/api/search/clubs", List[ClubSearchResult], params={"query": args.query}
    )
    club_id = args.club_id
    if not club_id and clubs:
        club_id = clubs[0].id
        print(f"     -> using club '{clubs[0].name}' ({club_id})")

    teams: List[Team] = []
    club_games: List[Game] = []
    if club_id:
        teams = test.check(f"/api/club/{club_id}/teams", List[Team]) or []
        test.check(f"/api/club/{club_id}/info", ClubInfoResponse)
        club_games += test.check(f"/api/club/{club_id}/prev_games", List[Game]) or []
        club_games += test.check(f"/api/club/{club_id}/next_games", List[Game]) or []
        if not args.quick:
            test.check(f"/api/club/{club_id}", FullClubInfoResponse)
    else:
        test.skip("/api/club/...", "no club found")

    print("\n== Team ==")
    team_id = args.team_id or (teams[0].id if teams else None)
    team_games: List[Game] = []
    if team_id:
        if not args.team_id:
            print(f"     -> using team '{teams[0].name}' ({team_id})")
        test.check(f"/api/team/{team_id}/table", Table)
        team_games += test.check(f"/api/team/{team_id}/prev_games", List[Game]) or []
        team_games += test.check(f"/api/team/{team_id}/next_games", List[Game]) or []
        test.check(f"/api/team/{team_id}", TeamInfoResponse)
    else:
        test.skip("/api/team/...", "no team found")

    print("\n== Game ==")
    # Prefer a game that has already been played, so events and lineup exist.
    games = team_games + club_games
    played = [g for g in games if g.home_score is not None]
    game_id = args.game_id or ((played or games)[0].id if games else None)
    if game_id:
        print(f"     -> using game {game_id}")
        game = test.check(f"/api/game/{game_id}", Game)
        if game:
            print(
                f"     {game.home_team} {game.home_score or '-'}:"
                f"{game.away_score or '-'} {game.away_team}, "
                f"{len(game.match_events or [])} events"
            )
        lineup = test.check(f"/api/game/{game_id}/lineup", Lineup)
        if lineup:
            print(
                f"     lineup: {len(lineup.home.starting)} home / "
                f"{len(lineup.away.starting)} away starters"
            )
    else:
        test.skip("/api/game/...", "no game found")

    test.check("/api/game/does-not-exist", expected_status=404)

    return test.summary()


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test for the Fussball.de API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=settings.API_KEY)
    parser.add_argument("--query", default="spvgg buchenbach", help="Club search term")
    parser.add_argument(
        "--club-id",
        default=settings.PREWARM_CLUB_ID,
        help="Club ID (default: PREWARM_CLUB_ID, otherwise first search result)",
    )
    parser.add_argument("--team-id", help="Team ID (default: first team of the club)")
    parser.add_argument("--game-id", help="Game ID (default: a game of the team)")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--quick", action="store_true", help="Skip the slow full club endpoint"
    )
    args = parser.parse_args()

    # Windows consoles default to cp1252, which cannot print all crawled names.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(0 if run(args) else 1)


if __name__ == "__main__":
    main()
