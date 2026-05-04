"""
Manager decision logic for O27.

Covers:
  - Joker insertion (§2.3 / §4.6)
  - Pinch-hit substitution
  - Pitching changes

Phase 1: constraint enforcement + heuristic stubs that always return False
         (no AI decisions; Phase 1 tests drive manager events explicitly).
Phase 2: heuristic logic for joker insertion, pitching changes, pinch hits.

All tunable thresholds are imported from o27.config.
"""

from __future__ import annotations
from .state import GameState, Player, SpellRecord
from typing import Optional
from o27 import config as cfg


# ---------------------------------------------------------------------------
# Joker insertion
# ---------------------------------------------------------------------------

def can_insert_joker(state: GameState, joker: Player) -> tuple[bool, str]:
    """Check whether a joker can be inserted right now.

    Per the corrected joker rule:
      - Joker must be in team.jokers_available (game-time pool of 3).
      - Joker must NOT have already been inserted this cycle through
        the order (tracked via Team.jokers_used_this_cycle, which
        advance_lineup resets when the lineup wraps).
      - Super-innings disable joker insertion (the 5-batter format
        has its own selection mechanic).
    """
    if state.is_super_inning:
        return False, "Joker insertion not allowed in super-innings."
    team = state.batting_team
    if joker.player_id not in {j.player_id for j in team.jokers_available}:
        return False, "Joker not in available pool."
    if joker.player_id in team.jokers_used_this_cycle:
        return False, "Joker already used this cycle."
    return True, ""


def insert_joker(state: GameState, joker: Player, lineup_position: int = -1) -> list[str]:
    """Insert a joker for the next PA via state.batter_override.

    The joker bats in place of the base-lineup batter for ONE plate
    appearance, then returns to the bench. The base lineup position is
    NOT advanced by the joker AB (handled in pa._end_at_bat). Insertion
    is marked on team.jokers_used_this_cycle so the same joker can't be
    inserted twice in the same cycle through the order.

    `lineup_position` is accepted for back-compat but ignored — the
    joker insertion is always "before the next scheduled batter."
    """
    _ = lineup_position
    ok, reason = can_insert_joker(state, joker)
    if not ok:
        return [f"  [Joker insert rejected: {reason}]"]
    team = state.batting_team
    state.batter_override = joker
    team.jokers_used_this_cycle.add(joker.player_id)
    state.events.append({
        "type": "joker_inserted",
        "joker_id": joker.player_id,
        "joker_name": joker.name,
    })
    return [f"  JOKER: {team.name} sends in {joker.name} for the next PA."]


def _legacy_insert_joker(state: GameState, joker: Player, lineup_position: int) -> list[str]:
    """
    Insert a joker at the given lineup position for the current batting team.

    The joker takes that slot immediately; the player previously scheduled at
    that position is skipped for this at-bat (joker bats in their place).

    Returns a list of log lines.
    """
    ok, reason = can_insert_joker(state, joker)
    if not ok:
        return [f"[MANAGER ERROR] Joker insertion rejected: {reason}"]

    team = state.batting_team
    log = [f"  JOKER: {team.name} inserts {joker.name} ({getattr(joker, 'archetype', 'joker')}) into lineup."]

    # Mark joker as used this half so advance_lineup() skips their natural slot.
    team.jokers_used_this_half.add(joker.player_id)
    # Remove from available pool so the same joker is not re-inserted this half.
    team.jokers_available = [j for j in team.jokers_available
                              if j.player_id != joker.player_id]
    # Move the joker to the desired slot within the existing 12-batter lineup.
    # The joker is already in the lineup; remove-then-insert re-orders without
    # changing the list length (preserves the 12-batter active lineup invariant).
    if joker in team.lineup:
        team.lineup.remove(joker)
    team.lineup.insert(lineup_position, joker)
    team.lineup_position = lineup_position % len(team.lineup)

    state.events.append({
        "type": "joker_inserted",   # distinct from the provider intent event "joker_insertion"
        "joker_id": joker.player_id,
        "joker_name": joker.name,
        "lineup_position": lineup_position,
    })
    return log


# ---------------------------------------------------------------------------
# Pinch-hit substitution
# ---------------------------------------------------------------------------

def pinch_hit(state: GameState, replacement: Player) -> list[str]:
    """
    Replace the current scheduled batter with a pinch hitter.

    The replaced batter is removed from the lineup (not just skipped);
    the replacement takes their slot. Standard baseball rules (§2.3).

    Returns log lines.
    """
    if state.is_super_inning:
        return ["[MANAGER ERROR] No pinch hit during super-inning."]

    team = state.batting_team
    pos = team.lineup_position % len(team.lineup)
    replaced = team.lineup[pos]
    team.lineup[pos] = replacement
    log = [f"  PINCH HIT: {replacement.name} bats for {replaced.name}."]

    state.events.append({
        "type": "pinch_hit",
        "replaced_id": replaced.player_id,
        "replaced_name": replaced.name,
        "replacement_id": replacement.player_id,
        "replacement_name": replacement.name,
    })
    return log


# ---------------------------------------------------------------------------
# Pitching change
# ---------------------------------------------------------------------------

def pitching_change(
    state: GameState,
    new_pitcher: Player,
) -> list[str]:
    """
    Replace the current pitcher with new_pitcher.

    Closes the current spell record and opens a new one.
    Returns log lines.
    """
    old_pitcher_id = state.current_pitcher_id
    old_pitcher = state.fielding_team.get_player(old_pitcher_id) if old_pitcher_id else None

    log = []
    if old_pitcher and state.pitcher_spell_count > 0:
        # Close the current spell only when the pitcher actually faced batters.
        spell = SpellRecord(
            pitcher_id=old_pitcher.player_id,
            pitcher_name=old_pitcher.name,
            batters_faced=state.pitcher_spell_count,
            outs_recorded=state.pitcher_outs_this_spell,
            runs_allowed=state.pitcher_runs_this_spell,
            unearned_runs=state.pitcher_unearned_runs_this_spell,
            hits_allowed=state.pitcher_h_this_spell,
            bb=state.pitcher_bb_this_spell,
            k=state.pitcher_k_this_spell,
            hbp=state.pitcher_hbp_this_spell,
            hr_allowed=state.pitcher_hr_this_spell,
            pitches_thrown=state.pitcher_pitches_this_spell,
            out_when_pulled=state.outs,
            start_batter_num=state.pitcher_start_pa + 1,
            half=state.half,
            super_inning_number=state.super_inning_number,
            sb_allowed=state.pitcher_sb_allowed_this_spell,
            cs_caught=state.pitcher_cs_caught_this_spell,
            fo_induced=state.pitcher_fo_induced_this_spell,
            er_arc=list(state.pitcher_er_arc_this_spell),
            k_arc=list(state.pitcher_k_arc_this_spell),
            fo_arc=list(state.pitcher_fo_arc_this_spell),
            bf_arc=list(state.pitcher_bf_arc_this_spell),
        )
        state.spell_log.append(spell)
        log.append(f"  PITCHING CHANGE: {old_pitcher.name} exits "
                   f"({state.pitcher_spell_count} BF this spell).")

    state.current_pitcher_id = new_pitcher.player_id
    state.pitcher_spell_count = 0
    state.pitcher_outs_this_spell = 0
    state.pitcher_runs_this_spell = 0
    state.pitcher_unearned_runs_this_spell = 0
    state.pitcher_h_this_spell = 0
    state.pitcher_bb_this_spell = 0
    state.pitcher_k_this_spell = 0
    state.pitcher_hbp_this_spell = 0
    state.pitcher_hr_this_spell = 0
    state.pitcher_pitches_this_spell = 0
    state.pitcher_sb_allowed_this_spell = 0
    state.pitcher_cs_caught_this_spell = 0
    state.pitcher_fo_induced_this_spell = 0
    state.pitcher_errors_this_spell = 0
    state.pitcher_er_arc_this_spell = [0, 0, 0]
    state.pitcher_k_arc_this_spell  = [0, 0, 0]
    state.pitcher_fo_arc_this_spell = [0, 0, 0]
    state.pitcher_bf_arc_this_spell = [0, 0, 0]
    state.pitcher_start_pa = state.total_pa_this_half
    log.append(f"  {new_pitcher.name} takes the mound.")

    state.events.append({
        "type": "pitching_change",
        "old_pitcher_id": old_pitcher_id,
        "new_pitcher_id": new_pitcher.player_id,
        "new_pitcher_name": new_pitcher.name,
    })
    return log


# ---------------------------------------------------------------------------
# Manager decision heuristics (Phase 2)
# ---------------------------------------------------------------------------

def _batting_deficit(state: GameState) -> int:
    """
    Return how many runs the batting team is trailing by (positive = behind).
    Negative means the batting team leads.
    """
    v = state.score.get("visitors", 0)
    h = state.score.get("home", 0)
    if state.half in ("top", "super_top"):
        return h - v
    return v - h


def _needed_archetype(state: GameState) -> Optional[str]:
    """
    Return the archetype called for by the current game situation, or None.

    Evaluation order (§4.6):
      1. power   — batting team down ≥ JOKER_POWER_DEFICIT with outs remaining.
                   Dominates: fires even when RISP or corners are also present.
      2. speed   — corners: 1B+3B occupied, 2B empty, exactly 1 out.
      3. contact — runners in scoring position (2B or 3B occupied).
    """
    # Power dominates: checked first regardless of base state.
    deficit = _batting_deficit(state)
    if deficit >= cfg.JOKER_POWER_DEFICIT and state.outs < cfg.JOKER_POWER_OUTS_CEIL:
        return "power"

    # Speed: corners (1B+3B, 2B empty, exactly 1 out) — spec §4.6.
    if (
        state.bases[0] is not None
        and state.bases[1] is None
        and state.bases[2] is not None
        and state.outs == 1
    ):
        return "speed"

    # Contact: runners in scoring position.
    if state.runners_in_scoring_position:
        return "contact"

    return None


def should_insert_joker(state: GameState, rng=None) -> Optional[Player]:
    """Leverage-aware joker insertion decision.

    Per-PA call: returns a Player from the team's joker pool to insert,
    or None to let the base lineup proceed normally. Constraints:
      - Each joker can be inserted at most ONCE PER CYCLE through the
        lineup (tracked on Team.jokers_used_this_cycle, reset by
        advance_lineup when the lineup wraps).
      - Insertion is always optional. A manager who never inserts is
        leaving offense on the table but not breaking any rule.

    Decision logic: probability of insertion scales with leverage —
    score-gap-tightness × outs-remaining × runners-on. At max leverage
    (close game, late, runners-on) the per-PA insert probability tops
    out around 35%. At low leverage (blowout, early, bases empty) it's
    near zero. Manager AI quality is then a real differentiator: a
    league-leading manager's joker leverage index — share of insertions
    that landed in high-leverage spots — is itself a stat.
    """
    if state.is_super_inning:
        return None
    # Don't insert a joker while another joker is already mid-AB.
    if getattr(state, "batter_override", None) is not None:
        return None
    team = state.batting_team
    if not team.jokers_available:
        return None
    # A joker can't be inserted if (a) already used this cycle, or (b)
    # currently on base from a prior PA. Without (b), Bonds could be at
    # 2B and "also" inserted to bat again — physically impossible.
    on_base_ids = {pid for pid in state.bases if pid is not None}
    eligible = [
        j for j in team.jokers_available
        if j.player_id not in team.jokers_used_this_cycle
        and j.player_id not in on_base_ids
    ]
    if not eligible:
        return None

    # Leverage components.
    score_gap = abs(state.score.get("visitors", 0) - state.score.get("home", 0))
    outs_left = max(1, 27 - state.outs)
    runners   = state.runner_count

    # Tighter games + later innings + runners on = high leverage.
    gap_factor    = max(0.0, 1.0 - score_gap / 10.0)   # tied = 1.0; 10+ gap = 0
    late_factor   = state.outs / 27.0                  # 0..1, late half = high
    runner_factor = (runners + 1) / 4.0                # 0.25..1.0
    leverage = gap_factor * late_factor * runner_factor

    # Per-PA insertion probability. Cap at 35% even in peak leverage —
    # manager shouldn't burn all 3 jokers on the first eligible PA.
    # Manager persona scales the willingness: a "fiery" skipper with
    # high joker_aggression shoots earlier; a patient one waits for
    # near-peak leverage before spending a joker.
    joker_agg = float(getattr(team, "mgr_joker_aggression", 0.5))
    insert_p = min(0.35, leverage * (0.25 + 0.5 * joker_agg))
    if rng is None:
        import random as _r
        roll = _r.random()
    else:
        roll = rng.random()
    if roll >= insert_p:
        return None

    # Pick the joker best-fit for the spot. Simple v1: best by hitting
    # skill. Future: archetype-aware (speed joker with runners on 1B,
    # power joker with bases empty in scoring spots, etc.).
    return max(eligible, key=lambda j: float(getattr(j, "skill", 0.5) or 0.5))


def _legacy_should_insert_joker(state: GameState) -> Optional[Player]:
    """
    Phase 8 §4.6 heuristic: select the joker whose archetype fits the situation.

    Evaluation order (see _needed_archetype for details):
      1. power   — batting team down ≥ JOKER_POWER_DEFICIT with outs remaining.
      2. speed   — corners (1B+3B, 2B empty, exactly 1 out).
      3. contact — runners in scoring position (2B or 3B occupied).

    Eligibility (§2.3):
      - Joker must be in jokers_available (removed after use each half).
      - Each physical joker bats at most once per half (jokers_used_this_half).
      - No cross-archetype fallback: if the required archetype joker is already
        used, no other joker fires for that situation.

    Returns the joker Player to insert, or None when no situation applies.
    """
    if state.is_super_inning:
        return None
    team = state.batting_team
    if not team.jokers_available:
        return None

    # Filter to available (not-yet-used-this-half) jokers.
    available = [j for j in team.jokers_available
                 if j.player_id not in team.jokers_used_this_half]
    if not available:
        return None

    # Cap: do not insert more than JOKER_MAX_PER_HALF jokers per team per half.
    if len(team.jokers_used_this_half) >= cfg.JOKER_MAX_PER_HALF:
        return None

    archetype = _needed_archetype(state)
    if archetype is None:
        return None

    typed = [j for j in available if getattr(j, "archetype", "") == archetype]
    if not typed:
        # Required archetype unavailable (already used this half) — skip.
        return None
    return max(typed, key=lambda j: j.skill)


def _manager_hook_check(state: GameState) -> bool:
    """Manager-discretion early hook: pull a pitcher who's getting tagged.

    Checked BEFORE fatigue thresholds. Returns True only when the spell is
    going badly enough that even a workhorse manager would consider a change.
    The decision scales with the fielding team's manager persona:

      - quick_hook (0..1) — lower run threshold for pulling.
      - leverage_aware  — extra pull pressure when the game is close.
      - bullpen_aggression — willingness to pull early in the half.

    A neutral manager (0.5 across the board) hooks at ~5 runs in spell.
    A high-quick_hook manager hooks at ~3. A patient one waits for ~7.
    """
    spell_runs = int(getattr(state, "pitcher_runs_this_spell", 0) or 0)
    spell_hits = int(getattr(state, "pitcher_h_this_spell", 0) or 0)
    bf         = int(getattr(state, "pitcher_spell_count", 0) or 0)
    # Need a meaningful sample before considering a hook. Two batters into
    # a spell isn't a "blow up" yet — give the pitcher a chance.
    if bf < 4:
        return False

    fielding = state.fielding_team
    quick   = float(getattr(fielding, "mgr_quick_hook", 0.5))
    bullpen = float(getattr(fielding, "mgr_bullpen_aggression", 0.5))
    lev_aw  = float(getattr(fielding, "mgr_leverage_aware", 0.5))

    # Base run threshold: 8 (very patient) → 2 (very quick).
    run_threshold = max(2, round(8 - 6 * quick))

    # Leverage-aware managers hook earlier when the game is still close.
    # _batting_deficit returns runs the BATTING team trails by — i.e. positive
    # means the fielding team leads. We want to hook earlier in close/tied
    # games and hold longer in blowouts.
    margin = -_batting_deficit(state)        # +ve = fielding team leads
    if abs(margin) <= 3:
        run_threshold -= round(2 * lev_aw)   # close game → quicker
    elif abs(margin) >= 8:
        run_threshold += 2                   # blowout → leave him in
    run_threshold = max(2, run_threshold)

    # Bullpen-aggressive managers also pull on a baserunner pile-up
    # (lots of hits this spell, even if runs haven't all scored yet).
    hit_threshold = max(4, round(10 - 6 * bullpen))

    # The actual call: blew up by runs, OR is bleeding hits and the manager
    # is willing to use the pen.
    if spell_runs >= run_threshold:
        return True
    if spell_hits >= hit_threshold and bullpen >= 0.5:
        return True
    return False


def should_change_pitcher(state: GameState) -> bool:
    """
    Trigger a pitching change.

    Two layers, evaluated in order:
      1. Manager-discretion early hook (`_manager_hook_check`) — pulls a
         pitcher who's getting tagged, gated by the fielding team's manager
         persona (quick_hook / leverage_aware / bullpen_aggression).
      2. Fatigue thresholds (the original logic) — emergent role-aware
         pulls based on pitcher stamina + spell length.

    Roles are derived LIVE from the current pitcher's Stamina rating —
    no stored `pitcher_role` tag is consulted.

    First-spell role mapping (the "starter"):
      - stamina >= WORKHORSE_STAMINA_THRESHOLD (0.62)
            → workhorse pull thresholds (deepest stints)
      - stamina <= OPENER_STAMINA_THRESHOLD    (0.40)
            → opener pull thresholds (pull fast, then committee)
      - else → classical SP (moderate stints)

    Subsequent spells (relief context) use RELIEVER_CHANGE_BASE/SCALE
    regardless of stamina — the spell is already a relief appearance.

    Threshold = max(base, base + round(pitcher_skill * scale))
    """
    if state.is_super_inning:
        return False
    pitcher = state.get_current_pitcher()
    if pitcher is None:
        return False

    # Layer 1: manager discretion. A skipper with a quick hook will pull a
    # starter getting torched well before fatigue would force the change.
    if _manager_hook_check(state):
        return True

    # Detect "this is a relief appearance" by checking spell_log: if any
    # previous spell exists in the current half, we're past the SP.
    in_relief = any(
        rec.half == state.half
        for rec in state.spell_log
    )

    stamina = float(getattr(pitcher, "stamina", pitcher.pitcher_skill) or 0.5)

    if in_relief:
        # Relief appearance — short-burst thresholds.
        base  = cfg.RELIEVER_CHANGE_BASE
        scale = cfg.RELIEVER_CHANGE_SCALE
        threshold = max(base, base + round(pitcher.pitcher_skill * scale))
        return state.pitcher_spell_count >= threshold

    # First-spell ("starter") — derive emergent role from stamina.
    if stamina >= cfg.WORKHORSE_STAMINA_THRESHOLD:
        # Workhorse: ride deep into the half.
        base  = cfg.WORKHORSE_CHANGE_BASE
        scale = cfg.WORKHORSE_CHANGE_SCALE
        threshold = max(base, base + round(pitcher.pitcher_skill * scale))
        if state.pitcher_spell_count < threshold:
            return False
        # Even past threshold, only change if a reliever can come in
        # (i.e. the half is "late enough"). Otherwise let the SP keep going.
        if state.outs < cfg.RELIEVER_ENTRY_OUTS_MIN:
            # Past threshold but still early — extend the SP rather than
            # burn an early reliever. Pull only if blown out (≥ 8 runs
            # this spell), which cuts off true disasters.
            return state.pitcher_runs_this_spell >= 8
        return True

    if stamina <= cfg.OPENER_STAMINA_THRESHOLD:
        # Opener: pull after a short stint, let the committee take over.
        # No "must wait until late" guard — that's exactly what makes
        # this a viable strategy for stamina-poor staffs.
        base  = cfg.OPENER_CHANGE_BASE
        scale = cfg.OPENER_CHANGE_SCALE
        threshold = max(base, base + round(pitcher.pitcher_skill * scale))
        return state.pitcher_spell_count >= threshold

    # Classical SP — moderate stint, no late-relief guard.
    base  = cfg.PITCHER_CHANGE_BASE
    scale = cfg.PITCHER_CHANGE_SCALE
    threshold = max(base, base + round(pitcher.pitcher_skill * scale))
    return state.pitcher_spell_count >= threshold


def pick_new_pitcher(state: GameState) -> Optional[Player]:
    """
    Task #65: derive the next pitcher's role LIVE from each candidate's
    current attributes (`stamina` and `pitcher_skill` aka Stuff) — no
    stored role tag is read.

    Rule of thumb (per pitcher, evaluated each appearance):
      - outs >= 19 (late innings / closer)   → max Stuff
      - outs in 10..18 (middle relief)       → max Stuff, with mid-range
                                               Stamina preferred so true
                                               workhorses are saved for
                                               starts
      - outs <  10 (long relief / spot start)→ max Stamina

    Falls back to whatever pitcher is left when a bucket is thin. Position
    players and jokers are never used as relievers.
    """
    fielding   = state.fielding_team
    current_id = state.current_pitcher_id
    restricted: set = set()

    already_pitched = {
        r.pitcher_id for r in state.spell_log if r.half == state.half
    }

    pitcher_candidates = [
        p for p in fielding.roster
        if p.is_pitcher
        and p.player_id != current_id
        and p.player_id not in restricted
        and p.player_id not in already_pitched
    ]

    outs = getattr(state, "outs", 0) or 0

    # Emergency PP-pitcher path: in a true blowout, a strong-arm position
    # player can take the mound to absorb the last few outs and preserve
    # the bullpen. Tight gating per user direction: late in the half AND
    # massive deficit. Triggers only when the FIELDING team is down big
    # (i.e. they're losing while their bullpen is getting torched).
    fielding_id   = fielding.team_id
    fielding_score = state.score.get(fielding_id, 0)
    other_id   = "home" if fielding_id == "visitors" else "visitors"
    batting_score = state.score.get(other_id, 0)
    deficit = batting_score - fielding_score   # how much we trail by
    outs_left = max(0, 27 - outs)
    if (deficit >= cfg.PP_PITCH_DEFICIT_MIN
            and outs_left <= cfg.PP_PITCH_OUTS_LEFT_MAX
            and outs_left > 0):
        # Find the position player with the highest arm rating who isn't
        # already the current pitcher. Must clear the arm threshold.
        pp_candidates = [
            p for p in fielding.roster
            if not p.is_pitcher
            and p.player_id != current_id
            and float(getattr(p, "arm", 0.5) or 0.5) >= cfg.PP_PITCH_ARM_MIN
        ]
        if pp_candidates:
            return max(pp_candidates, key=lambda pl: float(getattr(pl, "arm", 0.5) or 0.5))

    # Hard rest filter: pitchers who appeared in the last day or two are
    # de-prioritized from relief eligibility. We use a tier system mirroring
    # the SP picker — start with the most rested pool and broaden only when
    # nobody qualifies. This is what stops a single arm from being the
    # manager's "everyday closer" — the rotation has to cycle.
    for min_rest in (3, 2, 1):
        rested = [
            p for p in pitcher_candidates
            if int(getattr(p, "days_rest", 99) or 99) >= min_rest
        ]
        if rested:
            pitcher_candidates = rested
            break

    def _stuff(p: Player) -> float:
        return float(getattr(p, "pitcher_skill", 0.5) or 0.5)

    def _stamina(p: Player) -> float:
        # Fall back to pitcher_skill when stamina hasn't been hydrated
        # (e.g. legacy DB rows pre-Task-#65).
        s = getattr(p, "stamina", None)
        return float(s if s is not None else _stuff(p))

    def _rest_penalty(p: Player) -> float:
        """Multiplicative penalty applied to a pitcher's score based on
        recent workload. Pitched yesterday with a heavy load → strong
        penalty; pitched 3+ days ago → no penalty. Identity at default
        days_rest=99 / pitch_debt=0.
        """
        days_rest = int(getattr(p, "days_rest", 99) or 99)
        pitch_debt = int(getattr(p, "pitch_debt", 0) or 0)
        # Days-rest penalty: 0d → -0.30, 1d → -0.15, 2d → -0.05, 3d+ → 0.
        if days_rest <= 0:
            rp = 0.30
        elif days_rest == 1:
            rp = 0.15
        elif days_rest == 2:
            rp = 0.05
        else:
            rp = 0.0
        # Heavy debt adds another 0.0–0.15 on top.
        # 100+ pitches in last 5 days → -0.15.
        rp += min(0.15, pitch_debt / 100.0 * 0.15)
        return rp

    def _score(p: Player) -> float:
        if outs >= 19:
            # Late / closer — pure Stuff. Tired arms get penalized.
            return _stuff(p) - _rest_penalty(p)
        if outs >= 10:
            # Middle relief — Stuff dominates, but penalize the highest-
            # Stamina arms so they remain available for starts.
            return _stuff(p) - 0.25 * max(0.0, _stamina(p) - 0.55) - _rest_penalty(p)
        # Long relief / spot start — pure Stamina, rest-adjusted.
        return _stamina(p) - _rest_penalty(p)

    if pitcher_candidates:
        return max(pitcher_candidates, key=_score)

    # No fresh arm available — re-allow any non-restricted pitcher (even
    # one already pulled this half) before falling back further.
    fallback_pitchers = [
        p for p in fielding.roster
        if p.is_pitcher
        and p.player_id != current_id
        and p.player_id not in restricted
        and not getattr(p, "is_joker", False)
    ]
    if fallback_pitchers:
        return max(fallback_pitchers, key=_score)

    return None


def should_pinch_hit(state: GameState, rng=None) -> Optional[Player]:
    """Manager-tendency-driven pinch hit decision.

    Sends up a permanent replacement for the scheduled batter when the
    situation is high-leverage AND the replacement materially upgrades the
    spot. Two upgrade paths:

      * Skill upgrade — bench bat is meaningfully better than the scheduled
        hitter (covers the classic "weak hitter, big spot" case).
      * Platoon upgrade — bench bat has the platoon advantage vs the current
        pitcher and the scheduled batter does not. Gated by the manager's
        platoon_aggression so a dead-ball traditionalist won't swap for
        platoon reasons but a bullpen-innovator-coded skipper will.

    The decision is also gated by mgr_pinch_hit_aggression: a passive
    skipper barely uses the bench; an aggressive one will spend bench bats
    in the middle of close games.
    """
    if state.is_super_inning:
        return None

    batter = state.current_batter
    team   = state.batting_team

    # Only consider a pinch hit when the spot is non-trivial — the manager
    # shouldn't burn a bench bat in a 9-run blowout. "Meaningful spot"
    # means runners on, OR a tie/one-run game with outs remaining, OR the
    # late half of the at-bat-cycle when leverage compounds.
    score_diff = abs(state.score.get("visitors", 0) - state.score.get("home", 0))
    runners_on = bool(state.runners_on_base)
    late       = state.outs >= 18           # last third of the half
    tight      = score_diff <= cfg.PINCH_HIT_SCORE_DIFF_MAX
    if not (runners_on or (tight and late)):
        return None
    if score_diff > cfg.PINCH_HIT_SCORE_DIFF_MAX + 2 and not late:
        return None

    # Tendency gates. mgr_pinch_hit_aggression scales the per-spot trigger
    # probability; mgr_leverage_aware sharpens the response when the score
    # is close. A neutral manager (0.5) fires in maybe 20% of qualifying
    # spots; an aggressive one (0.9) fires in ~50%.
    ph_agg  = float(getattr(team, "mgr_pinch_hit_aggression", 0.5))
    lev_aw  = float(getattr(team, "mgr_leverage_aware", 0.5))
    plat_ag = float(getattr(team, "mgr_platoon_aggression", 0.5))
    base_p  = 0.10 + 0.50 * ph_agg
    if tight:
        base_p += 0.15 * lev_aw
    if late and tight:
        base_p += 0.10
    base_p = max(0.0, min(0.7, base_p))

    # Candidate pool: non-pitchers on the roster who aren't already in the
    # lineup (true bench bats; lineup players would otherwise duplicate).
    lineup_ids = {p.player_id for p in team.lineup}
    candidates = [
        p for p in team.roster
        if not p.is_pitcher and p.player_id not in lineup_ids
    ]
    if not candidates:
        return None

    pitcher = state.get_current_pitcher()
    p_throws = (getattr(pitcher, "throws", "") or "") if pitcher else ""

    def _has_platoon_edge(player) -> bool:
        if not p_throws:
            return False
        b = (getattr(player, "bats", "") or "")
        if not b:
            return False
        # Switch hitters always have the platoon advantage.
        if b == "S":
            return True
        # Opposite-handed batter vs pitcher = platoon edge.
        return b != p_throws

    # Skill upgrade pick.
    skill_best = max(candidates, key=lambda p: p.skill)
    skill_edge = skill_best.skill - batter.skill

    # Platoon upgrade pick: best bench bat with the edge, when the current
    # batter doesn't already have it.
    cur_has_edge = _has_platoon_edge(batter)
    platoon_pool = [c for c in candidates if _has_platoon_edge(c)]
    platoon_best = (
        max(platoon_pool, key=lambda p: p.skill) if platoon_pool else None
    )

    # Decide which upgrade path (if any) clears the bar.
    use_skill   = skill_edge >= cfg.PINCH_HIT_SKILL_EDGE
    use_platoon = (
        platoon_best is not None
        and not cur_has_edge
        and plat_ag >= 0.45
        and (platoon_best.skill + 0.05) >= batter.skill - cfg.PINCH_HIT_SKILL_EDGE
    )
    if not (use_skill or use_platoon):
        return None

    # Roll against the tendency-scaled probability. Falls through silently
    # most of the time even when an upgrade exists, so a single bench bat
    # isn't burned on the first eligible spot.
    if rng is None:
        import random as _r
        roll = _r.random()
    else:
        roll = rng.random()
    if roll >= base_p:
        return None

    # Prefer the platoon edge for a platoon-aggressive skipper, otherwise
    # the skill upgrade.
    if use_platoon and (not use_skill or plat_ag >= 0.7):
        return platoon_best
    return skill_best


def defensive_sub(
    state: GameState,
    player_out: Player,
    player_in: Player,
) -> list:
    """O27-specific tactical sub: replace a lineup player with a bench
    bat for defensive upgrade reasons. Unlike `pinch_hit` (which
    replaces the current scheduled batter for offense), this can target
    ANY lineup slot and is called by the FIELDING team's manager.

    The swap-in takes the lineup slot, so when their slot comes up to
    bat in the team's offensive half (or in super-innings), they're the
    one batting. Frees the manager to "spend" a slugger after they've
    already banked PAs and lock in defensive specialists for the rest
    of the fielding half.
    """
    if state.is_super_inning:
        return ["[MANAGER ERROR] No defensive sub during super-inning."]
    fielding = state.fielding_team
    if player_out not in fielding.lineup:
        return [f"  [MANAGER ERROR] {player_out.name} not in fielding lineup."]
    idx = fielding.lineup.index(player_out)
    fielding.lineup[idx] = player_in
    log = [
        f"  DEFENSIVE SUB: {player_in.name} replaces {player_out.name} "
        f"in the field (and takes their lineup slot)."
    ]
    state.events.append({
        "type": "defensive_sub",
        "team_id": fielding.team_id,
        "out_id":  player_out.player_id,
        "in_id":   player_in.player_id,
    })
    return log


def should_defensive_sub(state: GameState, rng=None) -> Optional[dict]:
    """Tactical defensive substitution by the FIELDING team's manager.

    O27's structure (one 27-out half per team) lets a manager bank
    offense early, then swap weak-defense bats out for defensive
    specialists who'll cover the rest of the fielding half. The road
    team's classic version: bat power up top, then load the field
    with glove-first guys for the bottom half — those guys won't bat
    again unless the game goes to a super-inning.

    Conditions:
      - Regulation half, not super-inning.
      - We've banked some defense already (state.outs >= 6) so this
        isn't a first-batter overreaction.
      - The fielding team has a meaningfully-better-defense bench bat
        available (not in the current lineup).

    No hard cap on subs per game — real teams cycle through the bench,
    and O27's continuous-half structure creates more spots, not fewer.
    The mechanic naturally throttles itself: each successful sub
    removes a bench bat from the candidate pool, and the worst-defense
    starter changes dynamically as the lineup shifts.

    Probability scales with mgr_bench_usage. A 0.5 manager fires this
    around 1.5% per opportunity check; a 0.9 manager around 4%. Over
    a 27-out half that's roughly 0.5–2 subs per team per game for
    average managers, more for aggressive ones.
    """
    if state.is_super_inning:
        return None
    if state.outs < 6:
        return None

    fielding = state.fielding_team

    bench_usage = float(getattr(fielding, "mgr_bench_usage", 0.5))
    p = 0.005 + 0.040 * bench_usage   # 0.5% .. 4.5%
    if rng is None:
        import random as _r
        roll = _r.random()
    else:
        roll = rng.random()
    if roll >= p:
        return None

    # Find the worst-defense lineup player (excluding pitcher and catcher;
    # catcher swaps need different handling because the catcher arm rating
    # is stamped on the team).
    lineup = list(fielding.lineup)
    candidates_out = [
        pl for pl in lineup
        if not pl.is_pitcher
        and (getattr(pl, "position", "") not in ("C", "DH"))
    ]
    if not candidates_out:
        return None
    worst = min(
        candidates_out,
        key=lambda pl: float(getattr(pl, "defense", 0.5) or 0.5),
    )

    # Bench candidates: roster non-pitchers not currently in the lineup.
    lineup_ids = {pl.player_id for pl in lineup}
    bench = [
        pl for pl in fielding.roster
        if not pl.is_pitcher and pl.player_id not in lineup_ids
    ]
    if not bench:
        return None
    best = max(bench, key=lambda pl: float(getattr(pl, "defense", 0.5) or 0.5))

    # Only swap if best is meaningfully better. 0.05 is the same edge
    # the pinch-hit logic uses for skill upgrades.
    edge = (float(getattr(best,  "defense", 0.5) or 0.5)
          - float(getattr(worst, "defense", 0.5) or 0.5))
    if edge < 0.05:
        return None

    return {"player_out": worst, "player_in": best}


def should_swap_offensive_for_defense(state: GameState, rng=None) -> Optional[Player]:
    """Mid-batting-half tactical swap: pull the current scheduled batter
    after they've banked a PA, bring in a defensive specialist who'll
    cover the rest of the team's fielding half.

    O27-specific tactic — only meaningful for the road team in the top
    half, since they bat first and then field. The home team bats last
    so their fielding half is already done by the time they're at the
    plate, and there's no defense to lock in.

    Conditions:
      - Regulation half (no super-innings).
      - state.half == "top" — visitors batting, will field next.
      - Lineup has cycled at least once (cycle_number >= 1) so the
        slugger has already had at least one AB to bank.
      - Current batter has notably worse defense than the best bench bat.

    No hard cap — the candidate pool naturally depletes as bench bats
    enter the lineup. Probability scales 0.5% .. 4.5% with
    mgr_bench_usage. Sluggish skippers basically never do this;
    aggressive ones cycle multiple gloves in across the back half of
    their batting block.

    Returns the replacement Player or None. Caller wraps in a
    tactical_def_swap event (logged separately from leverage-driven
    pinch hits for stat-tracking purposes).
    """
    if state.is_super_inning:
        return None
    if state.half != "top":
        return None

    team = state.batting_team
    if team.lineup_cycle_number < 1:
        return None

    bench_usage = float(getattr(team, "mgr_bench_usage", 0.5))
    p = 0.005 + 0.040 * bench_usage   # 0.5% .. 4.5% per PA past first cycle
    if rng is None:
        import random as _r
        roll = _r.random()
    else:
        roll = rng.random()
    if roll >= p:
        return None

    batter = state.current_batter
    if batter.is_pitcher:
        return None

    lineup_ids = {pl.player_id for pl in team.lineup}
    bench = [
        pl for pl in team.roster
        if not pl.is_pitcher and pl.player_id not in lineup_ids
    ]
    if not bench:
        return None
    best = max(bench, key=lambda pl: float(getattr(pl, "defense", 0.5) or 0.5))
    edge = (float(getattr(best,   "defense", 0.5) or 0.5)
          - float(getattr(batter, "defense", 0.5) or 0.5))
    if edge < 0.05:
        return None
    return best


def should_bunt(state: GameState, rng=None) -> Optional[dict]:
    """Manager-driven sacrifice bunt decision.

    Returns a synthetic event dict {"type": "sac_bunt", "outcome": ...}
    when the manager calls a bunt, otherwise None. The outcome is rolled
    here (rather than during a normal at-bat) so the engine can apply it
    directly without re-running the contact pipeline.

    Conditions for considering a bunt:
      - regulation half (no super-innings)
      - runner on 1B (or 1B + 2B); 2B-only is rare and skipped here
      - early-to-middle of the half (outs < 18 — too few outs left
        in the last third to spend one for a base)
      - batter is not a power threat
      - manager call rate scales with mgr_run_game and inversely with
        mgr_leverage_aware (modern analytics skippers don't bunt)
    """
    if state.is_super_inning:
        return None
    bases = state.bases
    # Need at least a 1B runner (the canonical bunt setup).
    if bases[0] is None:
        return None
    if state.outs >= 18:
        return None

    batter = state.current_batter
    if batter.is_pitcher:
        return None
    power = float(getattr(batter, "power", 0.5) or 0.5)
    if power > 0.55:
        return None  # don't bunt with a power threat

    team = state.batting_team
    run_game  = float(getattr(team, "mgr_run_game", 0.5))
    leverage  = float(getattr(team, "mgr_leverage_aware", 0.5))

    bunt_p = (
        cfg.SAC_BUNT_BASE_PROB
        * (run_game * cfg.SAC_BUNT_RUNGAME_SCALE / 0.5 if run_game > 0 else 0)
        * (1.0 + (1.0 - leverage) * cfg.SAC_BUNT_LEVERAGE_DAMPER)
    )
    # Score-margin tilt: more likely down 1-2 in the last third of the
    # batter's at-bat-cycle; less likely when leading by 4+.
    v = state.score.get("visitors", 0)
    h = state.score.get("home", 0)
    bat_score, fld_score = (v, h) if state.half in ("top", "super_top") else (h, v)
    margin = fld_score - bat_score
    if margin == 1 or margin == 2:
        bunt_p *= 1.5
    elif margin <= -3:
        bunt_p *= 0.3

    bunt_p = max(0.0, min(0.20, bunt_p))
    if rng is None:
        import random as _r
        roll = _r.random()
    else:
        roll = rng.random()
    if roll >= bunt_p:
        return None

    # Roll the bunt outcome. Three buckets:
    #   bunt-for-hit  — batter safe at 1B, runners advance 1
    #   sacrifice     — batter out at 1B, runners advance 1 (the canonical play)
    #   failed bunt   — popup or force at lead, no advance, batter out
    speed = float(getattr(batter, "speed", 0.5) or 0.5)
    hit_p = max(0.0, cfg.SAC_BUNT_HIT_BASE
                + (speed - 0.5) * cfg.SAC_BUNT_HIT_SPEED_SCALE)
    fail_p = cfg.SAC_BUNT_FAIL_RATE
    r = (rng or __import__("random")).random()
    if r < hit_p:
        kind = "hit"
    elif r < hit_p + fail_p:
        kind = "fail"
    else:
        kind = "sacrifice"

    return {"type": "sac_bunt", "outcome": kind}
