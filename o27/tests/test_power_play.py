"""Tests for the Power Play optional rule (the nickel fielder).

These build minimal GameState objects directly (no o27v2 DB) and force the
relevant config knobs so the stochastic paths are deterministic. The rule is
off by default, so the headline guarantee is: with the rule off, none of this
fires.
"""
from __future__ import annotations

import random

import pytest

from o27 import config as cfg
from o27.engine import power_play as pp
from o27.engine.state import GameState, Player, Team


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _mk_fielder(pid: str, name: str, position: str = "CF",
                arm: float = 0.5, glove: float = 0.5) -> Player:
    p = Player(player_id=pid, name=name, skill=0.5)
    p.position = position
    p.role_field_pos = position
    p.arm = arm
    p.defense = glove
    p.defense_outfield = glove
    p.defense_infield = glove
    return p


def _mk_pitcher(pid: str, name: str, arm: float = 0.5, glove: float = 0.5,
                position: str = "P") -> Player:
    p = Player(player_id=pid, name=name, skill=0.5)
    p.is_pitcher = True
    p.pitcher_skill = 0.5
    p.position = position
    p.role_field_pos = position
    p.arm = arm
    p.defense = glove
    p.defense_outfield = glove
    p.defense_infield = glove
    return p


def _mk_team(tid: str, name: str) -> Team:
    t = Team(team_id=tid, name=name)
    # Starting nine: 8 fielders + SP, all in the lineup / on the field.
    positions = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF"]
    for i, pos in enumerate(positions):
        p = _mk_fielder(f"{tid}_f{i}", f"{name}_{pos}", position=pos,
                        arm=0.5, glove=0.5)
        t.roster.append(p)
        t.lineup.append(p)
    sp = _mk_pitcher(f"{tid}_sp", f"{name}_SP")
    t.roster.append(sp)
    t.lineup.append(sp)
    return t


def _mk_state() -> GameState:
    # Top half → home fields (home is the deploying / fielding team).
    return GameState(visitors=_mk_team("visitors", "V"),
                     home=_mk_team("home", "H"))


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------

def test_rule_off_by_default():
    state = _mk_state()
    assert pp.power_play_on(state) is False
    assert pp.format_powerplays_line(state) is None
    # With the rule off, deploying is a no-op even if asked.
    pp.maybe_open_window(state, random.Random(0))
    assert state.power_play_open_out is None
    assert pp.is_window_active(state) is False


def test_per_game_override_beats_config():
    state = _mk_state()
    state.power_play_enabled = True
    assert pp.power_play_on(state) is True
    state.power_play_enabled = False
    assert pp.power_play_on(state) is False


# ---------------------------------------------------------------------------
# Nickel eligibility
# ---------------------------------------------------------------------------

def _team_with_bench(*bench: Player) -> Team:
    t = _mk_team("home", "H")
    t.bench = list(bench)
    t.roster.extend(bench)
    return t


def test_find_nickel_picks_strong_arm_glove():
    state = _mk_state()
    good = _mk_fielder("b_good", "Good", position="CF",
                       arm=0.9, glove=0.8)
    weak_arm = _mk_fielder("b_weakarm", "WeakArm", position="RF",
                           arm=0.3, glove=0.9)
    team = _team_with_bench(good, weak_arm)
    assert pp.find_nickel(state, team) == "b_good"


def test_find_nickel_allows_shortstop():
    state = _mk_state()
    ss = _mk_fielder("b_ss", "SoftHands", position="SS",
                     arm=0.85, glove=0.75)
    team = _team_with_bench(ss)
    assert pp.find_nickel(state, team) == "b_ss"


def test_find_nickel_rejects_ineligible_position():
    state = _mk_state()
    firstbase = _mk_fielder("b_1b", "Cornerguy", position="1B",
                            arm=0.95, glove=0.95)
    team = _team_with_bench(firstbase)
    assert pp.find_nickel(state, team) is None


def test_find_nickel_prefers_position_player_over_pitcher():
    state = _mk_state()
    pos = _mk_fielder("b_pos", "Outfielder", position="CF",
                      arm=0.75, glove=0.70)
    arm_pitcher = _mk_pitcher("b_p", "TwoWay", arm=0.95, glove=0.85,
                              position="P")
    arm_pitcher.role_field_pos = "P,RF"   # two-way eligibility
    team = _team_with_bench(arm_pitcher, pos)
    assert pp.find_nickel(state, team) == "b_pos"


def test_find_nickel_wildcard_pitcher_when_no_position_player():
    state = _mk_state()
    arm_pitcher = _mk_pitcher("b_p", "TwoWay", arm=0.95, glove=0.85)
    arm_pitcher.role_field_pos = "P,CF"
    team = _team_with_bench(arm_pitcher)
    assert pp.find_nickel(state, team) == "b_p"


def test_find_nickel_excludes_pitcher_who_appeared():
    state = _mk_state()
    arm_pitcher = _mk_pitcher("b_p", "TwoWay", arm=0.95, glove=0.85)
    arm_pitcher.role_field_pos = "P,CF"
    team = _team_with_bench(arm_pitcher)
    # He's the guy on the mound — you never field a pitcher who's pitched.
    state.current_pitcher_id = "b_p"
    assert pp.find_nickel(state, team) is None


def test_find_nickel_excludes_on_field_player():
    state = _mk_state()
    team = _mk_team("home", "H")
    # The starting CF is strong but already on the field — not eligible.
    team.lineup[6].arm = 0.95
    team.lineup[6].defense_outfield = 0.95
    team.bench = []
    assert pp.find_nickel(state, team) is None


# ---------------------------------------------------------------------------
# Window lifecycle
# ---------------------------------------------------------------------------

def test_window_opens_and_expires_after_window_outs(monkeypatch):
    monkeypatch.setattr(cfg, "POWER_PLAY_DEPLOY_BASE_EARLY", 1.0)
    monkeypatch.setattr(cfg, "POWER_PLAY_DEPLOY_BASE_MID", 1.0)
    state = _mk_state()
    state.power_play_enabled = True
    nickel = _mk_fielder("home_nf", "Nickel", position="CF", arm=0.9, glove=0.9)
    state.home.bench = [nickel]
    state.home.roster.append(nickel)
    state.outs = 13
    pp.maybe_open_window(state, random.Random(1))
    assert pp.is_window_active(state)
    assert state.power_play_deploy_team_id == "home"
    rec = state.power_play_deployments[-1]
    assert rec["start_out"] == 14

    # Simulate 4 outs ticking through note_out.
    for _ in range(cfg.POWER_PLAY_WINDOW_OUTS):
        state.outs += 1
        pp.note_out(state)
    assert pp.is_window_active(state) is False
    assert state.power_play_open_out is None
    assert state.power_play_deployments[-1]["end_out"] == 17


def test_window_use_or_lose_once_per_half(monkeypatch):
    monkeypatch.setattr(cfg, "POWER_PLAY_DEPLOY_BASE_MID", 1.0)
    state = _mk_state()
    state.power_play_enabled = True
    nickel = _mk_fielder("home_nf", "Nickel", position="CF", arm=0.9, glove=0.9)
    state.home.bench = [nickel]
    state.home.roster.append(nickel)
    state.outs = 12
    pp.maybe_open_window(state, random.Random(2))
    assert pp.is_window_active(state)
    # Expire it.
    for _ in range(cfg.POWER_PLAY_WINDOW_OUTS):
        state.outs += 1
        pp.note_out(state)
    # Same half (phase 0, same fielding team) — cannot reopen.
    pp.maybe_open_window(state, random.Random(3))
    assert pp.is_window_active(state) is False
    assert len(state.power_play_deployments) == 1


def test_never_deploys_in_super_inning(monkeypatch):
    monkeypatch.setattr(cfg, "POWER_PLAY_DEPLOY_BASE_FORCED", 1.0)
    state = _mk_state()
    state.power_play_enabled = True
    state.half = "super_top"
    state.super_inning_number = 1
    state.outs = 28
    pp.maybe_open_window(state, random.Random(4))
    assert pp.is_window_active(state) is False
    assert state.power_play_deployments == []


def test_clear_window_prevents_carryover():
    state = _mk_state()
    state.power_play_open_out = 13
    state.power_play_deploy_team_id = "home"
    state.power_play_nickel_id = "x"
    pp.clear_window(state)
    assert pp.is_window_active(state) is False
    assert state.power_play_open_out is None


def test_blowout_suppresses_deploy(monkeypatch):
    monkeypatch.setattr(cfg, "POWER_PLAY_DEPLOY_BASE_LATE", 1.0)
    monkeypatch.setattr(cfg, "POWER_PLAY_DEPLOY_BASE_FORCED", 1.0)
    state = _mk_state()
    state.power_play_enabled = True
    state.outs = 24
    state.score = {"visitors": 12, "home": 1}   # 11-run gap, out of hand
    pp.maybe_open_window(state, random.Random(5))
    assert pp.is_window_active(state) is False


# ---------------------------------------------------------------------------
# Fielding effect
# ---------------------------------------------------------------------------

def _open_window(state, nickel_id="home_nf"):
    nickel = _mk_fielder(nickel_id, "Nickel", position="CF",
                         arm=0.9, glove=0.9)
    state.home.bench = [nickel]
    state.home.roster.append(nickel)
    state.power_play_open_out = state.outs
    state.power_play_deploy_team_id = "home"
    state.power_play_nickel_id = nickel_id


def test_nickel_holds_xbh_to_single(monkeypatch):
    monkeypatch.setattr(cfg, "POWER_PLAY_XBH_HELD_PROB", 1.0)
    state = _mk_state()
    state.power_play_enabled = True
    state.outs = 14
    _open_window(state)
    ht, safe, fly, po = pp.apply_nickel_defense(
        random.Random(0), state, "double", True, False)
    assert ht == "single"
    assert state.home.pp_xbh_held == 1


def test_nickel_runs_down_single_for_out(monkeypatch):
    monkeypatch.setattr(cfg, "POWER_PLAY_SINGLE_OUT_PROB", 1.0)
    state = _mk_state()
    state.power_play_enabled = True
    state.outs = 14
    _open_window(state)
    ht, safe, fly, po = pp.apply_nickel_defense(
        random.Random(0), state, "single", True, False)
    assert ht == "fly_out"
    assert safe is False and fly is True and po is True
    assert state.home.pp_hits_converted == 1
    # The nickel is credited with the putout he made.
    assert pp.nickel_putout_for(state, "fly_out", random.Random(0), True) == "home_nf"


def test_nickel_putout_tallies_to_deployment(monkeypatch):
    monkeypatch.setattr(cfg, "POWER_PLAY_DEPLOY_BASE_MID", 1.0)
    state = _mk_state()
    state.power_play_enabled = True
    nickel = _mk_fielder("home_nf", "Reyes", position="CF", arm=0.9, glove=0.9)
    state.home.bench = [nickel]
    state.home.roster.append(nickel)
    state.outs = 13
    pp.maybe_open_window(state, random.Random(1))
    rec = state.power_play_deployments[-1]
    assert rec["nickel_name"] == "Reyes" and rec["po"] == 0
    # Two putouts credited during the window.
    pp.credit_nickel_putout(state)
    pp.credit_nickel_putout(state)
    assert state.power_play_deployments[-1]["po"] == 2
    assert pp.format_powerplays_line(state) == \
        "Powerplays: H — Reyes NF (O14-14, 2 PO)"


def test_nickel_effect_inert_when_window_closed():
    state = _mk_state()
    state.power_play_enabled = True
    # No window open.
    ht, safe, fly, po = pp.apply_nickel_defense(
        random.Random(0), state, "double", True, False)
    assert ht == "double" and po is False


# ---------------------------------------------------------------------------
# Box-score line
# ---------------------------------------------------------------------------

def test_powerplays_line_none_when_unused():
    state = _mk_state()
    state.power_play_enabled = True
    assert pp.format_powerplays_line(state) == "Powerplays: None"


def test_powerplays_line_single_window():
    state = _mk_state()
    state.power_play_enabled = True
    state.power_play_deployments = [
        {"team_id": "visitors", "team_name": "New York", "phase": 0,
         "start_out": 14, "end_out": 17, "nickel_name": "Reyes", "po": 2},
    ]
    assert pp.format_powerplays_line(state) == \
        "Powerplays: New York — Reyes NF (O14-17, 2 PO)"


def test_powerplays_line_omits_zero_po():
    state = _mk_state()
    state.power_play_enabled = True
    state.power_play_deployments = [
        {"team_id": "visitors", "team_name": "New York", "phase": 0,
         "start_out": 14, "end_out": 17, "nickel_name": "Reyes", "po": 0},
    ]
    assert pp.format_powerplays_line(state) == \
        "Powerplays: New York — Reyes NF (O14-17)"


def test_powerplays_line_two_teams():
    state = _mk_state()
    state.power_play_enabled = True
    state.power_play_deployments = [
        {"team_id": "visitors", "team_name": "New York", "phase": 0,
         "start_out": 14, "end_out": 17, "nickel_name": "Reyes", "po": 1},
        {"team_id": "home", "team_name": "Carolina", "phase": 0,
         "start_out": 24, "end_out": 27, "nickel_name": "Jones", "po": 0},
    ]
    assert pp.format_powerplays_line(state) == \
        "Powerplays: New York — Reyes NF (O14-17, 1 PO), Carolina — Jones NF (O24-27)"


def test_powerplays_line_regulation_plus_seconds_same_nickel():
    state = _mk_state()
    state.power_play_enabled = True
    state.power_play_deployments = [
        {"team_id": "home", "team_name": "Boston", "phase": 0,
         "start_out": 11, "end_out": 14, "nickel_name": "Reyes", "po": 2},
        {"team_id": "home", "team_name": "Boston", "phase": 1,
         "start_out": 25, "end_out": 27, "nickel_name": "Reyes", "po": 1},
    ]
    assert pp.format_powerplays_line(state) == \
        "Powerplays: Boston — Reyes NF (1: O11, 2: O25, 3 PO)"


def test_powerplays_line_regulation_plus_seconds_diff_nickels():
    state = _mk_state()
    state.power_play_enabled = True
    state.power_play_deployments = [
        {"team_id": "home", "team_name": "Boston", "phase": 0,
         "start_out": 11, "end_out": 14, "nickel_name": "Reyes", "po": 0},
        {"team_id": "home", "team_name": "Boston", "phase": 1,
         "start_out": 25, "end_out": 27, "nickel_name": "Ortiz", "po": 1},
    ]
    assert pp.format_powerplays_line(state) == \
        "Powerplays: Boston — Reyes NF (1: O11), Ortiz NF (2: O25, 1 PO)"
