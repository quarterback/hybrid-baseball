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
For Phase 2+, the provider calls probability models.

An optional Renderer (Phase 3+) may be passed to run_game(). When present:
  - All output is rendered via Jinja2 templates instead of raw log strings.
  - Batter stats are accumulated for the final box score.
  - The box score, partnership log, spell log, and super-inning log are
    appended at the end of the transcript.

Without a Renderer (existing test paths), raw log strings are returned
unchanged — no behavior difference for test_rules.py.

Public API
----------
  run_game(state, event_provider, renderer=None) -> (GameState, list[str])
  run_half(state, event_provider, renderer=None) -> list[str]
  halftime(state, renderer=None) -> list[str]
  check_winner(state) -> str | None
  setup_super_inning(state, visitors_5, home_5) -> list[str]
"""

from __future__ import annotations
from .state import (
    GameState, Team, Player, SpellRecord, SuperInningRound
)
from .pa import apply_event
from . import manager as mgr
from typing import Callable, Iterator, Optional


# ---------------------------------------------------------------------------
# Game entry point
# ---------------------------------------------------------------------------

def run_game(
    state: GameState,
    event_provider: Callable[[GameState], Optional[dict]],
    renderer=None,
    super_selector: Optional[Callable[[GameState, str], list]] = None,
) -> tuple[GameState, list[str]]:
    """
    Run a complete O27 game.

    Args:
        state:           Initialized GameState with both teams' lineups set.
        event_provider:  Callable(state) → event dict, or None to end PA.
        renderer:        Optional Renderer instance (Phase 3+). When provided,
                         all output uses Jinja2 templates and a box score is
                         appended at the end.
        super_selector:  Optional callable(state, team_id) → list[Player] of 5
                         batters for super-inning. If None, first 5 batters used.

    Returns:
        (final_state, full_log)
    """
    full_log: list[str] = []

    # === TOP HALF ===
    state.half = "top"
    state.total_pa_this_half = 0
    state.batting_team.reset_half()
    _set_fielding_pitcher(state)
    full_log.append(_half_header(state, renderer))
    half_log = run_half(state, event_provider, renderer)
    full_log += half_log
    _close_current_spell(state)
    full_log += _half_summary(state, "top", renderer)

    # === HALFTIME ===
    ht_log = halftime(state, renderer)
    full_log += ht_log

    # === BOTTOM HALF ===
    state.half = "bottom"
    state.outs = 0
    state.bases = [None, None, None]
    state.count.reset()
    state.total_pa_this_half = 0
    state.batting_team.reset_half()
    state.partnership_runs = 0
    state.partnership_first_batter_id = None
    _set_fielding_pitcher(state)
    full_log.append(_half_header(state, renderer))
    half_log = run_half(state, event_provider, renderer)
    full_log += half_log
    _close_current_spell(state)
    full_log += _half_summary(state, "bottom", renderer)

    # === WINNER CHECK ===
    winner = check_winner(state)
    if winner:
        state.winner = winner
        full_log += _game_over(state, renderer)
        if renderer:
            full_log += renderer.render_box_score(state)
            full_log += renderer.render_partnership_log(state)
            full_log += renderer.render_spell_log(state)
            if state.super_inning_number > 0:
                full_log += renderer.render_super_inning_log(state)
        return state, full_log

    # === SUPER-INNING (tie) ===
    if renderer:
        full_log += renderer.render_super_inning_tie()
    else:
        full_log.append("\n=== TIE — SUPER-INNING TIEBREAKER ===")

    while not state.winner:
        state.super_inning_number += 1

        if super_selector:
            v5 = super_selector(state, "visitors")
            h5 = super_selector(state, "home")
        else:
            v5 = state.visitors.roster[:5]
            h5 = state.home.roster[:5]

        si_log = setup_super_inning(state, v5, h5, renderer)
        full_log += si_log

        # Visitors bat (super_top).
        state.half = "super_top"
        state.outs = 0
        state.bases = [None, None, None]
        state.count.reset()
        state.total_pa_this_half = 0
        state.visitors.reset_super()
        state.visitors.super_lineup = v5
        state.visitors.super_lineup_position = 0
        state.partnership_runs = 0
        state.partnership_first_batter_id = None
        _set_fielding_pitcher(state)
        super_score_before_v = state.score["visitors"]
        v5_ids = [p.player_id for p in v5]
        v_snap = renderer.snapshot_batter_stats(v5_ids) if renderer else {}
        full_log.append(_half_header(state, renderer))
        full_log += run_half(state, event_provider, renderer)
        _close_current_spell(state)
        v_outcomes = renderer.batter_outcomes_since(v5, v_snap) if renderer else []

        # Home bats (super_bottom).
        state.half = "super_bottom"
        state.outs = 0
        state.bases = [None, None, None]
        state.count.reset()
        state.total_pa_this_half = 0
        state.home.reset_super()
        state.home.super_lineup = h5
        state.home.super_lineup_position = 0
        state.partnership_runs = 0
        state.partnership_first_batter_id = None
        _set_fielding_pitcher(state)
        super_score_before_h = state.score["home"]
        h5_ids = [p.player_id for p in h5]
        h_snap = renderer.snapshot_batter_stats(h5_ids) if renderer else {}
        full_log.append(_half_header(state, renderer))
        full_log += run_half(state, event_provider, renderer)
        _close_current_spell(state)
        h_outcomes = renderer.batter_outcomes_since(h5, h_snap) if renderer else []

        # Record round (with batter names + per-batter outcomes for end-of-game log).
        v_runs = state.score["visitors"] - super_score_before_v
        h_runs = state.score["home"] - super_score_before_h
        round_rec = SuperInningRound(
            team_name=state.visitors.name,
            selected_batter_ids=v5_ids,
            selected_batter_names=[p.name for p in v5],
            runs=v_runs,
            dismissals=len(state.visitors.super_dismissed),
            batter_outcomes=v_outcomes,
        )
        state.super_inning_rounds.append(round_rec)
        round_rec2 = SuperInningRound(
            team_name=state.home.name,
            selected_batter_ids=h5_ids,
            selected_batter_names=[p.name for p in h5],
            runs=h_runs,
            dismissals=len(state.home.super_dismissed),
            batter_outcomes=h_outcomes,
        )
        state.super_inning_rounds.append(round_rec2)

        if renderer:
            full_log += renderer.render_super_inning_round_summary(
                state, state.super_inning_number, v_runs, h_runs
            )
        else:
            full_log.append(
                f"  Super-inning R{state.super_inning_number}: "
                f"{state.visitors.name} {v_runs} – {state.home.name} {h_runs}"
            )

        winner = check_winner(state)
        if winner:
            state.winner = winner
            full_log += _game_over(state, renderer)
            if renderer:
                full_log += renderer.render_box_score(state)
                full_log += renderer.render_partnership_log(state)
                full_log += renderer.render_spell_log(state)
                full_log += renderer.render_super_inning_log(state)

    return state, full_log


# ---------------------------------------------------------------------------
# Half-inning runner
# ---------------------------------------------------------------------------

def run_half(
    state: GameState,
    event_provider: Callable[[GameState], Optional[dict]],
    renderer=None,
) -> list[str]:
    """
    Drive one half-inning to completion (27 outs in regulation; 5 dismissals
    in super). Returns all log lines produced during the half.

    When a Renderer is provided, events are rendered via Jinja2 templates.
    Without a Renderer, the existing raw apply_event() log lines are returned
    (backwards-compatible — tests continue to work unchanged).
    """
    log: list[str] = []
    while not state.is_half_over():
        if renderer:
            ctx = renderer.capture_context(state)
        event = event_provider(state)
        if event is None:
            break
        raw_log = apply_event(state, event)
        if renderer:
            log += renderer.render_event(event, ctx, state)
        else:
            log += raw_log
    return log


# ---------------------------------------------------------------------------
# Halftime
# ---------------------------------------------------------------------------

def halftime(state: GameState, renderer=None) -> list[str]:
    """
    Transition from top to bottom half.
    Records the target score and computes the required run rate for home.
    """
    v_score = state.score["visitors"]
    state.target_score = v_score
    target_runs = v_score + 1

    if renderer:
        return renderer.render_halftime(state)

    log = [
        "",
        "=" * 60,
        "HALFTIME",
        f"  {state.visitors.name}: {v_score} run(s)",
        f"  {state.home.name} need {target_runs} run(s) to win",
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
    renderer=None,
) -> list[str]:
    """Configure the state for a super-inning round."""
    if renderer:
        return renderer.render_super_inning_round_header(
            state, state.super_inning_number, visitors_5, home_5
        )
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

def _close_current_spell(state: GameState) -> None:
    """
    Close the current pitcher's spell and record it to state.spell_log.
    Called at the end of each half (regulation and super) to capture the
    final pitcher's stats even when no explicit pitching change was made.
    """
    if state.current_pitcher_id is None:
        return
    # Try fielding_team first; at end of half the half-state hasn't changed yet
    pitcher = (state.fielding_team.get_player(state.current_pitcher_id)
               or state.visitors.get_player(state.current_pitcher_id)
               or state.home.get_player(state.current_pitcher_id))
    if pitcher is None or state.pitcher_spell_count == 0:
        return
    spell = SpellRecord(
        pitcher_id=pitcher.player_id,
        pitcher_name=pitcher.name,
        batters_faced=state.pitcher_spell_count,
        outs_recorded=state.pitcher_outs_this_spell,
        runs_allowed=state.pitcher_runs_this_spell,
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
    state.pitcher_spell_count = 0
    state.pitcher_outs_this_spell = 0
    state.pitcher_runs_this_spell = 0
    state.pitcher_h_this_spell = 0
    state.pitcher_bb_this_spell = 0
    state.pitcher_k_this_spell = 0
    state.pitcher_hbp_this_spell = 0
    state.pitcher_hr_this_spell = 0
    state.pitcher_pitches_this_spell = 0


def _set_fielding_pitcher(state: GameState) -> None:
    """Point current_pitcher_id at the fielding team's pitcher and reset counters."""
    fielding = state.fielding_team
    restricted: set = set()  # Jokers removed in Task #47 — no fielding restrictions.

    def _assign(player: Player) -> None:
        state.current_pitcher_id = player.player_id
        state.pitcher_spell_count = 0
        state.pitcher_outs_this_spell = 0
        state.pitcher_runs_this_spell = 0
        state.pitcher_h_this_spell = 0
        state.pitcher_bb_this_spell = 0
        state.pitcher_k_this_spell = 0
        state.pitcher_hbp_this_spell = 0
        state.pitcher_hr_this_spell = 0
        state.pitcher_pitches_this_spell = 0
        state.pitcher_start_pa = state.total_pa_this_half

    for player in fielding.roster:
        if player.is_pitcher and player.player_id not in restricted:
            _assign(player)
            return
    for player in fielding.roster:
        if player.player_id not in restricted:
            _assign(player)
            return
    if fielding.roster:
        _assign(fielding.roster[0])


def _half_header(state: GameState, renderer=None) -> str:
    if renderer:
        return renderer.render_half_header(state)
    half_labels = {
        "top": "TOP HALF",
        "bottom": "BOTTOM HALF",
        "super_top": f"SUPER-INNING R{state.super_inning_number} — VISITORS",
        "super_bottom": f"SUPER-INNING R{state.super_inning_number} — HOME",
    }
    label = half_labels.get(state.half, state.half.upper())
    batting = state.batting_team.name
    return f"\n{'─' * 60}\n{label} | {batting} batting\n{'─' * 60}"


def _half_summary(state: GameState, which: str, renderer=None) -> list[str]:
    if renderer:
        return renderer.render_half_summary(state, which)
    team = state.visitors if which == "top" else state.home
    runs = state.score[team.team_id]
    rr = runs / 27 if state.outs > 0 else 0.0
    return [
        "",
        f"End of {'top' if which == 'top' else 'bottom'} half — "
        f"{team.name}: {runs} run(s) | Run rate: {rr:.3f} R/out",
    ]


def _game_over(state: GameState, renderer=None) -> list[str]:
    if renderer:
        return renderer.render_game_over(state)
    winner = state.winner
    other = "home" if winner == "visitors" else "visitors"
    suffix = " (super-inning)" if state.super_inning_number > 0 else ""
    return [
        f"\n=== GAME OVER{suffix}: {winner.upper()} WIN "
        f"{state.score[winner]}–{state.score[other]} ==="
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
