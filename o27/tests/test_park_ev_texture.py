"""Tests for the exit-velocity BABIP texture rules (park_effects rules 6-9).

These rules let the (EV, LA) sample re-decide a thin slice of marginal balls
in play AFTER the categorical roll and AFTER park geometry. They are paired
(two out→hit, two hit→out) so league offense stays ~flat. The rng draw is
forced here so each rule's firing is deterministic.

Identity contracts pinned:
  * park_dims None  → whole hook no-ops (legacy-DB safety).
  * non-BIP hit_type (strikeout/walk/HBP) → no-op.
  * mid-EV ball that matches no EV gate → unchanged.
"""
import random

from o27.engine.park_effects import apply_park_effects
from o27 import config as cfg

# A neutral 380-ft uniform park so geometry rules (1-5) never fire and we
# isolate the EV texture rules.
NEUTRAL_PARK = {"lf": 380, "lcf": 380, "cf": 380, "rcf": 380, "rf": 380,
                "wall_h": 10}


class _RNG(random.Random):
    """random() always returns a fixed value, so a `< probability` gate
    fires when value < p and is suppressed when value > p."""
    def __init__(self, value):
        super().__init__()
        self._v = value

    def random(self):
        return self._v


def _od(hit_type, batter_safe):
    return {"hit_type": hit_type, "batter_safe": batter_safe,
            "caught_fly": not batter_safe, "runner_advances": [0, 0, 0]}


# --- Rule 6: scorched grounder → seeing-eye single (out→hit) -------------

def test_rule6_scorched_grounder_becomes_single():
    od = _od("ground_out", False)
    apply_park_effects(_RNG(0.0), od, ev=cfg.EV_SCORCHED + 2, la=4.0,
                       spray=-5.0, park_dims=NEUTRAL_PARK)
    assert od["hit_type"] == "single"
    assert od["batter_safe"] is True and od["caught_fly"] is False


def test_rule6_does_not_fire_when_roll_misses():
    od = _od("ground_out", False)
    apply_park_effects(_RNG(0.99), od, ev=cfg.EV_SCORCHED + 2, la=4.0,
                       spray=-5.0, park_dims=NEUTRAL_PARK)
    assert od["hit_type"] == "ground_out"


# --- Rule 7: scorched liner → lineout (hit→out) --------------------------

def test_rule7_atem_ball_becomes_lineout():
    od = _od("double", True)
    apply_park_effects(_RNG(0.0), od, ev=cfg.EV_SCORCHED + 5, la=20.0,
                       spray=2.0, park_dims=NEUTRAL_PARK)
    assert od["hit_type"] == "line_out"
    assert od["batter_safe"] is False and od["caught_fly"] is True
    assert od["runner_advances"] == [0, 0, 0]


# --- Rule 8: soft fly/liner → bloop single (out→hit) ---------------------

def test_rule8_bloop_single():
    od = _od("fly_out", False)
    apply_park_effects(_RNG(0.0), od, ev=cfg.EV_SOFT - 2, la=22.0,
                       spray=8.0, park_dims=NEUTRAL_PARK)
    assert od["hit_type"] == "single"
    assert od["batter_safe"] is True


# --- Rule 9: soft grounder hit → routine out (hit→out) -------------------

def test_rule9_routine_roller():
    od = _od("single", True)
    apply_park_effects(_RNG(0.0), od, ev=cfg.EV_SOFT - 5, la=2.0,
                       spray=-3.0, park_dims=NEUTRAL_PARK)
    assert od["hit_type"] == "ground_out"
    assert od["batter_safe"] is False


# --- Identity contracts --------------------------------------------------

def test_no_park_dims_is_noop():
    od = _od("ground_out", False)
    apply_park_effects(_RNG(0.0), od, ev=120.0, la=4.0, spray=0.0,
                       park_dims=None)
    assert od["hit_type"] == "ground_out"


def test_non_bip_is_noop():
    od = {"hit_type": "strikeout", "batter_safe": False}
    apply_park_effects(_RNG(0.0), od, ev=120.0, la=4.0, spray=0.0,
                       park_dims=NEUTRAL_PARK)
    assert od["hit_type"] == "strikeout"


def test_mid_ev_ball_unchanged():
    # Medium-struck grounder: matches neither the scorched nor the soft gate.
    od = _od("ground_out", False)
    apply_park_effects(_RNG(0.0), od, ev=92.0, la=4.0, spray=0.0,
                       park_dims=NEUTRAL_PARK)
    assert od["hit_type"] == "ground_out"
