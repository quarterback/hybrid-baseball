# AAR — SQLite connection leak surfacing as `disk I/O error`

## Symptom

Production logs (2026-06-07) showed `sqlite3.OperationalError: disk I/O error`
500s across unrelated routes:

```
GET /almanac/players/lad_achille_gonzález.html  -> 500
  o27/almanac/loader.py:123  teams = [dict(r) for r in conn.execute("SELECT * FROM teams")]
  sqlite3.OperationalError: disk I/O error

GET /college/game/4848  -> 500
  o27v2/db.py:76  conn.execute("PRAGMA synchronous = NORMAL")
  sqlite3.OperationalError: disk I/O error
```

The tell: one failure fires on a **bare `PRAGMA` immediately after `connect()`**,
and another on the **first `SELECT`** of a freshly opened connection. Neither is
doing meaningful disk work of its own — they fail the moment SQLite tries to
*open* the database (and, in WAL mode, its `-wal` / `-shm` sidecars). That is the
classic signature of the process having run out of **file descriptors**:
`SQLITE_IOERR` is what SQLite raises when it cannot open the file handles it
needs. The errors hitting multiple routes at once (the almanac uses its *own*
`sqlite3.connect`, which already closes in a `finally`) confirmed the almanac was
a *victim*, not the source — something else was exhausting the process-wide fd
budget.

## Root cause

`o27v2/db.py` (and the fantasy `fdb.py` that mirrors it) opened a connection per
call and relied on `with get_conn() as conn:` for cleanup. **Python's `sqlite3`
context manager only manages the *transaction*** — `__exit__` commits or rolls
back and then deliberately **leaves the connection open**. So every
`db.fetchone` / `db.fetchall` / `db.execute` — and the ~30 fantasy write paths
that did `conn = db.get_conn()` with no close at all — leaked the connection's
file descriptors until CPython's GC happened to finalize the object.

Each page render fans out into many such calls (the fantasy `fdb` connections
are doubly expensive: they `ATTACH` the sim DB, so ~2 db handles + WAL/SHM each).
Under sustained load the open-file count drifted up against the process
`RLIMIT_NOFILE`, after which *any* new connection — even one only running a
`PRAGMA` — failed to open its files and raised `disk I/O error`.

Reproduced in a sandbox:

```
OLD pattern (with conn:, no close): fd delta over 200 conns = +401   # ~2 fds each, leaked
NEW db.fetchone (fixed):            fd delta over 200 calls = +1      # flat
```

## Fix

Two changes, both about *closing* connections; no query or schema logic touched.

1. **Central, covers every `with get_conn()` call site** (the read/write
   firehose: `db.fetchone/fetchall/execute/executemany`, plus sim, trades,
   streaks, injuries, init backfills, and the fantasy `fdb` helpers). `get_conn`
   now builds its connection from a `sqlite3.Connection` subclass,
   `_ManagedConnection`, whose `__exit__` runs the normal commit/rollback **and
   then closes**:

   ```python
   class _ManagedConnection(sqlite3.Connection):
       def __exit__(self, exc_type, exc_val, exc_tb):
           try:
               return super().__exit__(exc_type, exc_val, exc_tb)
           finally:
               self.close()
   ```

   Transaction semantics are preserved — a `with get_conn() as conn:` block still
   commits on success / rolls back on error; it just also closes now. Audited all
   25 `with get_conn()` sites first to confirm none reuse the connection after the
   block (which auto-close would break). `fdb.get_conn` opts in via the same
   `factory=`.

2. **The ~29 bare `conn = db.get_conn()` fantasy write sites** (which never used
   `with` at all) got an explicit `conn.close()` after their final `commit()`.
   For the settle paths that call `wallet.credit()` afterward, the close lands
   *before* the credit — which also matches the pre-existing "release the write
   lock before credit opens its own connection" intent.

## Validation

- All 12 changed files `py_compile` clean.
- Per-file `conn = db.get_conn()` count == `conn.close()` count (4/4, 3/3, …).
- Runtime fd test (temp DB via `O27V2_DB_PATH`): the subclass's `__exit__` closes
  the connection (a subsequent `execute` raises "Cannot operate on a closed
  database"), and **500 iterations of `fetchall` + `fetchone` + `execute` produce
  a net fd delta of 0** (was +1500-ish before).
- **Not run:** the Flask route smoke test and `pytest` — `flask`/`pytest` are
  absent in this sandbox (expected per CLAUDE.md). The behavioral change is
  connection lifecycle only; verified by the fd accounting above, not by a live
  request.

## Notes / not changed

- This is an fd-exhaustion fix. If the live box *also* has genuine disk pressure
  (a `-wal` that never checkpoints, a full volume), that is a separate ops issue;
  watch the volume's free space and WAL size after this lands. The fd leak was,
  however, sufficient on its own to produce exactly the observed errors.
- The exception path on the bare write sites is not `try/finally`-wrapped: if one
  of those rare writes raises before its `close()`, that single connection still
  leaks until GC. The **steady-state** leak (every successful read/write) is the
  one that exhausted fds, and that is closed. Left as-is to keep the diff a
  minimal, reviewable set of one-line additions rather than re-indenting every
  multi-line SQL literal.
