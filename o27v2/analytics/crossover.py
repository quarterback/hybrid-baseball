"""
Crossover (XO) stats: O27 rate stats translated to MLB-readable values
via mean-and-spread z-anchoring.

A player's standardized position within the O27 league distribution is
mapped onto MLB's mean AND standard deviation for the same stat:

    xo = MLB_mean + ((value - O27_mean) / O27_sd) * MLB_sd

This preserves rank order exactly (the map is affine/monotonic in value,
so XO leaderboards are the same player order as native) AND preserves
spread (a true O27 ace, N sd above O27 average, becomes a pitcher N sd
above MLB average — i.e. a recognizable MLB ace number, not a value
bunched at the league anchor).

XO is a READING layer, not a model. It does not change any underlying
calculation. It requires O27 per-stat league sd, which the web layer's
league-baselines aggregator must compute alongside league mean.

LOCKED DECISION: z-anchoring, NOT linear ratio. The linear-ratio method
collapses spread and is rejected. Do not substitute it.
"""

from __future__ import annotations

# Recent-MLB composite league means. One place to retune the entire
# XO panel. Source: 2018-2023 MLB qualified-player averages
# (approximate; tune via the blocking calibration panel on analytics.html).
MLB_ANCHOR_MEAN = {
    # Pitching
    "era":   4.30,
    "whip":  1.30,
    "k9":    8.5,
    "bb9":   3.2,
    "hr9":   1.2,
    "oavg":  0.250,
    "oobp":  0.320,
    "oslg":  0.415,
    "oops":  0.735,
    # Batting
    "avg":   0.250,
    "obp":   0.320,
    "slg":   0.415,
    "ops":   0.735,
    "woba":  0.320,
    "babip": 0.295,
}

# Recent-MLB composite per-stat league standard deviations (population
# sd across qualified players, same source season(s) as the means).
# These are REQUIRED. Do not approximate them as a fixed fraction of the
# mean — source them the same way the means were sourced.
MLB_ANCHOR_SD = {
    # Pitching
    "era":   1.05,
    "whip":  0.14,
    "k9":    2.10,
    "bb9":   0.95,
    "hr9":   0.45,
    "oavg":  0.022,
    "oobp":  0.026,
    "oslg":  0.050,
    "oops":  0.072,
    # Batting
    "avg":   0.024,
    "obp":   0.030,
    "slg":   0.055,
    "ops":   0.080,
    "woba":  0.034,
    "babip": 0.024,
}

# Stats that the XO map covers. Order is for stable iteration in
# templates / calibration panels.
XO_PITCHER_STATS = ("era", "whip", "k9", "bb9", "hr9",
                    "oavg", "oobp", "oslg", "oops")
XO_BATTER_STATS  = ("avg", "obp", "slg", "ops", "woba", "babip")


def to_xo(stat_name: str,
          value: float,
          o27_league_mean: float,
          o27_league_sd: float) -> float:
    """Mean-and-spread z-anchor map.

    Returns the native value unchanged if the stat is not anchored or if
    the O27 league sd is non-positive (degenerate distribution — cannot
    standardize; fall through rather than divide by zero).

    For stats where lower is better (ERA, WHIP, BB/9, HR/9, opponent
    slash), the z-anchor map still works correctly — a pitcher below O27
    mean ERA gets a negative z and lands below MLB mean ERA, which is
    the correct (good) direction. No sign flip needed.
    """
    m_mean = MLB_ANCHOR_MEAN.get(stat_name)
    m_sd   = MLB_ANCHOR_SD.get(stat_name)
    if m_mean is None or m_sd is None:
        return value
    if not o27_league_sd or o27_league_sd <= 0:
        return value
    z = (float(value) - float(o27_league_mean)) / float(o27_league_sd)
    return m_mean + z * m_sd
