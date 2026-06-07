# After-Action Report — Disk-full crash loop, DB maintenance, audio retention

**Date completed:** 2026-06-07
**Branch:** `claude/compassionate-clarke-L9oFl`

---

## TL;DR

The deployed app (Fly, `hybrid-baseball`, region `ams`) was in a **boot crash
loop**: `sqlite3.OperationalError: disk I/O error` in `init_db()` on every
start. Root cause was an **out-of-space `/data` volume** (the boot fsck showed
`253952/258048` blocks = ~98% full on a 1 GB volume). SQLite can't write its
WAL/journal on a full disk, so the first PRAGMA on connect threw and the process
exited → Fly restarted → loop → "max restart count of 10".

This was **not** a code regression and **unrelated to the name-pool work** done
earlier in the session (that ran from commit `b353fac`; the crashing machine was
on `ea887ee`, and none of those changes touch the DB or disk).

The volume filled because three things grew unbounded across a season:

1. **`game_pbp`** — a full play-by-play TEXT blob per game (~40 KB+/game).
2. **The SQLite WAL** — never checkpoint-truncated, so `*.db-wal` could balloon.
3. **Generated radio audio** — `o27audio` wrote a roundup (and in `full` mode a
   game broadcast) per game-day to `/data/audio` and never deleted any.

Immediate unstick was operational (extend the volume); the durable fixes are in
this branch.

---

## 1. Immediate fix (operational — done by the user)

There is no `flyctl` in the agent sandbox, so the agent could not touch the live
volume. The user extended it:

```
fly volumes extend <vol-id> -s 3 -a hybrid-baseball   # 1 GB → 3 GB
fly apps restart hybrid-baseball
```

That gave SQLite headroom and the app booted. **3 GB buys time, not a permanent
fix** — without the changes below the same three sources refill the volume.

## 2. `manage.py dbmaint` — reclaim space

New CLI command. Sweeps **every** save DB (the saves-registry dir + the legacy
single-DB file), not just the active one, and per DB:

- Prunes `game_pbp` rows for **completed games** (`played = 1`). Per the user's
  call, only the cosmetic play-by-play text is dropped — **all stats, box
  scores, and analytics (`game_pa_log`, `game_*_stats`) are kept**. The
  `/game/<id>/pbp` narrative page is the only thing lost for old games.
- `PRAGMA wal_checkpoint(TRUNCATE)` then `VACUUM` to return freed pages to the
  filesystem.
- Flags: `--dry-run` (report only) and `--keep-current-season`.

`VACUUM` needs free space ≈ DB size, so the documented order is **extend first,
then dbmaint**. The command catches a failed VACUUM and says so rather than
dying.

Verified on a seeded 8-team league, 30 games simmed: `dbmaint` freed 1.3 MB of
2.8 MB (PBP was ~half the file) and every stats table survived
(`game_batter_stats` 863, `game_pitcher_stats` 269, `game_pa_log` 1726 rows
intact; `game_pbp` → 0).

## 3. Boot-time WAL checkpoint + clear disk-full message

`cmd_runserver` now runs `PRAGMA wal_checkpoint(TRUNCATE)` after `init_db()` on
every boot, so a runaway `*.db-wal` can't accumulate across restarts. And
`init_db()` is wrapped so a `disk I/O error` / disk-full prints an actionable
message ("the /data volume is almost certainly full — extend it / run dbmaint")
instead of a raw traceback. (It still exits non-zero — this is clarity, not a
way to limp along on a full disk.)

## 4. Ephemeral generated audio (`o27audio`)

The autogen worker (`o27audio/worker.py`) made one roundup per new game-day
(`ref = <save_key>:<date>`) and, in `full` mode, one game broadcast
(`ref = <save_key>:<game_id>`), writing `.wav` + `.mp3` to `/data/audio/...`
and recording them in `manifest.db`. Nothing ever deleted old days.

Now, once a new game-day's clips exist, `_purge_old_days(save_key, latest)`
deletes every **earlier** day's audio for that save — both the files and the
manifest rows:

- Roundup refs carry the date directly.
- Game refs are mapped to their `game_date` via the save DB (`sources.load_game`).
- Clips whose date can't be resolved are **left alone** (fail-safe — never
  delete blindly).
- Scoped to the save being processed; best-effort so it can't break the worker
  loop.

New manifest helpers: `list_for_save()` (LIKE-escaped on the save key) and
`delete_clip()` (removes wav + mp3 + row, tolerant of already-missing files).

Verified both paths against temp manifests + a seeded DB: old roundups removed,
latest day kept, a game clip purged when its `game_date` ≠ keep-date and kept
when equal, a different save left untouched.

## 5. `fly.toml` region fix

`primary_region` was `iad` — which the user confirmed belongs to a *different*
app (their tennis game); `hybrid-baseball` and its `o27v2_data` volume live in
`ams`. Left as `iad`, a `fly deploy` could place a new machine/volume in the
wrong region, orphaned from the real data. Corrected to `ams`.

---

## What this does NOT do (caveats)

- **No live-volume access from the agent.** No `flyctl`/SSH in the sandbox, so
  the extend, deploy, and `dbmaint` run are the user's to execute. The live
  machine is still on `ea887ee` until this branch is deployed — the recurrence
  fixes only take effect after `fly deploy`.
- **`dbmaint` prunes only `game_pbp` text.** `game_pa_log` and the per-game stat
  tables (the next-largest contributors) are retained by design. If disk
  pressure returns, pruning per-PA logs for old seasons is the next lever.
- **Audio retention keys off "next day generated," not "played."** Tracking
  actual playback would need the web player to write back state; "keep only the
  latest game-day" is simpler and matches the intent ("overwritten or deleted").
  A manually-generated clip for the *current* day is kept; old saves' audio is
  only purged when that save is the one the worker is actively processing.
- **Baseline comparison workflow.** This save was being simmed as an old-engine
  baseline before loading rule-change updates. The user downloaded the save
  file as the frozen baseline. The recommended (cleaner) approach — fork the
  save *before* applying updates so old vs new differ only by engine — was
  discussed; a `duplicate-save` / `compare-saves` command was offered and
  declined for now, so it is **not** implemented.

## Commits (this phase)

* `Add dbmaint cleanup command + startup WAL checkpoint`
* `Fix fly.toml primary_region to ams`
* `Make generated radio audio ephemeral (keep latest day only)`
* this AAR

(Earlier in the same branch/session: the orphan-nation promotion and league-wide
name-pool scrub — see `docs/aar-orphan-nations-and-name-pollution.md`.)
