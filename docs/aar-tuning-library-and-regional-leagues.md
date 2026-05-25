# After-Action Report — Engine Style Library, Eclectic Randomizer, Reseed-into-Style + Infrastructure-Driven Regional Leagues

**Date completed:** 2026-05-25
**Branch:** `claude/tuning-library-expansion-3fFTu`

---

## What was asked for

User wanted to "start expanding the tuning engine library way more than what we
have now & to add more presets for various worldwide leagues so [they] can just
start testing and playing." The session scope evolved across several pivots:

1. **Engine presets first, then "national league" / worldwide playstyles.**
   Initial ask: a much bigger preset library plus an eclectic randomizer.
2. **A correction on talent scope:** styles should shape **regens / new-gens,
   not retroactively rewrite existing players** — and that's the *point*, not a
   caveat. The user has no existing league to preserve (pure sandbox), so we
   explicitly stopped optimizing for "instant effect on the current roster."
   They also wanted **"different kinds of resets"** — specifically the ability
   to reseed a league *into* a style in one action.
3. **A hard correction on calibration:** *"I don't care if scores blow past
   19-26 r/g, I never set that rule."* R/G is NOT a target; presets are defined
   by direction/identity, never tuned to hit a number. (This matches the
   existing in-code warning at `o27v2/config.py:251`.)
4. **A complete reframe of the "worldwide" idea:** *"O27 isn't baseball taking
   root in existing baseball countries. It's a new sport spreading through
   cricket countries, secondary markets, urban environments where a full
   baseball field is impossible."* Play style must emerge from **infrastructure,
   economics, and existing athletic culture — refusing tropes** ("oh, they're
   fast there"). Field size is the single biggest variable. Weather can factor.

After exploring what the engine can express, I surfaced three scoping
decisions; the user chose: **(a)** build the per-league park-geometry hook,
**(b)** ship regions as **standalone single-league configs** (not a unified
universe), **(c)** identity from **geometry + talent + weather** this round
(no per-league joker-mix / stay biasing yet).

---

## What was built

### Part 1 — Engine style-preset library (data)

`o27v2/engine_config.py`: grew the preset shelf from 2 → 12. New entries across
three classes, each bundling **live game-mechanics knobs** (felt on the next
game) *and* **`GEN_SHIFT_*` talent-pool knobs** (felt after a reseed):

- **Era recreations:** `era_1968` (pitcher-dominant), `era_1987` (HR spike),
  `era_2010s_tto` (three-true-outcomes).
- **Stylistic identities:** `junkball`, `launch_circus`, `contact_carnival`,
  `speed_demon`, `workhorse`.
- **Engine stress-tests:** `knifes_edge` (max-offense), `pitchers_hellscape`
  (min-offense).

New presets auto-surface as buttons in the UI — the existing
`{% for key, label in examples.items() %}` loop renders them with zero extra
wiring. Every knob name was validated against the auto-discovered `DEFAULTS`
set; the coupled `CONTACT_*_BASE` triple is always set together and sums ~1.0.

### Part 2 — Eclectic randomizer (code)

`engine_config.randomize_overrides(seed, n_knobs)` + `_RANDOMIZE_RANGES` (27
guard-railed knobs). Draws 8-12 knobs from sensible per-knob ranges (roughly
default ±50%, clamped well inside the math limits), coerces ints, and ~70% of
rolls also sets the contact triple coherently. Persists via the same
`_store` + `apply_overrides(force=True)` path as `apply_preset`. UI: a
`🎲 Randomize (Eclectic)` button + optional seed field; handled by a new
`action == "randomize"` branch in `engine_settings_post`.

### Part 3 — Reseed-a-league-into-a-style (code)

New route `POST /api/league/reseed-with-style` (`o27v2/web/app.py`) + a
confirm-gated UI control. The footgun it solves: `/api/season/reset` does
`db.drop_all()`, which drops `sim_meta` — wiping the engine tuning — *before*
it regenerates. So a naive "load style, then reset" loses the style. The new
route resolves the style bundle (preset key / saved environment / current
working tuning) **before** the wipe, then re-stores + force-applies it **after**
`init_db()` but **before** `seed_league()` reads `GEN_SHIFT_*`.

### Part 4 — Per-league park geometry (engine hook)

The headline of the regional work. Field dimensions were previously rolled from
one global shape distribution, so regions could only differ by talent/weather —
which would have erased the user's central thesis (small urban footprint vs.
converted cricket ground). Added:

- `_roll_park_dimensions(rng, shape_weights=None)` — optional per-shape weights
  override the global `_PARK_SHAPE_WEIGHTS`.
- `_PARK_PROFILES` — six named geometries (`urban_small`, `brazil_futsal`,
  `cricket_grounds`, `mixed_split`, `coastal_inland_mix`, `wild_variance`) +
  `_resolve_park_shape_weights()` (accepts a named key OR a raw `{shape:weight}`
  dict; falls back to global on empty/unknown/degenerate).
- `seed_league` reads a `park_profiles` config block keyed by league name and
  passes the resolved weights into the per-team dimension roll.

### Part 5 — Blended-locale cities + weather coverage (supporting fixes)

- **`o27v2/team_naming.py`:** a `{region: weight}` blend now resolves team
  **cities** (not just player names) by unioning each region's city pools
  (`_resolve_locale_city_keys`). Previously a dict locale on a *non-canonical*
  league name would crash on `(locale or "").strip()` / `.encode()`. Now a
  mixed-origin league gets mixed-but-regional cities → regional weather.
- **`o27/engine/weather.py`:** added the tropical SE-Asian / South-Asian /
  African / Pacific cities the regional pools actually generate but that fell
  back to a temperate default (Davao, Quezon City, Medan, Semarang, Makassar,
  Johor Bahru, Kota Kinabalu, Ho Chi Minh, Abidjan, Kinshasa, Apia, Nukuʻalofa,
  Ahmedabad/Rawalpindi as arid/subtropical, etc.). So "weather emerges from
  place" actually holds — hot/humid → fatigue bites harder, no weather knob.

### Part 6 — Six standalone region configs (data)

`o27v2/data/league_configs/region_*.json`, each a single league that pairs a
park profile + talent `style_profiles` + name-region pipeline + host cities.
Identity is infrastructure-first, not national character:

| Config | Parks | Talent (style bias) | Origin pipeline |
|---|---|---|---|
| `region_sea_urban` (14) | tiny urban bandbox/short-porch | contact+power up, ground-ball/command arms (velo down) | Philippines + SE Asia + Malaysia |
| `region_subcontinent` (12) | oval converted cricket grounds | placement (contact/eye/baserun), cricket fielding (arm/def), spin (movement), power way down | `south_asia` |
| `region_west_indies` (12) | split: cricket grounds + small urban | most athletic (power/speed/arm) | cricket Caribbean + Dutch + Guyana/Suriname + Latin + a slice of Africa |
| `region_africa` (12) | coastal small + inland big | football converts (speed/stamina/baserun), finesse developing | `africa` + `africa_cricket` |
| `region_brazil` (10) | futsal-court tiny | elite contact, badly underdeveloped pitching → run explosion | `south_america` + Latin |
| `region_pacific` (8) | wild variance (every shape) | rugby converts (power/arm/speed), finesse low | `pacific_islands` + ANZAC |

`_UNIVERSE_SPEC.md` updated with a `park_profiles` section (§5b) and a weather
note (weather follows the host city; keep a blend climate-coherent).

---

## Files changed

```
# Commit 1 — preset library + randomizer + reseed
o27v2/engine_config.py                         | +248  (12 presets+labels,
                                                        _RANDOMIZE_RANGES,
                                                        randomize_overrides)
o27v2/web/app.py                               |  +81  (randomize branch,
                                                        reseed-with-style route,
                                                        league_configs in render)
o27v2/web/templates/engine_settings.html       |  +88  (randomize button,
                                                        reseed-into-style form+JS)

# Commit 2 — regional leagues + park-geometry hook
o27v2/league.py                                |  +89  (_PARK_PROFILES,
                                                        _resolve_park_shape_weights,
                                                        shape_weights param,
                                                        park_profiles in seed_league)
o27v2/team_naming.py                           |  +27  (_resolve_locale_city_keys,
                                                        dict-safe seed)
o27/engine/weather.py                          |  +17  (tropical city coverage)
o27v2/data/league_configs/_UNIVERSE_SPEC.md     |  +35  (park_profiles docs)
o27v2/data/league_configs/region_*.json         |  NEW × 6 (region configs)
```

No DB schema changes (`park_profiles` is config-time only; it feeds the existing
`teams.park_shape` / `park_dimensions` columns from the prior ballpark work).

---

## Verification

### Presets move in the intended direction (o27 fixture batch, 120 games)

```
preset               R/G    K%    HR%    BA
default              32.2  14.5   4.67  .376
era_1968             25.5  16.3   3.17  .339   pitcher-dominant, low power
era_1987             34.2  14.5   5.24  .388   HR spike on a normal line
launch_circus        35.8  14.8   5.71  .404   HR-heavy
contact_carnival     37.4  12.6   5.33  .412   lowest K, highest BA
knifes_edge          47.1  11.2   7.51  .460   max offense
pitchers_hellscape   20.3  19.3   1.87  .296   min offense, highest K
junkball             23.9  13.6   1.86  .331   soft contact, low power
```

No target band enforced — overshoot is fine; only shape matters.

### Randomizer guard rails

200 seeds: every produced value within its `_RANDOMIZE_RANGES` bound, contact
triple (when set) sums ~1.0, 8-12 knobs perturbed, no degenerate/crash output.

### Reseed-into-style end to end

Reseeding into `era_1968` (GEN_SHIFT_PITCHING +12 / POWER -8) shifted the
**regenerated** pool's mean PITCHING 38.2 → 42.8 and POWER 49.6 → 46.2, and the
style **survived the DB rebuild** (15 overrides still live) — the sim_meta-wipe
footgun is handled. Existing players are never rewritten (structural: loading a
style only setattrs config + writes sim_meta; nothing UPDATEs the players table).

### Geometry drives regional style (2-week sim, seed 7)

```
region                 R/g    HR/g   HR%    3B/g
region_subcontinent    32.7   3.03   3.69   1.91   oval cricket grounds
region_sea_urban       44.3   7.81   8.59   2.18   urban bandbox
region_pacific         42.8   7.47   8.26   1.68   wild variance
region_brazil          57.6   9.70   9.46   2.40   futsal bandbox
```

**HR% spans 3.69 → 9.46 (2.5×) purely from park geometry + talent pool.**
Subcontinent is the lowest-power, lowest-scoring manufacture-runs league;
Brazil hit the "45-40 regularly" run explosion the brief described.

Talent means confirm the development thesis (seed 7): subcontinent POWER 44 /
CONTACT 55 / MOVEMENT 54 / PITCHING 35 (placement + spin, low velo); Pacific
POWER 56 / CMD 44 (raw tools, low finesse); Brazil PITCHING 32 (underdeveloped).

### Weather emerges from place

Hot/humid team-city share after the coverage fix: `region_sea_urban` 14/14,
`region_subcontinent` 11/12, `region_brazil` 9/10, `region_west_indies` 9/12,
`region_africa` 8/12, `region_pacific` 5/8. The non-hot remainder is
*legitimately* non-tropical (Cape Town mediterranean, Australian cities
temperate, Kathmandu highland) — not resolution failures.

### Regression

`pytest tests/` (excluding the DB-seed-dependent `test_stat_invariants`):
**130 passed, 7 skipped.** Existing configs `o27_global` (84 teams, generated
cities), `international` (36), `30teams` all seed without regression.

---

## Reused vs new

**Reused:**
- The entire ballpark-shape system from the pre-modern-park-revival session
  (`_PARK_SHAPES`, `_roll_park_dimensions`, `park_effects.py`). The geometry
  hook is a thin per-league weighting on top of work that already existed — the
  `oval` shape was even pre-labeled "Cricket-Ground Revival."
- The `style_profiles` + `name_regions` per-league infrastructure (and the
  already-built cricket/football/secondary-market name pipelines in
  `regions.json` — `south_asia` "cricket pipeline," `malaysia` "structural
  growth market," etc.). Someone had already built the talent-origin layer to
  the user's exact thesis.
- The weather climate-archetype + per-city resolution system.
- The preset persistence path (`apply_preset` / `save_overrides`) — the
  randomizer and reseed both reuse it rather than inventing storage.

**New:**
- 10 engine presets + the eclectic randomizer + the reseed-into-style route.
- The per-league park-geometry hook and six park profiles.
- Dict-locale → blended-cities resolution in `team_naming`.
- Six region configs.

---

## Honest gaps / what's still open

1. **`GEN_SHIFT_*` is global; `style_profiles` is the per-league lever** and it
   shifts the *mean*, not the spread. "Widest variance" (West Indies) is
   approximated via a genuinely mixed origin pipeline, not a true σ bump.
2. **Player-origin and team-city are coupled** in a standalone single-league
   config (both come from the league's `name_regions`). So the "Japanese/Korean
   players in the SE Asian league" flavor was deferred — I dropped the
   `east_asia` slice from SE Asia to keep its climate coherent (it was dragging
   in cold Sapporo). That cross-pollination is properly an emergent property of
   a **unified universe** (declined this round), where inter-league signings are
   real.
3. **Oval parks suppress HRs strongly but boost triples/ITP only modestly.** The
   HR suppression is the clean, dominant signal (3.69% vs 9.46%); pushing
   triples/inside-the-park harder would need the *global* ITP knobs, left
   untouched per the geometry+talent+weather scope.
4. **Per-league joker-archetype mix and stay-skill biasing are not built** —
   "power jokers dominate the Pacific" / "Brazil built on stay" would need a
   second engine hook (jokers + stay attrs are global today). Deferred.
5. **Standalone regions have `postseason: "none"`** (mirrors the peer-universe
   convention) — regular season is fully playable/testable, but there's no
   bracket yet for a single league.
6. **Weather's mechanical effect is modest** (stamina-decay ~0.95-1.07). The
   regional fatigue identity leans more on the talent pool (e.g. Africa's
   stamina bias, Brazil's stamina penalty) than on climate alone.
7. **Wave-2 "global styles" is intentionally just these six regions.** More can
   be added as pure data (a config + optional new park profile) with no engine
   change.

---

## Process notes

- The scope corrected four times; the biggest pivot was the user rejecting
  national-character framing entirely ("refusing tropes"). The right response
  was to stop proposing styles and first map what the engine can *express*
  (geometry / weather-from-place / talent-development / talent-origin), then
  show that three of those four already existed and only geometry needed a hook.
  That reframe is what made the build small.
- Discovering that `regions.json` already encoded the user's exact thesis
  (cricket pipelines, "structural growth market," a year-1→5→10 maturation arc)
  saved inventing a talent-origin layer — and was a strong signal the engine
  author shared the design intent.
- The "is a region a config or an engine change?" question was the key
  architectural call. Answer: mostly a config — the one genuine gap (per-league
  park geometry) was a ~90-line, fully contained hook on top of the existing
  shape system.
- Catching the dict-locale crash before shipping mattered: `o27_global` only
  survives a dict locale because its league names are *canonical*; a
  non-canonical regional league with a dict locale would have crashed
  `seed_league`. Fixing it also unlocked blended-but-regional cities (the right
  behavior for mixed-origin regions like West Indies).
- The weather coverage gap was caught by *measuring* (seed + count climates per
  region) rather than trusting the city pools. 7/14 SE-Asian cities were
  silently resolving to a temperate default; the table additions closed it.
- Per the user's explicit instruction, **no preset or region was tuned toward a
  target R/G.** Verification checks direction/shape relative to default only.
