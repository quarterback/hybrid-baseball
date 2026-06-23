# After-Action Report — Unifying RRR with the cricket-stat ports (PR #274)

**Date:** 2026-06-23
**Branch:** `claude/adoring-meitner-7wsd8p`
**Status:** Shipped. Merged PR #274 (xO/DO%/PAI) onto the RRR branch, reconciled
the duplicated RRR/3O math, and added a Chase-BA player/leaderboard split. All
green on a fresh flag-on sim DB.

---

## 1. Why this happened

While the RRR work (analytics + manager AI) was in flight on this branch, a
parallel Codex effort — **PR #274**, branch `codex/explore-adding-cricket-stats-metrics`
→ base `cricket-stats` — added three cricket-stat ports and, unlike the RRR
work, wired them onto **player cards and leaderboards**:

- **xO** (Expected Outs) and **O−xO** for pitchers,
- **DO%** (Dead Out %) for pitchers + **DOA%** (Dead Out Avoidance) for hitters,
- **PAI** (Pressure-Adjusted Impact) + **TRR+** for hitters.

The owner asked for three things: **(1) reconcile** the overlap, **(2) review**
#274, **(3) add** the RRR player/leaderboard split I'd proposed.

The critical overlap: PR #274's `pressure.py:_pressure_multiplier` **re-implemented
RRR/3O inline** (`runs_to_lead / (remaining_outs/3.0)`) — a second copy of the
metric this branch had just made canonical in `o27/stats/team.py`.

## 2. Review of PR #274 (findings)

- **Mergeable and well-scoped** (live `o27v2/` only, no schema migration; pure vs
  support-xO kept separate; DP-as-two-dead-outs handled per spec).
- **Never run against a populated DB** — validation was `py_compile` + import +
  `test_linear_weights` only. Its queries reference `game_pitcher_stats.fo_induced`
  / `game_batter_stats.fo`; a wrong name would 500 `/leaders` and `/stats`. → I
  ran the full render smoke (below); the pages return 200, so this is now cleared.
- **Duplicated RRR/3O** in `_pressure_multiplier` (the reconcile target).
- **xO league bins sum `outs_after − outs_before`**, so DP buckets contribute 2 →
  some EV/LA buckets exceed an out-rate of 1.0, mildly inflating grounder xO.
- **Per-request cost:** `/leaders` and `/stats` now each call PAI + xO + DO% +
  DOA (full `game_pa_log` scans + an RE-table build) at `min_pa=1` over all
  players — worth a cache/`min_pa` floor on big DBs. (Left as-is; flagged.)
- **Scope creep:** it bundles an unrelated **consecutive-pitch-count fatigue**
  engine change (`prob.py`/`config.py` + a test). Clean and tested, but it alters
  sim outcomes and is unrelated to stats.

## 3. What changed on this branch

- **Merged** `origin/codex/explore-adding-cricket-stats-metrics` (conflict-free —
  the two efforts touched different regions of `config.py`/`prob.py`/`app.py`).
- **Reconciled the duplication** — `o27v2/analytics/pressure.py:_pressure_multiplier`
  now calls `o27.stats.team.required_run_rate_3o`. Verified numerically identical
  to the old inline formula across every valid regulation state
  (`score_diff ∈ [-15,0]`, `outs ∈ [0,26]`); locked by
  `o27/tests/test_pressure_reconcile.py`.
- **Added the RRR split** — `build_chase_split_table` (in `pressure.py`):
  per-batter **Chase BA**, the hit rate on balls in play **while trailing** (the
  chase), with `chase_pa` sample size. Honest naming: `game_pa_log` logs only
  contact events (no BB/K), so it's a contact average, not full PAVG — the
  legible companion to PAI/TRR+ (which weight run value by RRR/3O continuously).
  Wired into `/leaders` (Pressure section card) and the **O27 Index** batter
  metrics → sortable on `/o27i/leaders` and surfaced on player pages, matching
  #274's placement so the cricket stats sit together.

## 4. Validation

- `pytest o27/tests` — **200 passed** (RRR + PR #274 fatigue + reconcile tests),
  on a flag-on engine.
- `pytest o27/tests/test_pressure_reconcile.py` — PAI weight ≡ canonical RRR/3O.
- Fresh DB (`initdb` + `sim 60`): render smoke of `/leaders`, `/stats?side=bat`,
  `/stats?side=pit`, `/o27i`, `/o27i/leaders` (+`?sort=chase_ba`), and pitcher &
  batter `/player/<id>/o27i` → all **200**; Chase BA present and sortable; #274's
  xO/DO%/PAI render against real data without the feared column 500s.
- `pytest tests/test_stat_invariants.py` — 13 passed.

## 5. Known limitations / not done

- **Chase BA is contact-only** (no BB/K in `game_pa_log`); labeled/tooltipped as
  such. Trailing (`score_diff_before < 0`) is the chase trigger; continuous RRR/3O
  severity lives in PAI.
- **Did not split** the bundled pitch-count fatigue change back out, refit xO's
  >1.0 bucket rates, or add caching to the per-request leaderboard analytics —
  all flagged above for follow-up.
- **Branch coordination is the owner's call:** this branch now contains both
  efforts; #274 can be closed in favor of it, or this can be rebased onto
  `cricket-stats` — not done without direction.
