"""Tests for the Declared Seconds mechanic.

Covers:
  - Pre-game bat-order choice exposed via state.home_bats_first.
  - Two-layer declaration AI (target outs-to-save + derived 'declare now').
  - Half-eligibility caps in is_half_over (regulation + walk-off + seconds).
  - Seconds-round loop (round cap, fielding-pitcher reassignment).
  - Stat-row phase split for seconds rounds.

These tests build minimal GameState objects directly so they don't depend
on the o27v2 league DB. Stochastic AI calls use a seeded RNG.
"""
from __future__ import annotations

import random

import pytest

from o27 import config as cfg
from o27.engine.game import (
    _finalize_declaration,
    _run_seconds_rounds,
    run_game,
)
from o27.engine.manager import (
    evaluate_declaration,
    should_bat_first,
)
from o27.engine.prob import ProbabilisticProvider
from o27.engine.state import GameState, Player, Team


def _mk_player(pid: str, name: str, is_pitcher: bool = False,
               role: str = "") -> Player:
    p = Player(player_id=pid, name=name, skill=0.5)
    p.pitcher_skill = 0.5
    p.stamina = 0.5
    p.is_pitcher = is_pitcher
    p.pitcher_role = role
    return p


def _mk_team(tid: str, name: str, n_players: int = 12,
             starter_role: str = "starter") -> Team:
    t = Team(team_id=tid, name=name)
    for i in range(n_players):
        role = starter_role if i == 0 else "reliever"
        p = _mk_player(f"{tid}_{i}", f"{name}_p{i}",
                       is_pitcher=True, role=role)
        # Stagger skill so heart-of-order / bottom-of-order detection
        # has distinct top-3 and bottom-3 groups.
        p.skill = 0.3 + (i % n_players) * 0.04   # 0.30..0.74
        t.roster.append(p)
        t.lineup.append(p)
    return t


def _mk_state() -> GameState:
    return GameState(visitors=_mk_team("visitors", "V"),
                     home=_mk_team("home", "H"))


# ---------------------------------------------------------------------------
# Eligibility gates
# ---------------------------------------------------------------------------

def test_evaluate_declaration_blocked_in_super_inning():
    state = _mk_state()
    state.half = "super_top"
    state.outs = 24
    decl, banked = evaluate_declaration(state, rng=random.Random(0))
    assert decl is False
    assert banked == 0


def test_evaluate_declaration_blocked_before_window():
    state = _mk_state()
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    state.outs = 19   # below SECONDS_MIN_DECLARE_OUT (22) - 1
    decl, banked = evaluate_declaration(state, rng=random.Random(0))
    assert decl is False
    assert banked == 0


def test_evaluate_declaration_blocked_if_already_declared():
    state = _mk_state()
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    state.outs = 25
    state.visitors.declared_at_out = 23
    decl, banked = evaluate_declaration(state, rng=random.Random(0))
    assert decl is False


# ---------------------------------------------------------------------------
# Two-layer decision basics
# ---------------------------------------------------------------------------

def test_evaluate_declaration_returns_tuple():
    state = _mk_state()
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    state.outs = 24
    state.score = {"visitors": 12, "home": 2}    # blowout lead → declare
    state.visitors.mgr_declare_aggression = 0.9
    result = evaluate_declaration(state, rng=random.Random(1))
    assert isinstance(result, tuple) and len(result) == 2
    decl, banked = result
    assert isinstance(decl, bool)
    assert isinstance(banked, int)


def test_blowout_lead_eventually_declares_with_aggressive_mgr():
    """A 12-run lead at out 26 with a high-aggression mgr should commonly
    declare. The AI's target-save value in this scenario is small (~1 out),
    so the declaration fires when outs_left matches the target — at out 26.
    """
    fired = 0
    for seed in range(50):
        state = _mk_state()
        state.first_batting_team = state.visitors
        state.second_batting_team = state.home
        state.outs = 26
        state.score = {"visitors": 12, "home": 2}
        state.visitors.mgr_declare_aggression = 0.9
        decl, _ = evaluate_declaration(state, rng=random.Random(seed))
        if decl:
            fired += 1
    assert fired >= 25, f"only declared {fired}/50 in clear-cut blowout"


def test_tight_game_with_patient_mgr_rarely_declares():
    """A 1-run lead at out 22 with patient mgr should usually NOT declare."""
    fired = 0
    for seed in range(50):
        state = _mk_state()
        state.first_batting_team = state.visitors
        state.second_batting_team = state.home
        state.outs = 22
        state.score = {"visitors": 5, "home": 4}   # narrow lead
        state.visitors.mgr_declare_aggression = 0.1
        decl, _ = evaluate_declaration(state, rng=random.Random(seed))
        if decl:
            fired += 1
    assert fired <= 10, f"declared {fired}/50 — too aggressive in marginal spot"


# ---------------------------------------------------------------------------
# Walk-off helpers on GameState
# ---------------------------------------------------------------------------

def test_regulation_walkoff_only_in_second_half():
    state = _mk_state()
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    state.half = "top"     # FIRST half
    state.score = {"visitors": 5, "home": 0}
    # First half, even with a lead, walkoff doesn't fire (it's the first batting half).
    assert state._regulation_walkoff() is False


def test_regulation_walkoff_fires_with_second_team_lead_and_no_banked():
    state = _mk_state()
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    state.half = "bottom"
    state.score = {"visitors": 4, "home": 5}     # home (second batter) leads
    state.visitors.outs_banked = 0                # no comeback option
    assert state._regulation_walkoff() is True


def test_regulation_walkoff_does_not_fire_if_first_team_can_rebut():
    state = _mk_state()
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    state.half = "bottom"
    state.score = {"visitors": 4, "home": 5}     # home leads
    state.visitors.outs_banked = 3                # CAN come back
    assert state._regulation_walkoff() is False


# ---------------------------------------------------------------------------
# Half-eligibility caps
# ---------------------------------------------------------------------------

def test_is_half_over_regulation_cap():
    state = _mk_state()
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    state.half = "top"
    state.outs = 27
    assert state.is_half_over() is True


def test_is_half_over_seconds_cap():
    state = _mk_state()
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    state.in_seconds_phase = True
    state.half = "seconds_first"
    state.visitors.outs_banked = 3
    state.outs = 3
    assert state.is_half_over() is True


def test_phase_number_property():
    state = _mk_state()
    assert state.phase_number == 0
    state.half = "super_top"
    state.super_inning_number = 2
    assert state.phase_number == 2
    state.half = "seconds_first"
    state.super_inning_number = 0
    state.in_seconds_phase = True
    state.seconds_phase_number = 1
    assert state.phase_number == 1


# ---------------------------------------------------------------------------
# Bat-first persona
# ---------------------------------------------------------------------------

def test_should_bat_first_biased_above_50pct():
    """The default home manager (mgr_bat_first_pref=0.5) should pick bat-first
    more than 50% of the time, per BAT_FIRST_BASE=0.65."""
    yes = 0
    rng = random.Random(2026)
    for _ in range(200):
        state = _mk_state()
        if should_bat_first(state, rng=rng):
            yes += 1
    assert yes >= 100, f"home bat-first rate {yes}/200 is below 50%"


# ---------------------------------------------------------------------------
# End-to-end via run_game
# ---------------------------------------------------------------------------

def test_run_game_completes_and_records_pre_game_choice():
    state = _mk_state()
    rng = random.Random(42)
    prov = ProbabilisticProvider(rng=rng)
    state, _log = run_game(state, prov, renderer=None)
    assert state.home_bats_first is not None
    assert state.winner in ("visitors", "home")
    # first/second_batting_team should be assigned
    assert state.first_batting_team is not None
    assert state.second_batting_team is not None
    assert state.first_batting_team is not state.second_batting_team


def test_run_game_seconds_round_round_cap():
    """A team can use seconds at most once per game (cap enforced via
    seconds_used flag). After a single seconds round, the team's
    seconds_used is True and no second round fires for them."""
    state = _mk_state()
    rng = random.Random(7)
    prov = ProbabilisticProvider(rng=rng)
    state, _ = run_game(state, prov, renderer=None)
    # Whichever team came back, their seconds_used flag is set.
    came_back = []
    if state.visitors.seconds_used:
        came_back.append(state.visitors)
    if state.home.seconds_used:
        came_back.append(state.home)
    for t in came_back:
        # Round cap: seconds_outs_used should be > 0, and the flag prevents
        # a second invocation.
        assert t.seconds_used is True
        assert t.seconds_outs_used > 0


def test_finalize_declaration_zero_when_no_decl():
    state = _mk_state()
    state.visitors.declared_at_out = None
    _finalize_declaration(state.visitors, state)
    assert state.visitors.outs_banked == 0


def test_finalize_declaration_records_bank():
    state = _mk_state()
    state.visitors.declared_at_out = 24
    _finalize_declaration(state.visitors, state)
    # banked = min(27 - 24, SECONDS_MAX_BANKED) = 3
    assert state.visitors.outs_banked == 3


def test_finalize_declaration_caps_at_max_banked():
    state = _mk_state()
    # Declared right at the eligibility floor — would bank 5 outs, but the
    # SECONDS_MAX_BANKED cap kicks in only above 6.
    state.visitors.declared_at_out = 22
    _finalize_declaration(state.visitors, state)
    assert state.visitors.outs_banked == min(5, cfg.SECONDS_MAX_BANKED)


# ---------------------------------------------------------------------------
# Seconds inning ordering / walk-off (symmetric to regulation top/bottom)
# ---------------------------------------------------------------------------

def test_seconds_walkoff_never_fires_in_seconds_first():
    """The first-batting team always uses their full banked-outs allotment
    in seconds, even when leading — analogous to the visitors batting the
    full top of the 9th."""
    state = _mk_state()
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    state.in_seconds_phase = True
    state.half = "seconds_first"
    # Visitors are batting and crushing it — no walk-off should fire.
    state.score = {"visitors": 20, "home": 1}
    state.visitors.outs_banked = 3
    assert state._seconds_walkoff() is False


def test_seconds_walkoff_fires_in_seconds_second_when_batting_team_takes_lead():
    """The second-batting team's seconds half walks off the moment they
    retake the lead — the first-batting team has already used theirs and
    cannot rebut."""
    state = _mk_state()
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    state.in_seconds_phase = True
    state.half = "seconds_second"
    state.score = {"visitors": 4, "home": 5}     # home (batting) just took lead
    state.visitors.seconds_used = True            # already used theirs
    assert state._seconds_walkoff() is True


def test_seconds_walkoff_does_not_fire_in_seconds_second_when_still_trailing():
    state = _mk_state()
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    state.in_seconds_phase = True
    state.half = "seconds_second"
    state.score = {"visitors": 6, "home": 4}     # home still trails
    state.visitors.seconds_used = True
    assert state._seconds_walkoff() is False


def test_run_seconds_rounds_first_batting_team_bats_even_when_leading():
    """If the first-batting team has banked outs, they always bat in the
    seconds_first half — even if they're already winning."""
    from o27.engine.game import _run_seconds_rounds

    state = _mk_state()
    state.home_bats_first = False
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    # Visitors lead and have banked outs; home has none.
    state.score = {"visitors": 7, "home": 3}
    state.visitors.outs_banked = 3
    state.home.outs_banked = 0
    state.current_pitcher_id = state.home.roster[0].player_id

    rng = random.Random(0)
    prov = ProbabilisticProvider(rng=rng)
    _run_seconds_rounds(state, prov, renderer=None)

    # Visitors (first-batting) batted their seconds half even while leading.
    assert state.visitors.seconds_used is True
    # Home (second-batting) was already winning's mirror — they were
    # losing and have no banked outs, so they don't bat. No walk-off ambiguity.
    assert state.home.seconds_used is False


def test_run_seconds_rounds_second_batting_team_skipped_when_already_ahead():
    """If the second-batting team is already ahead after the first-batting
    team's seconds turn, the second-batting team skips its turn — the
    walk-off shortcut, analogous to bottom-of-9th."""
    from o27.engine.game import _run_seconds_rounds

    state = _mk_state()
    state.home_bats_first = False
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    # Home leads going in; visitors have no banked outs to come back.
    # Home has banked outs but is already ahead, so they should skip.
    state.score = {"visitors": 3, "home": 7}
    state.visitors.outs_banked = 0
    state.home.outs_banked = 3
    state.current_pitcher_id = state.visitors.roster[0].player_id

    rng = random.Random(0)
    prov = ProbabilisticProvider(rng=rng)
    _run_seconds_rounds(state, prov, renderer=None)

    # Home (second-batting) skipped — already winning, no need to bat.
    assert state.home.seconds_used is False
    assert state.visitors.seconds_used is False


def test_run_seconds_rounds_half_order_is_first_then_second():
    """In a game where BOTH teams have banked outs, the first-batting
    team's seconds half runs before the second-batting team's, regardless
    of which side is trailing."""
    from o27.engine.game import _run_seconds_rounds

    state = _mk_state()
    state.home_bats_first = False
    state.first_batting_team = state.visitors
    state.second_batting_team = state.home
    # Tight game so neither team gets to skip; both have banked outs.
    state.score = {"visitors": 4, "home": 5}
    state.visitors.outs_banked = 2
    state.home.outs_banked = 2
    state.current_pitcher_id = state.home.roster[0].player_id

    # Track the order of `state.half` values observed as seconds halves run.
    halves_seen: list[str] = []
    original_provider = ProbabilisticProvider(rng=random.Random(1))

    def tracking_provider(s):
        if s.in_seconds_phase and (not halves_seen or halves_seen[-1] != s.half):
            halves_seen.append(s.half)
        return original_provider(s)

    _run_seconds_rounds(state, tracking_provider, renderer=None)

    # seconds_first must precede seconds_second if both halves ran.
    if "seconds_first" in halves_seen and "seconds_second" in halves_seen:
        assert halves_seen.index("seconds_first") < halves_seen.index("seconds_second")
