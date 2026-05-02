"""
Smoke test for the O27v2 game loop.

Runs 10 games with fixed seeds and confirms each:
  - Completes without exception
  - Produces a winner
  - Has a nonzero total score
  - Has a valid game_date / box score structure

Usage:
    python o27v2/smoke_test.py
    python -m o27v2.smoke_test       (from workspace root)
"""
from __future__ import annotations
import sys
import os
import random

_workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _workspace not in sys.path:
    sys.path.insert(0, _workspace)

from o27.engine.state import GameState, Team, Player
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.render.render import Renderer
from o27v2.league import generate_players, _load_teams_db


# ---------------------------------------------------------------------------
# Load team definitions from the database (no DB connection required)
# ---------------------------------------------------------------------------

def _get_team_defs() -> list[dict]:
    """Return a list of team defs from teams_database.json (MLB level first)."""
    all_teams = _load_teams_db()
    mlb = [t for t in all_teams if t["level"] == "MLB"]
    if len(mlb) >= 20:
        return mlb
    return all_teams


_TEAM_DEFS = _get_team_defs()


# ---------------------------------------------------------------------------
# Build two teams from league data (no DB required for smoke test)
# ---------------------------------------------------------------------------

def _make_engine_team(team_def: dict, players: list[dict], role: str) -> Team:
    roster: list[Player] = []
    jokers: list[Player] = []
    for p in players:
        player = Player(
            player_id=f"{role}_{p['name']}",
            name=p["name"],
            is_pitcher=bool(p["is_pitcher"]),
            is_joker=bool(p["is_joker"]),
            skill=float(p["skill"]),
            speed=float(p["speed"]),
            pitcher_skill=float(p["pitcher_skill"]),
            stay_aggressiveness=float(p["stay_aggressiveness"]),
            contact_quality_threshold=float(p["contact_quality_threshold"]),
        )
        roster.append(player)
        if player.is_joker:
            jokers.append(player)
    return Team(
        team_id=role,
        name=team_def["name"],
        roster=roster,
        lineup=list(roster),
        jokers_available=list(jokers),
    )


def _find_pitcher(team: Team) -> str | None:
    for p in team.roster:
        if p.is_pitcher:
            return p.player_id
    return team.roster[0].player_id if team.roster else None


def run_one_game(seed: int, visitors_idx: int = 0, home_idx: int = 1) -> dict:
    """Run a single O27 game. Returns result dict."""
    rng = random.Random(seed)

    v_players = generate_players(visitors_idx, random.Random(visitors_idx))
    h_players = generate_players(home_idx,     random.Random(home_idx))

    vdef = _TEAM_DEFS[visitors_idx % len(_TEAM_DEFS)]
    hdef = _TEAM_DEFS[home_idx     % len(_TEAM_DEFS)]

    visitors = _make_engine_team(vdef, v_players, "visitors")
    home     = _make_engine_team(hdef, h_players, "home")

    state = GameState(visitors=visitors, home=home)
    state.current_pitcher_id = _find_pitcher(home)

    renderer = Renderer()
    provider = ProbabilisticProvider(rng)

    final_state, log = run_game(state, provider, renderer)

    return {
        "seed": seed,
        "visitors": visitors.name,
        "home": home.name,
        "away_score": final_state.score["visitors"],
        "home_score": final_state.score["home"],
        "winner": final_state.winner,
        "super_inning": final_state.super_inning_number,
        "log_lines": len(log),
        "spell_records": len(final_state.spell_log),
        "batter_stats": len(renderer._batter_stats),
    }


# ---------------------------------------------------------------------------
# Smoke test runner
# ---------------------------------------------------------------------------

SEEDS = [0, 1, 2, 3, 4, 5, 42, 100, 999, 12345]

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def run_smoke_tests() -> bool:
    print("=" * 60)
    print("O27v2 Smoke Test — 10 random seeds")
    print("=" * 60)

    all_passed = True
    pair_assignments = [
        (0, 1), (2, 3), (4, 5), (6, 7), (8, 9),
        (10, 11), (12, 13), (14, 15), (16, 17), (18, 19),
    ]

    for i, (seed, (vi, hi)) in enumerate(zip(SEEDS, pair_assignments)):
        vdef = _TEAM_DEFS[vi % len(_TEAM_DEFS)]
        hdef = _TEAM_DEFS[hi % len(_TEAM_DEFS)]
        label = (f"Game {i+1:2d} seed={seed:>6} | "
                 f"{vdef['abbreviation']} @ {hdef['abbreviation']}")
        try:
            result = run_one_game(seed, visitors_idx=vi, home_idx=hi)

            checks = [
                result["winner"] in ("visitors", "home"),
                result["away_score"] + result["home_score"] > 0,
                result["log_lines"] > 50,
                result["spell_records"] >= 2,
                result["batter_stats"] > 0,
            ]
            if all(checks):
                score_str = f"{result['away_score']}–{result['home_score']}"
                si_note   = " (SI)" if result["super_inning"] > 0 else ""
                print(f"  {PASS} {label} → {score_str}{si_note}")
            else:
                print(f"  {FAIL} {label} → checks={checks}")
                all_passed = False

        except Exception as exc:
            print(f"  {FAIL} {label} → EXCEPTION: {exc}")
            import traceback
            traceback.print_exc()
            all_passed = False

    print("=" * 60)
    if all_passed:
        print(f"  {PASS} All 10 games completed successfully.")
    else:
        print(f"  {FAIL} One or more games failed.")
    print("=" * 60)
    return all_passed


if __name__ == "__main__":
    ok = run_smoke_tests()
    sys.exit(0 if ok else 1)
