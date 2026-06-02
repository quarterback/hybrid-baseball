"""Tests for the physics-first contact engine (generate + resolve).

generate_batted_ball() samples (EV, LA, spray, texture) from TALENT, with no
knowledge of the outcome. resolve_batted_ball() then derives the base hit_type
from that trajectory. Together they replace the WEAK/MEDIUM/HARD_CONTACT table
draw. These tests pin the band logic and the EV→hit monotonicity that is the
whole point of the inversion.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from o27.engine.batted_ball import generate_batted_ball, resolve_batted_ball

NEUTRAL_PARK = {"lf": 380, "lcf": 380, "cf": 380, "rcf": 380, "rf": 380, "wall_h": 10}


class _RNG(random.Random):
    def __init__(self, value):
        super().__init__()
        self._v = value
    def random(self):
        return self._v


# --- generator ----------------------------------------------------------

def test_generate_returns_plausible_physics():
    rng = random.Random(1)
    for _ in range(2000):
        ev, la, spray, tex = generate_batted_ball(rng, "medium", 0.5)
        assert 52.0 <= ev <= 119.0
        assert -45.0 <= la <= 60.0
        assert -44.0 <= spray <= 44.0
        assert tex in ("dribbler", "grounder", "liner", "flyball")


def test_power_raises_exit_velocity():
    rng = random.Random(2)
    weak = [generate_batted_ball(rng, "hard", 0.15)[0] for _ in range(4000)]
    strong = [generate_batted_ball(rng, "hard", 0.85)[0] for _ in range(4000)]
    assert sum(strong) / len(strong) > sum(weak) / len(weak) + 5.0


def test_generation_is_outcome_blind():
    # The generator takes no hit_type — it cannot peek at the result.
    import inspect
    params = inspect.signature(generate_batted_ball).parameters
    assert "hit_type" not in params


# --- resolver bands -----------------------------------------------------

def test_popup_is_out():
    ht, safe, cf = resolve_batted_ball(_RNG(0.0), ev=80.0, la=60.0, spray=0.0,
                                       park_dims=NEUTRAL_PARK)
    assert ht == "fly_out" and safe is False and cf is True


def test_crushed_fly_clears_the_fence():
    ht, safe, cf = resolve_batted_ball(_RNG(0.0), ev=112.0, la=28.0, spray=0.0,
                                       park_dims=NEUTRAL_PARK)
    assert ht == "hr" and safe is True


def test_weak_grounder_can_be_infield_single():
    # Low EV grounder, hit roll passes → infield single (legged out).
    ht, safe, cf = resolve_batted_ball(_RNG(0.0), ev=68.0, la=-3.0, spray=0.0,
                                       park_dims=NEUTRAL_PARK)
    assert ht == "infield_single" and safe is True


def test_grounder_out_when_roll_fails():
    ht, safe, cf = resolve_batted_ball(_RNG(0.99), ev=88.0, la=-3.0, spray=0.0,
                                       park_dims=NEUTRAL_PARK)
    assert ht in ("ground_out", "fielders_choice")


def test_scorched_liner_is_extra_base():
    ht, safe, cf = resolve_batted_ball(_RNG(0.0), ev=105.0, la=16.0, spray=20.0,
                                       park_dims=NEUTRAL_PARK)
    assert ht in ("double", "triple") and safe is True


# --- the headline property: EV drives the outcome -----------------------

def test_ev_monotonically_raises_hit_probability():
    rng = random.Random(7)
    def hit_rate(ev):
        hits = 0
        for _ in range(3000):
            # neutral-ish liner trajectory, vary only EV
            la = rng.gauss(14, 6)
            ht, safe, cf = resolve_batted_ball(rng, ev, la, 0.0, park_dims=NEUTRAL_PARK)
            hits += ht in ("single", "infield_single", "double", "triple", "hr")
        return hits / 3000
    r70, r90, r110 = hit_rate(70.0), hit_rate(90.0), hit_rate(110.0)
    assert r70 < r90 < r110
