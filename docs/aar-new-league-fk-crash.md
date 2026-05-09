# After-Action Report — /new-league FK Crash + Unrecoverable DB

**Date completed:** 2026-05-09
**Branch:** `claude/monitor-api-health-ypTDM`
**Commit:** `dec35cf` (drop_all FK fix)

---

## What was asked for

Two messages from the user, the second clarifying the impact:

> "still not able to get the league started again after the last 2 PRs"

> "i try to start a new league in the pro/rel universe and it crashes
> and then i can't get it back at all."

The session was originally scoped to monitoring `/api/health`. The
health logs themselves looked fine — `200` on every probe — but the
same log stream surfaced a real-user crash on `/new-league` followed
by every subsequent request 500'ing on a different error. That's what
the user was actually stuck on.

---

## Diagnosis

### Two errors, same root cause

The log showed `POST /new-league` raise:

```
File "/app/o27v2/db.py", line 775, in drop_all
    conn.executescript("""
sqlite3.IntegrityError: FOREIGN KEY constraint failed
```

Then the next page load and every subsequent request 500'd on:

```
File "/app/o27v2/sim.py", line 1707, in get_current_sim_date
    row = db.fetchone("SELECT value FROM sim_meta WHERE key = 'sim_date'")
sqlite3.OperationalError: no such table: sim_meta
```

The second error is a *consequence* of the first: `executescript`
runs each `DROP TABLE` statement in sequence and SQLite commits each
one as it goes. When the script raised midway through, the tables
that had already been dropped (including `sim_meta`) stayed dropped.
Every page-load context processor calls `get_current_sim_date()`, so
the whole app went down — not just `/new-league`.

### What `drop_all()` was missing

`drop_all()` enumerates the child tables in dependency order before
parents, gated by `PRAGMA foreign_keys = ON` (set in `get_conn()`).
The list it knew about:

```python
DROP TABLE IF EXISTS transactions;
DROP TABLE IF EXISTS game_pa_log;
DROP TABLE IF EXISTS game_pitcher_stats;
DROP TABLE IF EXISTS game_batter_stats;
DROP TABLE IF EXISTS team_phase_outs;
DROP TABLE IF EXISTS sim_meta;
DROP TABLE IF EXISTS season_awards;
DROP TABLE IF EXISTS games;
DROP TABLE IF EXISTS playoff_series;
DROP TABLE IF EXISTS players;
DROP TABLE IF EXISTS teams;
```

But three other tables — owned by `o27v2/auction.py`, not `db.py` —
also `REFERENCES teams(id)` and `players(id)`:

```
auction.py:46  CREATE TABLE IF NOT EXISTS auction_keepers (
auction.py:48      team_id  INTEGER NOT NULL REFERENCES teams(id),
auction.py:49      player_id INTEGER NOT NULL REFERENCES players(id),
auction.py:55  CREATE TABLE IF NOT EXISTS auction_results (
auction.py:59      player_id   INTEGER NOT NULL REFERENCES players(id),
auction.py:61      winner_team_id INTEGER REFERENCES teams(id),
auction.py:65      traded_to_team_id INTEGER REFERENCES teams(id),
auction.py:76  CREATE TABLE IF NOT EXISTS auction_lot_bids (
auction.py:80      team_id   INTEGER NOT NULL REFERENCES teams(id),
```

These get created (`init_auction_schema()`) and populated the first
time the user runs the new-league auction flow. Once they hold rows,
`DROP TABLE players` and `DROP TABLE teams` raise `FOREIGN KEY
constraint failed` — and `drop_all()` aborts halfway, killing the
app.

The "last 2 PRs" the user mentioned were the auction polish work
(#36 — `Live auction polish: render fix on FINAL PRICE + flag emojis
+ /youth schema`) and the data-cards wiring (#37 — `Fresh-league 500:
derive_linear_weights default for missing event types`). Neither
touched `drop_all()`, but #37 was the first time most users actually
got to the auction stage on a fresh league — exposing the latent FK
gap that #35 (`new-league-structure`) had opened by introducing the
auction tables in the first place.

### Why this hadn't surfaced earlier

`drop_all()` is the reset path for **starting a new league**, not the
fresh-install path. A fresh DB has empty tables; dropping them passes
trivially. The bug only triggers when:

1. A previous league completed enough of `seed_league` that
   `init_auction_schema()` ran (auction tables created), AND
2. The auction itself ran (rows inserted into `auction_keepers` /
   `auction_results` / `auction_lot_bids`), AND
3. The user then tries to start a *second* league.

So it's a regression that a fresh-DB integration test wouldn't catch
— it requires DB state from a prior season.

---

## What was built

### Commit `dec35cf` — drop_all FK fix

Two changes in `o27v2/db.py:drop_all()`:

**1. Drop the auction tables explicitly.** They go first since
they're pure children of teams/players and have no dependents:

```python
DROP TABLE IF EXISTS auction_lot_bids;
DROP TABLE IF EXISTS auction_results;
DROP TABLE IF EXISTS auction_keepers;
```

**2. Disable FK enforcement for the duration of the script.**
Drop ordering is fragile — every new module-owned table that
references `teams`/`players` is another partial-drop landmine. The
fix wraps the whole script in `PRAGMA foreign_keys = OFF`/`ON` so the
order doesn't matter and any future referencing table is structurally
safe:

```python
def drop_all() -> None:
    """Drop all tables (for re-seeding)."""
    with get_conn() as conn:
        # FKs off for the duration of the drop: auction_*, and any other
        # module-owned tables that reference teams/players, would otherwise
        # raise FOREIGN KEY constraint failed and abort the reset
        # mid-script — which leaves the DB in a half-dropped state where
        # every subsequent request 500s with "no such table: sim_meta".
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.executescript("""
                DROP TABLE IF EXISTS auction_lot_bids;
                DROP TABLE IF EXISTS auction_results;
                DROP TABLE IF EXISTS auction_keepers;
                DROP TABLE IF EXISTS transactions;
                ...
                DROP TABLE IF EXISTS players;
                DROP TABLE IF EXISTS teams;
            """)
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
```

The `try/finally` makes sure FKs are restored even if some other
exception (corrupt DB, locked file) escapes the script — the
connection is short-lived but the PRAGMA flips to a session-scoped
default at SQLite level, and being defensive here is cheap.

### What was deliberately *not* dropped

The seasons-archive tables — `seasons`, `season_standings`,
`season_batting_leaders`, `season_pitching_leaders` — are still left
intact, matching the existing comment in the schema:

```python
# Task #62: archived season history. These tables persist ACROSS
# the drop_all() / reseed cycle (drop_all() leaves them intact) so a
# multi-season test run can compare model output across seasons.
```

The repro test verified this: after `drop_all()`, those four tables
are still present.

### Recovery for the broken DB

The user's existing DB is in the half-dropped state described above.
No special migration is needed. On the next request `init_db()` runs
its `executescript(SCHEMA)` step, which is `CREATE TABLE IF NOT
EXISTS` for every table — missing tables come back, surviving rows
in untouched tables stay. Then the user can hit `/new-league` again
and the now-fixed `drop_all()` finishes cleanly.

---

## Repro / regression check

Wrote a focused integration repro before fixing — same setup as a
real user mid-second-season:

```python
db.init_db()
auction.init_auction_schema()

with db.get_conn() as c:
    c.execute("INSERT INTO teams (...)")
    c.execute("INSERT INTO players (...)")
    c.execute("INSERT INTO auction_keepers (team_id, player_id, season) VALUES (1,1,1)")
    c.commit()

db.drop_all()  # was: FOREIGN KEY constraint failed
```

Before the fix: raises `sqlite3.IntegrityError: FOREIGN KEY
constraint failed` exactly as in production.

After the fix:

```
drop_all OK
remaining tables: ['season_batting_leaders', 'season_pitching_leaders',
                   'season_standings', 'seasons', 'sqlite_sequence']
after reseed: ['auction_keepers', 'auction_lot_bids', 'auction_results',
               'game_batter_stats', 'game_pa_log', 'game_pitcher_stats',
               'games', 'players', 'playoff_series', 'season_awards',
               'season_batting_leaders', 'season_pitching_leaders',
               'season_standings', 'seasons', 'sim_meta', 'sqlite_sequence',
               'team_phase_outs', 'teams', 'transactions']
FK pragma still ON: 1
```

Three things to confirm:

1. `drop_all()` completes without raising.
2. Seasons-archive tables survive (`Task #62` invariant intact).
3. `PRAGMA foreign_keys` is back to `ON` after `drop_all()` returns
   — so app-level inserts still get FK protection.

All three pass.

---

## What didn't ship

No matching test in the repo's `tests/` directory. The repro lives in
the AAR rather than as a committed regression test because seeding a
two-league DB without dragging in the rest of `seed_league` requires
either fixture infra that doesn't exist yet or a brittle hand-built
state. The structural fix (FKs off + the auction tables in the drop
list) is the thing that prevents the class of bug — adding a new
referencing table in some future module won't reintroduce the
partial-drop failure mode, because FKs are simply off for the
duration of the script.

If we want a guard test later, the right shape is:

```python
def test_drop_all_with_full_referencing_state():
    seed_minimal_league_with_auction_run()
    db.drop_all()
    # No raise; reset gets us back to a reseedable state.
```

Skipping for now to keep the fix minimal.

---

## Files touched

- `o27v2/db.py:768` — `drop_all()`: added auction tables to the drop
  list; wrapped the executescript in `PRAGMA foreign_keys = OFF`/`ON`
  with a `try/finally` to restore FK enforcement.

Total diff: 26 insertions, 17 deletions in 1 file.
