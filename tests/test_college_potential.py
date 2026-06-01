"""Pin the college potential growth model against the worked examples in
the spec (o27v2/college_potential.py). The two examples are:

  P0=30, interest=214% (tier 3, mult 1.6) → 30 → 50 → 62 → 70 → 76
  P0=60, interest=80%  (tier 1, mult 1.0) → 60 → 65 → 69 → 73 → 77
"""
import random
from o27v2 import college_potential as cp


def _round_path(history):
    return [round(p) for p in history]


def test_super_bloomer_trajectory():
    """30-base, 214% interest, tier-3 cap multiplier — the headline hidden-gem case."""
    hist = cp.career_trajectory(30, 214)
    assert _round_path(hist) == [30, 50, 62, 70, 76], hist


def test_moderate_high_base_trajectory():
    """60-base, 80% interest, tier-1 cap multiplier — the steady senior."""
    hist = cp.career_trajectory(60, 80)
    assert _round_path(hist) == [60, 65, 69, 73, 77], hist


def test_tier_multiplier_boundaries():
    """Spec tier breakpoints: 0-100 → 1.0×, 101-200 → 1.3×, 201-320 → 1.6×."""
    assert cp._tier_multiplier(0)   == 1.0
    assert cp._tier_multiplier(100) == 1.0
    assert cp._tier_multiplier(101) == 1.3
    assert cp._tier_multiplier(200) == 1.3
    assert cp._tier_multiplier(201) == 1.6
    assert cp._tier_multiplier(320) == 1.6


def test_cap_base_shape():
    """C_MAX at floor (P=20), C_MIN floor at high P. Linear in between,
    rounded half-up to the spec's precomputed integer table."""
    assert cp._cap_base(20) == 15               # C_MAX
    assert cp._cap_base(80) == 4                # C_MIN floor
    # Midpoint P=50: raw 7.5 → rounds half-up to 8 per spec table
    assert cp._cap_base(50) == 8
    # P=62: raw 4.5 → rounds half-up to 5 (the year-3 super-bloomer value)
    assert cp._cap_base(62) == 5
    # P=70: raw 2.5, clamps to C_MIN=4
    assert cp._cap_base(70) == 4


def test_monotone_no_loss():
    """'Nobody loses anything' — potential is non-decreasing."""
    rng = random.Random(2026)
    for _ in range(50):
        P0     = rng.uniform(25, 75)
        rate   = rng.randint(1, 320)
        hist   = cp.career_trajectory(P0, rate)
        for a, b in zip(hist, hist[1:]):
            assert b >= a, (P0, rate, hist)


def test_global_max_clamp():
    """If global_max is set, potential never exceeds it."""
    hist = cp.career_trajectory(70, 250, global_max=80)
    assert all(p <= 80 + 1e-9 for p in hist)
    # Hits the cap by some year for this aggressive case
    assert max(hist) >= 79.0


def test_interest_rate_distribution_shape():
    """Per locked design: ~75% tier 1, ~20% tier 2, ~5% tier 3."""
    rng = random.Random(0)
    tiers = {1: 0, 2: 0, 3: 0}
    for _ in range(20_000):
        r = cp.draw_interest_rate(rng)
        if r <= 100:   tiers[1] += 1
        elif r <= 200: tiers[2] += 1
        else:          tiers[3] += 1
    pct1 = tiers[1] / 20_000
    pct2 = tiers[2] / 20_000
    pct3 = tiers[3] / 20_000
    # Allow 2pp tolerance against the 75/20/5 design target.
    assert abs(pct1 - 0.75) < 0.02, (pct1, pct2, pct3)
    assert abs(pct2 - 0.20) < 0.02, (pct1, pct2, pct3)
    assert abs(pct3 - 0.05) < 0.02, (pct1, pct2, pct3)
