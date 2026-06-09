# How O27 generates player birthplaces / nationalities (reference for the tennis port)

There are **two layers**: nationality (an ISO country code) and birthplace city.
They live in separate data files and are wired together at roster-generation time.
The model is **country → city** (two levels). There is NO stored state/province
tier — a player card shows `hometown` (city string) + `country` (rendered as a
flag). If tennis needs city → state → nation, that middle tier must be added.

## Data files (all under `o27v2/data/names/`)

- **`hometowns.json`** — birthplace **city** pools, keyed by ISO 3166-1 alpha-2
  country code. Shape:
  ```json
  { "cities": { "US": ["Los Angeles", "Houston", "Miami", "..."],
                "CA": ["Toronto", "Vancouver", "..."] } }
  ```
  Unknown country code → "".
- **`regions.json`** — world-region groupings + named presets. Maps name buckets
  (`first_keys` / `surname_keys`) AND the **country code** (`country`, or legacy
  `country_weights`) onto a region. A region either has flat keys (legacy) or a
  list of `subregions`; when a draw lands on a region with subregions, ONE
  subregion is chosen by weight and first+surname are drawn from THAT subregion
  so names stay culturally coherent. This file decides a player's **nationality**.
- `team_naming.json` — TEAM/city naming (`city_to_locale`, `cities_example`).
  Separate from player birthplaces; don't confuse the two.
- `nation_talent.json` — per-nation talent steering (not location names).

## Code

- **`o27v2/team_naming.py` → `roll_hometown(country_code, rng)`** (~line 557) —
  the birthplace-city generator:
  ```python
  cities = _load("hometowns.json")["cities"].get((country_code or "").upper())
  # picks one city from that country's pool
  ```
  Siblings: `roll_birthday(rng)`, `roll_secondary_country(country, rng)` (dual nationality).
- **`o27v2/league.py`** — loads `regions.json` (~line 78), resolves a region/preset,
  and draws `(first_name, last_name, country_code)` per player. The country code is
  the hinge that everything else keys off.
- **`o27v2/league.py` → `_stamp_player_flavor(p, rng)`** (~line 1878) — stamps each
  player's cosmetic identity:
  ```python
  ctry = p.get("country", "")
  p["hometown"]          = _tn.roll_hometown(ctry, rng)
  p["birthday"]          = _tn.roll_birthday(rng)
  p["secondary_country"] = _tn.roll_secondary_country(ctry, rng)
  ```

## Generation flow (replicate this)

1. **Region preset (`regions.json`) → country code.** League config picks a
   region/preset; the draw yields the player's ISO alpha-2 `country`.
2. **Country code → birthplace city.** `roll_hometown(country)` looks the code up
   in `hometowns.json` and picks a city → `hometown`.
3. Optional **dual nationality** via `roll_secondary_country` → `secondary_country`.

## Player schema (`o27v2/db.py`, ~line 246)

```
country           TEXT  -- ISO 3166-1 alpha-2; drives flag + hometown roll
hometown          TEXT  -- birthplace city, rolled from hometowns.json
secondary_country TEXT  -- dual nationality
birthday          TEXT  -- cosmetic "Mar 14"
```

## Gotchas for the port

- **Country → city only.** No intermediate state/region is stored on the player.
  Add a tier if tennis needs it.
- **Youth and college tiers have their own region tables.** `o27v2/youth.py` and
  `o27v2/college_league.py` define their own `name_region_id` mappings (still
  keying into the same `regions.json`) plus their own `hometown`/`country`
  columns. If you migrated one of those, the lookup is identical — just driven
  from that module instead of `league.py`.

## TL;DR

Birthplace cities = **`o27v2/data/names/hometowns.json`**. Nationality =
**`o27v2/data/names/regions.json`**. Read by
**`o27v2/team_naming.py::roll_hometown`**, wired in by
**`o27v2/league.py::_stamp_player_flavor`**.
