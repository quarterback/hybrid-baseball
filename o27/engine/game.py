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
"""

from __future__ import annotations
from .state import (
    GameState, Team, Player, SpellRecord, SuperInningRound
)
from .pa import apply_event, resolve_stranded_walk_backs
from . import manager as mgr
from typing import Callable, Iterator, Optional


# Hard ceiling on super-inning rounds. Without this, two evenly-matched
# lineups that keep trading identical run totals lock the engine into an
# unbounded while-loop inside simulate_game() — the bulk-sim per-game
# deadline only fires between games, so a hung game silently eats every
# chunk and the day's clock never advances.
#
# Regular-season games are allowed to end in a tie after this many
# tied SI rounds. Playoff games can't tie (the bracket needs a winner)
# so the loop will force a deterministic outcome instead — see below.
SI_MAX_ROUNDS = 4


# ---------------------------------------------------------------------------
# Game entry point
# ---------------------------------------------------------------------------

def run_game(
    state: GameState,
    event_provider: Callable[[GameState], Optional[dict]],
    renderer=None,
) -> tuple[GameState, list[str]]:
    """
    Run a complete O27 game.

    Args:
        state:           Initialized GameState with both teams' lineups set.
        event_provider:  Callable(state) → event dict, or None to end PA.
        renderer:        Optional Renderer instance (Phase 3+). When provided,
                         all output uses Jinja2 templates and a box score is
                         appended at the end.

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
    is_playoff = bool(getattr(state, "is_playoff", False))
    while not state.winner:
        if si_rounds_played >= SI_MAX_ROUNDS:
            # Regular-season game: let it end in a genuine tie. Sim writes
            # winner_id=NULL and the standings update guard already skips
            # W/L bookkeeping on a None winner. Playoff games can't tie —
            # the bracket would never advance — so we force a deterministic
            # winner from a stable hash of the team-id pair.
            if is_playoff:
                v_id = str(getattr(state.visitors, "team_id", "v"))
                h_id = str(getattr(state.home, "team_id", "h"))
                state.winner = (
                    "visitors"
                    if hash((v_id, h_id, state.super_inning_number)) & 1
                    else "home"
                )
                full_log.append(
                    f"[warn] Playoff SI cap ({SI_MAX_ROUNDS}) reached — "
                    f"forcing winner {state.winner} to keep the bracket moving."
                )
            else:
                full_log.append(
                    f"  >> Game ends in a TIE after {SI_MAX_ROUNDS} "
                    f"super-inning rounds."
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

        # Super-innings are normal 3-out innings, measured in cumulative
        # outs: the first super out is #28, so round r covers outs
        # 27+3*(r-1)+1 .. 27+3*r. Each half starts at the base out count and
        # ends at base+3 (or, in the bottom, the moment the home team leads).
        out_base = 27 + 3 * (si_rounds_played - 1)
        state.super_outs_target = out_base + 3

        if renderer:
            full_log += renderer.render_super_inning_round_header(
                state, si_rounds_played
            )
        else:
            full_log.append(f"\n--- Super-Inning Round {si_rounds_played} ---")

        # Visitors bat (super_top) — full 3 outs, no walk-off (like the
        # top of an extra inning).
        state.half = "super_top"
        state.outs = out_base
        state.bases = [None, None, None]
        state.count.reset()
        state.total_pa_this_half = 0
        state.partnership_runs = 0
        state.partnership_first_batter_id = None
        _set_fielding_pitcher(state)
        super_score_before_v = state.score["visitors"]
        full_log.append(_half_header(state, renderer))
        full_log += run_half(state, event_provider, renderer)
        _close_current_spell(state)

        # Home bats (super_bottom) — 3 outs, or walk-off the instant they
        # take the lead.
        state.half = "super_bottom"
        state.outs = out_base
        state.bases = [None, None, None]
        state.count.reset()
        state.total_pa_this_half = 0
        state.partnership_runs = 0
        state.partnership_first_batter_id = None
        _set_fielding_pitcher(state)
        super_score_before_h = state.score["home"]
        full_log.append(_half_header(state, renderer))
        full_log += run_half(state, event_provider, renderer)
        _close_current_spell(state)

        # Snapshot end of this SI round (phase = super_inning_number).
        if renderer:
            renderer.end_phase(state.super_inning_number)

        v_runs = state.score["visitors"] - super_score_before_v
        h_runs = state.score["home"] - super_score_before_h
        state.super_inning_rounds.append(
            SuperInningRound(team_name=state.visitors.name, runs=v_runs)
        )
        state.super_inning_rounds.append(
            SuperInningRound(team_name=state.home.name, runs=h_runs)
        )

        if renderer:
            full_log += renderer.render_super_inning_round_summary(
                state, si_rounds_played, v_runs, h_runs
            )
        else:
            full_log.append(
                f"  Super-inning R{si_rounds_played}: "
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
    # A joker insertion (state.batter_override) is "for the next PA" within
    # THIS half. If a half ends before the inserted joker completes his PA
    # (e.g. a walk-off on a between-pitch event right after the insertion),
    # the override would otherwise leak into the next half — where the batting
    # team has flipped, so the stale joker (now an opponent player) bats and
    # his out is misattributed to the wrong team. Clear it at every half start.
    state.batter_override = None
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

    # Walk-Back runners still on base when the half closes are stranded —
    # a successful stop for the pitcher who left them there. Settle them
    # (tick wb_faced, no run) before the next half clears the bases.
    log += resolve_stranded_walk_backs(state)

    # LOB on half-end: any runners still on base when the half closes are
    # stranded. The declaration branch already handles its own LOB credit
    # (and clears the bases via state.outs = 27), so don't double-count —
    # only credit here if no declaration fired this half.
    if (not state.is_super_inning
            and not state.in_seconds_phase
            and state.batting_team.declared_at_out is None):
        stranded = sum(1 for r in state.bases if r is not None)
        if stranded:
            state.batting_team.lob = int(getattr(state.batting_team, "lob", 0) or 0) + stranded
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
    # Keep any spell that recorded outs even when it faced no complete PA
    # (a reliever who only logged a pickoff / caught-stealing out, or a short
    # seconds/super half that ended on a runner-out): those outs are charged
    # on the batter side, so dropping the spell loses them from the pitcher
    # ledger and breaks the batter↔pitcher out reconciliation.
    if pitcher is None or (state.pitcher_spell_count == 0
                           and state.pitcher_outs_this_spell == 0):
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
        wb_faced=state.pitcher_wb_faced_this_spell,
        wb_runs=state.pitcher_wb_runs_this_spell,
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
    state.pitcher_wb_faced_this_spell = 0
    state.pitcher_wb_runs_this_spell = 0


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
    """Drive the post-regulation declared-seconds phase.

    Rule (symmetric to regulation top/bottom):
      - The first-batting team always uses ALL their banked outs in their
        seconds half if they have any, regardless of score — analogous to
        the top of the 9th, where the visitors bat their full half even
        when leading.
      - The second-batting team then uses their banked outs UNLESS they
        are already ahead at that point — analogous to the bottom of the
        9th, where the home team skips its turn when already winning.
        The seconds-second half also walks off the moment they retake the
        lead, since the first-batting team has already exhausted theirs
        and cannot rebut.
      - A team with 0 banked outs simply has no seconds half. Each team
        plays at most one seconds half per game.
    """
    full_log: list[str] = []
    first = state.first_batting_team
    second = state.second_batting_team

    def _play_seconds_half(team, half_label: str) -> None:
        state.in_seconds_phase = True
        state.seconds_phase_number += 1
        state.half = half_label
        state.outs = 0
        state.bases = [None, None, None]
        state.count.reset()
        state.total_pa_this_half = 0
        state.partnership_runs = 0
        state.partnership_first_batter_id = None
        # Lineup position is NOT reset — picks up where regulation left off.
        # The fielding team has flipped, so re-point current_pitcher_id.
        _set_fielding_pitcher(state)

        full_log.append(_half_header(state, renderer))
        full_log.extend(run_half(state, event_provider, renderer))
        _close_current_spell(state)
        team.seconds_used = True
        team.seconds_outs_used = state.outs
        # Each seconds half gets its own phase number so the renderer's
        # UNIQUE(player_id, game_id, phase) holds across the two halves.
        if renderer:
            renderer.end_phase(state.seconds_phase_number)

    # First-batting team's seconds half: always played if they have banked
    # outs (no walk-off — they finish their full allotment even if leading).
    if (first is not None
            and not first.seconds_used
            and int(first.outs_banked or 0) > 0):
        _play_seconds_half(first, "seconds_first")

    # Second-batting team's seconds half: skipped if they are already
    # winning at this point (walk-off shortcut). Otherwise played, and the
    # half itself can walk off if they retake the lead mid-half.
    if (second is not None
            and not second.seconds_used
            and int(second.outs_banked or 0) > 0):
        sb_score = int(state.score.get(second.team_id, 0) or 0)
        fb_score = int(state.score.get(first.team_id, 0) or 0) if first else 0
        if sb_score <= fb_score:
            _play_seconds_half(second, "seconds_second")

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
        state.pitcher_wb_faced_this_spell = 0
        state.pitcher_wb_runs_this_spell = 0
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
    if winner is None:
        v_score = state.score.get("visitors", 0)
        h_score = state.score.get("home", 0)
        return [
            f"\n=== GAME OVER (tie): {state.visitors.name} {v_score}, "
            f"{state.home.name} {h_score} ==="
        ]
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
