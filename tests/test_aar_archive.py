"""
AAR archive — transactions + auction ledger should survive season close.

Phase E follow-up: the live `transactions` and `auction_results` tables
get cleared at season reset, but season_archive snapshots them into
season_transactions / season_auction_results keyed by season_id, so the
season detail page can render the full economy + trade history for any
archived season.
"""
import datetime as _dt

import pytest

import o27v2.db as db
import o27v2.season_archive as sa


@pytest.fixture()
def fresh_db(tmp_path):
    original = db._DB_PATH
    db._DB_PATH = str(tmp_path / "aar.db")
    try:
        db.init_db()
        yield
    finally:
        db._DB_PATH = original


def _add_team(name, abbrev):
    return db.execute(
        "INSERT INTO teams (name, abbrev, city, league, division, "
        "org_strength, mgr_quick_hook, mgr_bullpen_aggression, mgr_joker_aggression) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (name, abbrev, "C", "Pro", "East", 50, 0.5, 0.5, 0.5),
    )


def _add_player(tid, name, pos="SS", ovr=70):
    return db.execute(
        "INSERT INTO players (name, position, is_pitcher, is_active, "
        "skill, contact, power, eye, speed, pitcher_skill, command, movement, stamina, "
        "defense, arm, baserunning, run_aggressiveness, "
        "defense_infield, defense_outfield, defense_catcher, archetype, "
        "stay_aggressiveness, contact_quality_threshold, team_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, pos, 0, 1, ovr, ovr, ovr, ovr, ovr, 0, 0, 0, 60,
         60, 60, 60, 60, 60, 60, 60, "", 0.3, 0.5, tid),
    )


def _make_archivable_season() -> int:
    """Create a games row + season_archive metadata so archive_current_season
    has the minimum it needs to write a `seasons` row."""
    # archive_current_season requires at least one played game
    db.execute(
        "INSERT INTO games (game_date, home_team_id, away_team_id, "
        "                   home_score, away_score, played) "
        "VALUES (?, 1, 2, 3, 2, 1)",
        ("2025-04-01",),
    )
    sa.set_active_league_meta(42, "default")
    sid = sa.archive_current_season(run_invariants=False)
    assert sid is not None
    return sid


def test_transactions_snapshot_into_archive(fresh_db):
    """After archive_current_season, every row in `transactions` should
    have a mirror row in `season_transactions` keyed to the new season_id,
    with team_abbrev + player_name denormalised."""
    t1 = _add_team("Alpha", "ALF")
    t2 = _add_team("Beta",  "BET")
    p1 = _add_player(t1, "Player One")
    p2 = _add_player(t2, "Player Two")
    # Seed a handful of transaction events covering different types
    rows = [
        ("2025-03-15", "auction_sign", t1, p1, "Won at auction ƒ50,000"),
        ("2025-03-15", "fa_sign",      t2, p2, "Signed from FA pool ƒ20,000"),
        ("2025-04-01", "trade",        t1, p2, "Trade · blockbuster — P2 BET→ALF"),
        ("2025-04-01", "trade",        t2, p1, "Trade · blockbuster — P1 ALF→BET"),
    ]
    for (gd, et, tid, pid, det) in rows:
        db.execute(
            "INSERT INTO transactions (season, game_date, event_type, "
            "team_id, player_id, detail) VALUES (?,?,?,?,?,?)",
            (1, gd, et, tid, pid, det),
        )

    sid = _make_archivable_season()
    snap = db.fetchall(
        "SELECT * FROM season_transactions WHERE season_id = ? ORDER BY id",
        (sid,),
    )
    assert len(snap) == 4
    # All four event types preserved
    types = {r["event_type"] for r in snap}
    assert types == {"auction_sign", "fa_sign", "trade"}
    # Team abbrev + player name denormalised
    by_pid = {(r["player_id"], r["team_id"]): r for r in snap}
    auc = by_pid[(p1, t1)]
    assert auc["team_abbrev"] == "ALF"
    assert auc["player_name"] == "Player One"
    # Detail string preserved verbatim
    trades = [r for r in snap if r["event_type"] == "trade"]
    assert all("blockbuster" in r["detail"] for r in trades)


def test_auction_results_snapshot_into_archive(fresh_db):
    """auction_results rows get copied into season_auction_results with
    abbrevs and player names denormalised so the archived season's
    auction summary still renders after roster wipes."""
    from o27v2 import auction as _au
    _au.init_auction_schema()
    t1 = _add_team("Alpha", "ALF")
    t2 = _add_team("Beta",  "BET")
    p1 = _add_player(t1, "Star Player", ovr=80)
    db.execute(
        "INSERT INTO auction_results "
        "(season, lot_order, player_id, player_overall, "
        " winner_team_id, winning_bid, second_bid, price) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (1, 1, p1, 80, t1, 50000, 45000, 45001),
    )

    sid = _make_archivable_season()
    snap = db.fetchall(
        "SELECT * FROM season_auction_results WHERE season_id = ?",
        (sid,),
    )
    assert len(snap) == 1
    r = snap[0]
    assert r["winner_abbrev"] == "ALF"
    assert r["player_name"] == "Star Player"
    assert r["winning_bid"] == 50000
    assert r["price"] == 45001


def test_transactions_table_can_be_wiped_after_archive(fresh_db):
    """The whole point: after archive, the offseason wipe of `transactions`
    must not lose the AAR data. Verify the snapshot survives a DELETE."""
    t1 = _add_team("Alpha", "ALF")
    _add_team("Beta",  "BET")   # needed for the placeholder game row
    p1 = _add_player(t1, "Player One")
    db.execute(
        "INSERT INTO transactions (season, game_date, event_type, "
        "team_id, player_id, detail) VALUES (?,?,?,?,?,?)",
        (1, "2025-04-01", "auction_sign", t1, p1, "ƒ100k"),
    )
    sid = _make_archivable_season()
    db.execute("DELETE FROM transactions")
    # Live transactions table is now empty …
    live = db.fetchall("SELECT * FROM transactions")
    assert len(live) == 0
    # … but the archive row is intact
    archived = db.fetchall(
        "SELECT * FROM season_transactions WHERE season_id = ?", (sid,))
    assert len(archived) == 1
    assert archived[0]["event_type"] == "auction_sign"
    assert archived[0]["team_abbrev"] == "ALF"
