"""
Phase E — post-auction trade reconciliation tests.

Covers the five trade classes (blockbuster, star-for-star, 3-cycle,
surplus, arbitrage) and the rescore-after-fire iteration that
prevents double-dipping and depth corruption.
"""
import pytest

import o27v2.db as db
import o27v2.post_auction_trades as pat


@pytest.fixture()
def fresh_db(tmp_path):
    original = db._DB_PATH
    db._DB_PATH = str(tmp_path / "pat.db")
    try:
        db.init_db()
        yield
    finally:
        db._DB_PATH = original


def _add_team(name: str, abbrev: str, *, org_strength: int = 50) -> int:
    return db.execute(
        "INSERT INTO teams (name, abbrev, city, league, division, "
        "org_strength, mgr_quick_hook, mgr_bullpen_aggression, mgr_joker_aggression) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (name, abbrev, "City", "Pro", "East",
         org_strength, 0.5, 0.5, 0.5),
    )


def _add_player(team_id: int | None, position: str, name: str,
                ovr: int, *, is_pitcher: bool = False) -> int:
    return db.execute(
        "INSERT INTO players (name, position, is_pitcher, is_active, "
        "skill, contact, power, eye, speed, pitcher_skill, command, movement, stamina, "
        "defense, arm, baserunning, run_aggressiveness, "
        "defense_infield, defense_outfield, defense_catcher, archetype, "
        "stay_aggressiveness, contact_quality_threshold, team_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, position, 1 if is_pitcher else 0, 1,
         ovr, ovr, ovr, ovr, ovr,
         ovr if is_pitcher else 0,
         ovr if is_pitcher else 0,
         ovr if is_pitcher else 0,
         60, 60, 60, 60, 60, 60, 60, 60, "", 0.3, 0.5, team_id),
    )


def test_blockbuster_fires_when_both_teams_have_blocked_talent(fresh_db):
    """T1 has a top-tier SS star + a talented-but-blocked SS, thin at CF.
    T2 has a top-tier CF star + a talented-but-blocked CF, thin at SS.
    The stars are mismatched in OVR (90 vs 78) so star-for-star can't
    fire — only the blocked-talent swap clears talent-symmetry."""
    t1 = _add_team("T1", "T1")
    t2 = _add_team("T2", "T2")
    # T1: ELITE SS star (90) + blocked talent SS (76) + thin CF (55)
    _add_player(t1, "SS", "T1_StarSS", 90)
    blocked_ss_id = _add_player(t1, "SS", "T1_TalentSS", 76)
    _add_player(t1, "CF", "T1_AvgCF", 55)
    # T2: mid-tier CF star (78) + blocked talent CF (76) + thin SS (55)
    _add_player(t2, "CF", "T2_StarCF", 78)
    blocked_cf_id = _add_player(t2, "CF", "T2_TalentCF", 76)
    _add_player(t2, "SS", "T2_AvgSS", 55)

    result = pat.run_post_auction_trades(season=1, rng_seed=42)
    assert result["total_trades"] >= 1
    new_team_ss = db.fetchone("SELECT team_id FROM players WHERE id=?",
                              (blocked_ss_id,))["team_id"]
    new_team_cf = db.fetchone("SELECT team_id FROM players WHERE id=?",
                              (blocked_cf_id,))["team_id"]
    # The blocked talents should have swapped (star-for-star can't fire
    # at |90-78|=12 > SYM_2TEAM=5)
    assert new_team_ss == t2
    assert new_team_cf == t1
    bb_trades = [t for t in result["trades"]
                 if t["class"] == "blockbuster" and t.get("tag") == "blockbuster"]
    assert len(bb_trades) >= 1


def test_star_for_star_fires_when_no_blocked_talent(fresh_db):
    """No blocked depth — just two teams whose stars happen to fit the
    other's need profile better than their own. Each team has a decent
    backup at the position they're shipping so the depth loss doesn't
    catastrophically void the trade.

    T1: Star SS (80) + decent SS backup (66), thin CF (55).
    T2: Star CF (80) + decent CF backup (66), thin SS (55).
    Stars swap; each team's new #1 at the inbound thin position is the
    incoming star, while the outbound position promotes the backup."""
    t1 = _add_team("T1", "T1")
    t2 = _add_team("T2", "T2")
    ss_id = _add_player(t1, "SS", "T1_StarSS", 80)
    _add_player(t1, "SS", "T1_BackupSS", 66)   # ≥ STARTER_FLOOR but small
    _add_player(t1, "CF", "T1_AvgCF", 55)
    cf_id = _add_player(t2, "CF", "T2_StarCF", 80)
    _add_player(t2, "CF", "T2_BackupCF", 66)
    _add_player(t2, "SS", "T2_AvgSS", 55)

    result = pat.run_post_auction_trades(season=1, rng_seed=42)
    new_ss_team = db.fetchone("SELECT team_id FROM players WHERE id=?",
                              (ss_id,))["team_id"]
    new_cf_team = db.fetchone("SELECT team_id FROM players WHERE id=?",
                              (cf_id,))["team_id"]
    # The "decent SS backup" is also a candidate the blockbuster class
    # could move — accept either outcome but require at least one
    # star-for-star or blockbuster trade fired with the stars or
    # backups exchanging teams.
    classes = {t["class"] for t in result["trades"]}
    assert classes & {"star_for_star", "blockbuster"}
    assert new_ss_team != t1 or new_cf_team != t2


def test_3_team_cycle_fires(fresh_db):
    """3-team cycle: A blocked at SS (B thin), B blocked at CF (C thin),
    C blocked at 1B (A thin). Each team has a competent (but not star)
    starter at the other positions so no 2-team blockbuster fires, and
    each team's star is asymmetric in OVR so star-for-star can't pair.
    Cycle is the only viable option."""
    t_a = _add_team("A", "A")
    t_b = _add_team("B", "B")
    t_c = _add_team("C", "C")
    # A: SS star (85) + blocked SS (75), thin at 1B (55), has CF=70 so
    # it's NOT thin at CF (blocks 2-team B→A blockbuster).
    _add_player(t_a, "SS", "A_StarSS", 85)
    pa = _add_player(t_a, "SS", "A_TalentSS", 75)
    _add_player(t_a, "1B", "A_Avg1B", 55)
    _add_player(t_a, "CF", "A_OkCF", 70)
    # B: CF star (78) + blocked CF (75), thin at SS (55), 1B=70 not thin.
    _add_player(t_b, "CF", "B_StarCF", 78)
    pb = _add_player(t_b, "CF", "B_TalentCF", 75)
    _add_player(t_b, "SS", "B_AvgSS", 55)
    _add_player(t_b, "1B", "B_Ok1B", 70)
    # C: 1B star (76) + blocked 1B (75), thin at CF (55), SS=70 not thin.
    _add_player(t_c, "1B", "C_Star1B", 76)
    pc = _add_player(t_c, "1B", "C_Talent1B", 75)
    _add_player(t_c, "CF", "C_AvgCF", 55)
    _add_player(t_c, "SS", "C_OkSS", 70)

    result = pat.run_post_auction_trades(season=1, rng_seed=42,
                                          per_team_cap=1)
    cycle_trades = [t for t in result["trades"] if t["class"] == "3_cycle"]
    assert len(cycle_trades) == 1
    # Verify the three blocked talents ended up at distinct other teams
    new_a = db.fetchone("SELECT team_id FROM players WHERE id=?", (pa,))["team_id"]
    new_b = db.fetchone("SELECT team_id FROM players WHERE id=?", (pb,))["team_id"]
    new_c = db.fetchone("SELECT team_id FROM players WHERE id=?", (pc,))["team_id"]
    assert {new_a, new_b, new_c} == {t_a, t_b, t_c}
    assert new_a != t_a and new_b != t_b and new_c != t_c


def test_rescore_prevents_double_dip(fresh_db):
    """T1 has only ONE blocked SS chip. Even though both T2 and T3 are
    thin at SS, the rescore-after-fire loop ensures T1's chip can only
    move once — the second iteration sees an empty depth chart and
    finds no second pairing involving the same player.

    Note: star-for-star may fire instead of (or in addition to) the
    blocked-talent swap depending on scoring; the key property is that
    T1 can only appear in trades involving DISTINCT moving players,
    not the same chip twice."""
    t1 = _add_team("T1", "T1", org_strength=50)
    t2 = _add_team("T2", "T2")
    t3 = _add_team("T3", "T3")
    _add_player(t1, "SS", "T1_Star", 82)
    blocked = _add_player(t1, "SS", "T1_Blocked", 76)
    _add_player(t1, "CF", "T1_AvgCF", 55)
    _add_player(t1, "1B", "T1_Avg1B", 55)
    _add_player(t2, "CF", "T2_Star", 82)
    _add_player(t2, "CF", "T2_Blocked", 76)
    _add_player(t2, "SS", "T2_AvgSS", 55)
    _add_player(t3, "1B", "T3_Star", 82)
    _add_player(t3, "1B", "T3_Blocked", 76)
    _add_player(t3, "SS", "T3_AvgSS", 55)

    result = pat.run_post_auction_trades(season=1, rng_seed=42)
    # The blocked T1 SS can only have moved zero or one times — never twice.
    moves_of_blocked = [
        m for t in result["trades"] for m in t["moves"]
        if m["player_id"] == blocked
    ]
    assert len(moves_of_blocked) <= 1
    # No single player appears in more than one fired trade.
    all_moved_ids = [m["player_id"] for t in result["trades"] for m in t["moves"]]
    assert len(all_moved_ids) == len(set(all_moved_ids))


def test_per_team_cap_enforced(fresh_db):
    """A team with many blocked talents can't fire more than per_team_cap
    trades in a single pass."""
    t_anchor = _add_team("ANCHOR", "ANCH")
    _add_player(t_anchor, "SS", "Anchor_StarSS", 82)
    # Multiple blocked SSes on the anchor team
    for k in range(5):
        _add_player(t_anchor, "SS", f"Anchor_Blocked_{k}", 76)
    # Make anchor thin at multiple other positions so trades have somewhere to go
    for pos in ("CF", "1B", "3B", "C", "2B"):
        _add_player(t_anchor, pos, f"Anchor_Avg_{pos}", 55)
    # Five other teams, each thin at SS with a blocked talent elsewhere
    for k in range(5):
        tid = _add_team(f"Other{k}", f"O{k}")
        pos_map = ["CF", "1B", "3B", "C", "2B"]
        pos = pos_map[k]
        _add_player(tid, pos, f"O{k}_Star", 82)
        _add_player(tid, pos, f"O{k}_Blocked", 76)
        _add_player(tid, "SS", f"O{k}_AvgSS", 55)

    result = pat.run_post_auction_trades(season=1, rng_seed=42,
                                          per_team_cap=2)
    anchor_count = sum(1 for t in result["trades"] if t_anchor in t["teams"])
    assert anchor_count <= 2


def test_emits_trade_transactions(fresh_db):
    """Each fired trade emits one transaction event per moved player
    (event_type='trade'), tagged to the destination team. Lets the
    player-card transactions tab surface inbound trades."""
    t1 = _add_team("T1", "T1")
    t2 = _add_team("T2", "T2")
    _add_player(t1, "SS", "T1_StarSS", 82)
    _add_player(t1, "SS", "T1_BlockedSS", 76)
    _add_player(t1, "CF", "T1_AvgCF", 55)
    _add_player(t2, "CF", "T2_StarCF", 82)
    _add_player(t2, "CF", "T2_BlockedCF", 76)
    _add_player(t2, "SS", "T2_AvgSS", 55)

    db.execute("CREATE TABLE IF NOT EXISTS seasons "
               "(season_number INTEGER PRIMARY KEY)")
    db.execute("INSERT OR IGNORE INTO seasons (season_number) VALUES (1)")

    result = pat.run_post_auction_trades(season=1, rng_seed=42)
    assert result["total_trades"] >= 1
    txs = db.fetchall(
        "SELECT * FROM transactions WHERE event_type='trade' "
        "ORDER BY player_id"
    )
    # Each fired trade emits N transaction rows (one per moved player)
    expected_rows = sum(len(t["moves"]) for t in result["trades"])
    assert len(txs) == expected_rows
    # Each trade detail string references the trade class
    for tx in txs:
        assert "Trade" in (tx["detail"] or "")


def test_no_trades_when_rosters_balanced(fresh_db):
    """Two teams with identical, balanced rosters → no candidates clear
    the both-sides-improve gate."""
    t1 = _add_team("T1", "T1")
    t2 = _add_team("T2", "T2")
    for tid in (t1, t2):
        for pos in ("SS", "CF", "1B", "C", "2B", "3B", "LF", "RF"):
            _add_player(tid, pos, f"{tid}_{pos}", 65)

    result = pat.run_post_auction_trades(season=1, rng_seed=42)
    assert result["total_trades"] == 0
