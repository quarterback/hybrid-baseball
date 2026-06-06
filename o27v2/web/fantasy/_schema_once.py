"""One-time-per-DB schema guard for CapSpace's lazy ``CREATE TABLE`` blocks.

Every fantasy module used to run its ``ensure_schema()`` —
``executescript(...) + commit()``, i.e. a full WAL **write** transaction (and a
leaked ``get_conn()`` connection) — on *every* call. A single ``/fantasy`` load
(or one background poll of ``/api/wallet`` / ``/api/activity``) fans out into
dozens of these via ``_settle_all()`` (seven games), the wallet reads, and the
contest list. The cost showed up two ways:

* **Lag** — dozens of synchronous write+commit round-trips per request,
  expensive on a networked volume.
* **``database is locked``** — those write transactions contend with a running
  sim's writes; under load one side loses the WAL writer lock.

The DDL is pure ``CREATE TABLE/INDEX IF NOT EXISTS`` (+ idempotent ``ALTER``),
so it only needs to run **once per save per process**. This memoizes "schema
built" keyed by the resolved DB path, so switching the active save (a new path)
still triggers a fresh build, while repeat calls against the same DB are no-ops.
"""

from __future__ import annotations

import functools

from o27v2 import db

# (resolved_db_path, module) -> already built this process
_built: set[tuple[str, str]] = set()


def once(fn):
    """Decorate an ``ensure_schema()`` so its body runs at most once per
    (resolved DB path, defining module) within this process."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            path = db._resolve_path()
        except Exception:
            path = "?"
        key = (path, fn.__module__)
        if key in _built:
            return None
        out = fn(*args, **kwargs)
        # Only mark built on success — a failed build (e.g. transient lock)
        # should be retried on the next call rather than silently skipped.
        _built.add(key)
        return out

    return wrapper


def reset() -> None:
    """Forget which schemas were built. Test hook for suites that drop the
    CapSpace tables and expect the next ``ensure_schema()`` to rebuild them."""
    _built.clear()
