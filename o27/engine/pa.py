"""
Plate appearance (PA) resolver for O27.

apply_event() is the core mutation function. It takes a GameState and a single
event dict, updates the state in place, and returns human-readable log lines.

Event dict format:
  { "type": "<event_type>", ...kwargs }

Supported event types (§4.3):
  Pitch events:
    ball, called_strike, swinging_strike, foul, foul_tip_caught,
    hit_by_pitch, wild_pitch, passed_ball

  Contact events (always paired with a fielding outcome):
    ball_in_play  — requires "choice": "run" | "stay"
                             "outcome": dict from fielding.py

  Baserunning events:
    stolen_base_attempt  — requires "base_idx": 0|1|2
    pickoff_attempt      — requires "base_idx": 0|1|2, "success": bool
    balk

  Manager events (call manager.py helpers instead for full enforcement):
    joker_insertion  — requires "joker": Player, "lineup_position": int
    pinch_hit        — requires "replacement": Player
    pitching_change  — requires "new_pitcher": Player

A plate appearance ends when:
  - Count reaches 4 balls (walk)
  - Count reaches 3 strikes (strikeout)
  - Foul tip caught (out)
  - Hit by pitch (batter awarded 1B)
  - Ball in play with "run" chosen (at-bat ends regardless of outcome)
  - Ball in play with "stay" chosen AND outcome results in batter out
  - Ball in play with "stay" chosen AND batter NOT out → at-bat CONTINUES
    with fresh 0-0 count
"""

from __future__ import annotations
from .state import GameState, Player, PartnershipRecord, SpellRecord
from . import stay as stay_mod
from . import manager as mgr
from .baserunning import advance_runners, wild_pitch_advance
from . import fielding as fld
from typing import Optional


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _record_out(state: GameState, batter_id: str) -> list[str]:
    """
    Record one out. Handles partnership tracking and super-inning dismissals.
    Returns log lines.
    """
    log = []
    state.outs += 1
    state.pitcher_outs_this_spell += 1

    # Super-inning: track dismissed batters.
    if state.is_super_inning:
        state.batting_team.super_dismissed.add(batter_id)
        log.append(f"  [Super-inning dismissal: {batter_id}; "
                   f"{len(state.batting_team.super_dismissed)}/5]")

    # Partnership: close when an out is recorded.
    second_batter_id = batter_id
    if state.partnership_first_batter_id is not None:
        # Look up names from both rosters (runner outs may be from either team).
        b1 = (state.batting_team.get_player(state.partnership_first_batter_id)
              or state.fielding_team.get_player(state.partnership_first_batter_id))
        b2 = (state.batting_team.get_player(second_batter_id)
              or state.fielding_team.get_player(second_batter_id))
        rec = PartnershipRecord(
            batter1_id=state.partnership_first_batter_id,
            batter1_name=b1.name if b1 else state.partnership_first_batter_id,
            batter2_id=second_batter_id,
            batter2_name=b2.name if b2 else second_batter_id,
            runs=state.partnership_runs,
            half=state.half,
            super_inning_number=state.super_inning_number,
        )
        state.partnership_log.append(rec)
        log.append(f"  Partnership ended: {state.partnership_runs} run(s).")
        state.partnership_runs = 0
    state.partnership_first_batter_id = second_batter_id

    return log


def _score_run(state: GameState, n: int = 1) -> list[str]:
    """Add n runs to the batting team's score."""
    team_id = state.batting_team.team_id
    state.score[team_id] += n
    state.partnership_runs += n
    state.pitcher_runs_this_spell += n
    return [f"  Run(s) scored: +{n} → {state.batting_team.name} {state.score[team_id]}"]


def _end_at_bat(state: GameState) -> list[str]:
    """
    Finalize a completed at-bat (not a stay continuation).
    - Records joker usage / fielding restriction before advancing lineup.
    - Resets count.
    - Advances lineup (skipping used jokers in regular halves).
    - Increments pitcher spell count and total PA counter.
    - Resets multi-hit tracker.
    Returns log lines.
    """
    log = []
    hits = state.current_at_bat_hits
    if hits > 1:
        log.append(f"  Multi-hit at-bat: {hits} credited hits.")
    # Jokers removed in Task #47 — no per-half eligibility tracking needed.
    state.count.reset()
    state.current_at_bat_hits = 0
    state.batting_team.advance_lineup()
    state.pitcher_spell_count += 1
    state.total_pa_this_half += 1
    return log


def _fresh_count(state: GameState) -> list[str]:
    """Reset count to 0-0 for a stay continuation."""
    state.count.reset()
    return ["  Stay — fresh 0-0 count."]


# ---------------------------------------------------------------------------
# Main event dispatcher
# ---------------------------------------------------------------------------

def apply_event(state: GameState, event: dict) -> list[str]:
    """
    Apply a single event to the game state (mutates in place).

    Returns a list of human-readable log lines describing what happened.
    Raises ValueError for invalid events or illegal actions.
    """
    etype = event["type"]
    log = [f"[{state.outs} outs | {state.count} | {state.bases_summary()}] "
           f"{etype.upper().replace('_', ' ')}"]
    state.events.append(event)

    # Per-spell pitch counter (Task #32). Only true pitch events count.
    _PITCH_EVENTS = (
        "ball", "called_strike", "swinging_strike",
        "foul", "foul_tip_caught", "ball_in_play", "hit_by_pitch",
    )
    if etype in _PITCH_EVENTS:
        state.pitcher_pitches_this_spell += 1

    # ------------------------------------------------------------------
    # Pitch events
    # ------------------------------------------------------------------

    if etype == "ball":
        state.count.balls += 1
        log.append(f"  Ball {state.count.balls}. Count: {state.count}.")
        if state.count.balls >= 4:
            log += _walk(state)
        return log

    if etype == "called_strike":
        return _strike(state, log, swinging=False)

    if etype == "swinging_strike":
        return _strike(state, log, swinging=True)

    if etype == "foul":
        # Foul cannot increase strike count past 2 (except foul tip caught).
        if state.count.strikes < 2:
            state.count.strikes += 1
        log.append(f"  Foul ball. Count: {state.count}.")
        return log

    if etype == "foul_tip_caught":
        # Foul tip caught = strikeout (§2.4).
        log.append("  Foul tip caught — STRIKEOUT.")
        state.pitcher_k_this_spell += 1
        batter_id = state.current_batter.player_id
        log += _record_out(state, batter_id)
        log += _end_at_bat(state)
        return log

    if etype == "hit_by_pitch":
        batter = state.current_batter
        log.append(f"  HBP — {batter.name} awarded 1B.")
        state.pitcher_hbp_this_spell += 1
        # Push runners if forced.
        state.bases, runs, advance_log = _force_advance_for_walk(state.bases, batter.player_id)
        log += advance_log
        if runs:
            log += _score_run(state, runs)
        log += _end_at_bat(state)
        return log

    if etype == "wild_pitch":
        new_bases, runs = wild_pitch_advance(state.bases)
        state.bases = new_bases
        log.append("  Wild pitch — runners advance.")
        if runs:
            log += _score_run(state, runs)
        return log

    if etype == "passed_ball":
        new_bases, runs = wild_pitch_advance(state.bases)
        state.bases = new_bases
        log.append("  Passed ball — runners advance.")
        if runs:
            # Passed-ball runs are charged to the pitcher's record but flagged
            # unearned (no errors exist in O27, so PB is the lone UER source).
            state.pitcher_unearned_runs_this_spell += runs
            log += _score_run(state, runs)
        return log

    # ------------------------------------------------------------------
    # Ball in play
    # ------------------------------------------------------------------

    if etype == "ball_in_play":
        choice = event.get("choice", "run")   # "run" | "stay"
        outcome = event["outcome"]             # dict from fielding.py
        return _resolve_contact(state, log, choice, outcome)

    # ------------------------------------------------------------------
    # Baserunning events
    # ------------------------------------------------------------------

    if etype == "stolen_base_attempt":
        base_idx = event["base_idx"]
        success = event.get("success", True)    # Phase 1: explicit; Phase 2: probabilistic
        runner_id = state.bases[base_idx]
        if runner_id is None:
            log.append("  No runner to steal.")
            return log
        if success:
            state.bases[base_idx] = None
            if base_idx + 1 <= 2:
                state.bases[base_idx + 1] = runner_id
                log.append(f"  Stolen base — runner advances to "
                            f"{'2B 3B Home'.split()[base_idx]}.")
            else:
                log += _score_run(state)
                log.append("  Steal of home — runner scores!")
        else:
            state.bases[base_idx] = None
            log.append(f"  Runner caught stealing at "
                       f"{'2B 3B Home'.split()[base_idx]}.")
            log += _record_out(state, runner_id)
        return log

    if etype == "pickoff_attempt":
        base_idx = event["base_idx"]
        success = event.get("success", False)   # Phase 1: explicit
        runner_id = state.bases[base_idx]
        if runner_id is None:
            log.append("  Pickoff — no runner there.")
            return log
        if success:
            state.bases[base_idx] = None
            log.append(f"  Pickoff — runner out at "
                       f"{'1B 2B 3B'.split()[base_idx]}!")
            log += _record_out(state, runner_id)
        else:
            log.append(f"  Pickoff attempt — runner safe.")
        return log

    if etype == "balk":
        new_bases, runs = wild_pitch_advance(state.bases)
        state.bases = new_bases
        log.append("  Balk — runners advance one base.")
        if runs:
            log += _score_run(state, runs)
        return log

    # ------------------------------------------------------------------
    # Manager events
    # ------------------------------------------------------------------

    if etype == "pinch_hit":
        replacement = event["replacement"]
        log += mgr.pinch_hit(state, replacement)
        return log

    if etype == "pitching_change":
        new_pitcher = event["new_pitcher"]
        log += mgr.pitching_change(state, new_pitcher)
        return log

    raise ValueError(f"Unknown event type: {etype!r}")


# ---------------------------------------------------------------------------
# Contact resolution
# ---------------------------------------------------------------------------

def _resolve_contact(
    state: GameState,
    log: list,
    choice: str,
    outcome: dict,
) -> list[str]:
    """Resolve a ball_in_play event (run_chosen or stay_chosen)."""
    batter = state.current_batter
    batter_id = batter.player_id
    caught_fly = outcome.get("caught_fly", False)
    batter_safe = outcome.get("batter_safe", True)
    hit_type = outcome.get("hit_type", "")

    # PRD §2.6: stay does not apply to home runs — batter must run.
    # fielding.py emits hit_type "hr" for home runs.
    if hit_type in ("hr", "home_run") and choice == "stay":
        log.append("  [Home run — stay not applicable. Batter must run.]")
        choice = "run"

    # ---- RUN CHOSEN ----
    if choice == "run":
        log.append(f"  {batter.name} runs → {hit_type}.")
        # Track hits allowed for the current pitcher's spell.
        if hit_type in ("single", "infield_single", "double", "triple", "hr", "home_run"):
            state.pitcher_h_this_spell += 1
        if hit_type in ("hr", "home_run"):
            state.pitcher_hr_this_spell += 1
        # Capture runner at runner_out_idx BEFORE advance_runners clears the slot.
        runner_out_idx = outcome.get("runner_out_idx")
        thrown_out_id = (state.bases[runner_out_idx]
                         if runner_out_idx is not None else None)
        new_bases, runs, adv_log = advance_runners(
            state.bases, outcome, batter_id, is_stay=False
        )
        state.bases = new_bases
        log += adv_log
        if runs:
            log += _score_run(state, runs)
        # Record out for runner thrown out on fielder's choice / DP.
        if thrown_out_id is not None:
            log += _record_out(state, thrown_out_id)
        if not batter_safe:
            log.append(f"  {batter.name} is out.")
            log += _record_out(state, batter_id)
        log += _end_at_bat(state)
        return log

    # ---- STAY CHOSEN ----
    # Check: stay must be available.
    if not stay_mod.stay_available(state):
        # No runners — stay not allowed; treat as run.
        log.append("  [Stay unavailable — no runners. Treating as run.]")
        return _resolve_contact(state, log, "run", outcome)

    # Check: does the stay result in the batter being retired?
    batter_out_on_stay = stay_mod.stay_results_in_out(state, caught_fly=caught_fly)

    if batter_out_on_stay:
        # Batter is out; runners still advance per fielding play.
        log.append(f"  {batter.name} STAYS — but is OUT "
                   f"({'2-strike contact' if state.count.strikes == 2 else 'caught fly'}).")
        # Capture runner thrown out BEFORE advance_runners clears the slot.
        runner_out_idx = outcome.get("runner_out_idx")
        thrown_out_id = (state.bases[runner_out_idx]
                         if runner_out_idx is not None else None)
        new_bases, runs, adv_log = advance_runners(
            state.bases, outcome, batter_id, is_stay=True
        )
        state.bases = new_bases
        log += adv_log
        if runs:
            log += _score_run(state, runs)
        if thrown_out_id is not None:
            log += _record_out(state, thrown_out_id)
        log += _record_out(state, batter_id)
        log += _end_at_bat(state)
        return log

    # Valid stay — batter safe, at-bat continues.
    log.append(f"  {batter.name} STAYS at the plate.")
    # Advance runners; no force at 1B; no DP through 1B.
    modified_outcome = dict(outcome)
    modified_outcome["batter_safe"] = True   # batter can't be put out on this play

    original_bases = list(state.bases)   # snapshot BEFORE mutation for credit check
    # Capture runner thrown out BEFORE advance_runners clears the slot.
    runner_out_idx_stay = outcome.get("runner_out_idx")
    stay_thrown_out_id = (state.bases[runner_out_idx_stay]
                          if runner_out_idx_stay is not None else None)

    new_bases, runs, adv_log = advance_runners(
        state.bases, modified_outcome, batter_id, is_stay=True
    )
    state.bases = new_bases
    log += adv_log
    if runs:
        log += _score_run(state, runs)
    # Record out for runner thrown out during valid stay (at-bat continues).
    if stay_thrown_out_id is not None:
        log += _record_out(state, stay_thrown_out_id)

    # Award hit credit (§2.7): only when a runner successfully advanced to a higher
    # base or scored — NOT when the only change was a runner being thrown out.
    # new_bases[i] is not None and ≠ original means a runner ARRIVED at that base.
    runner_successfully_advanced = runs > 0 or any(
        new_bases[i] is not None and new_bases[i] != original_bases[i]
        for i in range(3)
    )
    if runner_successfully_advanced:
        stay_mod.credit_stay_hit(state)
        state.pitcher_h_this_spell += 1    # stay-credited hit counts against pitcher
        log.append(f"  Hit credited to {batter.name} (stay). "
                   f"Total this AB: {state.current_at_bat_hits}.")

    # Fresh count — at-bat continues.
    log += _fresh_count(state)
    # Note: do NOT call _end_at_bat — the at-bat is still in progress.
    return log


# ---------------------------------------------------------------------------
# Walk helper
# ---------------------------------------------------------------------------

def _walk(state: GameState) -> list[str]:
    """Award a walk (4 balls). Force-advances runners."""
    batter = state.current_batter
    log = [f"  WALK — {batter.name} awarded 1B."]
    state.pitcher_bb_this_spell += 1
    state.bases, runs, adv_log = _force_advance_for_walk(state.bases, batter.player_id)
    log += adv_log
    if runs:
        log += _score_run(state, runs)
    log += _end_at_bat(state)
    return log


def _force_advance_for_walk(bases: list, batter_id: str) -> tuple[list, int, list[str]]:
    """
    Force-advance runners on a walk or HBP.
    Runners advance only if the base ahead is occupied (force).
    """
    new_bases = list(bases)
    runs = 0
    log = []
    # Check force chain: 1B→2B→3B→home
    if new_bases[0] is not None:             # someone on 1B
        if new_bases[1] is not None:         # someone on 2B
            if new_bases[2] is not None:     # someone on 3B — scores
                runs += 1
                log.append(f"  Runner scores on walk (forced from 3B).")
                new_bases[2] = None
            new_bases[2] = new_bases[1]      # 2B→3B
            new_bases[1] = None
        new_bases[1] = new_bases[0]          # 1B→2B
        new_bases[0] = None
    new_bases[0] = batter_id                 # batter takes 1B
    return new_bases, runs, log


# ---------------------------------------------------------------------------
# Strike helper
# ---------------------------------------------------------------------------

def _strike(state: GameState, log: list, swinging: bool) -> list[str]:
    state.count.strikes += 1
    kind = "swinging" if swinging else "called"
    log.append(f"  Strike ({kind}). Count: {state.count}.")
    if state.count.strikes >= 3:
        batter_id = state.current_batter.player_id
        log.append(f"  STRIKEOUT.")
        state.pitcher_k_this_spell += 1
        log += _record_out(state, batter_id)
        log += _end_at_bat(state)
    return log
