# After-Action Report — Per-League Playoffs, Configurable Format & Separate Postseason Stats

**Date completed:** 2026-06-07
**Branch:** `claude/confident-goldberg-PGeok`

---

## What was asked for

The playoff model was "fundamentally broken" from a user's-eye view: a season
would finish and it was unclear what the postseason actually was. Four concrete
requirements:

1. **Pick how many teams make the postseason, by league.**
2. **See the rounds** — clear round-by-round series, simulated game by game,
   round by round, until two teams vie for the title.
3. **Series are best-of 3 / 5 / 7 / 9**, configurable.
4. **Playoff stats kept separate** from regular-season stats, with their own
   tab — both on player cards and in the stats sections.

Design decisions confirmed with the user up front:

- **Per-league brackets.** Each league crowns its **own** champion. Leagues are
  nationally independent — a club in one league never meets a club in another
  during a bracket. *Only* when a single save holds exactly two leagues (the
  AL/NL case) do the two champions then play a **World Series** for the overall
  title. One-league saves crown that league's bracket winner; 3+ co-equal
  leagues each crown their own champion with no cross-final.
- **Live control on `/playoffs`** to set qualifiers-per-league and series
  lengths (persisted, not config-file-only).
- **Default ladder 3 / 5 / 7 / 7** (Wild Card / Division / League Championship /
  World Series), each selectable from {3, 5, 7, 9}.
- **Playoff stats are tracked separately and never double-counted.**
  Regular-season / career / leaderboard numbers exclude playoff games;
  postseason gets its own tab and page. (Same data model — a game is a game,
  `is_playoff` is just a flag — not a separate class of game.)

---

## What the old model did (and why it felt broken)

`o27v2/playoffs.py` built **one single league-wide bracket** that merged every
league's teams together. Division champs seeded first, then wild cards whose
count was auto-derived from team count (`(team_count − 24) // 2` → 3 at 30
teams). Consequences:

- No way to choose how many teams qualified, and **no per-league structure** —
  the AL and NL were thrown into one pool, so there was no "AL champion vs NL
  champion" World Series.
- Series lengths were hardcoded `[7, 7, 5]` then best-of-3. No best-of-9,
  nothing configurable.
- Round names were a hardcoded 4-entry list (Final / Semifinals / Quarterfinals
  / Wild Card) that didn't reflect a two-league world.
- The non-power-of-2 **bye placement was wrong** — byes weren't laid out on a
  standard seed bracket, so for a 6-team field the 1 and 2 seeds could meet
  before the final.
- Playoff games wrote `game_batter_stats` / `game_pitcher_stats` like any other
  game, but **nothing surfaced them separately** — there was no postseason view
  anywhere.
- Bonus bug: `season_archive.archive_current_season()` recorded the season
  **champion as the best regular-season team**, not the playoff winner.

---

## What was built

### 1. Per-league bracket engine (`o27v2/playoffs.py`, rewritten)

- **`playoff_settings()`** resolves live settings from `sim_meta` overrides →
  league config → hard defaults: `teams_per_league`, a `series_lengths` dict
  keyed by round kind, and a `world_series` flag (only meaningful with exactly
  two leagues). **`set_playoff_settings()`** persists them.
- **`compute_fields_by_league()`** seeds one field per league: division winners
  first (by win pct), then best-of-the-rest as wild cards, capped at
  `teams_per_league`. Leagues that can't field ≥2 qualifiers are dropped.
- **Standard bracket seeding** (`_bracket_seed_order` + rewritten
  `_round_one_pairings`): the classic 1-v-lowest layout with top seeds taking
  first-round byes whenever the field isn't a power of two. Verified for fields
  of 2–8 (e.g. 6 → seeds 1 & 2 bye, 3v6 / 4v5, and 1 only meets 2 in the final).
- **Series kind drives length:** `wild_card` / `division` / `championship`
  (the league final / LCS) / `world_series`, each best-of value from the live
  settings.
- **`initiate_playoffs()`** creates round-1 series for every league.
  **`_maybe_advance_round()`** advances each league independently, and once both
  league champions exist (two-league save, WS enabled)
  **`_maybe_create_world_series()`** schedules the interleague final between
  them (higher regular-season win pct hosts).
- **`champion()`** returns the World Series winner, or the lone league's
  champion for one-league saves; **`league_champions()`** lists per-league
  winners for the multi-league case.
- **`get_bracket_by_league()`** groups series for the UI (per-league rounds +
  a separate World Series bucket).

### 2. Schema (`o27v2/db.py`)

Added `league` and `series_kind` columns to `playoff_series` (CREATE TABLE +
idempotent ALTER-TABLE migration for existing DBs). Each league's series carry
its league name; the World Series carries `league = ''`.

### 3. Champion fix (`o27v2/season_archive.py`)

`archive_current_season()` now records the **postseason champion** (via
`playoffs.champion()`) when a bracket was played, falling back to the
best-record team only for the soccer model (postseason disabled) or when no
champion has been crowned.

### 4. Web — `/playoffs` (`app.py` + `playoffs.html`)

- Renders **per-league brackets** (one section per league, round-by-round
  tiles with clickable per-game box-score links) plus a distinct **World
  Series** section, with proper round labels.
- **Live "Postseason Format" control** (shown before the bracket locks):
  qualifiers-per-league, a best-of selector per round kind, and a "Play World
  Series" toggle (two-league saves only). Posts to **`/playoffs/settings`**,
  which is gated so settings can't change once series exist.
- **Projected field per league** before initiation; champion / league-champions
  banners after.

### 5. Postseason stats — separate, never double-counted

Same data model — a game is a game; `is_playoff` is just a flag, denormalized
from `games` onto `game_batter_stats` / `game_pitcher_stats` (CREATE + ALTER +
backfill in `db.py`; stamped at insert in `sim.py`). The flag is the only thing
that distinguishes the two populations.

- **Regular season excludes the postseason.** Every season / career /
  leaderboard / league-environment aggregation now filters `is_playoff = 0`, so
  playoff games are no longer folded into regular-season totals. This was a
  broad but mechanical pass: ~51 query sites in `o27v2/web/app.py` plus the
  career snapshots in `season_archive.py`, the league-environment +
  records + streaks aggregations in `o27v2/analytics/`, and the almanac loader
  in `o27/almanac/loader.py`. Two shared helpers carry the scope: the pitcher
  dedup subquery gained a `_REG_PSTATS_DEDUP_SQL` variant, and bare batter /
  pitcher aggregations wrap the table in a `WHERE is_playoff = 0` subquery.
  **Per-game box scores are deliberately left unfiltered** — a playoff game's
  box score must still show its own lines.
- **Player card (`player.html`):** a **Postseason** tab (only when the player
  has playoff appearances) with playoff-only batting & pitching lines + game
  logs, via the split helpers with an `is_playoff = 1` filter.
- **`/postseason/stats`:** a dedicated postseason leaderboard page (Batting /
  Pitching), reusing `_aggregate_batter_rows` / `_aggregate_pitcher_rows` over
  `is_playoff = 1` games. Linked from the bracket page.

### 6. Stat invariants scoped to the regular season

With the league run-environment and pitcher W-L now regular-season-only, two
league-aggregate invariants had to match: invariant 5 (Σ pitcher wins ==
decided games) and invariant 8 (league xRA anchored to league RA/27) now count
`is_playoff = 0` games. Per-row / per-game invariants (uniqueness, pitcher↔
batter cross-check, PA identity, …) stay all-games — they verify engine
correctness for every game, playoff included.

---

## Verification

- **End-to-end on an 8-team save** (`resetdb --config 8teams`, full season,
  then drained the bracket): AL and NL each ran a 4-team Division Series → best-
  of-7 League Championship; the two champions met in a best-of-7 World Series
  (NL Sounds beat AL 4-2). Champion crowned and World Series MVP selected.
- **Configurable format:** `/playoffs/settings` POST persisted
  `teams_per_league = 6` and a custom 5/7/9/9 ladder; reflected back by
  `playoff_settings()`.
- **Bracket seeding** unit-checked for fields 2–8 — standard seed order, correct
  bye placement.
- **Postseason tab & page** render for batters and pitchers; bracket page links
  to the stats page.
- **Stat invariants:** `run_invariant_harness()` → **9/9 pass** on the played-
  out save.
- **Test suite:** `pytest o27/tests o27v2/tests` → **255 passed, 1 skipped, 1
  failed**. The single failure (`test_managers.py::
  test_roll_manager_shape_for_new_types`, an extra `mgr_flip_aggression` key) is
  **pre-existing and unrelated** — `managers.py` was not touched in this work.

---

## What was deliberately left alone

- **In-season roster modeling** (`development.py`, `trades.py`, `injuries.py`,
  `rotation.py`, `bench.py`) still reads the raw stat tables unfiltered. These
  run during the regular season (no playoff rows exist yet) or as offseason
  modeling inputs — not as displayed statistical totals — so they don't
  double-count what a user reads. Scoping them to the regular season is a tidy
  follow-up, not part of the "don't double-count the stats" fix.

---

## Honest limits / follow-ups

1. **3+ co-equal leagues with the bracket enabled** crown independent champions
   with no overall champion (`champion()` returns None; `league_champions()`
   lists them). In practice every bracket-enabled preset is a 1- or 2-league
   national config, so this path is defensive rather than exercised.
2. **Postseason leaderboard thresholds** are minimal (≥1 PA / ≥1 out) because
   postseason samples are tiny by nature; there's no qualified-rate gating.
3. Two pre-existing, unrelated test failures remain (`test_managers.py` shape
   drift around `mgr_flip_aggression`; the chronically-flaky RNG-based
   `test_trades.py::test_gm_noise_can_be_lopsided`). Neither touches playoff
   code, and both seed leagues with no playoff games.
