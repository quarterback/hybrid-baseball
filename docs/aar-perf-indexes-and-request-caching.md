# AAR: Server-side performance — indexes, standings N+1, request-scoped caching

**Date:** 2026-06-05
**Trigger:** Site felt laggy even after a VM bump (shared-cpu-2x / 1gb) and the
front-end fix (self-hosting fonts + Bootstrap, PR #200). Remaining lag was
per-request server work, so this pass targets the database read path.

## What was wrong (profile)

A read-path audit found three compounding problems, all of which get worse as
seasons accumulate:

1. **No indexes on the hot tables.** `game_batter_stats`, `game_pitcher_stats`,
   and `games` had only a handful of unrelated indexes. Every Scores /
   Standings / Leaders / team / player read filtered or joined these tables by
   `player_id` / `game_id` / `team_id` / `game_date` — all **full table scans**.
2. **Standings N+1.** `/standings` ran `SELECT id FROM teams` then one
   `games` query *per team* (1 + 30 = 31 queries for a 30-team league), each a
   full scan of `games`.
3. **Repeated full-table aggregations per request.** `_pitcher_wl_map()`,
   `_terminal_outs_map()`, and `_league_baselines()` each scan/aggregate a whole
   stat table, and several helpers call them multiple times while rendering a
   single page (e.g. the Scores page calls `_pitcher_wl_map()` twice; several
   routes call `_league_baselines()` 2–3×). No caching existed.

## What changed

- **Indexes** (`o27v2/db.py`, appended to the `SCHEMA` constant so
  `init_db()`'s idempotent `executescript(SCHEMA)` creates them on existing live
  DBs at next boot):
  `game_batter_stats(player_id|game_id|team_id)`,
  `game_pitcher_stats(player_id|game_id|team_id)`,
  `games(game_date)`, `games(played, game_date)`,
  `games(home_team_id, game_date)`, `games(away_team_id, game_date)`,
  `players(team_id)`.
- **Standings batched** (`o27v2/web/app.py` `standings()`): one
  `SELECT … FROM games WHERE played=1 ORDER BY game_date, id`, bucketed by team
  in Python. Rows arrive in `(game_date, id)` order so each bucket stays
  chronological for the streak / last-5 / last-10 math. 31 queries → 1.
- **Request-scoped cache** (`o27v2/web/app.py` `_req_cache`): memoizes a
  producer for the lifetime of one request via `flask.g`, guarded by
  `has_app_context()` + try/except so CLI and background sim threads (which have
  no request context) just compute. `_pitcher_wl_map()` (full-season variant
  only — the `through_game` variant still computes fresh), `_terminal_outs_map()`,
  and `_league_baselines(league)` (keyed per league scope) now route through it.
  Impl bodies preserved as `_*_compute` functions; public names are thin
  wrappers, so all existing callers are unchanged.

## Validation

- `o27v2/web/app.py` and `o27v2/db.py` parse clean (`ast.parse`).
- Initialized a throwaway DB via `db.init_db()` (with `O27V2_DB_PATH` override):
  all 11 new indexes are present in `sqlite_master`, and
  `EXPLAIN QUERY PLAN` for the new standings query reports
  `SEARCH games USING COVERING INDEX idx_games_played` (was a full scan).
- **Not run:** the Flask route smoke test and `pytest` — `flask` and `pytest`
  are absent in this sandbox (expected per CLAUDE.md). The route-level behavior
  (cache + standings refactor) was verified by reading, not by executing. Worth
  a quick local `python3 o27v2/manage.py runserver` + page load before relying
  on it in anger.

## Deliberately deferred

- **Per-query connection churn.** `db.get_conn()` still opens a fresh
  `sqlite3.connect()` (+3 PRAGMAs) per `fetchall`/`fetchone`. Reusing one
  request-scoped connection (Flask `g` + `teardown_appcontext`) would cut ~20
  connection setups per page, but it's riskier (thread-affinity, background
  threads, the `with conn:` transaction semantics) and rated medium-impact next
  to the index scans. Left for a follow-up if pages still drag after indexes
  land.

## How to confirm it helped (live)

After deploy, the first boot builds the indexes (one-time, may take a beat on a
large DB). Then load Scores / Standings / Leaders and watch response times; if
still slow with indexes present, the next lever is the connection reuse above or
a specific slow route, not more hardware.
