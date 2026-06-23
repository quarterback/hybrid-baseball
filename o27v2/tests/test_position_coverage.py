"""Field position coverage: every game must field all eight positions.

A half-inning puts the full nine in the batting order (eight fielders + the
pitcher). A team can never take the field with a hole — e.g. the live bug where
a club fielded no third baseman all game (3B showed "—" in the defensive log).
The root cause: the position-assignment greedy only fills slots for the bodies
it is GIVEN, so if the starting fielder pool came up short of eight (a position
drained by injuries, or a league with no joker/DH pool to borrow from), a
canonical slot was left unmanned.

These tests pin the guarantee: `_topup_to_eight` forces eight distinct bodies
from the widest available pool, and `_assign_game_positions` then covers all
eight canonical slots.
"""
from o27v2 import sim

_CANON = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF"]


class _P:
    def __init__(self, pid, pos="", inf=0.5, outf=0.5, cat=0.5):
        self.player_id = pid
        self.position = pos
        self.defense_infield = inf
        self.defense_outfield = outf
        self.defense_catcher = cat
        self.game_position = None


def test_assign_eight_bodies_covers_every_slot():
    # Eight bodies with NO native 3B and a duplicate SS — the greedy must still
    # fill all eight canonical slots, each exactly once.
    eight = [_P("c", "C"), _P("a", "1B"), _P("b", "2B"), _P("d", "SS"),
             _P("e", "LF"), _P("f", "CF"), _P("g", "RF"), _P("h", "SS")]
    sim._assign_game_positions(eight, [], [])
    got = [p.game_position for p in eight]
    assert sorted(set(got)) == sorted(_CANON)   # all eight covered
    assert len(got) == len(set(got))            # nobody doubled up


def test_seven_bodies_would_leave_a_hole():
    # Documents the failure mode the fix prevents: fewer than eight fielders
    # leaves a canonical slot ("—" in the defensive log).
    seven = [_P("c", "C"), _P("a", "1B"), _P("b", "2B"), _P("d", "SS"),
             _P("e", "LF"), _P("f", "CF"), _P("g", "RF")]
    sim._assign_game_positions(seven, [], [])
    covered = {p.game_position for p in seven}
    assert "3B" not in covered          # the hole — exactly what we must avoid


def test_topup_forces_eight_from_bench_and_jokers_first():
    start = [_P(f"f{i}", _CANON[i]) for i in range(6)]   # only six fielders
    bench = [_P("b0", "RF")]
    jokers = [_P("j0")]
    dhs = []
    arms = [_P("a0"), _P("a1")]
    sim._topup_to_eight(start, bench, jokers, dhs, arms)
    assert len(start) == 8
    assert len({id(p) for p in start}) == 8         # distinct bodies
    assert bench == [] and jokers == []             # near pools drained first
    assert len(arms) == 2                           # spare arms untouched (last resort)


def test_topup_falls_back_to_spare_arm_when_no_bats_left():
    # No bench, no jokers, no DHs — only spare pitchers remain. The field must
    # still be filled: it does not matter who covers the bag.
    start = [_P(f"f{i}", _CANON[i]) for i in range(7)]   # seven fielders
    arms = [_P("a0"), _P("a1")]
    sim._topup_to_eight(start, [], [], [], arms)
    assert len(start) == 8
    assert len(arms) == 1                            # exactly one arm promoted


def test_topup_then_assign_never_leaves_a_hole():
    # End to end: short pool -> topup -> assign -> every canonical slot manned.
    start = [_P("c", "C"), _P("a", "1B"), _P("b", "2B"),
             _P("e", "LF"), _P("f", "CF"), _P("g", "RF")]   # six, no 3B/SS native
    arms = [_P("a0"), _P("a1")]
    sim._topup_to_eight(start, [], [], [], arms)
    sim._assign_game_positions(start, [], [])
    assert sorted({p.game_position for p in start}) == sorted(_CANON)


# ---------------------------------------------------------------------------
# Pitcher coverage (the ninth position) + last-line field verification.
# ---------------------------------------------------------------------------

def test_verify_assigns_pitcher_and_repairs_a_hole():
    # A duplicated game_position leaves a bag open; the verifier must redeploy
    # the spare body into the empty slot and stamp the pitcher "P".
    eight = [_P("c"), _P("a"), _P("b"), _P("d"), _P("e"), _P("f"), _P("g"), _P("h")]
    for p, gp in zip(eight, ["C", "1B", "2B", "SS", "SS", "LF", "CF", "RF"]):
        p.game_position = gp           # note: two SS, no 3B
    arm = _P("p")
    repaired = sim._verify_field_complete(eight, [arm])
    assert arm.game_position == "P"
    covered = {p.game_position for p in eight}
    assert sorted(covered) == sorted(_CANON)   # 3B got filled from the dup SS
    assert "3B" in repaired


# ---------------------------------------------------------------------------
# Roster-depth contract: >=2 of each infield slot, >=5 outfielders.
# ---------------------------------------------------------------------------

def _depth_ok(players):
    act = [p for p in players if p.get("is_active")]
    cnt = lambda pos: sum(1 for p in act if (p.get("position") or "").upper() == pos)
    inf_ok = all(cnt(p) >= 2 for p in ("C", "1B", "2B", "3B", "SS"))
    of = sum(cnt(p) for p in ("LF", "CF", "RF"))
    return inf_ok and of >= 5


def test_generated_rosters_meet_depth_contract():
    import random
    from o27v2 import league
    for seed in range(6):
        assert _depth_ok(league.generate_players(seed, random.Random(seed)))


def test_enforce_roster_depth_tops_up_a_thin_roster():
    from o27v2 import league
    # A deliberately thin roster: one of each IF, two OF — below contract.
    thin = ([{"position": p, "is_active": 1} for p in ("C", "1B", "2B", "3B", "SS")]
            + [{"position": "CF", "is_active": 1}, {"position": "LF", "is_active": 1}])
    factory = lambda pos, act: {"position": pos, "is_active": act}
    league._enforce_roster_depth(thin, factory)
    assert _depth_ok(thin)
