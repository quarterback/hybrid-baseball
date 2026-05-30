"""A/B harness for the unified per-half "locked in" form.

Mutates o27.config at runtime (prob.py reads cfg via getattr at call time) to
compare scenarios on IDENTICAL seeds. Reports the efficiency-tail metrics that
are the headline for this task: R/H p10/p90 spread + tail shares + run-std.
"""
from __future__ import annotations
import os, sys, random, statistics

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

import o27.config as cfg
from o27.engine.state import GameState
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.tune import FastRenderer
from o27.main import make_foxes, make_bears


def _team_hits(renderer, roster):
    ids = {p.player_id for p in roster}
    return sum(s.hits for pid, s in renderer._batter_stats.items() if pid in ids)


def _pct(xs, p):
    s = sorted(xs); k = (len(s) - 1) * p / 100.0
    lo = int(k); hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def run(games=500):
    pairs = []
    for seed in range(games):
        random.seed(seed)
        foxes, bears = make_foxes(), make_bears()
        state = GameState(visitors=foxes, home=bears)
        renderer = FastRenderer()
        provider = ProbabilisticProvider(random.Random(seed))
        run_game(state, provider, renderer)
        pairs.append((_team_hits(renderer, state.visitors.roster), state.score.get("visitors", 0)))
        pairs.append((_team_hits(renderer, state.home.roster), state.score.get("home", 0)))
    hits = [h for h, _ in pairs]; runs = [r for _, r in pairs]
    r = statistics.correlation(hits, runs)
    rph = [rr / hh for hh, rr in pairs if hh > 0]
    mean_h, mean_r = statistics.mean(hits), statistics.mean(runs)
    overall = mean_r / mean_h
    eff = sum(1 for hh, rr in pairs if hh > 0 and rr >= 1.6 * overall * hh) / len(pairs) * 100
    waste = sum(1 for hh, rr in pairs if hh >= mean_h and rr <= 0.6 * overall * hh) / len(pairs) * 100
    p10, p90 = _pct(rph, 10), _pct(rph, 90)
    return {
        "H": mean_h, "R": mean_r, "Rstd": statistics.pstdev(runs), "r": r,
        "RH": overall, "RHstd": statistics.pstdev(rph),
        "p10": p10, "p90": p90, "spread": p90 - p10, "eff": eff, "waste": waste,
    }


def show(label, m):
    print(f"{label:28s} H={m['H']:5.2f} R={m['R']:5.2f} Rstd={m['Rstd']:5.2f} "
          f"R/H={m['RH']:.3f} r={m['r']:.3f} | p10={m['p10']:.2f} p90={m['p90']:.2f} "
          f"spread={m['spread']:.2f} | eff={m['eff']:.1f}% waste={m['waste']:.1f}%")


def set_gains(slg, score, gidp, risp, xbh):
    cfg.SEQ_FORM_POWER_SCALE = slg
    cfg.SEQ_FORM_SCORE_SCALE = score
    cfg.SEQ_FORM_GIDP_SCALE = gidp
    cfg.RISP_CLUTCH_PENALTY_RELIEF = risp
    cfg.RISP_CLUTCH_XBH_RELIEF = xbh


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=400)
    ap.add_argument("--scenarios", default="off,ported,amp")
    args = ap.parse_args()

    base = dict(SIGMA=cfg.LOCKED_FORM_SIGMA, MIN=cfg.LOCKED_FORM_MIN, MAX=cfg.LOCKED_FORM_MAX,
                slg=cfg.SEQ_FORM_POWER_SCALE, score=cfg.SEQ_FORM_SCORE_SCALE,
                gidp=cfg.SEQ_FORM_GIDP_SCALE, risp=cfg.RISP_CLUTCH_PENALTY_RELIEF,
                xbh=cfg.RISP_CLUTCH_XBH_RELIEF)

    for sc in args.scenarios.split(","):
        sc = sc.strip()
        if sc == "off":
            cfg.LOCKED_FORM_SIGMA = 0.0
            show("OFF (sigma=0)", run(args.games))
        elif sc == "ported":
            cfg.LOCKED_FORM_SIGMA = base["SIGMA"]; cfg.LOCKED_FORM_MIN = base["MIN"]; cfg.LOCKED_FORM_MAX = base["MAX"]
            set_gains(base["slg"], base["score"], base["gidp"], base["risp"], base["xbh"])
            show("UNIFIED ported gains", run(args.games))
        elif sc == "amp":
            # Amplify the shared draw + gains together to widen the tails.
            cfg.LOCKED_FORM_SIGMA = 0.72; cfg.LOCKED_FORM_MIN = 0.06; cfg.LOCKED_FORM_MAX = 2.30
            set_gains(1.70, 1.40, 1.30, 1.05, 1.10)
            show("UNIFIED amplified", run(args.games))
        elif sc == "amp2":
            cfg.LOCKED_FORM_SIGMA = 0.85; cfg.LOCKED_FORM_MIN = 0.05; cfg.LOCKED_FORM_MAX = 2.55
            set_gains(2.00, 1.70, 1.45, 1.15, 1.20)
            show("UNIFIED amplified-2", run(args.games))
        elif sc.startswith("mod="):
            # mod=<sigma>/<slg> : moderate amplification, keep R/G in band.
            sig, slg = (float(x) for x in sc.split("=")[1].split("/"))
            cfg.LOCKED_FORM_SIGMA = sig; cfg.LOCKED_FORM_MIN = 0.08; cfg.LOCKED_FORM_MAX = 2.15
            cfg.LOCKED_FORM_MEAN_BASE = 0.94
            set_gains(slg, 1.20, 1.20, 0.95, 1.00)
            show(f"MOD sig={sig} slg={slg}", run(args.games))
        elif sc.startswith("risp="):
            # risp=<min>/<max> : amplified spread + deeper base RISP penalty to
            # pull the mean back down while keeping the wide tails.
            lo, hi = (float(x) for x in sc.split("=")[1].split("/"))
            cfg.LOCKED_FORM_SIGMA = 0.72; cfg.LOCKED_FORM_MIN = 0.06; cfg.LOCKED_FORM_MAX = 2.30
            cfg.LOCKED_FORM_MEAN_BASE = 0.92
            cfg.RISP_TALENT_PENALTY_MIN = lo; cfg.RISP_TALENT_PENALTY_MAX = hi
            set_gains(1.70, 1.40, 1.30, 1.05, 1.10)
            show(f"AMP risp={lo}/{hi}", run(args.games))
        elif sc.startswith("base="):
            # base=<mean_base> : amplified spread, swept center offset.
            mb = float(sc.split("=")[1])
            cfg.LOCKED_FORM_SIGMA = 0.72; cfg.LOCKED_FORM_MIN = 0.06; cfg.LOCKED_FORM_MAX = 2.30
            cfg.LOCKED_FORM_MEAN_BASE = mb
            set_gains(1.70, 1.40, 1.30, 1.05, 1.10)
            show(f"AMP base={mb}", run(args.games))
