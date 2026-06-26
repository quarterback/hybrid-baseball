#!/usr/bin/env python3
"""Calibrate each real park's engine multipliers so the LIVE O27 resolver
reproduces the park's listed (empirical) park factors.

Why this exists
---------------
The dataset's park factors are empirical MLB ratios (HR 0.79–1.26, AVG ~0.95–
1.13). They cannot be fed to the engine's `park_hr` / `park_hits` knobs
verbatim: `park_hr` scales the HR *distance threshold* inversely
(`hr_bar = fence / park_hr` in o27/engine/batted_ball.py), so a raw factor of
0.79 nearly erases home runs (~25× too strong) and 1.26 nearly triples them.
A measurement pass showed the naive mapping lands RMSE ≈ 0.53 vs the listed
factors; real park geometry already supplies most of the spread.

What it does
------------
For every park with listed factors, it Monte-Carlos the real resolver over one
fixed batted-ball sample (common random numbers vs a neutral reference = the
mean MLB park) and **bisects** the `park_hr` / `park_hits` inputs until the
simulated HR / AVG factors match the listed ones. The solved pair is written
back into the park record under an `"engine"` block; o27v2/real_parks.py reads
it. Geometry stays the primary, spray-dependent driver — these multipliers are
the residual that lands the overall level (and carries non-geometric effects
like Coors' altitude that the fence model can't see).

This couples the stored numbers to the engine's resolver tuning, so re-run it
after changing the RES_* config or the park geometry mapping:

    python3 scripts/calibrate_real_parks.py [--n 30000]

It rewrites o27v2/data/real_parks.json in place (only the `engine` blocks).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from o27.engine.batted_ball import generate_batted_ball, resolve_batted_ball  # noqa: E402
from o27v2 import real_parks as rp  # noqa: E402

_DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..",
                                      "o27v2", "data", "real_parks.json"))
_HIT = {"single", "infield_single", "double", "triple", "hr"}

# League-average batted-ball composition the sample is drawn from. Fixed and
# reused for every park (common random numbers), so a measured difference is
# purely the park, not sampling noise.
_QUAL = (("weak", 0.34), ("medium", 0.45), ("hard", 0.21))


def _build_sample(n: int, seed: int) -> list[tuple]:
    rng = random.Random(seed)

    def q():
        r = rng.random()
        a = 0.0
        for name, w in _QUAL:
            a += w
            if r < a:
                return name
        return "medium"

    out = []
    for _ in range(n):
        power = min(1.0, max(0.0, rng.gauss(0.5, 0.20)))
        bats = "L" if rng.random() < 0.43 else "R"
        ev, la, spray, _t = generate_batted_ball(rng, q(), power, batter_bats=bats)
        out.append((ev, la, spray))
    return out


def _measure(sample, dims, park_hr, park_hits, seed=99):
    # Common random numbers: same resolver seed for every call so the HR/AVG
    # ratios against the neutral baseline are low-variance.
    r = random.Random(seed)
    hr = hits = bip = 0
    for ev, la, spray in sample:
        ht, _s, _c = resolve_batted_ball(r, ev, la, spray, park_dims=dims,
                                         park_hr=park_hr, park_hits=park_hits)
        bip += 1
        if ht in _HIT:
            hits += 1
        if ht == "hr":
            hr += 1
    return hr / bip, hits / bip


def _bisect(fn, target, lo, hi, iters=22, tol=2e-3):
    """Solve fn(x) == target on a monotone-increasing fn; clamp at the bounds."""
    flo, fhi = fn(lo), fn(hi)
    if target <= flo:
        return lo
    if target >= fhi:
        return hi
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        fm = fn(mid)
        if abs(fm - target) <= tol:
            return mid
        if fm < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30_000, help="batted balls per eval")
    args = ap.parse_args()

    parks = json.load(open(_DATA, encoding="utf-8"))
    sample = _build_sample(args.n, seed=20260626)

    # Neutral reference = the mean MLB park, neutral multipliers. Park factors
    # are league-average-relative, so this is the 1.000 anchor.
    mlb_dims = [rp.park_to_dimensions(p) for p in parks if p.get("tier") == "MLB"]
    neutral = {k: round(statistics.mean(d[k] for d in mlb_dims))
               for k in ("lf", "lcf", "cf", "rcf", "rf", "wall_h")}
    base_hr, base_avg = _measure(sample, neutral, 1.0, 1.0)
    print(f"neutral park {neutral} | base HR/BIP={base_hr:.4f} AVG/BIP={base_avg:.4f}")

    hr_err, avg_err = [], []
    calibrated = 0
    for p in parks:
        pf = p.get("park_factors") or {}
        t_hr, t_avg = pf.get("hr"), pf.get("avg")
        if not t_hr or not t_avg:
            continue
        dims = rp.park_to_dimensions(p)

        # HR depends only on park_hr (+ geometry); solve it first.
        phr = _bisect(lambda x: _measure(sample, dims, x, 1.0)[0] / base_hr,
                      t_hr, 0.55, 2.2)
        # Then AVG given park_hr fixed.
        phits = _bisect(lambda x: _measure(sample, dims, phr, x)[1] / base_avg,
                        t_avg, 0.70, 1.45)

        sim_hr, sim_avg = _measure(sample, dims, phr, phits)
        p["engine"] = {
            "park_hr": round(phr, 4),
            "park_hits": round(phits, 4),
            "calibrated_n": args.n,
            "sim_hr_factor": round(sim_hr / base_hr, 3),
            "sim_avg_factor": round(sim_avg / base_avg, 3),
        }
        hr_err.append(sim_hr / base_hr - t_hr)
        avg_err.append(sim_avg / base_avg - t_avg)
        calibrated += 1

    def rmse(xs):
        return (sum(x * x for x in xs) / len(xs)) ** 0.5

    json.dump(parks, open(_DATA, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    open(_DATA, "a").write("\n")
    print(f"calibrated {calibrated} parks -> {_DATA}")
    print(f"HR  factor: rmse={rmse(hr_err):.4f} bias={sum(hr_err)/len(hr_err):+.4f}")
    print(f"AVG factor: rmse={rmse(avg_err):.4f} bias={sum(avg_err)/len(avg_err):+.4f}")


if __name__ == "__main__":
    main()
