"""Run Expectancy for O27.

Two RE tables, both keyed off `game_pa_log` state stamps:

  RE24-O27 — Run Expectancy by (bases_mask, outs_in_half).
    Standard sabermetric matrix adapted to O27's 27-out half. The
    `outs` axis is bucketed in groups of 3 (0-2, 3-5, …, 24-26) so the
    sample per cell is large enough to be useful — that gives a
    9 × 8 = 72-cell table instead of 27 × 8 = 216.

  RE-by-outs-remaining — coarser 1-D curve: expected runs scored from
    this state to end-of-half, given outs already recorded. Useful for
    the "how much offence is left?" headline plot.

Both tables are built from a single linear scan of every regulation
(phase=0) PA-log row. For each event we know:
  - the bucket (bases_before, outs_before)
  - how many runs scored from THIS PA to the end of the half

The half is identified by (game_id, team_id, phase=0). Within a half
we walk events in (ab_seq, swing_idx) order and accumulate a tail-sum
of runs_scored; that tail-sum at row N is the future-runs measurement
for the bucket associated with row N.

Functions return plain dicts so the web layer can JSON-serialise them.
"""
from __future__ import annotations
from collections import defaultdict
from typing import Iterable

from o27v2 import db


# Bases mask: 3-bit, bit0 = 1B, bit1 = 2B, bit2 = 3B.
_BASES_LABELS = {
    0: "___",
    1: "1__",
    2: "_2_",
    3: "12_",
    4: "__3",
    5: "1_3",
    6: "_23",
    7: "123",
}


def bases_label(mask: int | None) -> str:
    """Render a base-mask as a 3-char glyph (1B/2B/3B occupied).
    `None` (legacy rows) renders as '???'.
    """
    if mask is None:
        return "???"
    return _BASES_LABELS.get(int(mask), "???")


def _outs_bucket(outs: int) -> int:
    """Map raw outs (0..26) to a 3-out bucket (0..8).
    Out 27 is the half-ending out and never appears in outs_before, so
    we never need bucket 9.
    """
    return min(8, max(0, outs // 3))


_OUTS_BUCKET_LABELS = {
    0: "0-2", 1: "3-5", 2: "6-8",
    3: "9-11", 4: "12-14", 5: "15-17",
    6: "18-20", 7: "21-23", 8: "24-26",
}


def _iter_halves() -> Iterable[list[dict]]:
    """Yield each regulation half as a list of PA-log rows ordered
    chronologically. Halves with NULL state stamps (legacy rows) are
    skipped.
    """
    rows = db.fetchall(
        """
        SELECT game_id, team_id, phase, ab_seq, swing_idx,
               outs_before, bases_before, score_diff_before,
               runs_scored
        FROM game_pa_log
        WHERE phase = 0 AND outs_before IS NOT NULL
        ORDER BY game_id, team_id, phase, ab_seq, swing_idx
        """
    )
    current_key = None
    bucket: list[dict] = []
    for r in rows:
        key = (r["game_id"], r["team_id"], r["phase"])
        if key != current_key:
            if bucket:
                yield bucket
            bucket = []
            current_key = key
        bucket.append(r)
    if bucket:
        yield bucket


def build_re_table() -> dict:
    """Build the RE24-O27 matrix.

    Returns:
        {
            "n_halves":  int,
            "n_events":  int,
            "league_avg_runs_per_half": float,
            "cells": [
                {
                    "bases":   int (0..7),
                    "bases_label": "12_" etc,
                    "outs_bucket": int (0..8),
                    "outs_label":  "0-2" etc,
                    "n":       int,        # event count in this cell
                    "re":      float,      # mean future runs
                    "re_std":  float,      # population stdev
                },
                ...
            ],
            # Convenience pivots for the template:
            "matrix": {bases_int: {outs_bucket_int: {n, re}}},
        }
    """
    sums:   dict[tuple[int, int], float] = defaultdict(float)
    sumsq:  dict[tuple[int, int], float] = defaultdict(float)
    counts: dict[tuple[int, int], int]   = defaultdict(int)

    n_halves = 0
    n_events = 0
    total_runs = 0
    for half in _iter_halves():
        n_halves += 1
        # Tail-sum: future_runs[i] = sum of runs_scored[i:]
        n = len(half)
        n_events += n
        future = [0] * (n + 1)
        for i in range(n - 1, -1, -1):
            future[i] = future[i + 1] + (half[i]["runs_scored"] or 0)
        total_runs += future[0]

        for i in range(n):
            ev = half[i]
            bases = int(ev["bases_before"]) if ev["bases_before"] is not None else 0
            outs  = int(ev["outs_before"])  if ev["outs_before"]  is not None else 0
            bucket = _outs_bucket(outs)
            key = (bases, bucket)
            fr = future[i]
            sums[key]   += fr
            sumsq[key]  += fr * fr
            counts[key] += 1

    cells: list[dict] = []
    matrix: dict[int, dict[int, dict]] = {}
    for (bases, ob), n in counts.items():
        mean = sums[(bases, ob)] / n if n else 0.0
        var  = (sumsq[(bases, ob)] / n - mean * mean) if n else 0.0
        std  = max(0.0, var) ** 0.5
        cell = {
            "bases":       bases,
            "bases_label": bases_label(bases),
            "outs_bucket": ob,
            "outs_label":  _OUTS_BUCKET_LABELS[ob],
            "n":           n,
            "re":          round(mean, 3),
            "re_std":      round(std, 3),
        }
        cells.append(cell)
        matrix.setdefault(bases, {})[ob] = {"n": n, "re": cell["re"]}

    cells.sort(key=lambda c: (c["outs_bucket"], c["bases"]))

    return {
        "n_halves": n_halves,
        "n_events": n_events,
        "league_avg_runs_per_half": (total_runs / n_halves) if n_halves else 0.0,
        "cells":    cells,
        "matrix":   matrix,
    }


def build_re_by_outs_remaining() -> dict:
    """Run-expectancy curve indexed purely by outs already recorded.

    Returns:
        {
            "n_halves": int,
            "curve": [
                {"outs_so_far": 0, "outs_remaining": 27, "n": …, "re": …},
                {"outs_so_far": 1, "outs_remaining": 26, "n": …, "re": …},
                ...
                {"outs_so_far": 26, "outs_remaining": 1, "n": …, "re": …},
            ],
        }
    """
    sums:   dict[int, float] = defaultdict(float)
    counts: dict[int, int]   = defaultdict(int)

    n_halves = 0
    for half in _iter_halves():
        n_halves += 1
        n = len(half)
        future = [0] * (n + 1)
        for i in range(n - 1, -1, -1):
            future[i] = future[i + 1] + (half[i]["runs_scored"] or 0)

        for i in range(n):
            outs = int(half[i]["outs_before"] or 0)
            sums[outs]   += future[i]
            counts[outs] += 1

    curve = []
    for o in range(27):
        n = counts.get(o, 0)
        re = (sums[o] / n) if n else 0.0
        curve.append({
            "outs_so_far":    o,
            "outs_remaining": 27 - o,
            "n":              n,
            "re":             round(re, 3),
        })
    return {"n_halves": n_halves, "curve": curve}


def build_re_by_bases() -> dict:
    """Marginal RE by base state (collapsed across outs)."""
    sums:   dict[int, float] = defaultdict(float)
    counts: dict[int, int]   = defaultdict(int)

    for half in _iter_halves():
        n = len(half)
        future = [0] * (n + 1)
        for i in range(n - 1, -1, -1):
            future[i] = future[i + 1] + (half[i]["runs_scored"] or 0)
        for i in range(n):
            bases = int(half[i]["bases_before"] or 0)
            sums[bases]   += future[i]
            counts[bases] += 1

    rows = []
    for b in range(8):
        n = counts.get(b, 0)
        rows.append({
            "bases":       b,
            "bases_label": bases_label(b),
            "n":           n,
            "re":          round((sums[b] / n) if n else 0.0, 3),
        })
    return {"rows": rows}
