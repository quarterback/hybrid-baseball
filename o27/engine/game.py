"""
Top-level O27 game loop.

run_game() accepts a GameState (already populated with teams and lineups)
plus a sequence-provider callable and executes the full game:

    top half → halftime → bottom half → winner check → (super-inning if tied)

The sequence_provider is a callable:
    sequence_provider(state: GameState) -> dict | None

It returns the next event dict for apply_event(), or None to signal that
the current plate appearance is over (shouldn't happen — the PA resolver
drives its own termination).

For Phase 1 (scripted tests), a simple iterator-based provider is supplied.
For Phase 2, the provider will call probability models.

Public API
----------
  run_game(state, event_provider) -> GameState
  run_half(state, event_provider) -> list[str]      # drives one half-inning
  halftime(state) -> list[str]
  check_winner(state) -> str | None
  setup_super_inning(state, visitors_5, home_5) -> list[str]
"""

from __future__ import annotations
from engine.state import (
    GameState, Team, Player, SpellRecord, SuperInningRound
)
from engine.pa import apply_event
from engine import manager as mgr
from typing import Callable, Iterator, Optional


# ---------------------------------------------------------------------------
# Game entry point
# ---------------------------------------------------------------------------

def run_game(
    state: GameState,
    event_provider: Callable[[GameState], Optional[dict]],
    super_selector: Optional[Callable[[GameState, str], list]] = None,
) -> tuple[GameState, list[str]]:
    """
    Run a complete O27 game.

    Args:
        state:           Initialized GameState with both teams' lineups set.
        event_provider:  Callable(state) → event dict, or None to end PA.
                         The provider is called repeatedly during a PA until
                         the PA resolver signals completion (via count or out).
        super_selector:  Optional callable(state, team_id) → list[Player] of 5
                         batters for super-inning. If None, first 5 batters used.

    Returns:
        (final_state, full_log)
    """
    full_log: list[str] = []

    # === TOP HALF ===
    state.half = "top"
    state.batting_team.reset_half()
    full_log.append(_half_header(state))
    half_log = run_half(state, event_provider)
    full_log += half_log
    full_log += _half_summary(state, "top")

    # === HALFTIME ===
    ht_log = halftime(state)
    full_log += ht_log

    # === BOTTOM HALF ===
    state.half = "bottom"
    state.outs = 0
    state.bases = [None, None, None]
    state.count.reset()
    state.batting_team.reset_half()
    state.partnership_runs = 0
    state.partnership_first_batter_id = None
    full_log.append(_half_header(state))
    half_log = run_half(state, event_provider)
    full_log += half_log
    full_log += _half_summary(state, "bottom")

    # === WINNER CHECK ===
    winner = check_winner(state)
    if winner:
        state.winner = winner
        full_log.append(f"\n=== GAME OVER: {winner.upper()} WIN "
                        f"{state.score[winner]}–{state.score[_other(winner)]} ===")
        return state, full_log

    # === SUPER-INNING (tie) ===
    full_log.append("\n=== TIE — SUPER-INNING TIEBREAKER ===")
    while not state.winner:
        state.super_inning_number += 1
        full_log.append(f"\n--- Super-Inning Round {state.super_inning_number} ---")

        # Select 5 batters for each team.
        if super_selector:
            v5 = super_selector(state, "visitors")
            h5 = super_selector(state, "home")
        else:
            v5 = state.visitors.roster[:5]
            h5 = state.home.roster[:5]

        si_log = setup_super_inning(state, v5, h5)
        full_log += si_log

        # Visitors bat (super_top).
        state.half = "super_top"
        state.outs = 0
        state.bases = [None, None, None]
        state.count.reset()
        state.visitors.reset_super()
        state.visitors.super_lineup = v5
        state.visitors.super_lineup_position = 0
        super_score_before_v = state.score["visitors"]
        full_log.append(_half_header(state))
        full_log += run_half(state, event_provider)

        # Home bats (super_bottom).
        state.half = "super_bottom"
        state.outs = 0
        state.bases = [None, None, None]
        state.count.reset()
        state.home.reset_super()
        state.home.super_lineup = h5
        state.home.super_lineup_position = 0
        super_score_before_h = state.score["home"]
        full_log.append(_half_header(state))
        full_log += run_half(state, event_provider)

        # Record round.
        v_runs = state.score["visitors"] - super_score_before_v
        h_runs = state.score["home"] - super_score_before_h
        round_rec = SuperInningRound(
            team_name=state.visitors.name,
            selected_batter_ids=[p.player_id for p in v5],
            runs=v_runs,
            dismissals=len(state.visitors.super_dismissed),
        )
        state.super_inning_rounds.append(round_rec)
        round_rec2 = SuperInningRound(
            team_name=state.home.name,
            selected_batter_ids=[p.player_id for p in h5],
            runs=h_runs,
            dismissals=len(state.home.super_dismissed),
        )
        state.super_inning_rounds.append(round_rec2)

        full_log.append(f"  Super-inning R{state.super_inning_number}: "
                        f"{state.visitors.name} {v_runs} – "
                        f"{state.home.name} {h_runs}")

        winner = check_winner(state)
        if winner:
            state.winner = winner
            full_log.append(f"\n=== GAME OVER (super-inning): "
                            f"{winner.upper()} WIN ===")

    return state, full_log


# ---------------------------------------------------------------------------
# Half-inning runner
# ---------------------------------------------------------------------------

def run_half(
    state: GameState,
    event_provider: Callable[[GameState], Optional[dict]],
) -> list[str]:
    """
    Drive one half-inning to completion (27 outs in regulation; 5 dismissals
    in super). Returns all log lines produced during the half.
    """
    log: list[str] = []
    while not state.is_half_over():
        event = event_provider(state)
        if event is None:
            break
        log += apply_event(state, event)
    return log


# ---------------------------------------------------------------------------
# Halftime
# ---------------------------------------------------------------------------

def halftime(state: GameState) -> list[str]:
    """
    Transition from top to bottom half.
    Records the target score and computes the required run rate for the home team.
    """
    v_score = state.score["visitors"]
    state.target_score = v_score
    target_runs = v_score + 1    # home must exceed, not just equal

    required_rr = target_runs / 27 if 27 > 0 else 0.0

    log = [
        "",
        "=" * 60,
        "HALFTIME",
        f"  {state.visitors.name}: {v_score} run(s)",
        f"  {state.home.name} need {target_runs} run(s) to win "
        f"(required run rate: {required_rr:.3f} R/out)",
        "=" * 60,
        "",
    ]
    return log


# ---------------------------------------------------------------------------
# Winner determination
# ---------------------------------------------------------------------------

def check_winner(state: GameState) -> Optional[str]:
    """
    Return "visitors" or "home" if the game has a winner, or None if tied.
    Called after the bottom half ends (regulation) or after each super round.
    """
    v = state.score["visitors"]
    h = state.score["home"]
    if v > h:
        return "visitors"
    if h > v:
        return "home"
    return None


# ---------------------------------------------------------------------------
# Super-inning setup
# ---------------------------------------------------------------------------

def setup_super_inning(
    state: GameState,
    visitors_5: list,
    home_5: list,
) -> list[str]:
    """
    Configure the state for a super-inning round.

    Args:
        state:       Current GameState.
        visitors_5:  5 Player objects selected for visitors.
        home_5:      5 Player objects selected for home.
    """
    log = [
        f"  {state.visitors.name} super lineup: "
        f"{', '.join(p.name for p in visitors_5)}",
        f"  {state.home.name} super lineup: "
        f"{', '.join(p.name for p in home_5)}",
    ]
    return log


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------

def _half_header(state: GameState) -> str:
    half_labels = {
        "top": "TOP HALF",
        "bottom": "BOTTOM HALF",
        "super_top": f"SUPER-INNING R{state.super_inning_number} — VISITORS",
        "super_bottom": f"SUPER-INNING R{state.super_inning_number} — HOME",
    }
    label = half_labels.get(state.half, state.half.upper())
    batting = state.batting_team.name
    return f"\n{'─' * 60}\n{label} | {batting} batting\n{'─' * 60}"


def _half_summary(state: GameState, which: str) -> list[str]:
    team = state.visitors if which == "top" else state.home
    runs = state.score[team.team_id]
    rr = runs / 27 if state.outs > 0 else 0.0
    return [
        "",
        f"End of {'top' if which == 'top' else 'bottom'} half — "
        f"{team.name}: {runs} run(s) | Run rate: {rr:.3f} R/out",
    ]


def _other(team_id: str) -> str:
    return "home" if team_id == "visitors" else "visitors"


# ---------------------------------------------------------------------------
# Convenience: iterator-based event provider (for scripted tests)
# ---------------------------------------------------------------------------

def make_script_provider(events: list) -> Callable[[GameState], Optional[dict]]:
    """
    Create an event_provider from a pre-scripted list of event dicts.
    Returns events in order; raises StopIteration (→ None) when exhausted.
    """
    it: Iterator[dict] = iter(events)

    def provider(state: GameState) -> Optional[dict]:
        try:
            return next(it)
        except StopIteration:
            return None

    return provider
