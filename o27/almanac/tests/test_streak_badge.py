"""Tests for the almanac hot/cold streak badge (o27.almanac.render._streak_badge).

The performance streak (o27v2/streaks.py) is surfaced ONLY in the almanac's
player header — never in box scores or game pages. These tests pin the badge's
state→label mapping and that no-streak / legacy rows produce no badge.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from o27.almanac.render import _streak_badge


def test_no_streak_returns_none():
    assert _streak_badge({"streak_state": 0, "streak_weeks": 0}) is None


def test_legacy_row_without_columns_returns_none():
    # Pre-streak DB rows have no streak_* keys — must not blow up or badge.
    assert _streak_badge({}) is None


def test_garbage_values_return_none_safely():
    assert _streak_badge({"streak_state": "x", "streak_weeks": None}) is None


def test_hot_streak_tiers():
    # week field is 0-based completed weeks; tiers: 0 / 1-2 / 3+.
    assert _streak_badge({"streak_state": 1, "streak_weeks": 0})["label"] == "Heating Up"
    assert _streak_badge({"streak_state": 1, "streak_weeks": 2})["label"] == "Hot"
    assert _streak_badge({"streak_state": 1, "streak_weeks": 5})["label"] == "Scorching"


def test_cold_streak_tiers():
    assert _streak_badge({"streak_state": -1, "streak_weeks": 0})["label"] == "Cooling Off"
    assert _streak_badge({"streak_state": -1, "streak_weeks": 1})["label"] == "Cold"
    assert _streak_badge({"streak_state": -1, "streak_weeks": 4})["label"] == "Ice Cold"


def test_hot_and_cold_use_distinct_classes():
    hot = _streak_badge({"streak_state": 1, "streak_weeks": 0})
    cold = _streak_badge({"streak_state": -1, "streak_weeks": 0})
    assert hot["cls"] == "streak-hot"
    assert cold["cls"] == "streak-cold"
    assert hot["icon"] != cold["icon"]


def test_title_reports_human_week_number():
    # weeks=0 → "week 1" (the streak is in its first week).
    badge = _streak_badge({"streak_state": 1, "streak_weeks": 0})
    assert "week 1" in badge["title"]
    badge3 = _streak_badge({"streak_state": -1, "streak_weeks": 2})
    assert "week 3" in badge3["title"]


def test_badge_only_renders_on_player_pages():
    """Full pipeline: a player stamped with a streak shows the badge on his
    player page and NOWHERE else (box scores, leaders, teams, index)."""
    import tempfile, glob, os
    from o27.almanac import compute, render
    from o27.almanac.tests.fixture import build_fixture

    dataset = build_fixture()
    # Stamp a hot streak on the first position player.
    target = next(p for p in dataset["players"] if not p["is_pitcher"])
    target["streak_state"] = 1
    target["streak_weeks"] = 3

    views = compute.compute_views(dataset)
    out = tempfile.mkdtemp(prefix="alm_streak_test_")
    render.render_site(views, dataset, out, site_title="T", subtitle="t")

    by_dir = {}
    for f in glob.glob(os.path.join(out, "**", "*.html"), recursive=True):
        rel = os.path.relpath(f, out)
        top = rel.split(os.sep)[0] if os.sep in rel else "."
        if "streak-badge" in open(f).read():
            by_dir[top] = by_dir.get(top, 0) + 1

    # The badge appears, and only under players/.
    assert by_dir.get("players", 0) >= 1
    assert set(by_dir) <= {"players"}, f"streak badge leaked into: {set(by_dir) - {'players'}}"
