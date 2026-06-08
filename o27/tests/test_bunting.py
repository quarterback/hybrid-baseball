"""Bunting decision + outcome-roll tests.

Engine-level: exercises manager.should_bunt routing (which bunt type fires for
a given base state) and the outcome-roll helpers, using minimal duck-typed
fakes — no DB, no flask.

Run:  python -m pytest o27/tests/test_bunting.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from o27.engine import manager as M


class _Rng:
    """Deterministic RNG stub: random() always returns `v`."""
    def __init__(self, v=0.01):
        self.v = v
    def random(self):
        return self.v


class _Batter:
    def __init__(self, *, speed=0.5, power=0.5, bunt=0.5, is_pitcher=False):
        self.speed = speed
        self.power = power
        self.bunt = bunt
        self.is_pitcher = is_pitcher
        self.name = "Bunter"
        self.player_id = "b1"


class _Team:
    def __init__(self, run_game=0.9, leverage=0.2):
        self.mgr_run_game = run_game
        self.mgr_leverage_aware = leverage


class _State:
    def __init__(self, bases, batter, *, outs=4, half="top",
                 score=None, shift="none"):
        self.bases = bases
        self.current_batter = batter
        self.batting_team = _Team()
        self.outs = outs
        self.half = half
        self.score = score or {"visitors": 0, "home": 0}
        self.current_ab_shift_type = shift
    def get_current_pitcher(self):
        return None


# ---------------------------------------------------------------------------
# Outcome-roll helpers — always return a valid, type-tagged token
# ---------------------------------------------------------------------------

_SAC_OUTS   = {"hit", "lead_out", "fail", "sacrifice"}
_DRAG_OUTS  = {"hit", "out_productive"}
_SQZ_OUTS   = {"squeeze_score", "squeeze_score_hit", "squeeze_miss", "squeeze_hold"}


def test_roll_sacrifice_tokens():
    for v in (0.0, 0.2, 0.5, 0.95, 0.999):
        ev = M._roll_sacrifice(_Rng(v), 0.6, 0.6, 0.1, single_runner=True)
        assert ev["bunt_type"] == "sac"
        assert ev["outcome"] in _SAC_OUTS


def test_roll_sacrifice_multi_runner_never_lead_out():
    # lead_out is only valid with a single runner; multi-runner must not emit it.
    for v in (0.0, 0.2, 0.5, 0.95):
        ev = M._roll_sacrifice(_Rng(v), 0.5, 0.5, 0.1, single_runner=False)
        assert ev["outcome"] != "lead_out"


def test_roll_drag_tokens():
    for v in (0.0, 0.5, 0.999):
        ev = M._roll_drag(_Rng(v), 0.8, 0.6, 0.1)
        assert ev["bunt_type"] == "drag" and ev["outcome"] in _DRAG_OUTS


def test_roll_squeeze_tokens():
    for v in (0.0, 0.3, 0.6, 0.999):
        ev = M._roll_squeeze(_Rng(v), 0.6, 0.6, 0.1)
        assert ev["bunt_type"] in ("suicide", "safety")
        assert ev["outcome"] in _SQZ_OUTS


# ---------------------------------------------------------------------------
# should_bunt routing — base state selects the bunt type
# ---------------------------------------------------------------------------

def test_runner_on_third_calls_squeeze():
    st = _State([None, None, "r3"], _Batter(speed=0.5, power=0.4, bunt=0.6))
    ev = M.should_bunt(st, _Rng(0.0))
    assert ev is not None and ev["bunt_type"] in ("suicide", "safety")


def test_runner_on_first_calls_sacrifice():
    st = _State(["r1", None, None], _Batter(speed=0.5, power=0.4, bunt=0.6))
    ev = M.should_bunt(st, _Rng(0.0))
    assert ev is not None and ev["bunt_type"] == "sac"


def test_fast_weak_empty_bases_calls_drag():
    st = _State([None, None, None], _Batter(speed=0.85, power=0.3, bunt=0.6))
    ev = M.should_bunt(st, _Rng(0.0))
    assert ev is not None and ev["bunt_type"] == "drag"


def test_slugger_never_bunts():
    st = _State(["r1", None, None], _Batter(speed=0.5, power=0.95, bunt=0.6))
    assert M.should_bunt(st, _Rng(0.0)) is None


def test_pitcher_sacrifices_with_runner_on_first():
    # O27 has no DH — the weak-hitting pitcher is the classic sacrifice
    # bunter. With a runner on first and outs to spare he lays one down.
    st = _State(["r1", None, None], _Batter(power=0.3, is_pitcher=True))
    ev = M.should_bunt(st, _Rng(0.0))
    assert ev is not None and ev["bunt_type"] == "sac"


def test_pitcher_does_not_drag_or_squeeze():
    # Pitchers only ever sacrifice — never drag (too slow) and never squeeze.
    # Bases empty: nothing to sacrifice, so no bunt despite the low roll.
    st = _State([None, None, None], _Batter(speed=0.9, power=0.3, is_pitcher=True))
    assert M.should_bunt(st, _Rng(0.0)) is None
    # Runner on third only (a squeeze spot for a position player): a pitcher
    # still doesn't squeeze.
    st3 = _State([None, None, "r3"], _Batter(power=0.3, is_pitcher=True))
    assert M.should_bunt(st3, _Rng(0.0)) is None


def test_no_runners_slow_bat_no_bunt():
    st = _State([None, None, None], _Batter(speed=0.4, power=0.4, bunt=0.6))
    assert M.should_bunt(st, _Rng(0.0)) is None


def test_high_roll_declines_bunt():
    # A roll above every gate's probability never calls a bunt.
    st = _State(["r1", None, None], _Batter(speed=0.5, power=0.4, bunt=0.6))
    assert M.should_bunt(st, _Rng(0.999)) is None
