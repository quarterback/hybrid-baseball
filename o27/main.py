"""
O27 Simulator — CLI entry point.

Usage (from repo root):
    python -m o27.main [--seed SEED] [--output FILE]
    python o27/main.py  [--seed SEED] [--output FILE]
    pnpm run o27

Phase 2: seeded probabilistic simulation with two pre-defined AI teams.
"""

import sys
import os
import argparse
import random

# When run as `python o27/main.py`, sys.path[0] is the o27/ directory and the
# parent workspace root is not on the path.  Add it so that `o27` resolves as
# a proper importable package (mirrors what `python -m o27.main` does).
_workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _workspace_root not in sys.path:
    sys.path.insert(0, _workspace_root)

from o27.engine.state import GameState, Team, Player
from o27.engine.game import run_game, make_script_provider
from o27.engine.prob import ProbabilisticProvider
from o27.engine import fielding as fld


# ---------------------------------------------------------------------------
# AI team definitions
# ---------------------------------------------------------------------------

def _player(
    pid: str,
    name: str,
    *,
    skill: float = 0.50,
    speed: float = 0.50,
    pitcher_skill: float = 0.50,
    stay_aggressiveness: float = 0.35,
    contact_quality_threshold: float = 0.45,
    is_pitcher: bool = False,
    is_joker: bool = False,
) -> Player:
    return Player(
        player_id=pid,
        name=name,
        skill=skill,
        speed=speed,
        pitcher_skill=pitcher_skill,
        stay_aggressiveness=stay_aggressiveness,
        contact_quality_threshold=contact_quality_threshold,
        is_pitcher=is_pitcher,
        is_joker=is_joker,
    )


def make_foxes() -> Team:
    """
    Millbrook Foxes — balanced, high-contact, speed-oriented team.
    Distinct archetype: slap hitters who steal bases and occasionally use
    the stay mechanic.  Pitching is average.

    Stay aggressiveness deliberately low (≈ 0.06–0.10) so that game-wide
    stay rate lands in the PRD target range of 0.3–1.0 per game.
    """
    roster = [
        # Leadoff — elite contact, blazing speed
        _player("F1",  "M. Ashby",    skill=0.62, speed=0.78,
                stay_aggressiveness=0.10, contact_quality_threshold=0.38),
        # 2-hole — table-setter, good eye
        _player("F2",  "D. Cortez",   skill=0.58, speed=0.72,
                stay_aggressiveness=0.09, contact_quality_threshold=0.35),
        # 3-hole — best all-around hitter
        _player("F3",  "R. Tanner",   skill=0.65, speed=0.60,
                stay_aggressiveness=0.07, contact_quality_threshold=0.32),
        # Cleanup — moderate power, low stay use
        _player("F4",  "L. Moreau",   skill=0.63, speed=0.48,
                stay_aggressiveness=0.04, contact_quality_threshold=0.22),
        # 5-hole — contact hitter
        _player("F5",  "T. Hollis",   skill=0.56, speed=0.65,
                stay_aggressiveness=0.08, contact_quality_threshold=0.34),
        # 6-hole — versatile
        _player("F6",  "C. Nkrumah",  skill=0.52, speed=0.68,
                stay_aggressiveness=0.08, contact_quality_threshold=0.36),
        # 7-hole — solid utility
        _player("F7",  "P. Svensson", skill=0.50, speed=0.62,
                stay_aggressiveness=0.07, contact_quality_threshold=0.32),
        # 8-hole — below average bat, good speed
        _player("F8",  "K. Yamada",   skill=0.44, speed=0.70,
                stay_aggressiveness=0.06, contact_quality_threshold=0.30),
        # 9-hole pitcher — weak bat, average pitching
        _player("F9",  "S. Okafor",   skill=0.30, speed=0.38,
                pitcher_skill=0.52,
                stay_aggressiveness=0.03, contact_quality_threshold=0.18,
                is_pitcher=True),
        # Joker 1 — elite designated hitter
        _player("FJ1", "V. Ramos",    skill=0.70, speed=0.52,
                stay_aggressiveness=0.05, contact_quality_threshold=0.26,
                is_joker=True),
        # Joker 2 — high skill, speed threat
        _player("FJ2", "B. Lepage",   skill=0.67, speed=0.66,
                stay_aggressiveness=0.07, contact_quality_threshold=0.31,
                is_joker=True),
        # Joker 3 — contact specialist
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
    Distinct archetype: sluggers who swing for the fences; better pitching
    than Foxes but fewer stolen bases and very rare stay plays.
    """
    roster = [
        # Leadoff — modest contact, average speed for this team
        _player("B1",  "A. Driscoll", skill=0.56, speed=0.52,
                stay_aggressiveness=0.05, contact_quality_threshold=0.20),
        # 2-hole — power with some patience
        _player("B2",  "N. Petrov",   skill=0.60, speed=0.42,
                stay_aggressiveness=0.04, contact_quality_threshold=0.16),
        # 3-hole — best hitter, power focus
        _player("B3",  "O. Fitzgerald", skill=0.68, speed=0.36,
                stay_aggressiveness=0.03, contact_quality_threshold=0.13),
        # Cleanup — biggest bat on the team
        _player("B4",  "J. Makinde",  skill=0.66, speed=0.34,
                stay_aggressiveness=0.02, contact_quality_threshold=0.11),
        # 5-hole — solid power bat
        _player("B5",  "R. Colburn",  skill=0.56, speed=0.40,
                stay_aggressiveness=0.04, contact_quality_threshold=0.16),
        # 6-hole — average all-around
        _player("B6",  "T. Wachowski", skill=0.50, speed=0.44,
                stay_aggressiveness=0.05, contact_quality_threshold=0.20),
        # 7-hole — below average bat
        _player("B7",  "E. Solberg",  skill=0.46, speed=0.48,
                stay_aggressiveness=0.05, contact_quality_threshold=0.20),
        # 8-hole — weak bat, slightly more patient
        _player("B8",  "H. Mwangi",   skill=0.40, speed=0.46,
                stay_aggressiveness=0.04, contact_quality_threshold=0.18),
        # 9-hole pitcher — weak bat, stronger pitching (Bears' ace)
        _player("B9",  "C. Lindqvist", skill=0.28, speed=0.35,
                pitcher_skill=0.62,
                stay_aggressiveness=0.02, contact_quality_threshold=0.10,
                is_pitcher=True),
        # Joker 1 — power masher, almost never stays
        _player("BJ1", "D. Oduya",    skill=0.74, speed=0.38,
                stay_aggressiveness=0.02, contact_quality_threshold=0.08,
                is_joker=True),
        # Joker 2 — balanced power
        _player("BJ2", "M. Castillo", skill=0.71, speed=0.40,
                stay_aggressiveness=0.02, contact_quality_threshold=0.10,
                is_joker=True),
        # Joker 3 — slightly more contact-oriented
        _player("BJ3", "P. Graves",   skill=0.68, speed=0.44,
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
    """Build a generic demo roster (used by Phase 1 tests)."""
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
    """Build a minimal GameState with demo rosters (Phase 1 compatibility)."""
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
    args = parser.parse_args()

    if args.demo:
        # Phase 1 scripted demo (kept for reference).
        state = make_demo_state()
        events = _make_demo_events()
        provider = make_script_provider(events)
    else:
        # Phase 2 probabilistic simulation.
        rng = random.Random(args.seed)
        foxes = make_foxes()
        bears = make_bears()
        state = GameState(visitors=foxes, home=bears)
        provider = ProbabilisticProvider(rng)

    _, log = run_game(state, provider)
    output = "\n".join(log)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Game written to {args.output}")
        print(f"Final score: {state.score_summary()}")
    else:
        print(output)
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
