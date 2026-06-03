"""Relief mechanics — inherited-runner reconcile heuristic.

Exercises pa._reconcile_inherited (the engine tally of inherited runners that
scored against a reliever), the basis for IR-Stop%. Uses a minimal duck-typed
state — no DB.

Run:  python -m pytest o27/tests/test_relief.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from o27.engine import pa as PA


class _State:
    def __init__(self, bases, inherited):
        self.bases = list(bases)
        self.inherited_runner_ids = set(inherited)
        self.pitcher_ir_scored_this_spell = 0


def test_inherited_runner_scores_on_run_event():
    # Inherited runner on 2B departs (scored) on a 1-run event.
    st = _State(["b", None, None], {"r2"})  # r2 no longer on base
    PA._reconcile_inherited(st, runs_this_event=1)
    assert st.pitcher_ir_scored_this_spell == 1
    assert "r2" not in st.inherited_runner_ids


def test_inherited_runner_out_no_run_not_counted():
    # Inherited runner departs with zero runs booked → retired, not scored.
    st = _State([None, None, None], {"r1"})
    PA._reconcile_inherited(st, runs_this_event=0)
    assert st.pitcher_ir_scored_this_spell == 0
    assert st.inherited_runner_ids == set()


def test_inherited_runner_still_on_base_untouched():
    # Runner still aboard → nothing resolves yet.
    st = _State(["r1", None, None], {"r1"})
    PA._reconcile_inherited(st, runs_this_event=0)
    assert st.pitcher_ir_scored_this_spell == 0
    assert st.inherited_runner_ids == {"r1"}


def test_scored_capped_by_runs_this_event():
    # Two inherited runners depart but only one run scored → cap at 1.
    st = _State([None, None, None], {"r1", "r3"})
    PA._reconcile_inherited(st, runs_this_event=1)
    assert st.pitcher_ir_scored_this_spell == 1
    assert st.inherited_runner_ids == set()   # both resolved (1 scored, 1 out)


def test_no_inherited_runners_is_noop():
    st = _State(["x", None, None], set())
    PA._reconcile_inherited(st, runs_this_event=3)
    assert st.pitcher_ir_scored_this_spell == 0
