"""Quantify a PITCH_BASE first-pitch-contact change: HR-by-count + run env.

Run baseline vs a candidate PITCH_BASE override, headless, and report both
the HR-by-count distribution and run-environment proxies (runs/game, K%,
BB%) so the tradeoff of dialing back first-pitch aggression is visible.
"""
import os, sys, random
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from o27 import config as cfg
from o27.engine.state import GameState
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.main import make_foxes, make_bears
import scripts.hr_by_count as h

REAL = h.REAL


class Instr:
    def __init__(self, rng, agg):
        self._inner = ProbabilisticProvider(rng)
        self.a = agg

    def __call__(self, state):
        ev = self._inner(state)
        if ev:
            t = ev.get("type")
            b, s = state.count.balls, state.count.strikes
            if t in ("ball", "called_strike", "swinging_strike", "foul",
                     "foul_tip_caught", "ball_in_play", "hit_by_pitch"):
                self.a["pitches"] += 1
            if t == "ball_in_play":
                self.a["bip"] += 1
                oc = ev.get("outcome") or {}
                if oc.get("hit_type") in ("hr", "home_run"):
                    self.a["hr"][(b, s)] = self.a["hr"].get((b, s), 0) + 1
            elif t == "ball" and b == 3:
                self.a["bb"] += 1
            elif t in ("called_strike", "swinging_strike", "foul_tip_caught") and s == 2:
                self.a["k"] += 1
            elif t == "hit_by_pitch":
                self.a["hbp"] += 1
        return ev


def batch(games, seed):
    a = {"hr": {}, "bip": 0, "bb": 0, "k": 0, "hbp": 0, "runs": 0, "pitches": 0}
    for i in range(games):
        rng = random.Random(seed + i)
        st = GameState(visitors=make_foxes(), home=make_bears())
        run_game(st, Instr(rng, a), renderer=None)
        a["runs"] += sum(st.score.values())
    return a


def report(label, a, games):
    hr = a["hr"]; tot = sum(hr.values())
    pa = a["bip"] + a["bb"] + a["k"] + a["hbp"]
    p00 = 100 * hr.get((0, 0), 0) / tot
    deep = 100 * sum(hr.get(k, 0) for k in [(2, 2), (3, 2)]) / tot
    ahead = 100 * sum(hr.get(k, 0) for k in [(1, 0), (2, 0), (3, 0), (2, 1), (3, 1)]) / tot
    tv = 0.5 * sum(abs(100 * hr.get((b, s), 0) / tot - REAL[(b, s)][0])
                   for s in range(3) for b in range(4))
    print(f"\n== {label} ==")
    print(f"  runs/game {a['runs']/games:5.2f} | K% {100*a['k']/pa:4.1f} | BB% {100*a['bb']/pa:4.1f} "
          f"| HR/game {tot/games:4.2f} | BIP/game {a['bip']/games:4.1f} | pitches/PA {a['pitches']/pa:4.2f}")
    print(f"  0-0 HR% {p00:4.1f} | 0-strike-row HR% {100*sum(hr.get((b,0),0) for b in range(4))/tot:4.1f} "
          f"| deep(2-2,3-2)% {deep:4.1f} | ahead% {ahead:4.1f} | TVdist-vs-MLB {tv:4.1f}")
    print(f"  (MLB ref: 0-0 18.3 | 0-strike-row 36.5 | deep 18.3 | ahead 31.4)")
    return p00, deep, tv


if __name__ == "__main__":
    GAMES = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
    SEED = 9000

    base = batch(GAMES, SEED)
    report("BASELINE (current PITCH_BASE)", base, GAMES)

    # Philosophy: batters are SMARTER. Far less first-pitch swinging — the
    # removed contact is routed into TAKEN pitches (called strikes + balls),
    # not into whiffs, so the count deepens and pitchers work harder without
    # a three-true-outcomes K explosion. Two-strike contact is preserved/lifted
    # (protect the plate). (p_ball, p_called_strike, p_swinging, p_foul, p_contact)
    MODERATE = {
        (0, 0): (0.36, 0.24, 0.11, 0.15, 0.14),
        (1, 0): (0.40, 0.21, 0.09, 0.15, 0.15),
        (2, 0): (0.45, 0.18, 0.06, 0.15, 0.16),
        (3, 0): (0.52, 0.20, 0.04, 0.14, 0.10),
        (0, 1): (0.32, 0.18, 0.14, 0.19, 0.17),
        (1, 1): (0.35, 0.15, 0.13, 0.20, 0.17),
        (2, 1): (0.39, 0.13, 0.10, 0.20, 0.18),
        (3, 1): (0.44, 0.12, 0.08, 0.21, 0.15),
        (0, 2): (0.25, 0.10, 0.16, 0.29, 0.20),
        (1, 2): (0.28, 0.08, 0.16, 0.28, 0.20),
        (2, 2): (0.32, 0.07, 0.14, 0.26, 0.21),
        (3, 2): (0.36, 0.05, 0.12, 0.25, 0.22),
    }
    # Stronger take: push 0-strike contact down near 0.10-0.12.
    STRONG = {
        (0, 0): (0.37, 0.28, 0.10, 0.15, 0.10),
        (1, 0): (0.41, 0.24, 0.08, 0.15, 0.12),
        (2, 0): (0.46, 0.20, 0.05, 0.15, 0.14),
        (3, 0): (0.55, 0.20, 0.03, 0.14, 0.08),
        (0, 1): (0.33, 0.20, 0.13, 0.20, 0.14),
        (1, 1): (0.36, 0.17, 0.12, 0.20, 0.15),
        (2, 1): (0.40, 0.14, 0.09, 0.21, 0.16),
        (3, 1): (0.46, 0.13, 0.07, 0.21, 0.13),
        (0, 2): (0.25, 0.10, 0.16, 0.29, 0.20),
        (1, 2): (0.28, 0.08, 0.16, 0.28, 0.20),
        (2, 2): (0.32, 0.07, 0.14, 0.26, 0.21),
        (3, 2): (0.36, 0.05, 0.12, 0.25, 0.22),
    }
    cfg.PITCH_BASE = MODERATE
    report("MODERATE work-the-count", batch(GAMES, SEED), GAMES)
    cfg.PITCH_BASE = STRONG
    report("STRONG work-the-count", batch(GAMES, SEED), GAMES)

    # SMART: take early like STRONG, but PROTECT at two strikes — fewer whiffs,
    # more fouls (longer ABs, pitcher works harder) — so the K rate doesn't
    # balloon. A smart hitter fouls off the tough two-strike pitch.
    SMART = {
        (0, 0): (0.37, 0.27, 0.10, 0.15, 0.11),
        (1, 0): (0.41, 0.23, 0.08, 0.15, 0.13),
        (2, 0): (0.46, 0.19, 0.05, 0.15, 0.15),
        (3, 0): (0.54, 0.21, 0.03, 0.13, 0.09),
        (0, 1): (0.33, 0.19, 0.13, 0.20, 0.15),
        (1, 1): (0.36, 0.16, 0.12, 0.20, 0.16),
        (2, 1): (0.40, 0.13, 0.09, 0.21, 0.17),
        (3, 1): (0.46, 0.13, 0.07, 0.20, 0.14),
        (0, 2): (0.25, 0.10, 0.12, 0.33, 0.20),
        (1, 2): (0.28, 0.08, 0.12, 0.31, 0.21),
        (2, 2): (0.32, 0.07, 0.10, 0.29, 0.22),
        (3, 2): (0.36, 0.05, 0.08, 0.28, 0.23),
    }
    cfg.PITCH_BASE = SMART
    report("SMART (take early, protect late)", batch(GAMES, SEED), GAMES)
