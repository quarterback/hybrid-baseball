"""
O27 Simulator — CLI entry point.

Usage (from repo root):
    python -m o27.main [--seed SEED] [--output FILE] [--no-render]
    python o27/main.py  [--seed SEED] [--output FILE] [--no-render]
    pnpm run o27

Phase 3: Jinja2 play-by-play rendering wired in. The Renderer tracks per-batter
stats and renders the box score at game end.  Pass --no-render to get the raw
Phase 2 log format (useful for debugging or testing).
"""

import sys
import os
import argparse
import random

_workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _workspace_root not in sys.path:
    sys.path.insert(0, _workspace_root)

from o27 import config as _cfg

from o27.engine.state import GameState, Team, Player
from o27.engine.game import run_game, make_script_provider
from o27.engine.prob import ProbabilisticProvider
from o27.engine import fielding as fld
from o27.render.render import Renderer


# ---------------------------------------------------------------------------
# AI team definitions
# ---------------------------------------------------------------------------

def _player(
    pid: str,
    name: str,
    *,
    skill: float = _cfg.PLAYER_DEFAULT_SKILL,
    speed: float = _cfg.PLAYER_DEFAULT_SPEED,
    pitcher_skill: float = _cfg.PLAYER_DEFAULT_PITCHER_SKILL,
    stay_aggressiveness: float = _cfg.PLAYER_DEFAULT_STAY_AGGRESSIVENESS,
    contact_quality_threshold: float = _cfg.PLAYER_DEFAULT_CONTACT_QUALITY_THRESHOLD,
    is_pitcher: bool = False,
    is_joker: bool = False,   # Phase 10 retired jokers; flag is accepted
                              # for back-compat with the foxes/bears rosters
                              # below but no longer surfaces as a Player attr.
) -> Player:
    p = Player(
        player_id=pid,
        name=name,
        skill=skill,
        speed=speed,
        pitcher_skill=pitcher_skill,
        stay_aggressiveness=stay_aggressiveness,
        contact_quality_threshold=contact_quality_threshold,
        is_pitcher=is_pitcher,
    )
    # Stash legacy joker tag on the instance so the roster filter below
    # still works (main.py is the only caller that consults it).
    p.is_joker = is_joker
    return p


def make_foxes() -> Team:
    """
    Millbrook Foxes — balanced, high-contact, speed-oriented team.
    """
    roster = [
        _player("F1",  "M. Ashby",    skill=0.62, speed=0.78,
                stay_aggressiveness=0.10, contact_quality_threshold=0.38),
        _player("F2",  "D. Cortez",   skill=0.58, speed=0.72,
                stay_aggressiveness=0.09, contact_quality_threshold=0.35),
        _player("F3",  "R. Tanner",   skill=0.65, speed=0.60,
                stay_aggressiveness=0.07, contact_quality_threshold=0.32),
        _player("F4",  "L. Moreau",   skill=0.63, speed=0.48,
                stay_aggressiveness=0.04, contact_quality_threshold=0.22),
        _player("F5",  "T. Hollis",   skill=0.56, speed=0.65,
                stay_aggressiveness=0.08, contact_quality_threshold=0.34),
        _player("F6",  "C. Nkrumah",  skill=0.52, speed=0.68,
                stay_aggressiveness=0.08, contact_quality_threshold=0.36),
        _player("F7",  "P. Svensson", skill=0.50, speed=0.62,
                stay_aggressiveness=0.07, contact_quality_threshold=0.32),
        _player("F8",  "K. Yamada",   skill=0.44, speed=0.70,
                stay_aggressiveness=0.06, contact_quality_threshold=0.30),
        _player("F9",  "S. Okafor",   skill=0.30, speed=0.38,
                pitcher_skill=0.52,
                stay_aggressiveness=0.03, contact_quality_threshold=0.18,
                is_pitcher=True),
        _player("FJ1", "V. Ramos",    skill=0.70, speed=0.52,
                stay_aggressiveness=0.05, contact_quality_threshold=0.26,
                is_joker=True),
        _player("FJ2", "B. Lepage",   skill=0.67, speed=0.66,
                stay_aggressiveness=0.07, contact_quality_threshold=0.31,
                is_joker=True),
        _player("FJ3", "G. Osei",     skill=0.64, speed=0.58,
                stay_aggressiveness=0.08, contact_quality_threshold=0.34,
                is_joker=True),
    ]
    jokers = [p for p in roster if p.is_joker]
    return Team(
        team_id="visitors",
        name="Millbrook Foxes",
        roster=roster,
        lineup=list(roster),
        jokers_available=list(jokers),
    )


def make_bears() -> Team:
    """
    Ironvale Bears — power-heavy, slow-footed, low-stay team.
    """
    roster = [
        _player("B1",  "A. Driscoll",   skill=0.56, speed=0.52,
                stay_aggressiveness=0.05, contact_quality_threshold=0.20),
        _player("B2",  "N. Petrov",     skill=0.60, speed=0.42,
                stay_aggressiveness=0.04, contact_quality_threshold=0.16),
        _player("B3",  "O. Fitzgerald", skill=0.68, speed=0.36,
                stay_aggressiveness=0.03, contact_quality_threshold=0.13),
        _player("B4",  "J. Makinde",    skill=0.66, speed=0.34,
                stay_aggressiveness=0.02, contact_quality_threshold=0.11),
        _player("B5",  "R. Colburn",    skill=0.56, speed=0.40,
                stay_aggressiveness=0.04, contact_quality_threshold=0.16),
        _player("B6",  "T. Wachowski",  skill=0.50, speed=0.44,
                stay_aggressiveness=0.05, contact_quality_threshold=0.20),
        _player("B7",  "E. Solberg",    skill=0.46, speed=0.48,
                stay_aggressiveness=0.05, contact_quality_threshold=0.20),
        _player("B8",  "H. Mwangi",     skill=0.40, speed=0.46,
                stay_aggressiveness=0.04, contact_quality_threshold=0.18),
        _player("B9",  "C. Lindqvist",  skill=0.28, speed=0.35,
                pitcher_skill=0.62,
                stay_aggressiveness=0.02, contact_quality_threshold=0.10,
                is_pitcher=True),
        _player("BJ1", "D. Oduya",      skill=0.74, speed=0.38,
                stay_aggressiveness=0.02, contact_quality_threshold=0.08,
                is_joker=True),
        _player("BJ2", "M. Castillo",   skill=0.71, speed=0.40,
                stay_aggressiveness=0.02, contact_quality_threshold=0.10,
                is_joker=True),
        _player("BJ3", "P. Graves",     skill=0.68, speed=0.44,
                stay_aggressiveness=0.03, contact_quality_threshold=0.14,
                is_joker=True),
    ]
    jokers = [p for p in roster if p.is_joker]
    return Team(
        team_id="home",
        name="Ironvale Bears",
        roster=roster,
        lineup=list(roster),
        jokers_available=list(jokers),
    )


# ---------------------------------------------------------------------------
# Demo roster builder (Phase 1 compatibility — kept for tests)
# ---------------------------------------------------------------------------

def make_demo_player(pid: str, name: str, is_pitcher=False, is_joker=False) -> Player:
    return Player(
        player_id=pid,
        name=name,
        is_pitcher=is_pitcher,
        is_joker=is_joker,
    )


def make_demo_team(team_id: str, name: str) -> Team:
    prefix = team_id[0].upper()
    starters = []
    for i in range(1, 10):
        p = make_demo_player(f"{prefix}{i}", f"{name[:3]}-{i}",
                             is_pitcher=(i == 9))
        starters.append(p)
    jokers = []
    for j in range(1, 4):
        jk = make_demo_player(f"{prefix}J{j}", f"{name[:3]}-J{j}", is_joker=True)
        jokers.append(jk)
    roster = starters + jokers
    return Team(
        team_id=team_id,
        name=name,
        roster=roster,
        lineup=list(roster),
        jokers_available=list(jokers),
    )


def make_demo_state() -> GameState:
    visitors = make_demo_team("visitors", "Visitors")
    home = make_demo_team("home", "Home")
    return GameState(visitors=visitors, home=home)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="O27 Baseball-Cricket Simulator")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for deterministic simulation")
    parser.add_argument("--output", type=str, default=None,
                        help="Write output to this file instead of stdout")
    parser.add_argument("--demo", action="store_true",
                        help="Run Phase 1 scripted demo instead of probabilistic sim")
    parser.add_argument("--no-render", action="store_true",
                        help="Skip Jinja2 rendering; emit raw log lines (debug)")
    args = parser.parse_args()

    if args.demo:
        state = make_demo_state()
        events = _make_demo_events()
        provider = make_script_provider(events)
        renderer = None
    else:
        rng = random.Random(args.seed)
        foxes = make_foxes()
        bears = make_bears()
        state = GameState(visitors=foxes, home=bears)
        provider = ProbabilisticProvider(rng)
        renderer = None if args.no_render else Renderer()

    _, log = run_game(state, provider, renderer=renderer)
    output = "\n".join(log)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
            f.write("\n")
        print(f"Game written to {args.output}")
        print(f"Final score: {state.score_summary()}")
    else:
        print(output)
        if not renderer:
            print(f"\nFinal score: {state.score_summary()}")


def _make_demo_events() -> list:
    """Phase 1 scripted events (kept for --demo flag)."""
    events: list = []
    events += [
        {"type": "called_strike"},
        {"type": "swinging_strike"},
        {"type": "swinging_strike"},
    ]
    events += [
        {"type": "ball"}, {"type": "ball"},
        {"type": "ball"}, {"type": "ball"},
    ]
    events += [
        {"type": "ball_in_play", "choice": "run",
         "outcome": fld.outcome_single([2, 1, 1])},
    ]
    events += [
        {"type": "ball_in_play", "choice": "stay",
         "outcome": fld.outcome_stay_ground_ball([1, 1, 1])},
        {"type": "ball_in_play", "choice": "run",
         "outcome": fld.outcome_single([1, 1, 1])},
    ]
    for _ in range(23):
        events.append({"type": "ball_in_play", "choice": "run",
                       "outcome": fld.outcome_ground_out([0, 0, 0])})
    for _ in range(27):
        events.append({"type": "ball_in_play", "choice": "run",
                       "outcome": fld.outcome_ground_out([0, 0, 0])})
    return events


if __name__ == "__main__":
    main()
