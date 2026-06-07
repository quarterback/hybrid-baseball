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

## Round 2 — the page was *blank*, not just slow

After the schema-guard fix deployed, `/fantasy` still showed a **blank page**
on multiple devices (and InPrivate, so not a cache). Diagnosis from the live
deploy:

* The HTML, all JS bundles (byte-identical to the committed artifacts), MIME
  types, and the injected `__CAPSPACE_DATA__` blob were all correct, and the
  bundle rendered fine in a jsdom harness against the real data.
* The tell: `capspace-app.jsx` renders an **empty `<div className="app" />`**
  while `walletState === undefined`, i.e. until `fetch('/fantasy/api/wallet')`
  resolves. Timing the live endpoints showed why the page sat blank:
  `/api/wallet` **14.9 s**, `/api/slate` **18 s**, `/api/activity` **26 s**.
* All three funnel through `_settle_all()`, and the real cost is
  `build_slate_data()` — which scans the whole season's `game_*_stats` for the
  ~175-player pool. It is rebuilt **many times per request**: once for the
  slate itself, then again inside every settle path
  (`contests._LiveContext → _pool_for`, `sluggers`/`pitching`
  `_benchmark`/`_slate_entry`, `streak`). The schema-guard fix removed the DDL
  write storm but not this read blow-up.

### Round-2 fix
1. **Memoize `build_slate_data()` per `slate_date`** (`data.py`), invalidated by
   the played-game count (a cheap indexed `COUNT`, which bumps on every simmed
   game). Returns a shallow copy so callers can stamp `CONTESTS`/`WALLET`
   without polluting the cache. The many per-request rebuilds collapse to one.
2. **Windowed `_build_logs()`** — a `ROW_NUMBER() OVER (PARTITION BY player_id
   ORDER BY game_date DESC)` caps each player to the latest 5 rows in SQL,
   cutting a ~14k-row season scan to ~875. Python assembly is unchanged.
3. **Non-blank loading state** — `capspace-app.jsx`'s loading branch now renders
   a "CapSpace · Loading tonight's slate…" splash instead of an empty div, so a
   slow round-trip reads as loading, never as a broken page. Bundle rebuilt via
   `tools/build_capspace.sh` (only `capspace-app.js` changed).

### Round-2 validation
- Slate cache: 20 calls → 1 build; bumping the played count forces a rebuild;
  returned blobs are mutation-isolated.
- Windowed `_build_logs`: returns exactly the latest 5 games per player, most
  recent first, form sparkline oldest→newest.
- Rebuilt bundle: `node --check` passes; jsdom render shows the splash while the
  wallet fetch is pending and the full app (sidebar + 15 KB) once it resolves.
- Net effect: the per-request `build_slate_data` work drops from N rebuilds of a
  full-season scan to a single windowed build, so `/api/{wallet,slate,activity}`
  should return in ~1 s instead of 15-26 s, and the page paints a splash
  immediately. (Endpoint timings to be re-measured on the live deploy.)

## Round 3 — the page was unreachable: settle was on the critical path

Even with rounds 1-2 deployed, `/fantasy` still "wouldn't load." Timing the
live deploy showed the **HTML document itself took 15.7 s** to return, then
`/api/wallet` another ~12 s. The browser sat on a blank white page the whole
time. Two compounding causes:

* **Server:** `home()` → `_safe_slate()` ran `_settle_all()` **inline** before
  emitting any HTML, and `/api/wallet` / `/api/activity` did the same.
  `_settle_all()` is genuinely heavy — it grades a synthetic field and walks
  every pick across seven game types (`streakgame.status()`, called for
  `best_streak`, itself settles and game-queries per pick). None of that is
  needed to *render* the page or *show* a balance.
* **Client:** `capspace-app.jsx` gated the entire app on
  `walletState === undefined`, rendering nothing until `/api/wallet` resolved.

### Round-3 fix — settle is never on the request path
1. **Background, debounced settle** (`blueprint.py`): `_settle_all()` is now
   invoked via `_kick_settle()` — a non-overlapping (`Lock`), rate-limited
   (≥6 s apart) daemon thread. `home()`/`_safe_slate`, `/api/wallet`,
   `/api/activity` kick it and return immediately off the already-persisted
   wallet. Winnings still credit; they just appear on the next poll.
2. **Fast wallet read**: `_safe_slate` injects `wallet.balance()` (one row),
   and `/api/wallet` returns balance + records + `started` with no settle.
   `best_streak` now reads a persisted record (`wallet.rec_get`) that
   `streak.settle()` keeps current (`rec_max("best_streak", …)`), instead of
   recomputing the grade walk inline.
3. **Non-blocking UI** (`capspace-app.jsx`): removed the
   `walletState === undefined` gate. The app renders immediately using the
   balance injected in `__CAPSPACE_DATA__`; `loadWallet()` only refreshes it and
   decides onboarding in the background. Bundle rebuilt.

### Round-3 validation (Flask test client + jsdom)
- `GET /fantasy/` **14 ms**; `/api/wallet`, `/api/activity`, `/api/slate` each
  **~15 ms** (were 15-26 s). Settle runs exactly once across rapid calls, on the
  `capspace-settle` daemon thread — never the request thread.
- jsdom: the full app (sidebar + balance) renders even while the wallet fetch is
  *pending forever*; no blank/splash gate remains.

## Round 4 — the real conflict: sims vs. the fantasy app on one DB

The remaining `database is locked` came from the **sim and the fantasy app
sharing one SQLite file**. A "sim a day" run holds the main DB's single WAL
writer for a long stretch; meanwhile the fantasy app still *wrote* on some paths
(the `status()` functions and `book.status()` settled inline; placing a
bet/pick), and those writes collided with the sim's writer. Two fixes:

### 4a — fantasy is read-only on the request path
Removed every inline `settle()` / `settle_bets()` from the read paths
(`sportsbook`/`streak`/`sluggers`/`pitching` `status()`, `sportsbook.place`),
and `/api/activity` now reads the bet feed via a new read-only
`sportsbook.activity_bets()` (no settle, no live-odds rebuild). Settling happens
only in the background pass. `/api/activity` dropped from ~10 s to ~10 ms.

### 4b — CapSpace gets its own database file
CapSpace state is independent of the sim, so it now lives in
`<sim-db>-capspace.db` (new `o27v2/web/fantasy/fdb.py`). The fantasy code opens
that file as `main` and ATTACHes the sim DB as `sim`:

* fantasy **writes** (wallet, bets, picks, settle) hit the fantasy file — they
  never touch the sim's write lock, so using the app *during* a sim no longer
  locks;
* fantasy **reads** of games/players/stats keep working unchanged — the fantasy
  and sim table names are disjoint, so SQLite resolves each unqualified name to
  the right attached file, and reading an attached WAL DB never blocks the sim.

Drop-in: each fantasy module simply imports the shim as `db`
(`from . import fdb as db`), so all existing `db.get_conn/fetchall/...` calls
route to the fantasy file. A one-time, best-effort migration (`fdb._ensure_
migrated`) lifts any pre-split CapSpace tables out of the sim DB into the new
file on first use (column-aware, idempotent; sim-side copies are left orphaned
rather than dropped, so the migration never writes to the sim DB).

### Round-4 validation
- Migration: an existing `cap_wallet`/`cap_records` in the sim DB is copied into
  the fantasy file; balance/records read correctly; the sim-side copy is left
  untouched.
- Isolation: a `wallet.credit()` **completes in ~10 ms while another connection
  holds the sim DB's write lock** (`BEGIN IMMEDIATE`) — no `database is locked`.
  Writes land in the fantasy file; the sim DB is unchanged.
- `build_slate_data` still reads games/players/stats via the ATTACHed sim DB.
- Web smoke: `/fantasy/` and all `/fantasy/api/*` return 200 in <70 ms on the
  split DB.

## Round 5 — a settle self-deadlock the split DB exposed

After the split, the live logs still showed `database is locked`, but now from
the **background** settle writing the *fantasy* DB: `settle_bets` →
`wallet.credit` → `_set` → `INSERT cap_wallet`. Cause: `settle_bets` (and
`contests.settle_entries`) held an open write transaction on connection A (the
`UPDATE` loop, not yet committed) and, *inside the loop*, called
`wallet.credit()`, which opens connection B to write the same DB — B waits on
A's write lock, A doesn't commit until the loop (calling B) finishes →
self-deadlock → `database is locked`. Because `_settle_all` catches per-game
exceptions, this was silent: sportsbook/DFS winnings simply never credited.

`streak.settle` and `buyins.settle_one` already avoided this ("commit FIRST,
then credit"). Fixed `settle_bets` and `settle_entries` the same way: grade all
rows, write + commit the settlements, **then** loop the `wallet.credit()` calls
once the lock is released. Verified: a winning bet settles, credits exactly once
(+payout), raises nothing, and is idempotent on a second pass.

## Follow-ups (not done here)
- A single `build_slate_data` still scans the season for the pool's recent-form
  logs; the windowed query trims row transfer but not the scan. A date floor
  (only games within N days of the slate) would cut it further.
- The fantasy modules still open a connection in `ensure_schema()` without
  closing it; now bounded to once per module, but a `with get_conn()` would be
  tidier.
- Background settle is per-process; with multiple workers a few passes can
  overlap across processes (idempotent, lock-tolerant via the db retry), but a
  shared/post-sim trigger would be cleaner.
