"""Regression tests for consecutive-pitch stamina fatigue."""
from __future__ import annotations

from types import SimpleNamespace

from o27.engine.prob import _pitch_probs


def _pitcher(stamina: float) -> SimpleNamespace:
    return SimpleNamespace(
        pitcher_skill=0.5,
        stamina=stamina,
        command=0.5,
        grit=0.5,
        release_angle=0.5,
        pitch_variance=0.0,
        is_pitcher=True,
        repertoire=None,
        throws="",
        today_form=1.0,
        today_condition=1.0,
        movement=0.5,
    )


def _batter() -> SimpleNamespace:
    return SimpleNamespace(
        skill=0.5,
        eye=0.5,
        contact=0.5,
        bats="",
        today_condition=1.0,
    )


def test_consecutive_pitch_fatigue_hurts_pitch_outcome_surface():
    batter = _batter()
    pitcher = _pitcher(stamina=0.5)

    fresh = _pitch_probs(pitcher, batter, 0, 0, spell_count=0, pitch_count=45)
    tired = _pitch_probs(pitcher, batter, 0, 0, spell_count=0, pitch_count=70)

    # Tired pitchers lose command and miss fewer bats, even before BF-count
    # fatigue starts. Balls/contact rise while strike/foul outcomes fall.
    assert tired[0] > fresh[0]  # balls
    assert tired[4] > fresh[4]  # contact
    assert tired[1] < fresh[1]  # called strikes
    assert tired[2] < fresh[2]  # swinging strikes
    assert tired[3] < fresh[3]  # fouls


def test_high_stamina_delays_consecutive_pitch_fatigue():
    batter = _batter()

    low_stamina = _pitch_probs(_pitcher(stamina=0.2), batter, 0, 0, spell_count=0, pitch_count=70)
    high_stamina = _pitch_probs(_pitcher(stamina=0.9), batter, 0, 0, spell_count=0, pitch_count=70)

    assert low_stamina[0] > high_stamina[0]
    assert low_stamina[4] > high_stamina[4]
    assert low_stamina[2] < high_stamina[2]
