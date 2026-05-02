"""
Manager decision logic for O27.

Covers:
  - Joker insertion (§2.3 / §4.6)
  - Pinch-hit substitution
  - Pitching changes

Phase 1: constraint enforcement + heuristic stubs that always return False
         (no AI decisions; Phase 1 tests drive manager events explicitly).
Phase 2: heuristic logic for joker insertion, pitching changes, pinch hits.
"""

from __future__ import annotations
from .state import GameState, Player, SpellRecord
from typing import Optional


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
        return False, f"{joker.name} is not an available joker."
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
    log = [f"  JOKER: {team.name} inserts {joker.name} (joker) into lineup."]

    # Mark joker as used this half.
    team.jokers_used_this_half.add(joker.player_id)
    # Remove from available pool.
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
        "type": "joker_insertion",
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
    if old_pitcher:
        # Close the current spell with full stats.
        spell = SpellRecord(
            pitcher_id=old_pitcher.player_id,
            pitcher_name=old_pitcher.name,
            batters_faced=state.pitcher_spell_count,
            outs_recorded=state.pitcher_outs_this_spell,
            runs_allowed=state.pitcher_runs_this_spell,
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

def should_insert_joker(state: GameState) -> Optional[Player]:
    """
    Phase 2 §4.6 heuristic: insert highest-skill available joker when:
      - Runners in scoring position, AND
      - Current scheduled batter is a weak hitter (skill < 0.38 OR is_pitcher), AND
      - Game leverage is high: score within 4 AND at least 5 outs remaining.

    Returns the joker Player to insert, or None.
    """
    if state.is_super_inning:
        return None
    team = state.batting_team
    if not team.jokers_available:
        return None
    # Only consider jokers not yet used this half.
    available = [j for j in team.jokers_available
                 if j.player_id not in team.jokers_used_this_half]
    if not available:
        return None

    # Condition 1: runners in scoring position.
    if not state.runners_in_scoring_position:
        return None

    # Condition 2: current scheduled batter is weak.
    batter = state.current_batter
    batter_is_weak = batter.skill < 0.38 or batter.is_pitcher
    if not batter_is_weak:
        return None

    # Condition 3: high leverage — close game with outs remaining.
    score_diff = abs(state.score.get("visitors", 0) - state.score.get("home", 0))
    high_leverage = score_diff <= 4 and state.outs < 22
    if not high_leverage:
        return None

    # Insert the highest-skill available joker.
    return max(available, key=lambda j: j.skill)


def should_change_pitcher(state: GameState) -> bool:
    """
    Phase 2: trigger a pitching change when the pitcher's spell count exceeds
    their fatigue threshold (skill-scaled: higher-skill pitchers go longer).

    Threshold = max(10, 10 + round(pitcher.pitcher_skill * 20))  → 10–30 BF.
    """
    if state.is_super_inning:
        return False
    pitcher = state.get_current_pitcher()
    if pitcher is None:
        return False
    threshold = max(10, 10 + round(pitcher.pitcher_skill * 20))
    return state.pitcher_spell_count >= threshold


def pick_new_pitcher(state: GameState) -> Optional[Player]:
    """
    Pick the best available non-restricted pitcher from the fielding team's
    roster, excluding the current pitcher and fielding-restricted jokers.

    Returns None if no other pitcher is available.
    """
    fielding = state.fielding_team
    current_id = state.current_pitcher_id
    restricted = fielding.joker_fielding_restricted
    candidates = [
        p for p in fielding.roster
        if p.player_id != current_id
        and p.player_id not in restricted
        and not p.is_joker
    ]
    if not candidates:
        return None
    # Prefer is_pitcher=True players; among equals, highest pitcher_skill.
    candidates.sort(key=lambda p: (p.is_pitcher, p.pitcher_skill), reverse=True)
    return candidates[0]


def should_pinch_hit(state: GameState) -> Optional[Player]:
    """
    Phase 2 heuristic: send up a pinch hitter for the pitcher when:
      - Current scheduled batter is the pitcher (is_pitcher=True), AND
      - Runners in scoring position (2B or 3B occupied), AND
      - No jokers remain available to bat this half (joker insertion preferred
        when jokers exist; pinch hit is the fallback), AND
      - Game is in a high-leverage tie-or-close situation (score within 1).

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
    if score_diff > 1:
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
    if best.skill <= batter.skill + 0.05:
        return None

    return best
