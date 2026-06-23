"""
RRR/3O — cricket-style Required Run Rate normalized to 3 outs.

Covers the pure formula in o27/stats/team.py (DB-free):
  * start-of-chase equals target_runs / 9 (= REQ_RR_FULL × 3);
  * a walk-off (already at/over target) yields 0.0;
  * an exhausted out envelope yields None;
  * an unknown target yields None;
  * a hand-computed mid-chase value;
  * the chase-reconstruction helper rebuilds a sane curve and degrades to None
    on unstamped (legacy) rows.
"""
import math

from o27.stats.team import TeamStats, required_run_rate_3o
from o27v2.web.box_score import compute_chase_rrr


def test_starting_equals_target_over_nine():
    # Target 90 to win over 27 outs: 90/27 R/out × 3 = 90/9 = 10.0 per 3 outs.
    assert required_run_rate_3o(90, 0, 0) == 90 / 9
    assert math.isclose(required_run_rate_3o(90, 0, 0), 10.0)


def test_walkoff_is_zero():
    # Already reached the target: nothing more needed.
    assert required_run_rate_3o(10, 10, 12) == 0.0
    assert required_run_rate_3o(10, 14, 12) == 0.0


def test_exhausted_envelope_is_none():
    assert required_run_rate_3o(10, 5, 27) is None   # 0 outs remaining
    assert required_run_rate_3o(10, 5, 30) is None   # past the envelope


def test_no_target_is_none():
    assert required_run_rate_3o(None, 0, 0) is None


def test_midchase_handcomputed():
    # Need 12 to win, scored 6, 18 outs used → 9 remaining.
    # (12 - 6) / 9 × 3 = 6/9 × 3 = 2.0 per 3 outs.
    assert math.isclose(required_run_rate_3o(12, 6, 18), 2.0)


def test_teamstats_property_matches_function():
    ts = TeamStats(team_name="X", runs=4, outs=9, target_runs=20)
    assert ts.required_run_rate_3o == required_run_rate_3o(20, 4, 9)
    # None until a target is set.
    assert TeamStats(team_name="X", runs=0, outs=0).required_run_rate_3o is None


def test_compute_chase_rrr_curve():
    # Fielding side finished on 8 runs → target 9. Chaser score reconstructed
    # from score_diff_after + first_team_final (8). Rows: scoreless through the
    # 18th out, then a late surge.
    first_team_final = 8
    rows = [
        {"outs_after": 9,  "score_diff_after": -8},   # chaser 0
        {"outs_after": 18, "score_diff_after": -6},   # chaser 2
        {"outs_after": 24, "score_diff_after": 0},    # chaser 8
        {"outs_after": 26, "score_diff_after": 1},    # chaser 9 (walk-off)
    ]
    out = compute_chase_rrr(rows, first_team_final)
    assert out is not None
    assert math.isclose(out["starting"], 9 / 9)       # target 9 → 1.0
    # Pressure peaks late: at 24 outs (3 remaining) needing 9-8=1 run → 1/3×3=1.0;
    # earlier marks are lower, so peak should be >= starting and finite.
    assert out["peak"] is not None and out["peak"] >= out["starting"]
    # Checkpoints are reported for 27 / 18 / 9 outs remaining.
    marks = [m for (m, _) in out["checkpoints"]]
    assert marks == [27, 18, 9]
    # All defined checkpoint values are non-negative.
    for _, v in out["checkpoints"]:
        assert v is None or v >= 0.0


def test_compute_chase_rrr_degrades_on_legacy_rows():
    # Unstamped (NULL) PA log → cannot reconstruct → None.
    rows = [{"outs_after": None, "score_diff_after": None}]
    assert compute_chase_rrr(rows, 8) is None
    assert compute_chase_rrr([], 8) is None
    assert compute_chase_rrr([{"outs_after": 9, "score_diff_after": -8}], None) is None
