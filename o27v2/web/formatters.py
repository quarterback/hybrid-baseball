"""Presentation helpers exposed to Jinja as template filters.

Pure value-formatting functions (scout grades, flag emoji, star bars, park
JSON, pitch repertoire, money cells). They depend only on stdlib, markupsafe,
and o27v2.currency — never on the Flask app — so they live apart from the
route layer and are registered as filters by o27v2.web.app.
"""
from __future__ import annotations

from markupsafe import Markup

from o27v2 import currency


def _scout(val) -> int:
    """Render a stored attribute as a 20–80 scout grade.
    Task #47 stores grades natively as ints in [20, 80]; legacy float values
    in [0.0, 1.0] are converted on the fly via the 0.15 / 0.50 / 0.85 anchors."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return 50
    if v > 1.0:  # already a grade (int storage from Task #47)
        return max(20, min(80, int(round(v))))
    grade = 20 + (v - 0.15) / 0.70 * 60
    return max(20, min(80, int(round(grade))))


def _flag(country_code):
    """Render a country code as a flag.

    For real ISO 3166-1 alpha-2 codes, returns the regional-indicator
    emoji pair (OS/browser emoji font supplies the picture).

    For fictional countries registered in `_CUSTOM_FLAGS`, returns an
    inline <img> tag pointing at a static asset under
    `o27v2/web/static/flags/`. Templates wrap the output in a
    `<span class="player-flag">` regardless of which path renders.
    """
    if not country_code:
        return ""
    s = str(country_code).strip().upper()
    if s in _CUSTOM_FLAGS:
        path = _CUSTOM_FLAGS[s]
        return Markup(
            f'<img src="/static/flags/{path}" alt="{s}" class="player-flag-img" '
            f'style="height:1em;vertical-align:-0.15em;width:auto" />'
        )
    if len(s) != 2 or not s.isalpha():
        return ""
    base = 0x1F1E6
    a = ord("A")
    return chr(base + ord(s[0]) - a) + chr(base + ord(s[1]) - a)


# Fictional countries with custom flag art. Files live in
# o27v2/web/static/flags/ and are served by Flask's built-in static route.
_CUSTOM_FLAGS: dict[str, str] = {
    "ZR": "zr.png",   # Zaryanovia — alt-history Russian Far East
}


def _archetype_label(key) -> str:
    """Convert a manager archetype key (e.g. 'mad_scientist') to its
    human label (e.g. 'Mad Scientist'). Empty / unknown keys fall back
    to the snake_case key so templates render *something* rather than
    breaking."""
    if not key:
        return ""
    from o27v2.managers import archetype_label
    return archetype_label(str(key))


def _rating_stars(value) -> str:
    """Render a 0..1 float as a 5-dot rating bar. Fog-of-war display —
    hides the exact internal number while still showing the shape.

    < 0.20 → ●○○○○ ;  0.20-0.39 → ●●○○○ ;  0.40-0.59 → ●●●○○ ;
    0.60-0.79 → ●●●●○ ;  ≥ 0.80 → ●●●●●
    """
    try:
        v = float(value or 0.0)
    except (TypeError, ValueError):
        v = 0.0
    if v >= 0.80:
        filled = 5
    elif v >= 0.60:
        filled = 4
    elif v >= 0.40:
        filled = 3
    elif v >= 0.20:
        filled = 2
    else:
        filled = 1
    return "●" * filled + "○" * (5 - filled)


def _park_dimensions(value) -> dict:
    """Parse a JSON-encoded park_dimensions field into a dict. Returns
    an empty dict on malformed / legacy rows."""
    import json as _json
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return _json.loads(value) or {}
    except (ValueError, TypeError):
        return {}


def _park_quirks(value) -> list:
    """Parse the JSON-encoded park_quirks list into a list of dicts."""
    import json as _json
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        out = _json.loads(value)
        return out if isinstance(out, list) else []
    except (ValueError, TypeError):
        return []


def _park_shape_meta(value) -> dict:
    """Return {label, blurb} for a park_shape key. Empty dict on
    unknown / legacy values."""
    if not value:
        return {"label": "", "blurb": ""}
    try:
        from o27v2.league import _park_shape_meta as _impl
        return _impl(str(value))
    except Exception:
        return {"label": "", "blurb": ""}


def _repertoire(value) -> list:
    """Parse a pitcher's JSON repertoire into a sorted list of dicts.

    Each entry: {pitch_type, quality, usage_weight, grade, label, tier}.
      grade  = quality mapped to a 20-80 scout grade (the rest of the
               system uses 20-80, so the chip reads consistently).
      tier   = 'elite' / 'plus' / 'avg' / 'fringe' / 'org' for chip color.
      label  = humanized pitch_type (snake_case → Title Case, with a few
               canon overrides).
    Sorted by usage_weight desc so the primary pitch comes first.
    """
    import json as _json
    if not value:
        return []
    if isinstance(value, str):
        try:
            raw = _json.loads(value)
        except (ValueError, TypeError):
            return []
    elif isinstance(value, list):
        raw = value
    else:
        return []

    _OVERRIDES = {
        "four_seam":       "4-Seam",
        "sisko_slider":    "Sisko Slider",
        "vulcan_changeup": "Vulcan Change",
        "walking_slider":  "Walking Slider",
        "curve_10_to_2":   "10-to-2 Curve",
        "peeled_drop":      "Peeled Drop",
        "backhand_changeup":"Backhand Change",
        "sky_eephus":       "Sky Eephus",
        "slither_knuck":    "Slither Knuck",
        "drop_knuck":       "Drop Knuck",
        "rise_knuck":       "Rise Knuck",
    }

    def _label(pt: str) -> str:
        if pt in _OVERRIDES:
            return _OVERRIDES[pt]
        return pt.replace("_", " ").title()

    def _tier(grade: int) -> str:
        if grade >= 70: return "elite"
        if grade >= 60: return "plus"
        if grade >= 50: return "avg"
        if grade >= 40: return "fringe"
        return "org"

    out = []
    for e in raw:
        if not isinstance(e, dict) or not e.get("pitch_type"):
            continue
        q = float(e.get("quality", 0.5) or 0.5)
        grade = max(20, min(80, int(round(20 + q * 60))))
        out.append({
            "pitch_type":   e["pitch_type"],
            "label":        _label(e["pitch_type"]),
            "quality":      q,
            "usage_weight": float(e.get("usage_weight", 0.0) or 0.0),
            "grade":        grade,
            "tier":         _tier(grade),
        })
    out.sort(key=lambda r: r["usage_weight"], reverse=True)
    return out


def _money(g) -> Markup:
    """Render a guilder amount as a `<span class="o27-money">` cell with
    pre-baked guilder / USD / EUR labels and a clickable pill. The pill
    handler in base.html cycles between modes by swapping the visible
    label, so each money cell carries everything the toggle needs."""
    try:
        n = int(g or 0)
    except (TypeError, ValueError):
        n = 0
    label_g = currency.format_money(n, "guilder")
    label_u = currency.format_money(n, "usd")
    label_e = currency.format_money(n, "eur")
    label_z = currency.format_money(n, "zora")
    return Markup(
        f'<span class="o27-money" data-g="{n}" '
        f'data-label-guilder="{label_g}" '
        f'data-label-usd="{label_u}" '
        f'data-label-eur="{label_e}" '
        f'data-label-zora="{label_z}">'
        f'<span class="o27-money-label">{label_g}</span>'
        f'<button type="button" class="o27-money-pill" '
        f'aria-label="Toggle currency display">{currency.GUILDER}</button>'
        f'</span>'
    )
