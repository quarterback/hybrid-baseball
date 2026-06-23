"""Blowout management: rest the starters once the game is decided.

Nobody rides a starter to a 130-pitch complete game in a laugher, and nobody
bats the same nine eight times while up 40. These check the two
context-dependent "rest the regulars" paths:
  - should_change_pitcher pulls the STARTER when his team is well ahead;
  - should_pinch_hit rotates a bench bat in for the regular due up when the
    batting team is well ahead (low-leverage "garbage time" rest).
Both stay quiet in close games.
"""
import random

from o27.engine.state import Team, GameState, Player
from o27.engine import manager as mgr

_POS = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C"]


def _p(pid, name, pitcher=False, pos="", **kw):
    return Player(player_id=pid, name=name, is_pitcher=pitcher, position=pos, **kw)


def _team(tid, name):
    pre = tid[0].upper()
    starters = [_p(f"{pre}{i}", f"{name[:3]}-{i}", pos=_POS[i - 1],
                   contact=0.5, power=0.5, speed=0.5, defense=0.5, skill=0.5)
                for i in range(1, 9)]
    sp = _p(f"{pre}SP", f"{name[:3]}-SP", pitcher=True, pos="P",
            stamina=0.5, pitcher_skill=0.5)
    bench = [_p(f"{pre}B{b}", f"{name[:3]}-B{b}", pos=_POS[(b - 1) % 8],
                contact=0.6, power=0.6, speed=0.6, defense=0.6, skill=0.6)
             for b in range(1, 6)]
    pen = [_p(f"{pre}P{p}", f"{name[:3]}-P{p}", pitcher=True, pos="P",
              stamina=0.6, pitcher_skill=0.6) for p in range(1, 5)]
    roster = starters + [sp] + bench + pen
    return Team(team_id=tid, name=name, roster=roster, lineup=starters + [sp])


def _state():
    st = GameState(visitors=_team("visitors", "Visitors"),
                   home=_team("home", "Home"))
    st.half = "top"                          # visitors bat, home fields
    st.bases = [None, None, None]
    st.visitors.lineup_cycle_number = 2
    st.current_pitcher_id = "HSP"            # home SP on the mound
    st.pitcher_spell_count = 6               # past the hook's min sample, well
    st.pitcher_runs_this_spell = 0           # under any fatigue / hook threshold
    st.pitcher_h_this_spell = 0
    st.outs = 14
    return st


# --- pitcher pull ----------------------------------------------------------

def test_starter_pulled_when_team_is_well_ahead():
    st = _state()
    st.score = {"visitors": 3, "home": 15}   # home (pitching) up 12
    assert mgr.should_change_pitcher(st) is True


def test_starter_not_pulled_in_a_close_game():
    st = _state()
    st.score = {"visitors": 3, "home": 3}    # tie — no blowout, no fatigue yet
    assert mgr.should_change_pitcher(st) is False


def test_starter_not_pulled_too_early_even_when_ahead():
    st = _state()
    st.score = {"visitors": 0, "home": 12}
    st.outs = 4                               # before BLOWOUT_PULL_MIN_OUTS
    assert mgr.should_change_pitcher(st) is False


# --- position-player rest --------------------------------------------------

def test_pinch_hit_rests_a_regular_in_a_blowout():
    st = _state()
    st.score = {"visitors": 20, "home": 3}    # batting team (visitors) up 17
    res = mgr.should_pinch_hit(st, rng=random.Random(0))
    assert res is not None
    assert res not in st.visitors.lineup       # a bench bat comes in


def test_pinch_hit_no_rest_in_a_close_low_leverage_spot():
    st = _state()
    st.score = {"visitors": 4, "home": 3}     # close, bases empty, low leverage
    assert mgr.should_pinch_hit(st, rng=random.Random(0)) is None


def test_blowout_rest_waits_for_the_order_to_turn():
    st = _state()
    st.score = {"visitors": 20, "home": 3}
    st.visitors.lineup_cycle_number = 1        # only one trip through so far
    assert mgr.should_pinch_hit(st, rng=random.Random(0)) is None


# --- last-licks (decisive-half) offense boost ------------------------------

def _marginal():
    st = _state()
    st.score = {"visitors": 4, "home": 3}     # close
    st.outs = 18                               # late
    batter = st.visitors.lineup[0]
    cand = next(p for p in st.visitors.roster if p.player_id == "VB1")
    return st, batter, cand


def test_last_licks_boosts_offense_leverage_for_the_second_batting_team():
    st, batter, cand = _marginal()
    st.second_batting_team = st.visitors       # batting team is in last licks
    boosted = mgr.score_substitution(st, cand, "pinch_hit", batter)
    st.second_batting_team = st.home           # batting team is NOT last licks
    plain = mgr.score_substitution(st, cand, "pinch_hit", batter)
    assert boosted > plain
    assert abs((boosted - plain) - 0.12) < 1e-9


def test_last_licks_does_not_boost_in_a_blowout_gap():
    st, batter, cand = _marginal()
    st.score = {"visitors": 20, "home": 3}     # gap beyond DECISIVE_HALF_MAX_GAP
    st.second_batting_team = st.visitors
    a = mgr.score_substitution(st, cand, "pinch_hit", batter)
    st.second_batting_team = st.home
    b = mgr.score_substitution(st, cand, "pinch_hit", batter)
    assert a == b


def test_last_licks_does_not_boost_defensive_subs():
    st, batter, cand = _marginal()
    st.second_batting_team = st.visitors
    a = mgr.score_substitution(st, cand, "pinch_field", batter)
    st.second_batting_team = st.home
    b = mgr.score_substitution(st, cand, "pinch_field", batter)
    assert a == b


# --- late-game platoon pinch-hitting ---------------------------------------

def test_platoon_advantage_grows_late():
    st = _state()
    st.score = {"visitors": 4, "home": 3}
    pit = next(p for p in st.home.roster if p.player_id == "HSP")
    pit.throws = "R"
    st.current_pitcher_id = "HSP"
    batter = st.visitors.lineup[0]
    batter.bats = "R"                                   # no edge vs a RHP
    flip = next(p for p in st.visitors.roster if p.player_id == "VB1")
    flip.bats = "L"                                     # flips to a platoon edge
    noflip = next(p for p in st.visitors.roster if p.player_id == "VB2")
    noflip.bats = "R"                                   # same hand — no flip

    def swing(outs):
        st.outs = outs
        return (mgr.score_substitution(st, flip, "pinch_hit", batter)
                - mgr.score_substitution(st, noflip, "pinch_hit", batter))

    # The platoon bat is preferred, and the edge is worth more the later it gets.
    assert swing(24) > swing(3) > 0


# --- pinch-run specialist preference ---------------------------------------

def test_pinch_run_prefers_the_specialist():
    st = _state()
    st.score = {"visitors": 4, "home": 3}
    st.outs = 20
    st.second_batting_team = st.visitors               # decisive chase
    slow = st.visitors.lineup[7]
    slow.speed = 0.2
    st.bases = [slow.player_id, None, None]
    fast = next(p for p in st.visitors.roster if p.player_id == "VB1")
    fast.speed = 0.90
    fast.roster_slot = ""
    fast.role_run = False
    spec = next(p for p in st.visitors.roster if p.player_id == "VB2")
    spec.speed = 0.85                                   # a touch slower...
    spec.roster_slot = "pr_specialist"                 # ...but the burner
    spec.role_run = True
    res = mgr.should_pinch_run(st, rng=random.Random(0))
    assert res is not None
    assert res["runner_in"].player_id == "VB2"
