"""CapSpace — O27's daily-fantasy front-end, served at ``/fantasy``.

A standalone app with its own design system (like ``/almanac``): a React
prototype shipped verbatim as static assets, fed real data from the active
save. The page injects ``window.O27_RATES`` (the engine's currency snapshot)
and ``window.__CAPSPACE_DATA__`` (tonight's real slate + player pool); the
bundled JS falls back to its mock data when the blob is absent.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, render_template

from o27v2 import currency
from . import data as slate_data

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOG = logging.getLogger(__name__)

capspace_bp = Blueprint(
    "capspace",
    __name__,
    url_prefix="/fantasy",
    template_folder=os.path.join(_HERE, "templates"),
    static_folder=os.path.join(_HERE, "static"),
    # Flask concatenates url_prefix + static_url_path, so this resolves to
    # /fantasy/_static/… (a leading "/fantasy" here would double it).
    static_url_path="/_static",
)


def _safe_slate() -> dict | None:
    """Build the real slate, degrading to mock (None) on any error so the
    page always renders."""
    try:
        return slate_data.build_slate_data()
    except Exception:  # pragma: no cover - defensive; never 500 the app
        _LOG.exception("CapSpace slate build failed; falling back to mock data")
        return None


@capspace_bp.route("/")
def home():
    return render_template(
        "capspace.html",
        capspace_data=_safe_slate(),
        currency_rates=currency.rates_for_js(),
    )


@capspace_bp.route("/api/slate")
def api_slate():
    """JSON slate endpoint — handy for refresh / debugging. Returns the same
    blob injected into the page (or null when the save has no games)."""
    return jsonify(_safe_slate())
