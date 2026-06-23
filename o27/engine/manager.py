"""
Manager decision logic for O27.

Covers:
  - Joker insertion (§2.3 / §4.6)
  - Pinch-hit substitution
  - Pitching changes

Phase 1: constraint enforcement + heuristic stubs that always return False
         (no AI decisions; Phase 1 tests drive manager events explicitly).
Phase 2: heuristic logic for joker insertion, pitching changes, pinch hits.

All tunable thresholds are imported from o27.config.
"""

from __future__ import annotations
from .state import GameState, Player, SpellRecord, Substitution
from typing import Optional
from o27 import config as cfg
from o27.engine import cricket_order
from o27.engine import defense as _defense


def _in_regulation(state: GameState) -> bool:
    """True only during a regulation half — not super-innings, not a Declared
    Seconds frame. The Cricket Batting Order flip is regulation-only."""
    return not state.is_super_inning and not getattr(state, "in_seconds_phase", False)


# ---------------------------------------------------------------------------
# Substitution-trigger evaluation (Item 3)
# ---------------------------------------------------------------------------
#
# Single unified scorer that every new substitution decision routes through.
# Returns a [0, 1] leverage score; the manager fires when score >= threshold
# (where threshold = 1.0 - mgr_platoon_aggression, so a passive manager
# only swaps when the spot is overwhelming and an aggressive one swaps
# freely). The legacy `should_*` paths keep their inline scoring for now;
# Item 4 follow-up migrates them through this function and tunes the
# weights.
#
# Factors (each in [0, 1]):
#   score_gap_f     — tighter games score higher (max at tie)
#   late_arc_f      — later in the half scores higher (max at out 27)
#   runner_f        — runners on base raise PH / PR leverage
#   upgrade_f       — skill delta between candidate and outgoing player
#                     in the relevant dimension (hit / run / field)
#   conservation_f  — penalty proportional to bench depletion at the
#                     relevant role; deliberately simple in v1


def _has_platoon_edge(bats: str, p_throws: str) -> bool:
    """True if the batter has the platoon advantage against this pitcher.

    Switch hitters always have it; otherwise opposite-handed = edge.
    Empty handedness fields (legacy DB rows) return False.
    """
    if not bats or not p_throws:
        return False
    if bats == "S":
        return True
    return bats != p_throws


def score_substitution(
    state: GameState,
    candidate: Player,
    kind: str,
    out_player: Optional[Player] = None,
) -> float:
    """Score a candidate substitution against current game leverage.

    `kind` is one of "pinch_hit" / "pinch_run" / "pinch_field". Returns a
    score in [0, 1]; callers compare against a manager-derived threshold
    (`substitution_threshold(team)`).

    Factors (each in [0, 1], averaged):
      score_gap_f  — tighter games favor subs (1.0 at tie, 0.0 at 10+ gap)
      late_arc_f   — later in the half raises leverage (out/27)
      runner_f     — runners on raise PH/PR (PF is runner-independent)
      upgrade_f    — skill delta candidate vs outgoing in relevant dimension
      matchup_f    — handedness platoon edge for PH (neutral 0.5 otherwise)
    """
    if out_player is None:
        return 0.0

    # Score-gap factor — tighter games favor substitutions.
    v = state.score.get("visitors", 0)
    h = state.score.get("home", 0)
    score_gap = abs(v - h)
    score_gap_f = max(0.0, 1.0 - score_gap / 10.0)

    # Late-arc factor — late in the half raises leverage.
    late_arc_f = state.outs / 27.0 if state.outs < 27 else 1.0

    # Runner factor — PH / PR weight heavier with runners on; PF lighter.
    runners = state.runner_count
    if kind == "pinch_field":
        runner_f = 0.5    # defense leverage less runner-dependent
    else:
        runner_f = (runners + 1) / 4.0

    # Upgrade factor — skill delta in the relevant dimension.
    if kind == "pinch_hit":
        delta = float(getattr(candidate, "skill", 0.5)) - float(getattr(out_player, "skill", 0.5))
    elif kind == "pinch_run":
        delta = float(getattr(candidate, "speed", 0.5)) - float(getattr(out_player, "speed", 0.5))
    elif kind == "pinch_field":
        delta = float(getattr(candidate, "defense", 0.5)) - float(getattr(out_player, "defense", 0.5))
    else:
        delta = 0.0
    # Map delta from [-1, 1] to [0, 1], clipped.
    upgrade_f = max(0.0, min(1.0, 0.5 + delta))

    # Matchup factor — handedness platoon edge for pinch_hit. Replaces
    # the inline platoon-pool logic in the legacy should_pinch_hit (Item
    # 2 follow-up: handedness migrates into the trigger function).
    matchup_f = 0.5
    if kind == "pinch_hit":
        pitcher = state.get_current_pitcher()
        if pitcher is not None:
            p_throws = getattr(pitcher, "throws", "") or ""
            cand_edge = _has_platoon_edge(getattr(candidate,  "bats", "") or "", p_throws)
            out_edge  = _has_platoon_edge(getattr(out_player, "bats", "") or "", p_throws)
            if cand_edge and not out_edge:
                matchup_f = 0.85
            elif out_edge and not cand_edge:
                matchup_f = 0.15

    # Combine — equal-weighted average over five factors.
    score = (score_gap_f + late_arc_f + runner_f + upgrade_f + matchup_f) / 5.0
    return max(0.0, min(1.0, score))


def substitution_threshold(team) -> float:
    """Convert manager platoon aggression to a substitution threshold.

    The trigger function returns a leverage score in roughly [0.2, 0.9]
    depending on the spot; a manager fires when that score clears their
    threshold. Mapped to [0.55, 0.85] across the persona ladder so the
    different archetypes play visibly differently — not so they hit any
    particular volume target.

    Persona ladder (validated against 20-game batches at each fixed
    aggression):
      0.05  → threshold 0.835 → ~0.03 subs/team/game (a "never subs"
                                 dead-ball traditionalist)
      0.50  → threshold 0.700 → ~2.6  subs/team/game (mid-band)
      0.92  → threshold 0.574 → ~12.5 subs/team/game (platoon manager,
                                 cycles the bench aggressively)

    Substitutions remain situational throughout — the trigger only fires
    when the score-gap, late-arc, runner, upgrade, and matchup factors
    *combine* into enough leverage to clear the threshold. The threshold
    sets the bar for what each manager considers "enough leverage."
    """
    agg = float(getattr(team, "mgr_platoon_aggression", 0.5) or 0.5)
    return max(0.0, min(1.0, 0.85 - 0.30 * agg))


def _log_substitution(
    state: GameState,
    *,
    kind: str,
    team_id: str,
    in_player_id: str,
    out_player_id: str,
    lineup_index: Optional[int] = None,
    trigger_score: float = 0.0,
    reason: str = "",
    one_way: bool = True,
) -> None:
    """Append a Substitution to state.substitution_log and (by default) add
    the outgoing player to the team's one-way exit set.

    Centralized so every substitution path stamps the invariant. Callers
    that pass an empty out_player_id (e.g., joker insertion where the
    "out" player is the one whose lineup spot is hijacked for a PA only)
    skip the one-way enforcement — those callers are responsible for
    handling re-entry rules themselves.

    `one_way=False` records the substitution for the box score WITHOUT
    retiring the outgoing player. Used by tactical (non-injury) defensive
    subs: the glove changes in the field but the displaced starter is "not
    out" and keeps batting in his lineup-card slot.
    """
    score_for = state.score.get(team_id, 0)
    other_id = "home" if team_id == "visitors" else "visitors"
    score_against = state.score.get(other_id, 0)
    state.substitution_log.append(Substitution(
        half=state.half,
        outs_at_sub=state.outs,
        kind=kind,
        team_id=team_id,
        in_player_id=in_player_id,
        out_player_id=out_player_id,
        lineup_index=lineup_index,
        score_for=int(score_for),
        score_against=int(score_against),
        trigger_score=float(trigger_score),
        reason=reason,
    ))
    # One-way enforcement — the outgoing player is gone for the rest of
    # the game. Look up which team owns them. Skipped for field-only subs
    # (one_way=False), where the displaced starter stays eligible to bat.
    if out_player_id and one_way:
        for t in (state.visitors, state.home):
            if t.team_id == team_id:
                t.substituted_out.add(out_player_id)
                break


# ---------------------------------------------------------------------------
# Joker insertion
# ---------------------------------------------------------------------------

def can_insert_joker(state: GameState, joker: Player) -> tuple[bool, str]:
    """Check whether a joker can be inserted right now.

    Joker insertion rules:
      - Joker must be in team.jokers_available (game-time pool of 3).
      - Super-innings disable joker insertion (the 5-batter format
        has its own selection mechanic).
      - Per-turnover cooldown: a joker that has already batted this time
        through the order can't be re-inserted until the base lineup
        cycles (jokers_used_this_cycle, cleared in advance_lineup).

    The on-base safety check (a joker on base can't also be batting) is
    enforced in should_insert_joker.
    """
    if state.is_super_inning:
        return False, "Joker insertion not allowed in super-innings."
    team = state.batting_team
    if joker.player_id not in {j.player_id for j in team.jokers_available}:
        return False, "Joker not in available pool."
    if joker.player_id in team.jokers_used_this_cycle:
        return False, "Joker already used this time through the order."
    return True, ""


def insert_joker(state: GameState, joker: Player, lineup_position: int = -1) -> list[str]:
    """Insert a joker for the next PA via state.batter_override.

    The joker bats in place of the base-lineup batter for ONE plate
    appearance, then returns to the bench. The base lineup position is
    NOT advanced by the joker AB (handled in pa._end_at_bat). A joker may
    be deployed at most once per time through the order; he becomes
    eligible again only after the base lineup cycles (the cooldown set is
    cleared in advance_lineup). There is no overall per-game cap.

    `lineup_position` is accepted for back-compat but ignored — the
    joker insertion is always "before the next scheduled batter."
    """
    _ = lineup_position
    ok, reason = can_insert_joker(state, joker)
    if not ok:
        return [f"  [Joker insert rejected: {reason}]"]
    team = state.batting_team
    team.jokers_used_this_cycle.add(joker.player_id)
    state.batter_override = joker
    state.events.append({
        "type": "joker_inserted",
        "joker_id": joker.player_id,
        "joker_name": joker.name,
    })
    return [f"  JOKER: {team.name} sends in {joker.name} for the next PA."]


def _legacy_insert_joker(state: GameState, joker: Player, lineup_position: int) -> list[str]:
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
    log = [f"  JOKER: {team.name} inserts {joker.name} ({getattr(joker, 'archetype', 'joker')}) into lineup."]

    # Mark joker as used this half so advance_lineup() skips their natural slot.
    team.jokers_used_this_half.add(joker.player_id)
    # Remove from available pool so the same joker is not re-inserted this half.
    team.jokers_available = [j for j in team.jokers_available
                              if j.player_id != joker.player_id]
    # Move the joker to the desired slot within the existing 12-batter lineup.
    # The joker is already in the lineup; remove-then-insert re-orders without
    # changing the list length (preserves the 12-batter active lineup invariant).
    if joker in team.lineup:
        team.lineup.remove(joker)
    team.lineup.insert(lineup_position, joker)
    team.lineup_position = lineup_position % len(team.lineup)

    state.events.append({
        "type": "joker_inserted",   # distinct from the provider intent event "joker_insertion"
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
    team = state.batting_team
    pos = team.lineup_position % len(team.lineup)
    replaced = team.lineup[pos]
    team.lineup[pos] = replacement
    # Inherit the replaced player's fielding slot so the box score has
    # something to render. If the PH later actually plays the field
    # (mid-game, after their PA), they'll be at this position.
    if not getattr(replacement, "game_position", "") and getattr(replaced, "game_position", ""):
        replacement.game_position = replaced.game_position
    log = [f"  PINCH HIT: {replacement.name} bats for {replaced.name}."]

    state.events.append({
        "type": "pinch_hit",
        "replaced_id": replaced.player_id,
        "replaced_name": replaced.name,
        "replacement_id": replacement.player_id,
        "replacement_name": replacement.name,
    })
    _log_substitution(
        state,
        kind="pinch_hit",
        team_id=team.team_id,
        in_player_id=replacement.player_id,
        out_player_id=replaced.player_id,
        lineup_index=pos,
        reason="manager_ph",
    )
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
    if old_pitcher and (state.pitcher_spell_count > 0
                        or state.pitcher_outs_this_spell > 0):
        # Close the current spell when the pitcher faced batters OR recorded
        # any out (a pickoff / caught-stealing out can happen with zero
        # complete PAs; dropping it would lose the out from the pitcher ledger).
        spell = SpellRecord(
            pitcher_id=old_pitcher.player_id,
            pitcher_name=old_pitcher.name,
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
            # Use the unified phase index so a pitching change DURING a
            # seconds round tags the closing spell with the correct phase
            # (otherwise it falls back to raw super_inning_number=0 and the
            # spell's stats end up in phase=0 mixed with regulation).
            super_inning_number=getattr(state, "phase_number", 0) or state.super_inning_number,
            sb_allowed=state.pitcher_sb_allowed_this_spell,
            cs_caught=state.pitcher_cs_caught_this_spell,
            fo_induced=state.pitcher_fo_induced_this_spell,
            balks=state.pitcher_balks_this_spell,
            catchers_balks=state.pitcher_catchers_balk_this_spell,
            ci_allowed=state.pitcher_ci_this_spell,
            er_arc=list(state.pitcher_er_arc_this_spell),
            k_arc=list(state.pitcher_k_arc_this_spell),
            fo_arc=list(state.pitcher_fo_arc_this_spell),
            bf_arc=list(state.pitcher_bf_arc_this_spell),
            k_tto=list(state.pitcher_k_tto_this_spell),
            fo_tto=list(state.pitcher_fo_tto_this_spell),
            bf_tto=list(state.pitcher_bf_tto_this_spell),
            wb_faced=state.pitcher_wb_faced_this_spell,
            wb_runs=state.pitcher_wb_runs_this_spell,
            ir_inherited=state.pitcher_ir_inherited_this_spell,
            ir_scored=state.pitcher_ir_scored_this_spell,
            entry_lead=state.pitcher_entry_lead_this_spell,
            min_lead=state.pitcher_min_lead_this_spell,
            finished=0,   # replaced mid-half — did not finish the game
        )
        state.spell_log.append(spell)
        log.append(f"  PITCHING CHANGE: {old_pitcher.name} exits "
                   f"({state.pitcher_spell_count} BF this spell).")

    state.current_pitcher_id = new_pitcher.player_id
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
    state.pitcher_balks_this_spell = 0
    state.pitcher_catchers_balk_this_spell = 0
    state.pitcher_ci_this_spell = 0
    state.pitcher_errors_this_spell = 0
    state.pitcher_er_arc_this_spell = [0, 0, 0]
    state.pitcher_k_arc_this_spell  = [0, 0, 0]
    state.pitcher_fo_arc_this_spell = [0, 0, 0]
    state.pitcher_bf_arc_this_spell = [0, 0, 0]
    state.pitcher_k_tto_this_spell  = [0, 0, 0]
    state.pitcher_fo_tto_this_spell = [0, 0, 0]
    state.pitcher_bf_tto_this_spell = [0, 0, 0]
    state.pitcher_wb_faced_this_spell = 0
    state.pitcher_wb_runs_this_spell = 0
    # Inherited runners: whoever's on base at the change is charged to the new
    # arm's inherited-runner ledger — stranding them is to his credit, and any
    # who score count against his IR-Stop%. The starter inherits nobody (empty
    # bases at game start).
    state.inherited_runner_ids = {b for b in state.bases if b is not None}
    state.pitcher_ir_inherited_this_spell = len(state.inherited_runner_ids)
    state.pitcher_ir_scored_this_spell = 0
    # Re-arm finisher lead tracking — the new arm's entry lead is captured
    # lazily on his first event (pa.apply_event).
    state.pitcher_lead_init_this_spell = False
    state.pitcher_entry_lead_this_spell = 0
    state.pitcher_min_lead_this_spell = 0
    state.pitcher_start_pa = state.total_pa_this_half
    log.append(f"  {new_pitcher.name} takes the mound.")

    state.events.append({
        "type": "pitching_change",
        "old_pitcher_id": old_pitcher_id,
        "new_pitcher_id": new_pitcher.player_id,
        "new_pitcher_name": new_pitcher.name,
    })
    # Stamp the one-way invariant on the old pitcher. A pulled arm doesn't
    # return mid-game (existing pick_new_pitcher already excludes them via
    # `already_pitched`; this just promotes that to a hard cross-game-state
    # check so super-innings and Declared Seconds also respect it).
    if old_pitcher_id:
        _log_substitution(
            state,
            kind="pitching",
            team_id=state.fielding_team.team_id,
            in_player_id=new_pitcher.player_id,
            out_player_id=old_pitcher_id,
            reason="pitching_change",
        )
    return log


# ---------------------------------------------------------------------------
# Manager decision heuristics (Phase 2)
# ---------------------------------------------------------------------------

def _batting_deficit(state: GameState) -> int:
    """
    Return how many runs the batting team is trailing by (positive = behind).
    Negative means the batting team leads.
    """
    v = state.score.get("visitors", 0)
    h = state.score.get("home", 0)
    if state.half in ("top", "super_top"):
        return h - v
    return v - h


def _needed_archetype(state: GameState) -> Optional[str]:
    """
    Return the archetype called for by the current game situation, or None.

    Evaluation order (§4.6):
      1. power   — batting team down ≥ JOKER_POWER_DEFICIT with outs remaining.
                   Dominates: fires even when RISP or corners are also present.
      2. speed   — corners: 1B+3B occupied, 2B empty, exactly 1 out.
      3. contact — runners in scoring position (2B or 3B occupied).
    """
    # Power dominates: checked first regardless of base state.
    deficit = _batting_deficit(state)
    if deficit >= cfg.JOKER_POWER_DEFICIT and state.outs < cfg.JOKER_POWER_OUTS_CEIL:
        return "power"

    # Speed: corners (1B+3B, 2B empty, exactly 1 out) — spec §4.6.
    if (
        state.bases[0] is not None
        and state.bases[1] is None
        and state.bases[2] is not None
        and state.outs == 1
    ):
        return "speed"

    # Contact: runners in scoring position.
    if state.runners_in_scoring_position:
        return "contact"

    return None


def _pick_freshest_joker(eligible: list, state: GameState) -> Player:
    """Pick the joker with the fewest PAs this game (skill breaks ties).

    Replaces the legacy "always pick highest skill" selection that let
    one elite joker monopolize all insertions. Combined with the
    per-joker rating decay in prob.py, this spreads usage across the
    pool so each of the three jokers gets a meaningful share of the
    team's joker ABs.
    """
    return min(
        eligible,
        key=lambda j: (
            state.bgs(j.player_id).get("joker_pa", 0),
            -float(getattr(j, "skill", 0.5) or 0.5),
        ),
    )


def _pool_fatigue_mult(eligible: list, state: GameState) -> float:
    """Soft probability dampener tied to the freshest joker's usage.

    Used alongside the per-joker rating decay in prob.py. The decay
    handles the "joker stops being productive" half; this dampener
    handles the "manager realizes the bench is tapped out" half. It's
    NOT a hard cap — a determined manager in a clutch spot can still
    insert past the soft cap, just at a much lower roll rate.

    Reads the MIN joker_pa across eligible jokers (i.e., the freshest
    available). If even the freshest is already gassed, the pool is
    spent and probability collapses sharply.

    Curve leaves room for genuine anomaly games (the occasional 9-,
    10-, or 11-PA joker is a story, not a bug) while keeping the
    median at 5-7 PAs:
      pool freshness (min joker_pa) → multiplier
      0..3 PAs : 1.00   (pool fresh)
      4 PAs    : 0.90
      5 PAs    : 0.70
      6 PAs    : 0.50
      7 PAs    : 0.35
      8 PAs    : 0.22
      9+ PAs   : 0.12   (anomaly territory — rare but possible)
    """
    if not eligible:
        return 0.0
    fresh = min(
        state.bgs(j.player_id).get("joker_pa", 0) for j in eligible
    )
    if fresh <= 3:  return 1.00
    if fresh == 4:  return 0.90
    if fresh == 5:  return 0.70
    if fresh == 6:  return 0.50
    if fresh == 7:  return 0.35
    if fresh == 8:  return 0.22
    return 0.12


def should_insert_joker(state: GameState, rng=None) -> Optional[Player]:
    """Joker insertion decision — a leverage-aware, late-stage tactical tool.

    Per-PA call: returns a Player from the team's joker pool to insert,
    or None to let the base lineup proceed normally. Held off entirely until
    the lineup has turned over once (lineup_cycle_number >= 1) so the nine base
    batters each hit before any joker appears.

    A joker comes in only when BOTH hold:
      - it is a genuine upgrade over the batter due up (the best eligible
        joker out-hits him on skill) — so the manager never pinch-hits for
        his own good bats; and
      - the spot earns it: leverage = tight game × late in the half × runners
        on. The curve is weighted toward the end of the half (outs/27), so a
        weak hitter draws a joker in a big late spot but still bats freely
        early and mid-game.

    The bigger the skill gap, the more willing the manager is to spend the
    joker here (the `1 + upgrade` term), so the weak end of the order
    naturally draws the insertions — but always through leverage, never the
    old unconditional "replace the weak hitter every cycle" override, which
    benched the worst bats all game (not the intent).

    Selection: freshest joker first, ties broken by skill. The rating decay
    in prob.py makes each successive joker AB less productive, so spreading
    usage across the three jokers is the natural play.
    """
    if state.is_super_inning:
        return None
    # Don't insert a joker while another joker is already mid-AB.
    if getattr(state, "batter_override", None) is not None:
        return None
    team = state.batting_team
    if not team.jokers_available:
        return None
    # Lineup-integrity gate: no joker insertion until the starting lineup has
    # batted through once (lineup_cycle_number >= 1). The first trip through the
    # order belongs to the nine base batters — every fielder and the pitcher
    # hits before any tactical insertion. Mirrors should_pinch_hit/_run and the
    # defensive-sub gate. Forced injury subs bypass this via the executor path.
    if team.lineup_cycle_number < 1:
        return None
    # A joker on base from a prior PA can't physically also be at bat —
    # Bonds can't be at 2B and "also" inserted to bat again. And a joker
    # already used this time through the order is on cooldown until the
    # base lineup cycles.
    on_base_ids = {pid for pid in state.bases if pid is not None}
    eligible = [j for j in team.jokers_available
                if j.player_id not in on_base_ids
                and j.player_id not in team.jokers_used_this_cycle]
    if not eligible:
        return None

    # Upgrade guard: never joker for a batter the pool can't out-hit. This is
    # what stops the manager pinch-hitting his own good bats, and it makes the
    # leverage roll below naturally favor the weak end of the order.
    batter = state.current_batter
    batter_skill = (
        float(getattr(batter, "skill", 0.5) or 0.5) if batter is not None else 0.5
    )
    best_joker_skill = max(
        float(getattr(j, "skill", 0.5) or 0.5) for j in eligible
    )
    if best_joker_skill <= batter_skill:
        return None
    upgrade = best_joker_skill - batter_skill   # 0..1: how much better the joker is

    joker_agg = float(getattr(team, "mgr_joker_aggression", 0.5))
    # Pool fatigue — collapses insertion probability once every joker has been
    # used heavily. Identity (1.0) early; near-zero past ~8 PAs on the freshest.
    pool_mult = _pool_fatigue_mult(eligible, state)
    # Cricket Batting Order opportunity cost: when the rule is on, in regulation,
    # and this trip is still joker-free, deploying a joker forfeits the chance to
    # EARN this cycle's flip. Flip-minded skippers damp their insertion rate;
    # joker-happy ones barely flinch.
    pool_mult *= joker_flip_damp(team, state)

    # Leverage: tighter games + later innings + runners on = high leverage.
    # This is the whole point of a joker — a strategic, late-game weapon, not
    # an every-PA bench for weak hitters.
    score_gap = abs(state.score.get("visitors", 0) - state.score.get("home", 0))
    runners   = state.runner_count
    gap_factor    = max(0.0, 1.0 - score_gap / 10.0)   # tied = 1.0; 10+ gap = 0
    late_factor   = state.outs / 27.0                  # 0..1, late half = high
    runner_factor = (runners + 1) / 4.0                # 0.25..1.0
    leverage = gap_factor * late_factor * runner_factor

    # Per-PA insertion probability. The (1 + upgrade) term tilts spend toward
    # the weak end of the order; the 0.35 cap keeps even a max spot bounded.
    insert_p = min(0.35, leverage * (0.25 + 0.5 * joker_agg) * (1.0 + upgrade)) * pool_mult
    if rng is None:
        import random as _r
        roll = _r.random()
    else:
        roll = rng.random()
    if roll >= insert_p:
        return None

    return _pick_freshest_joker(eligible, state)


# ---------------------------------------------------------------------------
# Cricket Batting Order (optional rule) — flip-vs-joker decisions
# ---------------------------------------------------------------------------

def joker_flip_damp(team, state: GameState) -> float:
    """Multiplier (<=1.0) applied to joker-insertion probability to reflect the
    opportunity cost of forfeiting this cycle's earned flip.

    Returns 1.0 (no effect) unless ALL of:
      - the Cricket Batting Order rule is on for this team,
      - we're in a regulation half (the flip is regulation-only), and
      - the current trip is still joker-free (deploying now is what forfeits
        the flip; once a joker is used this cycle the flip is already gone, so
        further jokers this cycle are undamped).

    The cut scales with the skipper's flip aggression: a pure flip-minded
    manager loses up to CRICKET_JOKER_FLIP_DAMP of his insert probability; a
    joker-happy manager (low flip aggression) is barely affected.
    """
    if not cricket_order.cricket_order_on(team):
        return 1.0
    if not _in_regulation(state):
        return 1.0
    if team.jokers_used_this_cycle:
        return 1.0   # flip already forfeited this cycle — no further cost
    flip_agg = float(getattr(team, "mgr_flip_aggression", 0.5))
    return max(0.0, 1.0 - cfg.CRICKET_JOKER_FLIP_DAMP * flip_agg)


def should_use_flip(state: GameState, rng=None) -> bool:
    """Decide whether the batting manager spends an EARNED cricket flip at the
    top of a new cycle (use-or-lose). Persona-driven and situational.

    Drivers:
      - mgr_flip_aggression persona (0.5 neutral): a flip-loving skipper spends
        readily, a fixed-order traditionalist rarely does.
      - Score: trailing raises the desire to churn the order for offense; a
        comfortable lead lowers it.
      - Out-arc: later in the 27-out half raises the desire (less arc left to
        wait for the order to come good on its own).

    The caller (prob.py) has already gated rule-on + regulation + a pending
    flip; this function only weighs the spend.
    """
    team = state.batting_team
    flip_agg = float(getattr(team, "mgr_flip_aggression", 0.5))

    # Persona multiplier centred on 1.0 at flip_agg=0.5, spanning
    # (1 - S/2) .. (1 + S/2) across the [0,1] persona range.
    S = cfg.CRICKET_FLIP_AGG_SCALE
    persona_mult = 1.0 + S * (flip_agg - 0.5)
    persona_mult = max(0.0, persona_mult)

    own = state.score.get(team.team_id, 0)
    opp_id = "home" if team.team_id == "visitors" else "visitors"
    opp = state.score.get(opp_id, 0)
    # +1.0 when trailing by 10+, -1.0 when leading by 10+, 0 when tied.
    trail = max(-1.0, min(1.0, (opp - own) / 10.0))
    arc = max(0.0, min(1.0, state.outs / 27.0))

    situational = (1.0
                   + cfg.CRICKET_FLIP_TRAIL_SCALE * trail
                   + cfg.CRICKET_FLIP_ARC_SCALE * arc)

    p = cfg.CRICKET_FLIP_BASE_PROB * persona_mult * situational
    p = max(0.0, min(cfg.CRICKET_FLIP_MAX_PROB, p))

    roll = (rng.random() if rng is not None else __import__("random").random())
    return roll < p


def should_intentional_walk(state: GameState, rng=None) -> bool:
    """Decide whether to issue an intentional walk before this PA.

    Manager refuses to pitch to a hot or elite batter when first base is
    open and the situational stakes are high enough. Drivers:
      - Hot-streak factor: current-game AVG above IBB_AVG_FLOOR, with a
        bonus when the batter has 3+ hits today (3-for-3 reads stronger
        than .500 in 2 PAs).
      - Leverage: late in the half + runners in scoring position.
      - Manager persona: mgr_ibb_aggression (fielding team) sets the
        baseline willingness.

    Hard gates:
      - Never with 1B occupied (would just give a free base ahead).
      - Skip with 2 outs and bases empty (no leverage to walk anyone).
      - Skip in blowouts (score gap > IBB_MAX_SCORE_GAP).
      - Allowed in super-innings (normal extra-inning baseball).
    """
    if not getattr(cfg, "IBB_ENABLE", True):
        return False
    batter = state.current_batter
    if batter is None:
        return False
    # 1B occupied — walking just loads bases / adds a runner.
    if state.bases[0] is not None:
        return False
    # No runners and 2 outs — nothing at stake, just pitch.
    if state.outs >= 2 and state.runner_count == 0:
        return False
    # Blowout — let the starters work.
    score_gap = abs(state.score.get("visitors", 0) - state.score.get("home", 0))
    if score_gap > cfg.IBB_MAX_SCORE_GAP:
        return False

    bgs = state.bgs(batter.player_id)
    pa  = int(bgs.get("pa", 0))
    h   = int(bgs.get("h", 0))

    # Hot-streak factor — only meaningful with at least 2 PAs of evidence.
    hot = 0.0
    if pa >= 2:
        avg = h / pa
        hot = max(0.0, avg - cfg.IBB_AVG_FLOOR) * cfg.IBB_HOT_SCALE
        if h >= cfg.IBB_HOT_HITS_THRESHOLD:
            hot += cfg.IBB_HOT_HITS_BONUS

    # Elite-skill factor — even on a 0-fer day, a true elite bat in a
    # spot you can't afford to lose still earns an IBB consideration.
    skill = float(getattr(batter, "skill", 0.5) or 0.5)
    elite = max(0.0, skill - cfg.IBB_SKILL_FLOOR) * cfg.IBB_SKILL_SCALE

    # Leverage: late in the half + runners in scoring position weigh
    # heaviest. With no RISP we still allow some IBB (e.g., walk the
    # cleanup hitter to face the 5-spot) but at a reduced rate.
    late = state.outs / 27.0
    risp = 1.0 if state.runners_in_scoring_position else 0.0
    leverage = late * (0.4 + 0.6 * risp)

    # Fielding-team manager persona. Falls back to neutral when the
    # field isn't populated (legacy DBs).
    agg = float(getattr(state.fielding_team, "mgr_ibb_aggression", 0.5) or 0.5)

    p = cfg.IBB_BASE_PROB + (hot + elite + leverage) \
                          * (cfg.IBB_AGG_FLOOR + cfg.IBB_AGG_SCALE * agg)
    p = max(0.0, min(cfg.IBB_MAX_PROB, p))

    if p <= 0.0:
        return False
    if rng is None:
        import random as _r
        roll = _r.random()
    else:
        roll = rng.random()
    return roll < p


def _legacy_should_insert_joker(state: GameState) -> Optional[Player]:
    """
    Phase 8 §4.6 heuristic: select the joker whose archetype fits the situation.

    Evaluation order (see _needed_archetype for details):
      1. power   — batting team down ≥ JOKER_POWER_DEFICIT with outs remaining.
      2. speed   — corners (1B+3B, 2B empty, exactly 1 out).
      3. contact — runners in scoring position (2B or 3B occupied).

    Eligibility (§2.3):
      - Joker must be in jokers_available (removed after use each half).
      - Each physical joker bats at most once per half (jokers_used_this_half).
      - No cross-archetype fallback: if the required archetype joker is already
        used, no other joker fires for that situation.

    Returns the joker Player to insert, or None when no situation applies.
    """
    if state.is_super_inning:
        return None
    team = state.batting_team
    if not team.jokers_available:
        return None

    # Filter to available (not-yet-used-this-half) jokers.
    available = [j for j in team.jokers_available
                 if j.player_id not in team.jokers_used_this_half]
    if not available:
        return None

    # Cap: do not insert more than JOKER_MAX_PER_HALF jokers per team per half.
    if len(team.jokers_used_this_half) >= cfg.JOKER_MAX_PER_HALF:
        return None

    archetype = _needed_archetype(state)
    if archetype is None:
        return None

    typed = [j for j in available if getattr(j, "archetype", "") == archetype]
    if not typed:
        # Required archetype unavailable (already used this half) — skip.
        return None
    return max(typed, key=lambda j: j.skill)


def _manager_hook_check(state: GameState) -> bool:
    """Manager-discretion early hook: pull a pitcher who's getting tagged.

    Checked BEFORE fatigue thresholds. Returns True only when the spell is
    going badly enough that even a workhorse manager would consider a change.
    The decision scales with the fielding team's manager persona:

      - quick_hook (0..1) — lower run threshold for pulling.
      - leverage_aware  — extra pull pressure when the game is close.
      - bullpen_aggression — willingness to pull early in the half.

    A neutral manager (0.5 across the board) hooks at ~5 runs in spell.
    A high-quick_hook manager hooks at ~3. A patient one waits for ~7.
    """
    spell_runs = int(getattr(state, "pitcher_runs_this_spell", 0) or 0)
    spell_hits = int(getattr(state, "pitcher_h_this_spell", 0) or 0)
    bf         = int(getattr(state, "pitcher_spell_count", 0) or 0)
    # Need a meaningful sample before considering a hook. Two batters into
    # a spell isn't a "blow up" yet — give the pitcher a chance.
    if bf < 4:
        return False

    fielding = state.fielding_team
    quick   = float(getattr(fielding, "mgr_quick_hook", 0.5))
    bullpen = float(getattr(fielding, "mgr_bullpen_aggression", 0.5))
    lev_aw  = float(getattr(fielding, "mgr_leverage_aware", 0.5))

    # Base run threshold: 8 (very patient) → 2 (very quick).
    run_threshold = max(2, round(8 - 6 * quick))

    # Leverage-aware managers hook earlier when the game is still close.
    # _batting_deficit returns runs the BATTING team trails by — i.e. positive
    # means the fielding team leads. We want to hook earlier in close/tied
    # games and hold longer in blowouts.
    margin = -_batting_deficit(state)        # +ve = fielding team leads
    if abs(margin) <= 3:
        run_threshold -= round(2 * lev_aw)   # close game → quicker
    elif abs(margin) >= 8:
        run_threshold += 2                   # blowout → leave him in
    run_threshold = max(2, run_threshold)

    # Bullpen-aggressive managers also pull on a baserunner pile-up
    # (lots of hits this spell, even if runs haven't all scored yet).
    hit_threshold = max(4, round(10 - 6 * bullpen))

    # The actual call: blew up by runs, OR is bleeding hits and the manager
    # is willing to use the pen.
    if spell_runs >= run_threshold:
        return True
    if spell_hits >= hit_threshold and bullpen >= 0.5:
        return True
    return False


def should_change_pitcher(state: GameState) -> bool:
    """
    Trigger a pitching change.

    Two layers, evaluated in order:
      1. Manager-discretion early hook (`_manager_hook_check`) — pulls a
         pitcher who's getting tagged, gated by the fielding team's manager
         persona (quick_hook / leverage_aware / bullpen_aggression).
      2. Fatigue thresholds (the original logic) — emergent role-aware
         pulls based on pitcher stamina + spell length.

    Roles are derived LIVE from the current pitcher's Stamina rating —
    no stored `pitcher_role` tag is consulted.

    First-spell role mapping (the "starter"):
      - stamina >= WORKHORSE_STAMINA_THRESHOLD (0.62)
            → workhorse pull thresholds (deepest stints)
      - stamina <= OPENER_STAMINA_THRESHOLD    (0.40)
            → opener pull thresholds (pull fast, then committee)
      - else → classical SP (moderate stints)

    Subsequent spells (relief context) use RELIEVER_CHANGE_BASE/SCALE
    regardless of stamina — the spell is already a relief appearance.

    Threshold = max(base, base + round(pitcher_skill * scale))
    """
    pitcher = state.get_current_pitcher()
    if pitcher is None:
        return False

    # Layer 1: manager discretion. A skipper with a quick hook will pull a
    # starter getting torched well before fatigue would force the change.
    if _manager_hook_check(state):
        return True

    # Detect "this is a relief appearance" by checking spell_log: if any
    # previous spell exists in the current half, we're past the SP.
    in_relief = any(
        rec.half == state.half
        for rec in state.spell_log
    )

    # Comfortable-lead rest: when the pitcher's team is well ahead, pull the
    # STARTER to rest him rather than ride a meaningless complete game in a
    # laugher. Gated so it only fires once the lead is decisive AND he's banked
    # real work (enough outs that a reliever fits). Relievers in a blowout keep
    # mopping up — this targets the first spell only.
    if not in_relief:
        fld_lead = (state.score.get(state.fielding_team.team_id, 0)
                    - state.score.get(state.batting_team.team_id, 0))
        if (fld_lead >= int(getattr(cfg, "BLOWOUT_PULL_LEAD", 10))
                and state.outs >= int(getattr(cfg, "BLOWOUT_PULL_MIN_OUTS", 12))):
            return True

    stamina = float(getattr(pitcher, "stamina", pitcher.pitcher_skill) or 0.5)

    if in_relief:
        # Relief appearance — short-burst thresholds.
        base  = cfg.RELIEVER_CHANGE_BASE
        scale = cfg.RELIEVER_CHANGE_SCALE
        threshold = max(base, base + round(pitcher.pitcher_skill * scale))
        return state.pitcher_spell_count >= threshold

    # First-spell ("starter") — derive emergent role from stamina.
    if stamina >= cfg.WORKHORSE_STAMINA_THRESHOLD:
        # Workhorse: ride deep into the half.
        base  = cfg.WORKHORSE_CHANGE_BASE
        scale = cfg.WORKHORSE_CHANGE_SCALE
        threshold = max(base, base + round(pitcher.pitcher_skill * scale))
        if state.pitcher_spell_count < threshold:
            return False
        # Even past threshold, only change if a reliever can come in
        # (i.e. the half is "late enough"). Otherwise let the SP keep going.
        if state.outs < cfg.RELIEVER_ENTRY_OUTS_MIN:
            # Past threshold but still early — extend the SP rather than
            # burn an early reliever. Pull only if blown out (≥ 8 runs
            # this spell), which cuts off true disasters.
            return state.pitcher_runs_this_spell >= 8
        return True

    if stamina <= cfg.OPENER_STAMINA_THRESHOLD:
        # Opener: pull after a short stint, let the committee take over.
        # No "must wait until late" guard — that's exactly what makes
        # this a viable strategy for stamina-poor staffs.
        base  = cfg.OPENER_CHANGE_BASE
        scale = cfg.OPENER_CHANGE_SCALE
        threshold = max(base, base + round(pitcher.pitcher_skill * scale))
        return state.pitcher_spell_count >= threshold

    # Classical SP — moderate stint, no late-relief guard.
    base  = cfg.PITCHER_CHANGE_BASE
    scale = cfg.PITCHER_CHANGE_SCALE
    threshold = max(base, base + round(pitcher.pitcher_skill * scale))
    return state.pitcher_spell_count >= threshold


# Crew-role codes (mirror of o27v2.rotation; kept inline so the engine has
# no upward dependency on the o27v2 layer). HM Helms / 1C First Change /
# 2C Second Change / BO Bosun / SK Skidder / AN Anchor / PI Pilot.
def _preferred_relief_roles(outs: int) -> tuple[str, ...]:
    """Crew roles to prefer for a relief call at this out count (0..26),
    port-bound. Mirrors o27v2.rotation.preferred_relief_roles — keep the two
    in sync. The Helms steers the start and is not a relief option."""
    if outs >= 24:
        return ("PI", "AN")          # into port — Pilot, Anchor
    if outs >= 19:
        return ("AN", "PI", "SK")    # late hold — Anchor, Pilot, Skidder
    if outs >= 12:
        return ("SK", "2C", "BO")    # rough patch — Skidder, 2nd Change, Bosun
    if outs >= 6:
        return ("2C", "1C", "BO")    # middle watch
    return ("1C", "BO")              # first change / bulk after a short Helms


def pick_new_pitcher(state: GameState) -> Optional[Player]:
    """
    Task #65: derive the next pitcher's role LIVE from each candidate's
    current attributes (`stamina` and `pitcher_skill` aka Stuff) — no
    stored role tag is read.

    Rule of thumb (per pitcher, evaluated each appearance):
      - outs >= 19 (late innings / closer)   → max Stuff
      - outs in 10..18 (middle relief)       → max Stuff, with mid-range
                                               Stamina preferred so true
                                               workhorses are saved for
                                               starts
      - outs <  10 (long relief / spot start)→ max Stamina

    Falls back to whatever pitcher is left when a bucket is thin. Position
    players and jokers are never used as relievers.
    """
    fielding   = state.fielding_team
    current_id = state.current_pitcher_id
    restricted: set = set()

    already_pitched = {
        r.pitcher_id for r in state.spell_log if r.half == state.half
    }

    pitcher_candidates = [
        p for p in fielding.roster
        if p.is_pitcher
        and p.player_id != current_id
        and p.player_id not in restricted
        and p.player_id not in already_pitched
        and fielding.is_available(p.player_id)
    ]

    outs = getattr(state, "outs", 0) or 0

    # Emergency PP-pitcher path: in a true blowout, a strong-arm position
    # player can take the mound to absorb the last few outs and preserve
    # the bullpen. Tight gating per user direction: late in the half AND
    # massive deficit. Triggers only when the FIELDING team is down big
    # (i.e. they're losing while their bullpen is getting torched).
    fielding_id   = fielding.team_id
    fielding_score = state.score.get(fielding_id, 0)
    other_id   = "home" if fielding_id == "visitors" else "visitors"
    batting_score = state.score.get(other_id, 0)
    deficit = batting_score - fielding_score   # how much we trail by
    outs_left = max(0, 27 - outs)
    if (deficit >= cfg.PP_PITCH_DEFICIT_MIN
            and outs_left <= cfg.PP_PITCH_OUTS_LEFT_MAX
            and outs_left > 0):
        # Find the position player with the highest arm rating who isn't
        # already the current pitcher. Must clear the arm threshold.
        pp_candidates = [
            p for p in fielding.roster
            if not p.is_pitcher
            and p.player_id != current_id
            and float(getattr(p, "arm", 0.5) or 0.5) >= cfg.PP_PITCH_ARM_MIN
        ]
        if pp_candidates:
            return max(pp_candidates, key=lambda pl: float(getattr(pl, "arm", 0.5) or 0.5))

    # Hard rest filter: pitchers who appeared in the last day or two are
    # de-prioritized from relief eligibility. We use a tier system mirroring
    # the SP picker — start with the most rested pool and broaden only when
    # nobody qualifies. This is what stops a single arm from being the
    # manager's "everyday closer" — the rotation has to cycle.
    for min_rest in (3, 2, 1):
        rested = [
            p for p in pitcher_candidates
            if int(getattr(p, "days_rest", 99) or 99) >= min_rest
        ]
        if rested:
            pitcher_candidates = rested
            break

    # Crew-role preference (canonical default + live override). Each moment
    # of the 27-out voyage has preferred crew roles (source of truth:
    # o27v2.rotation.preferred_relief_roles — duplicated here as a tiny
    # mapping so the engine stays independent of the o27v2 layer). We scope
    # the rested candidates to the role(s) that fit this moment; if no
    # role-matched arm is rested/available, we fall through to the full pool
    # so fatigue/Stuff/matchup still decide — the role never traps a call.
    preferred = _preferred_relief_roles(outs)
    if preferred and any(getattr(p, "pitcher_role", "") for p in pitcher_candidates):
        scoped = [p for p in pitcher_candidates
                  if getattr(p, "pitcher_role", "") in preferred]
        if scoped:
            pitcher_candidates = scoped

    def _stuff(p: Player) -> float:
        return float(getattr(p, "pitcher_skill", 0.5) or 0.5)

    def _stamina(p: Player) -> float:
        # Fall back to pitcher_skill when stamina hasn't been hydrated
        # (e.g. legacy DB rows pre-Task-#65).
        s = getattr(p, "stamina", None)
        return float(s if s is not None else _stuff(p))

    def _rest_penalty(p: Player) -> float:
        """Multiplicative penalty applied to a pitcher's score based on
        recent workload. Pitched yesterday with a heavy load → strong
        penalty; pitched 3+ days ago → no penalty. Identity at default
        days_rest=99 / pitch_debt=0.
        """
        days_rest = int(getattr(p, "days_rest", 99) or 99)
        pitch_debt = int(getattr(p, "pitch_debt", 0) or 0)
        # Days-rest penalty: 0d → -0.30, 1d → -0.15, 2d → -0.05, 3d+ → 0.
        if days_rest <= 0:
            rp = 0.30
        elif days_rest == 1:
            rp = 0.15
        elif days_rest == 2:
            rp = 0.05
        else:
            rp = 0.0
        # Heavy debt adds another 0.0–0.15 on top.
        # 100+ pitches in last 5 days → -0.15.
        rp += min(0.15, pitch_debt / 100.0 * 0.15)
        return rp

    # ─── Pitch-type matchup awareness ──────────────────────────────────
    # The "modern manager" reads the next chunk of the batting lineup
    # and prefers an arm whose repertoire matches the threat profile:
    #   * high-Power / low-Eye lineup ⇒ favor breaking-ball-heavy arms
    #     (high k_delta) and HR-suppressing pitches (negative
    #     hard_contact_shift)
    #   * LHB-heavy lineup ⇒ favor opposite_heavy / cutter / vulcan
    # Weighting is scaled by mgr_platoon_aggression so a dead-ball
    # traditionalist barely consults the matchup while a sabermetric
    # maximalist leans hard on it. Capped at ±0.12 so the matchup never
    # overrides a meaningful Stuff/Stamina/rest gap.
    batting   = state.batting_team
    lineup    = getattr(batting, "lineup", None) or getattr(batting, "roster", [])
    # Look at the next ~3 batters (the threat coming up); fall back to
    # the whole lineup if upcoming_idx is unavailable.
    next_idx  = int(getattr(state, "batter_idx", 0) or 0)
    upcoming  = []
    if lineup:
        for i in range(3):
            upcoming.append(lineup[(next_idx + i) % len(lineup)])
    n = len(upcoming) or 1
    avg_power = sum(float(getattr(b, "power", 0.5) or 0.5) for b in upcoming) / n
    avg_eye   = sum(float(getattr(b, "eye",   0.5) or 0.5) for b in upcoming) / n
    pct_l = (sum(1 for b in upcoming if getattr(b, "bats", "") == "L") / n) if upcoming else 0.0
    power_dev = avg_power - 0.5
    eye_dev   = 0.5 - avg_eye

    mgr_platoon = float(getattr(fielding, "mgr_platoon_aggression", 0.5) or 0.5)
    matchup_weight = 0.12 * mgr_platoon   # 0 (no awareness) … 0.12 (full)

    def _matchup_bonus(p: Player) -> float:
        repertoire = getattr(p, "repertoire", None) or []
        if not repertoire or matchup_weight <= 0:
            return 0.0
        try:
            from o27 import config as _ecfg
            catalog = _ecfg.PITCH_CATALOG
        except Exception:
            return 0.0
        total_w = sum(float(getattr(e, "usage_weight", 0.0) or 0.0) for e in repertoire) or 1.0
        bonus = 0.0
        for entry in repertoire:
            meta = catalog.get(getattr(entry, "pitch_type", ""), {})
            if not meta:
                continue
            usage_w = float(getattr(entry, "usage_weight", 0.0) or 0.0) / total_w
            quality = float(getattr(entry, "quality", 0.5) or 0.5)
            # K-driving pitches help vs high-Power / low-Eye lineups.
            k_d = float(meta.get("k_delta", 0.0) or 0.0)
            # HR-suppressing pitches (negative hard_contact_shift) help vs Power.
            hcs = float(meta.get("hard_contact_shift", 0.0) or 0.0)
            # Opposite_heavy / cutter pitches help vs same-side-heavy lineup.
            platoon_mode  = meta.get("platoon_mode", "neutral")
            platoon_scale = float(meta.get("platoon_scale", 1.0) or 1.0)
            pitch_score = (
                max(0.0, k_d) * (power_dev + eye_dev) * 2.0
              - hcs * power_dev * 1.5
            )
            if platoon_mode == "opposite_heavy":
                # Bonus vs whichever side is over-represented.
                same_side_pct = pct_l if getattr(p, "throws", "R") == "L" else (1.0 - pct_l)
                pitch_score += (0.5 - same_side_pct) * platoon_scale * 0.10
            pitch_score *= quality
            bonus += pitch_score * usage_w
        # Clip to ±matchup_weight.
        return max(-matchup_weight, min(matchup_weight, bonus * matchup_weight * 2.0))

    def _score(p: Player) -> float:
        if outs >= 19:
            # Late / closer — pure Stuff. Tired arms get penalized.
            base = _stuff(p) - _rest_penalty(p)
        elif outs >= 10:
            # Middle relief — Stuff dominates, but penalize the highest-
            # Stamina arms so they remain available for starts.
            base = _stuff(p) - 0.25 * max(0.0, _stamina(p) - 0.55) - _rest_penalty(p)
        else:
            # Long relief / spot start — pure Stamina, rest-adjusted.
            base = _stamina(p) - _rest_penalty(p)
        return base + _matchup_bonus(p)

    if pitcher_candidates:
        return max(pitcher_candidates, key=_score)

    # No fresh arm available — re-allow any non-restricted pitcher (even
    # one already pulled this half) before falling back further.
    fallback_pitchers = [
        p for p in fielding.roster
        if p.is_pitcher
        and p.player_id != current_id
        and p.player_id not in restricted
        and not getattr(p, "is_joker", False)
    ]
    if fallback_pitchers:
        return max(fallback_pitchers, key=_score)

    return None


def _is_joker(player) -> bool:
    """True if this player is one of the team's 3 jokers.

    Jokers are a finite tactical resource — 3 per team, fixed
    pre-game, in `jokers_available` (NOT in the batting order). They
    can be inserted as a pinch-hitter via batter_override for any PA
    where they aren't currently on base, but they CANNOT be subbed
    out (pulled from the joker pool or pinch-run for when on base).
    This helper is the single source of truth for that check.
    """
    if player is None:
        return False
    return (getattr(player, "roster_slot", "") == "joker"
            or getattr(player, "game_position", "") == "J")


def should_pinch_hit(state: GameState, rng=None) -> Optional[Player]:
    """Pinch-hit decision routed through the unified leverage trigger.

    Item 4 migration: the old multi-path logic (skill upgrade + platoon
    upgrade, separate probability rolls) collapses to a single decision:

      1. Build the candidate pool (bench bats, available, hit-capable).
      2. Score each via score_substitution(kind="pinch_hit").
      3. Pick the best and fire iff score >= substitution_threshold(team).

    The handedness/platoon advantage is now baked into the matchup
    factor of score_substitution. The persona-derived threshold absorbs
    the legacy probability rolls.

    Jokers are NOT in the batting order — they live in jokers_available
    as a tactical pinch-hit pool. The current_batter at any PA is one
    of the 9 lineup spots (8 fielders + SP), so this function never
    targets a joker as the out_player.
    """
    batter = state.current_batter
    team   = state.batting_team

    # Lineup-integrity gate: the manager may not pinch-hit until the starting
    # fielded lineup has batted through once (lineup_cycle_number >= 1).
    # Mirrors should_swap_offensive_for_defense. Forced injury subs bypass
    # this — they call the executor directly, not this decider.
    if team.lineup_cycle_number < 1:
        return None

    # Candidate pool: non-pitchers on the roster who aren't already in the
    # lineup AND haven't been subbed out earlier in the game (one-way
    # invariant — pinch hitters can't pinch-hit a second time). Jokers
    # are EXCLUDED — they're a separate tactical pool deployed only via
    # the joker insertion mechanic (batter_override), not as regular
    # pinch hitters.
    lineup_ids = {p.player_id for p in team.lineup}
    candidates = [
        p for p in team.roster
        if not p.is_pitcher
        and p.player_id not in lineup_ids
        and team.is_available(p.player_id)
        and not _is_joker(p)
        and bool(getattr(p, "role_hit", True))
    ]
    if not candidates:
        return None

    threshold = substitution_threshold(team)
    best_score = 0.0
    best_cand: Optional[Player] = None
    for cand in candidates:
        s = score_substitution(state, cand, "pinch_hit", batter)
        if s > best_score:
            best_score = s
            best_cand = cand

    if best_cand is not None and best_score >= threshold:
        return best_cand

    # Comfortable-lead rest ("garbage time"): when the batting team is well
    # ahead and the order has already turned a couple of times, rotate a fresh
    # bat in for the regular due up so the bench gets run and the starters rest.
    # Low-leverage by design — this is NOT a leverage upgrade (those returned
    # above); it's the context-dependent "we're up 20, empty the bench" path.
    team_lead = (state.score.get(team.team_id, 0)
                 - state.score.get(state.fielding_team.team_id, 0))
    if (team_lead >= int(getattr(cfg, "BLOWOUT_REST_LEAD", 10))
            and team.lineup_cycle_number >= int(getattr(cfg, "BLOWOUT_REST_MIN_CYCLE", 2))):
        return max(candidates, key=lambda p: float(getattr(p, "skill", 0.5) or 0.5))
    return None


def defensive_sub(
    state: GameState,
    player_out: Player,
    player_in: Player,
    injury: bool = False,
) -> list:
    """O27 defensive substitution, called by the FIELDING team's manager.

    O27 treats the batting order as a fixed lineup card: both teams submit
    a card at first pitch and that is the order they bat all game,
    *regardless of what happens defensively*. A tactical defensive sub
    therefore swaps the GLOVE on the field but does NOT change the batting
    order — the displaced starter is "not out" (think of the substitute as
    a nickel fielder who only takes the field) and keeps batting in his slot
    the whole game. This is the default path.

    `injury=True` is the one exception: an injured fielder is a genuine
    "out", so his replacement takes both the field position AND his
    lineup-card slot, and the injured player is retired one-way.
    """
    fielding = state.fielding_team
    if player_out not in fielding.lineup:
        return [f"  [MANAGER ERROR] {player_out.name} not in fielding lineup."]
    idx = fielding.lineup.index(player_out)
    out_pos = (getattr(player_out, "game_position", "")
               or getattr(player_out, "position", "") or "")

    # Move team defense by the real marginal value of this glove swap at the
    # vacated position — talent and fit decide, so it can help or hurt. Keyed
    # on the player's canonical position to match the game-start computation.
    _defense.apply_sub_to_team_defense(
        fielding, player_out, player_in,
        getattr(player_out, "position", "") or out_pos,
    )

    if injury:
        # Real exit: the replacement inherits the glove and the batting slot.
        fielding.lineup[idx] = player_in
        if (not getattr(player_in, "game_position", "")) and out_pos:
            player_in.game_position = out_pos
        log = [
            f"  DEFENSIVE SUB: {player_in.name} replaces {player_out.name} "
            f"in the field (and takes their lineup slot)."
        ]
        state.events.append({
            "type": "defensive_sub",
            "team_id": fielding.team_id,
            "out_id":  player_out.player_id,
            "in_id":   player_in.player_id,
        })
        _log_substitution(
            state,
            kind="pinch_field",
            team_id=fielding.team_id,
            in_player_id=player_in.player_id,
            out_player_id=player_out.player_id,
            lineup_index=idx,
            reason="defensive_sub_injury",
        )
        return log

    # Field-only tactical sub: the batting order (lineup card) is untouched.
    if out_pos:
        player_in.game_position = out_pos
    fielding.field_replacements[player_out.player_id] = player_in
    log = [
        f"  DEFENSIVE SUB: {player_in.name} takes the field for "
        f"{player_out.name}{(' at ' + out_pos) if out_pos else ''} — "
        f"{player_out.name} keeps his spot in the batting order."
    ]
    state.events.append({
        "type": "defensive_sub",
        "team_id": fielding.team_id,
        "out_id":  player_out.player_id,
        "in_id":   player_in.player_id,
    })
    _log_substitution(
        state,
        kind="pinch_field",
        team_id=fielding.team_id,
        in_player_id=player_in.player_id,
        out_player_id=player_out.player_id,
        lineup_index=idx,
        reason="defensive_sub",
        one_way=False,
    )
    return log


def pinch_run(state: GameState, base_idx: int, runner_in: Player) -> list[str]:
    """Replace the runner at `base_idx` (0=1B, 1=2B, 2=3B) with `runner_in`.

    The pinch runner takes the original runner's lineup slot — when their
    spot comes up to bat, the PR is the one batting. Standard MLB rules.
    Emits a 'pinch_runner' event the renderer captures for box-score
    entry-type tagging.
    """
    if base_idx not in (0, 1, 2):
        return [f"  [PINCH RUN ERROR] base_idx {base_idx} out of range."]
    out_id = state.bases[base_idx]
    if out_id is None:
        return [f"  [PINCH RUN ERROR] no runner at "
                f"{'1B 2B 3B'.split()[base_idx]}."]
    # A Walk-Back bonus runner can't be pinch-run for: he's a free post-HR
    # runner, and swapping him would needlessly burn a bench player and pull
    # the HR-hitter's bat from the lineup. The rule simply disallows it.
    if out_id in state.walk_back_runner_ids:
        return [f"  [PINCH RUN] Walk-Back bonus runner cannot be replaced — "
                f"the HR-hitter stays on the bag."]
    batting = state.batting_team
    out_player = batting.get_player(out_id) if hasattr(batting, "get_player") else None
    # Replace in lineup: PR takes the outgoing runner's slot.
    if out_player is not None and out_player in batting.lineup:
        idx = batting.lineup.index(out_player)
        batting.lineup[idx] = runner_in
    state.bases[base_idx] = runner_in.player_id
    out_name = out_player.name if out_player else out_id
    log = [f"  PINCH RUN: {runner_in.name} replaces {out_name} at "
           f"{'1B 2B 3B'.split()[base_idx]}."]
    state.events.append({
        "type":    "pinch_runner",
        "in_id":   runner_in.player_id,
        "in_name": runner_in.name,
        "out_id":  out_id,
        "base_idx": base_idx,
    })
    _log_substitution(
        state,
        kind="pinch_run",
        team_id=batting.team_id,
        in_player_id=runner_in.player_id,
        out_player_id=out_id,
        reason="pinch_runner",
    )
    return log


def should_pinch_run(state: GameState, rng=None) -> Optional[dict]:
    """Pinch-runner decision routed through the unified leverage trigger.

    Item 4 migration: the old "late + close + slow runner + fast bench"
    multi-gate logic collapses into a single score_substitution call
    against the slowest baserunner. Critical structural gates preserved:

      - Must be on-base (PR is by definition a baserunner swap).
      - No PR in super-innings.

    Score factors via score_substitution(kind="pinch_run") capture the
    leverage gates: late_arc_f handles "late game", score_gap_f handles
    "close game", upgrade_f handles the speed delta.

    Returns {'base_idx': int, 'runner_in': Player} or None.
    """
    batting = state.batting_team
    # Lineup-integrity gate: no pinch-running until the starting lineup has
    # batted through once. Forced injury subs bypass this (executor path).
    if batting.lineup_cycle_number < 1:
        return None
    # PR requires a runner on base — the brief explicitly scopes PR to
    # on-base situations.
    if not any(b is not None for b in state.bases):
        return None

    # Pick the slowest runner on base as the candidate-to-replace.
    # Jokers fill the DH role and can't be subbed — skip them when
    # they're on base; the manager has to live with the joker's speed.
    out_idx = None
    out_runner: Optional[Player] = None
    slowest_speed = 1.0
    for i, pid in enumerate(state.bases):
        if pid is None:
            continue
        p = batting.get_player(pid) if hasattr(batting, "get_player") else None
        if p is None or _is_joker(p):
            continue
        # Walk-Back bonus runners are not eligible to be pinch-run for.
        if pid in state.walk_back_runner_ids:
            continue
        s = float(getattr(p, "speed", 0.5) or 0.5)
        if s < slowest_speed:
            slowest_speed = s
            out_idx = i
            out_runner = p
    if out_runner is None:
        return None

    # Bench candidates — non-pitchers not in lineup, not jokers, available.
    in_lineup = set(p.player_id for p in batting.lineup)
    joker_ids = {j.player_id for j in batting.jokers_available}
    bench_pool = [p for p in batting.roster
                  if p.player_id not in in_lineup
                  and not getattr(p, "is_pitcher", False)
                  and p.player_id not in joker_ids
                  and batting.is_available(p.player_id)]
    if not bench_pool:
        return None

    threshold = substitution_threshold(batting)
    best_score = 0.0
    best_cand: Optional[Player] = None
    for cand in bench_pool:
        s = score_substitution(state, cand, "pinch_run", out_runner)
        if s > best_score:
            best_score = s
            best_cand = cand

    if best_cand is None or best_score < threshold:
        return None
    return {"base_idx": out_idx, "runner_in": best_cand}


def joker_to_field(state: GameState, joker: Player, player_out: Player) -> list[str]:
    """Send a joker out to take a fielding position from `player_out`. Rare —
    a defensive-only move under extreme tactical need.

    Field-only, per the lineup-card rule: the joker takes the *glove* at
    `player_out`'s position, but the batting order is untouched — `player_out`
    is "not out" and keeps his slot. Reduces the joker pool by 1 (the joker is
    now committed to the field) and moves team defense by the real value of the
    swap, which can help or hurt.
    """
    fielding = state.fielding_team
    if joker not in fielding.jokers_available:
        return [f"  [JOKER FIELD ERROR] {joker.name} not in joker pool."]
    if player_out not in fielding.lineup:
        return [f"  [JOKER FIELD ERROR] {player_out.name} not in lineup."]
    idx = fielding.lineup.index(player_out)
    out_pos = getattr(player_out, "game_position", "") or getattr(player_out, "position", "")

    # Move team defense by the marginal value of the joker's glove at the spot.
    _defense.apply_sub_to_team_defense(
        fielding, player_out, joker,
        getattr(player_out, "position", "") or out_pos,
    )

    fielding.jokers_available = [
        j for j in fielding.jokers_available if j.player_id != joker.player_id
    ]
    fielding.field_replacements[player_out.player_id] = joker
    # Stamp the joker with the position they took. Format: "J→SS" so the
    # box score signals "this row is a joker who's now playing SS".
    joker.game_position = f"J→{out_pos}" if out_pos else "J"
    log = [
        f"  JOKER TO FIELD: {joker.name} takes the field for {player_out.name} "
        f"at {out_pos or '?'} — {player_out.name} keeps his spot in the order. "
        f"{len(fielding.jokers_available)} jokers remaining."
    ]
    state.events.append({
        "type":      "joker_to_field",
        "team_id":   fielding.team_id,
        "joker_id":  joker.player_id,
        "joker_name": joker.name,
        "out_id":    player_out.player_id,
        "position":  out_pos,
    })
    _log_substitution(
        state,
        kind="joker",
        team_id=fielding.team_id,
        in_player_id=joker.player_id,
        out_player_id=player_out.player_id,
        lineup_index=idx,
        reason="joker_to_field",
        one_way=False,
    )
    return log


def should_joker_to_field(state: GameState, rng=None) -> Optional[dict]:
    """Joker-to-field is RARE. Only fires under extreme conditions:
    very late game, fielding team trailing badly, a joker has notably
    better defense at some position than the current fielder. Real-MLB
    base rate is functionally zero (no DH→position swaps); we keep this
    so the mechanic exists but barely fires."""
    if state.is_super_inning:
        return None
    if state.outs < 24:
        return None
    fielding = state.fielding_team
    if not getattr(fielding, "jokers_available", None):
        return None
    # Only consider if fielding team is trailing meaningfully (defensive
    # downgrade is justified when the game is already mostly lost).
    bat_role = "visitors" if state.half in ("top", "super_top") else "home"
    fld_role = "home" if bat_role == "visitors" else "visitors"
    score_diff = state.score.get(fld_role, 0) - state.score.get(bat_role, 0)
    if score_diff > -3:
        return None
    rng = rng or _local_rng()
    # Tiny base rate; barely fires.
    if rng.random() >= 0.005:
        return None
    # Pick the worst-glove fielder and check if any joker has materially
    # better defense at that position group.
    weakest = None
    weakest_score = 1.0
    for p in fielding.lineup:
        if getattr(p, "is_pitcher", False):
            continue
        gp = getattr(p, "game_position", "") or ""
        if gp in ("LF", "CF", "RF"):
            s = float(getattr(p, "defense_outfield", 0.5) or 0.5)
        elif gp in ("1B", "2B", "3B", "SS"):
            s = float(getattr(p, "defense_infield", 0.5) or 0.5)
        elif gp == "C":
            s = float(getattr(p, "defense_catcher", 0.5) or 0.5)
        else:
            continue
        if s < weakest_score:
            weakest_score = s
            weakest = p
    if weakest is None:
        return None
    # Joker with best fit at that position group.
    gp = weakest.game_position
    if gp in ("LF", "CF", "RF"):
        attr = "defense_outfield"
    elif gp == "C":
        attr = "defense_catcher"
    else:
        attr = "defense_infield"
    candidates = sorted(
        [j for j in fielding.jokers_available
         if _defense.is_eligible_at(j, gp)],
        key=lambda j: -float(getattr(j, attr, 0.5) or 0.5),
    )
    if not candidates:
        return None
    best = candidates[0]
    if float(getattr(best, attr, 0.5) or 0.5) <= weakest_score + 0.10:
        return None
    return {"joker": best, "player_out": weakest}


def _local_rng():
    import random as _r
    return _r.Random()


def should_defensive_sub(state: GameState, rng=None) -> Optional[dict]:
    """Defensive substitution by the FIELDING team — routed through the
    unified leverage trigger.

    Item 4 migration: the legacy "prob roll scaled by mgr_bench_usage +
    static 0.05 defense-edge" path collapses to per-candidate scoring
    against the worst-defense fielder. Critical structural gates kept:

      - Regulation half only (no super-innings).
      - The opposing (batting) order must have turned over once
        (lineup_cycle_number >= 1) so the defense holds its starters
        through the first trip — no first-cycle bench churn.
      - Catchers and DHs are protected (catcher arm is stamped on the
        team; DH has no defensive slot to upgrade).

    Returns {'player_out': Player, 'player_in': Player} or None.
    """
    # Lineup-integrity gate: hold defensive subs until the opposing (batting)
    # order has turned over once. Replaces the old `outs < 6` heuristic,
    # which could still fire before the order cycled.
    if state.batting_team.lineup_cycle_number < 1:
        return None

    # Timing gates — a defensive replacement is a late-inning lock-in, not an
    # early-game move. Hard floor in the opening outs of any game, then a
    # rarity window: before the late-game out, even a leverage-clearing sub
    # only fires on a small probability roll. (Super-innings are already
    # late by definition and skip the rarity roll.)
    if not state.is_super_inning:
        if state.outs < int(getattr(cfg, "DEFENSIVE_SUB_MIN_OUTS", 3)):
            return None
        if state.outs < int(getattr(cfg, "DEFENSIVE_SUB_LATE_OUT", 16)):
            r = rng or _local_rng()
            if r.random() >= float(getattr(cfg, "DEFENSIVE_SUB_EARLY_RATE", 0.05)):
                return None

    fielding = state.fielding_team

    # Identify the weakest-defense lineup spot (the candidate to-replace).
    # Excluded: pitchers (the catcher_arm is stamped on the team so a
    # catcher swap needs its own handling). Jokers aren't in the
    # batting lineup so they don't appear here.
    lineup = list(fielding.lineup)
    # Skip starters whose glove has already been covered by a field-only
    # defensive sub — re-covering the same slot would just churn.
    candidates_out = [
        pl for pl in lineup
        if not pl.is_pitcher
        and (getattr(pl, "position", "") not in ("C", "DH"))
        and pl.player_id not in fielding.field_replacements
    ]
    if not candidates_out:
        return None
    worst = min(
        candidates_out,
        key=lambda pl: float(getattr(pl, "defense", 0.5) or 0.5),
    )
    worst_pos = (getattr(worst, "game_position", "")
                 or getattr(worst, "position", "") or "")

    # Bench candidates: roster non-pitchers not currently in the lineup
    # AND not already substituted out AND not already deployed as a
    # field-only defensive replacement AND eligible to play the vacated
    # position. Jokers excluded — they're a separate tactical pool, not
    # defensive replacements.
    lineup_ids = {pl.player_id for pl in lineup}
    glove_ids = {p.player_id for p in fielding.field_replacements.values()}
    bench = [
        pl for pl in fielding.roster
        if not pl.is_pitcher
        and pl.player_id not in lineup_ids
        and pl.player_id not in glove_ids
        and fielding.is_available(pl.player_id)
        and not _is_joker(pl)
        and _defense.is_eligible_at(pl, worst_pos)
    ]
    if not bench:
        return None

    threshold = substitution_threshold(fielding)
    best_score = 0.0
    best_cand: Optional[Player] = None
    for cand in bench:
        s = score_substitution(state, cand, "pinch_field", worst)
        if s > best_score:
            best_score = s
            best_cand = cand

    if best_cand is None or best_score < threshold:
        return None
    return {"player_out": worst, "player_in": best_cand}


def should_swap_catcher(state: GameState, rng=None) -> Optional[dict]:
    """Rotate a tiring catcher out for a fresh one from the catching corps.

    No catcher squats for all 27 outs — as outs pile up his game-calling
    decays (see prob._catcher_gc_shift). When he's gassed and the bench holds a
    credible reserve catcher, the manager spends him. How a club prioritizes
    its catchers (start the best caller, hold a defender for the late innings)
    swings the back third of a game.

    Returns {'player_out': tired_catcher, 'player_in': fresh_catcher} or None.
    Inert for rosters that carry no reserve catcher (bench empty) — same as
    should_defensive_sub.
    """
    if state.is_super_inning or state.in_seconds_phase:
        return None
    if state.outs < getattr(cfg, "CATCHER_ROTATION_OUT_GATE", 6):
        return None
    fielding = state.fielding_team

    # Fatigue gate — only rotate once the current catcher is actually tiring.
    outs_caught = int(getattr(fielding, "catcher_outs_caught", 0) or 0)
    if outs_caught <= getattr(cfg, "CATCHER_FATIGUE_THRESHOLD", 18):
        return None

    # Current catcher = lineup's best defense_catcher non-pitcher.
    lineup = list(fielding.lineup)
    in_lineup_catchers = [pl for pl in lineup if not pl.is_pitcher]
    if not in_lineup_catchers:
        return None
    current = max(in_lineup_catchers,
                  key=lambda pl: float(getattr(pl, "defense_catcher", 0.5) or 0.5))

    # Reserve catcher = best defense_catcher among available bench (roster not
    # in lineup, non-pitcher, non-joker). Must be a credible catcher.
    lineup_ids = {pl.player_id for pl in lineup}
    bench = [
        pl for pl in fielding.roster
        if not pl.is_pitcher
        and pl.player_id not in lineup_ids
        and fielding.is_available(pl.player_id)
        and not _is_joker(pl)
    ]
    if not bench:
        return None
    reserve_catchers = [
        pl for pl in bench
        if float(getattr(pl, "defense_catcher", 0.5) or 0.5) >= 0.5
    ]
    if not reserve_catchers:
        return None  # no credible reserve catcher on the bench

    # Situational pick (RFC): protecting a lead (or tied) → defensive
    # specialist (glove + arm + game-calling); chasing → spark-plug bat
    # (the catcher's lineup spot matters more than the position).
    fs = int(state.score.get(fielding.team_id, 0) or 0)
    opp_id = "home" if fielding.team_id == "visitors" else "visitors"
    os_ = int(state.score.get(opp_id, 0) or 0)
    if fs - os_ < 0:
        fresh = max(reserve_catchers,
                    key=lambda pl: float(getattr(pl, "skill", 0.5) or 0.5)
                    + 0.3 * float(getattr(pl, "speed", 0.5) or 0.5))
        role = "spark plug"
    else:
        fresh = max(reserve_catchers,
                    key=lambda pl: float(getattr(pl, "defense_catcher", 0.5) or 0.5)
                    + float(getattr(pl, "arm", 0.5) or 0.5)
                    + float(getattr(pl, "game_calling", 0.5) or 0.5))
        role = "defensive"

    return {"player_out": current, "player_in": fresh, "role": role}


def should_swap_offensive_for_defense(state: GameState, rng=None) -> Optional[dict]:
    """First-batting team pre-stages a glove for its upcoming fielding half.

    Field-only, per the lineup-card rule: this upgrades a weak defender's
    GLOVE for the team's fielding half WITHOUT touching the batting order —
    it mirrors should_defensive_sub (worst card defender + best eligible bench
    glove) but operates on the first-batting team while it's still at bat.
    No more pulling the current batter and burning his slot.

    Gates: regulation only; first-batting team only; lineup cycled once.
    Returns {'player_out', 'player_in'} or None.
    """
    if state.is_super_inning:
        return None
    if (state.first_batting_team is None
            or state.batting_team is not state.first_batting_team):
        return None

    team = state.batting_team
    if team.lineup_cycle_number < 1:
        return None

    # Weakest-defense card slot not already covered (skip C / DH / pitcher).
    candidates_out = [
        pl for pl in team.lineup
        if not pl.is_pitcher
        and (getattr(pl, "position", "") not in ("C", "DH"))
        and pl.player_id not in team.field_replacements
    ]
    if not candidates_out:
        return None
    worst = min(candidates_out,
                key=lambda pl: float(getattr(pl, "defense", 0.5) or 0.5))
    worst_pos = (getattr(worst, "game_position", "")
                 or getattr(worst, "position", "") or "")

    lineup_ids = {pl.player_id for pl in team.lineup}
    glove_ids = {p.player_id for p in team.field_replacements.values()}
    bench = [
        pl for pl in team.roster
        if not pl.is_pitcher
        and pl.player_id not in lineup_ids
        and pl.player_id not in glove_ids
        and team.is_available(pl.player_id)
        and not _is_joker(pl)
        and _defense.is_eligible_at(pl, worst_pos)
    ]
    if not bench:
        return None

    threshold = substitution_threshold(team)
    best_score = 0.0
    best_cand: Optional[Player] = None
    for cand in bench:
        s = score_substitution(state, cand, "pinch_field", worst)
        if s > best_score:
            best_score = s
            best_cand = cand

    if best_cand is None or best_score < threshold:
        return None
    return {"player_out": worst, "player_in": best_cand}


def offensive_to_defensive_swap(state: GameState,
                                player_out: Player,
                                player_in: Player) -> list[str]:
    """Field-only execution of should_swap_offensive_for_defense: the glove is
    staged for the team's fielding half (team defense moves by the swap's
    value) but the batting order is untouched — player_out keeps his slot."""
    team = state.batting_team
    if player_out not in team.lineup:
        return [f"  [DEF SWAP ERROR] {player_out.name} not in lineup."]
    out_pos = (getattr(player_out, "game_position", "")
               or getattr(player_out, "position", "") or "")
    _defense.apply_sub_to_team_defense(
        team, player_out, player_in,
        getattr(player_out, "position", "") or out_pos,
    )
    if out_pos:
        player_in.game_position = out_pos
    team.field_replacements[player_out.player_id] = player_in
    if player_in in team.bench:
        team.bench.remove(player_in)
    idx = team.lineup.index(player_out)
    log = [
        f"  DEF SWAP: {player_in.name} will field for {player_out.name} at "
        f"{out_pos or '?'} — {player_out.name} keeps his spot in the order."
    ]
    _log_substitution(
        state,
        kind="pinch_field",
        team_id=team.team_id,
        in_player_id=player_in.player_id,
        out_player_id=player_out.player_id,
        lineup_index=idx,
        reason="tactical_def_swap",
        one_way=False,
    )
    return log


def should_phase_transition_swap(state: GameState, rng=None) -> Optional[list[dict]]:
    """Wholesale offensive→defensive unit swap at the phase boundary.

    The first-batting team, late in its offensive phase, swaps in a unit
    of defensive specialists from the bench for its weakest-defense
    regulars — one tactical move that re-tools the field before the team
    has to defend. Distinct from `should_swap_offensive_for_defense`
    (one player) and `should_defensive_sub` (fielding team, mid-defense).

    Structural gates:
      - Regulation half only (no super-innings).
      - First-batting team only (they still have to field).
      - Fires at most once per game (team.phase_swap_done).
      - Late offensive phase (outs >= 18) so it lands near the boundary.
      - Lineup must have cycled at least once.

    The number of swaps and the firing likelihood scale with
    mgr_platoon_aggression. Each swap must clear the per-candidate
    leverage threshold, so a low-aggression skipper rarely swaps a full
    unit while a platoon-heavy one re-tools several slots.

    Returns a list of {'player_out': Player, 'player_in': Player} (one
    entry per swap) or None.
    """
    if state.is_super_inning:
        return None
    if (state.first_batting_team is None
            or state.batting_team is not state.first_batting_team):
        return None
    team = state.batting_team
    if getattr(team, "phase_swap_done", False):
        return None
    if state.outs < 18:
        return None
    if team.lineup_cycle_number < 1:
        return None

    aggression = float(getattr(team, "mgr_platoon_aggression", 0.5) or 0.5)
    # Max unit size scales with platoon aggression: 0.0–0.33 → 1,
    # 0.33–0.66 → 2, 0.66+ → 3. The threshold check below still gates
    # each individual swap, so this is a ceiling, not a quota.
    max_swaps = 1 + int(aggression * 3)        # 1..3 (aggression in [0,1])
    max_swaps = max(1, min(3, max_swaps))

    # Bench: roster non-pitchers not in the lineup, still available, not jokers.
    lineup = list(team.lineup)
    lineup_ids = {pl.player_id for pl in lineup}
    bench = [
        pl for pl in team.roster
        if not pl.is_pitcher
        and pl.player_id not in lineup_ids
        and team.is_available(pl.player_id)
        and not _is_joker(pl)
    ]
    if not bench:
        return None

    # Candidate regulars to pull: non-pitcher, non-catcher, non-DH, weakest
    # defense first. Catcher arm is a team-level stat; DH has no glove.
    candidates_out = sorted(
        [pl for pl in lineup
         if not pl.is_pitcher
         and (getattr(pl, "position", "") not in ("C", "DH"))],
        key=lambda pl: float(getattr(pl, "defense", 0.5) or 0.5),
    )

    threshold = substitution_threshold(team)
    swaps: list[dict] = []
    used_in_ids: set = set()
    for out_pl in candidates_out:
        if len(swaps) >= max_swaps:
            break
        out_pos = (getattr(out_pl, "game_position", "")
                   or getattr(out_pl, "position", "") or "")
        best_score = 0.0
        best_cand: Optional[Player] = None
        for cand in bench:
            if cand.player_id in used_in_ids:
                continue
            # Only a glove eligible for that position can take it.
            if not _defense.is_eligible_at(cand, out_pos):
                continue
            s = score_substitution(state, cand, "pinch_field", out_pl)
            if s > best_score:
                best_score = s
                best_cand = cand
        if best_cand is not None and best_score >= threshold:
            swaps.append({"player_out": out_pl, "player_in": best_cand})
            used_in_ids.add(best_cand.player_id)

    return swaps or None


def phase_transition_swap(state: GameState, swaps: list[dict]) -> list[str]:
    """Apply a wholesale offensive→defensive unit swap for the batting team
    as it prepares to take the field.

    Field-only, per the lineup-card rule: each entry installs `player_in`'s
    glove at `player_out`'s position and moves team defense by the real value
    of the swap, but the batting order is untouched — the outgoing regulars
    are "not out" and keep their slots (they have already batted; the order
    carries into super-innings unchanged). Emits a single
    phase_transition_swap event carrying the full incoming/outgoing roster so
    the renderer can write one multi-player line.
    """
    team = state.batting_team
    applied: list[tuple[Player, Player]] = []
    for sw in swaps:
        player_out = sw.get("player_out")
        player_in  = sw.get("player_in")
        if player_out is None or player_in is None:
            continue
        if player_out not in team.lineup:
            continue
        if not team.is_available(player_in.player_id):
            continue
        idx = team.lineup.index(player_out)
        out_pos = (getattr(player_out, "game_position", "")
                   or getattr(player_out, "position", "") or "")
        # Move team defense by the marginal value of this glove at the spot.
        _defense.apply_sub_to_team_defense(
            team, player_out, player_in,
            getattr(player_out, "position", "") or out_pos,
        )
        if out_pos:
            player_in.game_position = out_pos
        team.field_replacements[player_out.player_id] = player_in
        if player_in in team.bench:
            team.bench.remove(player_in)
        _log_substitution(
            state,
            kind="pinch_field",
            team_id=team.team_id,
            in_player_id=player_in.player_id,
            out_player_id=player_out.player_id,
            lineup_index=idx,
            reason="phase_transition_swap",
            one_way=False,
        )
        applied.append((player_in, player_out))

    if not applied:
        return []

    team.phase_swap_done = True
    ins  = ", ".join(pi.name for pi, _ in applied)
    outs = ", ".join(po.name for _, po in applied)
    log = [f"  PHASE TRANSITION: {team.name} switches to its defensive "
           f"lineup: {ins} in for {outs}."]
    state.events.append({
        "type": "phase_transition_swap",
        "team_id": team.team_id,
        "in_ids":  [pi.player_id for pi, _ in applied],
        "out_ids": [po.player_id for _, po in applied],
    })
    return log


def _pitcher_bunt_difficulty(state: GameState) -> float:
    """How hard the current pitcher makes a clean bunt: 0 (easy) .. ~0.5
    (elite stuff + command). Feeds every bunt outcome roll."""
    p = state.get_current_pitcher()
    if p is None:
        return 0.0
    stuff   = float(getattr(p, "pitcher_skill", 0.5) or 0.5)
    command = float(getattr(p, "command", 0.5) or 0.5)
    return max(0.0, (0.5 * stuff + 0.5 * command) - 0.5)


def _roll_bunt_safe(rng, speed: float, bunt: float) -> float:
    """Shared beat-it-out / bunt-single probability from speed + bat control."""
    return max(0.0, cfg.SAC_BUNT_HIT_BASE
               + (speed - 0.5) * cfg.SAC_BUNT_HIT_SPEED_SCALE
               + (bunt - 0.5) * 0.20)


def _roll_sacrifice(rng, speed, bunt, pdiff, single_runner) -> dict:
    hit_p  = _roll_bunt_safe(rng, speed, bunt)
    lead_p = (max(0.0, cfg.SAC_LEAD_OUT_BASE - (bunt - 0.5) * cfg.BUNT_SKILL_EXEC_SCALE)
              + pdiff * cfg.BUNT_PITCHER_DIFFICULTY_SCALE) if single_runner else 0.0
    fail_p = max(0.0, cfg.SAC_BUNT_FAIL_RATE + pdiff * 0.10)
    r = rng.random()
    if r < hit_p:
        out = "hit"
    elif r < hit_p + lead_p:
        out = "lead_out"
    elif r < hit_p + lead_p + fail_p:
        out = "fail"
    else:
        out = "sacrifice"
    return {"type": "sac_bunt", "bunt_type": "sac", "outcome": out}


def _roll_drag(rng, speed, bunt, pdiff) -> dict:
    hit_p = max(0.0, cfg.DRAG_BUNT_HIT_BASE
                + (speed - 0.5) * 0.40 + (bunt - 0.5) * 0.30
                - pdiff * cfg.BUNT_PITCHER_DIFFICULTY_SCALE)
    out = "hit" if rng.random() < hit_p else "out_productive"
    return {"type": "sac_bunt", "bunt_type": "drag", "outcome": out}


def _roll_squeeze(rng, speed, bunt, pdiff) -> dict:
    suicide = rng.random() < cfg.SQUEEZE_SUICIDE_SHARE
    btype = "suicide" if suicide else "safety"
    if suicide:
        miss_p = max(0.02, cfg.SUICIDE_MISS_BASE
                     - (bunt - 0.5) * cfg.BUNT_SKILL_EXEC_SCALE
                     + pdiff * cfg.BUNT_PITCHER_DIFFICULTY_SCALE)
        if rng.random() < miss_p:
            return {"type": "sac_bunt", "bunt_type": btype, "outcome": "squeeze_miss"}
        out = ("squeeze_score_hit" if rng.random() < _roll_bunt_safe(rng, speed, bunt)
               else "squeeze_score")
        return {"type": "sac_bunt", "bunt_type": btype, "outcome": out}
    # Safety squeeze — runner only goes on a bunt down well enough.
    score_p = max(0.0, min(0.97, cfg.SAFETY_SQUEEZE_SCORE_BASE
                  + (bunt - 0.5) * cfg.BUNT_SKILL_EXEC_SCALE
                  - pdiff * cfg.BUNT_PITCHER_DIFFICULTY_SCALE))
    if rng.random() < score_p:
        out = ("squeeze_score_hit" if rng.random() < _roll_bunt_safe(rng, speed, bunt)
               else "squeeze_score")
    else:
        out = "squeeze_hold"
    return {"type": "sac_bunt", "bunt_type": btype, "outcome": out}


def should_bunt(state: GameState, rng=None) -> Optional[dict]:
    """Manager-driven bunt decision across four types.

    Returns a synthetic event dict
    ``{"type": "sac_bunt", "bunt_type": ..., "outcome": ...}`` when the
    manager calls a bunt, else None. The outcome is rolled here so the engine
    applies it directly without re-running the contact pipeline. Type is
    chosen by base state, then gated by outs / score / manager persona /
    batter power, and executed against bunt skill, speed, and the pitcher's
    difficulty:

      * Squeeze (runner on 3B) — suicide or safety. Highest priority.
      * Sacrifice (runner on 1B, optionally + 2B) — trade an out to advance.
      * Bunt-for-hit / drag (fast, low-power bat; great vs the infield shift).
    """
    if rng is None:
        import random as _r
        rng = _r.Random()
    batter = state.current_batter
    bases = state.bases
    on1, on2, on3 = bases[0] is not None, bases[1] is not None, bases[2] is not None
    speed = float(getattr(batter, "speed", 0.5) or 0.5)
    power = float(getattr(batter, "power", 0.5) or 0.5)
    bunt  = float(getattr(batter, "bunt", 0.5) or 0.5)
    team  = state.batting_team
    run_game = float(getattr(team, "mgr_run_game", 0.5))
    leverage = float(getattr(team, "mgr_leverage_aware", 0.5))
    pdiff = _pitcher_bunt_difficulty(state)

    v = state.score.get("visitors", 0)
    h = state.score.get("home", 0)
    bat_score, fld_score = (v, h) if state.half in ("top", "super_top") else (h, v)
    margin = fld_score - bat_score          # +ve = batting team trails
    lev_mult = 1.0 + (1.0 - leverage) * cfg.SAC_BUNT_LEVERAGE_DAMPER

    # --- Pitcher at bat: the classic sacrifice bunter ----------------------
    # O27 has no DH, so pitchers hit — and the weak-bat pitcher gives himself
    # up to move a runner far more readily than a position player would. He
    # doesn't drag (too slow) or squeeze (rarely asked to under pressure);
    # he lays down the standard sacrifice with a runner on and outs to spare.
    if getattr(batter, "is_pitcher", False):
        if on1 and state.outs < 24:
            p = cfg.PITCHER_SAC_BUNT_BASE_PROB * lev_mult
            if -2 <= margin <= 2:
                p *= 1.3                     # one-score game: manufacture a run
            elif margin <= -4:
                p *= 0.5                      # down big, let him swing
            if rng.random() < max(0.0, min(0.60, p)):
                return _roll_sacrifice(rng, speed, bunt, pdiff,
                                       single_runner=(on1 and not on2 and not on3))
        return None

    # --- Squeeze: runner on 3B, outs to spare, not a slugger ---------------
    if on3 and state.outs < 24 and power <= 0.62:
        sq_p = cfg.SQUEEZE_BASE_PROB * (run_game * 1.0) * lev_mult
        if -1 <= margin <= 1:
            sq_p *= 1.4                      # tied / one-run game, manufacture it
        elif margin <= -3:
            sq_p *= 0.4
        if rng.random() < max(0.0, min(0.12, sq_p)):
            return _roll_squeeze(rng, speed, bunt, pdiff)

    # --- Sacrifice: force runner on 1B (maybe +2B), weak hitter -------------
    if on1 and state.outs < 18 and power <= 0.55:
        bunt_p = (cfg.SAC_BUNT_BASE_PROB
                  * (run_game * cfg.SAC_BUNT_RUNGAME_SCALE / 0.5 if run_game > 0 else 0)
                  * lev_mult)
        if margin == 1 or margin == 2:
            bunt_p *= 1.5
        elif margin <= -3:
            bunt_p *= 0.3
        if rng.random() < max(0.0, min(0.20, bunt_p)):
            return _roll_sacrifice(rng, speed, bunt, pdiff,
                                   single_runner=(on1 and not on2 and not on3))

    # --- Bunt-for-hit / drag: fast, low power, bases empty or just 1B -------
    shift = getattr(state, "current_ab_shift_type", "none") == "infield"
    if (speed > cfg.DRAG_BUNT_SPEED_GATE and power <= 0.50
            and not on2 and not on3 and state.outs < 24):
        drag_p = cfg.DRAG_BUNT_BASE_PROB * (speed - 0.5) * 2.0 * (1.0 + run_game)
        if shift:
            drag_p += cfg.BUNT_AGAINST_SHIFT_BASE_PROB * (speed - 0.5) * 2.0
        if rng.random() < max(0.0, min(0.30, drag_p)):
            return _roll_drag(rng, speed, bunt, pdiff)

    return None


# ===========================================================================
# Declared Seconds
# ===========================================================================

def _opp_team(state: GameState):
    """Return the opposing team (the one not currently batting)."""
    return state.fielding_team


def _team_score_diff(state: GameState) -> int:
    """Runs by which the batting team leads (negative = trailing)."""
    bat = state.batting_team
    opp = _opp_team(state)
    return state.score.get(bat.team_id, 0) - state.score.get(opp.team_id, 0)


def _pitcher_fatigue_tier(pitcher) -> str:
    """Categorize pitcher's fatigue state: 'fresh' | 'near_cliff' | 'in_decay'."""
    if pitcher is None:
        return "fresh"
    stamina = float(getattr(pitcher, "stamina", getattr(pitcher, "pitcher_skill", 0.5)) or 0.5)
    # No live spell stats on the pitcher object — read off the state instead
    # via the calling helper. Conservative default if we can't tell.
    return "fresh"


def _pitcher_fatigue_tier_for_state(state: GameState, pitcher) -> str:
    """Tier the pitcher's fatigue using GameState spell counters.

    Maps onto the same role-derived thresholds used by should_change_pitcher
    so the declaration AI's "cliff" is calibrated to the same point at which
    the manager would otherwise pull the arm.
    """
    if pitcher is None:
        return "fresh"
    bf = int(getattr(state, "pitcher_spell_count", 0) or 0)
    stamina = float(getattr(pitcher, "stamina", getattr(pitcher, "pitcher_skill", 0.5)) or 0.5)
    if stamina >= cfg.WORKHORSE_STAMINA_THRESHOLD:
        base, scale = cfg.WORKHORSE_CHANGE_BASE, cfg.WORKHORSE_CHANGE_SCALE
    elif stamina <= cfg.OPENER_STAMINA_THRESHOLD:
        base, scale = cfg.OPENER_CHANGE_BASE, cfg.OPENER_CHANGE_SCALE
    else:
        base, scale = cfg.PITCHER_CHANGE_BASE, cfg.PITCHER_CHANGE_SCALE
    threshold = max(base, base + round(float(getattr(pitcher, "pitcher_skill", 0.5)) * scale))
    if bf >= threshold + 4:
        return "in_decay"
    if bf >= threshold - 2:
        return "near_cliff"
    return "fresh"


def _bullpen_depth_remaining(team) -> float:
    """0..1: fraction of usable pitching arms still available.

    Counts pitchers who have not yet been used (no spell logged). Falls back
    to a neutral 0.5 when the roster lacks pitcher metadata.
    """
    if team is None:
        return 0.5
    roster = list(getattr(team, "roster", []) or [])
    pitchers = [p for p in roster if getattr(p, "pitcher_skill", None) is not None]
    if not pitchers:
        return 0.5
    used = set()
    for rec in getattr(team, "_used_pitcher_ids", []) or []:
        used.add(rec)
    # Best-effort: count rostered pitchers minus those known used.
    available = max(0, len(pitchers) - len(used))
    return max(0.0, min(1.0, available / max(1, len(pitchers))))


def _lineup_locked_in(state: GameState) -> bool:
    """Proxy for a 'rally going' lineup: partnership runs accumulating."""
    return int(getattr(state, "partnership_runs", 0) or 0) >= 3


def _heart_of_order_coming_up(state: GameState, n: int = 3) -> bool:
    """True if any of the next n batters is in the team's top-3 by skill."""
    team = state.batting_team
    lineup = list(getattr(team, "lineup", []) or [])
    if len(lineup) < 3:
        return False
    ranked = sorted(lineup, key=lambda p: float(getattr(p, "skill", 0.5) or 0.5), reverse=True)
    heart_ids = {p.player_id for p in ranked[:3]}
    pos = int(getattr(team, "lineup_position", 0) or 0)
    L = len(lineup)
    for i in range(n):
        b = lineup[(pos + i) % L]
        if b.player_id in heart_ids:
            return True
    return False


def _bottom_of_order_coming_up(state: GameState, n: int = 3) -> bool:
    """True if any of the next n batters is in the team's bottom-3 by skill."""
    team = state.batting_team
    lineup = list(getattr(team, "lineup", []) or [])
    if len(lineup) < 3:
        return False
    ranked = sorted(lineup, key=lambda p: float(getattr(p, "skill", 0.5) or 0.5))
    weak_ids = {p.player_id for p in ranked[:3]}
    pos = int(getattr(team, "lineup_position", 0) or 0)
    L = len(lineup)
    for i in range(n):
        b = lineup[(pos + i) % L]
        if b.player_id in weak_ids:
            return True
    return False


def _opp_starter_dealing(state: GameState) -> bool:
    """Crude check: opp pitcher has allowed few runs/hits this spell."""
    runs = int(getattr(state, "pitcher_runs_this_spell", 0) or 0)
    hits = int(getattr(state, "pitcher_h_this_spell", 0) or 0)
    bf   = int(getattr(state, "pitcher_spell_count", 0) or 0)
    return bf >= 10 and runs <= 1 and hits <= 4


def _opponent_declared_at(state: GameState):
    """Return the opp team's declared_at_out (int) or None."""
    opp = _opp_team(state)
    return getattr(opp, "declared_at_out", None)


def _weather_shift_pending(state: GameState) -> bool:
    """Best-effort: weather model doesn't track in-game shifts yet."""
    return False


def _park_offense_skew(state: GameState) -> float:
    """-0.2..+0.2: hitter-friendly parks skew positive."""
    park_hits = float(getattr(state.home, "park_hits", 1.0) or 1.0)
    return max(-0.2, min(0.2, (park_hits - 1.0)))


def _starter_drag(state: GameState) -> float:
    """0..1: lower = stronger starter (less reason to bat first for insurance)."""
    pitcher = state.get_current_pitcher()
    if pitcher is None:
        return 0.5
    skill = float(getattr(pitcher, "pitcher_skill", 0.5) or 0.5)
    return max(0.0, min(1.0, 1.0 - skill))


def _weather_offense_skew(state: GameState) -> float:
    """-0.2..+0.2: shorthand offense-friendliness from weather, if available."""
    w = getattr(state, "weather", None)
    if w is None:
        return 0.0
    raw = float(getattr(w, "offense_skew", 0.0) or 0.0)
    return max(-0.2, min(0.2, raw))


def should_bat_first(state: GameState, rng=None) -> bool:
    """Pre-game decision by the HOME manager: bat first or second?

    Default-biased above 0.5 so home usually elects to bat first — the
    retcon for the league's existing home-scores-more asymmetry.
    """
    home = state.home
    pref = float(getattr(home, "mgr_bat_first_pref", 0.5) or 0.5)
    park = _park_offense_skew(state)
    starter_drag = _starter_drag(state)
    opp_pen_drag = 1.0 - _bullpen_depth_remaining(state.visitors)
    weather = _weather_offense_skew(state)

    p = (cfg.BAT_FIRST_BASE
         + cfg.BAT_FIRST_PARK_SCALE      * park
         + cfg.BAT_FIRST_STARTER_SCALE   * starter_drag
         + cfg.BAT_FIRST_PERSONA_SCALE   * (pref - 0.5)
         + 0.10 * opp_pen_drag
         + 0.05 * weather)
    # Cap the home strategic edge tight to the base. Home is the only
    # team that gets to make this call — visitors have no equivalent
    # tactical lever — so a wide swing range turns it into a hidden
    # home advantage on top of any role-symmetric mechanics. Keeping
    # the deviation under ±1% means the decision is essentially a coin
    # flip with the tiniest situational nudge, removing it as a source
    # of structural home-team edge.
    edge_cap = cfg.BAT_FIRST_HOME_EDGE_CAP
    p = max(cfg.BAT_FIRST_BASE - edge_cap, min(cfg.BAT_FIRST_BASE + edge_cap, p))

    if rng is None:
        import random as _r
        roll = _r.random()
    else:
        roll = rng.random()
    return roll < p


def evaluate_declaration(state: GameState, rng=None) -> tuple[bool, int]:
    """Two-layer Declared Seconds decision.

    Returns (declare_now, outs_banked):
      - (False, 0) if not declaring this PA.
      - (True, K)  if declaring now and banking K outs.

    The target save (1..6) is recomputed every PA from situational factors;
    the team declares the moment outs_remaining drops to (or below) the target.
    """
    # ---- Hard eligibility gates ----
    if state.is_super_inning or state.in_seconds_phase:
        return False, 0
    if state.outs < cfg.SECONDS_MIN_DECLARE_OUT - 1:
        return False, 0
    if state.outs >= 27:
        return False, 0
    team = state.batting_team
    if team.declared_at_out is not None:
        return False, 0

    # ---- Context ----
    diff      = _team_score_diff(state)
    outs_left = 27 - state.outs
    is_first  = (team is state.first_batting_team) if state.first_batting_team else (state.half == "top")
    agg       = float(getattr(team, "mgr_declare_aggression", 0.5) or 0.5)

    # The pitcher in danger of cliffing in OPP's half is the one currently
    # ON THE MOUND (state.fielding_team's current pitcher), because they'll
    # keep pitching when the half ends and the opp comes to bat. But we
    # actually want THIS team's defensive arm: that's also state.fielding_team
    # right now in regulation — wait, no. In regulation, team-batting is bat,
    # opp is field. When OPP bats next, OPP becomes batter, batter becomes
    # field. So the relevant "preserve our pitcher" check is the current
    # FIELDING team's pitcher relative to a future half. But that doesn't
    # apply since the fielder is the opponent during our half. The real
    # signal here is "is OUR pitcher going to defend the opp's half?" —
    # YES, because our pitcher (the one who will pitch when opp bats) is
    # the current fielding team's pitcher, which IS us when roles flip.
    # Reading the current state: state.fielding_team is opp. Their pitcher
    # is the one shutting us down. Our defending pitcher is whichever arm
    # WE will deploy when we field. We don't have a clean way to get that
    # without picking a pitcher; treat the current ON-MOUND pitcher's
    # tier as a proxy (a generic fatigue signal across the game).
    pitcher = state.get_current_pitcher()
    fatigue_tier      = _pitcher_fatigue_tier_for_state(state, pitcher)
    own_bullpen_depth = _bullpen_depth_remaining(team)
    heart_due         = _heart_of_order_coming_up(state, n=3)
    weak_due          = _bottom_of_order_coming_up(state, n=3)
    opp_pen_depth     = _bullpen_depth_remaining(state.fielding_team)
    opp_starter_dealing = _opp_starter_dealing(state)
    opp_declared_at   = _opponent_declared_at(state)
    weather_shifting  = _weather_shift_pending(state)
    park_skew         = _park_offense_skew(state)

    # ---- Build target_save (continuous; rounded at end) ----
    # Starts deeply negative so declaration requires MULTIPLE compounding
    # signals rather than any single trigger. A team that's merely trailing
    # by 2 runs with a fresh pitcher should NOT declare — there must also
    # be lineup / bullpen / fatigue pressure piling on.
    target = -3.0

    # Score differential (non-linear tiers; small individual weights so the
    # score alone never crosses target > 0).
    if diff >= 10:
        target += 3.0    # comfortable lead — bank insurance (lone strong signal)
    elif diff >= 4:
        target += 1.5
    elif 1 <= diff <= 3:
        target += 0.0
    elif diff == 0:
        if is_first:
            target += 1.0
    elif -3 <= diff <= -1:
        target += 1.5 if is_first else 0.5
    elif diff <= -4:
        target += 2.5    # need full reset

    # Pitcher fatigue state — the biggest individual driver. A truly cooked
    # pitcher can push declaration on its own; a fresh one strongly suppresses.
    if fatigue_tier == "near_cliff":
        target += 1.5
    elif fatigue_tier == "in_decay":
        target += 2.5
    elif fatigue_tier == "fresh":
        target -= 0.5

    # Lineup position pressure (next 3 batters)
    if heart_due:
        target -= 1.5
    elif weak_due:
        target += 0.8

    # Bullpen state
    if own_bullpen_depth >= 0.6:
        target += 0.3
    elif own_bullpen_depth < 0.25:
        target -= 0.5

    # Opponent state
    if opp_starter_dealing:
        target += 0.8
    if opp_pen_depth < 0.30:
        target -= 0.8
    if opp_declared_at is not None:
        # If the opp banked outs, we want to at least match their bank so
        # they don't get the last word. Override pulls target up sharply.
        opp_banked = 27 - int(opp_declared_at)
        target = max(target, opp_banked * 0.8)

    # Ballpark / weather
    if park_skew > 0.10:
        target += 0.3
    elif park_skew < -0.10:
        target -= 0.3
    if weather_shifting:
        target += 0.6

    # Persona scaling: 0.5x (patient) .. 1.5x (aggressive). Neutral mgr (0.5)
    # gives 1.0x (no change).
    target *= (0.5 + agg)

    # Small Gaussian noise — gated to marginal cases only so we don't
    # consume game RNG on every PA from out 21+ (which shifts the rest of
    # the per-PA sequence and silently changes outcomes elsewhere). For
    # targets that are clearly negative (won't declare) or clearly above
    # max banked (always declares the full bank), noise can't change the
    # integer result anyway.
    if -0.6 <= target <= float(cfg.SECONDS_MAX_BANKED) + 0.6:
        if rng is None:
            import random as _r
            target += _r.gauss(0.0, 0.3)
        else:
            target += rng.gauss(0.0, 0.3)

    target_int = max(0, min(int(cfg.SECONDS_MAX_BANKED), int(round(target))))

    # Declare now iff the current outs_remaining is at-or-below target
    declare_now = (target_int > 0) and (outs_left <= target_int)
    if not declare_now:
        return False, 0

    team.declared_at_out = state.outs
    # Stamp the score at the moment of declaration so the box score can
    # render `TEAM declares Seconds at oN, (X-Y)` — derived later via the
    # game writer rather than recomputed from the final score (which is
    # an unreliable proxy once a seconds round has fired).
    opp = _opp_team(state)
    team.declare_score_for     = int(state.score.get(team.team_id, 0) or 0)
    team.declare_score_against = int(state.score.get(opp.team_id, 0) or 0)
    # Actual banked = outs_left (capped by SECONDS_MAX_BANKED). This is the
    # number that gets recorded; target_int was the AI's preference.
    banked = min(outs_left, int(cfg.SECONDS_MAX_BANKED))
    team.outs_banked = banked
    return True, banked
