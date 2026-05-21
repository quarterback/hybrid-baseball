"""Tests for the multiple-named-saves layer (o27v2/saves.py) and its
integration with o27v2/db.py's dynamic path resolution."""
from __future__ import annotations
import os
import sqlite3
import importlib
import tempfile
import shutil

import pytest


@pytest.fixture()
def env(monkeypatch):
    """Isolated saves dir + reloaded db/saves modules with no O27V2_DB_PATH
    override so the registry actually drives path resolution."""
    tmp = tempfile.mkdtemp()
    monkeypatch.delenv("O27V2_DB_PATH", raising=False)
    monkeypatch.setenv("O27V2_SAVES_DIR", os.path.join(tmp, "saves"))
    import o27v2.db as db
    import o27v2.saves as saves
    importlib.reload(db)
    importlib.reload(saves)
    yield db, saves
    shutil.rmtree(tmp, ignore_errors=True)


def _seed_minimal(db):
    """Create a teams table with one row in the active DB."""
    db.execute("CREATE TABLE IF NOT EXISTS teams (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO teams (name) VALUES (?)", ("Test Club",))


def test_new_save_sets_active_and_routes_writes(env):
    db, saves = env
    sid = saves.new_save("Alpha", "30teams", 42)
    assert saves.get_active_id() == sid
    assert db._resolve_path() == saves.db_path_for(sid)
    _seed_minimal(db)
    assert os.path.exists(saves.db_path_for(sid))
    row = db.fetchone("SELECT COUNT(*) AS n FROM teams")
    assert row["n"] == 1


def test_set_active_switches_file(env):
    db, saves = env
    a = saves.new_save("A", "x", 1)
    _seed_minimal(db)
    db.execute("INSERT INTO teams (name) VALUES (?)", ("A-only",))
    b = saves.new_save("B", "x", 2)
    _seed_minimal(db)  # b starts fresh
    assert db.fetchone("SELECT COUNT(*) AS n FROM teams")["n"] == 1
    saves.set_active(a)
    assert db.fetchone("SELECT COUNT(*) AS n FROM teams")["n"] == 2
    assert saves.get_active_id() == a
    saves.set_active(b)
    assert db.fetchone("SELECT COUNT(*) AS n FROM teams")["n"] == 1


def test_delete_removes_files_and_repoints(env):
    db, saves = env
    a = saves.new_save("A", "x", 1); _seed_minimal(db)
    b = saves.new_save("B", "x", 2); _seed_minimal(db)
    assert saves.get_active_id() == b
    saves.delete_save(b)
    assert not os.path.exists(saves.db_path_for(b))
    assert saves.get_active_id() == a
    with pytest.raises(ValueError):
        saves.delete_save(a)  # last remaining


def test_is_valid_save_db(env):
    db, saves = env
    saves.new_save("A", "x", 1); _seed_minimal(db)
    good = db._resolve_path()
    assert saves.is_valid_save_db(good)
    # A sqlite file without a teams table.
    fd, noteams = tempfile.mkstemp(suffix=".db"); os.close(fd)
    c = sqlite3.connect(noteams); c.execute("CREATE TABLE foo (x)"); c.commit(); c.close()
    assert not saves.is_valid_save_db(noteams)
    # A non-sqlite file.
    fd, junk = tempfile.mkstemp(); os.close(fd)
    with open(junk, "w") as f:
        f.write("not a database")
    assert not saves.is_valid_save_db(junk)


def test_export_snapshot_is_clean_single_file(env):
    db, saves = env
    sid = saves.new_save("A", "x", 1); _seed_minimal(db)
    fd, dest = tempfile.mkstemp(suffix=".db"); os.close(fd); os.remove(dest)
    saves.snapshot_to(sid, dest)
    assert os.path.exists(dest)
    assert not os.path.exists(dest + "-wal")
    assert saves.is_valid_save_db(dest)


def test_import_round_trip(env):
    db, saves = env
    sid = saves.new_save("Original", "x", 7); _seed_minimal(db)
    db.execute("INSERT INTO teams (name) VALUES (?)", ("Extra",))
    fd, exported = tempfile.mkstemp(suffix=".db"); os.close(fd); os.remove(exported)
    saves.snapshot_to(sid, exported)
    new_id = saves.register_existing_file(exported, "Imported")
    assert new_id != sid
    saves.set_active(new_id)
    assert db.fetchone("SELECT COUNT(*) AS n FROM teams")["n"] == 2


def test_env_override_bypasses_registry(monkeypatch):
    """With O27V2_DB_PATH set, set_active must not change which file is opened."""
    fd, fixed = tempfile.mkstemp(suffix=".db"); os.close(fd)
    monkeypatch.setenv("O27V2_DB_PATH", fixed)
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("O27V2_SAVES_DIR", os.path.join(tmp, "saves"))
    import o27v2.db as db
    import o27v2.saves as saves
    importlib.reload(db)
    importlib.reload(saves)
    try:
        sid = saves.new_save("Ignored", "x", 1)
        assert db._resolve_path() == fixed
        saves.set_active(sid)
        assert db._resolve_path() == fixed
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.remove(fixed)
        monkeypatch.delenv("O27V2_DB_PATH", raising=False)
        importlib.reload(db)
        importlib.reload(saves)
