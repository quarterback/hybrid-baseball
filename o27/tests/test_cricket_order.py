"""
Cricket Batting Order — optional rule (earned, use-or-lose, manager-decided).

Covers:
  * advance_lineup arming pending_flip only on a joker-free wrap, and only when
    the rule is on;
  * the flip helpers (can_flip / flip_line / apply_flip);
  * manager.should_use_flip — persona (mgr_flip_aggression) and situational
    (score, out-arc) drivers;
  * manager.joker_flip_damp — the joker-vs-flip opportunity cost;
  * end-to-end through the provider: flips fire in regulation, never in
    super-innings, and are inert when the rule is off.
"""
import random
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(__file__))  # for test_power_play helpers

from o27 import config as cfg
from o27.engine import cricket_order
from o27.engine import manager as mgr
from o27.engine.state import GameState, Team
from o27.engine.prob import ProbabilisticProvider
from o27.engine.game import run_game
from o27.render.render import Renderer
from test_power_play import _mk_state, _mk_team


class _Bat:
    def __init__(self, name):
        self.name = name


def _team(enabled=None, n=9, flip_agg=0.5, team_id="home"):
    return Team(team_id=team_id, name="T",
                lineup=[_Bat(chr(65 + i)) for i in range(n)],
                cricket_order_enabled=enabled, mgr_flip_aggression=flip_agg)


def _names(t):
    return [b.name for b in t.lineup]


def _advance_full_cycle(team, jokers=()):
    n = len(team.lineup)
    for i in range(n):
        if i == n - 1 and jokers:
            team.jokers_used_this_cycle = set(jokers)
        team.advance_lineup()


# ---------------------------------------------------------------------------
# advance_lineup arms pending_flip (it no longer flips directly)
# ---------------------------------------------------------------------------

def test_joker_free_cycle_arms_pending_flip(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", False, raising=False)
    team = _team(enabled=True)
    before = _names(team)
    _advance_full_cycle(team)
    assert team.pending_flip is True
    # advance_lineup does NOT itself reverse — the manager spends it later.
    assert _names(team) == before


def test_joker_used_does_not_arm_flip(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", False, raising=False)
    team = _team(enabled=True)
    _advance_full_cycle(team, jokers=("j1",))
    assert team.pending_flip is False


def test_rule_off_never_arms_flip(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", False, raising=False)
    team = _team(enabled=None)            # no override, global off
    _advance_full_cycle(team)
    assert team.pending_flip is False


def test_per_team_off_overrides_global_on(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", True, raising=False)
    team = _team(enabled=False)           # explicit opt-out
    _advance_full_cycle(team)
    assert team.pending_flip is False


# ---------------------------------------------------------------------------
# Flip helpers
# ---------------------------------------------------------------------------

def test_apply_flip_reverses_and_flip_line_names_new_leadoff():
    team = _team(enabled=True)
    before = _names(team)
    line = cricket_order.flip_line(team)          # computed pre-reversal
    assert before[-1] in line and "leads off" in line
    cricket_order.apply_flip(team)
    assert _names(team) == list(reversed(before))


def test_short_lineup_cannot_flip():
    team = _team(enabled=True, n=1)
    assert cricket_order.can_flip(team) is False
    assert cricket_order.flip_line(team) == ""
    cricket_order.apply_flip(team)                # no-op, no crash
    assert _names(team) == ["A"]


# ---------------------------------------------------------------------------
# should_use_flip — persona + situational
# ---------------------------------------------------------------------------

def _flip_rate(flip_agg, *, own=0, opp=0, outs=13, trials=4000):
    state = GameState(visitors=_team(enabled=True, flip_agg=flip_agg, team_id="visitors"),
                      home=_mk_team("home", "H"))
    state.half = "top"                            # visitors bat
    state.score = {"visitors": own, "home": opp}
    state.outs = outs
    rng = random.Random(1234)
    hits = sum(1 for _ in range(trials) if mgr.should_use_flip(state, rng=rng))
    return hits / trials


def test_flip_rate_monotonic_in_persona():
    low = _flip_rate(0.1)
    high = _flip_rate(0.9)
    assert high > low + 0.2          # flip-lovers spend far more often
    assert low < 0.45 < high


def test_flip_rate_higher_when_trailing():
    trailing = _flip_rate(0.5, own=0, opp=10)
    leading = _flip_rate(0.5, own=10, opp=0)
    assert trailing > leading + 0.1  # need offense -> churn the order


def test_flip_rate_higher_late_in_arc():
    early = _flip_rate(0.5, outs=2)
    late = _flip_rate(0.5, outs=26)
    assert late > early


# ---------------------------------------------------------------------------
# joker_flip_damp — the opportunity cost of forfeiting the flip
# ---------------------------------------------------------------------------

def _reg_state(team):
    state = GameState(visitors=team, home=_mk_team("home", "H"))
    state.half = "top"
    return state


def test_joker_damp_inert_when_rule_off():
    team = _team(enabled=False, flip_agg=1.0)
    assert mgr.joker_flip_damp(team, _reg_state(team)) == 1.0


def test_joker_damp_scales_with_flip_aggression():
    lover = _team(enabled=True, flip_agg=1.0)
    neutral = _team(enabled=True, flip_agg=0.0)
    d_lover = mgr.joker_flip_damp(lover, _reg_state(lover))
    d_neutral = mgr.joker_flip_damp(neutral, _reg_state(neutral))
    assert d_lover < d_neutral == 1.0
    assert d_lover == pytest.approx(1.0 - cfg.CRICKET_JOKER_FLIP_DAMP)


def test_joker_damp_gone_once_joker_used_this_cycle():
    team = _team(enabled=True, flip_agg=1.0)
    team.jokers_used_this_cycle = {"j1"}
    # Flip already forfeited this cycle — no further cost on later jokers.
    assert mgr.joker_flip_damp(team, _reg_state(team)) == 1.0


def test_joker_damp_inert_in_super_innings():
    team = _team(enabled=True, flip_agg=1.0)
    state = _reg_state(team)
    state.half = "super_top"
    assert state.is_super_inning is True
    assert mgr.joker_flip_damp(team, state) == 1.0


# ---------------------------------------------------------------------------
# End-to-end through the provider
# ---------------------------------------------------------------------------

def _run(enabled, flip_agg, seed):
    state = _mk_state()
    for t in (state.home, state.visitors):
        t.cricket_order_enabled = enabled
        t.mgr_flip_aggression = flip_agg
    final, log = run_game(state, ProbabilisticProvider(random.Random(seed)), Renderer())
    flips = sum(1 for line in log if "Cricket order flips" in line)
    return final, flips


def test_end_to_end_flip_lovers_flip(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", False, raising=False)
    total = sum(_run(True, 1.0, s)[1] for s in range(6))
    assert total > 0, "flip-loving managers with the rule on should flip"


def test_end_to_end_rule_off_never_flips(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", False, raising=False)
    total = sum(_run(None, 1.0, s)[1] for s in range(6))
    assert total == 0


def test_end_to_end_flip_lovers_flip_more_than_joker_lovers(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", False, raising=False)
    lovers = sum(_run(True, 0.95, s)[1] for s in range(8))
    refusers = sum(_run(True, 0.05, s)[1] for s in range(8))
    assert lovers > refusers
