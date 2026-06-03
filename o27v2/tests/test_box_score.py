"""Box-score rendering — the new per-game stats appear in the box.

Covers the two monospace renderers (box_score.py → the /game page;
box_text.py → the markdown/text export): SH (batting), IR inherited/scored
(pitching), and the Squeeze / Walk-Back footnotes. Pure functions over row
dicts — no DB, no flask.

Run:  python -m pytest o27v2/tests/test_box_score.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from o27v2.web import box_score as BS
from o27v2.web import box_text as BT


# ---------------------------------------------------------------------------
# box_score.py — the on-screen /game/<id> box
# ---------------------------------------------------------------------------

def test_box_score_batting_has_sh_column():
    rows = [{"player_name": "Steady Eddie", "position": "1B", "entry_type": "starter",
             "ab": 2, "runs": 0, "hits": 1, "doubles": 0, "triples": 0, "hr": 0,
             "rbi": 1, "bb": 0, "k": 0, "stays": 0, "sh": 2}]
    out = BS.render_batting_table("Home", rows)
    assert "SH" in out.splitlines()[1]          # header row carries SH
    # The SH value (2) renders on the player line.
    assert any("Eddie" in ln and ln.rstrip().split()[-2:] != [] and " 2" in ln
               for ln in out.splitlines())


def test_box_score_pitching_ir_column():
    rows = [
        {"player_name": "Reliever Rex", "batters_faced": 5, "outs_recorded": 3,
         "hits_allowed": 1, "runs_allowed": 1, "er": 1, "bb": 0, "k": 2,
         "hr_allowed": 0, "pitches": 12, "ir_inherited": 3, "ir_scored": 1},
        {"player_name": "Ace Alvarez", "batters_faced": 27, "outs_recorded": 21,
         "hits_allowed": 4, "runs_allowed": 0, "er": 0, "bb": 1, "k": 11,
         "hr_allowed": 0, "pitches": 90, "ir_inherited": 0, "ir_scored": 0},
    ]
    out = BS.render_pitching_table("Away", rows)
    assert "IR" in out.splitlines()[1]
    assert "3-1" in out          # reliever inherited 3, 1 scored
    # A pitcher who inherited nobody shows a dash, not 0-0.
    ace_line = next(ln for ln in out.splitlines() if "Alvarez" in ln)
    assert ace_line.rstrip().endswith("-")


def test_box_score_squeeze_annotation():
    rows = [{"player_name": "Steady Eddie", "sqz": 1, "sqz_rbi": 1},
            {"player_name": "Slugger Sam", "sqz": 0, "sqz_rbi": 0}]
    out = BS.render_batting_annotations(rows)
    assert "Squeeze:" in out and "Eddie" in out and "1 RBI" in out


def test_box_score_walkback_note():
    away = [{"player_name": "Ace Alvarez", "wb_runs": 2}]
    home = [{"player_name": "Reliever Rex", "wb_runs": 0}]
    note = BS._walkback_note(away, home)
    assert "Walk-Back runs:" in note and "off Alvarez 2" in note


def test_box_score_no_squeeze_no_walkback_when_absent():
    assert BS.render_batting_annotations(
        [{"player_name": "X", "sqz": 0}]) == ""
    assert BS._walkback_note([{"player_name": "X", "wb_runs": 0}], []) == ""


# ---------------------------------------------------------------------------
# box_text.py — the markdown / text export box
# ---------------------------------------------------------------------------

def test_box_text_batting_sh():
    rows = [{"player_name": "Steady Eddie", "position": "1B",
             "ab": 2, "runs": 0, "hits": 1, "hr": 0, "rbi": 1, "bb": 0,
             "k": 0, "sh": 2}]
    lines = BT._batting_block("Home", rows)
    assert "SH" in lines[0]          # header
    assert any("Eddie" in ln for ln in lines)


def test_box_text_pitching_ir_and_walkback():
    away = [{"player_name": "Reliever Rex", "batters_faced": 5, "outs_recorded": 3,
             "hits_allowed": 1, "runs_allowed": 1, "er": 1, "bb": 0, "k": 2,
             "hr_allowed": 0, "pitches": 12, "ir_inherited": 3, "ir_scored": 1,
             "wb_runs": 1}]
    block = BT._pitching_block("Away", away, None, None, 27, {})
    assert "IR" in block[0] and "3-1" in "\n".join(block)
    note = "\n".join(BT._pitching_annotations(away, []))
    assert "Walk-Back:" in note and "Rex 1" in note


def test_box_text_squeeze_annotation():
    rows = [{"player_name": "Steady Eddie", "sqz": 1, "sqz_rbi": 1}]
    note = "\n".join(BT._batting_annotations(rows))
    assert "Squeeze:" in note and "1 RBI" in note
