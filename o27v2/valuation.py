"""
Player and team valuation in guilders.

The mapping pipeline:
  1. `trade_value(player)` produces a 0..1 score from skill / age /
     role / archetype (existing function, unchanged).
  2. `_score_to_base_value(score)` maps that score onto the user's
     canonical tier bands at the default tier (ƒ400 cr cap):
       0.00-0.30 → ƒ20-50 lakh   (replacement)
       0.30-0.45 → ƒ1-5 cr       (solid starter)
       0.45-0.55 → ƒ5-15 cr      (above-average regular)
       0.55-0.65 → ƒ15-50 cr     (All-Star)
       0.65-0.78 → ƒ50-200 cr    (star)
       0.78-0.90 → ƒ200-500 cr   (generational)
       0.90-1.00 → ƒ500-900 cr   (auction-record territory)
  3. The base value is scaled by `cap / DEFAULT_TIER_CAP`, so a
     Galactic-League rookie sits higher than an Association rookie at
     the same trade_value, while preserving the headline band shape.

When a player record carries a non-zero `salary` field (persisted at
seed time via league.py), the precomputed value short-circuits the
recomputation — that's the canonical wage ledger.
"""
from __future__ import annotations

from o27v2 import db
from o27v2.currency import CRORE, LAKH
from o27v2.trades import trade_value

# League-name → cap mapping. Keys are matched case-insensitively so
# canonical tier names (Galactic / Premier / National / Association)
# resolve regardless of how a config spells them. Falls through to
# DEFAULT_TIER_CAP for the neutral names live configs use today (AL,
# NL, L1, MLB, etc.).
TIER_CAPS: dict[str, int] = {
    "galactic":    900 * CRORE,
    "premier":     650 * CRORE,
    "national":    400 * CRORE,
    "association": 225 * CRORE,
}
DEFAULT_TIER_CAP: int = 400 * CRORE

# Canonical tier breakpoints in (score, base_value_at_default_cap) form.
# Endpoints chosen so consecutive bands meet — a 0.30 score lands
# exactly at ƒ50 lakh; a 0.78 score lands exactly at ƒ200 cr.
_BANDS: list[tuple[float, int]] = [
    (0.00,  20 * LAKH),     # replacement floor
    (0.30,  50 * LAKH),     # replacement ceiling
    (0.45,   5 * CRORE),    # solid-starter ceiling
    (0.55,  15 * CRORE),    # above-average ceiling
    (0.65,  50 * CRORE),    # All-Star ceiling
    (0.78, 200 * CRORE),    # star ceiling
    (0.90, 500 * CRORE),    # generational ceiling
    (1.00, 900 * CRORE),    # auction-record ceiling
]


def cap_for_league(league_name: str | None) -> int:
    if not league_name:
        return DEFAULT_TIER_CAP
    return TIER_CAPS.get(league_name.strip().lower(), DEFAULT_TIER_CAP)


def _score_to_base_value(score: float) -> int:
    """Map a 0..1 trade-value score onto the canonical tier bands at
    the default tier cap. Linearly interpolates within each band."""
    s = max(0.0, min(1.0, float(score)))
    for i in range(len(_BANDS) - 1):
        lo_score, lo_value = _BANDS[i]
        hi_score, hi_value = _BANDS[i + 1]
        if s <= hi_score:
            if hi_score == lo_score:
                return lo_value
            t = (s - lo_score) / (hi_score - lo_score)
            return int(round(lo_value + t * (hi_value - lo_value)))
    return _BANDS[-1][1]


def estimate_player_value(player: dict, *, league_name: str | None = None) -> int:
    """Return a guilder amount. Prefers a persisted `salary` field when
    non-zero; otherwise derives from trade_value via the band map."""
    persisted = player.get("salary")
    try:
        persisted_int = int(persisted or 0)
    except (TypeError, ValueError):
        persisted_int = 0
    if persisted_int > 0:
        return persisted_int

    score = trade_value(dict(player))
    base = _score_to_base_value(score)
    cap = cap_for_league(league_name)
    return int(round(base * cap / DEFAULT_TIER_CAP))


def estimate_team_payroll(team_id: int) -> int:
    """Sum of player values across the team's full roster. Reads
    persisted salaries directly when available; falls back to
    estimate_player_value per row otherwise."""
    team = db.fetchone(
        "SELECT id, league FROM teams WHERE id = ?", (team_id,),
    )
    if not team:
        return 0

    summed = db.fetchone(
        "SELECT COALESCE(SUM(salary), 0) AS total, "
        "       COUNT(*)                 AS n,     "
        "       COUNT(NULLIF(salary, 0)) AS paid   "
        "FROM players WHERE team_id = ?",
        (team_id,),
    )
    if summed and summed["n"] and summed["paid"] == summed["n"]:
        return int(summed["total"])

    league_name = team["league"] if "league" in team.keys() else None
    roster = db.fetchall(
        "SELECT * FROM players WHERE team_id = ?", (team_id,),
    )
    return sum(
        estimate_player_value(dict(p), league_name=league_name) for p in roster
    )
