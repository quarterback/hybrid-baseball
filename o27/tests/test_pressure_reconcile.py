"""PAI's chase-pressure weight must stay tied to the ONE canonical RRR/3O.

PR #274 originally re-implemented RRR/3O inline inside
`o27v2/analytics/pressure.py:_pressure_multiplier`. This locks it to
`o27.stats.team.required_run_rate_3o` so the metric, the chase analytics, and
the manager AI can't drift apart again.
"""
from o27.stats.team import required_run_rate_3o
from o27v2.analytics.pressure import _pressure_multiplier


def _expected(score_diff, outs_before):
    if score_diff is None or outs_before is None or score_diff >= 0:
        return 1.0
    runs_to_lead = abs(int(score_diff)) + 1
    rrr_3o = required_run_rate_3o(runs_to_lead, 0, int(outs_before)) or 0.0
    return 1.0 + min(3.0, rrr_3o / 6.0)


def test_pressure_multiplier_uses_canonical_rrr_3o():
    for score_diff in range(-15, 1):
        for outs_before in range(0, 27):   # valid regulation PA states
            assert _pressure_multiplier(score_diff, outs_before) == _expected(
                score_diff, outs_before
            ), (score_diff, outs_before)


def test_pressure_multiplier_neutral_when_not_trailing():
    assert _pressure_multiplier(0, 5) == 1.0      # tied
    assert _pressure_multiplier(4, 10) == 1.0     # leading
    assert _pressure_multiplier(None, None) == 1.0
    assert _pressure_multiplier(-3, None) == 1.0  # missing state


def test_pressure_multiplier_grows_with_pressure_and_caps():
    # Deeper into a deficit late = higher pressure, capped at 1 + 3.0 = 4.0.
    mild = _pressure_multiplier(-1, 0)     # need 2 over 27 outs
    steep = _pressure_multiplier(-8, 24)   # need 9 in 3 outs
    assert 1.0 < mild < steep <= 4.0
