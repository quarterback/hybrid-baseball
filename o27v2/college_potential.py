"""
College player potential growth — annual "interest rate" development.

Each college player carries a hidden `base_potential` (20-80) and a personal
`interest_rate_percent` (1-320). At the end of each college season, their
potential grows by:

    gain = min(raw_gain, cap_effective)
    raw_gain      = (interest_rate_percent / 100) * P_current
    cap_base      = C_MAX * (1 - (P_current - 20) / 60)   clamped to [C_MIN, C_MAX]
    cap_effective = cap_base * tier_multiplier(interest_rate_percent)

The cap shape is the key piece: low-potential players have large yearly caps
(room to grow), high-potential players have small caps (already near ceiling).
Combined with the tier multiplier, this lets a 30-base / 214%-interest kid
trace something like 30 → 50 → 62 → 70 → 76 over a 4-year college career,
while a 60-base / 80% kid traces a more measured 60 → 65 → 69 → 73 → 77.

NOTE: this module is the pure growth math. Access (the visible-grade lens),
scouting fog, and the full college-tier league plumbing live elsewhere —
this function just answers "given current potential and interest rate, what
does it look like next year?".
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Spec constants
# ---------------------------------------------------------------------------

P_MIN: int = 20
P_MAX: int = 80
C_MAX: float = 15.0   # cap ceiling for lowest-base players
C_MIN: float = 4.0    # cap floor (every player keeps at least this much room)

# Each tier: (low%, high%, cap_multiplier). Inclusive bounds; tiers must
# cover the supported interest-rate range without gaps.
INTEREST_TIERS: tuple[tuple[int, int, float], ...] = (
    (0,   100, 1.0),    # ordinary developers
    (101, 200, 1.3),    # solid late bloomers
    (201, 320, 1.6),    # rare super-bloomers (a few per recruiting class)
)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _tier_multiplier(interest_pct: float) -> float:
    """Cap-multiplier tier for `interest_pct` (a number, percent)."""
    for low, high, mult in INTEREST_TIERS:
        if low <= interest_pct <= high:
            return mult
    # Above the top tier — clamp to the highest configured multiplier.
    return INTEREST_TIERS[-1][2]


def _cap_base(P: float) -> int:
    """Base yearly cap as a function of current potential P. Returns an
    integer — the spec's precomputed cap table is round-half-up of the
    linear-decay formula, and the worked examples rely on this rounding.
    """
    raw = C_MAX * (1.0 - (P - P_MIN) / (P_MAX - P_MIN))
    if raw < C_MIN: raw = C_MIN
    if raw > C_MAX: raw = C_MAX
    return int(raw + 0.5)   # round half up; e.g. 4.5 → 5, 7.25 → 7


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def grow_one_year(P: float, interest_pct: float,
                  *, global_max: float | None = None) -> float:
    """Apply one year of college development to potential `P`.

    `interest_pct` is the player's interest rate as a percent
    (e.g. 214 for 214%). `global_max` optionally clamps the result.

    Per spec: `cap_base` is round-half-up; `cap_effective` is integer-
    truncated after the tier multiplier (20.8 → 20). Raw interest gain
    is the only float; final P stays effectively integer when the cap
    is binding (the common case for any reasonable interest rate).
    """
    r = interest_pct / 100.0
    raw_gain = r * P
    cap_eff  = int(_cap_base(P) * _tier_multiplier(interest_pct))
    gain     = raw_gain if raw_gain < cap_eff else cap_eff
    P_new    = P + gain
    if global_max is not None and P_new > global_max:
        P_new = global_max
    return P_new


def career_trajectory(base_potential: float, interest_pct: float,
                      *, years: int = 4,
                      global_max: float | None = None) -> list[float]:
    """Return `[P0, P1, …, Pn]` — potential at each year-end for a player
    starting at `base_potential` with `interest_pct` interest, growing
    `years` college seasons (default 4 = freshman → senior)."""
    history = [float(base_potential)]
    P = float(base_potential)
    for _ in range(years):
        P = grow_one_year(P, interest_pct, global_max=global_max)
        history.append(P)
    return history


# ---------------------------------------------------------------------------
# Interest-rate distribution across a recruiting class
# ---------------------------------------------------------------------------
#
# Per the locked design: tier buckets, weighted so the class has a few
# memorable late bloomers and the rare super-bloomer headliner.
#
#   75% of players: Uniform(0, 100)   — tier 1, ordinary developers
#   20% of players: Uniform(101, 200) — tier 2, late bloomers
#    5% of players: Uniform(201, 320) — tier 3, super-bloomers
#
# Players draw their rate ONCE at college generation (static per player),
# so each kid has their own personality — fast / slow / steady. No per-year
# resampling.

_TIER_DISTRIBUTION: tuple[tuple[float, tuple[int, int]], ...] = (
    (0.75, (0,   100)),
    (0.20, (101, 200)),
    (0.05, (201, 320)),
)


def draw_interest_rate(rng) -> int:
    """Roll one player's interest_rate_percent for the spec distribution."""
    roll = rng.random()
    acc = 0.0
    for share, (lo, hi) in _TIER_DISTRIBUTION:
        acc += share
        if roll < acc:
            return rng.randint(lo, hi)
    return rng.randint(*_TIER_DISTRIBUTION[-1][1])
