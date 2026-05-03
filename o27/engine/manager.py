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
    """
    Check whether a joker can be inserted right now.

    Returns (ok, reason). If ok is False, reason explains why.

    Constraints (§2.3):
      - The joker must be in jokers_available for the batting team.
      - Each joker may bat only once per half-inning.
      - Cannot be used in a super-inning lineup (no jokers in super).
    """
    team = state.batting_team
    if state.is_super_inning:
        return False, "Joker insertion not available in super-inning."
    if joker.player_id not in {j.player_id for j in team.jokers_available}:
        return False, f"{joker.name} is not an available joker for this team."
    if joker.player_id in team.jokers_used_this_half:
        return False, f"{joker.name} has already batted this half-inning."
    return True, ""


def insert_joker(state: GameState, joker: Player, lineup_position: int) -> list[str]:
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
            hits_allowed=state.pitcher_h_this_spell,
            bb=state.pitcher_bb_this_spell,
            k=state.pitcher_k_this_spell,
            hbp=state.pitcher_hbp_this_spell,
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
    state.pitcher_h_this_spell = 0
    state.pitcher_bb_this_spell = 0
    state.pitcher_k_this_spell = 0
    state.pitcher_hbp_this_spell = 0
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
    Phase 8: trigger a pitching change using role-aware fatigue thresholds.

    Workhorse pitchers use WORKHORSE_CHANGE_BASE/SCALE (deeper stints).
    Committee pitchers use COMMITTEE_CHANGE_BASE/SCALE (short stints).
    All others fall back to the generic PITCHER_CHANGE_BASE/SCALE.

    Threshold = max(base, base + round(pitcher_skill * scale))
    """
    if state.is_super_inning:
        return False
    pitcher = state.get_current_pitcher()
    if pitcher is None:
        return False
    role = getattr(pitcher, "pitcher_role", "")
    if role == "workhorse":
        base  = cfg.WORKHORSE_CHANGE_BASE
        scale = cfg.WORKHORSE_CHANGE_SCALE
    elif role == "committee":
        base  = cfg.COMMITTEE_CHANGE_BASE
        scale = cfg.COMMITTEE_CHANGE_SCALE
    else:
        base  = cfg.PITCHER_CHANGE_BASE
        scale = cfg.PITCHER_CHANGE_SCALE
    threshold = max(base, base + round(pitcher.pitcher_skill * scale))
    return state.pitcher_spell_count >= threshold


def pick_new_pitcher(state: GameState) -> Optional[Player]:
    """
    Pick the best available non-restricted pitcher from the fielding team's
    roster, excluding:
      - the current pitcher
      - fielding-restricted jokers (batted jokers)
      - any pitcher already pulled this half (they stay in the dugout)

    Preference order: committee pitchers (role="committee") first, then
    remaining non-joker players sorted by pitcher_skill.  The workhorse
    (is_pitcher=True, role="workhorse") is treated as a last resort once
    they have been pulled, so they do not crowd out committee relievers.

    Returns None if no other pitcher is available.
    """
    fielding   = state.fielding_team
    current_id = state.current_pitcher_id
    restricted = fielding.joker_fielding_restricted

    already_pitched = {
        r.pitcher_id for r in state.spell_log if r.half == state.half
    }

    candidates = [
        p for p in fielding.roster
        if p.player_id != current_id
        and p.player_id not in restricted
        and p.player_id not in already_pitched
        and not p.is_joker
    ]
    if not candidates:
        return None

    candidates.sort(
        key=lambda p: (
            getattr(p, "pitcher_role", "") == "committee",
            p.pitcher_skill,
        ),
        reverse=True,
    )
    return candidates[0]


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

    # Prefer joker insertion when jokers are still available this half.
    team = state.batting_team
    jokers_left = [j for j in team.jokers_available
                   if j.player_id not in team.jokers_used_this_half]
    if jokers_left:
        return None

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
        and not p.is_joker
        and p.player_id not in lineup_ids
    ]
    if not candidates:
        return None

    best = max(candidates, key=lambda p: p.skill)
    # Only substitute if the replacement offers a meaningful skill upgrade.
    if best.skill <= batter.skill + cfg.PINCH_HIT_SKILL_EDGE:
        return None

    return best
