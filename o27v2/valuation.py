"""
Player and team valuation in guilders.

PR-1 doesn't persist salaries; it derives an "estimated value" on the
fly from `o27v2.trades.trade_value` (the existing 0..1 score driving the
deadline trade engine). The mapping is:

    estimate_player_value(p) = TIER_CAP * trade_value(p) * roster_share

`roster_share` carries the implicit assumption that a single player
shouldn't account for more than ~30% of a team's nominal cap, even at
the very top of the trade-value distribution. Combined with the cap
pressure baked into trade_value's age curve, the resulting figures land
in the user's "lakh-tier rookies → 500-crore generational stars" band
without requiring per-player overrides.
"""
from __future__ import annotations

from o27v2 import db
from o27v2.currency import CRORE
from o27v2.trades import trade_value

# League-name → cap mapping. Keys are matched case-insensitively so the
# four canonical tier names (Galactic / Premier / National / Association)
# resolve regardless of how a config spells them. Falls through to
# DEFAULT_TIER_CAP for the neutral names live configs use today (AL, NL,
# L1, MLB, etc.).
TIER_CAPS: dict[str, int] = {
    "galactic":    900 * CRORE,
    "premier":     650 * CRORE,
    "national":    400 * CRORE,
    "association": 225 * CRORE,
}
DEFAULT_TIER_CAP: int = 400 * CRORE  # mid-tier fallback

# A single star tops out around 30% of the team cap. trade_value() alone
# rarely exceeds ~0.85, so the effective cap on a player is roughly
# 0.30 * 0.85 * cap ≈ 25% — leaves room for the roster around them.
ROSTER_STAR_SHARE: float = 0.30

# Floor in guilders (≈ ƒ20 lakh). Replacement-tier rookies shouldn't
# bottom out at zero even if trade_value rounds down.
PLAYER_VALUE_FLOOR: int = 20 * 1_00_000


def cap_for_league(league_name: str | None) -> int:
    """Look up the tier cap for a league name. Defaults to ƒ400 cr."""
    if not league_name:
        return DEFAULT_TIER_CAP
    return TIER_CAPS.get(league_name.strip().lower(), DEFAULT_TIER_CAP)


def estimate_player_value(player: dict, *, league_name: str | None = None) -> int:
    """Map a player record (sqlite3.Row or dict-like) to a guilder amount."""
    cap = cap_for_league(league_name)
    score = trade_value(dict(player))                  # 0..1
    raw   = cap * ROSTER_STAR_SHARE * score
    return max(PLAYER_VALUE_FLOOR, int(round(raw)))


def estimate_team_payroll(team_id: int) -> int:
    """Sum of estimate_player_value across the team's full roster."""
    team = db.fetchone(
        "SELECT id, league FROM teams WHERE id = ?", (team_id,),
    )
    if not team:
        return 0
    league_name = team["league"] if "league" in team.keys() else None
    roster = db.fetchall(
        "SELECT * FROM players WHERE team_id = ?", (team_id,),
    )
    return sum(
        estimate_player_value(dict(p), league_name=league_name) for p in roster
    )
