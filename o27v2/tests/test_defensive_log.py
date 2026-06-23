"""Defensive log (text box score): per-position field coverage by out-envelope.

O27 separates the batting card from the fielding alignment — a defensive sub
changes who fields without touching who bats — so the box score carries a
DEFENSIVE LOG showing who covered each position and when. Pitcher envelopes
come from cumulative outs recorded; the eight field positions from the starter
plus any defensive entries. Pinch hit / pinch run never appear (they don't
change the field).
"""
from o27v2.web import box_score as bs


def _bisons_batting():
    def row(name, pos, et="starter", inn=1):
        return {"player_name": name, "game_position": pos,
                "entry_type": et, "entered_inning": inn}
    return [
        row("Sam Drew", "C"),
        row("Phil Durfee", "C", "DEF", 8),
        row("Ross Latimer", "1B"),
        row("Al Kohlberg", "2B"),
        row("Dae Gil", "3B"),
        row("Xan Fuenzalida", "SS"),
        row("Yon Pareja", "SS", "DEF", 2),
        row("Zed Dunkerque", "LF"),
        row("Joc Leininger", "CF"),
        row("Bo Arnett", "RF"),
        # An offensive pinch hitter for the first baseman — must NOT show up
        # in the defensive log; Latimer keeps fielding 1B.
        row("Pinch Hooligan", "1B", "PH", 5),
    ]


def _bisons_pitching():
    return [{"player_name": "Ace Holder", "ip_outs": 12},
            {"player_name": "Rel Brundage", "ip_outs": 15}]


def test_defensive_log_envelopes():
    out = bs._defensive_log_for("Bisons", _bisons_batting(), _bisons_pitching())
    assert "BISONS DEFENSIVE LOG (OUTS 1-27)" in out
    # Pitcher and the two changed positions show out-envelopes.
    assert "Holder (Outs 1-12) → Brundage (Outs 13-27)" in out
    assert "Drew (Outs 1-21) → Durfee (Outs 22-27)" in out
    assert "Fuenzalida (Outs 1-3) → Pareja (Outs 4-27)" in out
    # Unchanged positions show a single full-game envelope.
    assert "Latimer (Outs 1-27)" in out
    # The pinch hitter is offensive — never in the defensive log.
    assert "Hooligan" not in out
    # All nine position labels are present.
    for _, label in bs._DEF_LOG_POSITIONS:
        assert label in out


def test_defensive_log_lists_all_nine_even_with_no_subs():
    batting = [
        {"player_name": f"P{p}", "game_position": p, "entry_type": "starter",
         "entered_inning": 1}
        for p in ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF")
    ]
    pitching = [{"player_name": "Solo Arm", "ip_outs": 27}]
    out = bs._defensive_log_for("Mud Hens", batting, pitching)
    assert out.count("(Outs 1-27)") == 9      # P + 8 fielders, each full game
    assert " → " not in out                    # no changes anywhere


def test_defensive_log_uses_exact_entered_outs():
    # A sub at out 4 (mid-inning) must show 1-4 / 5-N — the precise out, not the
    # inning boundary (3). entered_outs overrides the inning fallback.
    batting = [
        {"player_name": "Starter Sam", "game_position": "SS",
         "entry_type": "starter", "entered_inning": 1, "entered_outs": 0},
        {"player_name": "Sub Sid", "game_position": "SS",
         "entry_type": "DEF", "entered_inning": 2, "entered_outs": 4},
    ]
    pitching = [{"player_name": "Solo Arm", "ip_outs": 27}]
    out = bs._defensive_log_for("X", batting, pitching)
    assert "Sam (Outs 1-4) → Sid (Outs 5-27)" in out


def test_defensive_log_inning_fallback_without_entered_outs():
    # Legacy rows (no entered_outs) fall back to the inning boundary.
    batting = [
        {"player_name": "Starter Sam", "game_position": "SS",
         "entry_type": "starter", "entered_inning": 1},
        {"player_name": "Sub Sid", "game_position": "SS",
         "entry_type": "DEF", "entered_inning": 2},
    ]
    pitching = [{"player_name": "Solo Arm", "ip_outs": 27}]
    out = bs._defensive_log_for("X", batting, pitching)
    assert "Sam (Outs 1-3) → Sid (Outs 4-27)" in out


def test_defensive_log_empty_without_pitching():
    assert bs._defensive_log_for("X", [], []) == ""
