"""
Power Play — optional league rule.

When a league enables the rule (cfg.POWER_PLAY_ENABLED, surfaced as a per-league
dashboard toggle via o27v2.engine_config), the FIELDING manager may deploy a
10th defender: the *nickel fielder* (NF, scorekeeping position 10), a middle
outfielder. The deployment is a use-or-lose window of up to
cfg.POWER_PLAY_WINDOW_OUTS outs.

Window rules (see config.py for the full note):
  * at most one window per defensive half (use it or lose it);
  * up to 4 outs, but always ends when the half ends (no carryover);
  * a fresh window is available in a Declared Seconds frame;
  * never available in extra (super) innings.

Nickel eligibility is *derived* from existing attributes — no new player type.
A nickel is any rostered player NOT currently on the field, eligible at OF or
SS, who clears both a strong-arm and a good-glove bar. Pitchers qualify only as
a wild card (lightly-used arms) and only if they have not appeared in the game.

All of this module's effects are gated on the rule being on AND a window being
active, so with the rule off (the default) the engine is byte-for-byte
unchanged.
"""
from __future__ import annotations

import random
from typing import Optional

from o27 import config as cfg
from o27.engine.state import GameState, Player, Team

# Positions that make a player nickel-eligible (outfield or shortstop).
_OF_POSITIONS = {"CF", "LF", "RF", "OF"}
_NICKEL_POSITIONS = _OF_POSITIONS | {"SS"}

# Scorekeeping tag / position number for the nickel fielder.
NICKEL_POS = "NF"
NICKEL_POS_NUMBER = 10


# ---------------------------------------------------------------------------
# Rule gate
# ---------------------------------------------------------------------------

def power_play_on(state: GameState) -> bool:
    """True if the optional rule is active for this game.

    Per-game override (state.power_play_enabled) wins when set; otherwise we
    fall back to the league/environment toggle on the config module.
    """
    override = getattr(state, "power_play_enabled", None)
    if override is not None:
        return bool(override)
    return bool(getattr(cfg, "POWER_PLAY_ENABLED", False))


# ---------------------------------------------------------------------------
# Window lifecycle
# ---------------------------------------------------------------------------

def clear_window(state: GameState) -> None:
    """Drop any active window. Called at every half start so a window never
    carries over into Declared Seconds or a super-inning."""
    state.power_play_open_out = None
    state.power_play_deploy_team_id = None
    state.power_play_nickel_id = None
    state.power_play_checked_this_ab = False
    state.power_play_presence = 0.0


def short_handed_for_batting(state: GameState) -> bool:
    """True when the team currently batting is facing an active nickel window
    (the defense deployed its 10th man). From the offense's point of view this
    is the "short-handed" condition — a man down against a loaded defense.

    Evaluated at PA start (once per AB) and snapshotted onto
    state.power_play_sh_active so the per-batter short-handed counters are
    charged for the whole PA even if its final out closes the window.
    """
    if not is_window_active(state):
        return False
    fielding = state.fielding_team
    return fielding is not None and fielding.team_id == getattr(
        state, "power_play_deploy_team_id", None)


def is_window_active(state: GameState) -> bool:
    """True while the nickel is on the field."""
    open_out = getattr(state, "power_play_open_out", None)
    if open_out is None:
        return False
    if getattr(state, "is_super_inning", False):
        return False
    window = int(getattr(cfg, "POWER_PLAY_WINDOW_OUTS", 4))
    return (state.outs - open_out) < window


def note_out(state: GameState) -> None:
    """Called after each recorded out. Extends the active deployment's covered
    out range and closes the window once it has spanned its full length."""
    open_out = getattr(state, "power_play_open_out", None)
    if open_out is None:
        return
    # Extend the box-score record to the out just recorded.
    deployments = getattr(state, "power_play_deployments", None)
    if deployments:
        rec = deployments[-1]
        if rec.get("_open"):
            rec["end_out"] = state.outs
    if not is_window_active(state):
        # Window has spanned its full length — retire the nickel.
        if deployments and deployments[-1].get("_open"):
            deployments[-1].pop("_open", None)
        state.power_play_open_out = None
        state.power_play_deploy_team_id = None
        state.power_play_nickel_id = None


# ---------------------------------------------------------------------------
# Nickel eligibility
# ---------------------------------------------------------------------------

def _positions_for(p: Player) -> set:
    """All positions a player can defend: primary + role_field_pos list."""
    out = set()
    pos = (getattr(p, "position", "") or "").strip()
    if pos:
        out.add(pos)
    raw = getattr(p, "role_field_pos", "") or ""
    for tok in raw.split(","):
        tok = tok.strip()
        if tok:
            out.add(tok)
    return out


def _nickel_field_grade(p: Player, positions: set) -> float:
    """Best applicable glove grade for the positions that make this player
    nickel-eligible (outfield glove for OF, infield glove for SS)."""
    grades = []
    if positions & _OF_POSITIONS:
        grades.append(float(getattr(p, "defense_outfield", 0.5) or 0.5))
    if "SS" in positions:
        grades.append(float(getattr(p, "defense_infield", 0.5) or 0.5))
    # General glove acts as a floor so a strong all-rounder isn't penalized.
    grades.append(float(getattr(p, "defense", 0.5) or 0.5))
    return max(grades) if grades else 0.5


def _presence_for(nickel: Optional[Player]) -> float:
    """Presence-lift fraction for this nickel, banded in
    [POWER_PLAY_PRESENCE_MIN, POWER_PLAY_PRESENCE_MAX] and scaled by his glove.

    The eligibility floor (POWER_PLAY_NICKEL_FIELD_MIN) maps to the bottom of
    the band and a perfect glove (1.0) to the top, so any eligible nickel lands
    inside the band — a replacement-grade glove barely moves it, an elite one
    lands near 4.4%.
    """
    lo = float(getattr(cfg, "POWER_PLAY_PRESENCE_MIN", 0.001))
    hi = float(getattr(cfg, "POWER_PLAY_PRESENCE_MAX", 0.044))
    if nickel is None:
        return lo
    grade = _nickel_field_grade(nickel, _positions_for(nickel))
    floor = float(getattr(cfg, "POWER_PLAY_NICKEL_FIELD_MIN", 0.58))
    span = max(1e-6, 1.0 - floor)
    frac01 = max(0.0, min(1.0, (grade - floor) / span))
    return lo + frac01 * (hi - lo)


def _has_appeared(state: GameState, p: Player) -> bool:
    """True if a pitcher has already taken the mound this game (so he can't
    then be pulled in as the nickel — you never field a guy who's pitched)."""
    if p.player_id == getattr(state, "current_pitcher_id", None):
        return True
    for spell in getattr(state, "spell_log", []) or []:
        if getattr(spell, "pitcher_id", None) == p.player_id:
            return True
    return False


def _is_eligible(state: GameState, team: Team, p: Player) -> bool:
    if p.player_id in getattr(team, "substituted_out", set()):
        return False
    positions = _positions_for(p)
    if not (positions & _NICKEL_POSITIONS):
        return False
    arm = float(getattr(p, "arm", 0.5) or 0.5)
    if arm < cfg.POWER_PLAY_NICKEL_ARM_MIN:
        return False
    if _nickel_field_grade(p, positions) < cfg.POWER_PLAY_NICKEL_FIELD_MIN:
        return False
    if getattr(p, "is_pitcher", False):
        # Wild-card two-way arm: only if he hasn't already pitched today.
        if _has_appeared(state, p):
            return False
    return True


def find_nickel(state: GameState, team: Team) -> Optional[str]:
    """Pick the best nickel-eligible player_id off the bench, or None.

    Non-pitchers are preferred; a qualifying pitcher is only ever chosen when
    no position player clears the bar (the rare two-way wild card). Candidates
    are drawn from the bench (active roster minus the starting nine), falling
    back to the full roster so the pool is never empty for a thin bench.
    """
    on_field = {p.player_id for p in getattr(team, "lineup", [])}
    pool = list(getattr(team, "bench", []) or [])
    if not pool:
        pool = [p for p in getattr(team, "roster", []) if p.player_id not in on_field]
    # Never pull someone already standing in the field.
    pool = [p for p in pool if p.player_id not in on_field]

    best_pos: Optional[Player] = None
    best_pos_score = -1.0
    best_pitch: Optional[Player] = None
    best_pitch_score = -1.0
    for p in pool:
        if not _is_eligible(state, team, p):
            continue
        positions = _positions_for(p)
        score = float(getattr(p, "arm", 0.5) or 0.5) + _nickel_field_grade(p, positions)
        if getattr(p, "is_pitcher", False):
            if score > best_pitch_score:
                best_pitch_score, best_pitch = score, p
        else:
            if score > best_pos_score:
                best_pos_score, best_pos = score, p
    chosen = best_pos or best_pitch
    return chosen.player_id if chosen else None


# ---------------------------------------------------------------------------
# Deployment decision (per AB, fielding side)
# ---------------------------------------------------------------------------

def _ensure_game_rolls(team: Team, rng: random.Random) -> None:
    """Roll this team's per-game skip / mistime behavior once."""
    if team.power_play_skip is None:
        team.power_play_skip = rng.random() < cfg.POWER_PLAY_SKIP_GAME_PROB
    if team.power_play_mistime is None:
        team.power_play_mistime = rng.random() < cfg.POWER_PLAY_MISTIME_PROB
        team.power_play_mistime_late = rng.random() < 0.5


def _deploy_prob(state: GameState, team: Team) -> float:
    """Per-AB probability the manager opens the window right now."""
    outs = state.outs
    cap = state.out_cap()
    remaining = cap - outs
    window = int(cfg.POWER_PLAY_WINDOW_OUTS)

    # Out of hand → never spend it (no good reason).
    diff = abs(int(state.score.get("visitors", 0)) - int(state.score.get("home", 0)))
    if diff >= cfg.POWER_PLAY_BLOWOUT_MARGIN:
        return 0.0

    # Mistimed managers either jump early or cram it into the final outs.
    if team.power_play_mistime:
        if team.power_play_mistime_late:
            return cfg.POWER_PLAY_DEPLOY_BASE_FORCED if remaining <= window else 0.0
        return cfg.POWER_PLAY_DEPLOY_BASE_FORCED   # deploy at the first chance

    # Well-timed manager: ramp the urgency across the arc; force it before the
    # window would be wasted (use-or-lose).
    if remaining <= window:
        base = cfg.POWER_PLAY_DEPLOY_BASE_FORCED
    elif outs >= cfg.LATE_GAME_OUTS_THRESHOLD:
        base = cfg.POWER_PLAY_DEPLOY_BASE_LATE
    elif outs >= 12:
        base = cfg.POWER_PLAY_DEPLOY_BASE_MID
    else:
        base = cfg.POWER_PLAY_DEPLOY_BASE_EARLY

    # Lean in when the game is close (real leverage to protect).
    if diff <= 2:
        base *= cfg.POWER_PLAY_CLOSE_GAME_MULT
    return min(1.0, base)


def maybe_open_window(state: GameState, rng: random.Random) -> None:
    """Considered once per AB, before the first pitch. Opens the nickel window
    for the fielding side when the manager elects to (and an eligible nickel
    exists). No-op unless the rule is on and all gates pass."""
    if not power_play_on(state):
        return
    if getattr(state, "is_super_inning", False):
        return                              # never in extras
    if is_window_active(state):
        return                              # already deployed, still running
    team = state.fielding_team
    if team is None:
        return
    key = (state.phase_number, team.team_id)
    if key in state.power_play_used:
        return                              # use-or-lose: already spent this half

    _ensure_game_rolls(team, rng)
    if team.power_play_skip:
        return

    if rng.random() >= _deploy_prob(state, team):
        return

    nickel_id = find_nickel(state, team)
    # Mark the half spent either way: with no eligible nickel the chance is
    # forfeited rather than retried every AB.
    state.power_play_used.add(key)
    if nickel_id is None:
        return

    state.power_play_open_out = state.outs
    state.power_play_deploy_team_id = team.team_id
    state.power_play_nickel_id = nickel_id
    nickel = team.get_player(nickel_id)
    state.power_play_presence = _presence_for(nickel)
    state.power_play_deployments.append({
        "team_id": team.team_id,
        "team_name": team.name,
        "phase": state.phase_number,
        "start_out": state.outs + 1,        # first out the nickel is on the field for
        "end_out": state.outs + 1,          # extended by note_out as outs accrue
        "nickel_id": nickel_id,
        "nickel_name": nickel.name if nickel else nickel_id,
        "po": 0,                            # putouts the nickel records in this window
        "_open": True,
    })


def credit_nickel_putout(state: GameState) -> None:
    """Tally a putout to the active nickel window (for the box-score line).

    Called from resolve_contact whenever the nickel is credited with the play.
    The full PO/A/E still accrue to the player's fielding line via the
    renderer's _credit_fielder; this counter just feeds the Powerplays note.
    """
    deployments = getattr(state, "power_play_deployments", None)
    if deployments and deployments[-1].get("_open"):
        deployments[-1]["po"] = int(deployments[-1].get("po", 0)) + 1


# ---------------------------------------------------------------------------
# Fielding effect (called from resolve_contact, after the shift layer)
# ---------------------------------------------------------------------------

def apply_nickel_defense(
    rng: random.Random,
    state: GameState,
    hit_type: str,
    batter_safe: bool,
    caught_fly: bool,
) -> tuple:
    """Suppress outfield production while the nickel is on the field.

    Returns (hit_type, batter_safe, caught_fly, nickel_putout) where
    nickel_putout is True when the nickel personally records the out (so the
    caller credits the PO to him under position NF).
    """
    if not is_window_active(state):
        return hit_type, batter_safe, caught_fly, False
    fielding = state.fielding_team
    if fielding is None or fielding.team_id != state.power_play_deploy_team_id:
        return hit_type, batter_safe, caught_fly, False

    # Extra-base gaps get cut off: some doubles/triples become singles.
    if hit_type in ("double", "triple"):
        if rng.random() < cfg.POWER_PLAY_XBH_HELD_PROB:
            fielding.pp_xbh_held = int(getattr(fielding, "pp_xbh_held", 0) or 0) + 1
            return "single", True, False, False
        return hit_type, batter_safe, caught_fly, False

    # Shallow outfield singles get run down for outs.
    if hit_type == "single":
        if rng.random() < cfg.POWER_PLAY_SINGLE_OUT_PROB:
            fielding.pp_hits_converted = int(getattr(fielding, "pp_hits_converted", 0) or 0) + 1
            return "fly_out", False, True, True
        return hit_type, batter_safe, caught_fly, False

    return hit_type, batter_safe, caught_fly, False


# ---------------------------------------------------------------------------
# Presence effect (per-PA, stash-and-restore — mirrors leadership flares)
# ---------------------------------------------------------------------------

# Fielding-team scalar the presence lift tightens (read by error chance,
# ground-out conversion and borderline plays — i.e. "across the lineup").
_PRESENCE_DEFENSE_ATTRS = ("defense_rating",)
# Active pitcher's effectiveness attrs ("all pitching effectiveness").
_PRESENCE_PITCHER_ATTRS = ("command", "pitcher_skill", "movement", "grit")


def _mult_attr(originals: list, obj, attr: str, frac: float) -> None:
    cur = getattr(obj, attr, None)
    if cur is None:
        return
    try:
        val = float(cur)
    except (TypeError, ValueError):
        return
    originals.append((obj, attr, cur))
    setattr(obj, attr, max(0.0, min(1.0, val * (1.0 + frac))))


def apply_presence_lift(state: GameState, pitcher: Optional[Player]) -> None:
    """Called at PA start (alongside the leadership flare). While the window is
    open, multiply the fielding team's defense_rating and the active pitcher's
    effectiveness attrs by (1 + presence), so every downstream roll for this PA
    sees a tighter defense and a sharper pitcher. Restored at PA end by
    release_presence_lift. No-op when the rule is off, the window is closed, the
    deploying team isn't the one fielding, or the lift is already active."""
    if getattr(state, "pp_presence_active", False):
        return
    if not is_window_active(state):
        return
    fielding = state.fielding_team
    if fielding is None or fielding.team_id != getattr(state, "power_play_deploy_team_id", None):
        return
    frac = float(getattr(state, "power_play_presence", 0.0) or 0.0)
    if frac <= 0.0:
        return
    originals: list = []
    for a in _PRESENCE_DEFENSE_ATTRS:
        _mult_attr(originals, fielding, a, frac)
    if pitcher is not None:
        for a in _PRESENCE_PITCHER_ATTRS:
            _mult_attr(originals, pitcher, a, frac)
    if not originals:
        return
    state.pp_presence_originals = originals
    state.pp_presence_active = True


def release_presence_lift(state: GameState) -> None:
    """Called at PA end (before the flare release, so lifts unwind LIFO).
    Restores every attr the presence lift touched and clears the active flag."""
    originals = getattr(state, "pp_presence_originals", None)
    if not getattr(state, "pp_presence_active", False) and not originals:
        return
    for obj, attr, val in reversed(originals or []):
        try:
            setattr(obj, attr, val)
        except (TypeError, ValueError, AttributeError):
            pass
    state.pp_presence_originals = []
    state.pp_presence_active = False


def nickel_putout_for(state: GameState, hit_type: str, rng: random.Random,
                      forced: bool) -> Optional[str]:
    """If the active nickel should be credited with this putout, return his
    player_id (logged under position NF); else None.

    `forced` is True for the single→fly_out conversion the nickel made himself.
    Otherwise, while the window is active, the nickel patrols a slice of the
    outfield and picks up a share of routine fly/line outs.
    """
    if not is_window_active(state):
        return None
    fielding = state.fielding_team
    if fielding is None or fielding.team_id != state.power_play_deploy_team_id:
        return None
    nickel_id = getattr(state, "power_play_nickel_id", None)
    if nickel_id is None:
        return None
    if forced:
        return nickel_id
    if hit_type in ("fly_out", "line_out") and rng.random() < cfg.POWER_PLAY_NICKEL_PO_SHARE:
        return nickel_id
    return None


# ---------------------------------------------------------------------------
# Box-score rendering
# ---------------------------------------------------------------------------

def _po_suffix(po: int) -> str:
    return f", {po} PO" if po else ""


def format_powerplays_line(state: GameState) -> Optional[str]:
    """The `Powerplays:` box-score line, or None when the rule is off.

    The nickel never bats, so he gets no batting row — instead his deployment
    and defensive line ride here, naming the player (NF) and his putouts:

      Single window:        "New York — Reyes NF (O14-17, 2 PO)"
      Two windows, one guy:  "Boston — Reyes NF (1: O11, 2: O25, 3 PO)"
      Two windows, two guys: "Boston — Reyes NF (1: O11), Ortiz NF (2: O25, 1 PO)"
      Neither team used it:  "Powerplays: None"
    """
    if not power_play_on(state):
        return None
    by_team: dict = {}
    order: list = []
    for rec in getattr(state, "power_play_deployments", []) or []:
        tid = rec["team_id"]
        if tid not in by_team:
            by_team[tid] = []
            order.append(tid)
        by_team[tid].append(rec)
    if not order:
        return "Powerplays: None"

    parts = []
    for tid in order:
        recs = sorted(by_team[tid], key=lambda r: r["start_out"])
        team_name = recs[0]["team_name"]
        names = {r.get("nickel_name") for r in recs}
        if len(names) == 1 and len(recs) > 1:
            # Same nickel held the role across both windows — one name, the
            # window list, and his combined putouts.
            windows = ", ".join(f"{i}: O{r['start_out']}"
                                for i, r in enumerate(recs, start=1))
            total_po = sum(int(r.get("po", 0)) for r in recs)
            nickel = recs[0].get("nickel_name") or "?"
            parts.append(f"{team_name} — {nickel} NF ({windows}{_po_suffix(total_po)})")
        elif len(recs) == 1:
            r = recs[0]
            nickel = r.get("nickel_name") or "?"
            po = int(r.get("po", 0))
            parts.append(
                f"{team_name} — {nickel} NF (O{r['start_out']}-{r['end_out']}{_po_suffix(po)})"
            )
        else:
            # Different nickels across windows — name each with its window.
            subs = []
            for i, r in enumerate(recs, start=1):
                nickel = r.get("nickel_name") or "?"
                po = int(r.get("po", 0))
                subs.append(f"{nickel} NF ({i}: O{r['start_out']}{_po_suffix(po)})")
            parts.append(f"{team_name} — " + ", ".join(subs))
    return "Powerplays: " + ", ".join(parts)
