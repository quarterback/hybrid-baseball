"""Sweep batted-ball texture depth toward an H/R target. Mutates o27.config at
runtime (prob.py reads cfg via getattr at call time)."""
import os, sys, random, statistics
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)
import o27.config as cfg
from o27.engine.state import GameState
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.tune import FastRenderer
from o27.main import make_foxes, make_bears


def _hits(rn, roster):
    ids = {p.player_id for p in roster}
    return sum(s.hits for pid, s in rn._batter_stats.items() if pid in ids)


def run(games=350):
    pairs = []
    for seed in range(games):
        random.seed(seed)
        fox, bear = make_foxes(), make_bears()
        st = GameState(visitors=fox, home=bear)
        rn = FastRenderer()
        run_game(st, ProbabilisticProvider(random.Random(seed)), rn)
        pairs.append((_hits(rn, fox.roster), st.score.get("visitors", 0)))
        pairs.append((_hits(rn, bear.roster), st.score.get("home", 0)))
    H = [h for h, _ in pairs]; R = [r for _, r in pairs]
    mh, mr = statistics.mean(H), statistics.mean(R)
    return mh, mr, mr / mh, statistics.pstdev(R)


def setshift(dr, gr, ln, fl):
    cfg.BATTED_BALL_SCORE_SHIFT = {"dribbler": dr, "grounder": gr,
                                   "liner": ln, "flyball": fl}


def setweights(weak_gb, med_gb):
    # bump grounder/dribbler share at weak+medium contact
    cfg.BATTED_BALL_WEIGHTS = {
        "weak":   (weak_gb * 0.45, weak_gb * 0.55, 0.14, 0.04),
        "medium": (med_gb * 0.25, med_gb * 0.75, 0.80 - med_gb, 0.10),
        "hard":   (0.0, 0.10, 0.55, 0.35),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=300)
    ap.add_argument("--scenarios", default="cur")
    args = ap.parse_args()
    for sc in args.scenarios.split(","):
        sc = sc.strip()
        if sc == "cur":
            pass
        elif sc == "off":
            setshift(0, 0, 0, 0)
        elif sc.startswith("d"):
            # d<dribbler>/<grounder>  e.g. d0.9/0.6
            dr, gr = (float(x) for x in sc[1:].split("/"))
            setshift(-dr, -gr, +0.06, 0.0)
        elif sc.startswith("w"):
            # w<weak_gb>/<med_gb>@<dribbler>/<grounder> — weights + shift
            wpart, spart = sc[1:].split("@")
            wgb, mgb = (float(x) for x in wpart.split("/"))
            dr, gr = (float(x) for x in spart.split("/"))
            setweights(wgb, mgb)
            setshift(-dr, -gr, +0.06, 0.0)
        mh, mr, rh, rstd = run(args.games)
        print(f"{sc:22s} H={mh:5.2f} R={mr:5.2f} R/H={rh:.3f} (H/R={1/rh:.2f}) Rstd={rstd:.2f}")
