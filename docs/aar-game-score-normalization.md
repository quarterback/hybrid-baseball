# After-Action Report — Game Score Normalization, Streaks, Game-Log Top 10, Park-Adjusted +Stats, WPA

**Date completed:** 2026-05-18
**Branch:** `claude/normalize-game-scores-3AXlu`
**Commit:** `dd1bb0c`

---

## What was asked for

User opened with two questions:

> "can we normalize Game Scores so that no matter what size league I'm
> in we can compare stats? Also, are there are other data we could be
> measuring or analyzising with stats that we aren't. I'm not nhinking
> just analytiscs though I'm interested in them"

The reply explained that the existing `GSc+` already normalizes the
*mean* (each league's `base` constant auto-tunes to 50), so the missing
piece for cross-league-size comparability was the *spread* — an 8-team
league has a tight pitcher-talent stdev, a 56-team tiered league has a
much wider one, and the ratio-based `GSc+` doesn't account for that.
Recommendation: a z-score-based `GSc Index` on a 100 / 15 (IQ-style)
scale, sitting alongside `GSc+`.

Then I listed candidate non-analytical features (WPA, streaks,
manager fingerprints, weather splits, etc.). The user picked five:

> Gsc Index, Streaks (hit, perfect games, no hitters), I want a game
> score log lfor hitter/pitchers that track the top 10 of that saeson
> for each with the batting/potching line of that game and a link to
> the box score, park adjusted + eversions of wRC/wERA+ WPA leverage
> index sure

Three clarifying questions answered before coding:
- Park-adjust → add **new** `wRC+` / `wERA+` columns (not replace existing)
- Game-score log → **dedicated leaderboard page** (not per-player)
- Commit cadence → **single squash commit at the end**

---

## What shipped

Five features, ~640 net lines across code / templates / docs / tests.

### 1. GSc Index — z-score Game Score
`o27v2/web/app.py:_league_baselines` now computes `gsc_std` by running
the empirical `_pitcher_game_score()` formula over each qualifying
pitcher's per-game means (min 9 outs to qualify, same minimum the rest
of the stack uses). `_aggregate_pitcher_rows` stamps:

```
gsc_index = 100 + 15 × (pitcher_gsc_avg − league_gsc_avg) / league_gsc_std
```

Rationale: `GSc+` reads as "this pitcher's GSc divided by the league
mean × 100" — answers "how do you stack up?" but doesn't account for
how wide the field is. Two pitchers at 60 GSc in two different leagues
both show GSc+ ≈ 120, but one might be "ace of an 8-team league where
the average is 50 and the top is 65" while the other is "back-end
starter in a 56-team league where the average is 50 but the top is 75."
GSc Index disambiguates: the first scores high (because the spread is
tight); the second scores moderate (because the spread is wide).

### 2. Streaks — `o27v2/analytics/streaks.py`
- **`longest_hit_streaks(top_n)`** — consecutive games with ≥ 1 hit.
  A 0-AB game (pinch run / rest) **leaves the streak intact**; an
  0-for game with ≥ 1 AB breaks it. Active streaks (still running at
  the latest league date) flagged with `active=True` for the ★ glyph
  in the template.
- **`no_hitters_and_perfect_games()`** — single pitcher records all
  27 outs of regulation with 0 hits = no-hitter. Perfect = no-hitter
  + 0 BB + 0 HBP + 0 UER + 0 opposing ROE.

### 3. Game-log Top 10 — `_top_pitcher_outings`, `_top_batter_games`
- **Top 10 outings** (pitchers): sort all `game_pitcher_stats` rows
  by per-appearance Game Score (computed through the canonical
  `_pitcher_game_score()` helper, so a future GSc formula tweak
  flows everywhere automatically).
- **Top 10 games** (batters): new `_batter_game_score()` formula —
  hitter analogue of pitcher GSc, clamped to [0, 100]:
  ```
  bGSc = 50 + 4·1B + 7·2B + 10·3B + 13·HR + 2·BB + 2·RBI + 1.5·R
         − 1.5·K − 2·(PA − H − BB)
  ```
  Weights echo wOBA's run-value structure but expressed on a
  0–100 readout instead of a rate. 50 ≈ a quiet 1-for-4.

Each row includes the pitching/batting line and links to the game's
box score via `url_for('game_detail', game_id=...)`.

### 4. Park-adjusted wRC+ and wERA+
New `_team_park_map()` helper folds `teams.park_hr` and `park_hits`
into a single "player park factor" via the half-home / half-road
approximation: `PF = ((park_hr + park_hits)/2 + 1) / 2`.

- **wRC+** (batters) — FanGraphs-style formula:
  ```
  wRC+ = ((wOBA − lg_wOBA)/1.20 + lg_R/PA) / (lg_R/PA × PF) × 100
  ```
- **wERA+** (pitchers) — ERA+ scale:
  ```
  wERA+ = (league_wERA × PF / pitcher_wERA) × 100
  ```

Both threaded into `_aggregate_batter_rows` / `_aggregate_pitcher_rows`
behind a lazy `park_map` argument that auto-builds when callers don't
pass one.

### 5. WPA + Leverage Index — `o27v2/analytics/wpa.py`
Empirical: built per render from this league's own outcomes, no
MLB-borrowed table.

- **`build_wp_table()`** — one pass over `game_pa_log` ∪
  `games.winner_id` indexed by `(batting_is_home, outs_bucket, bases, score_diff_clipped)`.
  Coarsens via a marginal cell `(batting_is_home, score_diff)`
  when the per-cell sample is < 8.
- **WPA per PA** = `WP(state_after) − WP(state_before)`, with the
  half-ending PA anchored to `1 − WP(opponent's_opening_state)`.
- **Leverage Index** = RMS of WPA at the state, normalized so league
  mean LI = 1.0.
- Player aggregates: batter WPA = Σ over his PAs; pitcher WPA = the
  negation (his arm's gain when he prevented offense).

Surfaced on `/leaders` as four cards (batter WPA, batter LI avg,
pitcher WPA, pitcher LI avg) plus a "Biggest PAs of the Season"
panel listing the 10 largest |WPA| events with box-score links.

### Tests
`tests/test_new_stats.py` — 8 tests over a tiny in-memory fixture (3
games, 5 players). Covers perfect-game detection, hit-streak
edge cases (0-AB days don't break a streak), GSc ordering, park-factor
math, presence of the new fields after aggregation, and shape stability
of the WPA builders on tiny samples. All pass; existing 66-test
deterministic suite still green.

### Docs
`docs/stats-reference.md` updated with: bGSc, GSc Index, wRC+, wERA+,
Park Factor, Streaks / No-Hitter / Perfect Game definitions, and a
new "Win Probability & Leverage" section.

---

## What is NOT wired (be honest)

The user asked "is everything wired to the various stats pages?"
**No — not yet.** The data layer is fine: every page that displays
existing "+ stats" already calls `_aggregate_pitcher_rows` /
`_aggregate_batter_rows`, so `gsc_index`, `wera_plus`, `wrc_plus`,
`park_factor` are stamped on the row objects everywhere automatically.
The TEMPLATES, however, only surface the new fields on `/leaders`. A
follow-up pass should add columns/cards to:

- **`templates/player.html`** — player_detail. The pitcher totals card
  (around line 376) and batter totals card (around line 108) display
  `gsc_plus` / `ops_plus` / `woba_plus` today; the new siblings should
  appear next to them.
- **`templates/team.html`** — team_detail roster tables.
- **`templates/stats_browse.html`** — the "Full sortable tables" page
  has dedicated columns for ops_plus / gsc_plus already; the new
  siblings need their own columns.
- **`templates/compare.html`** — head-to-head player compare.
- **`templates/game.html`** — per-pitcher box score line.
- **`text_export.py` / `box_text.py`** — markdown exports skip the
  new fields entirely.

WPA / LI is a separate matter: those aren't stamped by the aggregators
(they require the cross-game `build_player_wpa()` pass), so any other
page that wants WPA/LI needs to call that function directly. Today
only `/leaders` does.

**Season archive** — the `season_batting_leaders` / `season_pitching_leaders`
DB tables likely don't have columns for the new fields. If the user
wants the new stats to persist across the season transition, those
tables need a migration + writer-side stamping in `season_archive.py`.

I called the work "done" after `/leaders` was working end-to-end and
the test suite was green; the user's question caught a real gap. The
fix is mechanical (add `{{ row.gsc_index }}` columns to ~5 templates)
but it's a separate commit.

---

## Punch list — resolved

All seven items shipped in the follow-up commit. Summary:

1. ✅ **Player page** — headline stat strip carries wRC+ (batters) and
   wERA+ + GSc Idx (pitchers); the sabermetric row gained wRC+; the
   pitcher Value table gained GSc Idx, wERA+, WPA, LI; the splits
   tables gained matching rows. Per-game GSc was already inline-computed
   in the pitching log (locked formula), so left as-is.
2. ✅ **Stats browse** — `gsc_index`, `wera_plus`, `wrc_plus` columns
   added to all four sortable views (batter default + all,
   pitcher advanced + all).
3. ✅ **Team page** — OPS+ / wRC+ on the batter roster table; wERA+ +
   GSc Idx on the pitcher roster table.
4. ✅ **Compare page** — wRC+ row on the batting block; wERA+ + GSc Idx
   on the pitching block.
5. ✅ **Markdown / text exports** — `text_export.py`: player_export
   gained wRC+ / WPA / LI on the batting line and wERA+ / GSc Idx /
   WPA / LI on the pitching line. leaders_export gained wRC+ / wERA+ /
   GSc Idx / WPA tables. (box_text.py per-pitcher line was already
   surfacing per-game GSc, so untouched.)
6. ✅ **Season archive** — DB schema gained 7 new columns across
   `season_batting_leaders` (`wrc_plus`, `wpa`, `li_avg`) and
   `season_pitching_leaders` (`wera_plus`, `gsc_index`, `wpa`, `li_avg`).
   Idempotent migrations added to `init_db()` so existing live DBs
   pick them up on next boot. `_snapshot_leaders()` writer now stamps
   WPA / LI on every row (one shared `build_player_wpa()` call serves
   batters + pitchers) and persists the new columns. Three new
   archive categories: `wrc_plus`, `wera_plus`, `gsc_index`, `wpa`.
   `season_detail.html` renders all of them. Pre-existing bug noted:
   `_save_pitching("xfip", ...)` sorts by `xfip` which the aggregator
   doesn't stamp (only `xra` is) — left untouched, separate fix.
7. ✅ **WPA on player page** — `player_detail()` builds the WP table
   per render (one `build_player_wpa()` call), stamps `wpa` / `li_avg`
   on both `bt_totals` and `pt_totals`. Cold-start leagues render as
   "—" via the same Jinja gates used elsewhere.

### Tests
`tests/test_template_renders.py` — 7 new tests that actually render
every modified page through the Flask test client against the
in-memory fixture and assert the new field labels appear. Plus a
schema-migration test confirming `init_db()` adds all 7 new
season-archive columns on a fresh DB. 81/81 deterministic tests green.

---

## Design notes worth flagging

- **GSc Index is league-scoped, not tier-scoped (yet).** The mean uses
  the tier-scoped baseline (the same `scoped_p` lookup that drives
  `GSc+`), but the stdev I compute in `_league_baselines` is global.
  In a 56-team tiered config that means a B-tier ace gets z-scored
  against the combined Galactic + Association stdev. If the user wants
  fully tier-comparable index numbers, plumb `gsc_std` through
  `_league_baselines_by_league` too. (One-line query change.)
- **WPA cold-start.** With `min_n=8` per cell, a fresh-sim league
  needs ~50+ games before per-state lookups start hitting. Marginal
  fallback kicks in faster but is coarser. Templates show `—` until
  then; that's by design (no MLB-borrowed crutch).
- **Park-factor approximation.** Uses team home park × half-home/half-road,
  not per-game park lookup. A player who plays a 56-team interleague
  series at a hostile park sees the same PF as someone who never leaves
  division. Cheap and good enough; the proper fix is summing each
  game's home_team park_hr/hits weighted by the player's games at that
  park.
- **bGSc weights.** I picked weights by eye to make a 1-for-4 sit near
  50 and a 3-for-4 with 2 HR land in the 90s. Could be empirically
  refit against per-game RE24 deltas later (linear-weights regression
  the same way pitcher GSc gets its coefficients), but it's not
  controlling anything mechanical — it's a readout.
