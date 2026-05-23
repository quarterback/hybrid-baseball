"""
Name-system wiring guards.

regions.json references name buckets by key (first_keys / surname_keys). The
picker (o27v2/league.py) resolves a missing key to an EMPTY candidate list and
silently falls back to "Player <random>" — so a typo'd bucket key never raises,
it just quietly degrades the roster. These tests turn that silent failure into
a loud one:

  * every region/subregion bucket reference must resolve to a real bucket;
  * every preset must reference real regions;
  * sampling every preset and every per-country pin must yield real names,
    never the "Player N" fallback.
"""
from __future__ import annotations

import json
import os
import random
import re

from o27v2.league import (
    get_name_regions,
    get_name_region_presets,
    make_name_picker,
    make_country_pinned_picker,
)

_NAMES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "o27v2", "data", "names",
)
_PLAYER_FALLBACK = re.compile(r"^Player \d+$")


def _load(fname: str) -> dict[str, list[str]]:
    with open(os.path.join(_NAMES_DIR, fname), encoding="utf-8") as fh:
        return json.load(fh)


def _iter_keysets(region: dict):
    """Yield (first_keys, surname_keys) for a flat region and each subregion."""
    subs = region.get("subregions")
    if isinstance(subs, list) and subs:
        for sr in subs:
            yield sr.get("first_keys", []), sr.get("surname_keys", [])
    else:
        yield region.get("first_keys", []), region.get("surname_keys", [])


def test_first_keys_resolve_to_male_first_buckets():
    male = _load("male_first.json")
    missing = []
    for rid, region in get_name_regions().items():
        for first_keys, _ in _iter_keysets(region):
            for k in first_keys:
                if k not in male:
                    missing.append(f"{rid}: first_key {k!r}")
    assert not missing, "first_keys with no male_first bucket: " + "; ".join(missing)


def test_surname_keys_resolve_to_surname_buckets():
    surnames = _load("surnames.json")
    missing = []
    for rid, region in get_name_regions().items():
        for _, surname_keys in _iter_keysets(region):
            for k in surname_keys:
                if k not in surnames:
                    missing.append(f"{rid}: surname_key {k!r}")
    assert not missing, "surname_keys with no surnames bucket: " + "; ".join(missing)


def test_female_first_buckets_exist_for_every_first_key():
    """The picker draws female first names from the same key; a key present in
    male_first but missing in female_first would silently shrink a mixed/female
    league. Guard against drift between the two files."""
    female = _load("female_first.json")
    missing = []
    for rid, region in get_name_regions().items():
        for first_keys, _ in _iter_keysets(region):
            for k in first_keys:
                if k not in female:
                    missing.append(f"{rid}: {k!r}")
    assert not missing, "first_keys with no female_first bucket: " + "; ".join(missing)


def test_presets_reference_real_regions():
    regions = get_name_regions()
    bad = []
    for pid, preset in get_name_region_presets().items():
        for rid in (preset.get("weights") or {}):
            if rid not in regions:
                bad.append(f"{pid} -> {rid!r}")
    assert not bad, "presets referencing unknown regions: " + "; ".join(bad)


def test_every_preset_generates_real_names():
    presets = get_name_region_presets()
    for pid, preset in presets.items():
        rng = random.Random(1234)
        pick = make_name_picker(rng, gender="mixed", region_weights=preset["weights"])
        names = [pick()[0] for _ in range(60)]
        fallbacks = [n for n in names if _PLAYER_FALLBACK.match(n)]
        assert not fallbacks, f"preset {pid!r} produced fallback names: {fallbacks[:5]}"


def test_every_region_generates_real_names():
    for rid in get_name_regions():
        rng = random.Random(99)
        pick = make_name_picker(rng, gender="male", region_weights={rid: 1.0})
        names = [pick()[0] for _ in range(40)]
        fallbacks = [n for n in names if _PLAYER_FALLBACK.match(n)]
        assert not fallbacks, f"region {rid!r} produced fallback names: {fallbacks[:5]}"


def test_country_pins_resolve_for_every_subregion_country():
    """Every country code that appears in a subregion must produce real names
    via the youth-league country-pinned picker."""
    for rid, region in get_name_regions().items():
        for sr in region.get("subregions") or []:
            cc = sr.get("country")
            if not cc:
                continue
            rng = random.Random(7)
            pick = make_country_pinned_picker(rng, rid, cc, gender="male")
            name, country = pick()
            assert not _PLAYER_FALLBACK.match(name), \
                f"{rid}/{cc} produced fallback: {name!r}"
            assert country == cc
