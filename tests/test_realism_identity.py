"""
Realism layer — identity invariant tests.

The realism upgrade adds power/contact/eye/command/movement ratings,
handedness, daily pitcher form, and ballpark factors on top of the
existing probability surface. Each new contribution is wired so it
collapses to zero (or to a multiplicative 1.0) when its input is at
the neutral value:

  contact == power == eye == command == movement == 0.5
  today_form == 1.0
  bats == throws == ''        (sentinel: handedness unknown)
  park_hr == park_hits == 1.0

Under those conditions the engine MUST produce numerically identical
output to the pre-realism formulas. These tests verify that — both as
a regression guard against future drift and as documentation of the
identity contract.
"""
from __future__ import annotations

import math
import random

from o27.engine import prob
from o27.engine.state import GameState, Player, Team
from o27 import config as cfg


def _neutral_player(player_id: str = "p1", is_pitcher: bool = False) -> Player:
    """Construct a player whose every realism input lands at neutral."""
    return Player(
        player_id=player_id,
        name=player_id,
        is_pitcher=is_pitcher,
        skill=0.5,
        speed=0.5,
        pitcher_skill=0.5,
        # All realism ratings at 0.5 → every (x - 0.5) * 2 term == 0.
        contact=0.5,
        power=0.5,
        eye=0.5,
        command=0.5,
        movement=0.5,
        # '' handedness → platoon factor 1.0.
        bats="",
        throws="",
        # today_form == 1.0 → form_dev == 0.
        today_form=1.0,
    )


def test_platoon_factor_is_neutral_when_handedness_unknown():
    """Either side missing handedness → factor is exactly 1.0."""
    bat = _neutral_player("b")
    pit = _neutral_player("p", is_pitcher=True)
    assert prob._platoon_factor(bat, pit) == 1.0

    bat.bats = "L"   # batter set, pitcher still ''
    assert prob._platoon_factor(bat, pit) == 1.0

    bat.bats = ""
    pit.throws = "R"   # pitcher set, batter still ''
    assert prob._platoon_factor(bat, pit) == 1.0


def test_platoon_factor_applies_only_when_both_known():
    bat = _neutral_player("b")
    pit = _neutral_player("p", is_pitcher=True)
    bat.bats = "R"
    pit.throws = "R"
    assert prob._platoon_factor(bat, pit) == 1.0 - cfg.PLATOON_PENALTY

    bat.bats = "L"   # opposite-handed → neutral
    assert prob._platoon_factor(bat, pit) == 1.0

    bat.bats = "S"   # switch hitter → bonus (0.0 by default)
    assert prob._platoon_factor(bat, pit) == 1.0 + cfg.PLATOON_BONUS_SWITCH


def test_pitch_probs_identity_at_neutral_inputs():
    """At all neutral inputs, _pitch_probs must equal the normalized
    PITCH_BASE entry for the count — i.e. NO contribution from any
    realism term."""
    bat = _neutral_player("b")
    pit = _neutral_player("p", is_pitcher=True)

    for count, base in cfg.PITCH_BASE.items():
        balls, strikes = count
        # Direct realization of the legacy formula at neutral attrs:
        # both p_dom and b_dom are 0, no fatigue (spell_count=0), no
        # form/eye/contact/command shift, so the floor + normalize
        # operation alone determines the output.
        floor_base = [max(0.01, p) for p in base]
        total = sum(floor_base)
        expected = tuple(p / total for p in floor_base)

        actual = prob._pitch_probs(pit, bat, balls, strikes, spell_count=0)
        assert all(math.isclose(a, e, abs_tol=1e-12) for a, e in zip(actual, expected)), (
            f"identity broken at count {count}: actual={actual} expected={expected}"
        )


def test_contact_quality_distribution_is_stable_at_neutral_inputs():
    """At neutral inputs, contact_quality probabilities equal the
    plain CONTACT_*_BASE values (after the matchup shift collapses
    to 0 because both sides are 0.5)."""
    bat = _neutral_player("b")
    pit = _neutral_player("p", is_pitcher=True)

    rng = random.Random(123)
    counts = {"weak": 0, "medium": 0, "hard": 0}
    n = 50_000
    for _ in range(n):
        counts[prob.contact_quality(rng, bat, pit)] += 1

    weak_p   = counts["weak"]   / n
    med_p    = counts["medium"] / n
    hard_p   = counts["hard"]   / n

    # Tolerance: 50k samples → SE ~ sqrt(p(1-p)/n) ~ 0.0022. 4σ ≈ 0.009.
    assert abs(weak_p - cfg.CONTACT_WEAK_BASE)   < 0.01
    assert abs(med_p  - cfg.CONTACT_MEDIUM_BASE) < 0.01
    assert abs(hard_p - cfg.CONTACT_HARD_BASE)   < 0.01


def test_resolve_contact_table_unchanged_when_park_neutral_and_power_neutral():
    """park_hr==1.0, park_hits==1.0, power==0.5 → no row weights are
    rescaled inside resolve_contact for any quality level."""
    bat = _neutral_player("b")
    pit = _neutral_player("p", is_pitcher=True)

    # Build a minimal GameState with state.home carrying neutral park factors.
    state = GameState()
    state.home   = Team(team_id="home",     name="home",     park_hr=1.0, park_hits=1.0)
    state.visitors = Team(team_id="visitors", name="visitors", park_hr=1.0, park_hits=1.0)
    state.home.roster = [bat, pit]
    state.visitors.roster = [bat, pit]

    rng = random.Random(99)

    # Sample 'hard' resolutions and bin by hit_type. Compare the empirical
    # distribution to the raw HARD_CONTACT weight ratios — they should
    # match within sampling tolerance.
    hits = {row[0]: 0 for row in cfg.HARD_CONTACT}
    n = 30_000
    for _ in range(n):
        out = prob.resolve_contact(rng, "hard", bat, state)
        if out["hit_type"] in hits:
            hits[out["hit_type"]] += 1

    total_w = sum(r[3] for r in cfg.HARD_CONTACT)
    for name, _safe, _fly, w in cfg.HARD_CONTACT:
        expected = w / total_w
        actual = hits[name] / n
        assert abs(actual - expected) < 0.012, (
            f"resolve_contact 'hard' row '{name}' shifted at neutral inputs: "
            f"expected {expected:.3f}, got {actual:.3f}"
        )


def test_realism_attrs_actually_move_distribution_when_off_neutral():
    """Sanity check: when an attribute is moved off neutral, the output
    distribution shifts in the expected direction. This guards against
    the realism layer being silently no-op'd by a wiring bug."""
    bat_neutral = _neutral_player("bn")
    bat_eye = _neutral_player("be")
    bat_eye.eye = 0.95   # extreme eye

    pit = _neutral_player("p", is_pitcher=True)

    rng_a = random.Random(7)
    rng_b = random.Random(7)
    n = 30_000

    # Count "ball" outcomes for each batter at a 0-0 count.
    ball_outcomes_neutral = sum(
        1 for _ in range(n)
        if prob.pitch_outcome(rng_a, pit, bat_neutral, 0, 0, spell_count=0) == "ball"
    )
    ball_outcomes_eye = sum(
        1 for _ in range(n)
        if prob.pitch_outcome(rng_b, pit, bat_eye, 0, 0, spell_count=0) == "ball"
    )

    # An elite-eye batter must take more balls than a neutral batter.
    assert ball_outcomes_eye > ball_outcomes_neutral, (
        f"eye=0.95 batter saw {ball_outcomes_eye} balls vs neutral {ball_outcomes_neutral}"
    )
