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

# Demo: away manager declared seconds after out 20, plus a few example
# stays, a walk-back, and a joker insertion to illustrate the new
# macros. PBP-driven render will derive all of this from the game state.
DECLARED_AT_OUT = 20

def cell_origin(lineup_pos: int, col: int) -> tuple[int, int]:
    """Map a (1-indexed lineup position, 1-indexed PA column) to the
    (xstart, ystart) corner of the at-bat cell."""
    return ((col - 1) * 128, (12 - lineup_pos) * 128)

demos = []
demos.append(f"draw_declared_seconds_divider({DECLARED_AT_OUT + 1}, scoring)")

x, y = cell_origin(3, 2);  demos.append(f"draw_stay_ticks(3, {x}, {y}, scoring)")
x, y = cell_origin(2, 5);  demos.append(f"draw_stay_ticks(2, {x}, {y}, scoring)")
x, y = cell_origin(7, 3);  demos.append(f"draw_stay_ticks(1, {x}, {y}, scoring)")
x, y = cell_origin(4, 4);  demos.append(f"draw_walk_back_mark({x}, {y}, scoring)")
x, y = cell_origin(5, 6);  demos.append(f"draw_joker_glyph(2, {x}, {y}, scoring)")
x, y = cell_origin(11, 8); demos.append(f"draw_joker_glyph(1, {x}, {y}, scoring)")

inject = "".join(f"    {d};\n" for d in demos)
inject += (
    f"    label.top(btex {{\\midsf Notes: declared at out {DECLARED_AT_OUT}}} etex, "
    f"(0, -360)) withcolor scoring;\n"
)

# Pitcher arc demo. Origin in the lower-left footer area. 27 outs at 8
# units each = 216 units wide. Sample game: SP (Pitcher One) outs 0-15,
# RP1 (Pitcher Two) outs 16-21, ending early at out 21 because of the
# Declared Seconds (the bottom of the half spent the banked outs in
# extras, but the arc bar here is regulation only — extras would extend
# past 27 if present).
arc_x, arc_y = 400, -420
arc_demos = [
    f'draw_pitcher_arc_bar_frame({arc_x}, {arc_y}, 27, scoring)',
    f'draw_pitcher_arc_segment({arc_x}, {arc_y}, 0, 15, btex {{SP}} etex, scoring)',
    f'draw_pitcher_arc_segment({arc_x}, {arc_y}, 15, 21, btex {{RP1}} etex, scoring)',
]
inject += "".join(f"    {d};\n" for d in arc_demos)

for path in (os.path.join(OUT, "scorecard_away.mp"),):
    with open(path) as fd:
        text = fd.read()
    text = text.replace("endfig;", inject + "endfig;")
    with open(path, "w") as fd:
        fd.write(text)

