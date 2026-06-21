# After-Action Report — MLB baseline profiles + talent-style randomizer

**Date:** 2026-06-21
**Branch:** `claude/vigilant-davinci-hn34xy`
**Status:** Shipped to the league/universe builders. Profiles validated to
load + resolve; not sim-calibrated (see §4).

---

## 1. Why

Owner: the league setup is skewed toward exotic O27 flavors — "none of
this is optimized for a league that uses MLB-style parks or an MLB-style
talent profile, it's weirder" — and wanted a randomizer to roll talent
styles instead of setting sliders by hand when comparing styles.

Two concrete gaps:
- **No MLB talent baseline.** `_STYLE_PROFILES` had only regional flavors
  (npb/dominican/european/caribbean/athletic). The "O27 MLB" quick-add
  even used `dominican` (free-swinging TTO) as its stand-in for MLB.
- **No MLB park geometry.** `_PARK_PROFILES` were all exotic
  (futsal-tiny, cricket-grounds, wild-variance) — nothing producing
  conventional balanced-with-asymmetry MLB parks.
- **No randomizer** for the custom talent sliders.

## 2. What changed

`o27v2/league.py`
- `_STYLE_PROFILES["mlb"]` — modern MLB baseline: `power +7, eye +3,
  contact −3, pitcher_skill +9, command +2, movement −2` (disciplined TTO:
  real HR, decent walks, high-velo high-K, fly-ball tilt). Distinct from
  `dominican` (which is more extreme power + negative eye).
- `_PARK_PROFILES["mlb_standard"]` — `balanced 0.50` with classic quirks
  (short porches, deep gaps, occasional bandbox / Fenway triangle). All
  keys verified against `_PARK_SHAPE_NAMES`.
- Labels for both; `style_profile_label("mlb")` → "MLB (modern baseline)".

`o27v2/web/app.py`
- `mlb` added to `_universe_style_options` and the custom-builder preset
  list. `mlb_standard` auto-appears via `get_park_profiles()`. Both also
  flow into the single-league form (its dropdowns iterate the same lists).

`o27v2/web/templates/universe_new.html`
- "🎲 Randomize" button in the custom-style panel. `rollRandomBiases()`
  produces a *coherent* style — emphasizes a handful of knobs (3–4 hitter,
  1–2 pitcher) at meaningful ±magnitudes (8…STYLE_MAX) rather than
  jittering all twelve into noise — so each roll is a recognizable,
  comparable identity. Re-click to re-roll; zeroes unselected knobs first.
- "⚾ O27 MLB" quick-add now uses `style:'mlb', park:'mlb_standard',
  locale:'mlb'` instead of `dominican`.

## 3. Validation

- Profiles load; `mlb` style and `mlb_standard` park resolve; park weights
  non-degenerate; no unknown shape keys; both templates parse; `app.py` /
  `league.py` compile.

## 3b. Talent styles (the actual ask)

Owner clarified twice: **not** nationality stereotypes, **not** game-mechanic
strategies — *talent styles*, i.e. ways the player **generator** produces
different kinds of players, named purely for what the talent pool is like.
Same machinery as the regional profiles, reframed as abstract talent
archetypes. Added to `_STYLE_PROFILES` (labels + both builder menus, listed
ahead of the regional flavors):

| key | the players it produces |
| --- | --- |
| `sluggers` | big raw power, some swing-and-miss |
| `pure_hitters` | bat-to-ball contact, line drives, modest pop |
| `on_base` | disciplined eye, works counts, gets on |
| `speedsters` | fast, athletic, base-stealing, rangy |
| `defenders` | glove-and-arm gifted, light bats |
| `power_arms` | velocity & strikeouts, less precise |
| `finesse_arms` | command & movement over velocity |
| `five_tool` | loud raw tools, undeveloped polish (boom/bust) |
| `polished` | refined, sound, few extremes (high-floor) |
| `hitting_rich` | pool skews to bats over arms |
| `pitching_rich` | pool skews to arms over bats |

All validated: keys ∈ `_CUSTOM_STYLE_ATTRS`, magnitudes within ±`_CUSTOM_STYLE_MAX`, labeled.

## 4. Not done / deliberate

- **Existing regional + new profiles not sim-calibrated.** The bias
  numbers are hand-set from the attribute semantics, not measured against
  the live engine. A calibration pass (sim each profile, read the run
  environment / K-BB-HR / SB / decay it actually produces, adjust) is the
  honest next step before trusting the magnitudes.
- **Randomizer is talent-style only**, not parks. Park randomization is a
  trivial follow-up (random pick from `park_options`) if wanted.
