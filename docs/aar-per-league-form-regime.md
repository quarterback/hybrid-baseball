# After-Action Report — Per-league team-form regime (hot/cold band as a league setting)

**Date completed:** 2026-06-23
**Branch:** `claude/vigilant-davinci-hn34xy`

---

## What was asked for

While tuning the hot/cold form band, the user reframed the question from "what
should the band be" to "what should the band be *per league*":

> "The sport has to thrive on offense, but letting scores get low allows [me] to
>  test the tactical diversity in leagues. I'd also be curious if we turned that
>  off — would teams just hit normally and talent would dictate everything game
>  by game?" … chosen direction: **"Make it a per-league setting."**

So: expose the team-form variance regime per league, so different leagues can run
different regimes (talent-pure vs standard vs high-drama) side by side.

## The experiment that motivated it

Stronger team (talent gap ~0.18) vs weaker, 120 games each:

| | stronger win% | score swing (sd) | runs/team |
| --- | --- | --- | --- |
| form OFF | **83.3%** | 10.4 | 17.5 |
| form ON (.66) | 78.3% | 21.3 | 24.3 |

Turning form off makes **talent more decisive** (83% vs 78% — never 100%, cluster
luck keeps games live) and **scores much tighter** (~half the variance). Form on
is what manufactures both the low-scoring nail-biters (tactical diversity) and the
blowouts. A later live-DB check underlined it: with form off, league scoring fell
to **~10.6 runs/team, sd 4.7** — the cricket-lean run environment leans heavily on
the form-on asymmetry.

## What was built

The hot/cold band (`LOCKED_FORM_SIGMA/MIN/MAX`) is now a **per-league override**
of the global engine config, plumbed exactly like the Power Play / Cricket Order
opt-ins.

- **Engine (`o27/engine`):** `GameState` gains `form_sigma / form_min / form_max`
  (default `None`). `_locked_in_form` reads each from the state override, falling
  back to the global `cfg.LOCKED_FORM_*` when `None`. `sigma <= 0` disables form
  (talent-pure: every half at 1.0). With all-`None`, behavior is byte-identical
  to before.
- **Sim (`o27v2/sim.py`):** stamps the three columns from the home team's row
  onto the engine state per game (both teams share a league; home row is
  authoritative — same shape as Power Play / Cricket Order).
- **DB (`o27v2/db.py`):** three nullable `REAL` columns on `teams`
  (`form_sigma/min/max`) + a migration for existing saves. `NULL` → global config,
  so legacy leagues are unchanged.
- **Regimes (`o27v2/league.py`):** named presets writable from the UI —
  `default` (global), `talent` (form off), `standard` (the current band,
  explicit: 0.66 / 0.92 / 2.15), `high_drama` (wide both ways: 0.95 / 0.82 /
  3.40 — cold lows *and* hot halves into the 3.x range). `resolve_form_regime`
  maps a key → columns; `form_regime_key` reverse-maps for display.
- **UI:** a "Team Form" `<select>` per league on the universe builder
  (`universe_new.html`, incl. its preset clone JS) and on `/league/edit`
  (`league_edit.html`), with the create/edit handlers in `app.py` writing the
  chosen regime to every team in the league.

## Validation

- **Engine override (`o27v2/tests/test_form_regime.py`, 6 tests):** `sigma=0` →
  identity; a wide band lets halves clear the old 2.15 ceiling; a high floor
  clamps every half above it; `None` falls back to the global band; all four
  regimes round-trip key→columns→key; unknown key → default.
- **Suites:** `o27/tests` + `tests/test_stat_invariants.py` + the new file →
  **203 passed.** Template renders: 22 passed (2 pre-existing failures unrelated
  to this work — a `wrc_plus` stat-category assertion and the season-archive
  writer).
- **End to end:** fresh `initdb` creates the columns; setting a league to
  `talent` (form off) and simming 40 games gave **mean 10.6, sd 4.7** — visibly
  tighter and lower than the form-on default, confirming the per-league control
  reaches the engine.

## Scope / not changed

- **Global defaults untouched.** The `default` regime keeps the current band
  (0.66 / 0.92 / 2.15), so every existing league is unchanged until explicitly
  switched. The user's earlier "floor 0.95 / hot into 3.xx" idea is available as
  the `high_drama` preset rather than a global change.
- **Preset set, not free-form.** The UI offers four named regimes; the DB stores
  raw `(sigma, min, max)` so finer per-league bands are possible later without a
  schema change (add presets, or a custom-entry UI).
- **Interleague edge.** Form is stamped from the home team's league row; a
  cross-league game adopts the home league's regime (acceptable — form is a
  per-half batting-team effect and the vast majority of games are intra-league).
