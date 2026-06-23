"""RRR manager AI — the chasing (second-batting) side reacts to its Required
Run Rate per 3 outs (RRR/3O).

Covers:
  * state.chase_rrr_3o() — value + chaser/target gating;
  * rrr_manager_on — per-game override beats cfg default;
  * the band helpers rrr_aggressive / rrr_conceding / rrr_contact_mult;
  * _desperation_rally's pace-aware OR branch (fires when behind the required
    rate even if the deficit-band/outs-left gate would not), and that the
    deficit path is unchanged when the AI is off;
  * the concession gate in should_pinch_hit (freeze the premium PH, rotate a
    scrub) once the chase is mathematically dead;
  * determinism with the AI on, and that enabling it changes some chases.
"""
import os
import random
import sys

import pytest

from o27.engine.state import Team, GameState, Player
from o27.engine import manager as mgr
from o27 import config as cfg

sys.path.insert(0, os.path.dirname(__file__))  # for test_power_play helpers

_POS = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C"]


def _p(pid, name, pitcher=False, pos="", **kw):
    return Player(player_id=pid, name=name, is_pitcher=pitcher, position=pos, **kw)


def _team(tid, name, bench_skills=None):
    pre = tid[0].upper()
    starters = [_p(f"{pre}{i}", f"{name[:3]}-{i}", pos=_POS[i - 1],
                   contact=0.5, power=0.5, speed=0.5, defense=0.5, skill=0.5)
                for i in range(1, 9)]
    sp = _p(f"{pre}SP", f"{name[:3]}-SP", pitcher=True, pos="P",
            stamina=0.5, pitcher_skill=0.5)
    skills = bench_skills or [0.6] * 5
    bench = [_p(f"{pre}B{b}", f"{name[:3]}-B{b}", pos=_POS[(b - 1) % 8],
                contact=0.6, power=0.6, speed=0.6, defense=0.6, skill=skills[b - 1])
             for b in range(1, 6)]
    pen = [_p(f"{pre}P{p}", f"{name[:3]}-P{p}", pitcher=True, pos="P",
              stamina=0.6, pitcher_skill=0.6) for p in range(1, 5)]
    roster = starters + [sp] + bench + pen
    return Team(team_id=tid, name=name, roster=roster, lineup=starters + [sp])


def _chase_state(target_score, runs, outs, **team_kw):
    """Visitors are the chasing (second-batting) side: home batted first and
    finished on `target_score`; visitors have `runs` with `outs` recorded."""
    v = _team("visitors", "Visitors", **team_kw)
    h = _team("home", "Home")
    st = GameState(visitors=v, home=h)
    st.half = "top"                  # visitors bat → batting_team is visitors
    st.first_batting_team = h
    st.second_batting_team = v       # visitors are the chaser
    st.bases = [None, None, None]
    st.visitors.lineup_cycle_number = 2
    st.target_score = target_score
    st.score = {"visitors": runs, "home": target_score}
    st.outs = outs
    st.current_pitcher_id = "HSP"
    return st


# --- chase_rrr_3o + gating --------------------------------------------------

def test_chase_rrr_3o_value_and_gating():
    st = _chase_state(target_score=50, runs=0, outs=24)  # need 51 in 3 outs
    assert st.chase_rrr_3o() == pytest.approx(51.0)      # 51/3 * 3
    # Not the chasing side → None.
    st.second_batting_team = st.home
    assert st.chase_rrr_3o() is None
    # No target yet → None.
    st2 = _chase_state(50, 0, 24)
    st2.target_score = None
    assert st2.chase_rrr_3o() is None


# --- flag gate --------------------------------------------------------------

def test_rrr_manager_on_override_and_default():
    st = _chase_state(10, 0, 0)
    assert mgr.rrr_manager_on(st) is bool(cfg.RRR_MANAGER_ENABLED)  # cfg fallback
    st.rrr_manager_enabled = False
    assert mgr.rrr_manager_on(st) is False                          # override wins
    st.rrr_manager_enabled = True
    assert mgr.rrr_manager_on(st) is True


# --- bands ------------------------------------------------------------------

def test_rrr_aggressive_and_conceding_bands():
    aggro = _chase_state(8, 0, 21)     # need 9 in 6 outs → RRR/3O 4.5
    aggro.rrr_manager_enabled = True
    assert mgr.rrr_aggressive(aggro) is True
    assert mgr.rrr_conceding(aggro) is False

    dead = _chase_state(50, 0, 24)     # RRR/3O 51 → conceding
    dead.rrr_manager_enabled = True
    assert mgr.rrr_conceding(dead) is True
    assert mgr.rrr_aggressive(dead) is False

    easy = _chase_state(9, 0, 0)       # RRR/3O 10/9 ≈ 1.11 → neither
    easy.rrr_manager_enabled = True
    assert mgr.rrr_aggressive(easy) is False
    assert mgr.rrr_conceding(easy) is False

    aggro.rrr_manager_enabled = False  # flag off → no behavior
    assert mgr.rrr_aggressive(aggro) is False
    assert mgr.rrr_conceding(aggro) is False


def test_rrr_contact_mult_ramps_and_caps():
    # Below AGGRO → identity.
    easy = _chase_state(9, 0, 0)
    easy.rrr_manager_enabled = True
    assert mgr.rrr_contact_mult(easy) == 1.0

    # Midband (RRR/3O 4.5, halfway between AGGRO 3 and DESPERATION 6) → halfway
    # between 1.0 and the cap.
    mid = _chase_state(8, 0, 21)
    mid.rrr_manager_enabled = True
    expected = 1.0 + 0.5 * (cfg.RRR_CONTACT_LIFT_MAX - 1.0)
    assert mgr.rrr_contact_mult(mid) == pytest.approx(expected)

    # At/above DESPERATION → saturates at the cap.
    dead = _chase_state(50, 0, 24)
    dead.rrr_manager_enabled = True
    assert mgr.rrr_contact_mult(dead) == pytest.approx(cfg.RRR_CONTACT_LIFT_MAX)

    # Flag off / not chasing → identity.
    mid.rrr_manager_enabled = False
    assert mgr.rrr_contact_mult(mid) == 1.0
    on_not_chasing = _chase_state(50, 0, 24)
    on_not_chasing.rrr_manager_enabled = True
    on_not_chasing.second_batting_team = on_not_chasing.home
    assert mgr.rrr_contact_mult(on_not_chasing) == 1.0


# --- desperation-rally fold-in ----------------------------------------------

def test_desperation_rally_rrr_branch_fires_when_outs_gate_fails():
    # Behind the pace (RRR/3O 4.5) but only 6 outs left, so the legacy
    # deficit+outs path returns False; the RRR path makes it True.
    st = _chase_state(8, 0, 21)
    st.rrr_manager_enabled = False
    assert mgr._desperation_rally(st) is False
    st.rrr_manager_enabled = True
    assert mgr._desperation_rally(st) is True


def test_desperation_rally_deficit_path_unchanged_when_off():
    # Classic deficit-band rally (down 6, 15 outs left) still fires with the
    # RRR AI off and no chase context.
    st = _chase_state(0, 0, 12)
    st.second_batting_team = None
    st.score = {"visitors": 0, "home": 6}
    st.rrr_manager_enabled = False
    assert mgr._desperation_rally(st) is True


# --- concession gate --------------------------------------------------------

def test_concession_freezes_premium_pinch_hitter():
    # Need 9 in 1 out (RRR/3O 27) — dead. A premium 0.95 bat sits on the bench;
    # concession must rotate a scrub instead, and only when the AI is on.
    kw = dict(target_score=8, runs=0, outs=26,
              bench_skills=[0.95, 0.40, 0.41, 0.42, 0.43])

    on = _chase_state(**kw)
    on.rrr_manager_enabled = True
    on.visitors.lineup_cycle_number = 1
    res = mgr.should_pinch_hit(on, rng=random.Random(0))
    assert res is not None
    assert res.skill == pytest.approx(0.40)        # the scrub, never the 0.95 premium

    off = _chase_state(**kw)
    off.rrr_manager_enabled = False
    off.visitors.lineup_cycle_number = 1
    # AI off: no concession, deficit (8) below the blowout band, bases empty →
    # the leverage path doesn't clear the threshold → no sub.
    assert mgr.should_pinch_hit(off, rng=random.Random(0)) is None


# --- end-to-end determinism / activity --------------------------------------

from test_power_play import _mk_state           # noqa: E402
from o27.engine.prob import ProbabilisticProvider  # noqa: E402
from o27.engine.game import run_game            # noqa: E402
from o27.render.render import Renderer          # noqa: E402


def _run_game(enabled, seed):
    # Pin the pre-game coin flip (home_bats_first falls back to the GLOBAL random
    # when no rng is passed at setup) so the game is fully provider-deterministic.
    random.seed(seed)
    state = _mk_state()
    state.rrr_manager_enabled = enabled
    state.home_bats_first = False        # visitors bat first → home is the chaser
    final, log = run_game(state, ProbabilisticProvider(random.Random(seed)), Renderer())
    return final, log


def test_determinism_with_ai_on():
    assert _run_game(True, 7)[1] == _run_game(True, 7)[1]


def test_flag_off_stable_and_on_changes_some_chases():
    # Off is itself deterministic...
    assert _run_game(False, 3)[1] == _run_game(False, 3)[1]
    # ...and enabling the AI changes the outcome of at least one chase across a
    # spread of seeds (proves the levers actually fire).
    any_diff = any(_run_game(True, s)[1] != _run_game(False, s)[1] for s in range(12))
    assert any_diff, "RRR manager AI on should change some chase outcomes"
