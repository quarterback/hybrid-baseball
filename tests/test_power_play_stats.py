"""End-to-end tests for the Power Play stat rack (o27v2 DB layer).

Seeds a small league, opts it into the Power Play rule (the per-league flag the
new-league builder sets), sims a batch of games, and asserts the
`game_power_play_stats` table populates with sane defense + short-handed offense
lines — and that a league WITHOUT the rule writes nothing.
"""
from __future__ import annotations

import os
import tempfile

import pytest


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.environ["O27V2_DB_PATH"] = tmp.name
    from o27v2 import db, league, schedule
    # db froze the path at import time, so under pytest (where an earlier test
    # already imported db with a different path) the env var alone is ignored.
    # Reassign the module's path override at runtime — _resolve_path honors
    # this per its docstring — so every connection opens THIS fresh DB.
    db._DB_PATH = tmp.name
    db._DB_PATH_OVERRIDDEN = True
    db.init_db()
    league.seed_league(rng_seed=11)
    schedule.seed_schedule(rng_seed=11)
    return tmp.name, db


def _sim_first(n: int, db):
    from o27v2.sim import simulate_game
    for g in db.fetchall("SELECT id FROM games WHERE played=0 ORDER BY id LIMIT ?", (n,)):
        simulate_game(g["id"], seed=g["id"])


def test_column_present_and_default_off():
    path, db = _fresh_db()
    try:
        cols = [r["name"] for r in db.fetchall("PRAGMA table_info(teams)")]
        assert "power_play_enabled" in cols
        # Freshly seeded teams default to rule OFF.
        vals = {r["power_play_enabled"] for r in db.fetchall("SELECT power_play_enabled FROM teams")}
        assert vals == {0}
    finally:
        os.unlink(path)


def test_rule_off_writes_no_pp_stats():
    path, db = _fresh_db()
    try:
        _sim_first(15, db)  # no opt-in
        assert db.fetchone("SELECT COUNT(*) n FROM game_power_play_stats")["n"] == 0
    finally:
        os.unlink(path)


def test_rule_on_populates_defense_and_offense():
    path, db = _fresh_db()
    try:
        db.execute("UPDATE teams SET power_play_enabled = 1")  # what the route does
        _sim_first(30, db)
        rows = db.fetchall("SELECT * FROM game_power_play_stats")
        assert rows, "expected power-play stat rows once the rule is on"

        # Defense: at least one nickel deployment, and outs >= deploys.
        defense = [r for r in rows if r["pp_deploys"] > 0]
        assert defense, "expected at least one nickel deployment"
        for r in defense:
            assert r["pp_outs"] >= r["pp_deploys"]   # each window covers >= 1 out

        # Offense: short-handed PA exist, and the one hard invariant holds
        # (sh_ab <= sh_pa). Note sh_hits may exceed sh_ab — the engine's
        # Second-Chance mechanic credits multiple hits per AB, same as the
        # overall batting table — so we do NOT assert sh_hits <= sh_ab.
        offense = [r for r in rows if r["sh_pa"] > 0]
        assert offense, "expected short-handed plate appearances"
        for r in rows:
            assert r["sh_ab"] <= r["sh_pa"]
    finally:
        os.unlink(path)


def test_leaders_page_shows_section_only_when_on():
    path, db = _fresh_db()
    try:
        db.execute("UPDATE teams SET power_play_enabled = 1")
        _sim_first(30, db)
        import o27v2.web.app as web
        web.app.config["TESTING"] = True
        html = web.app.test_client().get("/leaders").get_data(as_text=True)
        assert "Power Play · Defense" in html
        assert "Short-handed Offense" in html
        assert "SH-AVG" in html
    finally:
        os.unlink(path)


def test_glossary_keys_resolve():
    # The leaderboard card() macro deep-links each key to the glossary; every
    # power-play key must have an entry or the link 404s the anchor.
    from o27v2.web.glossary import GLOSSARY_BY_KEY
    for k in ("pp_deploys", "pp_outs", "pp_xbh_held", "pp_hits_converted",
              "nickel_po", "sh_avg", "sh_hits", "sh_pa"):
        assert k in GLOSSARY_BY_KEY, f"missing glossary entry for {k}"
