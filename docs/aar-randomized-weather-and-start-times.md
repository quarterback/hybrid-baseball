# After-Action Report — Randomized Weather & Location-Based Start Times

**Date written:** 2026-05-31
**Branch:** `claude/randomized-weather-start-times-VXVdW`
**PR:** #157

Two static game-condition mechanics got replaced with location-aware randomized rolls:
the box score now reports a **real rolled temperature** instead of a fixed per-tier value,
and every game carries an **actual first-pitch clock time** in place of the old `dusk`
flag. Start times are modeled on real baseball conventions (MLB / NPB / KBO, scrambled
per game) and read in the home park's local time zone. The fading-light penalty that used
to ride on the `dusk` cloud tier now keys off the game's start time versus the park's
actual sunset.

---

## What it is

### Temperature — a real number, not a bucket midpoint

`Weather` still draws a categorical tier (`cold` / `mild` / `warm` / `hot`) from the
per-archetype, per-month climate table — that logic is unchanged and stays location- and
season-aware. What changed: `draw_weather` now also rolls an **exact °F within the tier's
range** and stamps it (`o27/engine/weather.py`, `_F_RANGE_BY_TEMP` + the `rng.randint` in
`draw_weather`). The tier still drives the engine multipliers; the rolled number is what the
page shows.

| Tier | °F range | Fallback midpoint (legacy rows) |
|------|----------|----------------------------------|
| cold | 38–55 | 47 |
| mild | 56–72 | 64 |
| warm | 73–85 | 79 |
| hot  | 86–99 | 92 |

`Weather.fahrenheit()` returns the stamped `temperature_f` when present, else the tier
midpoint — so pre-feature rows still render sensibly. `short_label()` and
`box_score_line()` now lead with `f"{self.fahrenheit()}°F"` instead of the word.

### Start time — local clock, scrambled convention

New module `o27/engine/gametime.py`. `draw_game_time(rng, game_date, lat, lon, city)`
returns a frozen `GameTime(start_minute, utc_offset, low_light, convention)`. Two concerns,
deliberately split:

**Location decides the clock and the daylight.**
- `utc_offset_for(city, lon)` — Zaryan cities carry explicit offsets
  (`zaryan_climate.utc_offset`); everyone else derives from longitude (15° per hour).
- `sunset_minute(lat, game_date)` — the standard sunrise equation (solar declination from
  day-of-year, hour angle from latitude), clamped for polar day/night. No dependencies,
  accurate to ~15 min, deterministic.

**Convention decides what o'clock games begin.** One of three real-world cultures is
scrambled per game (`_CONVENTIONS` in `gametime.py`):

| League | Day slots | Night slots | Day-game lean |
|--------|-----------|-------------|---------------|
| MLB | 1:05 / 1:10 / 1:35 / 2:10 / 4:05 | 6:40 / 7:05–7:15 / 7:35 / 8:10 | weekends; Thu getaway bump; weekdays night |
| NPB | 1:00 / 1:30 / 2:00 | 5:45 / 6:00 | weekends ~45–55%, else night |
| KBO | 2:00 / 5:00 | 6:30 | weekends ~50–60%, else night |

`p_day` is a weekday-indexed tuple (Mon=0 … Sun=6) per convention. The convention is
flavor only — it picks the time, then is discarded (not persisted).

### The low-light penalty (what `dusk` became)

`dusk` is **retired** as a cloud value. `CLOUD_TIERS` is now `("clear", "overcast")` and
`Weather.from_row` coerces any legacy `cloud_tier == "dusk"` to `overcast`.

The "harder to see the ball" effect now rides on `Weather.low_light`, decided at stamp time:
a game trips low-light when first pitch falls within `_LOW_LIGHT_LEAD_MIN` (90) minutes of
sunset or later — a nine-inning game runs ~3 h, so an hour-before-sunset start spends most of
itself under deepening dusk. Because sunset comes from latitude + date, the flag is genuinely
geographic and seasonal: a high-latitude midsummer 7 PM game (sunset ~9 PM) is **not**
low-light; an April 6:30 PM game (sunset ~7:30 PM) is.

The engine reads it through the same single `Weather` context it already used. The penalty
multipliers (`o27/engine/weather.py`):

| Rate | clear | overcast | + low light |
|------|-------|----------|-------------|
| Strikeout (`k_multiplier`) | 1.00 | 1.005 | ×1.03 |
| Error (`error_multiplier`) | 1.00 | 1.02 | ×1.05 |

These are the same magnitudes the old `dusk` tier carried; only the trigger moved.

---

## Where it's stamped and shown

Stamped at schedule time (`o27v2/schedule.py:seed_schedule`) and re-rolled in two other
places that already re-rolled weather: playoff scheduling (`o27v2/playoffs.py`) and the
team-relocation route (`_reroll_weather_for_team` in `o27v2/web/app.py`). All three call
`draw_game_time` alongside `draw_weather` on the same RNG.

Surfaced in three spots:
- **Schedule listing** — a "First pitch" column via the `first_pitch` Jinja filter
  (`o27v2/web/app.py`, `o27v2/web/templates/schedule.html`).
- **Box score footer** — `First pitch 7:05 PM ET` in both the HTML renderer
  (`o27v2/web/box_score.py`) and the plaintext export (`o27v2/web/box_text.py`), formatted
  by `gametime.format_start`.

### Schema

Four columns added to `games` (`o27v2/db.py`), with the idempotent `ALTER TABLE … ADD COLUMN`
migration block alongside the existing ones:

| Column | Type | Meaning |
|--------|------|---------|
| `temperature_f` | INTEGER | exact rolled °F |
| `start_minute` | INTEGER | minutes after local midnight (first pitch) |
| `start_utc_offset` | INTEGER | home-park UTC offset, for the TZ label |
| `low_light` | INTEGER | 1 = game runs into fading light |

---

## How it unfolded

1. **Recon before code.** Two parallel read-only sweeps mapped the weather engine and the
   display layer first. That surfaced the load-bearing fact: `dusk` was never a start time —
   it was a cloud tier with a gameplay penalty. The whole task reframed around that.
2. **Clarified before building.** A three-question prompt locked the spec: roll a real °F,
   randomized clock time by location, tie the visibility penalty to late starts.
3. **Requirements grew mid-build** — first "clone MLB times + time zones," then "NPB and KBO
   too, scramble them all." Folded in with targeted research rather than guesswork. The
   synthesis: **location** owns the time zone and sunset; **convention** is scrambled.

## Findings / watch-items

- **Pre-existing flaky calibration test.** `tests/test_weather_calibration.py` is
  non-deterministic across processes: `o27v2/sim.py:604` seeds a rest-day RNG from
  `hash((game_date, str(team_role), id))`, and CPython salts string hashing per process
  (`PYTHONHASHSEED`). Under fixed seeds the baseline fails 2 of 3, this branch 1 of 3 — the
  branch is no worse. The test's `dusk` reference was updated to `low_light=True`, which
  yields a near-identical K-multiplier (1.045 vs the old 1.040). Worth de-flaking that seed
  someday, but out of scope here.
- **Pre-existing suite breakage.** The full run shows 4 failures + 11 errors that reproduce
  **identically on the untouched baseline** (fixture/DB-setup quirks in a fresh container) —
  none attributable to this work.
- **Low-light frequency rose by design.** Tying the penalty to late starts means ~75% of
  chilly-April evening games carry it (vs. ~10% under random `dusk`), tapering through summer
  as sunsets move later. It stays inside the per-game flavor envelope but nudges league K/error
  rates up slightly. The knob is one line (`_LOW_LIGHT_LEAD_MIN`, or flag only post-sunset
  starts) if a rarer effect is preferred.

## Verification

- Unit probe: sunset realistic (NYC 7:27 PM June / 4:40 PM Jan), conventions evenly scrambled,
  °F rolls real.
- Full 2,430-game schedule seed: all four columns populated; start-hour spread shows the
  MLB/NPB/KBO mix (1/2/4 PM, 5 PM, 6/6:30 PM, 7/8 PM).
- Flask end-to-end: `/schedule` and `/game/<id>` render first pitch + rolled °F.
- Full test suite: no regressions versus baseline.
