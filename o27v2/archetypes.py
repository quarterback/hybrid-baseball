"""
Position-player archetypes.

Pitchers carry repertoire-based archetypes (see o27/data.py); this module
covers the *other* side of the field — a small catalogue of qualitative
identities that hitters fall into based on their 20-95 scout grades plus
position and handedness.

The classifier is deterministic and total: every non-pitcher / non-joker
player gets exactly one label. Pitchers and jokers return "" (they own
their own archetype dimension elsewhere).

Used in three places:
  - generation: o27v2/league.py:_make_hitter applies the label post-roll
  - off-season: o27v2/development.py:_develop_player re-derives after
    grades drift
  - manager AI: o27/engine/manager.py reads Player.archetype to bias
    pinch-hit and defensive-sub selection

Inputs are DB-shape integer grades (20-95). Both call sites work in that
scale; the engine `Player.archetype` field is populated by the DB→engine
loader at o27v2/sim.py and read by the manager.
"""
from __future__ import annotations

FIVE_TOOL_STAR        = "Five-Tool Star"
DEFENSIVE_CATCHER     = "Defensive Catcher"
SLUGGING_CATCHER      = "Slugging Catcher"
SLUGGER               = "Slugger"
POWER_HITTING_CORNER  = "Power-Hitting Corner"
CONTACT_HITTER        = "Contact Hitter"
SPEEDSTER             = "Speedster"
UTILITY_INFIELDER     = "Utility Infielder"
DEFENSIVE_SPECIALIST  = "Defensive Specialist"
REGULAR               = "Regular"

ALL_ARCHETYPES = (
    FIVE_TOOL_STAR,
    DEFENSIVE_CATCHER,
    SLUGGING_CATCHER,
    SLUGGER,
    POWER_HITTING_CORNER,
    CONTACT_HITTER,
    SPEEDSTER,
    UTILITY_INFIELDER,
    DEFENSIVE_SPECIALIST,
    REGULAR,
)


def _g(p: dict, k: str, default: int = 50) -> int:
    """Read a grade from a DB row, defaulting to league-average (50)."""
    v = p.get(k)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def classify_position_player(p: dict) -> str:
    """Return the archetype label for a position player.

    Pitchers and jokers return "" — they carry their own archetype
    dimension and must never get a hitter label written over it.

    Rules are evaluated in priority order; first match wins. The final
    Regular fallback guarantees totality.
    """
    if p.get("is_pitcher"):
        return ""
    if p.get("is_joker"):
        return ""

    contact     = _g(p, "contact")
    power       = _g(p, "power")
    eye         = _g(p, "eye")
    speed       = _g(p, "speed")
    skill       = _g(p, "skill")
    defense     = _g(p, "defense")
    if_g        = _g(p, "defense_infield")
    of_g        = _g(p, "defense_outfield")
    cat_g       = _g(p, "defense_catcher")
    baserunning = _g(p, "baserunning")
    pos         = (p.get("position") or "").upper()

    # 1. Five-Tool Star — elite across the board.
    if (skill   >= 70 and
        power   >= 60 and
        contact >= 60 and
        speed   >= 60 and
        max(if_g, of_g) >= 60):
        return FIVE_TOOL_STAR

    # 2. Defensive Catcher — glove-first backstop. Priority over
    #    Slugging Catcher so a catcher who is BOTH good with the glove
    #    and average with the bat lands on defense.
    if pos == "C" and cat_g >= 65 and skill <= 60:
        return DEFENSIVE_CATCHER

    # 3. Slugging Catcher — bat-first backstop.
    if pos == "C" and power >= 60:
        return SLUGGING_CATCHER

    # 4. Slugger — three-true-outcome power threat, but not a catcher
    #    (catchers are covered above).
    if power >= 70 and contact <= 55 and pos != "C":
        return SLUGGER

    # 5. Power-Hitting Corner — slow-footed mash at 1B/LF/RF.
    if pos in ("1B", "LF", "RF") and power >= 60 and speed <= 50:
        return POWER_HITTING_CORNER

    # 6. Utility Infielder — multi-position bench piece. Mirrors the
    #    `is_utility` rng branch at o27v2/league.py:1146 where all three
    #    defensive groups roll full-tier (Vargas-style). Restricted to
    #    middle/corner infield primaries so 1B-only sluggers don't slip
    #    in. Priority above Contact Hitter because positional flexibility
    #    is the distinguishing identity for these players (many contact
    #    hitters are single-position; a utility infielder is defined by
    #    the glove range, not the bat).
    if (pos in ("2B", "SS", "3B") and
        if_g  >= 50 and
        of_g  >= 45 and
        cat_g >= 30 and
        abs(if_g - of_g) <= 15):
        return UTILITY_INFIELDER

    # 7. Contact Hitter — bat-to-ball, low whiff, no power.
    if contact >= 65 and power <= 55:
        return CONTACT_HITTER

    # 8. Speedster — wheels plus baserunning IQ, no power.
    if speed >= 70 and baserunning >= 55 and power <= 55:
        return SPEEDSTER

    # 9. Defensive Specialist — glove-only, weak bat.
    if (defense >= 65 and
        skill   <= 50 and
        max(if_g, of_g) >= 65):
        return DEFENSIVE_SPECIALIST

    # 10. Regular — fallback (totality guarantee).
    return REGULAR


# ---------------------------------------------------------------------------
# Role-capability tagging (substitution-economy foundation, Item 1)
# ---------------------------------------------------------------------------
#
# Derived from the 20-95 scout grades. These are the "can this player do X
# well enough to be deployed in that role" flags — separate from `archetype`
# (which is the qualitative label). The substitution layer uses them to
# filter candidate pools cheaply: a bat-first player has role_hit=True and
# all role_field_pos entries False, so the pinch-fielder picker skips them
# automatically.
#
# Re-derived during off-season development the same way `archetype` is.

# Hit-capability threshold — composite of contact + power + eye must clear
# this for the player to count as a deployable bat. Centred a hair under
# league-mean so the median player is hit-capable but replacement-level
# bats wash out.
ROLE_HIT_THRESHOLD = 45      # mean of contact/power/eye

# Position-specific defensive thresholds. Catcher is tighter (you can't
# fake C); corner OF / 1B loosest (any glove can hide there in a pinch).
_FIELD_THRESHOLDS = {
    "C":  55,
    "SS": 50,
    "2B": 48,
    "3B": 48,
    "CF": 50,
    "LF": 42,
    "RF": 42,
    "1B": 42,
}

# Run-capability — pure speed + baserunning composite.
ROLE_RUN_THRESHOLD = 55


def is_hit_capable(p: dict) -> bool:
    """True if the player's contact/power/eye composite clears the
    deployment threshold for a bat substitution."""
    if p.get("is_pitcher"):
        return False
    mean = (_g(p, "contact") + _g(p, "power") + _g(p, "eye")) / 3.0
    return mean >= ROLE_HIT_THRESHOLD


def is_field_capable_at(p: dict, pos: str) -> bool:
    """True if the player's positional defense clears the per-position
    threshold (catcher tighter than corner OF)."""
    if p.get("is_pitcher"):
        return False
    threshold = _FIELD_THRESHOLDS.get((pos or "").upper())
    if threshold is None:
        return False
    pos_u = pos.upper()
    if pos_u in ("1B", "2B", "3B", "SS"):
        grade = _g(p, "defense_infield")
    elif pos_u in ("LF", "CF", "RF"):
        grade = _g(p, "defense_outfield")
    elif pos_u == "C":
        grade = _g(p, "defense_catcher")
    else:
        return False
    return grade >= threshold


def field_capable_positions(p: dict) -> list[str]:
    """Return the positions this player can defend at."""
    if p.get("is_pitcher"):
        return []
    out = []
    for pos in ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF"):
        if is_field_capable_at(p, pos):
            out.append(pos)
    return out


def is_run_capable(p: dict) -> bool:
    """True if speed + baserunning composite clears the PR-deployment
    threshold."""
    if p.get("is_pitcher"):
        return False
    mean = (_g(p, "speed") + _g(p, "baserunning")) / 2.0
    return mean >= ROLE_RUN_THRESHOLD


def is_two_way(p: dict) -> bool:
    """True if the player is both hit-capable AND field-capable at
    at least one position — the 'bridge' player who saves a substitution
    by covering both halves."""
    return is_hit_capable(p) and bool(field_capable_positions(p))


# Roster-slot taxonomy. Stamped at generation on `players.roster_slot` so
# downstream code can query the league-wide composition in SQL.
SLOT_BAT_FIRST     = "bat_first"
SLOT_GLOVE_FIRST   = "glove_first"
SLOT_TWO_WAY       = "two_way"
SLOT_PITCHER       = "pitcher"
SLOT_JOKER         = "joker"
SLOT_PR_SPECIALIST = "pr_specialist"
SLOT_PH_SPECIALIST = "ph_specialist"

ALL_ROSTER_SLOTS = (
    SLOT_BAT_FIRST, SLOT_GLOVE_FIRST, SLOT_TWO_WAY, SLOT_PITCHER,
    SLOT_JOKER, SLOT_PR_SPECIALIST, SLOT_PH_SPECIALIST,
)


def classify_roster_slot(p: dict) -> str:
    """Return the deployment slot for this player.

    Pitchers always land on SLOT_PITCHER. Jokers (existing flag) land on
    SLOT_JOKER. Specialists are detected as one-tool extremes: PR
    specialists are role_run AND not role_hit; PH specialists are
    role_hit with extreme power/contact AND no defensive position
    clearing its threshold.

    Falls through to bat_first / glove_first / two_way for the bulk of
    the position-player pool.
    """
    if p.get("is_pitcher"):
        return SLOT_PITCHER
    if p.get("is_joker"):
        return SLOT_JOKER

    hit    = is_hit_capable(p)
    field  = bool(field_capable_positions(p))
    run    = is_run_capable(p)

    # Specialists: one-tool extremes carved out before the main split.
    if run and not hit and not field:
        return SLOT_PR_SPECIALIST
    if hit and not field and (_g(p, "power") >= 65 or _g(p, "contact") >= 65):
        # Pure bat — no glove anywhere. Read as a PH specialist when the
        # bat is loud; otherwise just bat_first.
        return SLOT_PH_SPECIALIST

    if hit and field:
        return SLOT_TWO_WAY
    if hit and not field:
        return SLOT_BAT_FIRST
    if field and not hit:
        return SLOT_GLOVE_FIRST

    # No skill profile clears any threshold — call it bat_first as the
    # safest fallback (will sit on the bench, never deploy).
    return SLOT_BAT_FIRST


def encode_field_positions(p: dict) -> str:
    """Compact comma-joined position string for the players.role_field_pos
    column (e.g., "2B,SS,3B"). Empty when nothing clears."""
    return ",".join(field_capable_positions(p))
