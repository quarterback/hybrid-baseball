"""
Crew roles — the canonical pitching-staff source of truth for O27.

O27 is not baseball-with-a-bullpen. There are no innings to reset, no
3-out save, no "every fifth day" rotation. A game is one continuous
**27-out voyage**, and a staff *conducts* that voyage through its handful
of key moments. So the pitching roles here are a **ship's crew**, not the
MLB starter/closer ladder:

    Helms   — steers the voyage out. The team's primary arm; like a
              softball ace he takes the ball daily / every other day and
              carries the early arc. Highest-usage, not "every fifth day".
    First Change  — first change of the watch; first hand to relieve the
              Helms and hold the heading.
    Second Change — second change of the watch; carries the middle arc.
    Bosun   — works the deck through the long middle; the durable bulk
              hand who soaks innings or follows a short Helms outing.
    Skidder — slides in to skid the ship through a rough patch; the
              situational / deception arm for a high-leverage matchup.
    Anchor  — drops anchor to steady the ship and hold the heading late.
    Pilot   — the harbor pilot who guides the ship into port; the
              final-outs finisher.

Two principles the operator was explicit about:

  1. **Nobody owns a title.** A role is not an identity a player carries
     around — it is *where his skills place him on THIS team's staff*. The
     same arm is a Helms on a thin staff and only a Skidder on a stacked
     one. Roles are therefore always assigned *relative to the team* and
     re-derived whenever the staff changes (seed, season rollover, trades,
     call-ups, manual edit).
  2. **Orchestration over who-is-throwing.** The point is that a crew is
     always conducting the voyage through its moments — not that a
     particular name is on the mound. The engine consumes the role as a
     *preference* for each moment of the arc and still applies live
     overrides (fatigue, rest, matchup) on top.

Legacy rows (and the pre-crew saves) carry `pitcher_role=''` /
`rotation_slot=0`; every consumer reads that as "no crew role" and falls
back to the old live-derivation behavior, so old saves keep working.
"""
from __future__ import annotations

from typing import Any

from o27v2 import scout as _scout

# ---------------------------------------------------------------------------
# Crew role codes (kept <= 3 chars so they ride in box-score tags)
# ---------------------------------------------------------------------------
HELMS   = "HM"   # primary daily arm — steers the voyage out
CHANGE1 = "1C"   # first change of the watch
CHANGE2 = "2C"   # second change of the watch
BOSUN   = "BO"   # bulk / long hand — works the deck
SKIDDER = "SK"   # situational / deception arm for a rough patch
ANCHOR  = "AN"   # late hold
PILOT   = "PI"   # final-outs finisher — guides into port

# The Helms is the only role that "steers out" (i.e. opens the game). All
# other roles are conducted from the bullpen as the voyage unfolds.
STEER_ROLES   = (HELMS,)
RELIEF_ROLES  = (CHANGE1, CHANGE2, BOSUN, SKIDDER, ANCHOR, PILOT)
ALL_ROLES     = STEER_ROLES + RELIEF_ROLES

ROLE_LABELS = {
    HELMS:   "Helms",
    CHANGE1: "First Change",
    CHANGE2: "Second Change",
    BOSUN:   "Bosun",
    SKIDDER: "Skidder",
    ANCHOR:  "Anchor",
    PILOT:   "Pilot",
}

# One-line "what this role does" blurbs for the UI.
ROLE_BLURBS = {
    HELMS:   "steers the voyage out — primary daily arm, carries the early arc",
    CHANGE1: "first change of the watch — first to relieve the Helms",
    CHANGE2: "second change of the watch — carries the middle arc",
    BOSUN:   "works the deck — durable bulk hand, soaks innings",
    SKIDDER: "skids through a rough patch — situational / deception arm",
    ANCHOR:  "drops anchor — steadies and holds the heading late",
    PILOT:   "guides into port — the final-outs finisher",
}

# Crew complement for a full ~17-arm active staff. Counts scale to the
# actual staff size in `_role_counts`. Order here is also the fill
# priority: marquee roles draft their best-fit arms first, the watch
# changes pick up the remainder.
_CREW_PLAN: tuple[tuple[str, int], ...] = (
    (HELMS,   2),   # 1-2 aces alternate so the Helms can throw ~every other day
    (PILOT,   2),
    (ANCHOR,  2),
    (SKIDDER, 2),
    (BOSUN,   3),
    (CHANGE1, 3),
    (CHANGE2, 3),
)
_CREW_TOTAL = sum(n for _, n in _CREW_PLAN)   # 17


# ---------------------------------------------------------------------------
# Attribute helpers (work on dict rows or engine Player objects)
# ---------------------------------------------------------------------------
def _attr(p: Any, key: str, default: Any) -> Any:
    if isinstance(p, dict):
        return p.get(key, default)
    return getattr(p, key, default)


def _set(p: Any, key: str, value: Any) -> None:
    if isinstance(p, dict):
        p[key] = value
    else:
        setattr(p, key, value)


def _u(p: Any, key: str) -> float:
    """Read a 20-80 grade (or [0,1] unit) attribute as a [0,1] unit."""
    return _scout.to_unit(_attr(p, key, 50) or 50)


def _stuff(p: Any) -> float:
    return _u(p, "pitcher_skill")


def _stamina(p: Any) -> float:
    s = _attr(p, "stamina", None)
    if s is None or s == 0:
        return _stuff(p)
    return _scout.to_unit(s)


def _deception(p: Any) -> float:
    # Movement + command read as "shows the hitter a different look" — the
    # Skidder's stock-in-trade. Falls back to stuff when unrated.
    mv = _u(p, "movement")
    cm = _u(p, "command")
    return (mv * 0.6 + cm * 0.4)


def _overall(p: Any) -> float:
    return _stuff(p) * 0.5 + _stamina(p) * 0.3 + _deception(p) * 0.2


# Per-role fit function: how well an arm suits a crew role. Higher = better.
_ROLE_FIT = {
    HELMS:   lambda p: _stamina(p) * 0.55 + _stuff(p) * 0.45,  # daily horse
    PILOT:   lambda p: _stuff(p) * 0.85 + _deception(p) * 0.15,  # pure finish
    ANCHOR:  lambda p: _stuff(p) * 0.75 + _deception(p) * 0.25,  # late hold
    SKIDDER: lambda p: _deception(p) * 0.65 + _stuff(p) * 0.35,  # rough patch
    BOSUN:   lambda p: _stamina(p) * 0.70 + _stuff(p) * 0.30,    # bulk
    CHANGE1: _overall,
    CHANGE2: _overall,
}


def _role_counts(n_arms: int) -> list[tuple[str, int]]:
    """Scale the crew complement to an actual staff size, preserving the
    `_CREW_PLAN` order. Always returns counts summing to exactly `n_arms`."""
    if n_arms <= 0:
        return []
    if n_arms <= len(_CREW_PLAN):
        # Thin staff: one arm per role in priority order until we run out.
        return [(role, 1) for role, _ in _CREW_PLAN[:n_arms]]

    counts = {
        role: max(1, round(n_arms * base / _CREW_TOTAL))
        for role, base in _CREW_PLAN
    }
    # Reconcile rounding drift onto the watch-change corps (the flex roles).
    drift = n_arms - sum(counts.values())
    flex = [CHANGE2, CHANGE1, BOSUN]
    i = 0
    while drift != 0 and flex:
        role = flex[i % len(flex)]
        if drift > 0:
            counts[role] += 1
            drift -= 1
        elif counts[role] > 1:
            counts[role] -= 1
            drift += 1
        i += 1
        if i > 1000:   # safety — never spin
            break
    return [(role, counts[role]) for role, _ in _CREW_PLAN]


def assign_staff_roles(pitchers: list[Any]) -> list[Any]:
    """Stamp crew roles + within-role usage order onto a staff, in place.

    The assignment is **relative to this staff**: the best-fitting arms are
    drafted into the marquee roles (Helms, Pilot, Anchor, Skidder, Bosun)
    first, and the watch-change corps picks up the rest. So a given arm's
    role depends entirely on the company he keeps — exactly the operator's
    "a Helms here, a Skidder there" intent.

    `rotation_slot` becomes the usage rank *within* a role (1 = primary;
    e.g. the two Helms alternate as slot 1 / slot 2 so the steering ace can
    go ~every other day). Relievers' slot is their depth order at the role.
    Returns the same list for chaining.
    """
    pool = [p for p in pitchers if _attr(p, "is_pitcher", True)]
    if not pool:
        return pitchers

    remaining = list(pool)
    for role, count in _role_counts(len(pool)):
        if not remaining:
            break
        fit = _ROLE_FIT[role]
        # Best-fit arms for this role, drafted off the top of what's left.
        picked = sorted(remaining, key=fit, reverse=True)[:count]
        for slot, p in enumerate(picked, start=1):
            _set(p, "pitcher_role", role)
            _set(p, "rotation_slot", slot)
            remaining.remove(p)

    # Anyone still unassigned (shouldn't happen — counts sum to len(pool))
    # rides as a Second Change so no active arm is left role-less.
    for slot, p in enumerate(remaining, start=1):
        _set(p, "pitcher_role", CHANGE2)
        _set(p, "rotation_slot", slot)

    return pitchers


def preferred_relief_roles(outs: int) -> tuple[str, ...]:
    """Crew roles to prefer for a relief call at the given out count
    (0..26 across the 27-out voyage). Consumers scope candidates to these
    roles when any are rested/available, then fall through to the emergent
    Stuff/Stamina scoring (the live override). The Helms is the steering
    arm and is not a relief option.

    Voyage moments, port-bound:
      into port (>=24) → Pilot, Anchor
      late hold (>=19) → Anchor, Pilot, Skidder
      rough patch(>=12)→ Skidder, Second Change, Bosun
      middle watch(>=6)→ Second Change, First Change, Bosun
      first change(<6) → First Change, Bosun
    """
    if outs >= 24:
        return (PILOT, ANCHOR)
    if outs >= 19:
        return (ANCHOR, PILOT, SKIDDER)
    if outs >= 12:
        return (SKIDDER, CHANGE2, BOSUN)
    if outs >= 6:
        return (CHANGE2, CHANGE1, BOSUN)
    return (CHANGE1, BOSUN)


def is_steer_role(role: str) -> bool:
    """True for roles that open the game (steer the voyage out)."""
    return role in STEER_ROLES


# ---------------------------------------------------------------------------
# DB-facing helpers
# ---------------------------------------------------------------------------
def assign_roles_for_team(team_id: int) -> int:
    """Re-derive and persist crew roles for one team's active staff from
    current attributes. Returns the number of arms updated.

    Called at league seed (per team), at season rollover, after trades, and
    on call-ups / manual edits — anywhere the staff's composition can shift,
    so roles always reflect the current crew. Only active arms are roled;
    reserve depth stays '' until promoted and the staff is re-assigned."""
    from o27v2 import db

    rows = db.fetchall(
        "SELECT id, pitcher_skill, stamina, movement, command, is_pitcher "
        "FROM players WHERE team_id = ? AND is_pitcher = 1 AND is_active = 1 "
        "ORDER BY id",
        (team_id,),
    )
    if not rows:
        return 0
    records = [dict(r) for r in rows]
    assign_staff_roles(records)
    n = 0
    for r in records:
        db.execute(
            "UPDATE players SET pitcher_role = ?, rotation_slot = ? WHERE id = ?",
            (r.get("pitcher_role", ""), int(r.get("rotation_slot", 0) or 0), r["id"]),
        )
        n += 1
    return n


def set_player_role(player_id: int, role: str, rotation_slot: int = 1) -> bool:
    """Manual override: pin one arm to a crew role from the team page.
    Validates the code; returns True on a successful write. The next
    automatic re-assignment (trade / rollover) can move him again — roles
    are never permanent titles."""
    from o27v2 import db

    role = (role or "").strip().upper()
    if role and role not in ALL_ROLES:
        return False
    db.execute(
        "UPDATE players SET pitcher_role = ?, rotation_slot = ? "
        "WHERE id = ? AND is_pitcher = 1",
        (role, int(rotation_slot or 1), player_id),
    )
    return True
