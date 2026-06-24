"""Auction minimum-roster guarantee.

A team that wins few lots must not exit the auction with a skeleton roster
(the 9-batter / 5-pitcher bug). `_guarantee_min_roster` tops any short team up
from the free-agent pool until it can field a full lineup + staff.
"""
import pytest


@pytest.fixture()
def fresh_db(tmp_path):
    from o27v2 import db as _db
    original = _db._DB_PATH
    _db._DB_PATH = str(tmp_path / "auction.db")
    _db.init_db()
    try:
        yield _db._DB_PATH
    finally:
        _db._DB_PATH = original


def _roster(tid):
    from o27v2 import db
    rows = db.fetchall(
        "SELECT position, is_pitcher, COALESCE(is_joker,0) AS j "
        "FROM players WHERE team_id = ?", (tid,))
    total = len(rows)
    pitchers = sum(1 for r in rows if r["is_pitcher"])
    positions = {r["position"] for r in rows if not r["is_pitcher"] and not r["j"]}
    return total, pitchers, positions


def test_guarantee_min_roster_refills_a_skeleton(fresh_db):
    from o27v2 import db, league, auction

    league.seed_league(rng_seed=5, config_id="region_subcontinent")
    tid = db.fetchone("SELECT MIN(id) AS id FROM teams")["id"]

    # Strip team `tid` to an 8-man skeleton; the rest go to the FA pool
    # (team_id NULL) so they're available to be re-signed.
    keep = [r["id"] for r in db.fetchall(
        "SELECT id FROM players WHERE team_id = ? LIMIT 8", (tid,))]
    db.execute(
        "UPDATE players SET team_id = NULL, is_active = 0 "
        "WHERE team_id = ? AND id NOT IN (%s)" % ",".join("?" * len(keep)),
        (tid, *keep))
    total_before, _, _ = _roster(tid)
    assert total_before == 8

    signings = auction._guarantee_min_roster([tid])
    assert signings, "a stripped team should draw FA signings"

    total, pitchers, positions = _roster(tid)
    assert total >= auction.AUCTION_MIN_ROSTER
    assert pitchers >= auction.AUCTION_MIN_PITCHERS
    assert set(auction._CANONICAL_POSITIONS).issubset(positions), (
        f"missing positions after fill: "
        f"{set(auction._CANONICAL_POSITIONS) - positions}")


def test_guarantee_min_roster_noop_when_full(fresh_db):
    from o27v2 import db, league, auction

    league.seed_league(rng_seed=5, config_id="region_subcontinent")
    tid = db.fetchone("SELECT MIN(id) AS id FROM teams")["id"]
    # A freshly-seeded team is already full → no signings, no change.
    before, _, _ = _roster(tid)
    signings = auction._guarantee_min_roster([tid])
    after, _, _ = _roster(tid)
    assert signings == []
    assert after == before
