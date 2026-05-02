"""
Stay-vs-run decision logic for O27.

This module enforces all §2.5–§2.6 constraints and houses the stay-vs-run
heuristic (Phase 1: rule-based; Phase 2: probabilistic with tunable params).

Key rules from the PRD:
- Stay is only available when at least one runner is on base.
- Two-strike contact + stay → batter is OUT (runners advance per fielding play).
- Caught fly ball + stay → batter is OUT (standard fly out; runners may tag).
- Home runs: stay does not apply — batter must run.
- Triples: run recommended (reaching 3B is too valuable to forfeit).
- Bunt + stay: allowed; recorded as a hit.
- Stay with runner thrown out: that runner is out; at-bat continues with fresh count.
- Multiple stays per at-bat: allowed; no cap.
- No force at 1B on a stay play; no double play through first.
"""

from __future__ import annotations
from engine.state import GameState, Player


# ---------------------------------------------------------------------------
# Constraint checks
# ---------------------------------------------------------------------------

def stay_available(state: GameState) -> bool:
    """
    Return True if the stay option is mechanically available in this situation.
    Does NOT check whether the batter would choose stay — just eligibility.
    """
    return state.runners_on_base


def stay_results_in_out(state: GameState, caught_fly: bool = False) -> bool:
    """
    Return True if choosing stay on this contact would result in the batter
    being retired (i.e., the stay produces an out on the batter).

    This happens in two cases (§2.6):
      1. Two-strike count at time of contact.
      2. Fly ball caught in the air.
    """
    two_strike = state.count.strikes == 2
    return two_strike or caught_fly


# ---------------------------------------------------------------------------
# Stay-vs-run heuristic (§4.5)
# ---------------------------------------------------------------------------

CONTACT_QUALITY_WEAK = "weak"
CONTACT_QUALITY_MEDIUM = "medium"
CONTACT_QUALITY_HARD = "hard"


def should_stay(
    state: GameState,
    batter: Player,
    contact_quality: str,
    caught_fly: bool = False,
    is_hr: bool = False,
    is_triple: bool = False,
) -> bool:
    """
    Decide whether the batter should stay or run.

    Phase 1 returns a deterministic rule-based decision.
    Phase 2 will weight this with random draws against batter.stay_aggressiveness.

    Args:
        state:          Current GameState.
        batter:         The current batter Player.
        contact_quality: "weak" | "medium" | "hard".
        caught_fly:     True if the fielder caught the ball in the air.
        is_hr:          True if the ball is clearly a home run.
        is_triple:      True if the ball reaches the outfield gap (likely triple).

    Returns:
        True  → batter stays at home plate.
        False → batter becomes a batter-runner.
    """
    # Stay never available on empty bases.
    if not state.runners_on_base:
        return False

    # Home runs: stay does not apply (§2.6).
    if is_hr:
        return False

    # Triples: reaching 3B is too valuable to forfeit (§4.5).
    if is_triple:
        return False

    # Hard contact: run (§4.5).
    if contact_quality == CONTACT_QUALITY_HARD:
        return False

    # 2 outs: run (§4.5) — staying with 2 outs is risky and unproductive.
    if state.outs == 2:
        return False

    # Two-strike contact: batter is out on stay; still "chooses" stay per rule —
    # but heuristic should not deliberately self-out. Return False.
    if state.count.strikes == 2:
        return False

    # Caught fly: batter is out on stay; avoid choosing it.
    if caught_fly:
        return False

    # Weak or medium contact, runners on base, < 2 outs, not 2-strike: stay.
    if contact_quality in (CONTACT_QUALITY_WEAK, CONTACT_QUALITY_MEDIUM):
        return True

    return False


# ---------------------------------------------------------------------------
# Credit a stay-hit
# ---------------------------------------------------------------------------

def credit_stay_hit(state: GameState) -> None:
    """
    Award one hit credit to the current batter and increment the multi-hit
    at-bat counter.  Called whenever a stay results in runner advancement
    without the batter being retired.
    """
    state.current_at_bat_hits += 1
