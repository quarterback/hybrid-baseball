"""Stub O27 game driver.

Feeds a made-up game into the Vicyorus baseball_scorecard API using the
forked O27 templates. Patches min_innings up so the grid is wide enough
for an O27 half (~30 PAs). Real O27 PBP integration will replace this
with a reader over sim output.
"""
import os
from baseball_scorecard.baseball_scorecard import Scorecard
from baseball_scorecard.metapost.metapost_builder import MetapostBuilder
from baseball_scorecard.team.lineup import Lineup

# Metapost's coordinate range maxes out around 20 inning columns in this
# template (upstream README, "Limitations"). 20 columns is enough for the
# stub, and a proper fix will need scaling the whole drawing.
MetapostBuilder.min_innings = 20

# O27 has a 12-deep lineup (8 fielders + SP + 3 DH). The upstream Lineup
# class hardcodes a 9-position rotation in its wrap math.
Lineup.max_replacements = 4

# Override the modulus wrap so the lineup rotates through 12 spots.
def _o27_next_batter(self):
    self.current_batter += 1
    self.current_batter %= 13
    if self.current_batter == 0:
        self.current_batter = 1
Lineup.next_batter = _o27_next_batter

def _o27_no_ab(self):
    if self.current_batter in [0, 1]:
        self.current_batter = 12
    else:
        self.current_batter -= 1
Lineup.no_ab = _o27_no_ab

OUT = os.environ.get("O27_SCORECARD_OUT", os.getcwd())
# Use the forked O27 templates rather than the upstream defaults. The
# Makefile copies them into the build dir under the names the upstream
# library expects (team_scorecard_template.mp, final_scorecard_template.tex).
TEMPLATE_DIR = OUT

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
                5: "Echo", 6: "Foxtrot", 7: "Golf", 8: "Hotel",
                9: "India (DH)", 19: "Juliet (DH)", 29: "Kilo (DH)",
                21: "Pitcher One", 22: "Pitcher Two", 39: "Bench OF",
            },
            "lefties": [],
            "lineup": [
                [1, "CF"], [2, "SS"], [3, "RF"], [4, "1B"],
                [5, "LF"], [6, "3B"], [7, "C"], [8, "2B"],
                [21, "P"], [9, "DH"], [19, "DH"], [29, "DH"],
            ],
            "bench": [[39, "OF"]],
            "bullpen": [22],
        },
        "home": {
            "team": "Hosts",
            "starter": 31,
            "roster": {
                10: "Lima", 11: "Mike", 12: "November", 13: "Oscar",
                14: "Papa", 15: "Quebec", 16: "Romeo", 17: "Sierra",
                18: "Tango (DH)", 28: "Uniform (DH)", 38: "Victor (DH)",
                31: "Pitcher Three", 32: "Pitcher Four", 48: "Bench OF",
            },
            "lefties": [],
            "lineup": [
                [10, "CF"], [11, "SS"], [12, "RF"], [13, "1B"],
                [14, "LF"], [15, "3B"], [16, "C"], [17, "2B"],
                [31, "P"], [18, "DH"], [28, "DH"], [38, "DH"],
            ],
            "bench": [[48, "OF"]],
            "bullpen": [32],
        },
    },
    template_dir=TEMPLATE_DIR,
)

# Several half-innings of varied PA outcomes to stretch the grid.
PA_PATTERNS = [
    ("c b s", lambda ab: ab.out("K")),
    ("b s f", lambda ab: ab.out("F8")),
    ("c c", lambda ab: ab.out("G6-3")),
    ("b b s c", lambda ab: ab.hit(1)),
    ("c", lambda ab: ab.out("G6-4-3")),
    ("s b", lambda ab: ab.out("L7")),
    ("c b b", lambda ab: ab.out("F9")),
    ("b b b b", lambda ab: ab.hit(1)),
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

# Demo: away manager declared seconds after out 20 — illustrate the
# decoration line. PBP-driven render will infer this from the game state.
DECLARED_AT_OUT = 20
for path in (os.path.join(OUT, "scorecard_away.mp"),):
    with open(path) as fd:
        text = fd.read()
    text = text.replace(
        "endfig;",
        f"    draw_declared_seconds_divider({DECLARED_AT_OUT + 1}, scoring);\n"
        f"    label.top(btex {{\\midsf Notes: declared at out {DECLARED_AT_OUT}}} etex, "
        f"(0, -360)) withcolor scoring;\nendfig;",
    )
    with open(path, "w") as fd:
        fd.write(text)

