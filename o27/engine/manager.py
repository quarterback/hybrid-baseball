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
    """No-op stub: jokers were removed in Task #47.

    Always returns False. Kept for legacy import compatibility.
    """
    return False, "Jokers were removed in Task #47."


def insert_joker(state: GameState, joker: Player, lineup_position: int) -> list[str]:
    """No-op stub: jokers were removed in Task #47.

    Always returns an empty log. Kept for legacy import compatibility.
    """
    return []


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


def should_insert_joker(state: GameState) -> Optional[Player]:
    """No-op stub: jokers were removed in Task #47. Always returns None."""
    return None


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


def should_change_pitcher(state: GameState) -> bool:
    """
    Trigger a pitching change using emergent role-aware fatigue thresholds.

    Roles are derived LIVE from the current pitcher's Stamina rating —
    no stored `pitcher_role` tag is consulted. This is what lets a team
    naturally adopt an opener-and-committee strategy when they're short
    on stamina and a workhorse-ride strategy when they have the arms.

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


def should_pinch_hit(state: GameState) -> Optional[Player]:
    """
    Phase 2 heuristic: send up a pinch hitter for the pitcher when:
      - Current scheduled batter is the pitcher (is_pitcher=True), AND
      - Runners in scoring position (2B or 3B occupied), AND
      - No jokers remain available to bat this half (joker insertion preferred
        when jokers exist; pinch hit is the fallback), AND
      - Game is in a high-leverage tie-or-close situation
        (score within cfg.PINCH_HIT_SCORE_DIFF_MAX).

    The replacement is the highest-skill non-pitcher non-joker roster member
    who is distinct from the current batter.  Returns None when conditions are
    not met or no improvement is available.
    """
    if state.is_super_inning:
        return None

    batter = state.current_batter
    if not batter.is_pitcher:
        return None

    # Jokers removed in Task #47 — proceed straight to pinch-hit eligibility.
    team = state.batting_team

    if not state.runners_in_scoring_position:
        return None

    # Only pinch hit in very tight, high-leverage situations.
    score_diff = abs(state.score.get("visitors", 0) - state.score.get("home", 0))
    if score_diff > cfg.PINCH_HIT_SCORE_DIFF_MAX:
        return None

    # In O27, all 12 active players are in the lineup from the start, so
    # candidates must come from roster members NOT currently in the lineup
    # (i.e., genuine "bench" players added before the game for this purpose).
    # Selecting an active lineup player would duplicate them in the batting order.
    lineup_ids = {p.player_id for p in team.lineup}
    candidates = [
        p for p in team.roster
        if not p.is_pitcher
        and p.player_id not in lineup_ids
    ]
    if not candidates:
        return None

    best = max(candidates, key=lambda p: p.skill)
    # Only substitute if the replacement offers a meaningful skill upgrade.
    if best.skill <= batter.skill + cfg.PINCH_HIT_SKILL_EDGE:
        return None

    return best
