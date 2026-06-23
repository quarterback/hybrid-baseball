# AAR — DB hygiene: a phantom stat-bug, orphan saves, and a re-seed FK crash

This started as "stat invariant #6 (PA = AB + BB + HBP + SH) is failing — go
fix it." It ended with no stat bug at all, but two genuine database-hygiene
bugs surfaced along the way and were fixed.

## The phantom: PA identity "failure" that wasn't

The invariant flagged 81 batter rows where `PA < AB + BB + HBP + SH`, every one
short by exactly its sacrifice count — i.e. PA computed as if the sacrifice
never happened. That points straight at the sac-bunt accounting.

It held up to scrutiny in exactly the wrong direction:

- The engine handler is correct. In `o27/render/render.py` the `sac_bunt`
  branch does `s.pa += 1` **unconditionally** (before the outcome split that
  adds `s.sh += 1`). `s.sh` is incremented in exactly one place, right next to
  the `pa` bump, on the same object.
- 300 engine seeds (foxes/bears, including super-innings phases) — replicating
  `o27v2/sim.py`'s exact per-phase extraction — produced **zero** violations.
- A clean o27v2 sim of 240 games / 209 sacrifices — **zero** violations; all 12
  invariants green.

So where did the 81 come from? **Stale save files.** `o27v2/saves/` accumulates
a `save_*.db` per run, and the offending file was dated *the previous day* — it
held games simmed with older code (before SH was folded into the PA identity).
My analysis script grabbed it with `glob(...)[0]`; the 3,439-row count was the
tell — far too many rows for the 12-game sim I thought I was reading. `initdb`
is non-destructive (CREATE IF NOT EXISTS), so subsequent sims just *appended* to
that stale file. The base-commit "reproduction" failed for the same reason — it
read the same stale data.

No code bug. But the episode exposed two real ones.

## Bug 1: orphaned save files are never swept

Old `save_*.db` files linger in the saves dir (interrupted runs, prior
sessions). They're invisible to the app — the registry never points at them —
but any loose reader (a stray glob, a test resolving "a save" by listing the
dir) can pick one up and serve stale data. That is precisely what produced the
phantom failure.

**Fix:** `saves.prune_orphans()` deletes `save_*.db` files (plus `-wal/-shm/
-journal` sidecars) whose id isn't in the registry. Guards:

- Never touches a registered save — only unreferenced leftovers.
- No-ops when the registry is empty, so a single-DB deployment
  (`O27V2_DB_PATH` set) never has its files swept.
- Leaves non-`save_*.db` files alone.

Wired into `manage.py initdb` and `resetdb`, which report what they removed.

## Bug 2: re-seeding the schedule crashed on a populated save

Trying to re-init a save that already had played games crashed with
`sqlite3.IntegrityError: FOREIGN KEY constraint failed`. `seed_schedule`'s
"different seed → wipe played results" branch did:

```python
db.execute("DELETE FROM games")          # parent FIRST — wrong
db.execute("DELETE FROM game_batter_stats")
db.execute("DELETE FROM game_pitcher_stats")
db.execute("DELETE FROM team_phase_outs")
```

Two faults: it deleted the **parent before its children**, and the child list
was **hardcoded and incomplete** — it missed `game_pa_log`, `game_bunt_log`,
`game_scoring_events`, `game_pbp`, and `game_power_play_stats` (8 tables
FK-reference `games`; only 3 were listed). With FK enforcement on (`get_conn`
sets `PRAGMA foreign_keys = ON`), deleting `games` first is rejected.

**Fix:** `db.child_tables_of(parent)` discovers FK children from the schema, and
the wipe deletes every child before `games`. Discovering them dynamically means
a newly-added per-game table can't silently reintroduce the same bug — which is
exactly how the original incomplete list shipped.

## Validation

- `o27v2/tests/test_saves.py` — 2 new tests for `prune_orphans` (removes
  unregistered files + sidecars, keeps the real save and non-save files;
  no-ops on an empty registry). 9 pass.
- `o27v2/tests/test_schedule_reseed.py` — new. On a tiny `tu` league: insert
  child rows, reseed with a different seed, assert no FK error and children
  gone; plus a check that `child_tables_of("games")` finds all 8. 2 pass.
- Manual: `initdb` now sweeps an orphan (`Removed 1 orphaned save file(s)…`);
  the reseed wipe clears `game_batter_stats` 452→0, `game_bunt_log` 22→0,
  `game_pbp` 15→0 and reschedules 2,430 games with no FK crash.
- Full stat-invariant suite green on a clean DB (the original "failure" does
  not reproduce).

## What I did NOT change

- **No stat-accounting change.** The PA identity was never broken in current
  code; touching the (correct) sac-bunt handler would have been a fix in search
  of a bug.
- **`initdb` semantics.** It is still non-destructive on a *registered* save's
  data — to wipe a populated save and start clean, `resetdb` (drop + re-init)
  is the tool. I only made the existing reseed-wipe path FK-safe and added
  orphan sweeping; I did not make `initdb` itself reset data.
