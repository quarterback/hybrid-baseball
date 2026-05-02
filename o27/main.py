"""
O27 Simulator — CLI entry point.

Usage:
    python main.py [--seed SEED] [--output FILE]

Phase 1: runs a scripted demonstration game (no probability models).
Phase 2: will use seeded random probability models.
"""

import sys
import os
import argparse

# Allow imports from the o27/ directory.
sys.path.insert(0, os.path.dirname(__file__))

from engine.state import GameState, Team, Player
from engine.game import run_game, make_script_provider
from engine import fielding as fld


# ---------------------------------------------------------------------------
# Demo roster builder (Phase 1 stub)
# ---------------------------------------------------------------------------

def make_demo_player(pid: str, name: str, is_pitcher=False, is_joker=False) -> Player:
    return Player(
        player_id=pid,
        name=name,
        is_pitcher=is_pitcher,
        is_joker=is_joker,
    )


def make_demo_team(team_id: str, name: str) -> Team:
    """Build a 12-batter demo roster (9 position players + 3 jokers)."""
    prefix = team_id[0].upper()
    players = []
    for i in range(1, 10):
        is_p = (i == 9)  # slot 9 is the pitcher
        p = make_demo_player(f"{prefix}{i}", f"{name[:3]}-{i}",
                             is_pitcher=is_p)
        players.append(p)
    jokers = []
    for j in range(1, 4):
        jk = make_demo_player(f"{prefix}J{j}", f"{name[:3]}-J{j}",
                              is_joker=True)
        jokers.append(jk)
        players.append(jk)

    t = Team(
        team_id=team_id,
        name=name,
        roster=players,
        lineup=players[:12],   # full 12 in order
        jokers_available=list(jokers),
    )
    # Pitcher starts on the mound for the opposing team.
    return t


def make_demo_state() -> GameState:
    """Build a minimal demo GameState for Phase 1."""
    visitors = make_demo_team("visitors", "Visitors")
    home = make_demo_team("home", "Home")
    state = GameState(visitors=visitors, home=home)
    # Set starting pitchers.
    state.current_pitcher_id = home.roster[8].player_id   # pitcher bats 9th
    return state


# ---------------------------------------------------------------------------
# Phase 1 demo: scripted events
# ---------------------------------------------------------------------------

def make_demo_events() -> list:
    """
    A short scripted sequence that demonstrates the engine.
    Produces a few plate appearances in the top half only — enough to show
    the rules working.  The full Phase 2 engine will replace this.
    """
    events: list = []

    # PA 1: strikeout
    events += [
        {"type": "called_strike"},
        {"type": "swinging_strike"},
        {"type": "swinging_strike"},
    ]

    # PA 2: walk
    events += [
        {"type": "ball"},
        {"type": "ball"},
        {"type": "ball"},
        {"type": "ball"},
    ]

    # PA 3: single — runner on 1B scores
    events += [
        {"type": "ball_in_play",
         "choice": "run",
         "outcome": fld.outcome_single([2, 1, 1])},  # runner on 1B advances to 3B
    ]

    # PA 4: stay play — runner on 3B and 1B, batter stays
    events += [
        # First pitch of new at-bat — ball in play, batter stays
        {"type": "ball_in_play",
         "choice": "stay",
         "outcome": fld.outcome_stay_ground_ball([1, 1, 1])},
        # Continuation: batter gets another pitch, hits again and runs
        {"type": "ball_in_play",
         "choice": "run",
         "outcome": fld.outcome_single([1, 1, 1])},
    ]

    # Fill remaining 23 outs with ground outs to end the top half.
    for _ in range(23):
        events.append({
            "type": "ball_in_play",
            "choice": "run",
            "outcome": fld.outcome_ground_out([0, 0, 0]),
        })

    # Bottom half: 27 outs.
    for _ in range(27):
        events.append({
            "type": "ball_in_play",
            "choice": "run",
            "outcome": fld.outcome_ground_out([0, 0, 0]),
        })

    return events


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="O27 Baseball-Cricket Simulator")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (used in Phase 2)")
    parser.add_argument("--output", type=str, default=None,
                        help="Write output to this file instead of stdout")
    args = parser.parse_args()

    state = make_demo_state()
    events = make_demo_events()
    provider = make_script_provider(events)

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


if __name__ == "__main__":
    main()
