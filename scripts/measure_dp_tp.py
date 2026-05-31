"""Measure double-play and triple-play rates across simulated games.

Drives run_game on a fixed seed range (identical seeds before/after a config
change) and reports per-game GIDP/GITP counts plus per-PA rates. Used to
validate the DP-rate bump and the triple-play gate fix.
"""
from __future__ import annotations
import os, sys, random, statistics

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from o27.engine.state import GameState
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.tune import FastRenderer
from o27.main import make_foxes, make_bears


def run(games=400):
    gidp_total = 0
    gitp_total = 0
    pa_total = 0
    games_with_tp = 0
    for seed in range(games):
        random.seed(seed)
        foxes, bears = make_foxes(), make_bears()
        state = GameState(visitors=foxes, home=bears)
        renderer = FastRenderer()
        provider = ProbabilisticProvider(random.Random(seed))
        run_game(state, provider, renderer)
        g = sum(s.gidp for s in renderer._batter_stats.values())
        t = sum(s.gitp for s in renderer._batter_stats.values())
        pa = sum(s.pa for s in renderer._batter_stats.values())
        gidp_total += g
        gitp_total += t
        pa_total += pa
        if t > 0:
            games_with_tp += 1
    print(f"games={games}")
    print(f"GIDP/game = {gidp_total / games:.3f}   ({gidp_total} total)")
    print(f"GITP/game = {gitp_total / games:.4f}   ({gitp_total} total)")
    print(f"GIDP per 100 PA = {100 * gidp_total / pa_total:.3f}")
    print(f"GITP per 100 PA = {100 * gitp_total / pa_total:.4f}")
    print(f"games with >=1 TP = {games_with_tp}/{games} ({100*games_with_tp/games:.1f}%)")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    run(n)
