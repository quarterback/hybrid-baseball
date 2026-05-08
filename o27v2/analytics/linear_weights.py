"""Empirical linear weights for O27.

The FanGraphs / Tango wOBA constants (BB 0.72, 1B 0.95, … HR 2.05) and
the Bill James Game Score coefficients (-2 per H, -4 per ER, …) were
both calibrated to MLB's ~9 R/G environment. O27 sits at ~22 R/G —
every event is worth more in raw run-expectancy units, and the
relative weights drift (walks gain value vs. HR because the bases are
fuller more often, etc.).

This module derives empirical event run values from `game_pa_log`'s
state stamps, then exports two consumer-facing tables:

    woba_weights  = {"BB": .., "HBP": .., "1B": .., "2B": .., "3B": ..,
                     "HR": ..}

        wOBA scale: each weight is `(run_value_event - run_value_out) *
        OBP-scale-factor`, where the scale-factor is chosen so league
        wOBA == league OBP. Drop-in replacement for the hard-coded
        constants in expected_woba.py and the inline weights at
        web/app.py:_aggregate_batter_rows.

    gsc_coeffs    = {"out": +1, "K_over_out": .., "FO_over_out": ..,
                     "H": .., "ER": .., "HR": .., "BB": .., "HBP": ..,
                     "base": ..}

        Game Score scale (1 GSc point ≈ 0.5 runs, MLB convention
        preserved). `base` is auto-tuned so league-mean GSc == 50.
        Drop-in replacement for `_pitcher_game_score` in web/app.py.

Single source of truth: one walk through the PA log builds the full-
resolution RE per (bases, outs), then a second pass computes the
empirical event run values. BB/HBP run values are derived analytically
(walks aren't in the BIP-only PA log) by averaging the BB transition's
∆RE over the empirical state-occupation distribution.

Computed once per call; if you need cached, wrap at the call site.
"""
from __future__ import annotations
from collections import defaultdict
from typing import Iterable

from o27v2 import db


# Bases mask: 3-bit, bit0 = 1B, bit1 = 2B, bit2 = 3B.
# After-walk state and runs forced home, indexed by before-state.
# (HBP transitions identically.)
_WALK_TRANSITION = {
    0: (1, 0),   # ___ -> 1__   no run
    1: (3, 0),   # 1__ -> 12_   no run
    2: (3, 0),   # _2_ -> 12_   no run (2B unmoved; safe under standard force rules)
    3: (7, 0),   # 12_ -> 123   no run
    4: (5, 0),   # __3 -> 1_3   no run
    5: (7, 0),   # 1_3 -> 123   no run
    6: (7, 0),   # _23 -> 123   no run (3B doesn't have to advance)
    7: (7, 1),   # 123 -> 123   batter walks in
}


def _classify_bip(hit_type: str | None,
                  was_stay: int, stay_credited: int) -> str | None:
    """Map a BIP-event row to one of {1B, 2B, 3B, HR, out}.

    Mirrors expected_woba.py's stay-credit treatment: a credited 2C
    stay counts as a single; an uncredited stay counts as an out.
    Returns None if the row is uninterpretable (legacy NULL hit_type).
    """
    if was_stay and stay_credited:
        return "1B"
    if was_stay and not stay_credited:
        return "out"
    if hit_type in ("hr", "home_run"):
        return "HR"
    if hit_type == "triple":
        return "3B"
    if hit_type == "double":
        return "2B"
    if hit_type in ("single", "infield_single"):
        return "1B"
    # ground_out / fly_out / line_out / error / fielders_choice / etc.
    return "out"


def _iter_events_full() -> Iterable[dict]:
    """All regulation BIP events with full pre/post state stamps."""
    return db.fetchall(
        """
        SELECT game_id, team_id, ab_seq, swing_idx,
               outs_before, bases_before, score_diff_before,
               outs_after,  bases_after,  score_diff_after,
               runs_scored, hit_type, was_stay, stay_credited
        FROM game_pa_log
        WHERE phase = 0
          AND outs_before  IS NOT NULL
          AND bases_before IS NOT NULL
          AND outs_after   IS NOT NULL
          AND bases_after  IS NOT NULL
        ORDER BY game_id, team_id, ab_seq, swing_idx
        """
    )


def _build_re_full() -> tuple[dict[tuple[int, int], float], dict[tuple[int, int], int]]:
    """Single-out-granularity RE per (bases, outs).

    Returns (re_map, n_map). Both are keyed by (bases:int, outs:int).
    re_map[(b, o)] = average future runs scored from state (b, o) to
    end of half. n_map gives the sample count per cell.

    This is the same tail-sum walk as `run_expectancy.build_re_table`
    but without the 3-out bucketing.
    """
    sums:   dict[tuple[int, int], float] = defaultdict(float)
    counts: dict[tuple[int, int], int]   = defaultdict(int)

    rows = db.fetchall(
        """
        SELECT game_id, team_id, ab_seq, swing_idx,
               outs_before, bases_before, runs_scored
        FROM game_pa_log
        WHERE phase = 0 AND outs_before IS NOT NULL
        ORDER BY game_id, team_id, ab_seq, swing_idx
        """
    )
    # Walk halves
    cur_key = None
    half: list[dict] = []
    halves: list[list[dict]] = []
    for r in rows:
        key = (r["game_id"], r["team_id"])
        if key != cur_key:
            if half:
                halves.append(half)
            half = []
            cur_key = key
        half.append(r)
    if half:
        halves.append(half)

    for h in halves:
        n = len(h)
        future = [0] * (n + 1)
        for i in range(n - 1, -1, -1):
            future[i] = future[i + 1] + (h[i]["runs_scored"] or 0)
        for i in range(n):
            b = int(h[i]["bases_before"])
            o = int(h[i]["outs_before"])
            sums[(b, o)]   += future[i]
            counts[(b, o)] += 1

    re_map = {k: sums[k] / counts[k] for k in counts}
    return re_map, counts


def _re_lookup(re_map: dict[tuple[int, int], float],
               bases: int, outs: int) -> float:
    """RE at (bases, outs), with safe fallbacks for thin/end-of-half cells.

    - Valid (b, o) → direct.
    - outs >= 27 (half over) → 0.
    - Missing cell → fall back to (b, min(outs, 26)) then to nearest
      cell in the same bases row. As a last resort 0.
    """
    if outs >= 27:
        return 0.0
    if (bases, outs) in re_map:
        return re_map[(bases, outs)]
    for d in range(1, 5):
        for cand in ((bases, outs - d), (bases, outs + d)):
            if cand in re_map:
                return re_map[cand]
    return 0.0


def _state_occupation(re_n: dict[tuple[int, int], int]) -> dict[tuple[int, int], float]:
    """Empirical probability of each (bases, outs) state at PA-start."""
    total = sum(re_n.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in re_n.items()}


def _walk_run_value(re_map: dict[tuple[int, int], float],
                    state_p: dict[tuple[int, int], float]) -> float:
    """Empirical run value of a walk, averaged over state occupation.

    For each (bases, outs) state, the walk transitions to a new state
    via _WALK_TRANSITION, possibly forcing in a run. Run value =
    runs_forced + RE(bases', outs) - RE(bases, outs). Average weighted
    by the state's PA-start frequency.
    """
    s = 0.0
    for (b, o), p in state_p.items():
        if o >= 27:
            continue
        b2, runs = _WALK_TRANSITION[b]
        s += p * (runs + _re_lookup(re_map, b2, o) - _re_lookup(re_map, b, o))
    return s


def derive_linear_weights() -> dict:
    """Empirically derive wOBA weights and Game Score coefficients.

    Returns:
        {
            "n_events":        int,        # BIP rows used
            "league_re_start": float,      # avg runs / half
            "league_obp":      float,
            "league_woba":     float,      # under the new weights (calibration)

            # Per-event empirical run values (in run units)
            "rv": {"BB", "HBP", "1B", "2B", "3B", "HR", "out", "K"...},

            # Drop-in wOBA weights (OBP-scaled)
            "woba_weights": {
                "BB": float, "HBP": float,
                "1B": float, "2B": float, "3B": float, "HR": float,
            },

            # Game Score coefficients (1 GSc pt = 0.5 runs)
            "gsc_coeffs": {
                "out":          +1.0,           # locked: +1/out preserves outing-length intuition
                "K_over_out":   float (>=0),    # K bonus above a generic out
                "FO_over_out":  float (>=0),    # FO bonus above a generic out
                "H":            float (>=0),    # subtracted per non-HR hit (avg)
                "HR_over_H":    float (>=0),    # additional subtraction for HR (above the H term)
                "BB":           float (>=0),
                "ER":           float (>=0),    # runs allowed -> 2*ER points
                "UER":          float (>=0),
                "base":         float,          # auto-tuned so league-mean GSc = 50
            },
        }

    The wOBA weights are normalized so league-aggregate wOBA equals
    league OBP — same convention as MLB-fit wOBA. This makes the new
    numbers directly comparable in scale to OBP / SLG without
    requiring downstream rescaling.
    """
    re_map, re_n = _build_re_full()
    state_p     = _state_occupation(re_n)
    league_re_start = _re_lookup(re_map, 0, 0)

    # ---- Pass over BIP events: empirical RV per event type.
    rv_sum    = defaultdict(float)
    rv_n      = defaultdict(int)
    n_events  = 0
    for ev in _iter_events_full():
        et = _classify_bip(ev["hit_type"], ev["was_stay"], ev["stay_credited"])
        if et is None:
            continue
        re_b = _re_lookup(re_map, int(ev["bases_before"]), int(ev["outs_before"]))
        re_a = _re_lookup(re_map, int(ev["bases_after"]),  int(ev["outs_after"]))
        rv = (ev["runs_scored"] or 0) + re_a - re_b
        rv_sum[et] += rv
        rv_n[et]   += 1
        n_events   += 1

    rv = {et: (rv_sum[et] / rv_n[et]) if rv_n[et] else 0.0 for et in rv_sum}
    rv["BB"]  = _walk_run_value(re_map, state_p)
    rv["HBP"] = rv["BB"]   # same state transition

    # K run value: subset of "out" — game_pa_log doesn't separately tag
    # Ks (they aren't BIP events). For GSc purposes we approximate
    # K_over_out ≈ small positive bonus from leaving runners in place
    # (no advance) vs. an average BIP out which sometimes advances
    # runners. Use the empirical "out" value as the floor and add a
    # nominal +0.05 run-prevention bonus per K. Tweak with seed-2 data
    # if needed.
    rv["K_over_out"] = 0.05  # placeholder, tunable

    # ---- wOBA weights: OBP-scaled wRAA-per-PA.
    rv_out = rv.get("out", 0.0)
    raw = {et: rv[et] - rv_out for et in ("BB", "HBP", "1B", "2B", "3B", "HR")}

    # Compute league counts to derive the OBP-scale factor and to
    # validate league wOBA == league OBP.
    counts = db.fetchone(
        """
        SELECT COALESCE(SUM(pa), 0)      AS pa,
               COALESCE(SUM(ab), 0)      AS ab,
               COALESCE(SUM(hits), 0)    AS h,
               COALESCE(SUM(doubles), 0) AS d2,
               COALESCE(SUM(triples), 0) AS d3,
               COALESCE(SUM(hr), 0)      AS hr,
               COALESCE(SUM(bb), 0)      AS bb,
               COALESCE(SUM(hbp), 0)     AS hbp
        FROM game_batter_stats
        WHERE phase = 0
        """
    ) or {}
    pa = counts.get("pa") or 0
    h, hr_ct, d2, d3 = counts.get("h", 0), counts.get("hr", 0), counts.get("d2", 0), counts.get("d3", 0)
    bb_ct, hbp_ct = counts.get("bb", 0), counts.get("hbp", 0)
    singles = h - d2 - d3 - hr_ct
    league_obp = (h + bb_ct + hbp_ct) / pa if pa > 0 else 0.0

    # Aggregate raw wRAA-per-PA across the league:
    raw_total = (
        raw["BB"]  * bb_ct  + raw["HBP"] * hbp_ct +
        raw["1B"]  * singles + raw["2B"]  * d2 +
        raw["3B"]  * d3      + raw["HR"]  * hr_ct
    )
    raw_per_pa = (raw_total / pa) if pa > 0 else 0.0
    scale = (league_obp / raw_per_pa) if raw_per_pa > 0 else 1.0
    woba_weights = {et: round(scale * raw[et], 3) for et in raw}

    league_woba = league_obp  # by construction

    # ---- Game Score coefficients (1 pt ≈ 0.5 runs).
    PTS_PER_RUN = 2.0  # MLB convention preserved
    avg_h_rv = (
        rv["1B"] * singles + rv["2B"] * d2 +
        rv["3B"] * d3      + rv["HR"] * hr_ct
    ) / (singles + d2 + d3 + hr_ct) if (singles + d2 + d3 + hr_ct) > 0 else 0.0
    gsc = {
        "out":          1.0,
        "K_over_out":   round(rv["K_over_out"] * PTS_PER_RUN, 2),
        "FO_over_out":  1.0,   # foul-out is a cheap out; preserve +1 bonus convention
        "H":            round(avg_h_rv * PTS_PER_RUN, 2),
        "HR_over_H":    round((rv["HR"] - avg_h_rv) * PTS_PER_RUN, 2),
        "BB":           round(rv["BB"] * PTS_PER_RUN, 2),
        "ER":           round(1.0 * PTS_PER_RUN, 2),  # 1 run by definition
        "UER":          round(1.0 * PTS_PER_RUN * 0.5, 2),  # half-credit (engine convention)
        "base":         50.0,  # placeholder; tuned below
    }

    # Tune `base` so league-mean GSc per starter outing = 50.
    # League average starter line, from game_pitcher_stats:
    avg_line = db.fetchone(
        """
        SELECT
            COUNT(*)  AS n,
            AVG(outs_recorded) AS outs,
            AVG(k)       AS k,
            AVG(hits_allowed) AS h,
            AVG(er)      AS er,
            AVG(unearned_runs) AS uer,
            AVG(bb)      AS bb,
            AVG(hbp_allowed) AS hbp,
            AVG(hr_allowed)  AS hr,
            AVG(fo_induced) AS fo
        FROM game_pitcher_stats
        WHERE phase = 0 AND is_starter = 1
        """
    ) or {}
    if avg_line.get("n") or 0:
        outs = avg_line.get("outs") or 0
        k    = avg_line.get("k") or 0
        h_avg= avg_line.get("h") or 0
        er   = avg_line.get("er") or 0
        uer  = avg_line.get("uer") or 0
        bb   = avg_line.get("bb") or 0
        hbp  = avg_line.get("hbp") or 0
        hr_a = avg_line.get("hr") or 0
        fo   = avg_line.get("fo") or 0
        # Score for the average outing under base=0:
        s = (
            gsc["out"] * outs
            + gsc["K_over_out"] * k
            + gsc["FO_over_out"] * fo
            - gsc["H"] * h_avg
            - gsc["HR_over_H"] * hr_a
            - gsc["BB"] * (bb + hbp)
            - gsc["ER"] * er
            - gsc["UER"] * uer
        )
        gsc["base"] = round(50.0 - s, 2)

    return {
        "n_events":        n_events,
        "league_re_start": round(league_re_start, 3),
        "league_obp":      round(league_obp, 4),
        "league_woba":     round(league_woba, 4),
        "rv":              {k: round(v, 4) for k, v in rv.items()},
        "woba_weights":    woba_weights,
        "gsc_coeffs":      gsc,
    }
