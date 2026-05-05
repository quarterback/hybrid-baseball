"""Property + Monte-Carlo tests for the power-axis redistribution model.

The Phase 10.2 power model replaced an additive HR boost with a
sum-preserving redistribution along named edges in the contact tables.
These tests pin down two claims about that change:

1. **Sum-preservation (analytical).** For arbitrary edge configurations
   and arbitrary power_dev values, `_redistribute` must leave the total
   table weight invariant. This is a property of the function's math —
   should hold over the full input domain, not just sampled values.

2. **HR-rate stability (Monte Carlo).** Over a representative power
   distribution (the league's actual talent table), the redistribution
   should produce league-aggregate HR / 2B / 3B rates within tight
   tolerance of the un-redistributed baseline. This pins down the
   user's framing constraint: "don't increase total offense; produce
   more variety."

Run from repo root:  python -m pytest o27/tests/test_power_redistribute.py -v
Or:                  python o27/tests/test_power_redistribute.py
"""
import random
import sys
from pathlib import Path

# Path-fix so the test runs from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from o27 import config as cfg
from o27.engine.prob import (
    _redistribute,
    _hard_edges, _medium_edges, _weak_edges,
    _CONTACT_TABLES,
)


# ---------------------------------------------------------------------------
# Property test #1 — sum-preservation
# ---------------------------------------------------------------------------

def _table_weight(table: list) -> float:
    return sum(row[3] for row in table)


def test_sum_preservation_hard():
    """HARD edges across the full power_dev domain, sum invariant."""
    base = list(cfg.HARD_CONTACT)
    w0 = _table_weight(base)
    for power_dev in [-1.0, -0.7, -0.4, -0.15, -0.01, 0.0,
                      0.01, 0.15, 0.4, 0.7, 1.0]:
        out = _redistribute(base, _hard_edges(), power_dev)
        w1 = _table_weight(out)
        assert abs(w1 - w0) < 1e-9, (
            f"HARD non-preserving at power_dev={power_dev}: "
            f"before={w0:.6f}, after={w1:.6f}"
        )


def test_sum_preservation_medium():
    base = list(cfg.MEDIUM_CONTACT)
    w0 = _table_weight(base)
    for power_dev in [-1.0, -0.5, 0.0, 0.5, 1.0]:
        out = _redistribute(base, _medium_edges(), power_dev)
        assert abs(_table_weight(out) - w0) < 1e-9


def test_sum_preservation_weak():
    base = list(cfg.WEAK_CONTACT)
    w0 = _table_weight(base)
    for power_dev in [-1.0, -0.5, 0.0, 0.5, 1.0]:
        out = _redistribute(base, _weak_edges(), power_dev)
        assert abs(_table_weight(out) - w0) < 1e-9


def test_identity_at_zero_power_dev():
    """At power_dev=0, redistribute is the identity function."""
    for table_name, table, edges in [
        ("HARD",   cfg.HARD_CONTACT,   _hard_edges()),
        ("MEDIUM", cfg.MEDIUM_CONTACT, _medium_edges()),
        ("WEAK",   cfg.WEAK_CONTACT,   _weak_edges()),
    ]:
        out = _redistribute(list(table), edges, 0.0)
        assert out == list(table), f"{table_name} non-identity at power_dev=0"


def test_directionality():
    """Positive power_dev shifts weight in the named direction;
    negative reverses. Specifically: at power_dev=+1, line_out weight
    drops and HR weight rises in HARD.
    """
    base = dict((r[0], r[3]) for r in cfg.HARD_CONTACT)
    out_pos = _redistribute(list(cfg.HARD_CONTACT), _hard_edges(), +1.0)
    out_neg = _redistribute(list(cfg.HARD_CONTACT), _hard_edges(), -1.0)
    pos_w   = dict((r[0], r[3]) for r in out_pos)
    neg_w   = dict((r[0], r[3]) for r in out_neg)
    # Positive: HR up, line_out down.
    assert pos_w["hr"]       > base["hr"]
    assert pos_w["line_out"] < base["line_out"]
    # Negative: HR down, line_out up.
    assert neg_w["hr"]       < base["hr"]
    assert neg_w["line_out"] > base["line_out"]
    # Single → double on positive; double → single on negative.
    assert pos_w["double"] > base["double"]
    assert pos_w["single"] < base["single"]
    assert neg_w["single"] > base["single"]


# ---------------------------------------------------------------------------
# Monte Carlo — HR / 2B / 3B rate stability across power distribution
# ---------------------------------------------------------------------------

def _normalised_rates(table: list) -> dict[str, float]:
    """Convert weights to expected probabilities by dividing by total."""
    total = sum(r[3] for r in table)
    return {r[0]: (r[3] / total) for r in table}


def _league_expected_hard_rates(power_grades: list[int]) -> dict[str, float]:
    """Expected hard-contact event rates over a population's power grades.

    Returns the population-mean probability of each HARD outcome,
    computed by averaging the redistributed table's normalised rates
    across all `power_grades` values.
    """
    accum: dict[str, float] = {}
    n = len(power_grades)
    for grade in power_grades:
        # Match the engine's grade → unit conversion: grade 50 → 0.5.
        power = max(0.0, min(1.0, grade / 100.0))
        power_dev = (power - 0.5) * 2.0
        out = _redistribute(list(cfg.HARD_CONTACT), _hard_edges(), power_dev)
        for name, prob in _normalised_rates(out).items():
            accum[name] = accum.get(name, 0.0) + prob
    return {k: v / n for k, v in accum.items()}


def _draw_grade(rng: random.Random) -> int:
    """Sample one grade from the league's actual _TALENT_TIERS table."""
    # Reimport here so the test is robust to module-level imports failing
    # on a partial install — keeps the property tests runnable even if
    # the full league module isn't bootstrappable.
    from o27v2 import league as o27_league
    return o27_league._roll_tier_grade(rng)


def test_montecarlo_hr_rate_stability():
    """Average HR/2B/3B rate across the talent population should be
    within 1.5 percentage points of the unmodified table baseline."""
    rng = random.Random(20260505)
    grades = [_draw_grade(rng) for _ in range(20_000)]
    rates  = _league_expected_hard_rates(grades)
    base   = _normalised_rates(cfg.HARD_CONTACT)

    # Tolerance: 1.5pp absolute. The talent distribution centers
    # ~grade 46 (slight below-mean), so HR rate may drift very
    # slightly downward — but no row should move > 1.5pp.
    for name in ("hr", "double", "triple", "single"):
        delta_pp = abs(rates[name] - base[name]) * 100.0
        assert delta_pp < 1.5, (
            f"{name}: redistributed mean rate {rates[name]:.4f} differs from "
            f"baseline {base[name]:.4f} by {delta_pp:.2f} pp"
        )


def test_montecarlo_per_player_spread():
    """An elite-power batter (grade 90) should hit MORE HR + 2B + 3B
    than a low-power batter (grade 25), at the expense of singles
    + line outs. This validates the redistribution actually produces
    archetype spread.
    """
    elite_dev = (0.90 - 0.5) * 2.0
    weak_dev  = (0.25 - 0.5) * 2.0
    elite_rates = _normalised_rates(_redistribute(list(cfg.HARD_CONTACT),
                                                  _hard_edges(), elite_dev))
    weak_rates  = _normalised_rates(_redistribute(list(cfg.HARD_CONTACT),
                                                  _hard_edges(), weak_dev))
    elite_xbh = elite_rates["hr"] + elite_rates["double"] + elite_rates["triple"]
    weak_xbh  = weak_rates["hr"]  + weak_rates["double"]  + weak_rates["triple"]
    assert elite_xbh > weak_xbh + 0.05, (
        f"Elite XBH rate {elite_xbh:.3f} should exceed weak {weak_xbh:.3f} "
        f"by at least 5pp; spread is the whole point."
    )


# ---------------------------------------------------------------------------
# Manual runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for n, v in dict(globals()).items()
             if n.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
