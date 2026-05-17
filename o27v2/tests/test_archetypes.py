"""
Position-player archetype classifier — properties and fixtures.

Locks down totality (every position player gets exactly one label from
the known set), exemption (pitchers and jokers get ""), the motivating
Vargas-shape fixture, and the generation / off-season-development
wiring.
"""
from __future__ import annotations

import random

import pytest

from o27v2.archetypes import (
    ALL_ARCHETYPES,
    CONTACT_HITTER,
    DEFENSIVE_SPECIALIST,
    FIVE_TOOL_STAR,
    REGULAR,
    SLUGGER,
    SPEEDSTER,
    UTILITY_INFIELDER,
    classify_position_player,
)


# ---------------------------------------------------------------------------
# Fixtures — pure dicts in DB shape (no DB roundtrip needed)
# ---------------------------------------------------------------------------

def _base_player(**overrides) -> dict:
    """Return a league-average position-player row. Tests override the
    grades they care about."""
    p = {
        "is_pitcher": 0,
        "is_joker": 0,
        "position": "2B",
        "bats": "R",
        "contact": 50,
        "power": 50,
        "eye": 50,
        "speed": 50,
        "skill": 50,
        "defense": 50,
        "defense_infield": 50,
        "defense_outfield": 35,
        "defense_catcher": 25,
        "baserunning": 50,
    }
    p.update(overrides)
    return p


# ---------------------------------------------------------------------------
# Exemption
# ---------------------------------------------------------------------------

def test_pitcher_returns_empty():
    assert classify_position_player(_base_player(is_pitcher=1, position="P")) == ""


def test_joker_returns_empty():
    assert classify_position_player(_base_player(is_joker=1)) == ""


# ---------------------------------------------------------------------------
# Catalogue fixtures — pin the priority order with deliberate inputs
# ---------------------------------------------------------------------------

def test_vargas_classifies_as_utility_infielder():
    """Switch-hitting 2B/SS, modest power, balanced IF/OF defense — the
    motivating example."""
    vargas = _base_player(
        position="2B",
        bats="S",
        contact=65,
        power=40,
        speed=55,
        skill=55,
        defense_infield=55,
        defense_outfield=50,
        defense_catcher=30,
        baserunning=50,
    )
    assert classify_position_player(vargas) == UTILITY_INFIELDER


def test_slugger_beats_power_hitting_corner():
    """A 1B with elite power + low contact should land on Slugger (rule 4)
    before falling through to Power-Hitting Corner (rule 5). Priority
    order pinned deliberately."""
    p = _base_player(position="1B", power=80, contact=45, eye=55, speed=40)
    assert classify_position_player(p) == SLUGGER


def test_five_tool_star_wins_over_everything():
    """Elite across the board → Five-Tool Star regardless of other
    matches (rule 1, highest priority)."""
    p = _base_player(
        position="CF",
        skill=75, power=70, contact=72, speed=72,
        defense_infield=40, defense_outfield=70,
    )
    assert classify_position_player(p) == FIVE_TOOL_STAR


def test_contact_hitter_lock():
    p = _base_player(contact=72, power=40, speed=55, position="2B",
                     defense_infield=40, defense_outfield=35)
    # Vargas-style IF/OF balance is avoided here so Utility Infielder
    # doesn't shadow this case.
    assert classify_position_player(p) == CONTACT_HITTER


def test_speedster_lock():
    p = _base_player(speed=78, baserunning=70, power=45, contact=58, position="CF",
                     defense_infield=35, defense_outfield=55)
    assert classify_position_player(p) == SPEEDSTER


def test_defensive_specialist_lock():
    p = _base_player(
        position="SS",
        skill=42, contact=42, power=35, speed=45,
        defense=72, defense_infield=72, defense_outfield=40,
    )
    assert classify_position_player(p) == DEFENSIVE_SPECIALIST


def test_regular_fallback():
    """Average grades → Regular catch-all."""
    p = _base_player(
        position="LF",
        contact=50, power=50, eye=50, speed=50, skill=50,
        defense=50, defense_infield=35, defense_outfield=50, defense_catcher=25,
    )
    assert classify_position_player(p) == REGULAR


# ---------------------------------------------------------------------------
# Totality — every generated player gets a label from the catalogue
# ---------------------------------------------------------------------------

def test_classifier_total_over_sampled_generations():
    """Sample 2000 hitters via the real _make_hitter generator and assert
    every result has a non-empty archetype in the known set, equal to
    what classify_position_player produces on the same dict."""
    from o27v2.league import _make_hitter

    rng = random.Random(42)
    positions = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C"]
    catalogue = set(ALL_ARCHETYPES)

    for i in range(2000):
        pos = positions[i % len(positions)]
        p = _make_hitter(rng, pos, is_active=1, name=f"Test {i}")
        assert p["archetype"], f"empty archetype on generated player at i={i}"
        assert p["archetype"] in catalogue, (
            f"unknown archetype '{p['archetype']}' at i={i}"
        )
        # And it must agree with a re-classify of the same dict.
        assert p["archetype"] == classify_position_player(p)


def test_generation_archetype_distribution_covers_multiple_labels():
    """Across a large sample, at least three distinct archetype labels
    should appear — otherwise the catalogue is collapsing to one bucket."""
    from o27v2.league import _make_hitter

    rng = random.Random(7)
    positions = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C"]
    seen = set()
    for i in range(1000):
        pos = positions[i % len(positions)]
        p = _make_hitter(rng, pos, is_active=1, name=f"Dist {i}")
        seen.add(p["archetype"])
    assert len(seen) >= 3, f"only saw {seen}"


# ---------------------------------------------------------------------------
# Off-season re-derivation — label updates when grades drift
# ---------------------------------------------------------------------------

def test_offseason_redrive_updates_label_when_power_collapses():
    """A Slugger who loses 30 power grade points to aging should stop
    being labeled a Slugger."""
    from o27v2.development import _develop_player

    slugger = _base_player(
        position="1B",
        skill=68, power=78, contact=50, eye=55, speed=40,
        defense=45, defense_infield=35, defense_outfield=40,
        baserunning=40, age=37,
        work_ethic=40, work_habits=45, stamina=50, habit_cup=0.5,
        archetype=SLUGGER,
    )
    assert classify_position_player(slugger) == SLUGGER

    # Run dev with a seeded rng on a 37-year-old (sharp decline) until
    # the power grade drops below the Slugger threshold; the development
    # curve is noisy so loop a few times until it crosses or fail loudly.
    rng = random.Random(0)
    p = dict(slugger)
    for _ in range(8):
        updated, new_age = _develop_player(p, org_strength=50, rng=rng, is_pitcher=False)
        p = {**p, **updated, "age": new_age}
        if p["archetype"] != SLUGGER:
            break
    assert p["archetype"] != SLUGGER, (
        f"archetype stuck on {SLUGGER} after 8 dev passes (power={p.get('power')})"
    )
    assert p["archetype"] in set(ALL_ARCHETYPES)
