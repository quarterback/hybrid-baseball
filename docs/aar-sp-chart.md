# After-Action Report — Daily SP Chart

**Date completed:** 2026-06-30
**Branch:** `claude/sp-rankings-june-30-gnuw9f`

---

## TL;DR

The user pasted FanGraphs' *Starting Pitcher Chart – June 30th, 2026* (a daily
fantasy streaming chart for real MLB starters) on a branch named
`sp-rankings-june-30`, with no instruction beyond a later "aar this after."
O27's players are **entirely fictional**, so the real chart can't be ingested —
it served as a **format template**.

I built an analogous in-app **Daily SP Chart** at `/sp-chart`: for a game day it
lists each game's two *projected* starters, ranked into a FanGraphs-style table —
season IP/ERA/WHIP/K%/K-BB%, the opponent's team-offense rank, and a
shallow/medium/deep (10/12/15-team) start recommendation shown as `x` marks.

The feature is **pure reuse** — no engine changes, no schema changes, no new
tables. Every number comes from helpers the leaderboards already use.

---

## What was asked for

A starting-pitcher rankings page in the spirit of the pasted chart. Because the
interactive clarifying-question tool failed twice with a permission-stream error
(not a user denial), I proceeded on the most likely reading of the branch name +
pasted chart, surfacing the assumptions in the plan: build a new Daily SP Chart
page, default it to the next scheduled day, and include three nested start tiers.

## What I built

- **`/sp-chart` route + `_build_sp_chart` / `_next_unplayed_date` helpers**
  (`o27v2/web/app.py`, inserted after the schedule route).
- **`templates/sp_chart.html`** — the ranked chart + a "how to read it" note.
- **Nav link** in the Games dropdown (`templates/base.html`: added `sp_chart` to
  `g_games` and a dropdown `<li>` beside Schedule).

## How it works (reuse map)

- **Projected starter** — `projected_starter(team_id, game_date)`
  (`o27v2/sim.py:1629`), the same selection the live sim uses. One call per
  distinct club on the slate, memoized. Played days instead read the actual
  game-starter from `game_pitcher_stats` (`is_starter=1, phase=0`).
- **Season pitcher stats** — the exact column set + aggregator `/leaders` uses
  (`_aggregate_pitcher_rows`, `app.py:2767`), filtered to the charted arms. Gives
  `k_pct`, `k_minus_bb_pct`, `xra`; IP/ERA/WHIP derived from the same counters as
  `fantasy/data._season_statline`.
- **Opponent offense** — every team in scope ranked by team `woba` via
  `_aggregate_batter_rows` (the `/teams/stats` pattern); rank shown as `N/30`,
  plus opponent team `k_pct`.
- **Value score** (transparent): `skill = 100·K-BB% − 8·(xRA − league_RA27) −
  10·(WHIP − 1.30)`; `matchup = (100 − opp_wOBA+)·0.15`; `value = skill +
  matchup`. Rank by value desc; tiers are rank-percentile cutoffs (top 40/60/85%)
  so **deep ⊇ medium ⊇ shallow** by construction. Starters with no season log yet
  sort last with no marks.
- **Caching** — wrapped in `@_html_cache`, keyed on query string and invalidated
  by the data fingerprint, so the per-team `projected_starter` work runs at most
  once per simmed game.

## Validation

- `manage.py initdb` (30-team config) + `sim 80`, then loaded `/sp-chart` via the
  Flask test client: **HTTP 200**, 30 starters (15 games × 2) on the next
  unplayed day, sensible ordering (high K-BB% + easy matchup on top).
- Confirmed the tier-nesting invariant holds on real data (scored 21 → 9/13/18
  shallow/medium/deep, every shallow pick also medium and deep).
- Confirmed a past (fully-played) date renders actual starters, and the
  `?league=` filter and `?date=` stepping work.
- `pytest o27/tests o27v2/tests/test_rotation.py` → **222 passed** (additive
  change; no engine/rotation regressions).
- Flask was absent in the sandbox (per CLAUDE.md); installed it locally only to
  run the test client — no dependency was added to the project.

## What I did NOT change / known limitations

- **No engine, schema, or stat-math changes.** The chart only reads persisted
  data through existing aggregators.
- **Opponent quality is team-overall, not split by the pitcher's handedness.**
  FanGraphs ranks "opp wOBA vs the pitcher's hand." O27 stores `bats`/`throws`
  but persists no platoon split on aggregate rows (`game_pa_log` carries
  `pitcher_id` but is heavy/prunable). A true vs-RHP/vs-LHP split is the natural
  follow-up; the column is labeled team-overall and the page note says so.
- **Season stats on a historical date are season-to-date totals**, not "as of
  that date" — consistent with how the rest of the app surfaces season stats.
- **Value-score scaling is rough in tiny samples** (early-season 2–4 IP lines
  inflate K-BB%-driven value); it reads as intended at mid-season sample sizes,
  which is when a streaming chart is actually used.
