"""
Multiple named save slots (leagues) for O27v2.

Each save is its own SQLite file under a saves directory; a small JSON
registry tracks every save plus which one is "active". o27v2/db.py resolves
the active save's file for every connection, so all existing db.* callers
transparently read/write whichever league is active.

No accounts — saves are shared named slots with a single global active pointer.
"""
from __future__ import annotations
import os
import re
import json
import shutil
import sqlite3
import tempfile
import datetime
import uuid


def saves_dir() -> str:
    """Directory holding registry.json and the per-save .db files.

    Precedence: O27V2_SAVES_DIR, else <dir of O27V2_DB_PATH>/saves, else
    o27v2/saves (alongside this module). Created on demand.
    """
    d = os.environ.get("O27V2_SAVES_DIR")
    if not d:
        env_db = os.environ.get("O27V2_DB_PATH")
        if env_db:
            d = os.path.join(os.path.dirname(os.path.abspath(env_db)), "saves")
        else:
            d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saves")
    os.makedirs(d, exist_ok=True)
    return d


def _registry_path() -> str:
    return os.path.join(saves_dir(), "registry.json")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_registry() -> dict:
    """Read the registry, returning a safe empty default on any failure so a
    corrupt/missing registry never 500s the whole app."""
    try:
        with open(_registry_path()) as fh:
            reg = json.load(fh)
        if not isinstance(reg, dict) or "saves" not in reg:
            raise ValueError("malformed registry")
        reg.setdefault("active_id", None)
        reg.setdefault("saves", [])
        return reg
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return {"active_id": None, "saves": []}


def save_registry(reg: dict) -> None:
    """Atomic write — a half-written registry is exactly the kind of silent
    data-loss we are trying to eliminate."""
    path = _registry_path()
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(reg, fh, indent=2)
    os.replace(tmp, path)


def list_saves() -> list[dict]:
    saves = load_registry()["saves"]
    return sorted(saves, key=lambda s: s.get("last_played_at") or "", reverse=True)


def get_active_id() -> str | None:
    return load_registry().get("active_id")


def _find(reg: dict, save_id: str) -> dict | None:
    for s in reg["saves"]:
        if s["id"] == save_id:
            return s
    return None


def get_active_save() -> dict | None:
    reg = load_registry()
    return _find(reg, reg.get("active_id")) if reg.get("active_id") else None


def db_path_for(save_id: str) -> str:
    return os.path.join(saves_dir(), f"save_{save_id}.db")


def active_db_path() -> str | None:
    """File path of the active save, or None if no save is active. The file
    itself need not exist yet — a freshly created save's DB is created on
    first connect / init_db()."""
    sid = get_active_id()
    return db_path_for(sid) if sid else None


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def new_save(name: str, config_id: str, seed: int) -> str:
    """Register a brand-new save and make it active. Does NOT seed — the caller
    runs db.init_db()/seed_league against the now-active file."""
    reg = load_registry()
    sid = _new_id()
    now = _now()
    reg["saves"].append({
        "id": sid,
        "name": name,
        "config_id": config_id,
        "seed": seed,
        "created_at": now,
        "last_played_at": now,
        "filename": f"save_{sid}.db",
    })
    reg["active_id"] = sid
    save_registry(reg)
    return sid


def set_active(save_id: str) -> None:
    reg = load_registry()
    if not _find(reg, save_id):
        raise KeyError(f"unknown save: {save_id}")
    reg["active_id"] = save_id
    save_registry(reg)


def rename_save(save_id: str, new_name: str) -> None:
    reg = load_registry()
    s = _find(reg, save_id)
    if not s:
        raise KeyError(f"unknown save: {save_id}")
    s["name"] = new_name
    save_registry(reg)


def touch_save(save_id: str) -> None:
    reg = load_registry()
    s = _find(reg, save_id)
    if s:
        s["last_played_at"] = _now()
        save_registry(reg)


def _remove_db_files(save_id: str) -> None:
    base = db_path_for(save_id)
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(base + suffix)
        except FileNotFoundError:
            pass


def delete_save(save_id: str) -> None:
    """Delete a save and its DB files. Refuses to delete the last remaining
    save. If the deleted save was active, repoints to the most-recent
    remaining save."""
    reg = load_registry()
    s = _find(reg, save_id)
    if not s:
        raise KeyError(f"unknown save: {save_id}")
    if len(reg["saves"]) <= 1:
        raise ValueError("cannot delete the only save")
    reg["saves"] = [x for x in reg["saves"] if x["id"] != save_id]
    if reg.get("active_id") == save_id:
        remaining = sorted(reg["saves"],
                           key=lambda x: x.get("last_played_at") or "", reverse=True)
        reg["active_id"] = remaining[0]["id"] if remaining else None
    save_registry(reg)
    _remove_db_files(save_id)


def is_valid_save_db(path: str) -> bool:
    """True if path is a real O27 save (opens as SQLite and has a teams table)."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            conn.execute("SELECT COUNT(*) FROM teams").fetchone()
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        return False


def register_existing_file(src_path: str, name: str,
                           config_id: str | None = None,
                           seed: int | None = None) -> str:
    """Copy an existing .db file in as a new save (import / startup migration).
    Does not activate it."""
    reg = load_registry()
    sid = _new_id()
    dest = db_path_for(sid)
    shutil.copyfile(src_path, dest)
    now = _now()
    reg["saves"].append({
        "id": sid,
        "name": name,
        "config_id": config_id or "imported",
        "seed": seed,
        "created_at": now,
        "last_played_at": now,
        "filename": f"save_{sid}.db",
    })
    save_registry(reg)
    return sid


def snapshot_to(save_id: str, dest_path: str) -> None:
    """Write a clean single-file copy of a save via VACUUM INTO — merges any
    WAL contents and produces no -wal/-shm sidecars, so the export is safe to
    download while the DB is live."""
    src = db_path_for(save_id)
    if os.path.exists(dest_path):
        os.remove(dest_path)
    conn = sqlite3.connect(src)
    try:
        conn.execute("VACUUM INTO ?", (dest_path,))
    finally:
        conn.close()


_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def slug(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "").strip()).strip("-")
    return s or "league"
