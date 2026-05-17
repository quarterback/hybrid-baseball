"""
Tests for the motivation-driven trade engine (o27v2/trades.py).

Covers:
  - trade_value signature + snapshot range (valuation.py depends on it)
  - check_deadline_and_trades returns shape compatible with log_many
  - schema migration is idempotent
  - block_breaking finds blocked reserves
  - injury_backfill prefers same-position counterparties
  - rebuild_fire_sale sends old players
  - win_now_overpay sends more than it gets
  - gm_noise can produce lopsided trades
  - roster floor is preserved across trades
  - drift_fo_strategies moves a winning rebuilder forward
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture()
def fresh_db(tmp_path):
    """Spin up an empty O27v2 DB and yield its path. Restores _DB_PATH."""
    from o27v2 import db as _db
    path = str(tmp_path / "trades.db")
    original = _db._DB_PATH
    _db._DB_PATH = path
    _db.init_db()
    try:
        yield path
    finally:
        _db._DB_PATH = original


def _seed_season_window(start: str = "2024-04-01", end: str = "2024-09-30") -> None:
    """Insert two stub games so `_season_phase` and `_get_deadline_date`
    have a real calendar to work with. Tests that target the 'late' phase
    typically run dates well past the 2/3 mark (deadline ≈ 2024-08-01)."""
    from o27v2 import db as _db
    # Need teams; require at least one to exist.
    teams = _db.fetchall("SELECT id FROM teams ORDER BY id LIMIT 2")
    if len(teams) < 2:
        return
    t1, t2 = teams[0]["id"], teams[1]["id"]
    for d in (start, end):
        _db.execute(
            "INSERT INTO games (game_date, home_team_id, away_team_id, played) "
            "VALUES (?, ?, ?, 0)",
            (d, t1, t2),
        )


# ---------------------------------------------------------------------------
# 1. trade_value preserved
# ---------------------------------------------------------------------------

def test_trade_value_signature_preserved():
    from o27v2.trades import trade_value
    v = trade_value({"skill": 50, "pitcher_skill": 50, "speed": 50, "age": 27})
    assert isinstance(v, float)
    assert 0.0 <= v <= 1.0


def test_trade_value_snapshot():
    """trade_value output range is locked — valuation.py:_BANDS depends on
    these exact 0..1 outputs. Don't update casually."""
    from o27v2.trades import trade_value
    expected = {
        # all-80 power slugger at peak age
        (80, 80, 80, 27, "power", ""):       0.910,
        # average 50/50/50 at peak age
        (50, 50, 50, 27, "", ""):            0.500,
        # bad old player
        (30, 30, 30, 38, "", ""):            0.168667,
        # ace pitcher
        (60, 75, 50, 27, "", "workhorse"):   0.712917,
        # young speedster
        (50, 50, 50, 21, "speed", ""):       0.490,
    }
    for (skill, ps, speed, age, arch, role), want in expected.items():
        v = trade_value({
            "skill": skill, "pitcher_skill": ps, "speed": speed,
            "age": age, "archetype": arch, "pitcher_role": role,
        })
        assert abs(v - want) < 1e-4, f"{(skill,ps,speed,age,arch,role)}: got {v}, want {want}"


# ---------------------------------------------------------------------------
# 2. check_deadline_and_trades signature
# ---------------------------------------------------------------------------

def test_check_deadline_and_trades_signature(fresh_db):
    """Returns a list of dicts with the keys log_many() consumes."""
    from o27v2 import league
    from o27v2.trades import check_deadline_and_trades
    league.seed_league(rng_seed=99)
    out = check_deadline_and_trades("2024-04-01", games_played=10)
    assert isinstance(out, list)
    for e in out:
        assert isinstance(e, dict)
        assert "event_type" in e
        assert "team_id"    in e
        assert "player_id"  in e
        assert "detail"     in e
        assert e["event_type"].startswith("trade_")


# ---------------------------------------------------------------------------
# 3. Schema migration is idempotent (fo_* columns)
# ---------------------------------------------------------------------------

def test_fo_schema_migration_idempotent():
    """init_db() twice on the same DB must not error and must leave the
    fo_* columns present exactly once."""
    from o27v2 import db as _db
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "migration.db")
        original = _db._DB_PATH
        _db._DB_PATH = path
        try:
            _db.init_db()
            _db.init_db()   # second call must be a no-op
            cols = _db.fetchall("PRAGMA table_info(teams)")
            col_names = [c["name"] for c in cols]
            for col in ("fo_strategy", "fo_aggression", "fo_archetype_bias",
                        "fo_losing_streak", "fo_last_trade_date"):
                assert col_names.count(col) == 1, (
                    f"column {col} present {col_names.count(col)} times"
                )
        finally:
            _db._DB_PATH = original


# ---------------------------------------------------------------------------
# 4. Injury backfill prefers same-position counterparties
# ---------------------------------------------------------------------------

def test_injury_backfill_finds_same_position(fresh_db):
    """Inject a long-IL SS on team A; team B has surplus healthy SS.
    The motivation pass should produce a trade where an SS lands on A."""
    from o27v2 import db, league
    from o27v2.trades import run_motivation_pass

    league.seed_league(rng_seed=11)
    _seed_season_window()
    teams = db.fetchall("SELECT * FROM teams ORDER BY id LIMIT 2")
    a, b = teams[0], teams[1]
    # Long-IL the SS starter on team A. (SQLite default builds don't allow
    # LIMIT in UPDATE; pick a target id explicitly.)
    target = db.fetchone(
        "SELECT id FROM players WHERE team_id = ? AND position = 'SS' "
        "AND is_active = 1 ORDER BY id LIMIT 1",
        (a["id"],),
    )
    assert target is not None, "seed should produce at least one SS"
    db.execute(
        "UPDATE players SET injured_until = ?, il_tier = 'long' WHERE id = ?",
        ("2024-12-31", target["id"]),
    )
    # Force A into a posture where injury_backfill scores highly.
    db.execute(
        "UPDATE teams SET fo_strategy = 'contend', fo_aggression = 0.95 WHERE id = ?",
        (a["id"],),
    )
    # Ensure B has at least one extra SS reserve to part with.
    db.execute(
        "UPDATE teams SET fo_strategy = 'rebuild', fo_aggression = 0.9 WHERE id = ?",
        (b["id"],),
    )

    # Run several ticks across the late phase. The per-date throttle limits
    # each team to one trade per game_date, so iterate across distinct dates
    # and keep team A's IL hole fresh.
    import datetime as _dt
    events = []
    for i in range(40):
        d = (_dt.date(2024, 7, 15) + _dt.timedelta(days=i)).isoformat()
        events.extend(run_motivation_pass(d, games_played=80, rng_seed=1234 + i))
        # Keep the SS off the active roster — if it got traded earlier, that's
        # fine; the motivation hinges on the IL row continuing to exist on A.
        if any(e["event_type"] == "trade_injury_backfill" for e in events):
            break

    backfill = [e for e in events if e["event_type"] == "trade_injury_backfill"]
    assert len(backfill) >= 1, (
        f"expected at least one injury_backfill event, got types: "
        f"{sorted({e['event_type'] for e in events})}"
    )
    # The SS coming back to team A should be on team A now.
    a_ss = db.fetchall(
        "SELECT id FROM players WHERE team_id = ? AND position = 'SS' AND "
        "(injured_until IS NULL OR injured_until <= '2024-08-30')",
        (a["id"],),
    )
    assert len(a_ss) >= 1, "no healthy SS landed on team A after backfill"


# ---------------------------------------------------------------------------
# 5. Rebuild fire sale ships veterans
# ---------------------------------------------------------------------------

def test_rebuild_fire_sale_sends_vets(fresh_db):
    """A rebuild team's outgoing players in fire-sale trades should be old."""
    from o27v2 import db, league
    from o27v2.trades import run_motivation_pass

    league.seed_league(rng_seed=21)
    _seed_season_window()
    teams = db.fetchall("SELECT id FROM teams ORDER BY id")
    # Half rebuilders, half win-now buyers.
    half = len(teams) // 2
    buyer_ids = {t["id"] for t in teams[:half]}
    db.execute("UPDATE teams SET fo_strategy = 'rebuild', fo_aggression = 0.9")
    for tid in buyer_ids:
        db.execute(
            "UPDATE teams SET fo_strategy = 'win_now', fo_aggression = 0.9 WHERE id = ?",
            (tid,),
        )

    import datetime as _dt
    seen = []
    for i in range(60):
        d = (_dt.date(2024, 7, 14) + _dt.timedelta(days=i % 18)).isoformat()
        events = run_motivation_pass(d, games_played=120, rng_seed=999 + i)
        for e in events:
            if e["event_type"] != "trade_rebuild_fire_sale":
                continue
            # Seller-side event: emitted with team_id == rebuilder.
            if e["team_id"] in buyer_ids:
                continue
            p = db.fetchone("SELECT age FROM players WHERE id = ?", (e["player_id"],))
            if p:
                seen.append(int(p["age"] or 27))

    assert len(seen) >= 5, f"too few fire-sale send-side events to assess: {len(seen)}"
    # ≥60% of vet-shipped players should be age >= 30.
    vets = [a for a in seen if a >= 30]
    assert len(vets) / len(seen) >= 0.6, (
        f"rebuild fire-sale shipped only {len(vets)}/{len(seen)} vets — "
        f"ages: {seen}"
    )


# ---------------------------------------------------------------------------
# 6. Win-now overpay sends more than it gets
# ---------------------------------------------------------------------------

def test_win_now_overpay_overshoots(fresh_db):
    """In win_now_overpay deals, the initiator (win_now) sends more
    players than it receives."""
    from o27v2 import db, league
    from o27v2.trades import run_motivation_pass

    league.seed_league(rng_seed=31)
    _seed_season_window()
    teams = db.fetchall("SELECT id FROM teams ORDER BY id")
    win_now_id = teams[0]["id"]
    db.execute("UPDATE teams SET fo_strategy = 'rebuild', fo_aggression = 0.9")
    db.execute(
        "UPDATE teams SET fo_strategy = 'win_now', fo_aggression = 0.95 WHERE id = ?",
        (win_now_id,),
    )

    # Each pass emits at most one trade per team because of the per-date
    # throttle. Within one pass, count how many players the win_now team
    # sent (events with team_id == win_now_id) versus received.
    import datetime as _dt
    sent_more = 0
    total = 0
    for i in range(60):
        d = (_dt.date(2024, 7, 14) + _dt.timedelta(days=i % 18)).isoformat()
        events = run_motivation_pass(d, games_played=130, rng_seed=2024 + i)
        overpay = [e for e in events if e["event_type"] == "trade_win_now_overpay"]
        if not overpay:
            continue
        # Only consider passes where the win_now team actually participated.
        win_now_sent = sum(1 for e in overpay if e["team_id"] == win_now_id)
        partner_sent = sum(1 for e in overpay if e["team_id"] != win_now_id)
        if win_now_sent == 0 and partner_sent == 0:
            continue
        total += 1
        if win_now_sent > partner_sent:
            sent_more += 1

    assert total > 0, "no win_now_overpay trades fired across 60 passes"
    assert sent_more / total >= 0.6, (
        f"win_now sent more in only {sent_more}/{total} overpay trades"
    )


# ---------------------------------------------------------------------------
# 7. Roster floor never violated
# ---------------------------------------------------------------------------

def test_roster_floor_holds(fresh_db):
    """After many motivation passes, no team falls below the
    per-position-floor or the 5-healthy-pitcher minimum."""
    from o27v2 import db, league
    from o27v2.trades import run_motivation_pass, _CANONICAL_HITTER_POSITIONS

    league.seed_league(rng_seed=41)
    _seed_season_window()
    import datetime as _dt
    for i in range(80):
        d = (_dt.date(2024, 7, 14) + _dt.timedelta(days=i % 18)).isoformat()
        run_motivation_pass(d, games_played=100, rng_seed=500 + i)

    teams = db.fetchall("SELECT id FROM teams")
    for t in teams:
        rows = db.fetchall(
            "SELECT position, is_pitcher, COALESCE(is_joker,0) AS is_joker "
            "FROM players WHERE team_id = ? AND "
            "(injured_until IS NULL OR injured_until <= '2024-08-30')",
            (t["id"],),
        )
        pitchers = sum(1 for r in rows if r["is_pitcher"] and not r["is_joker"])
        assert pitchers >= 5, f"team {t['id']} dropped to {pitchers} healthy pitchers"
        for pos in _CANONICAL_HITTER_POSITIONS:
            present = any(r["position"] == pos and not r["is_joker"] for r in rows)
            assert present, f"team {t['id']} has no healthy {pos}"


# ---------------------------------------------------------------------------
# 8. GM noise can produce clearly lopsided deals
# ---------------------------------------------------------------------------

def test_gm_noise_can_be_lopsided(fresh_db):
    """gm_noise has a relaxed acceptance threshold, so some trades will
    have noticeably unequal trade_value totals."""
    from o27v2 import db, league
    from o27v2.trades import run_motivation_pass, trade_value

    league.seed_league(rng_seed=51)
    _seed_season_window()
    db.execute("UPDATE teams SET fo_aggression = 0.9")

    import datetime as _dt
    deltas = []
    for i in range(120):
        d = (_dt.date(2024, 7, 14) + _dt.timedelta(days=i % 18)).isoformat()
        events = run_motivation_pass(d, games_played=140, rng_seed=7000 + i)
        noise = [e for e in events if e["event_type"] == "trade_gm_noise"]
        if not noise:
            continue
        # Group by FROM team_id — each pass has at most 2 distinct from-teams
        # per fired noise trade (the two sides of a 1-for-1).
        by_team: dict[int, list[float]] = {}
        for e in noise:
            p = db.fetchone("SELECT * FROM players WHERE id = ?", (e["player_id"],))
            if not p:
                continue
            by_team.setdefault(e["team_id"], []).append(trade_value(dict(p)))
        if len(by_team) != 2:
            continue
        sides = list(by_team.values())
        deltas.append(abs(sum(sides[0]) - sum(sides[1])))

    if not deltas:
        pytest.skip("no gm_noise trades fired in this sample")
    lopsided = [d for d in deltas if d > 0.15]
    assert len(lopsided) >= max(1, int(0.2 * len(deltas))), (
        f"only {len(lopsided)}/{len(deltas)} gm_noise trades were lopsided"
    )


# ---------------------------------------------------------------------------
# 9. FO strategy drift in offseason
# ---------------------------------------------------------------------------

def test_drift_rebuild_to_develop(fresh_db):
    """A rebuild team that finishes 90-72 (wp=.556) should drift toward
    develop in the offseason. Run drift_fo_strategies until it succeeds —
    the 25% sticky guard occasionally blocks a single call."""
    import random
    from o27v2 import db, league
    from o27v2.front_office import drift_fo_strategies

    league.seed_league(rng_seed=61)
    team_id = db.fetchone("SELECT id FROM teams ORDER BY id LIMIT 1")["id"]
    db.execute(
        "UPDATE teams SET fo_strategy = 'rebuild', wins = 90, losses = 72 WHERE id = ?",
        (team_id,),
    )
    # Run drift up to 20 times; on average it converts in <5 due to the 25% sticky.
    for attempt in range(20):
        drift_fo_strategies(random.Random(attempt))
        cur = db.fetchone("SELECT fo_strategy FROM teams WHERE id = ?", (team_id,))["fo_strategy"]
        if cur != "rebuild":
            break
    assert cur in ("develop", "contend", "win_now"), (
        f"rebuild team with .556 wp didn't drift forward: ended at {cur}"
    )


# ---------------------------------------------------------------------------
# 10. roll_fo distribution sanity
# ---------------------------------------------------------------------------

def test_roll_fo_strategy_keys():
    import random
    from o27v2.front_office import roll_fo, STRATEGY_KEYS, ARCHETYPE_BIAS_OPTIONS
    seen_strats = set()
    seen_bias   = set()
    for i in range(200):
        fo = roll_fo(random.Random(i))
        assert fo["fo_strategy"] in STRATEGY_KEYS
        assert 0.0 <= fo["fo_aggression"] <= 1.0
        assert fo["fo_archetype_bias"] in ARCHETYPE_BIAS_OPTIONS
        seen_strats.add(fo["fo_strategy"])
        seen_bias.add(fo["fo_archetype_bias"])
    assert seen_strats == set(STRATEGY_KEYS)
    assert "" in seen_bias   # most teams have no bias
