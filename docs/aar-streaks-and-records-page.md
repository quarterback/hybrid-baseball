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

---

## Follow-on in the same session: Team Stats page + Walk-Back runs

After the records page, the user asked for (a) team-level Walk-Back runs and
(b) aggregating team stats "for the categories like players." Audit findings:

- **RISP** is an engine probability mechanic only (`_resolve_risp_pressure`),
  with **no surfaced stat**, and can't be computed exactly from `game_pa_log`
  (BIP-only — no K rows, no walk/HBP RBI). Decision: build RISP as **exact box
  columns** on `game_batter_stats` next (mirrors how 2C/`stay_*` was done),
  rather than an approximate pa_log derivation.
- **2C effectiveness** is already fully tracked + surfaced for *players*
  (2C, 2C-H, 2C-Conv%, 2C-RBI, 2C-RBI%, Δ2C, RAD on `/leaders` + glossary);
  the only gap was team-level.
- **Team stats:** the almanac computes per-team totals on static pages, but
  the **live app had no team leaderboard** — `/team/<id>` is player-by-player.

**Shipped:** `/teams/stats` (Stats ▸ Team Stats) — team batting & pitching
tables (one row per club), reusing the player aggregators
(`_aggregate_batter_rows` / `_aggregate_pitcher_rows`) on team-grouped rows so
team rate stats and the league-relative "+" stats are computed identically to
players. Adds team 2C-Conv% / 2C-RBI%, RAD, and **Walk-Back runs** (scored,
re-attributed from opponents' `wb_runs`; allowed, the team's own).

Key correctness notes:
- Team **batting** sums all phases; team **pitching** uses `_PSTATS_DEDUP_SQL`
  (collapses multi-phase appearances) — each matches its player-side
  convention, so a small leaguewide runs-scored vs runs-allowed gap is
  expected (super-inning lines the dedup drops), not a new bug.
- **Walk-Back runs** scored and allowed are both sourced from the same
  all-phase `team_walkback_runs()` so the two sides reconcile exactly
  (verified 139 = 139 over a 60-game sample), rather than mixing the deduped
  pitching total.

**Validated end-to-end this time:** installed flask, seeded a real 30-team DB
(`manage.py initdb` + `sim 60`), and hit `/streaks-and-records` and
`/teams/stats` (HTML + JSON) via the Flask test client — all 200, numbers
sane (caught and fixed a real bug: the records-page card macros were called
without their value `key`, which only surfaces at render time). Rate-stat
leaderboards (career AVG/OPS, etc.) are empty at 60 games because the 50-PA /
45-out floors aren't met yet — expected; they fill in once a real season is
simmed.

## Follow-on: RISP (exact box columns) — shipped

Built the agreed exact-RISP layer end to end. RISP = a runner on 2B and/or 3B
at the PA's start.

- **Stat object** (`o27/stats/batter.py`): added
  `risp_pa/ab/h/2b/3b/hr/bb/hbp/rbi` to `BatterStats`.
- **Engine credit** (`o27/render/render.py:_update_stats`): snapshot the nine
  batting counters at the top, compute the RISP flag once from
  `ctx["bases_list"]`, and at the end mirror the event's deltas into the
  `risp_*` subset. Single-point and exact by construction — no need to touch
  the ~10 scattered outcome branches, and the lone early `return` (the CS
  branch) credits none of these counters so it loses nothing. Also threaded
  through `_stat_delta` (per-phase deltas) and the team-line aggregation.
- **Persistence** (`o27v2/sim.py`): extract + both `game_batter_stats` INSERT
  sites; **schema + migration** (`o27v2/db.py`) mirroring the `stay_*`/`rad_*`
  pattern (`ALTER TABLE … ADD COLUMN … DEFAULT 0`).
- **Rates** (`_aggregate_batter_rows`): RISP slash line computed
  **PA-denominated** (RISP-AVG = risp_h/risp_pa, RISP-OBP, RISP-SLG, RISP-OPS)
  plus **RISP-Conv** = RBI per RISP PA. Crucial O27 nuance: per-AB RISP rates
  are *unreliable* because a single AB can credit multiple hits via stays
  (`risp_h` can exceed `risp_ab`) — so PA-denomination, matching PAVG, is the
  right call. Available to both player and team rows since both run through the
  shared aggregator.
- **Display**: RISP cards on `/leaders` ("Batting · Clutch (RISP)"), RISP
  columns on `/teams/stats`, and a glossary section (cards auto-link via
  `has_glossary`).

**Validation:** re-simmed a fresh 120-game DB; RISP captured correctly and
invariant-clean — every `risp_X <= X`, RISP RBI ≈ 87% of all RBI (sensible:
most RBI need a runner on 2B/3B), RISP-AVG > overall AVG (the RISP-pressure
model lifting contact). All pages render 200 (HTML + JSON). Engine + render
suites pass (102 tests); 10/11 stat invariants pass.

**Pre-existing, not mine:** invariant 6 (`PA == AB+BB+HBP`) fails on freshly
simmed DBs — confirmed identical on a pre-RISP DB. O27 sac bunts make PA exceed
AB+BB+HBP and `sh` isn't a persisted `game_batter_stats` column, so the
identity can't close. Untouched by this work; flagged for a separate pass.

## Follow-on: Bunting overhaul — shipped

Replaced the thin single-`sac_bunt` system (one speed-driven outcome roll, no
defense, `sh` silently dropped, nothing in the UI) with a full four-type
small-ball system.

- **New `bunt` player attribute** (bat control, distinct from foot speed).
  Schema column on `players`; existing rosters seeded `0.6·contact + 0.4·speed`
  via migration; `generate_players` derives it for new players;
  `_db_team_to_engine` threads it onto the engine `Player`.
- **Four bunt types** (`o27/engine/manager.should_bunt`, rewritten): **sacrifice**
  (1B/2B force), **drag / bunt-for-hit** (fast + low power, great vs the
  infield shift), and the **suicide** & **safety squeeze** (runner on 3B).
  Type is chosen by base state, gated by outs / score margin / `mgr_run_game` /
  `mgr_leverage_aware` / batter power, and executed against bunt skill, speed,
  and a new **pitcher difficulty** factor (stuff + command).
- **Richer outcomes** (`o27/engine/pa.py`): clean sac, bunt single, **lead
  runner forced out** (poor bunters, single-runner FC), popup, productive out,
  squeeze run-scores (± beaten out), **suicide miss → runner hung out at
  home**, and safety-squeeze hold. Each reduces to (new bases, runs, outs) via
  `wild_pitch_advance` + `_record_out`.
- **Stats** (`o27/stats/batter.py`, render credit, `_stat_delta`, team-line
  agg, `sim.py` extract + both INSERTs, `db.py` schema + migration): **`sh` is
  finally persisted**, plus `bunt_att`, `bunt_hits`, `sqz`, `sqz_rbi`. Surfaced
  as a "Small Ball" card cluster on `/leaders`, columns on `/teams/stats`, and a
  glossary section. New config block in `o27/config.py` for every rate.
- **Invariant 6 fixed for real:** with `sh` persisted, the PA identity is now
  `PA == AB + BB + HBP + SH`; the invariant test was updated to match and
  **now passes** (it failed on every fresh sim before this work).

**Validation:** simmed fresh 150-game DBs; all four bunt types fire, the `bunt`
attribute varies 21–80, the bunt mix is sane (~1.5 bunts/game after dialing
`SQUEEZE_BASE_PROB` down so squeezes stay special), **all 11 stat invariants
pass** (was 10/11), engine + bunt unit suites pass (101 + new
`o27/tests/test_bunting.py`), and `/leaders` `/teams/stats` `/glossary` render
200. Needs a resim to populate (box-stat addition).

Minor known gaps: secondary roster paths (youth/college call-ups, injury
replacements) insert `bunt` at the column default (50) rather than deriving it;
OR attribution on the two runner-out outcomes (`lead_out`, `squeeze_miss`) lands
on the batter via the renderer's out-reconciliation tail (team out totals and
scores are correct — only per-batter OR is slightly off on those rare plays).

## Still open

- RISP + bunting on the **player card** and **stats browser** sortable table
  (capture + aggregation already feed them; only column wiring remains).
- Box-score line for bunting (SH/bunt-hit shown in leaders/team, not yet in the
  per-game box).
- Deriving `bunt` in the youth/college/injury insert paths.
