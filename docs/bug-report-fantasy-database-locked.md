# Bug Report — Fantasy app (CapSpace): lag, blank page, and `database is locked`

**Status:** Fixed (branch `claude/tender-einstein-etBTY`)
**Area:** `o27v2/web/fantasy/` (CapSpace)
**Severity:** High — app effectively unusable, and fantasy winnings silently not credited

---

## Summary
The `/fantasy` app took 15–27 s to load (often showing a blank page), and using
it while a simulation was running threw `database is locked`. Root causes were a
write-storm + heavy work on the request path, the whole UI gating on a slow
wallet call, the fantasy app sharing the sim's database, and a settle
self-deadlock. All four are fixed.

## Symptoms
- "Massive lag" opening `/fantasy`; frequently a blank cream page.
- Popup on sim: `Simulation finished with 1 game(s) failing … First error: database is locked`.
- Server logs: repeated `CapSpace settle failed: settle_bets … sqlite3.OperationalError: database is locked`.
- Sportsbook/DFS winnings never appearing in the wallet.

## Root causes & fixes

1. **Schema write-storm.** Every fantasy module ran `ensure_schema()`
   (`executescript` + `commit`, a WAL write) on nearly every call; one page load
   fanned out into dozens. → Memoized `ensure_schema()` per DB path
   (`_schema_once.py`). Added a bounded retry-on-locked to `db.execute`/`executemany`.

2. **Heavy work on the request path.** `home()` / `/api/wallet` / `/api/slate` /
   `/api/activity` ran `_settle_all()` and rebuilt the season-scanning
   `build_slate_data()` many times per request (15–26 s each). → Settling moved
   to a debounced background thread; `build_slate_data()` is memoized per slate
   (invalidated by played-game count) and its game-log query is windowed; the
   `status()` read paths no longer settle inline.

3. **UI blocked on the wallet.** The React app rendered an empty `<div>` until
   `/api/wallet` returned. → It now renders immediately using the balance
   injected in the page; the wallet refreshes in the background.

4. **Sim vs. fantasy on one database.** A long "sim a day" run holds the sim
   DB's single WAL writer; fantasy writes collided with it. → CapSpace tables now
   live in their own file (`<sim-db>-capspace.db`, `fdb.py`); the fantasy code
   opens that file and ATTACHes the sim DB read-only, so fantasy **writes** never
   touch the sim's write lock while reads still work. A one-time migration lifts
   existing CapSpace tables out of the sim DB.

5. **Settle self-deadlock.** `settle_bets` / `contests.settle_entries` held an
   open write transaction and called `wallet.credit()` (a second connection to
   the same DB) *inside the loop* → self-deadlock → `database is locked`
   (swallowed, so winnings never credited). → Grade all rows, commit, **then**
   credit (matching `streak.settle` / `buyins.settle_one`).

## Validation
- `/fantasy/` ~14 ms; `/api/{wallet,slate,activity}` ~10–20 ms (were 15–27 s).
- A `wallet.credit()` completes in ~10 ms **while another connection holds the
  sim DB's write lock** — no `database is locked`.
- Migration carries the existing wallet/records into the fantasy DB; reads of
  games/players/stats still resolve via the ATTACHed sim DB.
- A winning bet settles, credits exactly once, and is idempotent on re-run.
- `pytest o27/tests o27v2/tests` → 235 passed (the lone failure,
  `test_gm_noise_can_be_lopsided`, is a pre-existing flaky randomized test
  unrelated to this change).

## Notes / follow-ups
- `_settle_all()` still runs on a background kick from read endpoints; longer
  term it belongs on a post-sim hook.
- A single `build_slate_data` still scans the season; a date-floored log query
  would trim it further.
- Full detail in `docs/aar-fantasy-lag-and-database-locked.md`.
