"""Wild & exotic ballpark shapes — generation + gameplay-impact tests.

Covers the five exotic archetypes added on top of the original seven:
bandbox, crescent (inverted CF), hourglass (pinched alleys), coffin_corner,
and sawtooth_wedge. Verifies the generator produces the intended deranged
geometry (and that the relaxed dimension floors let it survive), and that the
park-effects hook actually rewards/punishes drives by venue — the whole point
of the feature.
"""
from __future__ import annotations

import random

from o27v2.league import _roll_park_dimensions, _PARK_SHAPES, _QUIRK_CATALOG
from o27.engine.park_effects import apply_park_effects, _fence_at_angle


EXOTIC = ("bandbox", "crescent", "hourglass", "coffin_corner", "sawtooth_wedge")


def _samples_by_shape(n: int = 40_000) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for i in range(n):
        d = _roll_park_dimensions(random.Random(i))
        out.setdefault(d["shape"], []).append(d)
    return out


def test_exotic_shapes_are_generated():
    by_shape = _samples_by_shape()
    for shape in EXOTIC:
        assert by_shape.get(shape), f"exotic shape {shape!r} never generated"


def test_all_registered_shapes_have_meta():
    keys = {s[0] for s in _PARK_SHAPES}
    for shape in EXOTIC:
        assert shape in keys
    # Every shape entry carries a non-empty label + blurb for the UI.
    for key, label, blurb in _PARK_SHAPES:
        assert label and blurb, f"shape {key} missing label/blurb"


def test_dimension_floors_always_honored():
    """Relaxed clamps still enforce physical minimums for every shape."""
    for i in range(40_000):
        d = _roll_park_dimensions(random.Random(i))
        assert d["lf"] >= 250 and d["rf"] >= 250
        assert d["lcf"] >= 300 and d["rcf"] >= 300
        assert d["cf"] >= 355
        assert d["wall_h"] >= 1


def test_bandbox_is_tiny_everywhere():
    bb = _samples_by_shape()["bandbox"]
    # Average CF well under any normal park; lines genuinely short.
    avg_cf = sum(d["cf"] for d in bb) / len(bb)
    avg_line = sum((d["lf"] + d["rf"]) / 2 for d in bb) / len(bb)
    assert avg_cf < 380, avg_cf
    assert avg_line < 300, avg_line
    # The spite fence shows up: a meaningful share carry a freakish wall.
    tall = sum(1 for d in bb if d["wall_h"] >= 40) / len(bb)
    assert tall > 0.4, tall


def test_crescent_center_is_shorter_than_the_alleys():
    """The inversion that makes crescent unique: CF caves in vs the gaps."""
    cr = _samples_by_shape()["crescent"]
    inverted = sum(
        1 for d in cr if d["cf"] < d["lcf"] and d["cf"] < d["rcf"]
    ) / len(cr)
    assert inverted > 0.85, inverted


def test_hourglass_alleys_pinch_in():
    hg = _samples_by_shape()["hourglass"]
    pinched = sum(
        1 for d in hg
        if d["lcf"] < d["lf"] and d["rcf"] < d["rf"] and d["cf"] > d["lcf"]
    ) / len(hg)
    assert pinched > 0.8, pinched


def test_coffin_corner_is_wildly_asymmetric():
    cc = _samples_by_shape()["coffin_corner"]
    # One foul line dives short while the other plays deep.
    asym = sum(1 for d in cc if abs(d["lf"] - d["rf"]) >= 80) / len(cc)
    assert asym > 0.7, asym


def test_sawtooth_wedge_ramps_one_way():
    sw = _samples_by_shape()["sawtooth_wedge"]
    # A monotonic-ish ramp: one corner short, opposite corner a death valley.
    ramps = sum(1 for d in sw if abs(d["lf"] - d["rf"]) >= 90) / len(sw)
    assert ramps > 0.7, ramps


def test_new_quirks_are_shape_gated():
    by_key = {q["key"]: q for q in _QUIRK_CATALOG}
    expected = {
        "spite_fence": "bandbox",
        "the_coffin": "coffin_corner",
        "pinch_alleys": "hourglass",
        "inverted_wall": "crescent",
        "the_ramp": "sawtooth_wedge",
    }
    for key, shape in expected.items():
        assert key in by_key, f"quirk {key} missing"
        assert by_key[key]["shapes"] == (shape,), by_key[key]["shapes"]


# --- Gameplay impact -----------------------------------------------------

def _fly_out_drive(dims, ev, la, spray):
    rng = random.Random(0)
    oc = {"hit_type": "fly_out", "batter_safe": False, "caught_fly": True}
    apply_park_effects(rng, oc, ev, la, spray, dims)
    return oc["hit_type"]


def test_bandbox_pull_fly_clears_where_oval_dies():
    bandbox = {"lf": 270, "lcf": 332, "cf": 355, "rcf": 344,
               "rf": 273, "wall_h": 12, "shape": "bandbox"}
    oval = {"lf": 380, "lcf": 398, "cf": 418, "rcf": 398,
            "rf": 380, "wall_h": 6, "shape": "oval"}
    # Identical modest pull fly down the LF line.
    assert _fly_out_drive(bandbox, 100.0, 28.0, -40.0) == "hr"
    assert _fly_out_drive(oval,    100.0, 28.0, -40.0) == "fly_out"


def test_crescent_inversion_center_clears_alley_dies():
    crescent = {"lf": 350, "lcf": 419, "cf": 385, "rcf": 440,
                "rf": 327, "wall_h": 9, "shape": "crescent"}
    # Same drive: a HR to dead center (short CF) but a long out to the gap.
    assert _fly_out_drive(crescent, 108.0, 29.0, 0.0) == "hr"
    assert _fly_out_drive(crescent, 108.0, 29.0, 22.0) == "fly_out"


def test_park_effects_identity_when_dims_none():
    """Regression guard: the exotic work must not break the no-op contract."""
    rng = random.Random(0)
    oc = {"hit_type": "fly_out", "batter_safe": False, "caught_fly": True}
    apply_park_effects(rng, oc, 108.0, 29.0, 0.0, None)
    assert oc["hit_type"] == "fly_out"


def test_fence_interpolation_tracks_exotic_dims():
    crescent = {"lf": 350, "lcf": 419, "cf": 385, "rcf": 440,
                "rf": 327, "wall_h": 9, "shape": "crescent"}
    # Center fence is shorter than the power-alley fence — the inversion
    # is visible straight through the interpolator.
    assert _fence_at_angle(0.0, crescent) < _fence_at_angle(22.5, crescent)
