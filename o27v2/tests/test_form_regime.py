"""Per-league team-form regime: the hot/cold band (LOCKED_FORM_*) is overridable
per league so different leagues can run different variance regimes.

Covers the engine override (state.form_* wins over the global cfg, sigma<=0
disables form) and the o27v2 regime resolver round-trip.
"""
import random

from o27.engine.prob import _locked_in_form
from o27.engine.state import GameState, Team, Player
from o27 import config as cfg


def _state(sigma=None, fmin=None, fmax=None, power=0.9):
    t = Team(team_id="h", name="H",
             lineup=[Player(player_id=f"p{i}", name=f"P{i}") for i in range(9)])
    for p in t.lineup:
        p.power = power
        p.skill = power
    s = GameState(visitors=t, home=t)
    s.half = "top"
    s.form_sigma, s.form_min, s.form_max = sigma, fmin, fmax
    return s


def test_form_off_returns_identity():
    # sigma <= 0 disables the mechanism for this league: every half at 1.0.
    s = _state(sigma=0.0)
    assert _locked_in_form(random.Random(1), s) == 1.0


def test_per_league_band_widens_ceiling():
    # A high-drama band (max 3.40) lets hot teams climb past the old 2.15 cap.
    over = 0
    for seed in range(400):
        s = _state(sigma=0.95, fmin=0.82, fmax=3.40)
        if _locked_in_form(random.Random(seed), s) > 2.15:
            over += 1
    assert over > 0, "wide band should let some halves exceed the old 2.15 ceiling"


def test_per_league_floor_clamps():
    # A high floor keeps every half at or above it (no cold games).
    s = _state(sigma=0.66, fmin=0.95, fmax=2.15, power=0.1)  # weak team → cold draws
    vals = [_locked_in_form(random.Random(seed), _state(0.66, 0.95, 2.15, 0.1))
            for seed in range(200)]
    assert min(vals) >= 0.95 - 1e-9


def test_none_override_falls_back_to_global_cfg():
    # With no override, the draw clamps to the GLOBAL band, not beyond it.
    s = _state()  # all None
    f = _locked_in_form(random.Random(2), s)
    assert cfg.LOCKED_FORM_MIN - 1e-9 <= f <= cfg.LOCKED_FORM_MAX + 1e-9


def test_regime_resolver_round_trips():
    from o27v2.league import resolve_form_regime, form_regime_key, FORM_REGIMES
    for key in FORM_REGIMES:
        v = resolve_form_regime(key)
        assert form_regime_key(v["form_sigma"], v["form_min"], v["form_max"]) == key


def test_unknown_regime_is_default():
    from o27v2.league import resolve_form_regime
    v = resolve_form_regime("nonsense")
    assert v == {"form_sigma": None, "form_min": None, "form_max": None}
