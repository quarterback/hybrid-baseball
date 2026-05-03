"""
Unit tests for Phase 8 joker archetype trigger logic.

Covers _needed_archetype() and should_insert_joker() for the trigger
semantics specified in §4.6 with JOKER_POWER_DEFICIT=3:

  Evaluation order (power first):
    1. power   — batting team down >= JOKER_POWER_DEFICIT (=3) with outs remaining.
                 Checked first; dominates even when RISP or corners are present.
    2. speed   — corners: 1B+3B occupied, 2B empty, exactly 1 out.
    3. contact — runners in scoring position (2B or 3B occupied).

  No fallback: if the required archetype joker is unavailable, nothing fires.

Each team carries JOKERS_PER_ARCHETYPE (=3) jokers of each archetype (9 total).
JOKER_MAX_PER_HALF (=9) is set high enough to never prematurely block a valid
insertion; the physical joker pool is the binding constraint.
"""
from __future__ import annotations

import sys, os
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)

import pytest
from o27.engine.state import GameState, Team, Player
from o27.engine.manager import _needed_archetype, should_insert_joker
from o27 import config as cfg
from o27v2 import config as v2cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _joker(archetype: str, pid: str | None = None) -> Player:
    return Player(
        player_id=pid or f"joker_{archetype}",
        name=f"J_{archetype}",
        is_joker=True,
        archetype=archetype,
        skill=0.6,
    )


def _regular(pid: str) -> Player:
    return Player(player_id=pid, name=pid, is_joker=False)


def _make_jokers(prefix: str) -> list[Player]:
    """Return JOKERS_PER_ARCHETYPE jokers of each archetype."""
    jokers: list[Player] = []
    for arch in ("power", "speed", "contact"):
        for i in range(v2cfg.JOKERS_PER_ARCHETYPE):
            jokers.append(_joker(arch, f"{prefix}_{arch}_{i}"))
    return jokers


def _state(
    *,
    is_top_half: bool = True,
    visitor_score: int = 0,
    home_score: int = 0,
    outs: int = 0,
    base1: bool = False,
    base2: bool = False,
    base3: bool = False,
    visitor_jokers: list[Player] | None = None,
    home_jokers: list[Player] | None = None,
) -> GameState:
    """Construct a minimal GameState with controllable score and base state."""
    v_jokers = visitor_jokers if visitor_jokers is not None else _make_jokers("v")
    h_jokers = home_jokers if home_jokers is not None else _make_jokers("h")

    v_roster = [_regular(f"v{i}") for i in range(9)] + v_jokers
    h_roster = [_regular(f"h{i}") for i in range(9)] + h_jokers

    visitors = Team(
        team_id="visitors", name="Visitors",
        roster=v_roster, lineup=list(v_roster),
        jokers_available=list(v_jokers),
    )
    home = Team(
        team_id="home", name="Home",
        roster=h_roster, lineup=list(h_roster),
        jokers_available=list(h_jokers),
    )
    state = GameState(visitors=visitors, home=home)
    state.half = "top" if is_top_half else "bottom"
    state.score["visitors"] = visitor_score
    state.score["home"] = home_score
    state.outs = outs

    dummy_p = _regular("dummy")
    state.bases[0] = dummy_p if base1 else None
    state.bases[1] = dummy_p if base2 else None
    state.bases[2] = dummy_p if base3 else None
    return state


# ---------------------------------------------------------------------------
# _needed_archetype: power trigger (down >= JOKER_POWER_DEFICIT = 3)
# ---------------------------------------------------------------------------

class TestPowerTrigger:
    """Power fires only when down >= JOKER_POWER_DEFICIT (=3), bases empty."""

    def test_tied_empty_bases_no_power(self):
        """Tied 0-0, bases empty → deficit 0 < 3 → None."""
        state = _state(is_top_half=True, visitor_score=0, home_score=0)
        assert _needed_archetype(state) is None

    def test_trailing_by_one_no_power(self):
        """Home trailing by 1, bases empty → deficit 1 < 3 → None."""
        state = _state(is_top_half=False, visitor_score=1, home_score=0)
        assert _needed_archetype(state) is None

    def test_trailing_by_two_no_power(self):
        """Home trailing by 2, bases empty → deficit 2 < 3 → None."""
        state = _state(is_top_half=False, visitor_score=2, home_score=0)
        assert _needed_archetype(state) is None

    def test_trailing_by_three_fires_power(self):
        """Home trailing by exactly 3, bases empty → power."""
        state = _state(is_top_half=False, visitor_score=3, home_score=0)
        assert _needed_archetype(state) == "power"

    def test_trailing_by_ten_fires_power(self):
        """Home trailing by 10, bases empty → power."""
        state = _state(is_top_half=False, visitor_score=10, home_score=0)
        assert _needed_archetype(state) == "power"

    def test_ahead_empty_bases_no_power(self):
        """Visitors ahead by 5, bases empty → deficit < 0 → None."""
        state = _state(is_top_half=True, visitor_score=5, home_score=0)
        assert _needed_archetype(state) is None

    def test_power_suppressed_at_outs_ceil(self):
        """Down 3+ but outs >= JOKER_POWER_OUTS_CEIL → None."""
        state = _state(is_top_half=False, visitor_score=3, home_score=0,
                       outs=cfg.JOKER_POWER_OUTS_CEIL)
        assert _needed_archetype(state) is None


# ---------------------------------------------------------------------------
# _needed_archetype: power dominates when down >= 3
# ---------------------------------------------------------------------------

class TestPowerDominatesWhenDown3:
    """Power is checked first; it fires even when RISP or corners are present."""

    def test_power_dominates_risp_when_down_3(self):
        """Down 3, RISP (2B) → power wins over contact."""
        state = _state(is_top_half=False, visitor_score=3, home_score=0,
                       base2=True)
        assert _needed_archetype(state) == "power"

    def test_power_dominates_3b_risp_when_down_3(self):
        """Down 3, runner on 3B → power wins over contact."""
        state = _state(is_top_half=False, visitor_score=3, home_score=0,
                       base3=True)
        assert _needed_archetype(state) == "power"

    def test_power_dominates_corners_when_down_3(self):
        """Down 3, corners (1B+3B, 2B empty, 1 out) → power wins over speed."""
        state = _state(is_top_half=False, visitor_score=3, home_score=0,
                       outs=1, base1=True, base2=False, base3=True)
        assert _needed_archetype(state) == "power"

    def test_power_dominates_risp_when_down_10(self):
        """Down 10, RISP (2B+3B) → power wins."""
        state = _state(is_top_half=False, visitor_score=10, home_score=0,
                       base2=True, base3=True)
        assert _needed_archetype(state) == "power"


# ---------------------------------------------------------------------------
# _needed_archetype: base-state triggers fire when NOT down >= 3
# ---------------------------------------------------------------------------

class TestBaseStateTriggers:
    """Speed and contact fire when deficit < JOKER_POWER_DEFICIT (3)."""

    def test_contact_risp_when_tied(self):
        """Tied (deficit 0 < 3), RISP (2B) → contact."""
        state = _state(is_top_half=True, visitor_score=0, home_score=0,
                       base2=True)
        assert _needed_archetype(state) == "contact"

    def test_contact_risp_when_down_2(self):
        """Down 2 (deficit 2 < 3), RISP (2B) → contact."""
        state = _state(is_top_half=False, visitor_score=2, home_score=0,
                       base2=True)
        assert _needed_archetype(state) == "contact"

    def test_speed_corners_when_tied(self):
        """Tied, corners (1B+3B, 2B empty, 1 out) → speed."""
        state = _state(is_top_half=True, visitor_score=0, home_score=0,
                       outs=1, base1=True, base2=False, base3=True)
        assert _needed_archetype(state) == "speed"

    def test_speed_corners_beats_contact(self):
        """Corners (1B+3B, 2B empty, 1 out), down 2 → speed beats contact."""
        state = _state(is_top_half=False, visitor_score=2, home_score=0,
                       outs=1, base1=True, base2=False, base3=True)
        assert _needed_archetype(state) == "speed"

    def test_no_lead_runner_trigger(self):
        """Runner on 1B only (no RISP), not down 3 → None (no lead-runner trigger)."""
        state = _state(is_top_half=True, visitor_score=0, home_score=0,
                       outs=0, base1=True, base2=False, base3=False)
        assert _needed_archetype(state) is None

    def test_no_trigger_bases_empty_not_down_3(self):
        """Tied, bases empty → no trigger."""
        state = _state(is_top_half=True, visitor_score=0, home_score=0)
        assert _needed_archetype(state) is None

    def test_ahead_with_risp_fires_contact(self):
        """Batting team ahead (deficit < 0), RISP → contact (base-state applies)."""
        state = _state(is_top_half=True, visitor_score=3, home_score=0,
                       base2=True)
        assert _needed_archetype(state) == "contact"

    def test_ahead_empty_bases_no_trigger(self):
        """Batting team ahead, bases empty → None."""
        state = _state(is_top_half=True, visitor_score=3, home_score=0)
        assert _needed_archetype(state) is None


# ---------------------------------------------------------------------------
# should_insert_joker: eligibility + archetype selection
# ---------------------------------------------------------------------------

class TestShouldInsertJoker:
    """End-to-end: correct joker returned or None when unavailable."""

    def test_power_joker_selected_down_3(self):
        """Down 3+, bases empty → power joker returned."""
        state = _state(is_top_half=False, visitor_score=3, home_score=0)
        result = should_insert_joker(state)
        assert result is not None
        assert getattr(result, "archetype", "") == "power"

    def test_contact_joker_selected_risp(self):
        """RISP (2B), tied → contact joker returned."""
        state = _state(is_top_half=True, visitor_score=0, home_score=0,
                       base2=True)
        result = should_insert_joker(state)
        assert result is not None
        assert getattr(result, "archetype", "") == "contact"

    def test_speed_joker_selected_corners(self):
        """Corners (1B+3B, 2B empty, 1 out), tied → speed joker returned."""
        state = _state(is_top_half=True, visitor_score=0, home_score=0,
                       outs=1, base1=True, base2=False, base3=True)
        result = should_insert_joker(state)
        assert result is not None
        assert getattr(result, "archetype", "") == "speed"

    def test_no_insertion_when_all_power_jokers_used(self):
        """All power jokers used (down 3+) → None (no fallback to other archetypes)."""
        state = _state(is_top_half=False, visitor_score=3, home_score=0)
        team = state.home
        power_jokers = [j for j in team.jokers_available
                        if getattr(j, "archetype", "") == "power"]
        for j in power_jokers:
            team.jokers_used_this_half.add(j.player_id)
        team.jokers_available = [j for j in team.jokers_available
                                 if j.player_id not in team.jokers_used_this_half]
        result = should_insert_joker(state)
        assert result is None

    def test_second_power_joker_fires_when_first_used(self):
        """With multiple power jokers, second fires after first is used."""
        state = _state(is_top_half=False, visitor_score=3, home_score=0)
        team = state.home
        power_jokers = [j for j in team.jokers_available
                        if getattr(j, "archetype", "") == "power"]
        assert len(power_jokers) >= 2, "Need >=2 power jokers for this test"
        # Use the first power joker
        team.jokers_used_this_half.add(power_jokers[0].player_id)
        team.jokers_available = [j for j in team.jokers_available
                                 if j.player_id != power_jokers[0].player_id]
        result = should_insert_joker(state)
        assert result is not None
        assert getattr(result, "archetype", "") == "power"

    def test_no_insertion_in_super_inning(self):
        """Super-inning: no joker insertion regardless of situation."""
        state = _state(is_top_half=False, visitor_score=3, home_score=0)
        state.half = "super_top"
        assert should_insert_joker(state) is None

    def test_max_per_half_cap(self):
        """Once JOKER_MAX_PER_HALF jokers have batted, no more insertions."""
        state = _state(is_top_half=False, visitor_score=3, home_score=0)
        team = state.home
        jokers = list(team.jokers_available)
        for j in jokers[:cfg.JOKER_MAX_PER_HALF]:
            team.jokers_used_this_half.add(j.player_id)
        assert len(team.jokers_used_this_half) >= cfg.JOKER_MAX_PER_HALF
        assert should_insert_joker(state) is None
