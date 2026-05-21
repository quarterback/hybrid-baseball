# After-Action Report — Custom League Editing: Coordinate Weather, City Catalogue, Live Ballpark Editor, League Renaming

**Date completed:** 2026-05-21
**Branch:** `claude/custom-league-editing-Vp67c`
**Commit:**
- `15eda4e` — Add custom league/team editing: coords-based weather, city catalogue, live ballpark editor, league renaming

---

## What was asked for

The user wanted to push league/team customization further so they can
build "weirder and more expansive" leagues. In their words, across two
messages:

1. "I want to have a better way to edit league names and team names if I
   want to … create more custom leagues that I can set the geography of
   teams and stuff."
2. "Add more Finnish cities too and far more metro and micropolitan
   markets so I can get weirder and more expansive."
3. "Weather geography just would pick the closest city to them, it
   doesnt need to be exact."
4. "Also this would let me make ballpark dimensions on the fly so I can
   get weird then too in real time if i want to."

A scoping question confirmed all four capabilities were wanted, and that
edits should be allowed **at any time** (played games keep their stamped
weather; unplayed home games re-roll).

---

## Starting state

- Teams came from `o27v2/data/teams_database.json` (86 franchises, each
  with `name`, `city`, `lat`, `lon`, `level`). Coordinates were used
  **once** at seed time for west→east division placement, then discarded
  — the `teams` table stored no lat/lon.
- Weather (`o27/engine/weather.py`) mapped a city → climate archetype by
  **exact name match**, with a trailing country-code fallback
  (`Helsinki FIN` → subarctic). Unknown cities silently fell back to
  `continental_warm`. ~150 world cities, 20 of them Finnish.
- The team editor (`/team/<id>/edit`) edited name / abbrev / city as free
  text. Changing the city re-rolled weather for unplayed home games.
- Ballpark dimensions / shape / quirks were rolled once at seed
  (`_roll_park_dimensions`, `_roll_park_quirks`) and were not editable.
- League and division names were fixed at seed and not renameable.

---

## What shipped

### 1. Coordinate-based "nearest city" weather

The core change. Weather no longer needs an exact city-name match.

- Added `lat` / `lon` columns to the `teams` table (`o27v2/db.py` —
  schema + idempotent `ALTER TABLE` migration). NULL on legacy rows.
- `seed_league` now persists each team's coordinates from the catalogue.
- `o27/engine/weather.py` gained a coordinate gazetteer and a
  nearest-anchor lookup:
  - `_BASE_COORDS` — coordinates for existing archetype cities.
  - `_EXTRA_CITIES` — the expansion pack `(lat, lon, archetype)`, folded
    into both the name→archetype table and the name→coords map.
  - `_CLIMATE_ANCHORS` — every city we know both a location and an
    archetype for.
  - `nearest_city(lat, lon)` / `archetype_for_coords(lat, lon)` — pick
    the closest anchor using a cheap squared-equirectangular distance
    (monotone in true distance, so fine for nearest-neighbour ranking;
    no haversine needed since "doesn't need to be exact").
  - `city_gazetteer()` — sorted `{name, lat, lon, archetype}` list for
    the UI picker.
- `draw_weather(rng, city, game_date, lat=None, lon=None)` — when coords
  are supplied it resolves the archetype from the nearest anchor;
  otherwise it falls back to the legacy name lookup. **Backward
  compatible** — every existing call site without coords behaves exactly
  as before, and the RNG-consumption pattern is unchanged (still five
  `_choose` draws), so seeded schedules don't drift.
- Coordinates now flow into the weather draw at every stamping point:
  `o27v2/schedule.py` (season schedule) and `o27v2/playoffs.py`
  (per-playoff-game re-roll) both pass the home team's lat/lon.

Any custom location now resolves to sensible weather — a Finnish
hamlet, a US micropolitan market, or a free-typed foreign city with
manual coordinates (e.g. "Nuuk GRL" → nearest anchor Reykjavik →
subarctic).

### 2. Far bigger city catalogue

The gazetteer grew from ~150 to **362 cities**, all with coordinates +
archetype:

- **~40 Finnish towns** added on top of the original 20 — Seinäjoki,
  Kokkola, Kajaani, Kemi, Tornio, Iisalmi, Savonlinna, Raahe, Imatra,
  Hyvinkää, Järvenpää, Lohja, Rauma, Kuusamo, Sodankylä, Inari, Hanko,
  Mariehamn, Nokia, Ylöjärvi, Kerava, Riihimäki, Valkeakoski, Heinola,
  Varkaus, Pieksämäki, Ylivieska, Kuhmo, Sotkamo, Pietarsaari,
  Uusikaupunki, Naantali, Kaarina, Forssa, Kangasala, Tuusula,
  Nurmijärvi, Kirkkonummi, Kemijärvi — all `subarctic`.
- **~120 US metro / micropolitan markets** across every climate region,
  each tagged with the right archetype: Northeast / Great Lakes cold
  (Albany, Syracuse, Grand Rapids, Fargo, Duluth…), Southern warm
  (Memphis, Knoxville, Tulsa, Baton Rouge…), Texas (Austin, Lubbock →
  arid_steppe, Laredo → desert, Brownsville → coastal_warm), Florida
  (Orlando, Fort Myers → tropical, Key West → tropical), Southwest desert
  (Phoenix, Tucson, Yuma, Palm Springs), Mountain / Intermountain (Boise,
  Spokane, Reno, Flagstaff, Bozeman, Cheyenne), Pacific NW (Eugene,
  Bellingham), and California (Fresno, Bakersfield → arid_steppe, San
  Jose, Santa Barbara, Monterey…).
- A global anchor spread (existing world cities given coordinates) so
  foreign locations resolve to a sensible neighbour.

### 3. Live ballpark editor

`/team/<id>/edit` (`o27v2/web/templates/team_edit.html` +
`team_edit_post`) now edits the whole park in real time:

- Park name, **shape** (all 12 archetypes from `_PARK_SHAPES`), the six
  dimensions (LF / LCF / CF / RCF / RF / wall height), and HR / hits park
  factors.
- **Quirks** — checkbox list of the full 25-entry `_QUIRK_CATALOG`, with
  shape-fit hints shown but not enforced (so you can get weird on
  purpose).
- A **🎲 Re-roll ballpark** button (`action=reroll_park`) that rolls a
  fresh random shape + dimensions + quirks and saves immediately.
- New public helpers in `o27v2/league.py`: `get_park_shapes()`,
  `get_quirk_catalog()`, `get_park_shape_meta()`, `quirk_meta()`,
  `roll_park()`.

### 4. Relocation with a coordinate-aware city picker

The City field now has a datalist of all 362 gazetteer cities. Picking
one auto-fills latitude / longitude and previews the climate archetype
(client-side JS). Manual lat/lon override fields are also exposed.
Server-side resolution order: explicit lat/lon → gazetteer match on the
typed city → existing team coords.

### 5. Rename leagues & divisions

New `/league/edit` page (`league_edit.html`, `league_edit_get` /
`league_edit_post`), linked via a "✎ Rename" button on the League
dashboard. Lists every distinct league and division currently on the
teams table and renames them in place via paired hidden-old / text-new
fields. Renaming is **display-only** — it updates the `league` /
`division` columns on `teams`; records, rosters, and the schedule are
untouched. Leagues and divisions are renamed independently (so after
`AL` → `Atlantic` you may also want `AL West` → `Atlantic West`).

---

## Files touched

| File | Change |
|------|--------|
| `o27/engine/weather.py` | Coordinate gazetteer, `nearest_city` / `archetype_for_coords` / `city_gazetteer`, `draw_weather` coords param, +210 cities |
| `o27v2/db.py` | `lat` / `lon` columns on `teams` (schema + migration) |
| `o27v2/league.py` | Persist coords at seed; public park helpers (`get_park_shapes`, `get_quirk_catalog`, `quirk_meta`, `roll_park`, …) |
| `o27v2/schedule.py` | Pass home-team coords into the weather draw |
| `o27v2/playoffs.py` | Pass home-team coords into the per-game weather draw |
| `o27v2/web/app.py` | Rewritten team-edit GET/POST (coords, ballpark, re-roll); `/league/edit` GET/POST; `_team_climate`, `_parse_park_dims`, `_reroll_weather_for_team` helpers |
| `o27v2/web/templates/team_edit.html` | City picker, lat/lon, full ballpark editor, re-roll, archetype preview |
| `o27v2/web/templates/league_edit.html` | New — rename leagues/divisions |
| `o27v2/web/templates/league.html` | "✎ Rename" link |

---

## Testing

- End-to-end pipeline on a fresh temp DB: seed → schedule → relocate to
  Helsinki → reshape park → re-roll → rename leagues/divisions. Teams
  carry coords, games stamp weather, all writes verified.
- Web layer via Flask's test client: `/team/<id>/edit` and `/league/edit`
  GET (200), save / re-roll / rename POST (302) with DB assertions.
- Edge cases: legacy row with NULL coords + empty park renders and saves;
  free-typed non-gazetteer city ("Nuuk GRL") with manual coordinates
  persists and resolves to the nearest anchor.
- `nearest_city` spot-checks: Seinäjoki → subarctic, Boise →
  arid_steppe, Miami → tropical, Lapland point → Sodankylä/subarctic,
  Tokyo point → subtropical_humid.
- Test suite matches the pristine baseline (120 passed, 7 skipped). Two
  pre-existing issues are **not** caused by this change and reproduce
  identically on the unchanged tree: the
  `test_weather_calibration::test_extreme_weather_within_calibration_envelope`
  K-rate envelope (sampling-flaky) and the `test_stat_invariants` errors
  (need a DB setup absent in this container).

### Not verified

No real-browser click-through — the work was done in a headless
container, so the editor UI was exercised via the Flask test client and
DB assertions rather than a live browser session.

---

## Design notes / trade-offs

- **Distance metric.** Squared equirectangular distance, not haversine —
  it's monotone in true distance so nearest-neighbour ranking is
  identical, and the user explicitly said exactness doesn't matter.
- **Single source of truth for climate.** The gazetteer lives in the
  engine (`weather.py`), not a separate `o27v2/data` file, so the
  lower-layer engine stays self-contained and the editor reads its
  pick-list from the same place the weather draw does.
- **Backward compatibility.** `draw_weather`'s new params default to
  `None`; the name-lookup path and RNG sequence are unchanged, so
  existing seeds reproduce bit-for-bit.
- **Quirks are not shape-gated in the editor.** Generation still gates
  quirks by shape, but manual editing intentionally allows any
  combination — the whole point was to "get weird."
- **League renaming is display-only and independent per name.** Avoids
  magic prefix-rewriting that could clobber a division the user is also
  renaming in the same submit.
