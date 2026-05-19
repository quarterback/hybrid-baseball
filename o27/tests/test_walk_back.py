"""Walk-Back rule tests.

The Walk-Back rule (see docs/stats-reference.md):
- An HR resolves identically to MLB (existing runners + batter score).
- The batter then "walks back" to 3B as a Walk-Back runner for the
  NEXT batter's PA only.
- If the next batter drives him home with the bat (1B/2B/3B/HR/sac-fly
  /productive ground-out), +1 unearned run scores (Manfred-runner
  precedent).
- Pitcher counters: `pitcher_wb_faced_this_spell` ticks every Walk-Back
  PA faced; `pitcher_wb_runs_this_spell` ticks when the bonus scores.
- ERA excludes Walk-Back runs (carried in unearned_runs); wERA / runs
  allowed include them.

Run:  python -m pytest o27/tests/test_walk_back.py -v
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from o27.engine.state import GameState, Team, Player
from o27.engine.pa import apply_event
from o27.engine.fielding import (
    outcome_home_run, outcome_single, outcome_double, outcome_triple,
    outcome_ground_out, outcome_fly_out, outcome_fly_out_runner_scores,
    outcome_line_out,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _mk_player(pid: str, name: str, is_pitcher: bool = False) -> Player:
    p = Player(player_id=pid, name=name, skill=0.5)
    p.is_pitcher = is_pitcher
    p.pitcher_skill = 0.5
    p.stamina = 0.5
    return p


def _mk_team(tid: str, n_players: int = 9) -> Team:
    t = Team(team_id=tid, name=tid)
    for i in range(n_players):
        p = _mk_player(f"{tid}_{i}", f"{tid}_{i}")
        t.roster.append(p)
        t.lineup.append(p)
    # One pitcher on the roster for spell tracking.
    pitcher = _mk_player(f"{tid}_p", f"{tid}_p", is_pitcher=True)
    t.roster.append(pitcher)
    return t


def _mk_state() -> GameState:
    state = GameState(visitors=_mk_team("visitors"), home=_mk_team("home"))
    # Visitors are batting (top half) → home is fielding. Pin a pitcher
    # so spell counters are wired up.
    state.current_pitcher_id = "home_p"
    return state


def _bip(choice: str, outcome: dict) -> dict:
    return {"type": "ball_in_play", "choice": choice, "outcome": outcome}


# ---------------------------------------------------------------------------
# 1. HR play resolves MLB-exactly, then arms Walk-Back
# ---------------------------------------------------------------------------

def test_solo_hr_scores_one_and_arms_walk_back():
    state = _mk_state()
    # Solo HR (bases empty).
    apply_event(state, _bip("run", outcome_home_run()))
    # Team scored 1.
    assert state.score["visitors"] == 1
    # Bases empty (HR play itself doesn't place the Walk-Back runner —
    # it lives in state.walk_back_pending).
    assert state.bases == [None, None, None]
    # Walk-Back is armed for the next PA, carrying the HR-hitter id.
    assert state.walk_back_pending == "visitors_0"
    # ER on the spell: 1 (no Walk-Back charged yet — it's not scored).
    assert state.pitcher_runs_this_spell == 1
    assert state.pitcher_unearned_runs_this_spell == 0
    # No Walk-Back faced yet (the HR itself doesn't count).
    assert state.pitcher_wb_faced_this_spell == 0
    assert state.pitcher_wb_runs_this_spell == 0


def test_grand_slam_scores_four_and_arms_walk_back():
    state = _mk_state()
    state.bases = ["visitors_1", "visitors_2", "visitors_3"]
    apply_event(state, _bip("run", outcome_home_run()))
    assert state.score["visitors"] == 4
    assert state.bases == [None, None, None]
    assert state.walk_back_pending == "visitors_0"
    assert state.pitcher_runs_this_spell == 4
    assert state.pitcher_unearned_runs_this_spell == 0


# ---------------------------------------------------------------------------
# 2. Bonus FIRES on a single / 2B / 3B / HR / sac-fly / productive GO
# ---------------------------------------------------------------------------

def test_hr_then_single_bonus_run_scores_and_unearned():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    # HR-hitter is V_0. Next batter V_1 singles → Walk-Back fires.
    apply_event(state, _bip("run", outcome_single()))
    # 1 (HR) + 1 (Walk-Back bonus) = 2 team runs (single itself drives
    # no one home — bases were empty after the HR cleared them).
    assert state.score["visitors"] == 2
    # Walk-Back tally on the pitcher.
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 1
    # Bonus run is unearned (Manfred-runner precedent).
    assert state.pitcher_runs_this_spell == 2
    assert state.pitcher_unearned_runs_this_spell == 1
    # Flag consumed; no carryover.
    assert state.walk_back_pending is None


def test_hr_then_sac_fly_bonus_fires():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    # Sac fly that scores from 3B (runner_advances[2]==1). With no
    # actual 3B runner, the play credits no actual runs — but the
    # Walk-Back fires because the play type would have scored from 3B.
    apply_event(state, _bip("run", outcome_fly_out_runner_scores()))
    # HR (1) + Walk-Back bonus (1) = 2.
    assert state.score["visitors"] == 2
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 1
    assert state.pitcher_unearned_runs_this_spell == 1


def test_hr_then_productive_ground_out_bonus_fires():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    # Default ground_out advances all runners 1 base → would score 3B.
    apply_event(state, _bip("run", outcome_ground_out()))
    assert state.score["visitors"] == 2
    assert state.pitcher_wb_runs_this_spell == 1


def test_hr_then_double_bonus_fires():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    apply_event(state, _bip("run", outcome_double()))
    assert state.score["visitors"] == 2
    assert state.pitcher_wb_runs_this_spell == 1


def test_hr_then_hr_bonus_fires_and_new_bonus_arms():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    apply_event(state, _bip("run", outcome_home_run()))
    # HR1 (1) + Walk-Back from HR1 (1) + HR2 (1) = 3.
    assert state.score["visitors"] == 3
    # HR2 also arms its own Walk-Back.
    assert state.walk_back_pending == "visitors_1"
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 1


# ---------------------------------------------------------------------------
# 3. Bonus EVAPORATES on K / BB / HBP / unproductive out
# ---------------------------------------------------------------------------

def test_hr_then_strikeout_bonus_evaporates():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    # Strike three times to whiff.
    apply_event(state, {"type": "swinging_strike"})
    apply_event(state, {"type": "swinging_strike"})
    apply_event(state, {"type": "swinging_strike"})
    # Only HR run on the board; no Walk-Back bonus.
    assert state.score["visitors"] == 1
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 0
    # Pitcher: 1 ER from HR, 0 UER (no Walk-Back).
    assert state.pitcher_runs_this_spell == 1
    assert state.pitcher_unearned_runs_this_spell == 0
    assert state.walk_back_pending is None


def test_hr_then_walk_no_bonus():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    # Four balls = walk.
    for _ in range(4):
        apply_event(state, {"type": "ball"})
    # No bonus from a walk.
    assert state.score["visitors"] == 1
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 0


def test_hr_then_line_out_no_bonus():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    # Line out — runners frozen, no advance from 3B.
    apply_event(state, _bip("run", outcome_line_out()))
    assert state.score["visitors"] == 1
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 0


def test_hr_then_foul_out_no_bonus():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    # Three fouls = foul-out.
    apply_event(state, {"type": "foul"})
    apply_event(state, {"type": "foul"})
    apply_event(state, {"type": "foul"})
    assert state.score["visitors"] == 1
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 0


# ---------------------------------------------------------------------------
# 4. Bonus does NOT carry past one PA
# ---------------------------------------------------------------------------

def test_bonus_does_not_carry_past_one_pa():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    # K consumes the Walk-Back (faced += 1, runs unchanged).
    apply_event(state, {"type": "swinging_strike"})
    apply_event(state, {"type": "swinging_strike"})
    apply_event(state, {"type": "swinging_strike"})
    assert state.pitcher_wb_faced_this_spell == 1
    # The SUBSEQUENT batter singles — must not retroactively score the
    # already-evaporated Walk-Back.
    apply_event(state, _bip("run", outcome_single()))
    assert state.score["visitors"] == 1  # HR only — no bonus
    assert state.pitcher_wb_faced_this_spell == 1  # no second consume
    assert state.pitcher_wb_runs_this_spell == 0


# ---------------------------------------------------------------------------
# 5. Identity check: solo HR + strand matches MLB box-score totals
# ---------------------------------------------------------------------------

def test_solo_hr_strand_matches_mlb_box_score():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    # Force three outs without driving the Walk-Back in.
    for _ in range(3):
        for _ in range(3):
            apply_event(state, {"type": "swinging_strike"})
    # 1 R (the HR), 1 ER (the HR), 0 UER, 1 HR allowed.
    assert state.score["visitors"] == 1
    assert state.pitcher_runs_this_spell == 1
    assert state.pitcher_unearned_runs_this_spell == 0
    assert state.pitcher_hr_this_spell == 1
    # Walk-Back denominator pair: faced=1 (the next batter's PA),
    # runs=0 (stranded). The other two strikeout PAs are NOT Walk-Back
    # situations.
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 0


def test_grand_slam_then_single_yields_5_team_runs():
    state = _mk_state()
    state.bases = ["visitors_1", "visitors_2", "visitors_3"]
    apply_event(state, _bip("run", outcome_home_run()))   # 4 R from the slam
    apply_event(state, _bip("run", outcome_single()))     # +1 Walk-Back bonus
    assert state.score["visitors"] == 5
    # ER = 4 (the slam); UER = 1 (the Walk-Back bonus).
    assert state.pitcher_runs_this_spell == 5
    assert state.pitcher_unearned_runs_this_spell == 1


# ---------------------------------------------------------------------------
# 6. Walk-Back faced increments exactly ONCE per Walk-Back situation
# ---------------------------------------------------------------------------

def test_wb_faced_increments_once_per_walk_back_situation():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    # A foul or two before the K should NOT spuriously bump wb_faced —
    # consumption happens at PA terminus only.
    apply_event(state, {"type": "foul"})
    apply_event(state, {"type": "ball"})
    apply_event(state, {"type": "swinging_strike"})
    apply_event(state, {"type": "swinging_strike"})
    apply_event(state, {"type": "swinging_strike"})
    assert state.pitcher_wb_faced_this_spell == 1


# ---------------------------------------------------------------------------
# 7. ERA exclusion vs wERA inclusion — sim.py-level check.
# Walk-Back runs flow into pitcher_unearned_runs_this_spell, which sim.py
# subtracts from runs_allowed to compute ER. That gives ERA exclusion.
# ---------------------------------------------------------------------------

def test_walk_back_run_is_unearned_excluded_from_era():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    apply_event(state, _bip("run", outcome_single()))
    # Bonus run is in the unearned bucket. ER = runs_allowed - unearned.
    er = state.pitcher_runs_this_spell - state.pitcher_unearned_runs_this_spell
    assert er == 1  # the HR; bonus is excluded
    # wERA / runs_allowed include the bonus.
    assert state.pitcher_runs_this_spell == 2


# ---------------------------------------------------------------------------
# 8. Walk-Back Stop% computes correctly via aggregate counters
# ---------------------------------------------------------------------------

def test_walk_back_stop_pct_computed_correctly():
    state = _mk_state()
    # Three Walk-Back situations: one fires (single), two evaporate (Ks).
    for fire in (False, True, False):
        apply_event(state, _bip("run", outcome_home_run()))
        if fire:
            apply_event(state, _bip("run", outcome_single()))
        else:
            for _ in range(3):
                apply_event(state, {"type": "swinging_strike"})
    assert state.pitcher_wb_faced_this_spell == 3
    assert state.pitcher_wb_runs_this_spell == 1
    stop_pct = (state.pitcher_wb_faced_this_spell - state.pitcher_wb_runs_this_spell) / state.pitcher_wb_faced_this_spell
    assert abs(stop_pct - (2.0 / 3.0)) < 1e-9
