# AAR: Killing the page lag — diagnosis, a wrong turn, and the HTML result cache

**Date:** 2026-06-05
**Trigger:** Owner reported the live site (superinnings.com) hanging — a blank
white page on mobile, and "box scores now lag." Follow-on to
`aar-perf-indexes-and-request-caching.md` (indexes + request-scoped caching),
which had just deployed and did **not** solve the lag.

## TL;DR

The blank page was not a crash — it was a page taking 12–50s to render. The
lag is **not** the database (the SQL is milliseconds) and **not** box-score
generation. It's that the heavy read-only pages (Scores, Leaders, a box's
season context) **recompute everything on every load** — ~2.4s of CPU-bound
aggregation that throttles out to 30–50s on a shared Fly vCPU under any
concurrency. Fix: cache the rendered HTML, gated on a cheap data fingerprint
that changes only when the owner sims/trades, and move to a dedicated CPU.

## How we diagnosed it (and the wrong turn)

1. **Not a crash.** `curl` of the live site: `/api/health` → 200 in ~1s, but
   `/` → 200 in 3–12s and `/leaders` → 200 in **25–52s** (and *climbing* across
   back-to-back calls, i.e. CPU contention). The blank screenshot was the
   browser waiting on the slow HTML. Health being fast ruled out a down/looping
   machine.
2. **The index work was a red herring for the worst pages.** I had just shipped
   table indexes assuming full-table scans were the cost. Benchmarking the
   actual Leaders queries against a synthetic full season (44k batter rows, 20k
   pitcher rows): **15–75 ms**, with or without the indexes, with or without
   ANALYZE. `EXPLAIN QUERY PLAN` confirmed the new dedup composite index is used.
   So the indexes are harmless-to-helpful, but they were never going to fix a
   25s page. Lesson: **measure before indexing**; I should have profiled first.
3. **Profiled the real route.** Installed Flask in the sandbox and ran
   `leaders()` under cProfile against the synthetic season:
   `/leaders` total **2.39s** — sqlite execute 0.87s (≈200 small queries),
   `_top_batter_games` 0.55s, `_league_baselines` 0.48s, `_top_pitcher_outings`
   0.35s, `_aggregate_batter_rows` 0.26s. All of it recomputed every load.
   2.4s on a fast CPU → tens of seconds on a throttled shared vCPU. That's the
   lag.

## On the owner's "generate box scores on demand" idea

The owner proposed not generating box scores except for games they click.
Findings, recorded so it doesn't come up again:

- Box scores are **already** render-on-demand — clicking a game *reads* stored
  data; nothing is generated on click. What's written at sim time is (a) stat
  **lines** (`game_batter_stats`/`game_pitcher_stats`, required for
  Standings/Leaders — not skippable) and (b) play-by-play detail
  (`game_pbp`/`game_pa_log`, only needed for one game's box).
- Regenerating a box by re-simming is **not safe**: per CLAUDE.md, re-sims
  aren't reproducible once rosters change (post-game trades/injuries), so a
  regenerated box wouldn't match the recorded result.
- It also wouldn't fix the lag: Leaders/Scores don't read boxes.
- Pruning PBP detail remains a legitimate but **separate** lever for DB
  size / sim speed, not page latency.

## The fix

1. **Whole-page HTML result cache** (`o27v2/web/app.py`):
   - `_html_cache` decorator caches a GET view's rendered HTML body, keyed by
     `(active save DB, path, query string, league scope)`.
   - Invalidation by `_data_fingerprint()` — one indexed round-trip returning
     `(games-played count, max game id, transactions count, players count, max
     player id)`. Unchanged fingerprint ⇒ serve cache; a sim/trade/roster move
     changes it ⇒ next load recomputes exactly once.
   - We cache the **render**, never re-run the engine, so the determinism
     caveat does not apply. Cache is in-memory, bounded (`_HTML_CACHE_MAX=96`,
     clears wholesale on overflow), fine to lose on restart.
   - `after_request` hooks (cookie persistence, etc.) still run — we cache the
     body string, not the final `Response`. Redirects / JSON (`?format=json`,
     which returns a `Response`) pass straight through uncached.
   - Applied to the three worst routes: `index` (Scores), `leaders`,
     `game_detail` (box score).
2. **Dedicated CPU** (`fly.toml`): `shared-cpu-2x`/1gb → `performance-1x`/2gb.
   Removes burst throttling so the first (uncached) load after a sim stays near
   its true ~2.4s instead of ballooning under contention.

## Validation (Flask + synthetic full season, in-sandbox)

- `/` : MISS 1165ms → **HIT 1.9ms** (612×)
- `/leaders` : MISS 2548ms → **HIT 2.3ms** (1100×)
- `/game/1` (box) : MISS 2175ms → **HIT 1.9ms**
- **Invalidation:** inserting a played game (a "sim") forced a recompute
  (2052ms) then went instant again — correct.
- `?format=json` still returns `application/json` (not cached as HTML);
  undecorated routes (`/standings`) unaffected; `?league=` scopes key
  separately.
- Endpoints intact (`functools.wraps` preserves view names, so no
  `wrapper` endpoint collision).

Note: validated against **synthetic** data on the sandbox CPU. Real-data, live
before/after numbers should be re-measured after deploy (probe `/`, `/leaders`,
a `/game/<id>` twice each: second load should be tens of ms).

## Known limitations / follow-ups

- **Fingerprint blind spots.** A pure metadata mutation that changes neither a
  count nor a max id — e.g. renaming a team via `/team/<id>/edit` — won't
  invalidate the cache, so a stale team name could show until the next sim or
  cache eviction. Rare; acceptable. If it bites, add a teams-content signal to
  the fingerprint.
- **Per-process cache.** If the app ever runs multiple workers, each warms its
  own cache (harmless duplicate first-loads). Today it's a single process.
- **Uncached first load is still ~2.4s.** The cache makes repeats instant but
  doesn't make the cold compute cheap. If the first-after-sim load matters,
  the next lever is trimming `_top_batter_games`/`_top_pitcher_outings` and the
  XO-crossover work, or precomputing leaderboards at sim time.
- **More routes.** Only the three worst are decorated. `players`,
  `team_detail`, `team_stats`, `analytics` are candidates if they drag.
- The deferred per-query connection reuse from the previous AAR is still open.
