"""Ad-hoc measurement: how tightly are team hits coupled to team runs?

Runs N games, collects per-team (hits, runs) pairs, and reports:
  - Pearson correlation r(H, R)
  - distribution of runs-per-hit (R/H)
  - distribution of hits and runs
  - "few hits / many runs" and "many hits / few runs" tail rates

Not a calibration target; a diagnostic for the H~R variance tuning task.
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import sys

_workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _workspace_root not in sys.path:
    sys.path.insert(0, _workspace_root)

from o27.engine.state import GameState
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.tune import FastRenderer
from o27.main import make_foxes, make_bears


def _team_hits(renderer: FastRenderer, roster) -> int:
    ids = {p.player_id for p in roster}
    return sum(s.hits for pid, s in renderer._batter_stats.items() if pid in ids)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=500)
    args = ap.parse_args()

    pairs: list[tuple[int, int]] = []  # (hits, runs) per team per game
    for seed in range(args.games):
        random.seed(seed)
        foxes = make_foxes()
        bears = make_bears()
        state = GameState(visitors=foxes, home=bears)
        renderer = FastRenderer()
        provider = ProbabilisticProvider(random.Random(seed))
        run_game(state, provider, renderer)

        v_h = _team_hits(renderer, state.visitors.roster)
        h_h = _team_hits(renderer, state.home.roster)
        v_r = state.score.get("visitors", 0)
        h_r = state.score.get("home", 0)
        pairs.append((v_h, v_r))
        pairs.append((h_h, h_r))

    hits = [h for h, _ in pairs]
    runs = [r for _, r in pairs]
    # Pearson r
    r = statistics.correlation(hits, runs) if len(set(hits)) > 1 else float("nan")
    # R/H per team-game (guard against 0 hits)
    rph = [rr / hh for hh, rr in pairs if hh > 0]

    n = len(pairs)
    mean_h = statistics.mean(hits)
    mean_r = statistics.mean(runs)
    # Tail signals: relative to each game's expectation.
    overall_rph = mean_r / mean_h
    # "efficient" = scored >=1.6x expected runs given hits; "wasteful" = <=0.6x
    eff = sum(1 for hh, rr in pairs if hh > 0 and rr >= 1.6 * overall_rph * hh)
    waste = sum(1 for hh, rr in pairs if hh >= mean_h and rr <= 0.6 * overall_rph * hh)

    print(f"team-games               {n}")
    print(f"mean hits/team           {mean_h:.2f}  (std {statistics.pstdev(hits):.2f})")
    print(f"mean runs/team           {mean_r:.2f}  (std {statistics.pstdev(runs):.2f})")
    print(f"corr r(H,R)              {r:.4f}")
    print(f"overall R/H              {overall_rph:.3f}")
    print(f"R/H mean (per team-game) {statistics.mean(rph):.3f}  (std {statistics.pstdev(rph):.3f})")
    print(f"R/H 10th/50th/90th pct   "
          f"{_pct(rph,10):.2f} / {_pct(rph,50):.2f} / {_pct(rph,90):.2f}")
    print(f"efficient games (few H, many R)  {eff/n*100:.1f}%")
    print(f"wasteful games (many H, few R)   {waste/n*100:.1f}%")


def _pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    k = (len(s) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


if __name__ == "__main__":
    main()
