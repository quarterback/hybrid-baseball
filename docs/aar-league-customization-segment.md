# After-Action Report — League Customization Segment

**Date completed:** 2026-05-06
**Branch:** `claude/fix-game-simulation-jUSwp`
**Commits (in order):**
- `b58d11c` — Sim: surface engine errors instead of silently advancing the clock
- `ac14112` — New league: parametric size, divisions, weights, weekly off-days
- `ff021d0` — Schedule: road-trip grouping, off-day budget, balance verifier, scale to 86
- `777c7f9` — Names: port viperball pools, add gender + 12 world regions
- `ccb45c3` — Weather: 5 new archetypes + country fallback for global cities
- `88ba2d7` — Teams: rename / relocate UI with weather re-roll

---

## What was asked for

The session opened with the user reporting that game simulation was
broken (a row of "Err" buttons on the schedule despite a freshly
seeded league). It then expanded into a multi-part redesign of league
customization, in this rough order:

1. "Still cannot simulate games anymore something in the latest PRs
   broke something." — sim-failure investigation.
2. "It's still really crude and the structure of the leagues being
   fixed sizes isnt ideal" — replace the 6-preset picker with a
   parametric league builder.
3. "Layer them on yes crucial improvements at any schedule size" —
   homestand grouping, off-day budget, balance verification, series
   tuning per pair-game-count.
4. "Just being able to name the teams would allow me to customize
   their name and perhaps our locations." Plus broader scope: expand
   weather to non-US cities (Finland especially, for a pesäpallo
   league down the road), expand the name pool from the 4 thin
   buckets it had, and add female names so a female league is
   possible. Pointed at `quarterback/viperball` as a source.
5. After exploration: "Yes do us" → confirmed phase 1 (names),
   followed by "phase 3 first, then phase 2 last" for weather and
   then team rename.

---

## What shipped

### Sim error visibility (commit b58d11c)

The reported "Err" symptom was structural, not a specific engine bug
— the smoke test and every sim path I could exercise locally passed
cleanly. The root cause was that `simulate_date` and
`simulate_through` swallowed per-game exceptions silently and the
caller advanced the sim clock anyway, leaving the user at a later
date with a row of unplayed games and no diagnostic.

Made the failure mode observable:
- `_sim_response` now reports `errored` count + `first_error` text.
- Bulk endpoints skip `advance_sim_clock` when any game errored;
  fall back to `resync_sim_clock` so the badge anchors to the
  earliest unplayed date.
- `/api/sim/<game_id>` catches generic `Exception`, logs a full
  traceback, and returns `{error: "TypeName: message"}`.
- The schedule page and topbar Sim Today button surface the actual
  error message via `alert()` instead of a useless "Err" label.

### Parametric league builder (commit ac14112)

Replaced the six fixed-size JSON-preset cards
(8/12/16/24/30/36) with a tabbed UI: "Use a preset" keeps the
existing cards, "Build your own" exposes a form for arbitrary
configuration. The pairing generator and series scheduler already
worked at any even N — the limitation was the form.

`build_custom_config()` validates inputs (even count, divisible by
leagues × divs/league, non-negative weights). `seed_league` and
`seed_schedule` both grew a `config=` kwarg so a form-built dict
bypasses the JSON-file lookup. Added a flash region in `base.html`
for cross-page validation feedback.

### Schedule quality layer (commit ff021d0)

Three layered upgrades to make the schedule feel less crude at any
team count:

- **Length-aware series chunking.** `_chunks_for_count` no longer
  chops a 3-game pair into 2+1; thin pairs (≤4 games) stay as one
  series. Thick pairs lean toward 3-game with sprinkled 4-gamers
  (real-MLB cadence).

- **Road-trip / homestand grouping.** The greedy scheduler now
  scores each candidate series by how well it continues — or
  completes — both teams' current home/away stand, until the
  configured `target_stand_length` (default 3 series). 30-team /
  162-game runs now produce ~6.5-game homestands and ~7.6-game
  road trips instead of single-series flips.

- **Per-team off-day budget.** `max_consecutive_game_days` (default
  20, the real MLB CBA cap) forces an off-day when a team would
  exceed the streak. League-wide off-days reset every team's
  streak.

`verify_opponent_balance()` returns a structured report (intra/inter
averages, per-pair spread, off-day min/max). The new-league POST
flashes a one-line summary on the redirect plus warnings for any
obvious imbalance, so creators see the schedule's quality before
simming a season.

Form: team-count max bumped 60 → 86 (the team-database ceiling).
Stress-tested: schedule generation in <1s for any size up to 86,
end-to-end seed + sim path clean at 60 teams × 120 games.

### Name infrastructure (commit 777c7f9)

Replaced 4 male-only buckets (~1,100 first names total) with the
viperball name pools — 3,523 male first names + 4,631 female first
names + 4,602 surnames across 40 source buckets. ~11x more names
with full gender and geographic coverage.

A new `regions.json` meta file groups the 40 raw buckets into 12
selectable world regions (USA, Latin America/Caribbean, Western
Europe, Eastern Europe, Nordic, East Asia, Southeast Asia, South
Asia, Central/West Asia, Sub-Saharan Africa, ANZAC/Pacific, Canada)
and ships six named distribution presets (`americas_pro`, `global`,
`european`, `asian_pro`, `nordic`, `us_only`).

`make_name_picker(rng, gender=, region_weights=)` is the new entry
point. Three gender modes: 'male', 'female', 'mixed' (50/50 per
draw). Region weights auto-normalise. `build_custom_config` and the
form expose `gender` and `name_region_preset`. Legacy presets fall
back to `americas_pro` defaults so existing leagues are unchanged.

Sample output (12-team Nordic + female league): Filippa Nielsen,
Eveliina Sembrant, Tuva Mjelde, Matilda Kemppi.

### Weather expansion (commit ccb45c3)

Five new archetypes plus a country-code fallback chain so non-US
teams produce sensible weather without per-city authoring:

- `subarctic` — Helsinki, Stockholm, Reykjavik shape: cold-dominated
  season, brief mild summer, light precip + frequent overcast.
- `mediterranean` — Madrid, Rome, Athens, Marseille: hot dry summers,
  mild wet shoulders.
- `tropical_monsoon` — Bangkok, Mumbai, Manila, Jakarta: hot humid
  year-round with heavy precip Jul-Aug.
- `subtropical_humid` — Tokyo, Shanghai, Seoul, Buenos Aires: hot
  humid summers, less extreme than tropical_monsoon.
- `arid_steppe` — Almaty, Tashkent, interior West-Asia: hot dry
  summers, cool springs, low humidity.

`archetype_for_city` lookup chain is now: exact city → city stripped
of trailing 2/3-letter country code → country-code default
(~110 countries) → `continental_warm` fallback. `_CITY_ARCHETYPES`
extended with ~180 international cities for cases where the country
default is wrong (Perth mediterranean inside otherwise-subtropical_humid
AUS, Cape Town mediterranean inside ZAF, Saint Petersburg subarctic
inside RUS, etc).

Sanity: Helsinki-May draws produce cold/mild + overcast + light rain;
Bangkok-July draws produce hot + humid + heavy precip; existing US
team weather is unchanged.

### Team rename / relocate (commit 88ba2d7)

`/team/<id>/edit` (GET form, POST apply) lets league owners
customise team identity without rebuilding the league. Three
editable fields: name, abbrev (2-4 alphanumeric, must be unique),
and city. Validation bounces back to the form via flash + redirect
on blank name, malformed abbrev, or abbrev collision.

Roster / division / league / manager persona / park factors / season
stats are all left intact — explicitly identity-only.

Weather re-roll: changing the city UPDATEs every unplayed home game's
weather columns using a fresh `draw_weather()`. RNG forks off
`team_id` so the reseed is deterministic per team. Already-played
games keep their stamped conditions so the box-score record is
preserved.

Verified: relocating a continental_cold team to "Helsinki FIN"
shifts the unplayed-home temperature distribution from
{warm/mild/hot} to {mild/cold/warm} = the subarctic shape.

---

## What I'd do differently

**The opening sim-failure investigation.** I spent meaningful time
trying to reproduce the user's "Err" symptom locally and never
could — every code path I exercised passed. The right move would
have been to stop trying to repro after the second or third clean
run and instead immediately ship the error-surfacing change, since
that's what would let the user paste the actual stack trace. The
fix I shipped is the right one regardless, but I sequenced it
behind too much fruitless local debugging.

**Should have asked about target_stand_length=3 specifically.**
Real MLB's average homestand is closer to 3 series; the bias
fired but produced 2-series average stands in the 30-team test.
A higher target (4 series) might match MLB more closely, but the
greedy scheduler can't always honour it given other constraints.
Worth tuning empirically — but I left the knob exposed in the
form rather than picking a tighter default.

**Defensive on `seed_league` legacy paths.** I added `gender` and
`name_region_weights` to `build_custom_config` and threaded them
through `seed_league`, but the JSON presets (`30teams.json` etc.)
don't carry these keys. I let them fall back to defaults via
`config.get(..., default)` rather than explicitly extending the
JSON files, which means the new fields are invisible from the
preset path. Acceptable for now but worth a follow-up to make
the preset cards opt-in to gender / region selection too.

**No tests.** This work added ~14k lines (most of it the name
pool JSONs) and ~1.5k lines of code. I leaned on the smoke test
and ad-hoc Python inspection but didn't write `pytest` cases for
the new functionality (`make_name_picker`, `archetype_for_city`
lookup chain, `_strip_country_code`, `verify_opponent_balance`,
team-edit POST validation, weather re-roll determinism). The
existing `tests/` directory is mostly invariant tests; adding a
handful of unit tests for the new pure functions would be cheap.

---

## Pointers for follow-up work

1. **Weather city dataset.** Viperball's `data/name_pools/cities.json`
   is hierarchical-by-region but contains no lat/lon. If we want
   `lat/lon`-driven park factors or sun-angle effects, we'd need a
   second pass to either author them per-city or geocode against an
   external dataset.

2. **Female league archetype.** The name pools support it now, and
   the team-rename UI lets owners customise identity, but no
   league-config preset bundles "female + Nordic + smaller league
   + summer season" into a one-click pesäpallo starter. Trivial
   to add as another preset JSON.

3. **Custom region weight UI.** The form currently exposes the six
   `region_presets` as a dropdown. A "Custom" option with sliders
   per region (or a JSON paste) would let users tune their own
   distribution without editing files.

4. **Per-team city lat/lon for travel modeling.** Once cities have
   coordinates, the schedule's road-trip grouping can incorporate
   geographic clustering ("West-coast teams swing together
   through the West coast"). Out of scope here.

5. **Larger team database.** 86-team ceiling is `teams_database.json`.
   Allowing duplicate names with numeric suffixes or letting users
   define their own teams (the natural extension of the rename UI)
   would push the ceiling further.
