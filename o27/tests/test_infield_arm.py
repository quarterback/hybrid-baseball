"""Tests for the infield-arm helper that backs the Tier-3 leg-out mechanic.

`_avg_infield_arm(state)` is the counterweight to batter foot speed on
borderline grounders (throw to first). It mirrors `_avg_outfielder_arm`: it
averages the `arm` rating over the fielding team's 1B/2B/3B/SS and falls back
to neutral 0.5 when positions aren't stamped.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from o27.engine.prob import _avg_infield_arm, _avg_outfielder_arm


def _state(players):
    return SimpleNamespace(fielding_team=SimpleNamespace(lineup=players))


def _p(pos, arm):
    return SimpleNamespace(game_position=pos, position=pos, arm=arm)


def test_averages_infielders_only():
    players = [
        _p("1B", 0.4), _p("2B", 0.6), _p("3B", 0.8), _p("SS", 0.2),
        _p("CF", 1.0), _p("C", 1.0),  # ignored
    ]
    assert abs(_avg_infield_arm(_state(players)) - 0.5) < 1e-9


def test_infield_and_outfield_partition_cleanly():
    players = [_p("SS", 0.9), _p("RF", 0.1)]
    assert _avg_infield_arm(_state(players)) == 0.9
    assert _avg_outfielder_arm(_state(players)) == 0.1


def test_neutral_fallback_when_no_positions():
    players = [_p("", 0.9), _p("", 0.1)]
    assert _avg_infield_arm(_state(players)) == 0.5


def test_empty_lineup_is_neutral():
    assert _avg_infield_arm(_state([])) == 0.5
