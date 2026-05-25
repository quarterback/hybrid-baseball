# After-Action Report — fast-sim stat repair (wERA archive) + outs-based DIPS leaderboards

**Date completed:** 2026-05-25
**Branch:** `claude/sim-ahead-stats-analytics-8eqD0`
**Predecessor:** `aar-ui-retheme-nav-and-web-modularization.md`

---

## Context

The session opened with screenshots of the archived-season leaderboards
(wRC+ / WPA / wERA / wERA+ · Top 10) where every value was degenerate:

- **wRC+** — identical `-23` for all ten batters.
- **WPA** — `+0.00` for every batter and pitcher.
- **wERA** — `0.00` for everyone; **wERA+** — `100` for everyone.

The user couldn't tell whether the stats were "always broken or a specific
issue with the fast sim context," noted that an ERA+ of 100 (and WPA) told
them nothing, and asked for **more useful, front-office-grade analytics** —
specifically flagging that **total outs as an IP proxy** would be an easy
base for new rate stats.

### Diagnosis (three distinct causes, not one)

Confirmed by reading the code and reproducing each failure against a
throwaway DB. The headline finding: **a normally-simmed current season
viewed live computes all four correctly.** The screenshots are the
*fast-sim + archived-season* combination.

1. **WPA = +0.00 — fast-sim-specific.** `simulate_game(detail="lite")`
   (the mode behind multi-season / pre-sim "sim ahead", `o27v2/sim.py:1720`,
   `if pa_log and not lite`) writes box scores but **skips the per-PA event
   log** `game_pa_log`. `build_player_wpa()` walks that log, so an empty log
   → 0.00 for everyone.

2. **wRC+ = −23 (constant) — fast-sim-specific, one step removed.** The wOBA
   weights aren't hardcoded; they're *empirically derived from* `game_pa_log`
   (`analytics/linear_weights.py:derive_linear_weights` → `_iter_events_full`).
   Empty log → `n_events == 0` → every weight defaults to 0 → **every
   batter's wOBA = 0** → wRC+ collapses to one league-determined constant.
   Reproduced: two hitters with OPS 2.800 vs 0.500 both got wOBA 0.000 and
   identical wRC+.

3. **wERA = 0.00 / wERA+ = 100 — an archive bug, *not* fast-sim.** wERA reads
   the arc-bucketed earned-run columns (`er_arc1/2/3`), and the engine writes
   those **even in lite mode** (verified: a 3-game run produced
   `er_arc = [25,24,8]`). The real fault was in the season-archive snapshot:
   `season_archive._snapshot_leaders` **omitted the arc columns from its
   pitching `SELECT`**, so `_aggregate_pitcher_rows` read every arc as 0 →
   wERA 0 → wERA+ fell back to 100. The live `/leaders` page (which selects
   the arc columns) worked the whole time. Reproduced: the same pitcher row
   gives `werra=4.000` with arc columns, `werra=0.000` without.

### Design read

WPA and the pa_log-derived "+" metrics are the *least* salvageable here —
reviving them in fast sim means re-simulating every PA, which defeats the
point of fast sim (the user explicitly declined that option). The durable
answer is **box-score analytics keyed off total outs** (IP = outs/3), which
survive lite mode untouched. `WHIP / K/9 / BB/9 / HR/9 / ERA / RA/27` already
existed and were computed exactly that way; the gaps were **FIP and K/BB**,
plus the fact that the **archived boards surfaced the broken stats** instead
of the robust ones.

## What shipped

Scope was confirmed with the user before implementing (fix the wERA archive
bug; add FIP + K/BB; swap the broken archive columns; and — in a follow-up
turn — substitute OPS+ for wRC+ on the archive).

1. **Fixed the archived wERA bug.** `season_archive._snapshot_leaders`'s
   pitching aggregation now selects the arc-bucketed columns
   (`er_arc*/k_arc*/fo_arc*/bf_arc*`) plus the true-outcome / hit-type columns
   (`batters_faced`, `hbp_allowed`, `unearned_runs`, `fo_induced`, `pitches`,
   `singles/doubles/triples_allowed`, `is_starter`) that
   `_aggregate_pitcher_rows` consumes. Archived wERA / wERA+ / xRA / Decay /
   GSc now match the live page. (Verified: 2.01 vs 5.99, not 0.00/0.00.)

2. **Added FIP and K/BB to the pitcher aggregate** (`o27v2/web/app.py`,
   `_aggregate_pitcher_rows`). FIP is the classic DIPS estimator on the ERA
   scale — `(13·HR + 3·(BB+HBP) − 2·K)/IP + C` — with a new
   `_league_fip_constant()` helper that anchors league FIP to league ERA
   (IP = outs/3; a 27-out frame = 9 IP, so the per-9 scale lines up with the
   existing `era`/`k9` columns). K/BB is `K/BB`, left `None` when the pitcher
   has issued no walks. Both use only box-score counters + outs, so they need
   no arc data and no `game_pa_log` and stay correct under fast sim.

3. **Swapped the dead boards on the archived season page.** Replaced the
   `+0.00` pitching WPA board with **FIP / K/BB / WHIP / K/9** boards, and
   substituted **OPS+** for **wRC+** on the batting side (OPS+ is OPS-relative,
   box-score-only, so it ranks correctly in fast and full archives alike;
   reproduced 157 vs 28 where wRC+ was a flat constant). Dropped both WPA
   boards. New persisted columns `fip_dips / kbb / whip_v / k9` (pitching) and
   `ops_plus` (batting) were added to `season_pitching_leaders` /
   `season_batting_leaders` via the existing idempotent `ALTER` migration
   pattern + the `CREATE TABLE` defaults. Legacy archives with no new-category
   rows fall back to their original wRC+ table.

4. **Surfaced FIP + K/BB on the live `/leaders` page** (`leaders.html`) — FIP
   alongside the wERA/xRA run-estimator trio, K/BB with the command rates.
   The `card()` macro already filters `None` via `selectattr(key,'ne',None)`,
   so walk-less pitchers are simply omitted from the K/BB board (no crash).

## Decisions & tradeoffs

- **Diagnose, then fix the *right* layer.** wERA looked like the same
  fast-sim problem as WPA/wRC+, but it was an independent archive-query
  omission. Confirming the arc columns survive lite (engine repro) is what
  separated "fix the snapshot SQL" from the much larger "re-architect fast
  sim."
- **Box-score-robust stats over reviving pa_log metrics.** FIP/K/BB/WHIP/K9
  are immune to the lite-mode data gap by construction. Chosen over making
  lite sim write a minimal `game_pa_log` (heavier, slows the very mode that
  exists to be fast).
- **OPS+ substitutes for wRC+ on the archive only.** wRC+ stays on the live
  page (correct there). On the archive it's box-score OPS+ — strictly less
  sophisticated than wRC+, but it *works* in the fast-sim archives the user
  actually generates, where wRC+ is noise. Legacy full-detail archives keep
  their real wRC+ board via a template `if`.
- **New, clearly-named archive columns instead of reusing the legacy ones.**
  `season_pitching_leaders` already overloads `era`/`fip`/`whip` to hold
  wERA/xRA/GSc-avg; adding `fip_dips`/`kbb`/`whip_v`/`k9` (rather than piling
  more meanings onto the overloaded columns) keeps the new boards
  unambiguous.
- **`_league_fip_constant()` as its own helper.** One extra league query per
  `_aggregate_pitcher_rows` call (same magnitude as the existing
  `_league_werra_consts`), in exchange for not changing that function's
  return-tuple arity and the one place that unpacks it.

## Verification

No browser and no seeded DB in this environment, so verification was via
throwaway DBs (`O27V2_DB_PATH` + `init_db`), the Flask test client, and the
stat suite (flask/pytest installed ad hoc):

- **Engine repro:** ran 3 games through the core engine — `er_arc` populated
  (`[25,24,8]`), proving the arc data exists independent of lite mode.
- **Archive repro:** same pitcher row aggregates to `werra=4.000` with the arc
  columns vs `werra=0.000` without — the exact bug, and its fix.
- **End-to-end snapshot:** built a DB with arc + box-score data, ran
  `_snapshot_leaders`, read back `season_pitching_leaders` — wERA (2.01/5.99),
  wERA+ (199/67), FIP (0.83/7.17), K/BB (11.0/0.60), WHIP, K/9 all populated
  and correctly ranked; OPS+ board 157 vs 28 where wRC+ was constant.
- **Render:** `/leaders` and `/seasons/1` both return 200; confirmed the FIP
  and K/BB cards on `/leaders`, the FIP/K/BB/WHIP/K9 + OPS+ tables on the
  archive page, and that the WPA / wRC+ tables are gone.
- `tests/test_new_stats.py` — **8 passed** (it exercises
  `_aggregate_pitcher_rows` / `_aggregate_batter_rows` and the wERA+ path).
  `tests/test_stat_invariants.py` errors with `no such table: games` — the
  same pre-existing seeded-DB environment issue noted in the predecessor AAR,
  unrelated to these changes.

## Follow-ups (open)

- **wRC+ / WPA in fast sim remain dead by design.** If they're wanted in lite
  seasons, the cheapest route is a *minimal* `game_pa_log` write under lite
  (just the columns `derive_linear_weights` / WPA need) rather than the full
  per-PA narrative — a deliberate, measured slowdown of fast sim. Not done;
  the user preferred the box-score-robust substitutes.
- **Glossary entries** for `fip` / `kbb` aren't added; the `card()` macro only
  links to the glossary when `has_glossary(key)` is true, so the new cards
  render fine without links. Worth a short `glossary.py` pass for parity with
  the other stats.
- **xRA is O27's native FIP analog.** FIP and xRA now sit side by side on both
  pages; if that reads as redundant to players, consider folding FIP into the
  XO-crossover block (MLB-readable) and leaving xRA as the native headline.
- **Career / player-page lines** already select the arc columns, so career
  wERA was unaffected — but a sweep to confirm every archive-adjacent
  aggregation (HoF, team pages) selects the full arc set would close the loop
  on this class of "snapshot SELECT drifted from the aggregator" bug.
