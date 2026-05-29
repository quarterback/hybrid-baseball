"""Stub O27 game driver.

Feeds a tiny made-up game into the Vicyorus baseball_scorecard API using
the forked templates in this dir. Real O27 PBP integration will replace
this with a reader over sim output.
"""
import os
from baseball_scorecard.baseball_scorecard import Scorecard

OUT = os.environ.get("O27_SCORECARD_OUT", os.getcwd())

game = Scorecard(
    OUT,
    {
        "scorer": "O27 prototype",
        "date": "2026-05-29",
        "at": "Zaryanovia Field",
        "att": "12,034",
        "temp": "62°F, clear",
        "wind": "5 mph out to RF",
        "umpires": {"HP": "Test, A.", "1B": "Test, B.", "2B": "Test, C.", "3B": "Test, D."},
        "away": {
            "team": "Visitors",
            "starter": 21,
            "roster": {
                1: "Alpha", 2: "Bravo", 3: "Charlie", 4: "Delta",
                5: "Echo", 6: "Foxtrot", 7: "Golf", 8: "Hotel", 9: "India",
                21: "Pitcher One", 22: "Pitcher Two",
            },
            "lefties": [],
            "lineup": [
                [1, "CF"], [2, "SS"], [3, "RF"], [4, "1B"],
                [5, "LF"], [6, "3B"], [7, "C"], [8, "2B"], [21, "P"],
            ],
            "bench": [[9, "OF"]],
            "bullpen": [22],
        },
        "home": {
            "team": "Hosts",
            "starter": 31,
            "roster": {
                10: "Juliet", 11: "Kilo", 12: "Lima", 13: "Mike",
                14: "November", 15: "Oscar", 16: "Papa", 17: "Quebec", 18: "Romeo",
                31: "Pitcher Three", 32: "Pitcher Four",
            },
            "lefties": [],
            "lineup": [
                [10, "CF"], [11, "SS"], [12, "RF"], [13, "1B"],
                [14, "LF"], [15, "3B"], [16, "C"], [17, "2B"], [31, "P"],
            ],
            "bench": [[18, "OF"]],
            "bullpen": [32],
        },
    },
)

# Top 1.
t = game.new_inning()
t.new_ab(); t.pitch_list("c b s"); t.out("K")
t.new_ab(); t.pitch_list("b s f"); t.out("F8")
t.new_ab(); t.pitch_list("c c"); t.out("G6-3")

# Bot 1.
b = game.new_inning()
b.new_ab(); b.pitch_list("b b s c"); b.hit(1)
b.new_ab(); b.pitch_list("c"); b.out("G6-4-3", rbis=0)
b.new_ab(); b.pitch_list("s b"); b.out("L7")
b.new_ab(); b.pitch_list("c b b"); b.out("F9")

game.winning_pitcher(31)
game.losing_pitcher(21, is_away_team=True)
game.generate_scorecard()
