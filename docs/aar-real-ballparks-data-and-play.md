# After-Action Report — Real Ballpark Data: Play In It, Generate From It

**Date completed:** 2026-06-26
**Branch:** `claude/mlb-stadium-dimensions-7icja0`

---

## What was asked for

The owner pasted a large stadium dataset (transposed, parks-as-columns) and a
link to the source Google Sheet
(`docs.google.com/spreadsheets/d/14vzfHwMBmsE6HKQHlizyfozplYny5Agnm3sdP992dng`),
then stated the intent plainly:

> being able to have actual ballpark data can 1) influence the generation of
> alternate future parks that are realistic but varied and 2) let me play O27
> in actual baseball stadiums to see how the game performs with real dimensions

So two deliverables off one dataset: a **generator** seeded by reality, and a
**play-in-real-parks** mode. The pasted data covered the whole affiliated
pyramid — MLB, Triple-A (IL/PCL), Double-A (EL/SL/TL), High-A (MWL/NWL/SAL),
Single-A (CAL/CAR/FSL), the complex/spring leagues (ACL/FCL) and the Dominican
academies (DSL/MLBDL).

## What was built

### 1. The dataset — `o27v2/data/real_parks.json` (203 parks)

Rather than hand-transcribe ~150 transposed parks across 16 tabs (error-prone),
`scripts/build_real_parks.py` pulls the published workbook as `.xlsx` and
flattens every level tab into one normalized JSON list. The script is
stdlib-only (xlsx is a zip of XML), so it reruns in a bare sandbox; pass a local
`.xlsx` to rebuild offline. The generated JSON is the committed source of truth.

Each record carries: park name, level + coarse `tier`, MLB team abbrev (MLB tab
only), seven measured outfield distances **and** per-zone wall heights, monthly
temp/humidity (Apr–Oct), roof/surface, altitude, GPS coords, closest city,
seating, and the park-factor block (avg/2b/3b/hr with L/R splits + overall).

Parsing gotcha worth recording: the MLB tab repeats every row label lower down
in an averaged `#VALUE!` "Overall Statistics" helper block. Keying on the
**first** occurrence of each column-A label is what keeps the real human-entered
top block (Left Line 344/8) instead of the averaged junk (331.4/310). The
Athletics' MLB row is a placeholder ("Sutter Health Park" with empty geometry);
the builder backfills it from the AAA-PCL Sacramento entry so the A's are
playable.

### 2. The loader — `o27v2/real_parks.py`

Turns raw records into the two things the sim wants, importing nothing from
`league` (one-directional dependency):

- `park_to_dimensions(rec)` → engine `{lf, lcf, cf, rcf, rf, wall_h, walls,
  shape}`. The spreadsheet stores seven zones; the engine interpolates a fence
  from five, so we feed the five that line up with its spray-angle control
  points (two foul lines, two alleys, dead center). The two gap readings stay
  in the dataset but aren't fed to the five-point model. `walls` is a new
  per-angle height map (see §3); `wall_h` is the rounded mean for any consumer
  that ignores it. `classify_shape()` maps real dims onto the nearest existing
  archetype key for the UI label / quirk gating.
- `park_factors(rec)` → `(park_hr, park_hits)` onto the existing per-team knobs
  (`park_hr ← HR factor`, `park_hits ← AVG factor`).
- `find_by_abbrev()` with an alias map (`AZ→ARI, TB→TBR, KC→KCR, SD→SDP,
  SF→SFG, CWS→CHW, WSH→WSN, ATH→OAK`) so a real-MLB league pairs each club with
  its own stadium before falling back to positional assignment.
- `realistic_park_dimensions(rng, tier)` → deliverable #1: seed from a random
  real park of that tier, jitter each fence point a few feet (with a shared
  corner skew so the park stays coherent), clamp to the engine's physical
  floors. Realistic but varied — lives inside the real distribution instead of
  the deliberately-exotic global generator.

### 3. Per-angle wall heights — `o27/engine/park_effects.py`

The pre-existing hook interpolated fence **distance** by spray angle but used a
single scalar **wall height** — which smears Fenway's 37-ft Monster into a
meaningless average. Added `_wall_at_angle()`, mirroring `_fence_at_angle()`:
interpolate from the record's `walls` map when present, else return the scalar
`wall_h`. `apply_park_effects` now reads `wall_h = _wall_at_angle(spray, dims)`,
so every downstream rule (HR downgrade, picket robbery, wall-ball carom) sees
the real height **at the ball's spray angle**. Fully backward compatible:
generated parks have no `walls` key and behave exactly as before.

### 4. Play in real parks — `seed_league` wiring + `mlb_real.json` config

Two optional, independent config keys, each keyed like the other per-league
options (a bare string applies to every league; a `{league: value}` dict
targets specific ones):

- `"real_parks": "MLB"` — assign **actual stadiums** to teams: own stadium by
  abbreviation first, then positional from a shuffled tier pool. Overrides the
  rolled park's dimensions (+walls), factors, name, and — via the park's real
  coords + city — its weather and first-pitch clock. Quirks are cleared (no
  fictional Ivy Wall on a real park).
- `"park_gen": "realistic"` — route generation through
  `realistic_park_dimensions` for that tier.

`o27v2/data/league_configs/mlb_real.json` is a ready 30-team config
(`real_parks: "MLB"`, `mlb` talent style).

## Verification

- `tests/test_real_parks.py` — 18 tests: dataset counts (203 / 30 MLB), tier
  partition, A's backfill, abbrev aliasing, Fenway Monster mapping (37→3),
  Coors factors, `classify_shape` ∈ engine shape names for all 203, wall
  interpolation + scalar fallback, a Monster-demotes-a-fringe-HR behavioral
  test, realistic-gen floors/variety, and a `seed_league` test asserting all 30
  teams get distinct real parks with persisted per-zone walls. All pass.
- `o27/tests` + `tests/test_park_shapes.py` regression run: 228 passed.
  `test_realism_identity::test_resolve_contact_table_unchanged_*` fails **on the
  clean tree too** (verified via `git stash`) — pre-existing, unrelated to this
  work (it exercises `prob.resolve_contact`, untouched here).
- Seeded `mlb_real`, built its 2430-game schedule, simulated games (BOS→Fenway
  w/ 37-ft Monster, NYY→Yankee Stadium 408 CF, SFG→Oracle, real coords). The A's
  resolve to Sutter Health Park. `o27v2/smoke_test.py`: 10/10 PASS.

## Reused vs new

- **New:** `o27v2/data/real_parks.json`, `o27v2/real_parks.py`,
  `scripts/build_real_parks.py`, `o27v2/data/league_configs/mlb_real.json`,
  `tests/test_real_parks.py`, `_wall_at_angle()`.
- **Reused:** the existing `park_dimensions` shape, `_fence_at_angle`
  interpolation pattern, the `park_hr`/`park_hits` knobs, the per-league config
  plumbing (`config.get(...)` in `seed_league`), the weather/gametime systems
  (driven for free by setting each team's real city + coords).

## Honest gaps / what's still open

- **No web-UI exposure of the new presets.** `mlb_real` is seedable from the
  config dir and `real_parks` / `park_gen` are honored by `seed_league`, but
  the `/new-league` + universe-builder forms don't surface them yet (they
  iterate `_PARK_PROFILES` / style menus). A follow-up should add a "park
  source" picker and a `get_real_park_levels()` helper for the dropdown.
- **Park factors aren't engine-calibrated.** We map the spreadsheet's empirical
  HR/AVG factors onto `park_hr`/`park_hits` directly; whether O27's geometry
  hook + these multipliers reproduce the real run environment is unverified. The
  geometry now does most of the work, so there's a double-count risk worth a
  calibration pass (sim each real park, compare HR/2B/3B vs the listed factors).
- **Realistic generator isn't wired into the universe builder's randomizer**,
  and jitter magnitudes (±6 ft fences, ±2 ft walls) are hand-set, not fit to the
  per-zone variance in the data.
- **Minor-league realism beyond geometry** (tiny seating, turf rates, DSL
  climate) is in the dataset but unused by the sim.
- **Spring-training / DSL parks** are in the dataset and tier-tagged `R`; only
  geometry/weather would apply if a league were built on them.

## Process notes

- Scope arrived as a wall of pasted data with no instruction; an
  `AskUserQuestion` to pin the deliverable was interrupted by the harness, then
  the owner's "1) ... 2) ..." message resolved it cleanly — both options, not
  one.
- Pulling the workbook as `.xlsx` and parsing it with the stdlib beat
  transcribing 203 transposed parks by hand; the first-occurrence label fix was
  the one real parsing subtlety.
