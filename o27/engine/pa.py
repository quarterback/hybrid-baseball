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
from . import prob as _prob
from .. import config as _cfg
from typing import Optional


def _pick_walk_back_sponsor(state: GameState) -> str:
    """Pick a deterministic Walk-Back sponsor from the rotating pool.

    Deterministic per (game-event-position) so the same Walk-Back shows
    the same sponsor across re-renders. Falls back to an empty string if
    no sponsor pool is configured.
    """
    pool = getattr(_cfg, "WALK_BACK_SPONSORS", None) or []
    if not pool:
        return ""
    # Stable index from the half + PA counter — no RNG dependency.
    idx = (state.total_pa_this_half + len(state.events)) % len(pool)
    return pool[idx]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Walk-Back helpers (post-HR rule-placed runner — see docs/stats-reference.md
# "Walk-Back Rule"). The flag lives on GameState (walk_back_pending) and is
# set after an HR resolves; the NEXT PA captures + clears it, increments
# the current pitcher's wb_faced counter exactly once, and fires the bonus
# run (always unearned) iff the PA drove a runner home from 3B with the bat.
# ---------------------------------------------------------------------------

# Hit types that drive a runner home from 3B with the bat regardless of
# fielding detail (a base hit always scores the trail runner from 3B).
_WALK_BACK_BAT_DRIVES = frozenset((
    "single", "infield_single",
    "double", "triple",
    "hr", "home_run",
))


def _walk_back_should_fire(hit_type: str, batted_advance_from_3b: int = 0) -> bool:
    """True if the Walk-Back runner is driven home with the bat by this PA.

    Fires on: any base hit (1B/2B/3B/HR), OR any batted ball whose
    runner-advance semantic would have scored a runner from 3B (sac fly,
    productive ground out). Walks, strikeouts, foul-outs, line-outs that
    freeze runners, WP / PB / SB / pickoffs / errors-on-non-balls do NOT
    qualify — the runner must be batted home.
    """
    if hit_type in _WALK_BACK_BAT_DRIVES:
        return True
    # Sac fly / productive ground out: the play's runner_advances[2] is the
    # advance amount that WOULD apply to a runner on 3B. Any positive value
    # scores from 3B.
    if hit_type in ("fly_out", "ground_out", "fielders_choice"):
        return batted_advance_from_3b >= 1
    return False


def _resolve_walk_back_at_pa_end(
    state: GameState,
    hit_type: str = "",
    batted_advance_from_3b: int = 0,
) -> list[str]:
    """Resolve any pending Walk-Back at a TRUE PA terminus.

    Call this at every PA-ending event AFTER the play's own run/out
    accounting has been applied but BEFORE _end_at_bat(). For stays that
    do NOT end the PA (multi-hit continuation), do NOT call this — the
    PA is still in progress.

    Behaviour:
      - If state.walk_back_pending is None: no-op, returns [].
      - If set: increments the current pitcher's wb_faced counter exactly
        once (Manfred-runner-faced measurement), then either fires the
        +1 unearned bonus run (if `_walk_back_should_fire(hit_type, ...)`)
        or evaporates the runner. Clears state.walk_back_pending.

    The bonus run, when it fires:
      - bumps state.score (driver RBI is credited downstream by render.py
        via runs_scored delta — so we do NOT call any RBI helper here)
      - bumps pitcher_runs_this_spell AND pitcher_unearned_runs_this_spell
        (Manfred-runner precedent: always unearned, never arc-bucketed
        as earned). pitcher_wb_runs_this_spell increments alongside.
      - bumps partnership_runs (it's still a run that scored this AB).
    """
    pending = state.walk_back_pending
    if pending is None:
        return []
    state.walk_back_pending = None
    state.pitcher_wb_faced_this_spell += 1
    if _walk_back_should_fire(hit_type, batted_advance_from_3b):
        team_id = state.batting_team.team_id
        state.score[team_id] += 1
        state.partnership_runs += 1
        state.pitcher_runs_this_spell += 1
        state.pitcher_unearned_runs_this_spell += 1
        state.pitcher_wb_runs_this_spell += 1
        return [f"  Walk-Back: {pending} is batted home — +1 run (unearned). "
                f"{state.batting_team.name} {state.score[team_id]}."]
    return [f"  Walk-Back: {pending} evaporates — not driven in."]


def _arc_index(outs: int) -> int:
    """Bucket an outs-count into arc 0 (1-9) / arc 1 (10-18) / arc 2 (19-27).

    Super-innings outs (>=27 or in a fresh super-innings half) roll into
    arc 2 (treat as continuation per the wERA/Decay design).
    """
    if outs < 9:
        return 0
    if outs < 18:
        return 1
    return 2

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
    """Add n runs to the batting team's score.

    UER tagging: if any defensive error has occurred this spell, the run
    is charged as unearned. This is over-aggressive vs MLB scoring (real
    scoring tries to determine what runs would have scored absent the
    error), but it's stable and produces visible UER counts that rise
    naturally with team error rate.
    """
    team_id = state.batting_team.team_id
    state.score[team_id] += n
    state.partnership_runs += n
    state.pitcher_runs_this_spell += n
    if getattr(state, "pitcher_errors_this_spell", 0) > 0:
        state.pitcher_unearned_runs_this_spell += n
    else:
        # Earned runs are arc-bucketed at SCORE time (not at reach time)
        # so wERA's late-arc weighting captures when damage actually
        # happens in the 27-out continuous innings.
        state.pitcher_er_arc_this_spell[_arc_index(state.outs)] += n
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
    state.count.reset()
    state.current_at_bat_hits = 0
    state.current_at_bat_swings = 0
    # Hit-and-run protection clears at PA boundary — the play is over.
    state.hit_and_run_active = False
    # Per-PA leadership flares — restore all mutated rating fields to
    # their original values before the next batter steps in. Safe no-op
    # when no flare was active this PA. See prob.apply_pa_leadership_flares
    # for the matching PA-start hook.
    _prob.release_pa_leadership_flares(state)
    # Per-game batter stat bookkeeping. PA counter ticks every AB. If
    # this AB was a joker insertion, the joker_pa counter ticks too —
    # the joker's effective ratings in prob.py decay against this count
    # on their NEXT joker insertion.
    batter = state.current_batter
    if batter is not None:
        bgs = state.bgs(batter.player_id)
        bgs["pa"] += 1
        if state.batter_override is not None:
            bgs["joker_pa"] += 1

    # Joker AB: clear the override and DO NOT advance the base lineup.
    # The joker insertion was an EXTRA PA — the base lineup position
    # stays the same so the originally-scheduled batter takes the next
    # turn.
    if state.batter_override is not None:
        state.batter_override = None
    else:
        state.batting_team.advance_lineup()
    state.pitcher_spell_count += 1
    state.total_pa_this_half += 1
    # BF arc anchored to the OUTS the AB started in (not the outs the
    # AB ends at), so K/BB/FO and BF for the same AB share an arc.
    state.pitcher_bf_arc_this_spell[_arc_index(state.pa_start_outs)] += 1
    # Snapshot start-of-PA outs for the upcoming PA.
    state.pa_start_outs = state.outs
    return log


def _stay_credit_strike(state: GameState) -> list[str]:
    """Stay continuation: spends one strike from the batter's 3-strike
    budget and logs the new count.

    Per O27 rules, every contact event uses one of the batter's 3 strikes,
    whether they ran or stayed. A stay-chosen contact credits a hit AND
    advances the strike counter; the count carries forward across stays
    (no reset). At 3 strikes the AB ends. This is what makes multi-hit
    ABs bounded (max 3 hits, only from a 0-0 start) and what makes
    staying a real cost rather than a free runner-mover.
    """
    state.count.strikes += 1
    return [f"  Stay — strike spent. Count: {state.count}."]


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

    if etype == "intentional_walk":
        # Manager-issued free pass. Bypasses the 4-pitch sim and routes
        # straight through _walk so BB stats, force-advances, and the AB
        # boundary are all handled the same as a 4-ball walk.
        batter = state.current_batter
        log.append(f"  INTENTIONAL WALK — {batter.name} given a free pass.")
        log += _walk(state)
        return log

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
        # O27 foul-out rule: 3 fouls in an at-bat = OUT (FO). The foul
        # counter is independent of the strike counter — at strikes==2
        # the strike count freezes (no MLB-style infinite fouls), but
        # the foul counter keeps climbing toward 3.
        state.count.fouls += 1
        if state.count.fouls >= 3:
            log.append(f"  Foul #{state.count.fouls} — FOUL OUT.")
            state.pitcher_fo_induced_this_spell += 1
            state.pitcher_fo_arc_this_spell[_arc_index(state.pa_start_outs)] += 1
            batter_id = state.current_batter.player_id
            log += _record_out(state, batter_id)
            # Walk-Back terminus: foul-out evaporates the bonus.
            log += _resolve_walk_back_at_pa_end(state, hit_type="foul_out")
            log += _end_at_bat(state)
            return log
        if state.count.strikes < 2:
            state.count.strikes += 1
        log.append(f"  Foul ball (#{state.count.fouls}). Count: {state.count}.")
        return log

    if etype == "foul_tip_caught":
        # Foul tip caught = strikeout (§2.4).
        log.append("  Foul tip caught — STRIKEOUT.")
        state.pitcher_k_this_spell += 1
        state.pitcher_k_arc_this_spell[_arc_index(state.pa_start_outs)] += 1
        batter_id = state.current_batter.player_id
        log += _record_out(state, batter_id)
        # Walk-Back terminus: K evaporates the bonus.
        log += _resolve_walk_back_at_pa_end(state, hit_type="k")
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
        # Walk-Back terminus: HBP is not "batted home" — evaporates.
        log += _resolve_walk_back_at_pa_end(state, hit_type="hbp")
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
        is_hit_and_run = bool(event.get("hit_and_run", False))
        runner_id = state.bases[base_idx]
        if runner_id is None:
            log.append("  No runner to steal.")
            return log
        if success:
            state.bases[base_idx] = None
            state.pitcher_sb_allowed_this_spell += 1
            if base_idx + 1 <= 2:
                state.bases[base_idx + 1] = runner_id
                tag = " (hit-and-run)" if is_hit_and_run else ""
                log.append(f"  Stolen base{tag} — runner advances to "
                            f"{'2B 3B Home'.split()[base_idx]}.")
            else:
                log += _score_run(state)
                log.append("  Steal of home — runner scores!")
            # Flag the rest of this PA for the contact-side bonus when
            # hit-and-run successfully puts the runner in motion. The
            # batter is now swinging at most pitches to protect, so K
            # weight drops. State helper resets the flag at PA boundaries.
            if is_hit_and_run:
                state.hit_and_run_active = True
        else:
            state.bases[base_idx] = None
            state.pitcher_cs_caught_this_spell += 1
            tag = " (hit-and-run)" if is_hit_and_run else ""
            log.append(f"  Runner caught stealing{tag} at "
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

    if etype == "joker_insertion":
        joker = event["joker"]
        log += mgr.insert_joker(state, joker)
        return log

    if etype == "pinch_hit":
        replacement = event["replacement"]
        log += mgr.pinch_hit(state, replacement)
        return log

    if etype == "pitching_change":
        new_pitcher = event["new_pitcher"]
        log += mgr.pitching_change(state, new_pitcher)
        return log

    if etype == "declaration":
        # Declared Seconds: the batting team's manager has chosen to bank
        # the remaining outs for a rebuttal half. Setting state.outs = 27
        # makes is_half_over True so run_half exits cleanly after this
        # event is rendered. The actual team/score stamping is already
        # done in manager.evaluate_declaration; this just terminates the
        # half and emits a play-by-play line.
        team_name = state.batting_team.name
        at_out = int(event.get("at_out", state.outs))
        log.append(f"  >> {team_name} DECLARES SECONDS at out {at_out}.")
        # LOB: runners on base at declaration are stranded — they don't
        # carry over to the rebuttal half. Count them onto the team's
        # season LOB stat so the declaration decision visibly costs
        # something when there are runners aboard.
        stranded = sum(1 for r in state.bases if r is not None)
        if stranded:
            state.batting_team.lob = int(getattr(state.batting_team, "lob", 0) or 0) + stranded
            log.append(f"  >> {stranded} runner(s) stranded on declaration.")
        state.outs = 27
        return log

    if etype == "defensive_sub":
        out_p = event["player_out"]
        in_p  = event["player_in"]
        log += mgr.defensive_sub(state, out_p, in_p)
        return log

    if etype == "pinch_runner":
        log += mgr.pinch_run(state, event["base_idx"], event["runner_in"])
        return log

    if etype == "joker_to_field":
        log += mgr.joker_to_field(state, event["joker"], event["player_out"])
        return log

    if etype == "tactical_def_swap":
        # Mid-batting-half offensive→defensive swap. Reuse pinch_hit
        # semantics (replace current scheduled batter, take the slot)
        # but record our own event tag so the once-per-team cap is
        # separate from leverage-driven pinch hits.
        replacement = event["replacement"]
        log += mgr.pinch_hit(state, replacement)
        log[-1] = log[-1].replace("PINCH HIT", "DEF SWAP")
        state.events.append({
            "type": "tactical_def_swap",
            "team_id": state.batting_team.team_id,
            "replacement_id": replacement.player_id,
        })
        return log

    if etype == "sac_bunt":
        # Manager-called sacrifice bunt. Three resolved outcomes — see
        # manager.should_bunt for the rolling logic. We synthesize the
        # base-state changes here without going through the full contact
        # pipeline (no fielder credit, no error roll — the bunt itself
        # is the play).
        outcome = event.get("outcome", "sacrifice")
        batter = state.current_batter
        batter_id = batter.player_id
        log.append(f"  Sacrifice bunt called by manager.")
        if outcome == "hit":
            # Bunt for hit — advance every runner one base; batter safe at 1B.
            new_bases = [None, None, None]
            runs = 0
            for idx in (2, 1, 0):
                pid = state.bases[idx]
                if pid is None:
                    continue
                np = idx + 1
                if np >= 3:
                    runs += 1
                else:
                    new_bases[np] = pid
            new_bases[0] = batter_id
            state.bases = new_bases
            if runs:
                log += _score_run(state, runs)
            log.append(f"  Bunt single — {batter.name} reaches 1B.")
            # Batter recorded as a hit; the existing _resolve_contact path
            # logs h/ab — but this synthetic event needs to advance the
            # at-bat-cycle state itself.
            state.batting_team.advance_lineup()
            state.count.reset()
            state.total_pa_this_half += 1
        elif outcome == "fail":
            # Failed bunt — batter out, no advancement (popup or lead-runner
            # force; we model as runner stays). 10% of bunt calls.
            log.append(f"  Bunt fails — {batter.name} out, runners hold.")
            log += _record_out(state, batter_id)
            state.batting_team.advance_lineup()
            state.count.reset()
            state.total_pa_this_half += 1
        else:
            # Canonical sacrifice — batter out at 1B, runners advance one.
            new_bases, runs = wild_pitch_advance(state.bases)
            state.bases = new_bases
            if runs:
                log += _score_run(state, runs)
            log.append(f"  Sacrifice — {batter.name} out, runners advance.")
            log += _record_out(state, batter_id)
            state.batting_team.advance_lineup()
            state.count.reset()
            state.total_pa_this_half += 1
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
    # Bump the in-AB swing counter so the next pitch's contact_quality sees
    # this as a 2nd+ swing (only matters when AB continues — a run-chosen or
    # terminal stay calls _end_at_bat which resets to 0).
    state.current_at_bat_swings += 1
    batter = state.current_batter
    batter_id = batter.player_id
    caught_fly = outcome.get("caught_fly", False)
    batter_safe = outcome.get("batter_safe", True)
    hit_type = outcome.get("hit_type", "")

    # Shift telemetry — credit the fielding team for outs added or hits lost
    # by their shift call. The flip itself already happened in resolve_contact;
    # here we accumulate the season-game counter and surface it in the log.
    shift_effect = outcome.get("shift_effect")
    if shift_effect == "out_added":
        state.fielding_team.shift_outs_added += 1
        log.append("  Shift converts single → ground out. "
                   "(Defense reads the pull tendency.)")
    elif shift_effect == "hit_lost":
        state.fielding_team.shift_hits_lost += 1
        log.append("  Shift exposed — ground ball through the vacated side. "
                   "(Batter beat the alignment.)")

    # PRD §2.6: stay does not apply to home runs — batter must run.
    # fielding.py emits hit_type "hr" for home runs.
    if hit_type in ("hr", "home_run") and choice == "stay":
        log.append("  [Home run — stay not applicable.]")
        choice = "run"

    # ---- RUN CHOSEN ----
    if choice == "run":
        # Errors are surfaced explicitly in the play-by-play log so
        # broadcasters / box scores can call them out separately from hits.
        if hit_type == "error" or outcome.get("is_error"):
            log.append(f"  {batter.name} reached on ERROR.")
            state.pitcher_errors_this_spell += 1
        else:
            log.append(f"  {batter.name} runs → {hit_type}.")
        # Track hits allowed for the current pitcher's spell.
        # Errors are NOT hits — pitcher's H allowed does not increment.
        if hit_type in ("single", "infield_single", "double", "triple", "hr", "home_run"):
            state.pitcher_h_this_spell += 1
            state.bgs(batter.player_id)["h"] += 1
        if hit_type in ("hr", "home_run"):
            state.pitcher_hr_this_spell += 1
        # Capture runners at runner_out_idx + extra_runner_outs (TP) BEFORE
        # advance_runners clears the slots.
        runner_out_idx = outcome.get("runner_out_idx")
        extra_outs_idxs = outcome.get("extra_runner_outs") or []
        out_runner_ids: list[str] = []
        if runner_out_idx is not None and state.bases[runner_out_idx] is not None:
            out_runner_ids.append(state.bases[runner_out_idx])
        for ix in extra_outs_idxs:
            if state.bases[ix] is not None:
                out_runner_ids.append(state.bases[ix])
        new_bases, runs, adv_log = advance_runners(
            state.bases, outcome, batter_id, is_stay=False
        )
        state.bases = new_bases
        log += adv_log
        if runs:
            log += _score_run(state, runs)
        # Record outs for runners thrown out on fielder's choice / DP / TP.
        for rid in out_runner_ids:
            log += _record_out(state, rid)
        if not batter_safe:
            log.append(f"  {batter.name} is out.")
            log += _record_out(state, batter_id)
        # Walk-Back terminus: resolve any pending Walk-Back from the prior
        # PA. The 3B advance value drives sac-fly / productive-ground-out
        # detection.
        advances = outcome.get("runner_advances") or [0, 0, 0]
        adv_from_3b = advances[2] if len(advances) > 2 else 0
        log += _resolve_walk_back_at_pa_end(state, hit_type=hit_type,
                                            batted_advance_from_3b=adv_from_3b)
        # Walk-Back arming: an HR resolves MLB-exactly above; AFTER all
        # bookkeeping for this PA finishes, arm the Walk-Back for the
        # next batter's PA only. The HR-hitter is the bonus runner.
        if hit_type in ("hr", "home_run"):
            state.walk_back_pending = batter_id
            log.append(f"  [Walk-Back armed — {batter.name} can be batted home by the next hitter for +1.]")
            sponsor = _pick_walk_back_sponsor(state)
            if sponsor:
                log.append(f"  [The Walk-Back is brought to you by {sponsor}.]")
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
        # Walk-Back terminus on a stay-out: the stay played as the
        # fielding outcome it was, so use its hit_type + 3B-advance.
        advances = outcome.get("runner_advances") or [0, 0, 0]
        adv_from_3b = advances[2] if len(advances) > 2 else 0
        log += _resolve_walk_back_at_pa_end(state, hit_type=hit_type,
                                            batted_advance_from_3b=adv_from_3b)
        log += _end_at_bat(state)
        return log

    # Valid stay — batter safe, at-bat continues.
    log.append(f"  {batter.name} STAYS at the plate.")
    # Advance runners; no force at 1B; no DP through 1B.
    modified_outcome = dict(outcome)
    modified_outcome["batter_safe"] = True   # batter can't be put out on this play

    # Note: 2C-event runner_advances are talent-weighted in
    # prob.py (post-stay-decision block), not here. The outcome dict
    # arriving in this branch already reflects the eye/contact-vs-command
    # gate that decides hit-credit (weak) and advancement magnitude
    # (medium). pa.py just consumes the modified outcome.

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

    # 2C hit credit (§2.7): a 2C that advances a runner IS a hit — the
    # batter delivered contact that moved the chain. Movement-only 2Cs
    # (1B→2B, no run) and run-scoring 2Cs both credit a hit. Only a
    # failed 2C (talent gate produced adv=0, no runner moved) skips
    # the hit credit. The design intent is "advance runners or bring
    # them home"; both of those produce a hit.
    runner_successfully_advanced = runs > 0 or any(
        new_bases[i] is not None and new_bases[i] != original_bases[i]
        for i in range(3)
    )
    if runner_successfully_advanced:
        stay_mod.credit_stay_hit(state)
        state.pitcher_h_this_spell += 1    # stay-credited hit counts against pitcher
        state.bgs(batter.player_id)["h"] += 1
        log.append(f"  Hit credited to {batter.name} (stay). "
                   f"Total this AB: {state.current_at_bat_hits}.")

    # Strike-burn is skill-conditional: a 2C that successfully advanced
    # runners (the talent gate in prob.py passed) costs nothing on the
    # count — the batter earned it. A 2C where no runner moved (gate
    # failed) burns a strike, ending the AB at 3. Pitchers still pay
    # via pitch count (ball_in_play always increments pitches), so a
    # chain of earned 2Cs runs the pitcher's count up.
    if runner_successfully_advanced:
        log.append(f"  Stay successful — count unchanged. Count: {state.count}.")
    else:
        log += _stay_credit_strike(state)
        if state.count.strikes >= 3:
            log.append(f"  At-bat ends — {batter.name} used all 3 strikes "
                       f"(failed-stay sequence; no batter-out).")
            log += _end_at_bat(state)
    # Note: when AB doesn't end, do NOT call _end_at_bat — the at-bat is
    # still in progress with the new (carried-forward) count.
    return log


# ---------------------------------------------------------------------------
# Walk helper
# ---------------------------------------------------------------------------

def _walk(state: GameState) -> list[str]:
    """Award a walk (4 balls). Force-advances runners."""
    batter = state.current_batter
    log = [f"  WALK — {batter.name} awarded 1B."]
    state.pitcher_bb_this_spell += 1
    state.bgs(batter.player_id)["bb"] += 1
    # No bb_arc tracking yet — current spec uses BB only as a per-pitcher
    # season counter (xFIP / K%-BB%); arc-bucketing it would add cost
    # without serving the trio. Revisit if a per-arc walk-rate metric
    # earns a slot.
    state.bases, runs, adv_log = _force_advance_for_walk(state.bases, batter.player_id)
    log += adv_log
    if runs:
        log += _score_run(state, runs)
    # Walk-Back terminus: walks do not drive a runner home (force advance
    # only scores from 3B if bases were loaded, which can't be true after
    # an HR cleared them; walk-back-pending sees a "bb" hit_type → evap).
    log += _resolve_walk_back_at_pa_end(state, hit_type="bb")
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
        state.pitcher_k_arc_this_spell[_arc_index(state.pa_start_outs)] += 1
        log += _record_out(state, batter_id)
        # Walk-Back terminus: K evaporates the bonus.
        log += _resolve_walk_back_at_pa_end(state, hit_type="k")
        log += _end_at_bat(state)
    return log
