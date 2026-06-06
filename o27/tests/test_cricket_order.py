"""
Cricket Batting Order — optional rule.

Verifies the order flips 1-9 -> 9-1 at the end of a joker-free trip through the
lineup, that deploying a joker locks the order for that cycle, and that the rule
is byte-for-byte inert when off (the default).
"""
import pytest

from o27 import config as cfg
from o27.engine import cricket_order
from o27.engine.state import Team


class _Bat:
    """Minimal stand-in for a lineup Player — only `.name` is read."""
    def __init__(self, name):
        self.name = name


def _team(enabled=None, n=9):
    lineup = [_Bat(chr(ord("A") + i)) for i in range(n)]
    return Team(team_id="home", name="Test", lineup=lineup,
                cricket_order_enabled=enabled)


def _names(team):
    return [b.name for b in team.lineup]


def _run_cycle(team, jokers_used=()):
    """Advance one full trip through the order. Optionally mark jokers as used
    during the trip (added before the wrapping call, as in a real game)."""
    n = len(team.lineup)
    flips = []
    for i in range(n):
        if i == n - 1 and jokers_used:
            team.jokers_used_this_cycle = set(jokers_used)
        flips.append(team.advance_lineup())
    return flips


# ---------------------------------------------------------------------------
# Rule OFF (default) — no behavior change
# ---------------------------------------------------------------------------

def test_off_by_default_no_flip(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", False, raising=False)
    team = _team(enabled=None)
    before = _names(team)
    flips = _run_cycle(team)
    assert _names(team) == before          # order untouched
    assert all(f is None for f in flips)   # no log line emitted
    assert team.lineup_position == 0       # wrapped cleanly


def test_per_team_off_overrides_global_on(monkeypatch):
    # Global default ON, but this team explicitly opted OUT (False, not None).
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", True, raising=False)
    team = _team(enabled=False)
    before = _names(team)
    _run_cycle(team)
    assert _names(team) == before


# ---------------------------------------------------------------------------
# Rule ON — joker-free trip flips the order
# ---------------------------------------------------------------------------

def test_joker_free_cycle_flips(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", False, raising=False)
    team = _team(enabled=True)
    before = _names(team)
    flips = _run_cycle(team)
    assert _names(team) == list(reversed(before))   # 1-9 -> 9-1
    # Exactly one flip, emitted on the wrapping (last) advance.
    assert sum(1 for f in flips if f) == 1
    assert flips[-1] is not None and "leads off" in flips[-1]
    assert before[-1] in flips[-1]                  # old #9 now leads off


def test_two_joker_free_cycles_flip_back(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", False, raising=False)
    team = _team(enabled=True)
    before = _names(team)
    _run_cycle(team)
    _run_cycle(team)
    assert _names(team) == before                   # flipped, then flipped back


# ---------------------------------------------------------------------------
# Rule ON — a joker locks the order for that cycle
# ---------------------------------------------------------------------------

def test_joker_used_locks_order(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", False, raising=False)
    team = _team(enabled=True)
    before = _names(team)
    flips = _run_cycle(team, jokers_used=("joker-1",))
    assert _names(team) == before                   # no flip this trip
    assert all(f is None for f in flips)


def test_joker_only_locks_its_own_cycle(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", False, raising=False)
    team = _team(enabled=True)
    before = _names(team)
    _run_cycle(team, jokers_used=("joker-1",))       # locked — no flip
    assert _names(team) == before
    _run_cycle(team)                                 # joker-free — flips
    assert _names(team) == list(reversed(before))


# ---------------------------------------------------------------------------
# Gate composition + edge cases
# ---------------------------------------------------------------------------

def test_global_default_drives_when_no_override(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", True, raising=False)
    team = _team(enabled=None)                       # falls back to global
    assert cricket_order.cricket_order_on(team) is True
    before = _names(team)
    _run_cycle(team)
    assert _names(team) == list(reversed(before))


def test_short_lineup_no_crash(monkeypatch):
    monkeypatch.setattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", False, raising=False)
    team = _team(enabled=True, n=1)
    flips = _run_cycle(team)                          # single batter wraps every PA
    assert _names(team) == ["A"]
    assert all(f is None for f in flips)              # nothing to flip
