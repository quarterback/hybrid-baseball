"""
Report O27's home-runs-by-count distribution.

NOTE: the MLB column is a DIAGNOSTIC MIRROR, not a target. O27 is its own sport;
the goal is a coherent count -> power curve (low on 0-0 / when buried, high on a
full count / ahead), not matching MLB. "diff" / the total-variation line are
just convenient flatness detectors.

The engine models pitches and a ball-strike count but does NOT persist the
count at which each plate-appearance outcome occurs (see db.py game_pa_log —
no balls/strikes columns). So we can't query the live DB. Instead we run a
fresh batch of headless games and capture state.count at the instant the
provider yields each home-run ball_in_play event.

Why that instant is "the count the HR was hit on": the provider returns the
event, and apply_event() only afterward mutates the count (pa.py increments
balls/strikes per pitch and resets at PA end). So when a ball_in_play is
produced, state.count still holds the pre-contact count — the count the
batter put the ball in play on. This matches how Retrosheet/the reference
chart bucket home runs.

Usage:
    python3 scripts/hr_by_count.py --games 2000 --seed 1
"""
from __future__ import annotations

import argparse
import os
import random
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from o27.engine.state import GameState
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.main import make_foxes, make_bears

# Real-MLB reference (Philip Bump / CT Insider, Retrosheet, 1910-2025).
# (balls, strikes) -> (pct_of_all_HR, raw_count)
REAL = {
    (0, 0): (18.3, 35578),
    (1, 0): (12.0, 23301),
    (2, 0): (5.6, 10858),
    (3, 0): (0.6, 1233),
    (0, 1): (9.7, 18742),
    (1, 1): (11.1, 21561),
    (2, 1): (8.3, 16128),
    (3, 1): (4.9, 9589),
    (0, 2): (3.5, 6857),
    (1, 2): (7.6, 14678),
    (2, 2): (9.1, 17669),
    (3, 2): (9.2, 17920),
}


class CountCapturingProvider:
    """Wrap ProbabilisticProvider; record (balls, strikes) on every HR."""

    def __init__(self, rng, tally, bip_tally=None):
        self._inner = ProbabilisticProvider(rng)
        self._tally = tally
        self._bip = bip_tally

    def __call__(self, state):
        event = self._inner(state)
        if event and event.get("type") == "ball_in_play":
            key = (state.count.balls, state.count.strikes)
            if self._bip is not None:
                self._bip[key] = self._bip.get(key, 0) + 1
            outcome = event.get("outcome") or {}
            if outcome.get("hit_type") in ("hr", "home_run"):
                self._tally[key] = self._tally.get(key, 0) + 1
        return event


def run(games: int, base_seed: int):
    tally: dict = {}
    bip: dict = {}
    for i in range(games):
        rng = random.Random(base_seed + i)
        state = GameState(visitors=make_foxes(), home=make_bears())
        provider = CountCapturingProvider(rng, tally, bip)
        run_game(state, provider, renderer=None)
    return tally, bip


def report(tally: dict, bip: dict) -> None:
    total = sum(tally.values())
    bip_total = sum(bip.values()) or 1
    if not total:
        print("No home runs recorded — increase --games.")
        return

    print(f"\nTotal home runs sampled: {total}   (balls in play: {sum(bip.values())})\n")
    header = "count  |  O27 HR%  O27 n  |  MLB HR% |  diff   |  O27 BIP%  HR/BIP"
    print(header)
    print("-" * len(header))

    # Order rows the way the chart reads: strikes outer (0,1,2), balls inner.
    rows = [(b, s) for s in range(3) for b in range(4)]
    sum_abs_diff = 0.0
    for (b, s) in rows:
        n = tally.get((b, s), 0)
        o27_pct = 100.0 * n / total
        mlb_pct = REAL[(b, s)][0]
        diff = o27_pct - mlb_pct
        sum_abs_diff += abs(diff)
        bn = bip.get((b, s), 0)
        bip_pct = 100.0 * bn / bip_total
        hr_per_bip = (100.0 * n / bn) if bn else 0.0
        print(f" {b}-{s}   | {o27_pct:6.1f}  {n:6d} | {mlb_pct:6.1f}  | {diff:+6.1f}  | "
              f"{bip_pct:7.1f}   {hr_per_bip:5.2f}%")

    print("-" * len(header))
    print(f"Sum of absolute pct differences (total variation x2): {sum_abs_diff:.1f}")

    # Aggregate readings that the chart is really about.
    first_pitch = 100.0 * tally.get((0, 0), 0) / total
    two_strike = 100.0 * sum(tally.get((b, 2), 0) for b in range(4)) / total
    ahead = 100.0 * sum(tally.get((b, s), 0)
                        for (b, s) in [(1, 0), (2, 0), (3, 0), (2, 1), (3, 1)]) / total
    print(f"\nFirst-pitch (0-0) HRs:  O27 {first_pitch:5.1f}%  vs MLB 18.3%")
    print(f"Two-strike   HRs:       O27 {two_strike:5.1f}%  vs MLB 20.2%")
    print(f"Hitter's-count HRs:     O27 {ahead:5.1f}%  vs MLB 31.4%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    tally, bip = run(args.games, args.seed)
    report(tally, bip)


if __name__ == "__main__":
    main()
