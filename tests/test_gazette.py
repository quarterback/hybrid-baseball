"""Tests for The O27 Gazette (o27.gazette).

Seeds a small league, sims a batch, then checks that:
  - the structured daily payload serializes with the expected shape,
  - inflection points are real WP swings drawn from the pa_log,
  - every writer voice composes a prompt that obeys the O27 vocabulary
    rules (no MLB "innings", mechanic named "second-chance at-bat"),
  - the data feed and the prose layer are decoupled (a hand-built payload
    renders a prompt with no DB access),
  - the Flask blueprint serves the page and the .txt / .json exports.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.environ["O27V2_DB_PATH"] = tmp.name
    from o27v2 import db, league, schedule
    # db froze the path at import time; reassign the runtime override so every
    # connection opens THIS fresh DB (see test_power_play_stats for the why).
    db._DB_PATH = tmp.name
    db._DB_PATH_OVERRIDDEN = True
    db.init_db()
    league.seed_league(rng_seed=7)
    schedule.seed_schedule(rng_seed=7)
    return tmp.name, db


def _sim_first(n: int, db):
    from o27v2.sim import simulate_game
    for g in db.fetchall("SELECT id FROM games WHERE played=0 ORDER BY id LIMIT ?", (n,)):
        simulate_game(g["id"], seed=g["id"])


def test_payload_shape_and_inflection_points():
    path, db = _fresh_db()
    try:
        _sim_first(40, db)
        import o27.gazette as gz
        date = gz.latest_slate_date()
        assert date, "expected at least one played slate"
        payload = gz.build_daily_payload(date)
        assert payload["games_played"] >= 1
        g = payload["games"][0]
        # Core structure the prompt depends on.
        for key in ("matchup", "final", "inflection_points", "scoring",
                    "standouts", "went_to_extras"):
            assert key in g
        assert g["final"]["winner"] in (g["matchup"]["away"]["abbrev"],
                                        g["matchup"]["home"]["abbrev"])
        # Inflection points carry a signed WP swing attributed to a team.
        for ip in g["inflection_points"]:
            assert "win_prob_swing_pct" in ip and "swing_for" in ip
            assert isinstance(ip["win_prob_swing_pct"], float)
    finally:
        os.unlink(path)


def test_every_voice_obeys_the_brief():
    path, db = _fresh_db()
    try:
        _sim_first(40, db)
        import o27.gazette as gz
        payload = gz.build_daily_payload(gz.latest_slate_date())
        for v in gz.all_voices():
            prompt = gz.build_prompt(payload, voice=v.id)
            low = prompt.lower()
            # The mechanic must be named correctly...
            assert "second-chance at-bat" in low
            # ...and the prompt must instruct against the wrong name. The only
            # place "stay mechanic" may appear is inside that prohibition.
            assert low.count("stay mechanic") <= 1
            # No MLB inning framing leaks into the brief.
            assert "no innings" in low
    finally:
        os.unlink(path)


def test_data_and_prose_layers_are_decoupled():
    # build_prompt takes a payload dict; a hand-built one needs no DB at all.
    from o27.gazette import build_prompt
    fake = {
        "publication": "The O27 Gazette",
        "edition_date": "2099-01-01",
        "games_played": 1,
        "games": [{"matchup": {"away": {"name": "Hand Edited"}}}],
    }
    out = build_prompt(fake, voice="wire")
    assert "Hand Edited" in out
    assert "2099-01-01" in out


def test_user_voices_file_extends_roster(tmp_path):
    vf = tmp_path / "voices.json"
    vf.write_text(json.dumps({
        "poet": {"name": "The Poet", "blurb": "verse",
                 "persona": "Write it as free verse."}
    }))
    os.environ["O27_GAZETTE_VOICES"] = str(vf)
    try:
        # Reload so the module re-reads the env-pointed file.
        import importlib
        from o27.gazette import voices as _v
        importlib.reload(_v)
        ids = {v.id for v in _v.all_voices()}
        assert "poet" in ids and "beat" in ids  # user voice + builtins
        assert _v.get_voice("poet").name == "The Poet"
    finally:
        del os.environ["O27_GAZETTE_VOICES"]
        import importlib
        from o27.gazette import voices as _v
        importlib.reload(_v)


def test_blueprint_serves_page_and_exports():
    path, db = _fresh_db()
    try:
        _sim_first(40, db)
        import o27v2.web.app as web
        web.app.config["TESTING"] = True
        c = web.app.test_client()
        assert c.get("/gazette/").status_code == 200
        assert c.get("/gazette/?voice=scribe").status_code == 200
        txt = c.get("/gazette/export.txt?voice=homer")
        assert txt.status_code == 200 and len(txt.get_data()) > 100
        js = c.get("/gazette/export.json")
        assert js.status_code == 200
        assert js.get_json()["publication"] == "The O27 Gazette"
    finally:
        os.unlink(path)


def test_render_not_configured_raises_and_page_degrades():
    path, db = _fresh_db()
    os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        _sim_first(40, db)
        import o27.gazette as gz
        from o27.gazette import render
        assert render.is_configured() is False
        payload = gz.build_daily_payload(gz.latest_slate_date())
        with pytest.raises(render.GazetteNotConfigured):
            render.generate(payload, "beat")
        # The page still serves and tells the user how to enable generation.
        import o27v2.web.app as web
        web.app.config["TESTING"] = True
        html = web.app.test_client().get("/gazette/").get_data(as_text=True)
        assert "ANTHROPIC_API_KEY" in html
    finally:
        os.unlink(path)


def test_article_cache_roundtrip():
    path, db = _fresh_db()
    try:
        from o27.gazette import render
        assert render.get_cached("2026-04-17", "beat") is None
        render._save("2026-04-17", "beat", "claude-opus-4-8", "HEADLINE\n\nBody.")
        row = render.get_cached("2026-04-17", "beat")
        assert row and row["article"].startswith("HEADLINE")
        assert row["model"] == "claude-opus-4-8"
        # Upsert replaces, doesn't duplicate.
        render._save("2026-04-17", "beat", "claude-opus-4-8", "NEW BODY")
        assert render.get_cached("2026-04-17", "beat")["article"] == "NEW BODY"
        n = db.fetchone("SELECT COUNT(*) AS n FROM gazette_articles")["n"]
        assert n == 1
    finally:
        os.unlink(path)
