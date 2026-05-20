"""Walk-Back rule tests.

The Walk-Back rule (see docs/stats-reference.md):
- An HR resolves identically to MLB (existing runners + batter score).
- The batter then "walks back" to 3B as a LIVE bonus runner — no different
  from any other runner on third. He persists across plate appearances until
  his fate is actually decided: he scores, is put out, or is stranded when
  the half ends. He is NOT consumed by a single next-PA window.
- Whenever he scores it is a +1 unearned Walk-Back run (Manfred-runner
  precedent). A subsequent HR scores him (as the lead runner) and places a
  fresh Walk-Back runner on third.
- Pitcher counters: `pitcher_wb_faced_this_spell` ticks once when the runner's
  fate resolves (score / out / strand), charged to the pitcher on the mound;
  `pitcher_wb_runs_this_spell` ticks when he scores.
- ERA excludes Walk-Back runs (carried in unearned_runs); wERA / runs
  allowed include them.

Run:  python -m pytest o27/tests/test_walk_back.py -v
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from o27.engine.state import GameState, Team, Player
from o27.engine.pa import apply_event, resolve_stranded_walk_backs
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
# 1. HR resolves MLB-exactly, then places a live runner on 3B
# ---------------------------------------------------------------------------

def test_solo_hr_places_walk_back_runner_on_3b():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    assert state.score["visitors"] == 1
    # The HR-hitter is now a live runner on third — not a phantom flag.
    assert state.bases == [None, None, "visitors_0"]
    assert state.walk_back_runner_ids == {"visitors_0"}
    # Nothing resolved yet: 1 ER from the HR, no Walk-Back charged.
    assert state.pitcher_runs_this_spell == 1
    assert state.pitcher_unearned_runs_this_spell == 0
    assert state.pitcher_wb_faced_this_spell == 0
    assert state.pitcher_wb_runs_this_spell == 0


def test_grand_slam_places_walk_back_runner_on_3b():
    state = _mk_state()
    state.bases = ["visitors_1", "visitors_2", "visitors_3"]
    apply_event(state, _bip("run", outcome_home_run()))
    assert state.score["visitors"] == 4
    assert state.bases == [None, None, "visitors_0"]
    assert state.walk_back_runner_ids == {"visitors_0"}
    assert state.pitcher_runs_this_spell == 4
    assert state.pitcher_unearned_runs_this_spell == 0


# ---------------------------------------------------------------------------
# 2. The runner scores like any other runner on 3B (unearned)
# ---------------------------------------------------------------------------

def test_hr_then_single_scores_walk_back():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    # V_1 singles; the runner on 3B (V_0) scores.
    apply_event(state, _bip("run", outcome_single()))
    assert state.score["visitors"] == 2
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 1
    assert state.pitcher_runs_this_spell == 2
    assert state.pitcher_unearned_runs_this_spell == 1
    assert state.walk_back_runner_ids == set()


def test_hr_then_sac_fly_scores_walk_back():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    apply_event(state, _bip("run", outcome_fly_out_runner_scores()))
    assert state.score["visitors"] == 2
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 1
    assert state.pitcher_unearned_runs_this_spell == 1


def test_hr_then_productive_ground_out_scores_walk_back():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    apply_event(state, _bip("run", outcome_ground_out()))
    assert state.score["visitors"] == 2
    assert state.pitcher_wb_runs_this_spell == 1


def test_hr_then_double_scores_walk_back():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    apply_event(state, _bip("run", outcome_double()))
    assert state.score["visitors"] == 2
    assert state.pitcher_wb_runs_this_spell == 1


def test_hr_then_hr_scores_runner_and_places_new_one():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    apply_event(state, _bip("run", outcome_home_run()))
    # HR1 (1) + V_0 walk-back run on HR2 (1) + HR2 batter (1) = 3.
    assert state.score["visitors"] == 3
    # The second HR-hitter is the new live runner on 3B.
    assert state.walk_back_runner_ids == {"visitors_1"}
    assert state.bases == [None, None, "visitors_1"]
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 1


# ---------------------------------------------------------------------------
# 3. The runner PERSISTS — he is not consumed by a non-driving PA
# ---------------------------------------------------------------------------

def test_hr_then_strikeout_runner_stays_on_3b():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    apply_event(state, {"type": "swinging_strike"})
    apply_event(state, {"type": "swinging_strike"})
    apply_event(state, {"type": "swinging_strike"})
    # The K is the BATTER's out — the Walk-Back runner is untouched.
    assert state.score["visitors"] == 1
    assert state.bases[2] == "visitors_0"
    assert state.walk_back_runner_ids == {"visitors_0"}
    assert state.pitcher_wb_faced_this_spell == 0
    assert state.pitcher_wb_runs_this_spell == 0


def test_hr_then_walk_runner_stays_on_3b():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    for _ in range(4):
        apply_event(state, {"type": "ball"})
    # Walk doesn't force the runner home (bases weren't loaded); he holds.
    assert state.score["visitors"] == 1
    assert state.bases[2] == "visitors_0"
    assert state.walk_back_runner_ids == {"visitors_0"}
    assert state.pitcher_wb_faced_this_spell == 0


def test_hr_then_line_out_runner_stays_on_3b():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    apply_event(state, _bip("run", outcome_line_out()))
    assert state.score["visitors"] == 1
    assert state.bases[2] == "visitors_0"
    assert state.pitcher_wb_faced_this_spell == 0


def test_hr_then_foul_out_runner_stays_on_3b():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    apply_event(state, {"type": "foul"})
    apply_event(state, {"type": "foul"})
    apply_event(state, {"type": "foul"})
    assert state.score["visitors"] == 1
    assert state.bases[2] == "visitors_0"
    assert state.pitcher_wb_faced_this_spell == 0


def test_runner_scores_a_later_pa_not_just_the_next():
    """The bonus carries: a strikeout does not evaporate the runner, and a
    subsequent hit drives him home for the unearned run."""
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    # An intervening strikeout PA — runner persists.
    apply_event(state, {"type": "swinging_strike"})
    apply_event(state, {"type": "swinging_strike"})
    apply_event(state, {"type": "swinging_strike"})
    assert state.pitcher_wb_faced_this_spell == 0
    # The NEXT hitter singles — the runner who has been waiting scores.
    apply_event(state, _bip("run", outcome_single()))
    assert state.score["visitors"] == 2
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 1
    assert state.pitcher_unearned_runs_this_spell == 1


# ---------------------------------------------------------------------------
# 4. The runner can be put out on the bases (a stop, no run)
# ---------------------------------------------------------------------------

def test_walk_back_runner_picked_off_is_a_stop():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    assert state.bases[2] == "visitors_0"
    # Pick him off third — a stop: faced ticks, no run, no carryover.
    apply_event(state, {"type": "pickoff_attempt", "base_idx": 2, "success": True})
    assert state.bases[2] is None
    assert state.walk_back_runner_ids == set()
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 0
    assert state.score["visitors"] == 1


def test_walk_back_runner_cannot_be_pinch_run_for():
    """Rule: a Walk-Back bonus runner can't be replaced by a pinch runner —
    he stays on the bag (keeps the HR-hitter's bat in the lineup and avoids
    burning a bench player on a free runner)."""
    from o27.engine import manager as mgr
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    assert state.bases[2] == "visitors_0"
    pr = _mk_player("visitors_pr", "visitors_pr")
    state.visitors.roster.append(pr)
    log = mgr.pinch_run(state, base_idx=2, runner_in=pr)
    # Refused: the Walk-Back runner is untouched and still tracked.
    assert state.bases[2] == "visitors_0"
    assert state.walk_back_runner_ids == {"visitors_0"}
    assert any("cannot be replaced" in line for line in log)


def test_walk_back_runner_caught_stealing_home_is_a_stop():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    apply_event(state, {"type": "stolen_base_attempt", "base_idx": 2, "success": False})
    assert state.walk_back_runner_ids == set()
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 0
    assert state.score["visitors"] == 1


# ---------------------------------------------------------------------------
# 5. Stranded at half's end is a stop
# ---------------------------------------------------------------------------

def test_walk_back_runner_stranded_at_half_end_is_a_stop():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    # Half ends with the bonus runner still on third.
    resolve_stranded_walk_backs(state)
    assert state.walk_back_runner_ids == set()
    assert state.pitcher_wb_faced_this_spell == 1
    assert state.pitcher_wb_runs_this_spell == 0
    assert state.score["visitors"] == 1


# ---------------------------------------------------------------------------
# 6. Identity / accounting
# ---------------------------------------------------------------------------

def test_grand_slam_then_single_yields_5_team_runs():
    state = _mk_state()
    state.bases = ["visitors_1", "visitors_2", "visitors_3"]
    apply_event(state, _bip("run", outcome_home_run()))   # 4 R from the slam
    apply_event(state, _bip("run", outcome_single()))     # +1 Walk-Back run
    assert state.score["visitors"] == 5
    assert state.pitcher_runs_this_spell == 5
    assert state.pitcher_unearned_runs_this_spell == 1


def test_walk_back_run_is_unearned_excluded_from_era():
    state = _mk_state()
    apply_event(state, _bip("run", outcome_home_run()))
    apply_event(state, _bip("run", outcome_single()))
    er = state.pitcher_runs_this_spell - state.pitcher_unearned_runs_this_spell
    assert er == 1                       # the HR; bonus is excluded
    assert state.pitcher_runs_this_spell == 2
    # The earned-run arc buckets must agree with ER (the bonus run was
    # demoted out of er_arc when it scored).
    assert sum(state.pitcher_er_arc_this_spell) == er


def test_walk_back_stop_pct_computed_correctly():
    state = _mk_state()
    # Three Walk-Back situations: one scores (single), two are stranded.
    for scores in (False, True, False):
        apply_event(state, _bip("run", outcome_home_run()))
        if scores:
            apply_event(state, _bip("run", outcome_single()))
        else:
            # Strand the runner (end the half) without driving him in.
            resolve_stranded_walk_backs(state)
    assert state.pitcher_wb_faced_this_spell == 3
    assert state.pitcher_wb_runs_this_spell == 1
    stop_pct = (state.pitcher_wb_faced_this_spell - state.pitcher_wb_runs_this_spell) / state.pitcher_wb_faced_this_spell
    assert abs(stop_pct - (2.0 / 3.0)) < 1e-9
