"""Tests for the descriptive batted-ball taxonomy (classify_batted_ball).

The classifier is purely descriptive — these tests pin the families and the
hit_type reconciliation so the names can never contradict the categorical
result (a caught fly is never a "double", a HR is always named in the HR
family, etc.).
"""
from o27.engine.batted_ball import classify_batted_ball as C


def test_home_runs_named_in_hr_family():
    assert "no-doubter" in C(112, 28, 5, "hr")
    assert C(96, 30, -3, "home_run")          # some HR phrase, no crash
    # A "wall-scraper" is still a HR, never a flyout.
    name = C(91, 33, 38, "hr")
    assert "flyout" not in name and "fly" not in name


def test_ground_ball_family():
    # Weak grounder hit → seeing-eye / dribbler / swinging bunt.
    assert C(70, 2, 5, "infield_single") in ("dribbler", "swinging bunt")
    assert "grounder" in C(72, 3, 5, "single")          # seeing-eye grounder
    assert "scorched" in C(105, 4, 5, "single")          # scorched one-hopper
    # Ground OUT stays grounder family, never a fly.
    assert "fly" not in C(88, 1, 0, "ground_out")


def test_line_drive_family():
    assert "frozen rope" in C(106, 16, 5, "single")
    assert "lineout" in C(99, 18, 5, "line_out")


def test_fly_ball_family():
    assert C(70, 35, 5, "fly_out") == "can of corn"
    assert "warning-track" in C(105, 38, 5, "fly_out")
    # A bloop that drops is a hit, not an out.
    assert "bloop" in C(72, 52, 5, "single")


def test_caught_fly_not_named_as_hit():
    # The engine passes "fly_out" for a caught fly even if the BIP was deep;
    # the name must be an out phrase.
    name = C(101, 40, 10, "fly_out")
    assert "double" not in name and "single" not in name


def test_never_crashes_on_none():
    # Defensive: missing physics must not raise.
    assert isinstance(C(None, None, None, ""), str)
    assert isinstance(C(None, None, None, "single"), str)
