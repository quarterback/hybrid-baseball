"""Shared position-aware defense math.

This is the single source of truth for "how good is this player's glove at
this position" and "how much does this position count toward team defense."
It lives in the engine so BOTH the pre-game team-defense computation (o27v2)
and the in-game defensive-substitution delta (o27.engine.manager) use the
exact same formula — a defensive sub then moves `team.defense_rating` by the
real marginal value of swapping one glove for another at one position.

A substitute is just a player: talent and fit dictate the contribution, which
can be positive OR negative. A rangey, strong-armed outfielder helps; an
error-prone first baseman shoved into center with no arm hurts. The rating
blends:
  - 60% the position-group sub-rating (catcher / infield / outfield), 40%
    general defense — so a specialist is rewarded in-group and penalized
    out-of-group;
  - a foot-speed (range) term, weighted heavily up the middle (CF) and nil at
    the corners of the diamond (1B / C);
  - a throwing-arm term, weighted where throws matter (C, corner OF, left side
    of the infield) and nil at 1B.

Both the speed and arm terms are deviations from neutral (0.5), so a league of
average-speed / average-arm players is identical to the old speed-only model
and the all-defaults identity (everything 0.5 → rating 0.5) is preserved.
"""
from __future__ import annotations

# Positional value — how much each spot counts toward the team-defense mean.
POSITIONAL_VALUE: dict[str, float] = {
    "C":  +1.5,
    "SS": +1.0,
    "CF": +0.5,
    "2B": +0.5,
    "3B": +0.3,
    "LF": -0.3,
    "RF": -0.3,
    "1B": -0.7,
    "DH": -1.5,
    "UT":  0.0,
    "P":  -2.0,   # pitchers rarely field; their "defense" mostly reflects PFP
}

INFIELD_POSITIONS  = frozenset(("1B", "2B", "3B", "SS"))
OUTFIELD_POSITIONS = frozenset(("LF", "CF", "RF"))

# Canonical 8 fielding positions (excluding pitcher). Every starting fielder
# must land on exactly one of these.
CANONICAL_FIELDING_8 = ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF")

# Foot-speed (range) weight per position. Speed = 0.5 is neutral.
SPEED_RANGE_POSITIONS: dict[str, float] = {
    "CF":  0.30, "LF":  0.22, "RF":  0.22,
    "SS":  0.18, "2B":  0.18,
    "3B":  0.08, "1B":  0.04,
    "C":   0.00,
}

# Throwing-arm weight per position. Arm = 0.5 is neutral. Heaviest where the
# throw is the play: catcher (caught stealing), corner outfield (assists /
# holding runners), and the left side of the infield (the long throw).
ARM_POSITIONS: dict[str, float] = {
    "C":   0.20,
    "RF":  0.18, "LF":  0.12, "CF":  0.10,
    "SS":  0.12, "3B":  0.14, "2B":  0.06,
    "1B":  0.02,
}


def positional_weight(pos: str) -> float:
    """Team-defense weight for a position. Even negative-value spots still
    contribute (floor 0.5), but premium positions count for more."""
    return max(0.5, 1.5 + POSITIONAL_VALUE.get(pos, 0.0))


def position_defense_rating(player, pos: str) -> float:
    """The player's effective 0..1 defense if he plays `pos`.

    Identity preserved at all defaults (defense/speed/arm = 0.5 → 0.5).
    """
    general = float(getattr(player, "defense", 0.5) or 0.5)
    if pos == "C":
        sub = float(getattr(player, "defense_catcher", 0.5) or 0.5)
    elif pos in INFIELD_POSITIONS:
        sub = float(getattr(player, "defense_infield", 0.5) or 0.5)
    elif pos in OUTFIELD_POSITIONS:
        sub = float(getattr(player, "defense_outfield", 0.5) or 0.5)
    else:
        sub = general
    base = 0.6 * sub + 0.4 * general
    speed = float(getattr(player, "speed", 0.5) or 0.5)
    arm   = float(getattr(player, "arm", 0.5) or 0.5)
    base += SPEED_RANGE_POSITIONS.get(pos, 0.0) * (speed - 0.5)
    base += ARM_POSITIONS.get(pos, 0.0) * (arm - 0.5)
    return base


def apply_sub_to_team_defense(team, player_out, player_in, pos: str) -> float | None:
    """Move `team.defense_rating` by the marginal value of replacing
    `player_out` with `player_in` at `pos` on defense.

    The rating is a positional-value-weighted mean; swapping one glove shifts
    the weighted sum by `weight(pos) * (rating_in - rating_out)` and the rating
    is re-derived as weighted_sum / weight_sum. The change can be positive OR
    negative — a worse glove (or a bad positional fit) genuinely hurts.

    No-op when the team has no stashed components (weight_sum <= 0), e.g. the
    lightweight college/world-cup/youth sims that hard-set defense_rating.
    Returns the new defense_rating, or None when skipped.
    """
    weight_sum = float(getattr(team, "defense_weight_sum", 0.0) or 0.0)
    if weight_sum <= 0:
        return None
    w = positional_weight(pos)
    delta = w * (position_defense_rating(player_in, pos)
                 - position_defense_rating(player_out, pos))
    team.defense_weighted_sum = float(getattr(team, "defense_weighted_sum", 0.0)) + delta
    team.defense_rating = max(0.0, min(1.0, team.defense_weighted_sum / weight_sum))
    return team.defense_rating
