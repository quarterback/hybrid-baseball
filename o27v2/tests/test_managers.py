"""
Manager archetype catalogue — locks the two substitution-economy types
into the catalogue and the roll-output shape used by league seeding.

The platoon_manager and special_teams archetypes are downstream of the
O27 platoon/substitution subsystem; their *deployment* behavior
(roster construction in the 42-45 band, wholesale phase-transition
swap) lands as the subsystem's Items 1-3 land. Their data shape is
landed now so seeding picks them up and the type axis exists in the
schema before the deployment layers go in.
"""
from __future__ import annotations

import random

import pytest

from o27v2.managers import (
    ARCHETYPES,
    ARCHETYPE_KEYS,
    archetype_label,
    roll_manager,
)


_NEW_TYPES = ("platoon_manager", "special_teams")


def test_substitution_economy_types_present():
    for key in _NEW_TYPES:
        assert key in ARCHETYPES, f"missing archetype: {key}"
        assert key in ARCHETYPE_KEYS
        assert ARCHETYPES[key].label, f"empty label for {key}"


def test_substitution_economy_types_carry_high_platoon_aggression():
    """Both types sit high on the platoon_aggression axis by design.
    This is the field slated to broaden into the universal
    substitution-trigger threshold; the new types' identities both
    push toward heavy substitution use, so their centre values must
    sit on the aggressive side of the scale. Centre values, not
    rolled values -- noise can drift individual managers lower."""
    for key in _NEW_TYPES:
        arch = ARCHETYPES[key]
        assert arch.platoon_aggression >= 0.70, (
            f"{key} centre platoon_aggression={arch.platoon_aggression} "
            f"too low for a substitution-economy type"
        )


def test_substitution_economy_types_roll_eventually():
    """roll_manager picks archetypes uniformly; a wide sample should
    surface both new types. Guards against accidental removal from
    ARCHETYPE_KEYS."""
    rng = random.Random(123)
    seen: set[str] = set()
    for _ in range(800):
        m = roll_manager(rng)
        seen.add(m["manager_archetype"])
        if seen.issuperset(_NEW_TYPES):
            break
    assert seen.issuperset(_NEW_TYPES), (
        f"missing from rolled sample: {set(_NEW_TYPES) - seen}"
    )


def test_roll_manager_shape_for_new_types():
    """Rolled output dict must carry the same mgr_* columns the
    teams-table INSERT in league.py consumes, with all values clamped
    to [0, 1]."""
    expected = {
        "manager_archetype",
        "mgr_quick_hook",
        "mgr_bullpen_aggression",
        "mgr_leverage_aware",
        "mgr_joker_aggression",
        "mgr_pinch_hit_aggression",
        "mgr_platoon_aggression",
        "mgr_run_game",
        "mgr_bench_usage",
        "mgr_shift_aggression",
        "mgr_ibb_aggression",
        "mgr_declare_aggression",
        "mgr_bat_first_pref",
    }
    rng = random.Random(0)
    by_key: dict[str, dict] = {}
    for _ in range(600):
        m = roll_manager(rng)
        by_key.setdefault(m["manager_archetype"], m)
        if all(k in by_key for k in _NEW_TYPES):
            break

    for key in _NEW_TYPES:
        m = by_key[key]
        assert set(m.keys()) == expected, (
            f"{key} roll shape mismatch: "
            f"missing={expected - set(m.keys())} extra={set(m.keys()) - expected}"
        )
        for col, v in m.items():
            if col == "manager_archetype":
                continue
            assert 0.0 <= v <= 1.0, f"{key}.{col}={v} out of [0,1]"


def test_archetype_label_resolves_new_types():
    assert archetype_label("platoon_manager") == "Platoon Manager"
    assert archetype_label("special_teams") == "Special-Teams Skipper"
