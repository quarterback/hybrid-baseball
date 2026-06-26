"""Cricket-card renderer — unit tests over the pure formatting logic.

The structure mapping matters: O27 is one 27-out half per side (top bats first,
bottom chases), NOT cricket's two separate innings; a side only bats again in a
super inning. These tests pin that framing so a refactor can't quietly revert to
the wrong "1st innings / 2nd innings" labelling.
"""
from __future__ import annotations

from o27v2.web import cricket_card as cc


def _innings(team, runs, first, batters=(), bowlers=()):
    return {"team": team, "runs": runs, "wickets": 27, "pa": 40,
            "batting_first": first, "batters": list(batters),
            "bowlers": list(bowlers)}


def test_total_bases_mapping():
    # 1 single, 1 double, 1 triple, 1 HR = 1 + 2 + 3 + 4 = 10.
    b = {"hits": 4, "doubles": 1, "triples": 1, "hr": 1}
    assert cc._total_bases(b) == 10
    assert cc._total_bases({"hits": 3, "doubles": 0, "triples": 0, "hr": 0}) == 3


def test_short_name():
    assert cc._short("Devon Conway") == "D. Conway"
    assert cc._short("Ichiro") == "Ichiro"


def test_innings_labels_top_and_chase():
    top = _innings("Foxes", 9, first=True)
    bot = _innings("Bears", 7, first=False)
    top_txt = "\n".join(cc._render_innings(top, target=10))
    bot_txt = "\n".join(cc._render_innings(bot, target=10))
    assert "top · batting first" in top_txt
    assert "9/27" in top_txt                      # all out at 27, by rule
    assert "bottom · chasing 10" in bot_txt
    # No leftover cricket two-innings wording.
    assert "1st innings" not in top_txt and "2nd innings" not in bot_txt


def test_not_out_star_and_score():
    inn = _innings("Foxes", 9, first=True, batters=[
        {"name": "A Smith", "score": 7, "balls": 5, "not_out": True, "rbi": 2},
        {"name": "B Jones", "score": 4, "balls": 6, "not_out": False, "rbi": 1},
    ])
    txt = "\n".join(cc._render_innings(inn, target=None))
    assert "7*" in txt and "(5)" in txt           # not-out, balls faced
    assert "4 " in txt                            # out batter, no star


def test_result_decided_by_runs():
    card = {"innings": [_innings("Foxes", 9, True), _innings("Bears", 7, False)],
            "super_innings": [], "winner_id": 1, "away_id": 1, "home_id": 2}
    assert cc._result_line(card) == "  Foxes beat Bears by 2 runs"


def test_result_tie_goes_to_super_innings():
    card = {"innings": [_innings("Foxes", 8, True), _innings("Bears", 8, False)],
            "super_innings": [{"half": "super_top", "v": 2, "h": 1}],
            "winner_id": 1, "away_id": 1, "home_id": 2}
    line = cc._result_line(card)
    assert "Level at 8 after regulation" in line
    assert "Foxes win in super inning" in line
