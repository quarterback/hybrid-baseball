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
from engine.state import GameState, Player, SpellRecord
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
    # Set the current batter to the joker by adjusting lineup_position.
    # We do this by inserting the joker into the lineup at the specified slot
    # and setting the position to point at it.
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
        # Close the current spell.
        spell = SpellRecord(
            pitcher_id=old_pitcher.player_id,
            pitcher_name=old_pitcher.name,
            batters_faced=state.pitcher_spell_count,
            half=state.half,
        )
        state.spell_log.append(spell)
        log.append(f"  PITCHING CHANGE: {old_pitcher.name} exits "
                   f"({state.pitcher_spell_count} BF this spell).")

    state.current_pitcher_id = new_pitcher.player_id
    state.pitcher_spell_count = 0
    log.append(f"  {new_pitcher.name} takes the mound.")

    state.events.append({
        "type": "pitching_change",
        "old_pitcher_id": old_pitcher_id,
        "new_pitcher_id": new_pitcher.player_id,
        "new_pitcher_name": new_pitcher.name,
    })
    return log


# ---------------------------------------------------------------------------
# Manager decision heuristics (Phase 2 stubs)
# ---------------------------------------------------------------------------

def should_insert_joker(state: GameState) -> Optional[Player]:
    """
    Phase 1 stub: always returns None (no automatic joker insertion).

    Phase 2 (§4.6): insert highest-skill joker when:
      - Runners in scoring position AND
      - Current scheduled batter is a weak hitter (e.g., pitcher) AND
      - Game leverage is high (close score, deep in half).
    """
    return None


def should_change_pitcher(state: GameState) -> bool:
    """
    Phase 1 stub: always returns False.

    Phase 2: trigger when pitcher_spell_count exceeds a fatigue threshold
    derived from pitcher skill.
    """
    return False


def should_pinch_hit(state: GameState) -> Optional[Player]:
    """
    Phase 1 stub: always returns None.

    Phase 2: identify situations where a bench bat is clearly superior to the
    scheduled hitter.
    """
    return None
