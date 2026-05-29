"""Stub O27 game driver.

Feeds a made-up game into the Vicyorus baseball_scorecard API using the
forked O27 templates. Patches min_innings up so the grid is wide enough
for an O27 half (~30 PAs). Real O27 PBP integration will replace this
with a reader over sim output.
"""
import os
from baseball_scorecard.baseball_scorecard import Scorecard
from baseball_scorecard.metapost.metapost_builder import MetapostBuilder

# An O27 half is one continuous arc of 27 outs. With walks, hits, and
# stays, that's typically ~30-40 PAs per team. Bump the minimum column
# count so the layout reserves enough cells to lay them out.
# Metapost's coordinate range maxes out around 20 inning columns in this
# template (upstream README, "Limitations"). 20 columns is enough for the
# stub, and a proper fix will need scaling the whole drawing.
MetapostBuilder.min_innings = 20

OUT = os.environ.get("O27_SCORECARD_OUT", os.getcwd())

game = Scorecard(
    OUT,
    {
        "scorer": "O27 prototype",
        "date": "2026-05-29",
        "at": "Zaryanovia Field",
        "att": "12,034",
        "temp": "62 F, clear",
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

# Eight half-innings, varied outcomes, to stretch the grid.
PA_PATTERNS = [
    ("c b s", lambda ab: ab.out("K")),
    ("b s f", lambda ab: ab.out("F8")),
    ("c c", lambda ab: ab.out("G6-3")),
    ("b b s c", lambda ab: ab.hit(1)),
    ("c", lambda ab: ab.out("G6-4-3")),
    ("s b", lambda ab: ab.out("L7")),
    ("c b b", lambda ab: ab.out("F9")),
    ("b b b b", lambda ab: ab.hit(1)),  # walk-as-single shortcut for stub
    ("c s", lambda ab: ab.out("K")),
    ("b s", lambda ab: ab.hit(2)),
    ("c f s", lambda ab: ab.out("K")),
    ("b c c", lambda ab: ab.out("G4-3")),
]

for half_idx in range(8):
    half = game.new_inning()
    for i in range(3):
        pitches, finish = PA_PATTERNS[(half_idx * 3 + i) % len(PA_PATTERNS)]
        half.new_ab()
        half.pitch_list(pitches)
        finish(half)

game.winning_pitcher(31)
game.losing_pitcher(21, is_away_team=True)
game.generate_scorecard()
