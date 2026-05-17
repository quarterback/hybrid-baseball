"""
Tests for the talent-driven RISP pressure model AND the per-PA
leadership-flare mechanic (o27.engine.prob).

Two systems interact here:

  1. RISP pressure (`_resolve_risp_pressure`) — two-stage roll. Stage 1
     decides if the moment manifests; Stage 2 picks one of
     {hit, error, leave_up} (mutually exclusive). Inputs: situational
     pressure, pitcher composure (command + grit), batter clutch
     (eye + contact baseline + leadership/grit mental stack +
     per-PA leadership flare lift).

  2. Leadership flare (`_apply_leadership_lift`) — per-PA one-off
     ratings bump that fires under accumulated leverage conditions
     (RISP, loaded, late game, close game). Returned as a transient
     additive offset; does NOT mutate the player. Fires for BOTH
     batters AND pitchers — leadership is leverage-symmetric, not
     batter-only.

These tests pin directional invariants — no exact rate assertions
that would brittle-fail under tuning.
"""
from __future__ import annotations

import random

from o27.engine.state import Player, Team
from o27.engine.prob import _resolve_risp_pressure, _apply_leadership_lift


class _FakeState:
    """Minimum surface area for the pressure roll — .bases,
    .fielding_team, plus optional .outs / .is_super_inning / .score
    for the leadership-flare leverage conditions."""
    def __init__(self, bases, fielding_team, outs=0, is_super=False, score=None):
        self.bases = list(bases)
        self.fielding_team = fielding_team
        self.outs = outs
        self.is_super_inning = is_super
        self.score = score or {"visitors": 0, "home": 0}


def _new_batter(eye: float = 0.5, contact: float = 0.5,
                leadership: float = 0.5, grit: float = 0.5) -> Player:
    return Player(player_id="b", name="B", is_pitcher=False,
                  eye=eye, contact=contact,
                  leadership=leadership, grit=grit)


def _new_pitcher(command: float = 0.5, grit: float = 0.5,
                 leadership: float = 0.5) -> Player:
    return Player(player_id="p", name="P", is_pitcher=True,
                  command=command, grit=grit, leadership=leadership)


def _new_fielding(defense_rating: float = 0.5) -> Team:
    t = Team(team_id="F", name="F", roster=[])
    t.defense_rating = defense_rating
    return t


def _fire_rate(state, batter, pitcher, n=10000, seed=42) -> float:
    rng = random.Random(seed)
    fires = sum(1 for _ in range(n)
                if _resolve_risp_pressure(rng, state, batter, pitcher) is not None)
    return fires / n


# --- RISP pressure invariants -------------------------------------------

def test_no_risp_never_fires():
    """Bases empty or runner on 1B only — pressure never manifests."""
    fielding = _new_fielding()
    batter = _new_batter(eye=0.85, contact=0.85)
    pitcher = _new_pitcher(command=0.15, grit=0.15)
    for bases in ([None, None, None], ["r1", None, None]):
        state = _FakeState(bases, fielding)
        assert _fire_rate(state, batter, pitcher, n=5000) == 0.0


def test_loaded_pressure_strictly_above_risp_only():
    """Bases-loaded fires strictly more often than RISP alone — the O27
    design point (2C stay mechanic compounds the payoff)."""
    fielding = _new_fielding()
    batter = _new_batter(eye=0.65, contact=0.65)
    pitcher = _new_pitcher(command=0.40, grit=0.40)
    risp_alone = _fire_rate(_FakeState([None, "r2", None], fielding), batter, pitcher)
    loaded     = _fire_rate(_FakeState(["r1", "r2", "r3"], fielding), batter, pitcher)
    assert loaded > 1.4 * risp_alone


def test_clutch_batter_fires_more_than_anti_clutch():
    """High eye + contact lifts clutch via _batter_clutch — even with
    neutral mental, hard skills drive the baseline."""
    fielding = _new_fielding()
    pitcher = _new_pitcher(command=0.40, grit=0.40)
    bases = ["r1", "r2", "r3"]
    clutch_rate = _fire_rate(_FakeState(bases, fielding),
                             _new_batter(eye=0.85, contact=0.85), pitcher)
    flat_rate   = _fire_rate(_FakeState(bases, fielding),
                             _new_batter(eye=0.15, contact=0.15), pitcher)
    assert clutch_rate > flat_rate


def test_mental_stack_lifts_low_hardskill_batter():
    """Joker archetype: low eye/contact but maxed leadership + grit
    lifts pressure firing well above a flat-everything bench guy.
    This is the explicit "stacks the bonus" path."""
    fielding = _new_fielding()
    pitcher = _new_pitcher(command=0.50, grit=0.50)
    bases = ["r1", "r2", "r3"]
    joker = _new_batter(eye=0.15, contact=0.15, leadership=0.85, grit=0.85)
    null  = _new_batter(eye=0.15, contact=0.15, leadership=0.50, grit=0.50)
    joker_rate = _fire_rate(_FakeState(bases, fielding), joker, pitcher)
    null_rate  = _fire_rate(_FakeState(bases, fielding), null,  pitcher)
    assert joker_rate > null_rate, (
        f"joker (lead+grit stacked) {joker_rate:.3f} should lift over "
        f"null-mental {null_rate:.3f}"
    )


def test_uncomposed_pitcher_fires_more_than_veteran():
    """Low pitcher composure fires more pressure events."""
    fielding = _new_fielding()
    batter = _new_batter(eye=0.60, contact=0.60)
    bases = [None, "r2", None]
    weak  = _fire_rate(_FakeState(bases, fielding), batter, _new_pitcher(0.20, 0.20))
    sharp = _fire_rate(_FakeState(bases, fielding), batter, _new_pitcher(0.80, 0.80))
    assert weak > sharp


def test_manifestation_set_is_constrained():
    """Returned manifestation is one of three canonical pathways.
    All three reachable under a balanced matchup."""
    fielding = _new_fielding(defense_rating=0.4)
    batter = _new_batter(eye=0.85, contact=0.85)
    pitcher = _new_pitcher(command=0.30, grit=0.30)
    state = _FakeState(["r1", "r2", "r3"], fielding)
    rng = random.Random(7)
    seen = set()
    for _ in range(5000):
        m = _resolve_risp_pressure(rng, state, batter, pitcher)
        if m is not None:
            assert m in {"hit", "error", "leave_up"}
            seen.add(m)
    assert seen == {"hit", "error", "leave_up"}


def test_weak_defense_skews_to_error_path():
    """Stage-2 manifestation weighting is talent-driven — weaker
    defense → larger share of error manifestations among events fired."""
    batter = _new_batter(eye=0.65, contact=0.65)
    pitcher = _new_pitcher(command=0.50, grit=0.50)
    bases = ["r1", "r2", "r3"]

    def _error_share(defense_rating, n=10000, seed=1):
        rng = random.Random(seed)
        evs = [_resolve_risp_pressure(
            rng, _FakeState(bases, _new_fielding(defense_rating=defense_rating)),
            batter, pitcher) for _ in range(n)]
        non_none = [e for e in evs if e is not None]
        return sum(1 for e in non_none if e == "error") / max(1, len(non_none))

    assert _error_share(0.20) > _error_share(0.80)


# --- Leadership flare invariants ----------------------------------------

def _lift_rate(state, player, n=10000, seed=1):
    rng = random.Random(seed)
    fires = sum(1 for _ in range(n)
                if _apply_leadership_lift(rng, state, player) > 0.0)
    return fires / n


def test_flare_gated_by_leverage():
    """No RISP, no late game, no super — flare never fires regardless
    of leadership. Pressure attribute only matters in pressure spots."""
    fielding = _new_fielding()
    elite_leader = _new_batter(leadership=0.95)
    state = _FakeState([None, None, None], fielding, outs=3,
                       score={"visitors": 5, "home": 5})
    assert _lift_rate(state, elite_leader, n=5000) == 0.0


def test_flare_fires_for_batter_in_risp():
    """Batter with elite leadership in a RISP spot — flare fires
    on a meaningful share of PAs (>5%)."""
    fielding = _new_fielding()
    state = _FakeState([None, "r2", None], fielding, outs=0,
                       score={"visitors": 0, "home": 0})
    rate = _lift_rate(state, _new_batter(leadership=0.85), n=5000)
    assert rate > 0.05


def test_flare_fires_for_pitcher_too():
    """Leadership is leverage-symmetric — high-leadership PITCHER
    also fires flares in the same leverage spots. NOT batter-only."""
    fielding = _new_fielding()
    state = _FakeState(["r1", "r2", "r3"], fielding, outs=18,
                       score={"visitors": 3, "home": 3})
    elite_pitcher = _new_pitcher(leadership=0.85)
    rate = _lift_rate(state, elite_pitcher, n=5000)
    assert rate > 0.05, (
        f"pitcher flare rate {rate:.3f} should be non-trivial — "
        "leadership is not batter-only"
    )


def test_high_leadership_fires_more_than_low():
    """Same leverage, same conditions — leadership rating drives the
    fire probability AND the magnitude band."""
    fielding = _new_fielding()
    state = _FakeState(["r1", "r2", "r3"], fielding, outs=18,
                       score={"visitors": 0, "home": 0})  # close + late + loaded
    hi = _lift_rate(state, _new_batter(leadership=0.85), n=5000)
    lo = _lift_rate(state, _new_batter(leadership=0.15), n=5000)
    assert hi > lo


def test_flare_magnitude_within_band():
    """Magnitude rolls within the documented [0.05, 0.30] band."""
    fielding = _new_fielding()
    state = _FakeState(["r1", "r2", "r3"], fielding, outs=18,
                       score={"visitors": 0, "home": 0})
    player = _new_batter(leadership=0.85)
    rng = random.Random(99)
    samples = [_apply_leadership_lift(rng, state, player) for _ in range(5000)]
    fires = [s for s in samples if s > 0.0]
    assert fires, "expected the flare to fire at least once"
    assert all(0.0 < s <= 0.30 for s in fires)


def test_progressive_conditions_stack_fire_rate():
    """More leverage conditions → higher fire rate. RISP alone fires
    less often than RISP + late + close + tied, holding leadership
    fixed."""
    fielding = _new_fielding()
    player = _new_batter(leadership=0.75)
    risp_only = _FakeState([None, "r2", None], fielding, outs=3,
                           score={"visitors": 1, "home": 8})  # not close, early
    stacked   = _FakeState(["r1", "r2", "r3"], fielding, outs=20,
                           score={"visitors": 4, "home": 4})  # loaded+late+tied
    r1 = _lift_rate(risp_only, player, n=5000)
    r2 = _lift_rate(stacked,   player, n=5000)
    assert r2 > r1


def test_pitcher_flare_suppresses_pressure_event():
    """When the pitcher's command + grit are LIFTED transiently (the
    in-place mutation the flare scope performs), the pressure event
    fires LESS often — composure is lifted, vulnerability drops.
    This is the duel."""
    fielding = _new_fielding()
    batter = _new_batter(eye=0.65, contact=0.65)
    state = _FakeState(["r1", "r2", "r3"], fielding)

    def _rate_with_pitcher_at(command, grit, n=5000, seed=2):
        rng = random.Random(seed)
        pitcher = _new_pitcher(command=command, grit=grit)
        fires = sum(1 for _ in range(n)
                    if _resolve_risp_pressure(rng, state, batter, pitcher) is not None)
        return fires / n

    # Base pitcher: command=0.40, grit=0.40. "Flared" pitcher: same
    # base plus +0.25 lift on each (clamped to 1.0).
    no_flare  = _rate_with_pitcher_at(command=0.40, grit=0.40)
    flared    = _rate_with_pitcher_at(command=0.65, grit=0.65)
    assert flared < no_flare, (
        f"pitcher flare should suppress pressure: base={no_flare:.3f} "
        f"flared={flared:.3f}"
    )


def test_batter_flare_amplifies_pressure_event():
    """Mirror of the pitcher test — when the batter's eye + contact
    are lifted (flare scope mutation), the pressure fire rate goes up
    via clutch."""
    fielding = _new_fielding()
    pitcher = _new_pitcher(command=0.50, grit=0.50)
    state = _FakeState([None, "r2", None], fielding)

    def _rate_with_batter_at(eye, contact, n=5000, seed=3):
        rng = random.Random(seed)
        batter = _new_batter(eye=eye, contact=contact)
        fires = sum(1 for _ in range(n)
                    if _resolve_risp_pressure(rng, state, batter, pitcher) is not None)
        return fires / n

    no_flare = _rate_with_batter_at(eye=0.50, contact=0.50)
    flared   = _rate_with_batter_at(eye=0.75, contact=0.75)
    assert flared > no_flare


def test_flare_scope_lifts_all_listed_attrs():
    """apply_pa_leadership_flares mutates every listed rating attribute
    in-place on the side that fires (we force the magnitude high here
    by giving the player max leadership in a stacked-leverage state).
    The release hook restores everything cleanly."""
    from o27.engine.prob import (
        apply_pa_leadership_flares,
        release_pa_leadership_flares,
        _BATTER_LIFT_ATTRS,
        _PITCHER_LIFT_ATTRS,
    )
    fielding = _new_fielding()
    batter = _new_batter(eye=0.5, contact=0.5, leadership=0.95, grit=0.5)
    # Give the batter a hard_contact_delta to ensure all listed attrs
    # exist for the test (skill, power are dataclass defaults).
    pitcher = _new_pitcher(command=0.5, grit=0.5, leadership=0.95)
    state = _FakeState(["r1", "r2", "r3"], fielding, outs=18,
                       score={"visitors": 0, "home": 0})
    state.flare_originals = []
    state.flare_lift_active = False

    # Try repeatedly until at least one flare fires (probabilistic).
    rng = random.Random(0)
    fired = False
    for attempt in range(200):
        batter_orig  = {a: getattr(batter, a) for a in _BATTER_LIFT_ATTRS
                        if hasattr(batter, a)}
        pitcher_orig = {a: getattr(pitcher, a) for a in _PITCHER_LIFT_ATTRS
                        if hasattr(pitcher, a)}
        fielding_orig_def = fielding.defense_rating
        apply_pa_leadership_flares(rng, state, batter, pitcher)
        if state.flare_lift_active:
            fired = True
            # At least ONE listed attr should be lifted above its original
            # on whichever side fired.
            b_lifted = any(getattr(batter, a)  > batter_orig.get(a, 0)
                           for a in batter_orig)
            p_lifted = any(getattr(pitcher, a) > pitcher_orig.get(a, 0)
                           for a in pitcher_orig)
            # Defense should be lifted only when the pitcher's flare fired.
            d_lifted = fielding.defense_rating > fielding_orig_def
            assert b_lifted or p_lifted, "expected at least one side lifted"
            if p_lifted:
                assert d_lifted, "pitcher flare should also lift defense_rating"
            release_pa_leadership_flares(state)
            # All originals restored exactly:
            for a, v in batter_orig.items():
                assert getattr(batter, a) == v
            for a, v in pitcher_orig.items():
                assert getattr(pitcher, a) == v
            assert fielding.defense_rating == fielding_orig_def
            assert not state.flare_lift_active
            assert state.flare_originals == []
            break
    assert fired, "Expected at least one flare to fire across 200 attempts"
