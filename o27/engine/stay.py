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
from .state import GameState, Player


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

    Per the corrected stay rule:
      - A stay credits a hit AND uses one strike from the 3-strike budget.
      - The count carries forward; the AB ends when strikes reach 3, but
        ending on the strike count is NOT a batter-out — the hit is still
        credited, the batter just goes back to the dugout. No team out.
      - The ONLY thing that makes a stay produce a batter-out is a caught
        fly: the ball was caught in the air, the batter was out on contact,
        the stay decision is moot.
    """
    return caught_fly


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
    Deterministic stay-vs-run decision (Phase 1 rule-based; also used in tests).

    For probabilistic Phase 2 decisions use engine.prob.should_stay_prob().

    Returns True → batter stays at home plate.
    """
    if not state.runners_on_base:
        return False
    if is_hr:
        return False
    if is_triple:
        return False
    if contact_quality == CONTACT_QUALITY_HARD:
        return False
    if state.outs == 2:
        return False
    if state.count.strikes == 2:
        return False
    if caught_fly:
        return False
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
