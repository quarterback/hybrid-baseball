"""Real-stadium dataset + integration tests.

Covers the three layers of the real-parks feature:
  * the committed `data/real_parks.json` dataset is well-formed,
  * `o27v2.real_parks` transforms records into engine geometry / factors and
    generates realistic-but-varied parks,
  * `o27.engine.park_effects` interpolates per-angle wall heights (and still
    honors the scalar-only contract for generated parks),
  * `seed_league` actually plants real stadiums on a real-parks league.
"""
from __future__ import annotations

import json
import random

import pytest

from o27v2 import real_parks as rp
from o27v2.league import _PARK_SHAPE_NAMES
from o27.engine.park_effects import _wall_at_angle, apply_park_effects


# --- Dataset integrity ---------------------------------------------------

def test_dataset_counts_and_shape():
    parks = rp.load_real_parks()
    assert len(parks) == 203
    mlb = rp.parks_for_tier("MLB")
    assert len(mlb) == 30
    # Every MLB park has full geometry + the headline park factors.
    for p in mlb:
        for k in ("lf", "lcf", "cf", "rcf", "rf"):
            assert rp._zone(p, "dist", k, -1) > 0, (p["park"], k)
        assert p["park_factors"].get("hr") is not None
        assert p["park_factors"].get("avg") is not None
        assert p.get("lat") is not None and p.get("lon") is not None


def test_tiers_partition_the_dataset():
    parks = rp.load_real_parks()
    assert sum(len(rp.parks_for_tier(t)) for t in ("MLB", "AAA", "AA", "A+", "A", "R")) == len(parks)


def test_athletics_placeholder_backfilled():
    # The A's MLB-tab row is a placeholder; geometry comes from the Sacramento
    # AAA-PCL park. After the build it must be playable.
    ath = rp.find_by_abbrev("OAK")  # alias of the park-data "ATH"
    assert ath is not None
    assert ath["park"] == "Sutter Health Park"
    assert rp._zone(ath, "dist", "cf", -1) > 0


# --- Abbreviation aliasing ----------------------------------------------

@pytest.mark.parametrize("db_abbrev,expected", [
    ("ARI", "Chase Field"),
    ("TBR", "Tropicana Field"),
    ("KCR", "Kauffman Stadium"),
    ("SFG", "Oracle Park"),
    ("CHW", "Rate Field"),
    ("MIL", "American Family Field"),
    ("BOS", "Fenway Park"),
])
def test_abbrev_alias_resolution(db_abbrev, expected):
    rec = rp.find_by_abbrev(db_abbrev)
    assert rec is not None and rec["park"] == expected


# --- Record -> engine geometry ------------------------------------------

def test_park_to_dimensions_fenway_monster():
    dims = rp.park_to_dimensions(rp.find_by_abbrev("BOS"))
    assert set(dims) >= {"lf", "lcf", "cf", "rcf", "rf", "wall_h", "walls", "shape"}
    # The Monster rides the LF line and tapers toward the RF corner.
    assert dims["walls"]["lf"] == 37
    assert dims["walls"]["rf"] <= 6
    assert dims["lf"] < 320 and dims["rf"] < 320     # both short Fenway lines
    assert dims["shape"] in _PARK_SHAPE_NAMES


def test_park_factors_map_to_hr_and_hits():
    hr, hits = rp.park_factors(rp.find_by_abbrev("COL"))   # Coors: hitter heaven
    assert hr > 1.2 and hits > 1.1
    hr2, hits2 = rp.park_factors({"park_factors": {}})      # missing -> neutral
    assert hr2 == 1.0 and hits2 == 1.0


def test_classify_shape_always_valid():
    for p in rp.load_real_parks():
        dims = rp.park_to_dimensions(p)
        assert dims["shape"] in _PARK_SHAPE_NAMES, (p["park"], dims["shape"])


# --- Per-angle wall interpolation ---------------------------------------

def test_wall_at_angle_interpolates_and_falls_back():
    fen = rp.park_to_dimensions(rp.find_by_abbrev("BOS"))
    assert _wall_at_angle(-45, fen) == 37.0          # LF line = Monster
    assert _wall_at_angle(45, fen) == fen["walls"]["rf"]
    mid = _wall_at_angle(-33.75, fen)                # between lf(37) and lcf
    assert fen["walls"]["lcf"] < mid < 37.0
    # No walls map -> scalar wall_h (the pre-existing generated-park contract).
    assert _wall_at_angle(-45, {"wall_h": 12}) == 12.0
    assert _wall_at_angle(0, {}) == 10.0             # defensive default


def test_monster_demotes_a_fringe_home_run():
    """A fly that would clear an 8-ft fence dies off the 37-ft Monster."""
    fen = rp.park_to_dimensions(rp.find_by_abbrev("BOS"))
    flat = dict(fen, walls={k: 8 for k in fen["walls"]})
    demoted = 0
    for s in range(50):
        oc = {"hit_type": "hr", "batter_safe": True, "caught_fly": False,
              "runner_advances": [4, 4, 4]}
        apply_park_effects(random.Random(s), oc, 99.0, 27.0, -44.0, fen)
        if oc["hit_type"] != "hr":
            demoted += 1
    # The same fringe drive over a uniform 8-ft fence is never wall-demoted.
    for s in range(50):
        oc = {"hit_type": "hr", "batter_safe": True, "caught_fly": False,
              "runner_advances": [4, 4, 4]}
        apply_park_effects(random.Random(s), oc, 99.0, 27.0, -44.0, flat)
        assert oc["hit_type"] == "hr"
    assert demoted > 0


# --- Realistic-but-varied generation ------------------------------------

def test_realistic_dims_respect_floors_and_carry_walls():
    rng = random.Random(0)
    for _ in range(2000):
        d = rp.realistic_park_dimensions(rng, "MLB")
        assert d["lf"] >= 250 and d["rf"] >= 250
        assert d["lcf"] >= 300 and d["rcf"] >= 300
        assert d["cf"] >= 355
        assert "walls" in d and all(v >= 2 for v in d["walls"].values())
        assert d["shape"] in _PARK_SHAPE_NAMES


def test_realistic_is_varied_but_realistic():
    rng = random.Random(1)
    cfs = [rp.realistic_park_dimensions(rng, "MLB")["cf"] for _ in range(400)]
    assert len(set(cfs)) > 30                  # genuinely varied
    assert 355 <= min(cfs) and max(cfs) <= 470  # but inside the real envelope


# --- League seeding ------------------------------------------------------

def test_seed_league_plants_real_parks(tmp_path, monkeypatch):
    from o27v2 import db, league
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "real.db"), raising=False)
    monkeypatch.setattr(db, "_DB_PATH_OVERRIDDEN", True, raising=False)
    db.init_db()
    league.seed_league(rng_seed=7, config_id="mlb_real")

    rows = db.fetchall("SELECT abbrev, park_name, park_dimensions FROM teams")
    assert len(rows) == 30
    assert len({r["park_name"] for r in rows}) == 30          # all distinct, real
    real_names = {p["park"] for p in rp.parks_for_tier("MLB")}
    for r in rows:
        assert r["park_name"] in real_names
        assert "walls" in json.loads(r["park_dimensions"])    # per-zone walls persisted
