"""O27 lineup-card rule.

Both teams submit a batting order at first pitch and that order is fixed for
the whole game *regardless of what happens defensively*. A tactical
(non-injury) defensive substitution swaps the GLOVE on the field but never
touches the batting order: the displaced starter is "not out" and keeps
batting in his slot; the substitute is a defense-only entrant ("nickel
fielder").

This is the fix for box scores where the team that fields first (bats second)
showed up missing a position in its batting order — its manager was swapping
starters out of the shared lineup list on defense, before that team had ever
batted. Injuries are the one exception: an injured fielder is a real exit, so
his replacement takes both the glove and the batting slot.
"""
import random

from o27.engine.state import Team, GameState, Player
from o27.engine import manager as mgr
from o27.engine import defense as dfn

_POS = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C"]


def _p(pid, name, pitcher=False, pos="", **kw):
    return Player(player_id=pid, name=name, is_pitcher=pitcher, position=pos, **kw)


def _team(tid, name, worst_idx=None):
    pre = tid[0].upper()
    starters = []
    for i in range(1, 9):
        d = 0.01 if (worst_idx is not None and i == worst_idx) else 0.5
        starters.append(_p(f"{pre}{i}", f"{name[:3]}-{i}", pos=_POS[i - 1],
                           contact=0.5, power=0.5, speed=0.5, defense=d))
    sp = _p(f"{pre}SP", f"{name[:3]}-SP", pitcher=True, pos="P", stamina=0.5)
    # Elite-glove bench so a defensive upgrade is genuinely on offer.
    bench = [_p(f"{pre}B{b}", f"{name[:3]}-B{b}", pos=_POS[(b - 1) % 7],
                contact=0.5, power=0.5, speed=0.5, defense=0.99)
             for b in range(1, 4)]
    roster = starters + [sp] + bench
    t = Team(team_id=tid, name=name, roster=roster, lineup=starters + [sp])
    t.mgr_platoon_aggression = 1.0   # aggressive skipper → low sub threshold
    return t


def _fielding_state(worst_idx=2):
    """half=top → visitors bat, home fields. The defensive-sub gate keys on
    the BATTING team's cycle, so give the visitors a completed first cycle.
    Tie game, deep into the half → high leverage so the sub clears the bar."""
    st = GameState(visitors=_team("visitors", "Visitors"),
                   home=_team("home", "Home", worst_idx=worst_idx))
    st.half = "top"
    st.outs = 26
    st.score = {"visitors": 3, "home": 3}
    st.bases = [None, None, None]
    st.visitors.lineup_cycle_number = 1
    return st


def _same_order(a, b):
    return len(a) == len(b) and all(x is y for x, y in zip(a, b))


def _stash_defense(team):
    """Mirror sim.py: stash the team-defense components so the engine can
    apply marginal defensive-sub updates."""
    wsum = wt = 0.0
    for p in team.lineup:
        pos = getattr(p, "position", "")
        if p.is_pitcher or pos in ("DH", "P", ""):
            continue
        w = dfn.positional_weight(pos)
        wsum += w * dfn.position_defense_rating(p, pos)
        wt += w
    team.defense_weighted_sum = wsum
    team.defense_weight_sum = wt
    team.defense_rating = (wsum / wt) if wt > 0 else 0.5


def test_field_only_defensive_sub_keeps_starter_in_batting_order():
    st = _fielding_state()
    home = st.home
    before = list(home.lineup)
    out = home.lineup[1]                       # SS — the worst-defense starter
    glove = next(p for p in home.roster if p.player_id == "HB1")
    mgr.defensive_sub(st, out, glove)
    # The batting order (lineup card) is UNCHANGED.
    assert _same_order(home.lineup, before)
    assert out in home.lineup
    assert glove not in home.lineup
    # The displaced starter is NOT retired — he keeps batting.
    assert out.player_id not in home.substituted_out
    # The field change is recorded; the glove took over the position.
    assert home.field_replacements.get(out.player_id) is glove
    assert glove.game_position == (out.game_position or out.position)


def test_injury_fielder_sub_replaces_in_order_and_retires():
    st = _fielding_state()
    home = st.home
    out = home.lineup[1]
    glove = next(p for p in home.roster if p.player_id == "HB1")
    idx = home.lineup.index(out)
    mgr.defensive_sub(st, out, glove, injury=True)
    # An injury is a real exit: replacement takes the slot, victim retired.
    assert home.lineup[idx] is glove
    assert out not in home.lineup
    assert out.player_id in home.substituted_out


def test_should_defensive_sub_fires_then_skips_covered_slot():
    st = _fielding_state()
    home = st.home
    first = mgr.should_defensive_sub(st)
    assert first is not None                   # leverage clears the threshold
    out1 = first["player_out"]
    mgr.defensive_sub(st, out1, first["player_in"])
    # The just-covered starter is never re-chosen, and the deployed glove
    # is never re-used.
    second = mgr.should_defensive_sub(st)
    if second is not None:
        assert second["player_out"].player_id != out1.player_id
        assert second["player_in"].player_id != first["player_in"].player_id


def test_lineup_card_survives_a_full_round_of_defensive_subs():
    # Even with the manager subbing on defense all game, the nine lineup-card
    # slots stay intact and every fielding position is still in the order.
    st = _fielding_state()
    home = st.home
    starters = list(home.lineup)
    for _ in range(10):
        d = mgr.should_defensive_sub(st)
        if d is None:
            break
        mgr.defensive_sub(st, d["player_out"], d["player_in"])
    assert _same_order(home.lineup, starters)
    order_positions = {p.position for p in home.lineup if not p.is_pitcher}
    assert set(_POS).issubset(order_positions)


def test_defensive_sub_with_a_better_glove_raises_team_defense():
    st = _fielding_state()                       # SS starter has defense 0.01
    home = st.home
    _stash_defense(home)
    before = home.defense_rating
    out = home.lineup[1]                          # the weak SS
    glove = next(p for p in home.roster if p.player_id == "HB1")   # defense 0.99
    mgr.defensive_sub(st, out, glove)
    assert home.defense_rating > before


def test_defensive_sub_with_a_worse_glove_lowers_team_defense():
    # A butcher with no glove, no range, no arm, shoved in for an average
    # shortstop, should HURT the defense — a sub is just a player.
    st = _fielding_state(worst_idx=None)         # every starter at defense 0.5
    home = st.home
    _stash_defense(home)
    before = home.defense_rating
    out = home.lineup[1]                          # an average SS
    butcher = Player(player_id="HX", name="Butcher", position="SS",
                     defense=0.05, defense_infield=0.05, defense_outfield=0.05,
                     defense_catcher=0.05, speed=0.05, arm=0.05)
    home.roster.append(butcher)
    mgr.defensive_sub(st, out, butcher)
    assert home.defense_rating < before


def test_team_defense_identity_at_all_defaults():
    # Arm/speed terms are deviations from neutral, so an all-0.5 club still
    # rates exactly 0.5 (no silent rebalancing of average teams).
    p = Player(player_id="Z", name="Z", position="SS")
    assert abs(dfn.position_defense_rating(p, "SS") - 0.5) < 1e-9
    assert abs(dfn.position_defense_rating(p, "CF") - 0.5) < 1e-9
