# After-Action Report — Concurrent Saves / Leagues + Restart Data-Loss Fix

**Date completed:** 2026-05-21
**Branch:** `claude/concurrent-saves-leagues-q6DrT`
**Commits (in order):**
- `417eb63` — Add multiple named save slots for leagues + fix data-loss on restart

---

## What was asked for

The user runs the public O27 site and wanted three things, in their words:
the game "is visible for anyone but it doesn't allow you to have a concurrent
set of saves or leagues, and it's tough when I can't save the league or access
it days later."

Clarifying answers gathered before building:

- **Privacy model:** *Shared named slots* — no login. Anyone visiting can list
  the saved leagues and switch the active one.
- **Save behaviour:** *Both* — switch between many leagues in-app **and**
  export/import a league as a file.
- **Persistence pain:** *Data disappears on its own* — not just "new league
  wipes the old," but leagues vanishing between visits.

---

## What shipped

**Multiple named leagues (concurrent saves).** Each league is its own SQLite
file under a saves directory, tracked by a JSON registry (`o27v2/saves.py`)
with a single global "active" pointer. `db.get_conn()` resolves the active
save's file at call time (`o27v2/db.py` `_resolve_path()`), so all ~50 existing
routes work unchanged. An explicit `O27V2_DB_PATH` (or a runtime `_DB_PATH`
reassignment) still selects a single fixed DB, which is what the test suite
relies on — the registry is bypassed in that case.

- **`/saves` page** (nav link "Saves"): list, **activate**, rename, delete,
  **export**, and **import** leagues. The active league's name shows next to
  the ⚾ O27 brand in the topbar.
- **`/new-league` no longer wipes the active league** — it creates a new save
  file and switches to it (`o27v2/web/app.py` `new_league_post`). Old leagues
  stay on disk.
- **Export** = clean single-file snapshot via `VACUUM INTO` (merges WAL, no
  `-wal`/`-shm` sidecars). **Import** validates the uploaded file has a `teams`
  table before registering it.
- **Startup migration** (`o27v2/manage.py` `cmd_runserver`): adopts any legacy
  single DB as "Save 1", or creates an initial slot on a fresh box, so the
  seeded league always lives in a registered save.
- **`fly.toml`:** dropped `O27V2_DB_PATH` in favour of
  `O27V2_SAVES_DIR=/data/saves` so the registry drives storage on the
  persistent volume. (With the env var set, the registry would be bypassed.)

**The actual "data disappears on its own" fix.** Found during verification, and
the more important outcome. The startup safety check `_wipe_if_stale()`
(`o27v2/db.py`) was meant to clear ancient pre-upgrade databases. It decided a
league was stale if any pitcher had a blank `pitcher_role` — but **Task #65
deliberately stopped storing `pitcher_role`** (roles are derived live at game
time, see `o27v2/league.py:1763` and `o27v2/sim.py:1157`). So the check matched
*every* healthy modern league. Because `init_db()` runs on every server boot,
each Fly restart (redeploy, 512 MB OOM, idle cycle) silently dropped the league
and reseeded a fresh one — matching "I come back days later and it's gone." The
staleness signal now keys on a populated roster with **zero archetypes**
(genuinely pre-Phase-8 data) instead.

---

## What went well

- **The architecture made the refactor cheap.** The decisive early finding was
  that `get_conn()` opens a fresh connection with **no pooling/caching**. Making
  the DB path dynamic was a ~15-line change in one place rather than touching 50
  routes.
- **The test-bypass design held.** Keying "explicit override = single fixed DB"
  off `O27V2_DB_PATH` / `_DB_PATH` let the existing suite pass untouched, which
  is what we want from a refactor this broad.
- **Verification caught the real bug.** Running an actual `/new-league` →
  activate flow (not just unit tests) is what surfaced the wipe-on-restart. A
  green unit suite alone would have shipped the latent bug.

---

## What surprised us (the important part)

- **The root cause wasn't the feature.** "New league wipes the old" was real but
  secondary. The infrastructure (`fly.toml` volume + mount) looked correct the
  whole time — the loss was happening in application logic on the boot path.
- **A destructive heuristic ran unconditionally on a hot path.** An auto
  `drop_all()` at startup is a footgun regardless of trigger; it stayed hidden
  only because its signal *used* to be selective.
- **Tests encoded a dead data model.** The `test_phase8` failures were already
  red on `main` and asserted `pitcher_role == "workhorse"` and `is_joker` rows
  that no longer exist — i.e. the suite hadn't kept up with Task #65, which is
  partly *why* nobody noticed the heuristic had gone stale.

---

## What was harder than expected

- **Telling "my regression" from "already broken."** Several failures
  (stat-invariants, two statistical trade/weather tests) needed `git stash`
  baseline runs to attribute correctly. Three of the "failures" were
  pre-existing.
- **Fresh-install edge case.** The first pass would have seeded into the
  unregistered fallback file on a brand-new box, leaving the league invisible in
  `/saves`. Caught during fresh-start verification; fixed by creating the slot
  before seeding.

---

## Lessons

1. **Config looking correct ≠ data being safe.** When persistence is the
   complaint, audit application code that mutates/deletes on startup, not just
   the storage mount.
2. **A stale heuristic is created by a change elsewhere.** `_wipe_if_stale`
   wasn't wrong when written — Task #65 broke it from a distance. Signals that
   depend on another module's invariant should assert that invariant, or they
   rot silently.
3. **Verify behaviour, not just the suite.** The unit tests were green on the
   dangerous path; the manual flow wasn't.
4. **Tests that drift become camouflage.** The outdated phase8 assertions masked
   the regression for anyone who ran the suite previously.

---

## Verification performed

- New `o27v2/tests/test_saves.py` (7 tests) + realigned
  `test_phase8_db_migration.py` — all green.
- Full suite: 179 passed. Remaining failures are pre-existing and confirmed red
  on `main`: an empty stat-invariants fixture DB (no `games` table) and two
  RNG/order-sensitive statistical tests (`gm_noise_can_be_lopsided`, weather
  envelope) that pass standalone.
- End-to-end via the Flask test client: create two leagues → both preserved →
  activate switches the data → export produces a valid single-file DB → import
  round-trips → rename/delete work → activating no longer wipes.

---

## Open risks / follow-ups

- **Deploy step unverified in prod.** The legacy `/data/o27v2.db → Save 1`
  adoption is tested locally but not on the live volume. Recommended: deploy,
  confirm `/data/saves/registry.json` + `save_*.db` exist, then restart the
  machine and confirm the active save survives.
- **Switching saves mid-simulation** is guarded against the multi-season runner,
  but a single long sim request racing an activate is theoretically possible —
  low risk on a single-user site.
- **Two flaky statistical tests** are order/RNG-sensitive (pass standalone, can
  flake in full-suite runs). Pre-existing; worth seeding or widening tolerances
  someday.
- **No PR opened**, per the user's preference at the time.
