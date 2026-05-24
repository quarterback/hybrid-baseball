# After-Action Report — Career Leaderboard Placement + Stat-Invariant Repair

**Date completed:** 2026-05-24
**Branch:** `claude/youth-teams-league-history-nYDmy`
**Commits:** `3011c91` (career → almanac), `ece4271` (invariant 8 → xRA),
`2814300` (phase-outs cap + W-bound).

**Outcome:** 4 of the 6 originally-failing invariants fixed and committed
(#1, #4, #5, #8). #2/#3 (batter↔pitcher out reconciliation) and #9 (walk-back
runs ≤ unearned) remain open — both root-caused to deep engine attribution
bugs, with fix attempts that were tried and reverted (documented below) and a
concrete next-step plan for each.

---

## Intent

Two threads ran back-to-back:

1. **Career leaderboard placement.** A multi-season career-stats leaderboard
   had been built in the web app; the user chose the **almanac** as its home.
   Move it there, remove the interim web-app page, and prove it with real data.
2. **"Fix the bugs and errors only."** The `tests/test_stat_invariants.py`
   harness was failing 6 of its checks against a multi-season sim. Turn it green.

---

## Thread 1 — Career leaderboard → almanac (delivered)

The career data layer (per-player season-line snapshot tables
`season_player_batting` / `season_player_pitching`, written at archive time)
was already in place and is the right home for cross-season totals — raw game
logs are wiped each season, so career accumulation must read the snapshots.

Moved the presentation into the almanac so it lives with the rest of the
stat site:

- `loader.py` loads the two snapshot tables (via the existing `_try_query`
  graceful-degradation path).
- `compute.py` aggregates them per player into ranked career batting/pitching
  leaderboards (`_career_leaderboards`), attached to `Views.career`. Rate stats
  gate on a career minimum (50 PA / 60 outs).
- New **Career** page renders in both the static export (`career.html`) and
  the live blueprint (`/career.html`), with a nav entry in `_base.html.j2`.
- Removed the interim web-app `/seasons/career` route, helpers, template, and
  the seasons-page link.

Crucially, the almanac computes career data from its **own loaded dataset**,
not the global DB connection — so it is correct whether built statically from
an arbitrary DB path or served live.

**Validation.** Ran a real 5-season carry-forward history (aging + offseason
development between seasons). Boards populated correctly: counting leaders
showed `seasons=5` with totals ≈ 5× a single season, confirming roster
continuity and snapshot accumulation. Two honest caveats surfaced and were
reported: (a) totals only accrue from seasons archived *after* the snapshot
tables existed; (b) the career rate-stat minimums are low enough that a
small-sample season can top a rate board (a tuning knob, not a bug).

---

## Thread 2 — Stat invariants

Six failing checks. The decisive move was reading the test logic and querying
**real game rows**, not trusting the assertion messages. That split the
failures cleanly into stale tests vs real engine bugs.

### Stale tests (the test drifted from production)

- **#8 FIP-anchored.** Asserted on `r.get("xfip")`, but the pitcher aggregator
  no longer emits `xfip` — the expected-runs metric was replaced by **xRA**
  (`_aggregate_pitcher_rows` sets `p["xra"] = raw_xra * xra_norm`, anchored to
  league wERA by construction). The dead key read `0.0`, so league xFIP came
  out `0.0000` on every populated DB. Fix: check `xra`.
- **#5 W ≤ G.** The test re-derived W with a "max outs per team-game" rule and
  asserted equality with production `_pitcher_wl_map` — which uses an SP-outs
  threshold + most-effective-reliever rule. The local copy had drifted. Fix:
  derive W straight from `_pitcher_wl_map` (authoritative, self-maintaining)
  and keep the `W ≤ G` and `ΣW == decided-games` checks.

### Real engine bug — phase-outs cap (#1, #4)

Proven unambiguous by a single query: game 9875 was a plain regulation game
(`super_inning=0`, zero seconds used, 24–14) yet a pitching team recorded
**28 outs in phase 0** across four legitimate rows. Regulation caps at 27.

Root cause: in the "27-out continuous" model, `is_half_over()` is evaluated
only *between* PAs. A single multi-out PA (double/triple play, stay-out +
runner-out) called `_record_out` twice with no cap check between them, so a DP
beginning at out 26 recorded the 27th **and** 28th out. Fix: a single
authoritative `GameState.out_cap()` (reused by `is_half_over`), and
`_record_out` refuses to record once the cap is reached. Invariants 1 and 4
went green; engine unit tests unaffected.

### Real bug — batter/pitcher out reconciliation (#2, #3) — OPEN

Elusive; worth documenting the *method* and the dead ends, because two
hypotheses and one fix all failed:

- **Wrong hypothesis A: pitcher-side spell drop.** `_close_spell` skips a spell
  with `pitcher_spell_count == 0`, which would drop a reliever's pickoff/CS out.
  Plausible, but a re-sim showed the aggregate gap unchanged and it added a few
  reverse mismatches. Reverted.
- **Single-process trace narrowed it to the batter side.** Wrapping
  `_record_out` to count engine outs per pitching team and comparing to the DB
  pitcher rows showed **zero** pitcher-side leak — engine outs == pitcher
  `outs_recorded` exactly. A per-team ground-truth dump confirmed the **batter**
  ledger over-counts: the renderer's raw cumulative OR (== extracted ==
  `game_batter_stats`) is 28–30 for a half the engine recorded as 27 (or 24–25
  on a walk-off). So the renderer charges batter-outs the engine never recorded.
- **Failed fix: two-way reconciliation in `render.py`.** The renderer tops the
  batter OR up to `state.outs` via a "leftover" charge that only ever *adds*; I
  made it also roll back when structured charges exceed the engine delta. On a
  full re-sim the gap was **unchanged** (≈3060 mismatched team-halves), so the
  over-charge is *not* in the leftover block — it's elsewhere in the renderer's
  per-event structured charging. Per-event instrumentation gave contradictory
  signals (events appeared to match the engine delta yet the cumulative
  diverged), which means the over-charge is subtle and the baseline used by
  both the trace and the production code (`ctx["outs"]`) needs auditing.
  Reverted the change rather than ship a no-op.

**Status: root-caused to the renderer's batter-out attribution, not yet fixed.**
Next attempt should instrument `_update_stats` with an *independent* engine-out
counter (not `ctx["outs"]`) to find the specific event class that charges a
batter-out with no matching engine out, then fix that branch — likely a path
that emits an out on a play the engine treats as safe (or a multi-out play
whose nominal out count exceeds what the engine recorded under the out-cap).

### Real bug — walk-back run classification (#9) — OPEN

The invariant: per (game, team), `wb_runs ≤ unearned_runs` (a Walk-Back run is
unearned by rule). Failing on ~6–7 (game, team) groups per season.

- **Attempt: persistence-boundary clamp** in `_extract_pitcher_stats`
  (`o27v2/sim.py`): store `unearned_runs = min(runs_allowed, max(unearned_runs,
  wb_runs))` and recompute ER (plus trim the `er_arc` buckets so they keep
  summing to ER, keeping invariant 8 anchored). This took the violations from
  ~7 down to **1** per season and is correct for the common case — but it's
  bounded by the *pitcher's own* `runs_allowed`, so it can't cover the last
  case. Reverted, because it changes ER system-wide for a result that's still
  red.
- **Root cause of the remaining case.** game 1472, team 21: pitcher pid 991 has
  `wb_runs=1` but `runs_allowed=0`. The team's pitcher `runs_allowed` sums to
  21, exactly matching the opponent's 21 runs — so the bonus run *is* counted,
  just charged to a **different pitcher** than the one credited the `wb_runs`.
  The Walk-Back run's `wb_runs` tick and its `runs_allowed` charge land on two
  different spells of the same team.

**Status: root-caused to cross-pitcher attribution in the engine's Walk-Back
scoring path, not fixed.** The clean fix is in the engine: when
`_reconcile_walk_back` ticks `wb_runs` for a bonus runner who scored, the run
must be charged to (and demoted on) the *same* pitcher. That requires tracing
why the bonus run's `_score_run` charge and the reconcile tick diverge across a
spell boundary — a focused walk through `baserunning.py`/`pa.py` Walk-Back
scoring. (An extraction-level team-aggregate reconciliation — bump `unearned`
on any same-team spell with `runs > unearned` headroom — would make the
invariant green but only approximates per-pitcher attribution, so it was not
pursued.)

---

## What went well

- **Query the data, don't trust the message.** Game 9875 (real engine bug) and
  the "27-out continuous + walk-off" model (legitimate, *not* a bug) were both
  settled by looking at actual rows.
- **Single source of truth.** `out_cap()` replaced duplicated thresholds rather
  than adding a parallel one.
- **Cheap trace beats expensive sim.** Narrowing #2/#3 to the batter side (and
  ruling out a pitcher-side leak) came from one-process `_record_out` and
  per-team ground-truth traces, not multi-minute re-sims.
- **Held commits until verified.** Engine changes that touch every box score
  were not committed before a fresh-sim harness run confirmed them.

## What cost time

- Two full re-sim cycles spent on hypotheses (BF==0 spell drop) that a cheap
  single-game trace would have ruled out first.
- A trace run that reseeded the DB and landed on a non-mismatch game — should
  have pinned the failing `(game_id, seed)` before instrumenting.
- Initially conflating #2/#3 with the cap bug; they share a root theme (two
  independent out-counting paths never strictly reconciled to `state.outs`) but
  are separate problems — the cap leak on the engine side (fixed) vs the
  batter-out over-charge on the renderer side (still open).
- Several re-sim cycles chasing #9 fix variants (per-spell clamp, then the
  `er_arc` trim) that ultimately couldn't cover a cross-pitcher attribution
  case — and reverted. A per-(game,team) ground-truth dump up front would have
  shown the wb-run/run split across spells before the clamp was written.

## Standing lessons for this codebase

1. **Trace one game before you sim a season.** A monkeypatched single-game
   trace costs seconds; a verification sim costs minutes. Use the cheap signal
   to choose the hypothesis worth the expensive confirmation.
2. **`state.outs` is the spine.** Both stat ledgers (renderer batter OR and
   engine spell pitcher outs) must reconcile to it. New out-affecting code
   should be checked against `state.outs`, not against either ledger alone.
3. **Half the "engine failures" were test rot.** When an invariant trips,
   check whether the *test* still matches production (`xfip`→`xra`, local W
   re-derivation vs `_pitcher_wl_map`) before assuming the engine regressed.
