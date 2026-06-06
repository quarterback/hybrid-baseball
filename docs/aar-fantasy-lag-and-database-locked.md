# AAR — CapSpace (/fantasy) lag + `database is locked` on sim

## Symptoms
1. The fantasy app (`/fantasy`) "still has a massive lag when I click it" — even
   after the earlier precompile/Babel fix (`docs/aar-capspace-precompile.md`),
   which had only addressed the *client* cost.
2. Simming threw a popup: *"Simulation finished with 1 game(s) failing. Played 0
   successfully. First error: database is locked."*

## Diagnosis — one root cause behind both
Every fantasy submodule (`wallet`, `contests`, `buyins`, `sluggers`,
`pitching`, `categories`, `sportsbook`, `streak`, `bestball`) had its own
`ensure_schema()` shaped as `db.get_conn(); conn.executescript(<CREATE TABLE IF
NOT EXISTS …>); conn.commit()` — i.e. a full **WAL write transaction** (plus a
leaked, never-closed connection) — and it was called on *almost every* public
function: `wallet.balance()`, `wallet.records()`, `wallet.started()`,
`contests.list_contests()`, `contests.settle_entries()`, every game's
`settle()`, etc.

A single `/fantasy` request fans out massively:
`home()` → `_safe_slate()` → `build_slate_data()` **+** `_contest_cards()`
(→ `list_contests` → `ensure_schema`) **+** `_settle_all()` (seven games'
`settle()`, each calling `ensure_schema()` and nested `wallet.credit()` →
`wallet.balance()` → `ensure_schema()` again…). The background pollers
(`/api/wallet`, `/api/activity`) do the same on a timer.

That produced **dozens of write+commit round-trips per request**. Two
consequences:

* **Lag** — each commit is a WAL write (an fsync-class op, expensive on Fly's
  networked volume), serialized behind the single WAL writer lock.
* **`database is locked`** — those page-load writes contend with a running
  sim's writes. `busy_timeout = 10000` (set in `get_conn`) makes a connection
  *wait* for the writer lock, but it does **not** cover the read→write upgrade
  case (a txn that SELECTs then writes is refused immediately to avoid
  deadlock), which the settle paths (`SELECT … WHERE settled=0` then `UPDATE`)
  hit. Under a write storm one side loses and the sim reports the failed game.

## Fix
1. **Memoize `ensure_schema()` per resolved DB path** — new helper
   `o27v2/web/fantasy/_schema_once.py` exposes an `@once` decorator that runs the
   (idempotent) DDL at most once per `(db._resolve_path(), module)` per process.
   Switching the active save (a different path) still triggers a fresh build;
   repeat calls against the same DB become no-ops. Applied to all nine
   `ensure_schema()` functions. This collapses the per-request write storm to
   zero after warm-up, killing both the lag and the lock contention. (It also
   incidentally bounds the old connection leak to one per module.)
   A `reset()` hook is provided for test suites that drop the CapSpace tables.
2. **Defensive retry-on-locked** in `o27v2/db.py` — `execute()` and
   `executemany()` now run through `_retry_on_locked()`, a short bounded backoff
   (6 tries, 50ms→…) that turns a momentary `database is locked` into a brief
   wait instead of a 500/failed game. Non-lock `OperationalError`s (e.g.
   `no such table`) propagate immediately.

No engine, schema, or scoring logic changed — only *when* the lazy DDL runs and
how transient lock collisions are handled.

## Validation
- Instrumented run: 45 `ensure_schema()` calls (5× across all 9 modules)
  produce exactly **9** `executescript` runs (one per module); a further 30
  `wallet.balance/records/started` reads add **0**. `reset()` forces a rebuild.
- Retry helper: succeeds after a transient lock; re-raises non-lock errors.
- `pytest o27/tests` → 106 passed. `pytest o27v2/tests` → 129 passed, 1 skipped.
- **Not** verified against the live deployed DB in a browser — the page should
  be loaded once post-deploy to confirm the felt loads quickly and a concurrent
  sim no longer reports `database is locked`.

## Follow-ups (not done here)
- `build_slate_data._build_logs()` still pulls every season `game_*_stats` row
  for the ~250-player pool to keep only the last 5 per player; a windowed query
  would trim the remaining read cost. Left alone to keep this change tight.
- The fantasy modules still open a connection in `ensure_schema()` without
  closing it; now bounded to once per module, but a `with get_conn()` would be
  tidier.
