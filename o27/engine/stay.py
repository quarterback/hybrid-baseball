"""
Stay-vs-run decision logic for O27.

This module enforces all §2.5–§2.6 constraints and houses the stay-vs-run
heuristic (Phase 1: rule-based; Phase 2: probabilistic with tunable params).

Key rules (2C-through-the-hitting-engine rework — see
docs/design-2c-hitting-engine-rework.md):
- Stay is only available when at least one runner is on base AND the batter has
  a strike to spare (strikes < 2). Each stay burns a strike, so an AB allows at
  most 3 batted balls: stay on balls 1 and 2, the 3rd (at 2 strikes) forces a
  run-or-out decision.
- The 2C resolves through the real hitting engine. If the resolved contact is an
  OUT in the field (caught fly / ground / fly / line out) the batter is OUT and
  the AB ends — you can't keep hitting after making an out.
- If it's a HIT, the batter STAYS; runners advance by the REAL hit type (single
  →1 base, double→2, triple→3); the batter is credited the real type.
- Home runs: stay does not apply — batter must run (Walk-Back to 3B).
- The stay-vs-run decision is EV-driven (advance/score runners vs. out risk;
  never waste the last out on a non-scoring stay). See prob.should_stay_prob.
- Stay with runner thrown out: that runner is out; at-bat continues.
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

    Available only with a runner on base AND a strike to spare. Each stay burns
    a strike, so at 2 strikes the next batted ball can't be stayed on (it forces
    run-or-out) — this is the max-3-batted-balls cap.
    """
    return state.runners_on_base and state.count.strikes < 2


def stay_results_in_out(state: GameState, outcome: dict) -> bool:
    """
    Return True if choosing to stay on this contact retires the batter.

    The 2C now resolves through the real hitting engine, so the rule is the
    natural one: if the ball was an OUT in the field — caught fly, ground out,
    fly out, line out (i.e. the batter was not safe) — staying can't rescue it;
    the batter is out and the at-bat ends. A clean hit lets the batter stay.
    """
    if outcome.get("caught_fly"):
        return True
    return not outcome.get("batter_safe", True)


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
    # Cap: need a strike to spare (each stay burns one; max 3 batted balls).
    if state.count.strikes >= 2:
        return False
    if is_hr:                       # HR — take the bases (Walk-Back)
        return False
    if is_triple:                   # triple — reaching 3B beats staying
        return False
    if caught_fly:                  # caught — batter is out, stay is moot
        return False
    if contact_quality == CONTACT_QUALITY_HARD:   # likely XBH — take the base
        return False
    # Don't burn the last out on a stay that can't score a run (no runner on 3B).
    if state.outs == 2 and state.bases[2] is None:
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
