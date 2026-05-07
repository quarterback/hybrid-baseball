"""SABR-flavoured analytics on top of the core stat suite.

Computes context-tier metrics (run expectancy, leverage, WPA, expected
wOBA, Pythagorean-exponent refit) from the per-event game_pa_log. These
sit one layer above the rate-tier stats in o27v2.web.app and are exposed
through the `/analytics` route.

The PA log carries pre/post game-state stamps (outs_before, bases_before,
score_diff_before, …) populated by o27/render/render.py. RE24 / RE-by-
outs-remaining / leverage are all derivable from those columns alone —
no engine replay required.
"""

from o27v2.analytics.run_expectancy import (
    build_re_table,
    build_re_by_outs_remaining,
    bases_label,
)
from o27v2.analytics.expected_woba import build_xwoba_table
from o27v2.analytics.pythag import refit_pythag_exponent
from o27v2.analytics.base_runs import build_base_runs_table

__all__ = [
    "build_re_table",
    "build_re_by_outs_remaining",
    "bases_label",
    "build_xwoba_table",
    "refit_pythag_exponent",
    "build_base_runs_table",
]
