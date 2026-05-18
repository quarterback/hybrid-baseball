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


# Hard ceiling on super-inning rounds. Without this, two evenly-matched
# lineups that keep producing identical 5-dismissal totals lock the engine
# into an unbounded while-loop inside simulate_game() — the bulk-sim
# per-game deadline only fires between games, so a hung game silently
# eats every chunk and the day's clock never advances. After this many
# tied SI rounds we force a winner from the running score's run pattern
# (deterministic, seeded off game state) and end the game.
SI_MAX_ROUNDS = 8


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

    # === PRE-GAME: home manager picks bat-first / bat-second ===
    if state.home_bats_first is None:
        state.home_bats_first = bool(mgr.should_bat_first(state))
    if state.home_bats_first:
        state.first_batting_team  = state.home
        state.second_batting_team = state.visitors
        first_half_label, second_half_label = "bottom", "top"
    else:
        state.first_batting_team  = state.visitors
        state.second_batting_team = state.home
        first_half_label, second_half_label = "top", "bottom"

    # === FIRST HALF (whichever team bats first) ===
    state.half = first_half_label
    state.total_pa_this_half = 0
    state.batting_team.reset_half()
    _set_fielding_pitcher(state)
    full_log.append(_half_header(state, renderer))
    half_log = run_half(state, event_provider, renderer)
    full_log += half_log
    _close_current_spell(state)
    _finalize_declaration(state.first_batting_team, state)
    full_log += _half_summary(state, first_half_label, renderer)

    # === HALFTIME ===
    ht_log = halftime(state, renderer)
    full_log += ht_log

    # === SECOND HALF ===
    state.half = second_half_label
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
    _finalize_declaration(state.second_batting_team, state)
    full_log += _half_summary(state, second_half_label, renderer)

    # Task #58: snapshot end of regulation (phase 0) so per-phase batter
    # rows can be computed after the game finishes.
    if renderer:
        renderer.end_phase(0)

    # === WINNER CHECK ===
    winner = check_winner(state)
    if winner is not None:
        # === DECLARED SECONDS (if the loser has banked outs) ===
        seconds_log = _run_seconds_rounds(state, event_provider, renderer)
        full_log += seconds_log
        # Re-check the winner after any seconds round(s). A seconds round
        # can return the score to a tie, in which case we fall through
        # to the super-inning tiebreaker just like a regulation tie.
        winner = check_winner(state)

    if winner is not None:
        state.winner = winner
        if renderer is not None:
            _reconcile_batter_runs(state, renderer)
        full_log += _game_over(state, renderer)
        if renderer:
            full_log += renderer.render_box_score(state)
            full_log += renderer.render_partnership_log(state)
            full_log += renderer.render_spell_log(state)
            if state.super_inning_number > 0:
                full_log += renderer.render_super_inning_log(state)
        return state, full_log

    # === SUPER-INNING (tie, possibly after seconds) ===
    if renderer:
        full_log += renderer.render_super_inning_tie()
    else:
        full_log.append("\n=== TIE — SUPER-INNING TIEBREAKER ===")

    si_rounds_played = 0
    while not state.winner:
        if si_rounds_played >= SI_MAX_ROUNDS:
            # Force a winner so the game terminates. Deterministic from the
            # current state: pick whichever team has more partnership-runs
            # (proxy for offensive performance) across the whole game; if
            # that's also tied, fall back to a hash of team ids so the
            # outcome is stable across re-runs of the same seed.
            v_score = state.score.get("visitors", 0)
            h_score = state.score.get("home", 0)
            if v_score != h_score:
                state.winner = "visitors" if v_score > h_score else "home"
            else:
                v_id = str(getattr(state.visitors, "team_id", "v"))
                h_id = str(getattr(state.home, "team_id", "h"))
                state.winner = "visitors" if hash((v_id, h_id, state.super_inning_number)) & 1 else "home"
            full_log.append(
                f"[warn] SI round cap ({SI_MAX_ROUNDS}) hit — forcing winner "
                f"{state.winner} to terminate."
            )
            if renderer is not None:
                _reconcile_batter_runs(state, renderer)
            full_log += _game_over(state, renderer)
            if renderer:
                full_log += renderer.render_box_score(state)
                full_log += renderer.render_partnership_log(state)
                full_log += renderer.render_spell_log(state)
                full_log += renderer.render_super_inning_log(state)
            break
        si_rounds_played += 1
        # If a seconds round already wrote to phase=1 (because the seconds
        # round tied the score and SI fires after), bump SI's phase index
        # past it so SI rounds don't collide on UNIQUE(player, game, phase).
        if state.super_inning_number == 0 and state.seconds_phase_number > 0:
            state.super_inning_number = state.seconds_phase_number
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
        # Task #58: SI half cap = 5. The dismissal-set cap is the real
        # invariant; the outs counter is a softer guard that previously
        # asserted-and-crashed on rare runner-out interactions. Treat an
        # outs overrun as a logged anomaly so calibration runs don't die.
        if state.outs > 5:
            full_log.append(
                f"[warn] SI super_top outs overrun for visitors round "
                f"{state.super_inning_number}: outs={state.outs}"
            )
        assert len(state.visitors.super_dismissed) <= 5, (
            f"SI dismissal cap exceeded for visitors round "
            f"{state.super_inning_number}: {len(state.visitors.super_dismissed)}"
        )

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
        # Task #58: SI half cap = 5 for the home half too. See note above —
        # outs overrun is downgraded from assertion to logged anomaly.
        if state.outs > 5:
            full_log.append(
                f"[warn] SI super_bottom outs overrun for home round "
                f"{state.super_inning_number}: outs={state.outs}"
            )
        assert len(state.home.super_dismissed) <= 5, (
            f"SI dismissal cap exceeded for home round "
            f"{state.super_inning_number}: {len(state.home.super_dismissed)}"
        )

        # Snapshot end of this SI round (phase = super_inning_number).
        if renderer:
            renderer.end_phase(state.super_inning_number)

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
            if renderer is not None:
                _reconcile_batter_runs(state, renderer)
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
        # Use the unified phase number so seconds-round spells get phase>=1
        # instead of phase=0. SI and seconds are mutually exclusive within
        # one game, so this stays consistent with the existing SI-round
        # behavior (super_inning_number was already the phase index there).
        super_inning_number=getattr(state, "phase_number", 0) or state.super_inning_number,
        sb_allowed=state.pitcher_sb_allowed_this_spell,
        cs_caught=state.pitcher_cs_caught_this_spell,
        fo_induced=state.pitcher_fo_induced_this_spell,
        er_arc=list(state.pitcher_er_arc_this_spell),
        k_arc=list(state.pitcher_k_arc_this_spell),
        fo_arc=list(state.pitcher_fo_arc_this_spell),
        bf_arc=list(state.pitcher_bf_arc_this_spell),
    )
    state.spell_log.append(spell)
    state.pitcher_spell_count = 0
    state.pitcher_outs_this_spell = 0
    state.pitcher_runs_this_spell = 0
    state.pitcher_unearned_runs_this_spell = 0
    state.pitcher_h_this_spell = 0
    state.pitcher_bb_this_spell = 0
    state.pitcher_k_this_spell = 0
    state.pitcher_sb_allowed_this_spell = 0
    state.pitcher_cs_caught_this_spell = 0
    state.pitcher_fo_induced_this_spell = 0
    state.pitcher_errors_this_spell = 0
    state.pitcher_hbp_this_spell = 0
    state.pitcher_hr_this_spell = 0
    state.pitcher_pitches_this_spell = 0
    state.pitcher_er_arc_this_spell = [0, 0, 0]
    state.pitcher_k_arc_this_spell  = [0, 0, 0]
    state.pitcher_fo_arc_this_spell = [0, 0, 0]
    state.pitcher_bf_arc_this_spell = [0, 0, 0]


def _finalize_declaration(team: Team, state: GameState) -> None:
    """After a half ends, record what the team banked.

    If the team declared mid-half, evaluate_declaration already stamped
    declared_at_out and outs_banked. If they played the full half (no
    declaration), banked stays 0. This helper exists so any future
    bookkeeping (analytics hooks, log lines) has a single anchor point.
    """
    if team.declared_at_out is None:
        team.outs_banked = 0
        return
    # The actual outs banked = 27 - declared_at_out (already set by
    # evaluate_declaration as min(outs_left, cap), which equals 27 - declared_at_out
    # since state.outs == declared_at_out at the moment of stamping).
    banked = max(0, min(int(__import__("o27").config.SECONDS_MAX_BANKED),
                         27 - int(team.declared_at_out)))
    team.outs_banked = banked


def _run_seconds_rounds(state, event_provider, renderer) -> list[str]:
    """Drive the post-regulation declared-seconds loop.

    Rule: a team's banked outs are used only if the OTHER team has more
    runs (i.e. this team is trailing). Tied or leading teams leave their
    banked outs unused — the insurance simply wasn't needed. Each team
    can come back at most once.

    If the comeback flips the lead, the now-trailing team gets a turn
    (subject to the same rule). If the score stays tied or stays decided
    in the same direction, the loop exits and SI fires (for ties) or the
    game ends (for a confirmed winner).
    """
    full_log: list[str] = []
    rounds_played = 0
    while rounds_played < 2:
        winner = check_winner(state)
        if winner is None:
            # Tied at this point — neither team is trailing, so neither
            # uses banked outs. Loop exits and the engine falls through
            # to SI if appropriate.
            break
        winner_team = (state.first_batting_team
                       if winner == state.first_batting_team.team_id
                       else state.second_batting_team)
        trailer_team = (state.second_batting_team
                        if winner_team is state.first_batting_team
                        else state.first_batting_team)
        if int(trailer_team.outs_banked or 0) <= 0 or trailer_team.seconds_used:
            break

        # Setup the seconds half.
        state.in_seconds_phase = True
        state.seconds_phase_number += 1   # 1 for first round, 2 if a second fires
        state.half = "seconds_first" if trailer_team is state.first_batting_team else "seconds_second"
        state.outs = 0
        state.bases = [None, None, None]
        state.count.reset()
        state.total_pa_this_half = 0
        state.partnership_runs = 0
        state.partnership_first_batter_id = None
        # Lineup position is NOT reset — picks up where regulation left off.
        # Pitcher stays on; fatigue intact. The fielding team's pitcher is
        # whoever was on the mound, but we still need current_pitcher_id
        # to point at the FIELDING team's pitcher (which has flipped if
        # the team that took the lead is now defending). Set it.
        _set_fielding_pitcher(state)

        full_log.append(_half_header(state, renderer))
        half_log = run_half(state, event_provider, renderer)
        full_log += half_log
        _close_current_spell(state)

        trailer_team.seconds_used = True
        trailer_team.seconds_outs_used = state.outs

        # Snapshot end of this seconds round. Each round gets its own phase
        # number so a double-seconds (both teams come back) doesn't collide
        # on UNIQUE(player_id, game_id, phase). Round 1 → phase 1, round 2 → phase 2.
        if renderer:
            renderer.end_phase(state.seconds_phase_number)

        rounds_played += 1
        # Loop: if the comeback flipped the lead AND the opposing team has
        # banked outs AND hasn't used seconds, they get THEIR seconds round.

    # Done with seconds. Reset the phase flag so any downstream readers
    # see "game over, not in seconds."
    state.in_seconds_phase = False
    return full_log


def _reconcile_batter_runs(state: GameState, renderer) -> None:
    """End-of-game safety net: force Σ(batter.runs) per team to equal
    state.score, the engine's authoritative score.

    The per-batter run credit (`Renderer._credit_runs`) is best-effort and
    occasionally drifts in edge cases (lineup wraparound onto a base,
    same player on two bases via batter-displacement, runner subs whose
    BatterStats entry isn't in the renderer dict, etc.). Rather than
    hunting every path that can drop or duplicate a run credit, we just
    reconcile at game end: if a team's batter-runs sum is off from
    state.score, credit or debit a plausible player to match.

    Credit fallback: the player on this team with the most PAs.
    Debit fallback: the player on this team with the most runs already
    credited (so we never produce negative-runs rows).

    This also updates `renderer._phase_end_snapshots` so per-phase row
    extraction sees the corrected values. The adjustment is applied to
    the FINAL phase the team played in (where the drift most likely
    materialized) so per-phase splits stay self-consistent.
    """
    for team in (state.visitors, state.home):
        team_roster_ids = {p.player_id for p in team.roster}
        target = int(state.score.get(team.team_id, 0) or 0)
        # Sum batter.runs for this team across all stat entries.
        team_runs = sum(
            s.runs for pid, s in renderer._batter_stats.items()
            if pid in team_roster_ids
        )
        diff = target - team_runs
        if diff == 0:
            continue

        # Pick the adjustment target.
        if diff > 0:
            # Need to credit `diff` more runs. Pick the player with the
            # most PAs on this team — they're most likely to have scored
            # in the close-margin run we're missing.
            candidates = [
                (s.pa, pid, s) for pid, s in renderer._batter_stats.items()
                if pid in team_roster_ids
            ]
        else:
            # Need to debit |diff| runs. Pick the player with the most
            # already-credited runs so we don't go negative.
            candidates = [
                (s.runs, pid, s) for pid, s in renderer._batter_stats.items()
                if pid in team_roster_ids and s.runs > 0
            ]
        if not candidates:
            continue
        candidates.sort(key=lambda c: -c[0])
        _, _, target_stats = candidates[0]
        target_stats.runs = max(0, target_stats.runs + diff)

        # Mirror the adjustment into the LAST phase snapshot so per-phase
        # row extraction picks it up. Find the highest phase this player
        # appears in (= the most recent phase where their stats moved).
        snapshots = renderer._phase_end_snapshots
        if snapshots:
            last_phase = max(snapshots.keys())
            snap = snapshots.get(last_phase, {})
            if target_stats.player_id in snap:
                snap[target_stats.player_id].runs = max(
                    0, snap[target_stats.player_id].runs + diff
                )


def _set_fielding_pitcher(state: GameState) -> None:
    """Point current_pitcher_id at the fielding team's pitcher and reset counters."""
    fielding = state.fielding_team
    restricted: set = set()  # Jokers removed in Task #47 — no fielding restrictions.

    def _assign(player: Player) -> None:
        state.current_pitcher_id = player.player_id
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
        state.pa_start_outs = state.outs
        state.pitcher_start_pa = state.total_pa_this_half

    # Phase 10: pick a true starter (pitcher_role=="starter"/"workhorse")
    # before any other pitcher; never fall back to a position player unless
    # the roster has zero pitchers.
    for role in ("starter", "workhorse"):
        for player in fielding.roster:
            if (player.is_pitcher
                    and getattr(player, "pitcher_role", "") == role
                    and player.player_id not in restricted):
                _assign(player)
                return
    for player in fielding.roster:
        if player.is_pitcher and player.player_id not in restricted:
            _assign(player)
            return
    # Emergency fallback only — should not happen with Phase 10 league setup.
    for player in fielding.roster:
        if player.player_id not in restricted:
            _assign(player)
            return
    if fielding.roster:
        _assign(fielding.roster[0])


def _half_header(state: GameState, renderer=None) -> str:
    if renderer:
        return renderer.render_half_header(state)
    bat = state.batting_team
    banked = int(getattr(bat, "outs_banked", 0) or 0)
    half_labels = {
        "top": "TOP HALF",
        "bottom": "BOTTOM HALF",
        "super_top": f"SUPER-INNING R{state.super_inning_number} — VISITORS",
        "super_bottom": f"SUPER-INNING R{state.super_inning_number} — HOME",
        "seconds_first":  f"SECONDS — {bat.name} (banked {banked} outs)",
        "seconds_second": f"SECONDS — {bat.name} (banked {banked} outs)",
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
    if state.super_inning_number > 0:
        suffix = " (super-inning)"
    elif (state.visitors.seconds_used or state.home.seconds_used):
        suffix = " (declared seconds)"
    elif (state.visitors.declared_at_out is not None
          or state.home.declared_at_out is not None):
        suffix = " (declared)"
    else:
        suffix = ""
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
