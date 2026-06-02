# After-Action Report — Streaks & Records page

**Date completed:** 2026-06-02
**Branch:** `claude/friendly-hypatia-ShSHm`

---

## TL;DR

Added a dedicated **Streaks & Records** page under the Stats menu
(`/streaks-and-records`) that pulls together three families of leaderboards
that didn't have a home:

1. **In-season streaks** — consecutive-game **home-run**, **hitting**, and
   **on-base** streaks (batters); **double-digit-strikeout start** streaks and
   **scoreless-innings** runs (pitchers). Plus the season's no-hitters /
   perfect games, re-surfaced here from `/leaders`.
2. **Single-game records** — the best individual games of the season: most
   HR / RBI / hits / total bases in a game, most strikeouts in a game, longest
   outing.
3. **All-time records** — career totals (summed across every archived season
   **plus** the season in progress) and the best single seasons ever, every
   row linking to the player card.

Everything is computed from the **per-game aggregate tables**
(`game_batter_stats`, `game_pitcher_stats`) and the durable cross-season
snapshot `player_career_lines` — the same sources the rest of the app trusts.

---

## What was asked for

> "add a streaks page somewhere for home run streaks, consecutive inning
> streaks, consecutive out streaks … aggregating a season records page for
> when I do finally sim more than one season, retaining records from past
> seasons of the top x players in major categories with their link to their
> player card, strikeout streaks, most strikeouts per game … it'd be its own
> page streaks and records, under stats."

Confirmed scope with the user up front: **all four sections** (in-season
streaks, single-game records, all-time/cross-season records, no-hitters), and
**build the cross-season aggregation fully now** even though only one season
exists today.

---

## Where things live

| Piece | File |
| --- | --- |
| New streak functions | `o27v2/analytics/streaks.py` (appended) |
| Single-game + cross-season records | `o27v2/analytics/records.py` (new) |
| Route `/streaks-and-records` | `o27v2/web/app.py` (`streaks_and_records()`) |
| Template | `o27v2/web/templates/streaks_and_records.html` (new) |
| Nav entry (Stats ▸ Streaks & Records) | `o27v2/web/templates/base.html` |
| Tests | `o27v2/tests/test_streaks_records.py` (new) |

`o27v2/analytics/streaks.py` already housed the hit-streak and
no-hitter/perfect-game logic used by `/leaders`, so the new streak engine
slots in beside it and shares its `_team_in()` / player-lookup conventions.
(Not to be confused with `o27v2/streaks.py`, the hot/cold *performance*
overlay — different module, different concern.)

## Key design decisions

- **No `game_pa_log` for streaks.** Its `hit_type` column is documented as
  NULL on non-BIP events (strikeouts, walks), so it cannot reliably classify
  K / out outcomes. Every streak and record is therefore computed from the
  per-game box lines, which are exact — the same path `/leaders` and the
  season archiver use.
- **One generic streak engine.** `_count_streaks()` takes `extends`/`breaks`
  predicates so HR, hitting, on-base, and double-digit-K streaks are one code
  path. A no-PA day is *neutral* (carries a streak without extending it),
  matching the existing hit-streak rule.
- **Scoreless-innings streaks are appearance-atomic.** Runs are charged at the
  appearance level, not per inning, so a clean outing adds its full innings and
  the first outing to allow a run ends the streak contributing zero. This
  slightly *under*-counts (a pitcher who allows a run in the 4th loses credit
  for the 3 clean innings before it) — a deliberate, documented simplification,
  since per-inning earned-run attribution isn't persisted per pitcher.
- **Cross-season records fold in the live season.** `player_career_lines` is
  only written at rollover, so `records.py` computes a live line for the
  in-progress season from the game tables and merges it with the archived
  lines, keyed by the stable `player_id` (which is what lets every record row
  link to a player card). Double-counting is guarded by
  `season_archive.get_current_archived_season_id()`. **Caveat:** live-season
  pitcher **wins** need the web layer's decision map, so W/L is credited only
  once the season is archived; K / IP / ERA / WHIP count live.
- **Scope rules.** In-season streaks and single-game records are
  **league-scoped** (like `/leaders`). All-time records are **universe-wide** —
  a player can change leagues between seasons, so "all-time" deliberately
  ignores the league filter.
- **Rate-stat floors.** Career/season rate leaders (AVG, OBP, ERA, …) use
  modest qualification floors (50 PA / 45 outs) so the boards populate in a
  short opening season without surfacing one-game flukes.

## Validation

`o27v2/tests/test_streaks_records.py` builds a synthetic temp DB (no flask
needed) and asserts the tricky cases:

- HR streak returns the **longest** run (3 games), not a later shorter one.
- Hitting streak **splits** on a hitless game; on-base streak survives it via
  walks.
- Double-digit-K streak counts **only starts** (a relief outing between starts
  is neutral; a sub-threshold start breaks it).
- Scoreless streak **accumulates innings** across two clean starts (14.0 IP).
- Single-game TB math (4 hits incl. 3 HR → 13 TB) and most-K-in-a-game.
- Career totals **sum across the archived season + the live one** (40+7 HR,
  250+54 K, `seasons == 2`), and best-single-season correctly flags the live
  season as `Current`.

All 11 pass; the existing `test_streaks.py` (14) still passes; the changed
Python modules `py_compile` clean and both templates parse under Jinja2.

**Not validated in this environment:** the live Flask render of the page —
`flask` isn't installed in the sandbox and there's no seeded DB, so the route
itself wasn't exercised end-to-end. The route is thin (it only calls the
analytics functions, which *are* tested, and passes their output to a
parse-checked template), but a first real-DB page load is the obvious thing to
eyeball.

## Follow-ups / not done

- **Consecutive batters retired** ("consecutive out streaks") was left out:
  doing it correctly needs a reliable per-PA out flag, and `game_pa_log`
  doesn't give one (Ks/BBs have NULL `hit_type`). The scoreless-innings and
  longest-outing boards cover the adjacent ground; a true "X straight retired"
  record would want a small persistence change first.
- **Walkback-run / walkback-RBI** tracking (raised mid-session) is a separate
  stat-definition question, not part of this page — captured for its own pass.
