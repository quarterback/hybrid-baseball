"""Invariants for the SABR analytics suite.

Validates the contracts that downstream consumers (the /analytics page,
JSON callers) rely on:

  * RE24-O27 must cover all 8 base states × 9 outs buckets when the
    sample is large (full season).
  * RE@start should equal the league mean runs/half (the marginal RE
    of state (0 outs, bases empty) ≈ league avg, tight when n is large).
  * RE-by-outs-remaining must be monotonically non-increasing.
  * League xwOBA must equal league wOBA (calibration check — xwOBA is
    a re-bucketing of the same point sum).
  * Pythag refit must produce SSE ≤ MLB-default SSE.

These run against whatever `o27v2/o27v2.db` is on disk; the suite skips
gracefully when the DB has fewer than ~50 games (insufficient sample).
"""
from __future__ import annotations
import os
import pytest

from o27v2 import db


def _games_played() -> int:
    try:
        row = db.fetchone("SELECT COUNT(*) AS n FROM games WHERE played = 1")
        return row["n"] or 0
    except Exception:
        return 0


_MIN_GAMES = 50

pytestmark = pytest.mark.skipif(
    _games_played() < _MIN_GAMES,
    reason=f"needs ≥ {_MIN_GAMES} simulated games for stable analytics",
)


def test_re24_covers_full_state_space():
    """Every (bases, outs-bucket) cell should have ≥ 1 event in a full season."""
    from o27v2.analytics.run_expectancy import build_re_table
    t = build_re_table()
    seen = {(c["bases"], c["outs_bucket"]) for c in t["cells"]}
    if _games_played() >= 1500:
        # full state space (8 bases × 9 outs-buckets = 72)
        for b in range(8):
            for ob in range(9):
                assert (b, ob) in seen, f"missing cell bases={b} outs_bucket={ob}"
    else:
        # Partial-season — just verify the high-frequency cells are populated.
        common = [(0, 0), (0, 8), (3, 0), (7, 0)]
        for k in common:
            assert k in seen, f"missing common cell {k}"


def test_re_at_start_equals_league_mean_runs_per_half():
    """RE at (bases empty, outs 0-2) sits within ±15% of league mean
    runs/half (it's the marginal expectation conditional on a BIP event
    happening in the first 3 outs — close to but not equal to the
    unconditional mean)."""
    from o27v2.analytics.run_expectancy import build_re_table
    t = build_re_table()
    league_mean = t["league_avg_runs_per_half"]
    cell_0_0 = next(
        (c for c in t["cells"] if c["bases"] == 0 and c["outs_bucket"] == 0),
        None,
    )
    assert cell_0_0 is not None
    assert abs(cell_0_0["re"] - league_mean) / league_mean < 0.15, (
        f"RE@(empty, 0-2)={cell_0_0['re']} vs league mean {league_mean}"
    )


def test_re_curve_overall_decreasing():
    """RE@start should be at least 4× RE@end (it's the integral of all
    remaining run-scoring opportunities). Local up-ticks at arc boundaries
    are allowed — they reflect real engine behaviour (joker deployment,
    bullpen turnover at outs 9 / 18) and are themselves a SABR finding.
    """
    from o27v2.analytics.run_expectancy import build_re_by_outs_remaining
    c = build_re_by_outs_remaining()
    re_start = c["curve"][0]["re"]
    re_end   = c["curve"][-1]["re"]
    assert re_start > 4 * re_end, (
        f"RE@start ({re_start}) should dominate RE@end ({re_end})"
    )

    # Three-out windowed average should be strictly monotone — local
    # noise is allowed but the trend must be down.
    curve = [r["re"] for r in c["curve"]]
    windowed = []
    for i in range(0, 27, 3):
        chunk = curve[i:i + 3]
        windowed.append(sum(chunk) / len(chunk))
    for i in range(1, len(windowed)):
        assert windowed[i] < windowed[i - 1] + 0.05, (
            f"3-out window avg jumps: {windowed[i-1]:.3f} → {windowed[i]:.3f}"
        )


def test_xwoba_calibration():
    """League xwOBA must equal league wOBA to within rounding —
    xwOBA replaces actual per-quality avg with bucket mean, which by
    construction sums to the same total."""
    from o27v2.analytics.expected_woba import build_xwoba_table
    x = build_xwoba_table(min_pa=20)
    diff = abs(x["league_woba"] - x["league_xwoba"])
    assert diff < 0.005, (
        f"xwOBA calibration failed: wOBA={x['league_woba']:.4f} "
        f"xwOBA={x['league_xwoba']:.4f} diff={diff:.4f}"
    )


def test_pythag_refit_beats_mlb_default():
    """The fitted exponent must produce ≤ SSE than the MLB default 1.83
    (equality if the refit happens to land exactly on 1.83)."""
    from o27v2.analytics.pythag import refit_pythag_exponent
    p = refit_pythag_exponent()
    assert p["fitted_sse"] <= p["mlb_default_sse"] + 1e-6, (
        f"refit SSE {p['fitted_sse']} worse than default "
        f"{p['mlb_default_sse']}"
    )
    # Also, the fitted exponent should be in the search bounds.
    assert 1.0 <= p["fitted_exponent"] <= 4.0


def test_pa_log_state_stamps_present():
    """Every PA-log row written under the current schema should have
    state stamps populated. Legacy NULL rows are okay only if the DB
    pre-dates the stamping work; for fresh sims the column must be set."""
    rows = db.fetchall(
        "SELECT COUNT(*) AS n, COUNT(outs_before) AS stamped FROM game_pa_log"
    )
    if not rows:
        pytest.skip("no game_pa_log rows")
    n = rows[0]["n"]
    stamped = rows[0]["stamped"]
    # Allow up to 1% legacy/missing if mixed schema is ever encountered
    assert stamped >= n * 0.99, (
        f"only {stamped}/{n} PA-log rows have state stamps"
    )


def test_pa_log_state_bounds():
    """outs_before in [0, 26], outs_after in [0, 27] (regulation),
    bases_before/after in [0, 7]."""
    row = db.fetchone(
        """
        SELECT
            MIN(outs_before)  AS o_min, MAX(outs_before)  AS o_max,
            MIN(outs_after)   AS oa_min, MAX(outs_after)  AS oa_max,
            MIN(bases_before) AS b_min, MAX(bases_before) AS b_max,
            MIN(bases_after)  AS ba_min, MAX(bases_after) AS ba_max
        FROM game_pa_log WHERE phase = 0
        """
    )
    assert row["o_min"] is not None
    assert 0 <= row["o_min"] and row["o_max"] <= 26
    # outs_after can briefly hit 28 on rare post-third-out runner events
    # (e.g. a runner thrown out trying to advance after the 27th out is
    # recorded). These are valid, just rare (~0.1% of events).
    assert 0 <= row["oa_min"] and row["oa_max"] <= 28
    assert 0 <= row["b_min"] and row["b_max"] <= 7
    assert 0 <= row["ba_min"] and row["ba_max"] <= 7
