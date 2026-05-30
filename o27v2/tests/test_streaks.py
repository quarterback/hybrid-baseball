"""Performance-streak tests — ramp math, ignition/revert, and the engine overlay.

Run:  python -m pytest o27v2/tests/test_streaks.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from o27v2 import streaks as s


# ---------------------------------------------------------------------------
# Ramp magnitude
# ---------------------------------------------------------------------------

def test_week1_matches_spec():
    # "+18 per week" — week 1 of a hot streak is +18 grade points.
    assert s.streak_grade_delta(1, 0) == s.STREAK_WEEK1
    assert s.streak_grade_delta(-1, 0) == -s.STREAK_WEEK1


def test_ramp_accelerates_by_week_step():
    # "each week that can go up +5" — week N = WEEK1 + (N-1)*STEP, until capped.
    assert s.streak_grade_delta(1, 1) == s.STREAK_WEEK1 + s.STREAK_WEEK_STEP
    assert s.streak_grade_delta(1, 2) == s.STREAK_WEEK1 + 2 * s.STREAK_WEEK_STEP


def test_capped_both_directions():
    # A long run peaks at the cap, not superhuman.
    assert s.streak_grade_delta(1, 99) == s.STREAK_CAP
    assert s.streak_grade_delta(-1, 99) == -s.STREAK_CAP


def test_no_streak_is_zero():
    assert s.streak_grade_delta(0, 5) == 0.0
    assert s.streak_unit_delta(0, 5) == 0.0


def test_cold_is_mirror_of_hot():
    for w in range(0, 8):
        assert s.streak_grade_delta(-1, w) == -s.streak_grade_delta(1, w)


def test_team_streak_is_lighter():
    # The team overlay is a fraction of the player ramp at the same week.
    full = s.streak_unit_delta(1, 3)
    team = s.streak_unit_delta(1, 3, scale=s.STREAK_TEAM_SCALE)
    assert 0.0 < team < full


# ---------------------------------------------------------------------------
# State machine — ignite, ramp, break/revert (flu-like: slow then ramps)
# ---------------------------------------------------------------------------

def _run_heat(grade_per_game: float, n: int, *, good_decay=None):
    """Drive the streak machine for n games at a constant per-game grade."""
    decay = s.STREAK_HEAT_DECAY
    state, weeks, games, heat = 0, 0, 0, 0.0
    history = []
    for _ in range(n):
        heat += -heat * decay
        heat += grade_per_game * (s.STREAK_HEAT_GOOD if grade_per_game > 0
                                  else s.STREAK_HEAT_BAD)
        heat = max(-1.5, min(1.5, heat))
        state, weeks, games = s._advance_streak(
            state, weeks, games, heat, s.STREAK_IGNITE, s.STREAK_BREAK
        )
        history.append((state, weeks))
    return state, weeks, history


def test_does_not_ignite_on_one_good_game():
    # Flu-like: slow to start. A single good game must not trigger a streak.
    state, _, _ = _run_heat(0.30, 1)
    assert state == 0


def test_sustained_good_play_ignites_and_ramps():
    state, weeks, hist = _run_heat(0.30, 30)
    assert state == 1
    assert weeks >= 1                       # ramped past week 1
    # It ignited only after several games, not immediately.
    assert hist[0][0] == 0


def test_streak_breaks_and_reverts_when_player_cools():
    # Heat up, then go cold — the streak must break back to neutral.
    state, weeks, games, heat = 1, 3, 0, 1.2
    # Feed contrary (bad) games until it breaks.
    for _ in range(10):
        heat += -heat * s.STREAK_HEAT_DECAY - 0.30
        heat = max(-1.5, min(1.5, heat))
        state, weeks, games = s._advance_streak(
            state, weeks, games, heat, s.STREAK_IGNITE, s.STREAK_BREAK
        )
    # Ends up neutral or flipped cold — never stuck hot.
    assert state in (0, -1)


# ---------------------------------------------------------------------------
# Engine overlay — applied to hitters, reverts cleanly, skips pitchers' bats
# ---------------------------------------------------------------------------

class _FakePlayer:
    def __init__(self, is_pitcher=False, **attrs):
        self.is_pitcher = is_pitcher
        for k, v in attrs.items():
            setattr(self, k, v)


def test_overlay_lifts_hitter_attributes():
    p = _FakePlayer(skill=0.5, contact=0.5, power=0.5, eye=0.5)
    row = {"streak_state": 1, "streak_weeks": 0}
    s.apply_player_streak(p, row)
    bump = s.streak_unit_delta(1, 0)
    assert abs(p.power - (0.5 + bump)) < 1e-9
    assert abs(p.contact - (0.5 + bump)) < 1e-9


def test_overlay_clamps_to_unit_range():
    p = _FakePlayer(skill=0.95, contact=0.95, power=0.95, eye=0.95)
    row = {"streak_state": 1, "streak_weeks": 5}     # big hot streak
    s.apply_player_streak(p, row)
    assert p.power <= 1.0 and p.eye <= 1.0


def test_cold_overlay_lowers_attributes():
    p = _FakePlayer(skill=0.5, contact=0.5, power=0.5, eye=0.5)
    row = {"streak_state": -1, "streak_weeks": 0}
    s.apply_player_streak(p, row)
    assert p.power < 0.5


def test_no_streak_is_identity():
    p = _FakePlayer(skill=0.5, contact=0.6, power=0.7, eye=0.4)
    row = {"streak_state": 0, "streak_weeks": 0}
    s.apply_player_streak(p, row)
    assert (p.skill, p.contact, p.power, p.eye) == (0.5, 0.6, 0.7, 0.4)


def test_pitcher_bat_unaffected_by_own_streak_but_gets_team_overlay():
    # A pitcher's own (batting) streak doesn't ride — but a team streak still
    # overlays everyone, including the pitcher's plate appearances.
    p = _FakePlayer(is_pitcher=True, skill=0.5, contact=0.5, power=0.5, eye=0.5)
    row = {"streak_state": 1, "streak_weeks": 4}
    team_delta = s.streak_unit_delta(1, 2, scale=s.STREAK_TEAM_SCALE)
    s.apply_player_streak(p, row, team_delta=team_delta)
    # Only the team delta applied (the +4-week personal ramp was ignored).
    assert abs(p.power - (0.5 + team_delta)) < 1e-9
