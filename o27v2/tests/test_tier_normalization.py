"""Cross-tier / cross-config normalization of OPS+ / wOBA+ / GSc+.

The "+" stats must mean the same thing regardless of the run environment
or league size: a 120 in an 8-team 35-R/G season should be the same
"20% above this player's actual competitive context" as a 120 in a 56-
team 22-R/G tiered season. These tests assert the normalization holds.

The aggregator functions live in `o27v2.web.app` and accept an optional
per-tier baseline map plus a team→league lookup. When provided, each
row's "+" stats are computed against the row's tier-specific baseline
instead of a global one — making them cross-config comparable.
"""

import sys, os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from o27v2.web.app import _aggregate_batter_rows, _aggregate_pitcher_rows
import o27v2.web.app as _app_mod


@pytest.fixture(autouse=True)
def _stub_linear_weights(monkeypatch):
    """The aggregator pulls wOBA weights via `_linear_weights()`, which
    queries the live DB. In CI / a fresh test environment the DB has no
    played games, so weights collapse to zero and every row produces
    wOBA=0 (which masks all the relativization math we're trying to
    verify). Stub with reasonable O27-like weights so the tests have
    meaningful values to compare."""
    stub = {
        "n_events": 1, "league_re_start": 5.0, "league_obp": 0.4, "league_woba": 0.4,
        "rv": {"out": -0.3, "1B": 0.9, "2B": 0.8, "3B": 0.6, "HR": 1.0,
               "STAY": -0.3, "BB": 1.1, "HBP": 1.1, "K_over_out": 0.05},
        "woba_weights": {"BB": 1.0, "HBP": 1.0, "1B": 1.2, "2B": 1.2,
                         "3B": 1.2, "HR": 1.4, "STAY": 0.07},
        "gsc_coeffs": {"out": 1.0, "K_over_out": 0.1, "FO_over_out": 1.0,
                       "H": 1.2, "HR_over_H": 0.8, "BB": 2.0, "ER": 2.0,
                       "UER": 1.0, "base": 55.0},
    }
    monkeypatch.setattr(_app_mod, "_linear_weights", lambda: stub)


def _mk_batter_row(team_id=1, pa=100, ab=80, h=30, d2=5, d3=1, hr=5,
                   bb=10, hbp=2, k=20, runs=15, rbi=12):
    """A minimal batter row matching what the SQL aggregator pulls."""
    return {
        "team_id": team_id,
        "pa": pa, "ab": ab,
        "h": h, "hits": h,
        "doubles": d2, "triples": d3, "hr": hr,
        "bb": bb, "hbp": hbp,
        "k": k, "runs": runs, "rbi": rbi,
        "stays": 0, "stay_hits": 0, "stay_rbi": 0,
        "fo": 0, "mhab": 0, "sb": 0, "cs": 0,
        "rad_1b": 0, "rad_2b": 0, "rad_3b": 0,
        "adv_op_1b": 0, "adv_adv_1b": 0,
        "adv_op_2b": 0, "adv_adv_2b": 0,
        "adv_op_3b": 0, "adv_adv_3b": 0,
        "c2_op_1b": 0, "c2_adv_1b": 0,
        "c2_op_2b": 0, "c2_adv_2b": 0,
        "c2_op_3b": 0, "c2_adv_3b": 0,
        "po": 0, "a": 0, "e": 0, "gidp": 0,
    }


# Empty dicts passed for `baselines_by_league` / `team_league_map` bypass
# the auto-build's DB roundtrip — required for hermetic tests so the test
# DB's actual contents don't bleed into the assertion math.
_EMPTY_DISPATCH = {"baselines_by_league": {}, "team_league_map": {}}


def test_woba_plus_is_globally_consistent_when_no_tier_map():
    """Without a tier dispatch, a row's wOBA+ comes from the single
    baseline argument. Sanity: a player at the league baseline gets 100."""
    row = _mk_batter_row()
    # Pick a baseline whose wOBA matches what _aggregate_batter_rows will
    # produce for this row's stats — i.e. the player IS the league.
    _aggregate_batter_rows([row], **_EMPTY_DISPATCH)
    league_woba = row["woba"]
    baseline = {"obp": row["obp"], "slg": row["slg"], "ops": row["ops"],
                "woba": league_woba, "league": ""}
    # Now re-aggregate with that as the explicit baseline.
    row2 = _mk_batter_row()
    _aggregate_batter_rows([row2], baselines=baseline, **_EMPTY_DISPATCH)
    assert abs(row2["woba_plus"] - 100.0) < 0.01, (
        f"player AT league baseline should yield wOBA+ = 100, got {row2['woba_plus']}"
    )


def test_woba_plus_scales_with_baseline():
    """A player 20% above their league's wOBA should get wOBA+ = 120,
    regardless of the absolute league wOBA value. This is the
    cross-environment comparability property: a high-offense league and a
    low-offense league must produce the same wOBA+ for equivalently
    above-average players."""
    # Compute the row's wOBA once.
    row = _mk_batter_row()
    _aggregate_batter_rows([row], **_EMPTY_DISPATCH)
    player_woba = row["woba"]

    # Construct two synthetic leagues, low-RPG and high-RPG, both with the
    # player at exactly 20% above league wOBA.
    low_lg  = {"obp": 0.3, "slg": 0.4, "ops": 0.7,  "woba": player_woba / 1.20, "league": "low"}
    high_lg = {"obp": 0.5, "slg": 0.6, "ops": 1.1,  "woba": player_woba / 1.20, "league": "high"}

    row_low  = _mk_batter_row()
    row_high = _mk_batter_row()
    _aggregate_batter_rows([row_low],  baselines=low_lg,  **_EMPTY_DISPATCH)
    _aggregate_batter_rows([row_high], baselines=high_lg, **_EMPTY_DISPATCH)

    # Both should land at wOBA+ ≈ 120.
    assert abs(row_low["woba_plus"]  - 120.0) < 0.5
    assert abs(row_high["woba_plus"] - 120.0) < 0.5
    # And both should be approximately equal to each other.
    assert abs(row_low["woba_plus"] - row_high["woba_plus"]) < 0.5


def test_per_tier_dispatch_routes_to_correct_baseline():
    """When `baselines_by_league` + `team_league_map` are provided, each
    row's "+" stats use the row's tier's baseline — even if the rows are
    aggregated together. Cross-tier players in a tiered config compute
    their "+" against their own tier."""
    # Two players with identical stats.
    row_a = _mk_batter_row(team_id=10)
    row_b = _mk_batter_row(team_id=20)

    # Run a one-row pass first to know what wOBA this player produces.
    probe = _mk_batter_row()
    _aggregate_batter_rows([probe], **_EMPTY_DISPATCH)
    player_woba = probe["woba"]

    # Build two different tier baselines so the same player gets
    # different "+" values per tier.
    tier_a_baseline = {"obp": 0.30, "slg": 0.40, "ops": 0.70,
                       "woba": player_woba / 1.20, "league": "tier_a"}  # → 120
    tier_b_baseline = {"obp": 0.50, "slg": 0.60, "ops": 1.10,
                       "woba": player_woba / 0.80, "league": "tier_b"}  # → 80

    baselines_by_league = {
        "tier_a": tier_a_baseline,
        "tier_b": tier_b_baseline,
        "":       tier_a_baseline,   # fallback
    }
    team_league_map = {10: "tier_a", 20: "tier_b"}

    _aggregate_batter_rows(
        [row_a, row_b],
        baselines=tier_a_baseline,   # global fallback
        baselines_by_league=baselines_by_league,
        team_league_map=team_league_map,
    )

    assert abs(row_a["woba_plus"] - 120.0) < 0.5
    assert abs(row_b["woba_plus"] -  80.0) < 0.5
    assert row_a["woba_plus_scope"] == "tier_a"
    assert row_b["woba_plus_scope"] == "tier_b"


def test_scope_is_stamped_for_provenance():
    """Templates need to know which baseline the row's '+' stats are
    scoped to. The aggregator stamps `ops_plus_scope` and
    `woba_plus_scope` on every row."""
    row = _mk_batter_row()
    _aggregate_batter_rows(
        [row],
        baselines={"woba": 0.4, "ops": 0.9, "league": "Galactic"},
        **_EMPTY_DISPATCH,
    )
    assert row["woba_plus_scope"] == "Galactic"
    assert row["ops_plus_scope"] == "Galactic"
