"""
Pin the Phase D Assistant GM behavior — roster-gap-aware valuation
multiplier dampers bids when a team's already stuffed at a position.

The bug this fixes: one team could win 12+ lots at one position in
a single auction because nothing checked whether they'd already filled
the slot. Now bid valuations multiply by:
   need >= 2: 1.30   (multiple open slots — push hard)
   need == 1: 1.15   (one open slot — push)
   need == 0: 0.90   (target met — mild damper)
   need == -1: 0.50  (one over — strong damper)
   need <= -2: 0.15  (2+ over — essentially won't bid)
"""
import pytest

import o27v2.db as db
import o27v2.auction as au


@pytest.fixture()
def two_team_db(tmp_path):
    original = db._DB_PATH
    db._DB_PATH = str(tmp_path / "agm.db")
    try:
        db.init_db()
        for i in range(2):
            db.execute(
                "INSERT INTO teams (name, abbrev, city, league, division, "
                "org_strength, mgr_quick_hook, mgr_bullpen_aggression, mgr_joker_aggression) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (f"T{i+1}", f"T{i+1}", "C", "Pro", "East",
                 50, 0.5, 0.5, 0.5),
            )
        yield
    finally:
        db._DB_PATH = original


def _add_player_at(team_id: int, position: str, name: str) -> None:
    db.execute(
        "INSERT INTO players (name, position, is_pitcher, is_active, "
        "skill, contact, power, eye, speed, pitcher_skill, command, movement, stamina, "
        "defense, arm, baserunning, run_aggressiveness, "
        "defense_infield, defense_outfield, defense_catcher, archetype, "
        "stay_aggressiveness, contact_quality_threshold, team_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, position, 0, 1, 60,60,60,60,60, 0,0,0,60, 60,60,60,60, 60,60,60, "", 0.3, 0.5, team_id),
    )


def test_position_need_is_signed(two_team_db):
    """Empty team has high positive need; over-stuffed team has negative."""
    # T1 empty
    assert au._team_position_need(1, "CF", False) == 4   # target 4 CF
    # T1 with 4 CFs — exactly at target → 0
    for k in range(4):
        _add_player_at(1, "CF", f"CF_{k}")
    assert au._team_position_need(1, "CF", False) == 0
    # T1 with 6 CFs → -2
    _add_player_at(1, "CF", "CF_5")
    _add_player_at(1, "CF", "CF_6")
    assert au._team_position_need(1, "CF", False) == -2


@pytest.mark.parametrize("need,expected", [
    (5,  1.30),   # multiple open
    (3,  1.30),
    (2,  1.30),
    (1,  1.15),   # one open
    (0,  0.90),   # at target
    (-1, 0.50),   # one over
    (-2, 0.15),   # 2+ over
    (-5, 0.15),
])
def test_need_multiplier_table(need, expected):
    assert au._need_multiplier(need) == expected


def test_needy_team_outvalues_stuffed_team(two_team_db):
    """Same player; T1 has 4 CFs (at target), T2 has 0 (needs all). T2 should
    value the player markedly higher than T1."""
    for k in range(4):
        _add_player_at(1, "CF", f"CF_{k}")
    test_player = {"id": 99, "name": "Hot CF", "position": "CF",
                   "is_pitcher": False, "skill": 70, "contact": 70,
                   "power": 70, "eye": 70, "speed": 70,
                   "pitcher_skill": 0, "command": 0, "movement": 0,
                   "stamina": 70}
    t1 = dict(db.fetchone("SELECT * FROM teams WHERE id=1"))
    t2 = dict(db.fetchone("SELECT * FROM teams WHERE id=2"))
    val_stuffed = au._team_valuation_noisefree(test_player, 1, au._team_auction_profile(t1))
    val_needy   = au._team_valuation_noisefree(test_player, 2, au._team_auction_profile(t2))
    assert val_needy > val_stuffed, (val_needy, val_stuffed)
    # T2 multiplier 1.30 vs T1 0.90 = ratio ≥ 1.4×
    assert val_needy / val_stuffed >= 1.40


def test_overstuffed_team_essentially_wont_bid(two_team_db):
    """T1 with 7 CFs (3 over target) values a hot CF at ~1/8 of T2's needy bid."""
    for k in range(7):
        _add_player_at(1, "CF", f"CF_{k}")
    test_player = {"id": 99, "name": "Hot CF", "position": "CF",
                   "is_pitcher": False, "skill": 70, "contact": 70,
                   "power": 70, "eye": 70, "speed": 70,
                   "pitcher_skill": 0, "command": 0, "movement": 0,
                   "stamina": 70}
    t1 = dict(db.fetchone("SELECT * FROM teams WHERE id=1"))
    t2 = dict(db.fetchone("SELECT * FROM teams WHERE id=2"))
    val_overstuffed = au._team_valuation_noisefree(test_player, 1, au._team_auction_profile(t1))
    val_needy       = au._team_valuation_noisefree(test_player, 2, au._team_auction_profile(t2))
    # Overstuffed mult 0.15 vs needy 1.30 = ratio of ~8.7×
    assert val_needy / val_overstuffed >= 7.0


# ---------------------------------------------------------------------------
# Apron damper — penalise whale-bid behavior so a team that's already
# made a big-money bid value future lots progressively less.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("peak_pct,expected", [
    (0.00, 1.00),   # no marquee bid yet
    (0.04, 1.00),   # under deadband
    (0.10, 0.85),
    (0.15, 0.85),   # boundary inclusive of upper
    (0.20, 0.65),
    (0.30, 0.65),
    (0.40, 0.45),
    (0.50, 0.45),
    (0.60, 0.30),
    (0.70, 0.30),
    (0.85, 0.20),
    (1.20, 0.20),   # past 100% (somehow) — still floored at 0.20
])
def test_apron_damper_table(peak_pct, expected):
    assert au._apron_damper(peak_pct) == expected


def test_whale_team_valuation_dampens_vs_clean_team(two_team_db):
    """Same player, same roster need; T1 has already made a 40%-of-purse
    win and T2 hasn't bid yet. T1 should value subsequent lots markedly
    less than T2."""
    test_player = {"id": 99, "name": "Star OF", "position": "CF",
                   "is_pitcher": False, "skill": 75, "contact": 75,
                   "power": 75, "eye": 75, "speed": 75,
                   "pitcher_skill": 0, "command": 0, "movement": 0,
                   "stamina": 70}
    t1 = dict(db.fetchone("SELECT * FROM teams WHERE id=1"))
    t2 = dict(db.fetchone("SELECT * FROM teams WHERE id=2"))
    val_whale = au._team_valuation_noisefree(
        test_player, 1, au._team_auction_profile(t1), big_bid_pct=0.40,
    )
    val_clean = au._team_valuation_noisefree(
        test_player, 2, au._team_auction_profile(t2), big_bid_pct=0.00,
    )
    # Whale damper 0.45 vs clean 1.00 → clean values ≥ 2× the whale
    assert val_clean > val_whale
    assert val_clean / val_whale >= 2.0


def test_cheap_winning_team_pays_no_apron_premium(two_team_db):
    """A team that's been winning low-tier lots (peak bid stays under
    the 5% deadband) gets no apron damper — that's the whole point of
    flagging on biggest-single-bid rather than cumulative spend."""
    t1 = dict(db.fetchone("SELECT * FROM teams WHERE id=1"))
    profile = au._team_auction_profile(t1)
    test_player = {"id": 99, "name": "Mid", "position": "CF",
                   "is_pitcher": False, "skill": 60, "contact": 60,
                   "power": 60, "eye": 60, "speed": 60,
                   "pitcher_skill": 0, "command": 0, "movement": 0,
                   "stamina": 60}
    # 4% peak — under the 5% deadband
    val_cheap_eater = au._team_valuation_noisefree(
        test_player, 1, profile, big_bid_pct=0.04,
    )
    # No peak yet
    val_fresh = au._team_valuation_noisefree(
        test_player, 1, profile, big_bid_pct=0.00,
    )
    assert val_cheap_eater == val_fresh
