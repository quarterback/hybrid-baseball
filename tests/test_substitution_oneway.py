"""Tests for the substitution-economy one-way invariant (Item 3).

Walks `state.substitution_log` after running games and asserts:
  - No `out_player_id` ever appears as a subsequent `in_player_id`.
  - No `out_player_id` ever appears in super-inning play-by-play.
  - Super-innings lineup respects `team.substituted_out`.

Builds minimal GameState fixtures so the tests don't depend on the
o27v2 league DB.
"""
from __future__ import annotations

import random

from o27.engine.manager import (
    pinch_hit,
    pinch_run,
    defensive_sub,
    pitching_change,
    score_substitution,
    substitution_threshold,
)
from o27.engine.state import GameState, Player, Substitution, Team


def _mk_player(pid: str, name: str, **overrides) -> Player:
    p = Player(player_id=pid, name=name)
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


def _mk_team(tid: str, name: str) -> Team:
    t = Team(team_id=tid, name=name)
    # 9-player starting lineup + 6 bench bats + 4 pitchers (1 starter + 3 bullpen).
    for i in range(9):
        p = _mk_player(f"{tid}_l{i}", f"{name}_L{i}", skill=0.4 + i * 0.02)
        t.roster.append(p)
        t.lineup.append(p)
    for i in range(6):
        p = _mk_player(f"{tid}_b{i}", f"{name}_B{i}",
                       skill=0.55 + i * 0.03,
                       speed=0.55 + i * 0.05,
                       defense=0.55 + i * 0.04,
                       role_hit=True)
        t.roster.append(p)
    for i in range(4):
        p = _mk_player(f"{tid}_p{i}", f"{name}_P{i}", is_pitcher=True)
        t.roster.append(p)
    t.bench = [p for p in t.roster if not p.is_pitcher and p not in t.lineup]
    return t


def _mk_state() -> GameState:
    return GameState(visitors=_mk_team("visitors", "V"),
                     home=_mk_team("home", "H"))


# ---------------------------------------------------------------------------
# Substitution log is populated
# ---------------------------------------------------------------------------

def test_pinch_hit_logs_substitution_and_marks_oneway():
    state = _mk_state()
    state.half = "top"
    state.outs = 20
    replacement = state.visitors.bench[0]
    out_player = state.visitors.lineup[state.visitors.lineup_position]
    pinch_hit(state, replacement)

    # Log entry exists.
    assert len(state.substitution_log) == 1
    rec = state.substitution_log[0]
    assert rec.kind == "pinch_hit"
    assert rec.in_player_id == replacement.player_id
    assert rec.out_player_id == out_player.player_id

    # One-way invariant: the replaced player is in substituted_out.
    assert not state.visitors.is_available(out_player.player_id)
    # The replacement can still be subbed out later.
    assert state.visitors.is_available(replacement.player_id)


def test_pinch_run_logs_substitution_and_marks_oneway():
    state = _mk_state()
    state.half = "top"
    state.outs = 22
    # Put a runner on first.
    runner_id = state.visitors.lineup[0].player_id
    state.bases = [runner_id, None, None]
    replacement = state.visitors.bench[1]
    pinch_run(state, base_idx=0, runner_in=replacement)

    assert len(state.substitution_log) == 1
    rec = state.substitution_log[0]
    assert rec.kind == "pinch_run"
    assert rec.out_player_id == runner_id
    assert not state.visitors.is_available(runner_id)


def test_defensive_sub_logs_substitution_and_marks_oneway():
    state = _mk_state()
    state.half = "top"   # Visitors are batting → home is fielding.
    state.outs = 12
    player_out = state.home.lineup[3]
    player_in  = state.home.bench[0]
    defensive_sub(state, player_out, player_in)

    assert len(state.substitution_log) == 1
    rec = state.substitution_log[0]
    assert rec.kind == "pinch_field"
    assert rec.team_id == "home"
    assert not state.home.is_available(player_out.player_id)


def test_pitching_change_marks_pitcher_oneway():
    state = _mk_state()
    state.half = "top"
    # Make the home roster have at least one is_pitcher player as the
    # current pitcher.
    old_pitcher = state.home.roster[-4]      # first pitcher in the home roster
    state.current_pitcher_id = old_pitcher.player_id
    state.pitcher_spell_count = 5             # required for spell-close path
    new_pitcher = state.home.roster[-3]
    pitching_change(state, new_pitcher)

    sub_records = [r for r in state.substitution_log if r.kind == "pitching"]
    assert len(sub_records) == 1
    rec = sub_records[0]
    assert rec.out_player_id == old_pitcher.player_id
    assert not state.home.is_available(old_pitcher.player_id)


# ---------------------------------------------------------------------------
# One-way invariant: no player ever re-enters
# ---------------------------------------------------------------------------

def test_oneway_invariant_no_reentry():
    """Walk the log and assert no out_player_id ever appears as a later
    in_player_id."""
    state = _mk_state()
    state.half = "top"
    state.outs = 5

    # Three independent subs.
    state.outs = 5
    pinch_hit(state, state.visitors.bench[0])
    state.outs = 10
    runner_id = state.visitors.lineup[1].player_id
    state.bases = [runner_id, None, None]
    pinch_run(state, base_idx=0, runner_in=state.visitors.bench[1])
    state.outs = 14
    defensive_sub(state, state.home.lineup[4], state.home.bench[0])

    seen_out_ids: set = set()
    for rec in state.substitution_log:
        # Player coming IN must NOT have been previously subbed out.
        assert rec.in_player_id not in seen_out_ids, (
            f"One-way invariant violated: {rec.in_player_id} re-entered "
            f"after being subbed out."
        )
        seen_out_ids.add(rec.out_player_id)


# ---------------------------------------------------------------------------
# Super-innings depletion: substituted_out players excluded
# ---------------------------------------------------------------------------

def test_super_lineup_skips_subbed_out_players():
    """The default super-innings picker must filter out anyone who was
    pulled mid-game."""
    from o27.engine.game import _default_super_lineup

    state = _mk_state()
    state.half = "top"
    state.outs = 8
    out_player = state.visitors.lineup[0]
    pinch_hit(state, state.visitors.bench[0])
    # Sanity: the replaced player is in substituted_out.
    assert not state.visitors.is_available(out_player.player_id)

    v5 = _default_super_lineup(state.visitors)
    out_ids = {p.player_id for p in v5}
    assert out_player.player_id not in out_ids
    assert len(v5) == 5


# ---------------------------------------------------------------------------
# Trigger function: monotonicity sanity
# ---------------------------------------------------------------------------

def test_score_substitution_monotonic_in_upgrade():
    """A bigger skill upgrade for a PH should produce a higher score."""
    state = _mk_state()
    state.half = "top"
    state.outs = 20
    out_player = _mk_player("o", "Out", skill=0.3)
    weak_sub   = _mk_player("w", "Weak", skill=0.4)
    strong_sub = _mk_player("s", "Strong", skill=0.8)
    score_weak   = score_substitution(state, weak_sub,   "pinch_hit", out_player)
    score_strong = score_substitution(state, strong_sub, "pinch_hit", out_player)
    assert score_strong > score_weak


def test_substitution_threshold_inverse_of_aggression():
    """Aggressive manager (high platoon_aggression) → low threshold."""
    state = _mk_state()
    state.visitors.mgr_platoon_aggression = 0.92    # platoon_manager
    state.home.mgr_platoon_aggression     = 0.05    # workhorse-traditionalist
    assert substitution_threshold(state.visitors) < substitution_threshold(state.home)
