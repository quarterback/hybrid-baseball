"""
O27v2 Flask web application.

Routes:
  GET  /                  Scores dashboard — today's games, recent finals, division leaders, top-5 leaders
  GET  /standings         Full standings — one wide table per league, sortable
  GET  /schedule          Full schedule (filter: team, status)
  GET  /game/<id>         Box score for a completed game
  GET  /players           Browseable player index (server-paginated, sortable, filterable)
  GET  /player/<id>       Single player season + game log
  GET  /teams             Team list
  GET  /team/<id>         Team header + batting roster + pitching roster + last 10 games
  GET  /leaders           Season-to-date leaderboards (replaces /stats; /stats redirects here)
  GET  /transactions      League transaction log (filterable by team / type)
  GET  /new-league        League-creation screen
  POST /new-league        Apply the chosen config (reset DB + reseed)
  POST /api/sim           Simulate the next N games (JSON response)
"""
from __future__ import annotations
import math
import os
import sys

_workspace = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _workspace not in sys.path:
    sys.path.insert(0, _workspace)

from flask import Flask, render_template, request, redirect, url_for, jsonify, abort, Response

from o27v2 import db, currency, valuation
from o27v2.web import text_export
from o27v2.sim import (
    simulate_game,
    simulate_next_n,
    simulate_date,
    simulate_through,
    get_current_sim_date,
    get_last_scheduled_date,
    get_all_star_date,
    is_season_complete,
    advance_sim_clock,
    resync_sim_clock,
    get_earliest_unplayed_date,
)
from o27v2.league import get_league_configs

import datetime as _dt

app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = "o27v2-dev-key"


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


app.jinja_env.filters["scout"] = _scout


def _flag(country_code) -> str:
    """Render an ISO 3166-1 alpha-2 country code as a flag emoji.

    Two regional-indicator code points (U+1F1E6..U+1F1FF). Empty / invalid
    codes render as empty string so templates can unconditionally
    `{{ p.country | flag }}` next to player names.
    """
    if not country_code:
        return ""
    s = str(country_code).strip().upper()
    if len(s) != 2 or not s.isalpha():
        return ""
    base = 0x1F1E6
    a = ord("A")
    return chr(base + ord(s[0]) - a) + chr(base + ord(s[1]) - a)


app.jinja_env.filters["flag"] = _flag


def _archetype_label(key) -> str:
    """Convert a manager archetype key (e.g. 'mad_scientist') to its
    human label (e.g. 'Mad Scientist'). Empty / unknown keys fall back
    to the snake_case key so templates render *something* rather than
    breaking."""
    if not key:
        return ""
    from o27v2.managers import archetype_label
    return archetype_label(str(key))


app.jinja_env.filters["archetype_label"] = _archetype_label


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


app.jinja_env.filters["rating_stars"] = _rating_stars


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


app.jinja_env.filters["park_dimensions"] = _park_dimensions


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


app.jinja_env.filters["park_quirks"] = _park_quirks


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


app.jinja_env.filters["park_shape_meta"] = _park_shape_meta


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


app.jinja_env.filters["repertoire"] = _repertoire


from markupsafe import Markup as _Markup  # noqa: E402


def _money(g) -> _Markup:
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
    return _Markup(
        f'<span class="o27-money" data-g="{n}" '
        f'data-label-guilder="{label_g}" '
        f'data-label-usd="{label_u}" '
        f'data-label-eur="{label_e}">'
        f'<span class="o27-money-label">{label_g}</span>'
        f'<button type="button" class="o27-money-pill" '
        f'aria-label="Toggle currency display">{currency.GUILDER}</button>'
        f'</span>'
    )


app.jinja_env.filters["money"] = _money


@app.context_processor
def inject_currency_rates():
    return {"currency_rates": currency.rates_for_js()}


@app.context_processor
def inject_sim_state():
    return {"sim": {
        "current_date":   get_current_sim_date(),
        "all_star_date":  get_all_star_date(),
        "last_date":      get_last_scheduled_date(),
        "season_complete": is_season_complete(),
    }}


# ---- App version footer -------------------------------------------------
# Computed once at process start so the footer reflects the actual code
# loaded into THIS process. After a Fly redeploy the new image starts a
# new process and `_APP_VERSION` is recomputed with the new SHA. After a
# bare machine restart of the same image, the SHA is the same but
# `_APP_BOOTED_AT` advances — so the user can tell whether they're
# looking at a fresh deploy or just a restarted image.

def _resolve_app_version() -> dict:
    """Resolve the running build's short SHA without requiring `git` to be
    on the container's PATH. Reads `.git/HEAD` directly — works in
    `python:3.12-slim` where the git binary isn't installed."""
    sha   = os.environ.get("APP_VERSION") or ""
    dirty = False
    if not sha:
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        try:
            head_path = os.path.join(repo_root, ".git", "HEAD")
            with open(head_path) as f:
                head = f.read().strip()
            if head.startswith("ref:"):
                ref = head.split(" ", 1)[1].strip()
                # Try the loose ref file first; fall back to packed-refs if
                # the branch was packed by the deploy step.
                ref_path = os.path.join(repo_root, ".git", ref)
                if os.path.exists(ref_path):
                    with open(ref_path) as f:
                        sha = f.read().strip()
                else:
                    packed = os.path.join(repo_root, ".git", "packed-refs")
                    if os.path.exists(packed):
                        with open(packed) as f:
                            for line in f:
                                line = line.strip()
                                if line.endswith(" " + ref):
                                    sha = line.split(" ", 1)[0]
                                    break
            else:
                # Detached HEAD — full SHA written directly.
                sha = head
            sha = sha[:7] if sha else ""
        except Exception:
            sha = ""

        # Best-effort dirty check via `git status --porcelain` (only if git
        # is actually available — we don't want to fail the page render).
        try:
            import subprocess
            status = subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=repo_root,
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).decode().strip()
            dirty = bool(status)
        except Exception:
            pass
    return {"sha": sha or "dev", "dirty": dirty}


_APP_VERSION_INFO = _resolve_app_version()
_APP_BOOTED_AT    = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


@app.context_processor
def inject_app_version():
    return {"app_version": {
        "sha":       _APP_VERSION_INFO["sha"],
        "dirty":     _APP_VERSION_INFO["dirty"],
        "booted_at": _APP_BOOTED_AT,
    }}


def _end_of_month(d: _dt.date) -> _dt.date:
    if d.month == 12:
        return _dt.date(d.year, 12, 31)
    return _dt.date(d.year, d.month + 1, 1) - _dt.timedelta(days=1)


def _sim_response(
    from_date: str | None,
    to_date: str | None,
    results: list,
    done: bool = True,
) -> dict:
    errors = [r for r in results if isinstance(r, dict) and "error" in r]
    return {
        "simulated":       len(results) - len(errors),
        "errored":         len(errors),
        "first_error":     (errors[0].get("error") if errors else None),
        "from_date":       from_date,
        "to_date":         to_date,
        "current_date":    get_current_sim_date(),
        "season_complete": is_season_complete(),
        # Bulk-sim endpoints chunk their work to keep each HTTP round-trip
        # short (mobile Safari + Fly proxy drop long requests with a generic
        # "Load failed"). `done=False` → the JS loops and POSTs again until
        # the target is reached.
        "done":            done,
    }


def _had_errors(results: list) -> bool:
    return any(isinstance(r, dict) and "error" in r for r in results)


def _clamp_to_last(date_str: str) -> str:
    last = get_last_scheduled_date()
    if last is None:
        return date_str
    last_plus_one = (_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat()
    return min(date_str, last_plus_one)


# ---------------------------------------------------------------------------
# Dual HTML / JSON renderer. Every data view in the app passes through
# `_serve()` instead of calling `render_template()` directly. Add
# `?format=json` to any URL to get a structured payload suitable for
# scripts, notebooks, or LLM-prompt context.
#
# JSON output shape:
#   {
#     "endpoint": "leaders",
#     "args":     {"side": "pit", "view": "advanced", ...},  # query string echo
#     "data":     {...the same dict the Jinja template received},
#   }
#
# The data block is best-effort JSON-serialized: sqlite3.Row → dict,
# datetime/date → ISO string, callables / Jinja macros / Flask g objects
# / undefined types are dropped (with a key list under "_dropped" so the
# caller can see what was excluded).
# ---------------------------------------------------------------------------

def _jsonable(value, _depth: int = 0):
    """Recursively coerce a render-context value into JSON-friendly form."""
    if _depth > 8:
        return f"<truncated:{type(value).__name__}>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    # sqlite3.Row exposes .keys()
    if hasattr(value, "keys") and callable(value.keys):
        try:
            return {k: _jsonable(value[k], _depth + 1) for k in value.keys()}
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(k): _jsonable(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, _depth + 1) for v in value]
    # Fallback: anything else (callable, custom class, etc.)
    return None


def _serve(template: str, **context):
    """Render `template` for HTML clients, or emit a JSON payload when
    the request has ?format=json. The Jinja-context dict is passed through
    `_jsonable` so the JSON shape mirrors what the template sees.

    Drop-in replacement for `render_template(...)` everywhere a route
    returns table-shaped data. Routes that already return Response /
    jsonify directly (export.md, /api/sim/*) don't go through here.
    """
    fmt = (request.args.get("format") or "").lower()
    if fmt in ("json", "j"):
        # Strip the few keys we know aren't useful to consumers and would
        # only bloat the response (the live `request` object never shows
        # up here, but the sim_state injector adds noise on every page).
        skip = {"sim", "_csrf_token"}
        clean = {k: v for k, v in context.items() if k not in skip}
        dropped: list[str] = []
        out = {}
        for k, v in clean.items():
            j = _jsonable(v)
            if j is None and v is not None:
                dropped.append(k)
            else:
                out[k] = j
        endpoint = (request.endpoint or "").rsplit(".", 1)[-1]
        payload = {
            "endpoint": endpoint,
            "args":     dict(request.args),
            "data":     out,
        }
        if dropped:
            payload["_dropped"] = dropped
        return jsonify(payload)
    return render_template(template, **context)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _divisions() -> dict[str, list[dict]]:
    teams = db.fetchall("SELECT * FROM teams ORDER BY division, wins DESC, losses ASC")
    divs: dict[str, list[dict]] = {}
    for t in teams:
        divs.setdefault(t["division"], []).append(t)
    return divs


def _leagues_with_divisions() -> dict[str, dict[str, list[dict]]]:
    """Return {league_name: {division_name: [team, ...]}} sorted by win pct."""
    teams = db.fetchall(
        "SELECT * FROM teams ORDER BY league, division, wins DESC, losses ASC"
    )
    out: dict[str, dict[str, list[dict]]] = {}
    for t in teams:
        out.setdefault(t["league"], {}).setdefault(t["division"], []).append(t)
    return out


def _active_config() -> dict | None:
    """Return the currently active league config dict, or None when no
    league config is recorded in sim_meta."""
    from o27v2.season_archive import get_active_league_meta
    _seed, cfg_id = get_active_league_meta()
    if not cfg_id:
        return None
    try:
        from o27v2.league import get_config
        return get_config(cfg_id)
    except Exception:
        return None


def _tiered_standings(cfg: dict) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """Build tier-ordered standings + per-tier cut-line metadata for a
    tiered config. Returns (tiers, meta) where:
      tiers   — {tier_name: [team_row, ...]} in seed order (1..N)
      meta    — {tier_name: {position: zone}} with zones in
                {"promotion", "relegation_playoff", "relegation_auto"}.

    Zone assignment is symmetric to `o27v2/promotion.apply_promotion_relegation`:
      * Top N seeds in any tier *below the top tier* are flagged
        "promotion" (would move up).
      * Bottom M seeds in any tier *above the bottom tier* are flagged
        "relegation_auto".
      * The seeds listed in `playoff_seeds` (default [11,12,13]) for any
        tier above the bottom tier are flagged "relegation_playoff".
    """
    tier_order = list(cfg.get("tier_order") or cfg.get("leagues") or [])
    pr = dict(cfg.get("promotion_relegation") or {})
    n_promote = int(pr.get("auto_promote_top_n", 2))
    n_auto    = int(pr.get("auto_relegate_bottom_n", 1))
    use_po    = bool(pr.get("playoff_relegation", True))
    po_seeds  = list(pr.get("playoff_seeds") or [11, 12, 13])

    rows = db.fetchall(
        "SELECT * FROM teams ORDER BY wins DESC, losses ASC, id ASC"
    )
    by_tier: dict[str, list[dict]] = {tier: [] for tier in tier_order}
    for r in rows:
        if r["league"] in by_tier:
            by_tier[r["league"]].append(dict(r))

    meta: dict[str, dict] = {}
    for idx, tier in enumerate(tier_order):
        ranks: dict[int, str] = {}
        not_top    = idx > 0
        not_bottom = idx < len(tier_order) - 1
        n_in_tier  = len(by_tier.get(tier, []))
        if not_top:
            for seed in range(1, n_promote + 1):
                ranks[seed] = "promotion"
        if not_bottom:
            for seed in range(n_in_tier, n_in_tier - n_auto, -1):
                if seed >= 1:
                    ranks[seed] = "relegation_auto"
            if use_po:
                for seed in po_seeds:
                    if 1 <= seed <= n_in_tier and seed not in ranks:
                        ranks[seed] = "relegation_playoff"
        meta[tier] = {
            "ranks":      ranks,
            "is_top":     idx == 0,
            "is_bottom":  idx == len(tier_order) - 1,
            "tier_index": idx,
        }
    return by_tier, meta


def _all_games_played() -> bool:
    """True iff there's at least one game and zero unplayed games. Used
    to gate the 'Run Promotion/Relegation' button on /standings."""
    row = db.fetchone(
        "SELECT COUNT(*) AS total, "
        "       SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played "
        "FROM games"
    )
    if not row or not row["total"]:
        return False
    return (row["played"] or 0) == row["total"]


def _win_pct(t: dict) -> str:
    total = t["wins"] + t["losses"]
    if total == 0:
        return ".000"
    return f".{int(t['wins'] / total * 1000):03d}"


def _gb(leader: dict, team: dict) -> str:
    diff = (leader["wins"] - team["wins"] + team["losses"] - leader["losses"]) / 2
    if diff == 0:
        return "—"
    return f"{diff:.1f}"


# Dedup subquery: collapse duplicate (player_id, game_id) rows in
# game_pitcher_stats (Task #57 audit — pre-#58 the engine could re-insert a
# pitcher's line if they appeared in multiple half-innings, inflating BF/K/G).
# We pick ONE real row per (game_id, player_id) — the row with the most outs,
# breaking ties by lowest rowid (earliest appearance). This avoids the
# "Frankenstein" totals you get from MAX-per-column, which can mix maxima from
# different duplicate rows and overstate stats. Task #58 will add a UNIQUE
# constraint on (player_id, game_id, phase) so this subquery becomes a no-op.
_PSTATS_DEDUP_SQL = """(
    SELECT game_id, player_id, team_id, batters_faced, outs_recorded,
           hits_allowed, runs_allowed, er, bb, k, hr_allowed, pitches,
           hbp_allowed, unearned_runs, sb_allowed, cs_caught, fo_induced,
           er_arc1, er_arc2, er_arc3,
           k_arc1,  k_arc2,  k_arc3,
           fo_arc1, fo_arc2, fo_arc3,
           bf_arc1, bf_arc2, bf_arc3,
           is_starter
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY game_id, player_id
                   ORDER BY outs_recorded DESC, rowid ASC
               ) AS _rn
        FROM game_pitcher_stats
    )
    WHERE _rn = 1
)"""


_SP_OUTS_THRESHOLD = 12  # MLB-style: 5 IP minimum scaled to O27 = 12 outs

# Defensive position-value factors (approx. runs / 162 games range).
# A player with elite defense at SS saves ~12 runs vs neutral over a full
# season; at 1B that's ~4. Pure position value, used for DRS / dWAR.
_POSITION_DRS_RANGE: dict[str, float] = {
    "C":  15.0,
    "SS": 12.0,
    "2B":  8.0,
    "CF":  8.0,
    "3B":  7.0,
    "LF":  5.0,
    "RF":  5.0,
    "1B":  4.0,
    "DH":  0.0,
    "UT":  6.0,    # utility — gets average-of-positions bump
    "P":   2.0,    # pitchers field comebackers / cover bases — small effect
}


_INFIELD_POS_SET  = frozenset(("1B", "2B", "3B", "SS"))
_OUTFIELD_POS_SET = frozenset(("LF", "CF", "RF"))


def _position_defense_for_row(row: dict) -> float:
    """Return the player's effective defense rating at their position
    using a 60% sub-group + 40% general blend. All inputs come from the
    SUM-aggregated row; ints (20-95 grade) and floats (0..1 unit) both
    work via the scout-style 100-divide fallback.
    """
    pos = str(row.get("position") or "")

    def _norm(v):
        if v is None:
            return 0.5
        v = float(v)
        if v <= 1.0:
            return v
        # 20-95 grade scale: extend past 0.85 for elite-plus.
        if v <= 80.0:
            return 0.15 + (v - 20.0) / 60.0 * 0.70
        return 0.85 + (v - 80.0) / 15.0 * 0.15

    general = _norm(row.get("defense"))
    if pos == "C":
        sub = _norm(row.get("defense_catcher"))
    elif pos in _INFIELD_POS_SET:
        sub = _norm(row.get("defense_infield"))
    elif pos in _OUTFIELD_POS_SET:
        sub = _norm(row.get("defense_outfield"))
    else:
        sub = general
    return 0.6 * sub + 0.4 * general


def _pitcher_per_game_decay_map() -> dict[int, dict[str, float]]:
    """Per-pitcher per-appearance Decay map and arc-3 reach rate.

    Returns {player_id: {'decay_pg': float, 'arc3_reach_rate': float,
                          'decay_pg_known': bool, 'g_total': int,
                          'g_arc3_reach': int}}

    `decay_pg` is the mean of per-appearance Decay (raw arc-1 K% −
    arc-3 K% in points) across the pitcher's appearances that had
    BOTH arc-1 sample AND arc-3 sample. This is the "less-noisy"
    sibling to the season-aggregated Decay — it equal-weights games
    instead of weighting by BFs.

    `arc3_reach_rate` is the fraction of the pitcher's appearances
    that reached arc-3 (bf_arc3 > 0). The actual survivor-bias
    signal: a pitcher with low Decay but only 30% arc3-reach rate
    has a Decay computed from his GOOD outings only — the bad ones
    got him pulled before arc-3.

    NOTE on dedup: per-game arc fields can be double-counted by the
    same dedup logic that hits the season aggregates. We MAX across
    duplicate rows for the same (player, game) instead of SUMming —
    the per-row arc data should be identical across duplicates, but
    MAX is safe regardless. If a pitcher genuinely had two spells
    in one game (rare in O27), they collapse into one appearance
    record here.
    """
    rows = db.fetchall(
        """SELECT ps.player_id,
                  ps.game_id,
                  MAX(COALESCE(ps.bf_arc1, 0)) AS bfa1,
                  MAX(COALESCE(ps.bf_arc3, 0)) AS bfa3,
                  MAX(COALESCE(ps.k_arc1,  0) + COALESCE(ps.fo_arc1, 0)) AS ka1,
                  MAX(COALESCE(ps.k_arc3,  0) + COALESCE(ps.fo_arc3, 0)) AS ka3
             FROM game_pitcher_stats ps
             JOIN games g ON g.id = ps.game_id
            WHERE g.played = 1
            GROUP BY ps.player_id, ps.game_id"""
    )
    out: dict[int, dict[str, float]] = {}
    for r in rows:
        pid = r["player_id"]
        slot = out.setdefault(pid, {
            "g_total": 0, "g_arc3_reach": 0,
            "decay_sum": 0.0, "decay_n": 0,
        })
        slot["g_total"] += 1
        bfa1 = r["bfa1"] or 0
        bfa3 = r["bfa3"] or 0
        if bfa3 > 0:
            slot["g_arc3_reach"] += 1
        if bfa1 > 0 and bfa3 > 0:
            kp1 = (r["ka1"] or 0) / bfa1
            kp3 = (r["ka3"] or 0) / bfa3
            slot["decay_sum"] += (kp1 - kp3) * 100.0
            slot["decay_n"]   += 1

    finalized: dict[int, dict[str, float]] = {}
    for pid, slot in out.items():
        g = slot["g_total"]
        finalized[pid] = {
            "g_total":          g,
            "g_arc3_reach":     slot["g_arc3_reach"],
            "arc3_reach_rate":  (slot["g_arc3_reach"] / g) if g else 0.0,
            "decay_pg_known":   slot["decay_n"] > 0,
            "decay_pg":         (slot["decay_sum"] / slot["decay_n"]) if slot["decay_n"] else 0.0,
        }
    return finalized


def _stamp_per_game_decay(rows: list[dict], drift: float = 0.0) -> None:
    """Stamp per-pitcher decay_pg / arc3_reach_rate / g_arc3_reach onto
    aggregated pitcher rows. Same drift correction is applied to
    `decay_pg` so it sits in the same "0 = league norm" frame as the
    season Decay.
    """
    if not rows:
        return
    pmap = _pitcher_per_game_decay_map()
    for r in rows:
        pid = r.get("player_id")
        meta = pmap.get(pid)
        if not meta:
            r["decay_pg"]         = 999.9
            r["decay_pg_raw"]     = 999.9
            r["decay_pg_known"]   = False
            r["arc3_reach_rate"]  = 0.0
            r["g_arc3_reach"]     = 0
            r["g_total"]          = 0
            continue
        r["decay_pg_known"]  = meta["decay_pg_known"]
        r["decay_pg_raw"]    = meta["decay_pg"] if meta["decay_pg_known"] else 999.9
        r["decay_pg"]        = (meta["decay_pg"] - drift) if meta["decay_pg_known"] else 999.9
        r["arc3_reach_rate"] = meta["arc3_reach_rate"]
        r["g_arc3_reach"]    = meta["g_arc3_reach"]
        r["g_total"]         = meta["g_total"]


def _pitcher_wl_map() -> dict[int, dict[str, int]]:
    """Award W/L per MLB-style rules adapted to the O27 27-out-per-side
    structure.

    Winning team:
      - Starting pitcher (earliest appearance, lowest game_pitcher_stats
        rowid) gets the W if they recorded at least _SP_OUTS_THRESHOLD
        (12) outs.
      - Otherwise the W goes to the reliever on the winning team who
        was most effective: max(outs - ER), with a tiebreaker on outs.
        This is a reasonable approximation of the MLB scorer's "most
        effective reliever" rule without modeling lead-state per inning.

    Losing team:
      - The pitcher with the most earned runs allowed gets the L. Ties
        broken toward the pitcher who appeared earlier (took the lead
        loss). This sidesteps the full "pitcher of record at lead change"
        rule but produces stable, defensible attribution.

    Saves are intentionally NOT computed — the user flagged this as
    "hard to figure out how" and it requires lead-state tracking we
    don't currently capture in game_pitcher_stats.
    """
    rows = db.fetchall(
        """SELECT ps.game_id, ps.team_id, ps.player_id,
                  ps.outs_recorded AS outs,
                  ps.runs_allowed  AS runs,
                  ps.er            AS er,
                  ps.rowid         AS rowid,
                  g.winner_id
             FROM game_pitcher_stats ps
             JOIN games g ON g.id = ps.game_id
            WHERE g.played = 1
            ORDER BY ps.game_id, ps.team_id, ps.rowid"""
    )

    # Group by (game_id, team_id) so we can apply the W/L decision logic
    # on each team-game in isolation.
    by_team_game: dict[tuple[int, int], list[dict]] = {}
    winners: dict[int, int | None] = {}
    for r in rows:
        key = (r["game_id"], r["team_id"])
        by_team_game.setdefault(key, []).append(r)
        winners[r["game_id"]] = r["winner_id"]

    out: dict[int, dict[str, int]] = {}
    for (game_id, team_id), pitchers in by_team_game.items():
        # rowid order = appearance order. First entry is the SP.
        winner_id = winners.get(game_id)
        if winner_id is None:
            continue   # tied / unfinished game (shouldn't happen post-SI)

        is_winner = winner_id == team_id
        if is_winner:
            sp = pitchers[0]
            credited = None
            if (sp["outs"] or 0) >= _SP_OUTS_THRESHOLD:
                credited = sp["player_id"]
            else:
                # Most effective reliever: max(outs - ER), tiebreak on outs.
                relievers = pitchers[1:] or pitchers   # fall back to SP if solo
                relievers = sorted(
                    relievers,
                    key=lambda p: ((p["outs"] or 0) - (p["er"] or 0),
                                   p["outs"] or 0),
                    reverse=True,
                )
                credited = relievers[0]["player_id"]
            if credited is not None:
                rec = out.setdefault(credited, {"w": 0, "l": 0})
                rec["w"] += 1
        else:
            # L: pitcher with most ER. Tiebreak: earliest appearance.
            losers = sorted(
                pitchers,
                key=lambda p: (-(p["er"] or 0), p["rowid"]),
            )
            charged = losers[0]["player_id"]
            rec = out.setdefault(charged, {"w": 0, "l": 0})
            rec["l"] += 1
    return out


def _attach_decisions(games: list[dict]) -> None:
    """For finals only, attach `w_pitcher` and `l_pitcher` dicts to each
    game with the format {'name', 'w', 'l'} — last name and the pitcher's
    season W-L through this game. Powers the b-ref-style game-card line
    'W: Bello (2-4)' under the score."""
    if not games:
        return
    finals = [g for g in games if g.get("played")]
    if not finals:
        return
    ids = [g["id"] for g in finals]
    ph = ",".join("?" * len(ids))
    rows = db.fetchall(
        f"""SELECT ps.game_id, ps.team_id, ps.player_id,
                   ps.outs_recorded AS outs, ps.runs_allowed AS runs,
                   ps.er AS er, ps.rowid AS rowid,
                   p.name AS player_name
              FROM game_pitcher_stats ps
              JOIN players p ON ps.player_id = p.id
             WHERE ps.game_id IN ({ph})
             ORDER BY ps.game_id, ps.team_id, ps.rowid""",
        tuple(ids),
    )
    by_team_game: dict[tuple[int, int], list[dict]] = {}
    name_by_id: dict[int, str] = {}
    for r in rows:
        by_team_game.setdefault((r["game_id"], r["team_id"]), []).append(dict(r))
        name_by_id[r["player_id"]] = r["player_name"]

    season_wl = _pitcher_wl_map()

    def _last(name: str) -> str:
        return (name or "").rsplit(" ", 1)[-1]

    for g in finals:
        winner_id = g.get("winner_id")
        if winner_id is None:
            continue
        loser_id = g["away_team_id"] if winner_id == g["home_team_id"] else g["home_team_id"]
        win_pitchers = by_team_game.get((g["id"], winner_id), [])
        lose_pitchers = by_team_game.get((g["id"], loser_id), [])
        # W: SP if 12+ outs else most-effective reliever.
        w_pid = None
        if win_pitchers:
            sp = win_pitchers[0]
            if (sp["outs"] or 0) >= _SP_OUTS_THRESHOLD:
                w_pid = sp["player_id"]
            else:
                relievers = win_pitchers[1:] or win_pitchers
                relievers = sorted(
                    relievers,
                    key=lambda p: ((p["outs"] or 0) - (p["er"] or 0), p["outs"] or 0),
                    reverse=True,
                )
                w_pid = relievers[0]["player_id"]
        # L: pitcher with most ER.
        l_pid = None
        if lose_pitchers:
            losers = sorted(lose_pitchers, key=lambda p: (-(p["er"] or 0), p["rowid"]))
            l_pid = losers[0]["player_id"]
        if w_pid:
            wl = season_wl.get(w_pid, {"w": 0, "l": 0})
            g["w_pitcher"] = {"name": _last(name_by_id.get(w_pid, "")),
                              "w": wl["w"], "l": wl["l"]}
        if l_pid:
            wl = season_wl.get(l_pid, {"w": 0, "l": 0})
            g["l_pitcher"] = {"name": _last(name_by_id.get(l_pid, "")),
                              "w": wl["w"], "l": wl["l"]}


def _attach_hits(games: list[dict]) -> None:
    """Sum hits per (game_id, team_id) from game_batter_stats and attach
    home_hits / away_hits to each game row. Pure roll-up of sim output —
    nothing is invented; if a game wasn't played, both hit totals are None."""
    if not games:
        return
    ids = [g["id"] for g in games]
    ph = ",".join("?" * len(ids))
    rows = db.fetchall(
        f"""SELECT game_id, team_id, SUM(hits) AS h
            FROM game_batter_stats
            WHERE game_id IN ({ph})
            GROUP BY game_id, team_id""",
        tuple(ids),
    )
    by_game: dict[int, dict[int, int]] = {}
    for r in rows:
        by_game.setdefault(r["game_id"], {})[r["team_id"]] = r["h"] or 0
    for g in games:
        team_hits = by_game.get(g["id"], {})
        g["home_hits"] = team_hits.get(g["home_team_id"]) if g.get("played") else None
        g["away_hits"] = team_hits.get(g["away_team_id"]) if g.get("played") else None


def _qualifying_thresholds(games_played: int) -> tuple[int, int]:
    """MLB-style qualifying minimums: 3.1 PA per team-game for batting,
    1 IP (3 outs) per team-game for pitching, with a small absolute floor
    so the leaderboards aren't empty in the first week of a season.

    Used by both the dashboard's Top-5 widget and /leaders so the two
    views agree on who qualifies — otherwise a 5-PA hitter at .800 leads
    one but not the other.
    """
    num_teams_row = db.fetchone("SELECT COUNT(*) as n FROM teams")
    num_teams = (num_teams_row["n"] if num_teams_row else 0) or 2
    games_per_team = max(1, (games_played * 2) // num_teams)
    min_pa   = max(5, int(round(3.1 * games_per_team)))
    min_outs = max(5, 3 * games_per_team)
    return min_pa, min_outs


def _aggregate_batter_rows(rows: list[dict], baselines: dict | None = None) -> None:
    """Mutates rows in place to add classical, advanced, and O27-native
    sabermetrics.

    Adds (classical):  avg, obp (HBP-aware), slg, ops
    Adds (advanced):   iso, babip, k_pct, bb_pct, hr_pct, bb_k, sb_pct
    Adds (O27-native): woba, stay_pct, stay_rbi_per_stay, fo_pct, mhab_pct
    Adds (relative):   ops_plus, woba_plus    [if baselines provided]

    Pass `baselines=_league_baselines()` to enable OPS+ / wOBA+. Without
    baselines the row keys are still set (to 100.0) for templating sanity.
    """
    if baselines is None:
        baselines = {"obp": 0.0, "slg": 0.0, "ops": 0.0, "woba": 0.0}
    # O27 stat semantics:
    #   AVG, SLG, ISO are PA-denominated (NOT AB-denominated). Stays inside
    #   an AB make AB-denominated rates produce strange numbers (you can put
    #   up huge total-base counts inside a small AB sample), so we use PA
    #   throughout. AB is preserved and surfaced as H/AB — a stayer-vs-runner
    #   profile metric — but the canonical batting average is H/PA.
    #   Targets in this run environment: league AVG ~.350-.380, top hitters
    #   .450+; league SLG ~.550-.600, top sluggers approach 1.000.
    for b in rows:
        ab = b.get("ab") or 0
        h = b.get("h") or 0
        bb = b.get("bb") or 0
        pa = b.get("pa") or 0
        d2 = b.get("d2") or 0
        d3 = b.get("d3") or 0
        hr = b.get("hr") or 0
        k = b.get("k") or 0
        hbp = b.get("hbp") or 0
        sb = b.get("sb") or 0
        cs = b.get("cs") or 0
        # PAVG = H/PA — the headline batting average in O27. Bounded
        # 0.000-1.000. League-wide. (The legacy "avg" key is kept as an
        # alias for templates / leader queries that haven't migrated yet.)
        b["pavg"] = (h / pa) if pa else 0.0
        b["avg"]  = b["pavg"]
        # OBP — already PA-denominated as in MLB.
        b["obp"] = ((h + bb + hbp) / pa) if pa else 0.0
        # SLG = total bases / PA (O27 semantic). Per-PA reads cleanly across
        # multi-hit ABs; bounded ~0..1 (a hitter averaging a base per PA is
        # at the ceiling of slugging in this sport).
        singles = h - d2 - d3 - hr
        tb = singles + 2 * d2 + 3 * d3 + 4 * hr
        b["slg"] = (tb / pa) if pa else 0.0
        b["ops"] = b["obp"] + b["slg"]
        # BAVG = H/AB — the secondary "stayer profile" metric. Inherits
        # MLB's batting-average semantics (per-AB rate). In O27 it can
        # exceed 1.000 because multi-hit ABs are real (max 3 hits in 1 AB
        # via stays). Read together with PAVG it diagnoses style:
        #   high PAVG, BAVG ≈ 1.000  → slap-and-go contact hitter
        #   high PAVG, BAVG > 1.0   → productive stayer
        #   low PAVG,  BAVG > 1.0   → tries to stay but gets caught out
        b["bavg"]     = (h / ab) if ab else 0.0
        b["h_per_ab"] = b["bavg"]   # legacy alias
        # Stay differential — how much of the BAVG comes from stays.
        b["stay_diff"] = b["bavg"] - b["pavg"]
        # ISO = SLG - AVG (still works; both PA-denominated).
        b["iso"]    = b["slg"] - b["avg"]
        # BABIP redefined for O27: hits-on-balls-in-play / balls-in-play,
        # where a "ball in play" = any contact event (run-chosen and stay-
        # chosen). Stays count as both numerator (the hit was credited)
        # and denominator (a ball was put in play). The denominator is
        # PA - K - BB - HBP - HR (subtract events that aren't BIPs).
        bip_denom = pa - k - bb - hbp - hr
        b["babip"]  = ((h - hr) / bip_denom) if bip_denom > 0 else 0.0
        b["k_pct"]  = (k  / pa) if pa else 0.0
        b["bb_pct"] = (bb / pa) if pa else 0.0
        b["hr_pct"] = (hr / pa) if pa else 0.0
        b["bb_k"]   = (bb / k)  if k  else (bb * 1.0)
        # Stolen-base success rate on attempts.
        attempts = sb + cs
        b["sb_pct"] = (sb / attempts) if attempts else 0.0

        # --- O27-native sabermetrics ---
        # wOBA with linear weights empirically derived from the league's
        # RE matrix (see `o27v2.analytics.linear_weights`), normalized so
        # league wOBA == league OBP. Walks gain value vs MLB because the
        # bases are fuller more often in 22 R/G; HR loses relative value
        # because singles + walks already clear them. Denominator is PA
        # (NOT AB+BB+HBP) since each PA represents one opportunity;
        # stays inside an AB are separate PAs.
        ww = _linear_weights()["woba_weights"]
        singles = h - d2 - d3 - hr
        woba_num = (
            ww["BB"] * bb + ww["HBP"] * hbp + ww["1B"] * singles +
            ww["2B"] * d2 + ww["3B"]  * d3  + ww["HR"] * hr
        )
        b["woba"] = (woba_num / pa) if pa else 0.0

        # Stay% — share of PAs in which the batter chose to stay (dance
        # the runners). Distinctively O27 — no MLB analog.
        stays_v = b.get("stays") or 0
        b["stay_pct"] = (stays_v / pa) if pa else 0.0
        # Stay-RBI per stay — efficiency of stays. Stays don't always score
        # runners; this surfaces who actually drives in runs while staying.
        stay_rbi = b.get("stay_rbi") or 0
        b["stay_rbi_per_stay"] = (stay_rbi / stays_v) if stays_v else 0.0
        # Stay-RBI%: fraction of total RBI driven by stays. Diagnostic ratio
        # for "is the second-chance AB rule pulling its share of offense?"
        # League-wide ~8-15% under [1,1,1] advancement, higher when stays
        # advance runners aggressively.
        rbi_v = b.get("rbi") or 0
        b["stay_rbi_pct"] = (stay_rbi / rbi_v) if rbi_v else 0.0
        # 2C-Conv%: fraction of valid 2C events that credited a hit
        # (i.e., a runner advanced). Talent-weighted via the eye-vs-command
        # second-swing modifier in contact_quality. Spec target: top hitters
        # 60-70%, marginal 30-40%.
        stay_hits = b.get("stay_hits") or 0
        b["stay_hits"] = stay_hits
        b["stay_conv_pct"] = (stay_hits / stays_v) if stays_v else 0.0
        # Foul-out rate (O27's 3-foul cap). High FO% = batter prone to
        # fouling himself out — a real cost in this rule set.
        fo = b.get("fo") or 0
        b["fo_pct"] = (fo / pa) if pa else 0.0
        # Multi-hit AB% — share of ABs with 2+ credited hits (a stay-led
        # hit-fest in a single AB).
        mhab = b.get("mhab") or 0
        b["mhab_pct"] = (mhab / ab) if ab else 0.0

        # OPS+ / wOBA+ — relativized to live league baselines.
        league_ops  = baselines.get("ops")  or 0
        league_woba = baselines.get("woba") or 0
        b["ops_plus"]  = (b["ops"]  / league_ops  * 100.0) if league_ops  else 100.0
        b["woba_plus"] = (b["woba"] / league_woba * 100.0) if league_woba else 100.0

        # bVORP — value over replacement, in runs.
        # (wOBA - replacement_wOBA) × PA / wOBA_scale ≈ runs above replacement.
        # Uses a simplified wOBA scale of 1.20 (FanGraphs convention).
        repl_woba = baselines.get("replacement_woba") or 0
        woba_scale = 1.20
        b["vorp"] = ((b["woba"] - repl_woba) * pa / woba_scale) if (pa and league_woba) else 0.0

        # --- Defensive value ---
        # DRS = (player_position_defense - 0.5) × 2 × games_played / 162
        #       × position_drs_range. Scales linearly with games played.
        # dWAR = DRS / runs_per_win.
        rpw = baselines.get("runs_per_win") or 10.0
        pos = str(b.get("position") or "")
        games = b.get("g") or 0
        pos_def = _position_defense_for_row(b)
        b["pos_def"] = pos_def
        drs_range = _POSITION_DRS_RANGE.get(pos, 4.0)
        b["drs"] = (pos_def - 0.5) * 2.0 * (games / 162.0) * drs_range if games else 0.0
        b["dwar"] = b["drs"] / rpw if rpw else 0.0
        # bWAR — total batter value = batting WAR + defensive WAR.
        bwar_off = b["vorp"] / rpw if rpw else 0.0
        b["war_off"] = bwar_off
        b["war"] = bwar_off + b["dwar"]

        # --- Per-fielder counting stats ---
        # PO/E come straight off the row; chances and fielding% derive from
        # them. fld_pct is None when the player has zero fielding chances
        # (templates render it as "—").
        po_v = b.get("po") or 0
        e_v  = b.get("e")  or 0
        b["po"]      = po_v
        b["e"]       = e_v
        b["chances"] = po_v + e_v
        b["fld_pct"] = (po_v / (po_v + e_v)) if (po_v + e_v) > 0 else None


_LINEAR_WEIGHTS_CACHE: dict | None = None


def _linear_weights() -> dict:
    """Lazy cache for `derive_linear_weights()` — one DB walk per process.

    Recomputed only on `invalidate_linear_weights()` (called after any
    sim that adds games). The output drives both the wOBA weights in
    `_aggregate_batter_rows` and the Game Score coefficients in
    `_pitcher_game_score`, so consumers see consistent league-derived
    constants.
    """
    global _LINEAR_WEIGHTS_CACHE
    if _LINEAR_WEIGHTS_CACHE is None:
        from o27v2.analytics.linear_weights import derive_linear_weights
        _LINEAR_WEIGHTS_CACHE = derive_linear_weights()
    return _LINEAR_WEIGHTS_CACHE


def invalidate_linear_weights() -> None:
    """Drop the cached linear-weights table — call after sim writes."""
    global _LINEAR_WEIGHTS_CACHE
    _LINEAR_WEIGHTS_CACHE = None


def _pitcher_game_score(
    outs: float, k: float, h: float, er: float, uer: float,
    bb: float, hr: float, fo: float,
) -> float:
    """Per-appearance Game Score for an O27 pitcher row.

    Coefficients are O27-empirical: each penalty/bonus equals the event's
    average run-prevention value (derived from the RE matrix in
    `linear_weights.derive_linear_weights`), scaled at 1 GSc point ≈ 0.5
    runs (MLB convention preserved). The base constant is auto-tuned so
    league-mean starter GSc ≈ 50 in this run environment.

    Result is clamped to [0, 100]. The HR penalty is the *additional*
    cost beyond a generic non-HR hit, since `H` already absorbs the
    average-hit penalty.
    """
    c = _linear_weights()["gsc_coeffs"]
    score = (
        c["base"]
        + c["out"]         * outs
        + c["K_over_out"]  * k
        + c["FO_over_out"] * fo
        - c["H"]           * h
        - c["HR_over_H"]   * hr
        - c["BB"]          * bb
        - c["ER"]          * er
        - c["UER"]         * uer
    )
    if score < 0:
        return 0.0
    if score > 100:
        return 100.0
    return float(score)


# xRA seeded linear-weights coefficients. Non-negative — every event
# contributes ≥ 0 expected runs allowed. Outs and Ks contribute zero.
# v2: separate weights per hit type (Tango linear weights, lightly
# rounded). Doubles and triples aren't persisted at the pitcher row
# level, so per-pitcher non-HR hits are apportioned by the league
# share of 1B/2B/3B among non-HR hits (computed once per render in
# `_league_werra_consts`). xra_norm still anchors league xRA to league
# wERA, so introducing the 2B/3B uplift doesn't drift the league mean.
_XRA_W_HR    = 1.40
_XRA_W_1B    = 0.45
_XRA_W_2B    = 0.78
_XRA_W_3B    = 1.05
_XRA_W_HIT   = _XRA_W_1B   # back-compat alias if anything else imports it
_XRA_W_BB    = 0.32
_XRA_W_HBP   = 0.32


def _league_werra_consts() -> tuple[float, float, float, float, float, float]:
    """Refit (C_w, xra_norm, league_outs_per_g, share_1b, share_2b, share_3b)
    per call.

    - C_w anchors league wERA to league raw ER per 27 outs so wERA reads
      on the same scale as the old ERA: a pitcher whose ER are spread
      proportionally to the league sees wERA ≈ raw-ER/27, while late-arc
      damage pulls the metric up and early-arc damage pulls it down.
    - xra_norm is a multiplicative scaler: per-pitcher raw_xra (sum of
      seeded run values divided by outs * 27) is multiplied by this so
      league xRA == league wERA. Multiplicative anchor (vs. an additive
      constant) keeps every pitcher's xRA ≥ 0 — no more `xFIP -8.81`
      blow-ups in small samples.
    - league_outs_per_g feeds OS+ (league-relative outs share).
    - share_1b/2b/3b are the league shares of singles/doubles/triples
      within non-HR hits, sourced from game_batter_stats. xRA v2 uses
      these to apportion each pitcher's non-HR hits across hit types
      (the pitcher table doesn't break hits-allowed down by type).
      Defaults to MLB-ish 0.85 / 0.12 / 0.03 on an empty DB.

    Falls back to neutral constants on an empty DB.
    """
    row = db.fetchone(
        f"""SELECT COALESCE(SUM(hr_allowed),0)    AS hr,
                   COALESCE(SUM(hits_allowed),0)  AS h,
                   COALESCE(SUM(bb),0)            AS bb,
                   COALESCE(SUM(hbp_allowed),0)   AS hbp,
                   COALESCE(SUM(k),0)             AS k,
                   COALESCE(SUM(fo_induced),0)    AS fo,
                   COALESCE(SUM(er),0)            AS er,
                   COALESCE(SUM(outs_recorded),0) AS outs,
                   COALESCE(SUM(er_arc1),0)       AS era1,
                   COALESCE(SUM(er_arc2),0)       AS era2,
                   COALESCE(SUM(er_arc3),0)       AS era3,
                   COUNT(*)                       AS g
            FROM {_PSTATS_DEDUP_SQL} ps"""
    ) or {}
    outs = row.get("outs") or 0
    g    = row.get("g")    or 0
    # MLB-ish defaults for the 1B/2B/3B shares; only used on an empty DB
    # or when the batter aggregate yields zero non-HR hits.
    _DEFAULT_SHARES = (0.85, 0.12, 0.03)
    if not outs:
        return (1.0, 1.0, 5.0, *_DEFAULT_SHARES)
    league_era = (row["er"] * 27.0) / outs
    league_w_raw = (
        (0.85 * row["era1"] + 1.00 * row["era2"] + 1.20 * row["era3"])
        * 27.0
        / outs
    )
    c_w = (league_era / league_w_raw) if league_w_raw > 0 else 1.0
    league_werra = league_w_raw * c_w  # by construction == league_era

    # League hit-type shares within non-HR hits. Sourced from the batter
    # side because pitcher stats don't persist hits-allowed by type.
    bat_row = db.fetchone(
        """SELECT COALESCE(SUM(hits),0)    AS h,
                  COALESCE(SUM(doubles),0) AS d2,
                  COALESCE(SUM(triples),0) AS d3,
                  COALESCE(SUM(hr),0)      AS hr
           FROM game_batter_stats"""
    ) or {}
    bat_non_hr = max(0, (bat_row.get("h") or 0) - (bat_row.get("hr") or 0))
    if bat_non_hr > 0:
        d2 = bat_row.get("d2") or 0
        d3 = bat_row.get("d3") or 0
        share_2b = d2 / bat_non_hr
        share_3b = d3 / bat_non_hr
        share_1b = max(0.0, 1.0 - share_2b - share_3b)
    else:
        share_1b, share_2b, share_3b = _DEFAULT_SHARES

    # League raw xRA — apportion non-HR hits across 1B/2B/3B using the
    # league shares and the v2 per-type weights. The xra_norm anchor
    # below absorbs whatever absolute level this produces so league xRA
    # still equals league wERA.
    non_hr_hits = max(0, row["h"] - row["hr"])
    per_non_hr_weight = (
        _XRA_W_1B * share_1b + _XRA_W_2B * share_2b + _XRA_W_3B * share_3b
    )
    raw_xra_total = (
        _XRA_W_HR  * row["hr"]
      + per_non_hr_weight * non_hr_hits
      + _XRA_W_BB  * row["bb"]
      + _XRA_W_HBP * row["hbp"]
    )
    league_raw_xra = (raw_xra_total * 27.0 / outs) if outs else 0.0
    xra_norm = (league_werra / league_raw_xra) if league_raw_xra > 0 else 1.0
    league_outs_per_g = (outs / g) if g else 5.0
    return c_w, xra_norm, league_outs_per_g, share_1b, share_2b, share_3b


def _league_baselines() -> dict[str, float]:
    """Compute league baselines for OPS+/ERA+/wOBA+/WAR/VORP relativization.

    Refit every render cycle so the baselines track wherever the live league
    has actually settled — same pattern as the FIP constant. Falls back to
    sensible defaults if no games have been played yet.

    Returns:
      obp, slg, ops, woba         — league-average rate stats
      era, ra27                   — league-average pitching
      replacement_woba            — ~85% of league wOBA (replacement hitter)
      replacement_era             — ~120% of league ERA (replacement pitcher)
      runs_per_win                — Pythagorean-derived; ~18 for O27 vs ~10 MLB
      total_pa, total_outs        — for sample-size sanity in callers
    """
    bat = db.fetchone(
        """SELECT COALESCE(SUM(pa),0)  AS pa,
                  COALESCE(SUM(ab),0)  AS ab,
                  COALESCE(SUM(hits),0) AS h,
                  COALESCE(SUM(doubles),0) AS d2,
                  COALESCE(SUM(triples),0) AS d3,
                  COALESCE(SUM(hr),0)   AS hr,
                  COALESCE(SUM(bb),0)   AS bb,
                  COALESCE(SUM(hbp),0)  AS hbp,
                  COALESCE(SUM(runs),0) AS r
             FROM game_batter_stats"""
    ) or {}
    pit = db.fetchone(
        f"""SELECT COALESCE(SUM(er),0)            AS er,
                   COALESCE(SUM(runs_allowed),0)  AS r,
                   COALESCE(SUM(outs_recorded),0) AS outs,
                   COALESCE(SUM(batters_faced),0) AS bf,
                   COALESCE(SUM(hits_allowed),0)  AS h,
                   COALESCE(SUM(bb),0)            AS bb,
                   COALESCE(SUM(k),0)             AS k,
                   COALESCE(SUM(hr_allowed),0)    AS hr,
                   COALESCE(SUM(fo_induced),0)    AS fo,
                   COALESCE(SUM(unearned_runs),0) AS uer,
                   COALESCE(SUM(er_arc1),0)       AS era1,
                   COALESCE(SUM(er_arc2),0)       AS era2,
                   COALESCE(SUM(er_arc3),0)       AS era3,
                   COALESCE(SUM(k_arc1),0)        AS ka1,
                   COALESCE(SUM(k_arc3),0)        AS ka3,
                   COALESCE(SUM(fo_arc1),0)       AS foa1,
                   COALESCE(SUM(fo_arc3),0)       AS foa3,
                   COALESCE(SUM(bf_arc1),0)       AS bfa1,
                   COALESCE(SUM(bf_arc3),0)       AS bfa3,
                   COUNT(*)                       AS g
              FROM {_PSTATS_DEDUP_SQL} ps"""
    ) or {}

    out: dict[str, float] = {
        "obp": 0.330, "slg": 0.420, "ops": 0.750, "era": 5.00, "ra27": 5.00,
        "woba": 0.330, "replacement_woba": 0.280, "replacement_era": 6.00,
        "runs_per_win": 10.0,
        "total_pa": 0.0, "total_outs": 0.0,
        # New league-level baselines for the wERA / xRA / GSc+ stack.
        "league_werra": 5.00, "league_xra": 5.00, "gsc_avg": 50.0,
    }

    pa = bat.get("pa", 0) or 0
    ab = bat.get("ab", 0) or 0
    h  = bat.get("h", 0)  or 0
    d2 = bat.get("d2", 0) or 0
    d3 = bat.get("d3", 0) or 0
    hr = bat.get("hr", 0) or 0
    bb = bat.get("bb", 0) or 0
    hbp= bat.get("hbp", 0) or 0
    if pa and ab:
        # PA-denominated rate stats (O27 semantic — see _aggregate_batter_rows).
        singles = h - d2 - d3 - hr
        tb      = singles + 2 * d2 + 3 * d3 + 4 * hr
        out["obp"] = (h + bb + hbp) / pa
        out["slg"] = tb / pa
        out["ops"] = out["obp"] + out["slg"]
        # wOBA stays PA-denominated as in MLB; weights tuned for O27 in the
        # batter aggregator and mirrored here so league mean tracks.
        woba_num = 0.72 * bb + 0.74 * hbp + 0.95 * singles + 1.30 * d2 + 1.70 * d3 + 2.05 * hr
        woba_den = pa   # NOT (AB + BB + HBP) — full PA-denominator in O27.
        out["woba"] = (woba_num / woba_den) if woba_den else 0.0
        # Replacement hitter sits ~85% of league wOBA — same convention
        # FanGraphs uses, and it's an easy mental anchor for users.
        out["replacement_woba"] = out["woba"] * 0.85
        out["total_pa"] = float(pa)

    pit_outs = pit.get("outs", 0) or 0
    if pit_outs:
        out["era"]  = (pit.get("er", 0) or 0) * 27.0 / pit_outs
        out["ra27"] = (pit.get("r", 0)  or 0) * 27.0 / pit_outs
        # Replacement pitcher allows ~20% more runs than league average.
        out["replacement_era"] = out["era"] * 1.20
        out["total_outs"] = float(pit_outs)

        # league_werra is anchored to league_era by construction (see
        # _league_werra_consts()); we surface it as its own baseline so
        # the aggregator can read it without re-fitting.
        out["league_werra"] = out["era"]

        # league_xra is anchored to league_werra by xra_norm construction
        # (see _league_werra_consts()).
        out["league_xra"] = out["league_werra"]

        # League arc-1 vs arc-3 K% (incl foul-outs) — used as the zero-point
        # for drift-corrected Decay. In O27 the 27-out single inning cycles
        # to weaker hitters, so league arc-3 K% is structurally higher than
        # arc-1 K% (~3-4 pp) regardless of pitcher fatigue. The drift is
        # this delta; subtracting it from raw Decay isolates the
        # fatigue-driven signal from the lineup-cycling signal.
        bfa1 = pit.get("bfa1") or 0
        bfa3 = pit.get("bfa3") or 0
        if bfa1 > 0 and bfa3 > 0:
            la1 = ((pit.get("ka1") or 0) + (pit.get("foa1") or 0)) / bfa1
            la3 = ((pit.get("ka3") or 0) + (pit.get("foa3") or 0)) / bfa3
            out["league_arc1_k_pct"] = la1
            out["league_arc3_k_pct"] = la3
            # Stored in percentage POINTS (×100) to match the units Decay
            # reports — keeps the subtraction unit-clean downstream.
            out["league_decay_drift"] = (la1 - la3) * 100.0
        else:
            out["league_arc1_k_pct"] = 0.0
            out["league_arc3_k_pct"] = 0.0
            out["league_decay_drift"] = 0.0

        # League-average per-appearance Game Score, computed from
        # per-game means (linear in counts so this matches mean(GSc) for
        # un-clamped outings).
        g = pit.get("g") or 0
        if g:
            out["gsc_avg"] = _pitcher_game_score(
                pit_outs / g,
                (pit.get("k") or 0)  / g,
                (pit.get("h") or 0)  / g,
                (pit.get("er") or 0) / g,
                (pit.get("uer") or 0) / g,
                (pit.get("bb") or 0) / g,
                (pit.get("hr") or 0) / g,
                (pit.get("fo") or 0) / g,
            )

    # Runs-per-win for WAR. Pythagorean-flavored heuristic: in MLB
    # (~9 R/G total), it's ~10. In O27 (~25 R/G total) it's ~18.
    # Formula 9 + sqrt(R/G - per-team) lands roughly correct for both.
    if pit_outs:
        # Total runs across all teams over all games / games-played.
        games_played = db.fetchone("SELECT COUNT(*) AS n FROM games WHERE played=1")["n"] or 0
        if games_played > 0:
            r_per_game = ((pit.get("r", 0) or 0) * 2.0) / games_played   # both teams
            out["runs_per_win"] = max(8.0, 9.0 + (r_per_game / 4.0) ** 0.5 * 3.5)

    return out


def _aggregate_pitcher_rows(
    rows: list[dict],
    wl: dict[int, dict[str, int]] | None = None,
    werra_consts: tuple[float, float, float] | None = None,
    baselines: dict | None = None,
) -> None:
    """Compute the O27 pitcher-stat suite onto each row in place.

    Keys produced:
      werra, xra, decay   — three result-tier metrics
      gsc_avg             — mean Game Score (linear approx from aggregates)
      os_pct              — outs share per appearance (outs/g/27)
      os_plus             — league-relative outs share (100 = league avg)
      aor                 — avg outs per appearance
      ws_pct              — Workhorse Start % (outs ≥ 18 AND ER ≤ 6 in starts)
                            -- approximated; precise computation requires
                            -- per-row data and is done by the player-page
                            -- aggregator separately.
      gsc_plus            — league-relative GSc (100 = league avg, higher = better)
      k_pct, bb_pct, hr_pct (PA-rate; K% includes foul-outs)
      k_minus_bb_pct      — (K - BB) / BF, plain Ks (no foul-outs)
      oavg, babip_allowed, outs_per_pitch, p_per_bf, fo_pct_pit
      vorp, war           — rebased to wERA
      w, l                — from the wl map if provided
    """
    if werra_consts is None:
        werra_consts = _league_werra_consts()
    c_w, xra_norm, league_outs_per_g, share_1b, share_2b, share_3b = werra_consts
    # Pre-blend the per-non-HR-hit weight using league shares so each
    # pitcher's xRA picks up the v2 2B/3B uplift without persisting
    # hit-type breakdowns on game_pitcher_stats.
    per_non_hr_weight = (
        _XRA_W_1B * share_1b + _XRA_W_2B * share_2b + _XRA_W_3B * share_3b
    )
    if baselines is None:
        baselines = {"era": 0.0}
    for p in rows:
        outs = p.get("outs") or 0
        h = p.get("h") or 0
        bb = p.get("bb") or 0
        er = p.get("er") or 0
        k = p.get("k") or 0
        hr = p.get("hr_allowed") or p.get("hra") or 0
        bf = p.get("bf") or p.get("batters_faced") or 0
        hbp_a = p.get("hbp_allowed") or 0
        uer = p.get("unearned_runs") or p.get("uer") or 0
        fo = p.get("fo_induced") or 0
        g  = p.get("g") or 0

        er1, er2, er3 = (p.get("er_arc1") or 0,
                         p.get("er_arc2") or 0,
                         p.get("er_arc3") or 0)
        k1, k2, k3    = (p.get("k_arc1") or 0,
                         p.get("k_arc2") or 0,
                         p.get("k_arc3") or 0)
        fo1, fo2, fo3 = (p.get("fo_arc1") or 0,
                         p.get("fo_arc2") or 0,
                         p.get("fo_arc3") or 0)
        bf1, bf2, bf3 = (p.get("bf_arc1") or 0,
                         p.get("bf_arc2") or 0,
                         p.get("bf_arc3") or 0)
        gs = p.get("gs") or p.get("starts") or 0

        # --- Result-tier: wERA / xRA / Decay ---
        weighted_er = 0.85 * er1 + 1.00 * er2 + 1.20 * er3
        p["werra"] = (weighted_er * 27.0 / outs) * c_w if outs else 0.0
        # xRA v3 — when per-pitcher hit-type shares are available
        # (singles_allowed / doubles_allowed / triples_allowed sums on
        # game_pitcher_stats), use them directly so sinker-heavy arms
        # and four-seam-heavy arms produce different xRA values for the
        # same Stuff. Pre-v3 rows (sums all zero) fall back to the
        # league-share blended weight from v2.
        if outs:
            non_hr_hits = max(0, h - hr)
            s_allowed = p.get("singles_allowed") or 0
            d_allowed = p.get("doubles_allowed") or 0
            t_allowed = p.get("triples_allowed") or 0
            per_pitcher_hit_sum = s_allowed + d_allowed + t_allowed
            if per_pitcher_hit_sum > 0 and per_pitcher_hit_sum <= non_hr_hits + 2:
                # Trust per-pitcher counts (allow tiny rounding tolerance
                # against the legacy non_hr_hits sum).
                hit_run_value = (
                    _XRA_W_1B * s_allowed
                  + _XRA_W_2B * d_allowed
                  + _XRA_W_3B * t_allowed
                )
            else:
                hit_run_value = per_non_hr_weight * non_hr_hits
            raw_xra = (
                _XRA_W_HR  * hr
              + hit_run_value
              + _XRA_W_BB  * bb
              + _XRA_W_HBP * hbp_a
            ) * 27.0 / outs
            p["xra"] = raw_xra * xra_norm
        else:
            p["xra"] = 0.0
        # Decay: drift-corrected K%_arc1 - K%_arc3 (× 100). K% counts
        # foul-outs as Ks. The drift correction subtracts the league's
        # structural arc-1→arc-3 lift (lineup-cycling weakens hitters
        # late in the 27-out inning), so:
        #   0    = pitcher matches league norm
        #   >0   = fades worse than the lineup naturally lifts K%
        #   <0   = holds up better than the lineup-cycle baseline
        # `decay_raw` keeps the un-corrected delta as a diagnostic
        # field — useful when comparing across configs / seasons where
        # the lineup-cycling magnitude itself shifts.
        league_drift = (baselines or {}).get("league_decay_drift", 0.0) if baselines else 0.0
        if bf1 > 0 and bf3 > 0:
            kp1 = (k1 + fo1) / bf1
            kp3 = (k3 + fo3) / bf3
            raw_delta = (kp1 - kp3) * 100.0
            p["decay_raw"] = raw_delta
            p["decay"] = raw_delta - league_drift
            p["decay_known"] = True
        else:
            # Sentinel that sorts to the worst end of "low is better"
            # leaderboards but doesn't break Jinja sort. Templates check
            # `decay_known` to decide whether to render the value.
            p["decay"] = 999.9
            p["decay_raw"] = 999.9
            p["decay_known"] = False

        # LateK% — arc-3-only K% (incl foul-outs). Visible for short-relief
        # specialists who never see arc-1 sample (so Decay is undefined),
        # and useful as a sibling stat to Decay even for starters: a
        # closer's arc-3 K% relative to league arc-3 K% reads cleaner than
        # a Decay number for someone who only pitched the back half.
        # Sentinel None when no arc-3 sample at all.
        if bf3 > 0:
            p["late_k_pct"]   = (k3 + fo3) / bf3
            p["late_k_known"] = True
        else:
            p["late_k_pct"]   = 0.0
            p["late_k_known"] = False
        # Percent-scaled view for the leader-card macro, which formats raw
        # numbers (no transform). Avoids the 0.305 vs 30.5% display quirk.
        p["late_k_pct_pct"] = p["late_k_pct"] * 100.0

        # --- Workload ---
        p["aor"]    = (outs / g) if g else 0.0
        p["os_pct"] = (p["aor"] / 27.0) if g else 0.0  # avg per appearance
        p["os_plus"] = (
            (p["aor"] / league_outs_per_g) * 100.0
        ) if (g and league_outs_per_g) else 100.0
        # Approx GSc avg from per-game means. Linear in counts so the
        # aggregate equals mean-GSc when no individual outing is clamped.
        if g:
            p["gsc_avg"] = _pitcher_game_score(
                outs / g, k / g, h / g, er / g, uer / g, bb / g, hr / g, fo / g
            )
        else:
            p["gsc_avg"] = 0.0
        # ws_pct: Workhorse Start % among starts. The precise per-game
        # check (outs >= 18 AND er <= 6) needs per-row data; an aggregator
        # caller that has it will overwrite this. As a placeholder, leave 0.
        p.setdefault("ws_pct", 0.0)

        # --- PA-rate stats (K% includes foul-outs as locked in spec) ---
        p["k_pct"]  = ((k + fo) / bf) if bf else 0.0
        p["bb_pct"] = (bb / bf) if bf else 0.0
        p["hr_pct"] = (hr / bf) if bf else 0.0
        # K-BB% — plain Ks only (no foul-outs), per current spec.
        # Quick-read dominance signal that doesn't need recalibration.
        p["k_minus_bb_pct"] = ((k - bb) / bf) if bf else 0.0

        # --- Opponent profile (kept) ---
        ab_faced = max(0, bf - bb - hbp_a)
        p["oavg"] = (h / ab_faced) if ab_faced > 0 else 0.0
        bip_denom = ab_faced - k - hr
        p["babip_allowed"] = ((h - hr) / bip_denom) if bip_denom > 0 else 0.0
        # oOPS — opponent OPS. oOBP = (H+BB+HBP)/BF; oSLG approx via
        # (H + 3·HR) / AB_faced because doubles/triples allowed aren't
        # persisted on the pitcher row. Read as "approximate slug."
        p["oobp"] = ((h + bb + hbp_a) / bf) if bf else 0.0
        p["oslg"] = ((h + 3 * hr) / ab_faced) if ab_faced > 0 else 0.0
        p["oops"] = p["oobp"] + p["oslg"]

        # --- Per-pitch ---
        pitches = p.get("pitches") or 0
        p["outs_per_pitch"] = (outs / pitches) if pitches else 0.0
        p["p_per_bf"] = (pitches / bf) if bf else 0.0
        p["fo_pct_pit"] = (fo / bf) if bf else 0.0

        # --- GSc+ (league-relative; replaces ERA+ as headline index) ---
        league_gsc = baselines.get("gsc_avg") or 0.0
        if league_gsc > 0 and p["gsc_avg"] > 0:
            p["gsc_plus"] = (p["gsc_avg"] / league_gsc) * 100.0
        else:
            p["gsc_plus"] = 100.0

        # --- VORP / WAR rebased to wERA ---
        # Replacement wERA = league_werra × 1.2 (carries the existing
        # 120% replacement-anchor convention).
        league_werra_baseline = baselines.get("league_werra") or baselines.get("era") or 0.0
        repl_werra = league_werra_baseline * 1.20
        if outs and repl_werra:
            p["vorp"] = (repl_werra - p["werra"]) * (outs / 27.0)
        else:
            p["vorp"] = 0.0
        rpw = baselines.get("runs_per_win") or 10.0
        p["war"] = p["vorp"] / rpw if rpw else 0.0

        if wl is not None:
            pid = p.get("player_id") or p.get("id")
            d = wl.get(pid, {"w": 0, "l": 0})
            p["w"] = d["w"]
            p["l"] = d["l"]

    # Per-appearance Decay + arc-3 reach rate. One cheap GROUP BY query
    # against game_pitcher_stats; mapped onto every pitcher row in `rows`.
    # decay_pg uses the SAME drift correction as season Decay so both sit
    # in the "0 = league norm" frame. arc3_reach_rate exposes the
    # survivor-bias signal directly: a pitcher whose Decay was computed
    # from only 30% of his appearances is sampling his good days.
    drift = (baselines or {}).get("league_decay_drift", 0.0) if baselines else 0.0
    _stamp_per_game_decay(rows, drift=drift)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    team_count = db.fetchone("SELECT COUNT(*) as n FROM teams")
    if not team_count or team_count["n"] == 0:
        return redirect(url_for("new_league_get"))

    # Date strip lets the user navigate days via prev/next arrows; the
    # ?date= query param overrides the auto-detected current sim date.
    requested = request.args.get("date")
    today = requested or get_current_sim_date()
    today_games = []
    if today:
        today_games = db.fetchall(
            """SELECT g.*,
                      ht.name as home_name, ht.abbrev as home_abbrev,
                      at.name as away_name, at.abbrev as away_abbrev
               FROM games g
               JOIN teams ht ON g.home_team_id = ht.id
               JOIN teams at ON g.away_team_id = at.id
               WHERE g.game_date = ?
               ORDER BY g.id""",
            (today,),
        )
        _attach_hits(today_games)
        _attach_decisions(today_games)
    # Prev / next days that have any scheduled games — for the date strip
    # arrows. We hop by date, not by single days, so a day with no games
    # doesn't render as a dead-end.
    prev_date = None
    next_date = None
    if today:
        r = db.fetchone(
            "SELECT MAX(game_date) AS d FROM games WHERE game_date < ?",
            (today,),
        )
        prev_date = r["d"] if r else None
        r = db.fetchone(
            "SELECT MIN(game_date) AS d FROM games WHERE game_date > ?",
            (today,),
        )
        next_date = r["d"] if r else None

    # Yesterday's finals = the most recent date < today with played=1 games.
    yesterday = None
    yesterday_games: list[dict] = []
    last_played = db.fetchone(
        "SELECT MAX(game_date) AS d FROM games WHERE played = 1"
        + (" AND game_date < ?" if today else ""),
        (today,) if today else (),
    )
    if last_played and last_played["d"]:
        yesterday = last_played["d"]
        yesterday_games = db.fetchall(
            """SELECT g.*,
                      ht.name as home_name, ht.abbrev as home_abbrev,
                      at.name as away_name, at.abbrev as away_abbrev
               FROM games g
               JOIN teams ht ON g.home_team_id = ht.id
               JOIN teams at ON g.away_team_id = at.id
               WHERE g.played = 1 AND g.game_date = ?
               ORDER BY g.id""",
            (yesterday,),
        )
        _attach_hits(yesterday_games)
        _attach_decisions(yesterday_games)

    divs = _divisions()

    # Top-5 leaders for AVG / HR / RBI / W / ERA / K. Use the shared
    # qualifying threshold so this widget matches /leaders.
    games_played_row = db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")
    games_played = games_played_row["n"] if games_played_row else 0
    min_pa, min_outs = _qualifying_thresholds(games_played)

    top = {"avg": [], "hr": [], "rbi": [], "w": [], "werra": [], "k": []}
    baselines = _league_baselines()
    if games_played > 0:
        batting = db.fetchall(
            """SELECT p.id as player_id, p.name as player_name,
                      t.id as team_id, t.abbrev as team_abbrev,
                      SUM(bs.pa) as pa, SUM(bs.ab) as ab, SUM(bs.hits) as h,
                      SUM(bs.doubles) as d2, SUM(bs.triples) as d3, SUM(bs.hr) as hr,
                      SUM(bs.rbi) as rbi, SUM(bs.bb) as bb
               FROM game_batter_stats bs
               JOIN players p ON bs.player_id = p.id
               JOIN teams   t ON bs.team_id = t.id
               GROUP BY p.id
               HAVING SUM(bs.pa) >= ?""",
            (min_pa,),
        )
        _aggregate_batter_rows(batting, baselines=baselines)
        top["avg"] = sorted(batting, key=lambda x: x["avg"], reverse=True)[:5]
        top["hr"]  = sorted(batting, key=lambda x: x["hr"] or 0, reverse=True)[:5]
        top["rbi"] = sorted(batting, key=lambda x: x["rbi"] or 0, reverse=True)[:5]

        pitching = db.fetchall(
            f"""SELECT p.id as player_id, p.name as player_name,
                      t.id as team_id, t.abbrev as team_abbrev,
                      SUM(ps.outs_recorded) as outs,
                      SUM(ps.batters_faced) as bf,
                      SUM(ps.hits_allowed) as h, SUM(ps.runs_allowed) as r,
                      SUM(ps.er) as er,
                      SUM(ps.bb) as bb, SUM(ps.k) as k,
                      SUM(ps.hr_allowed) as hr_allowed,
                      SUM(ps.pitches) as pitches,
                      SUM(ps.hbp_allowed) as hbp_allowed,
                      SUM(ps.unearned_runs) as unearned_runs,
                      SUM(ps.fo_induced) as fo_induced,
                      SUM(ps.er_arc1) as er_arc1, SUM(ps.er_arc2) as er_arc2, SUM(ps.er_arc3) as er_arc3,
                      SUM(ps.k_arc1)  as k_arc1,  SUM(ps.k_arc2)  as k_arc2,  SUM(ps.k_arc3)  as k_arc3,
                      SUM(ps.fo_arc1) as fo_arc1, SUM(ps.fo_arc2) as fo_arc2, SUM(ps.fo_arc3) as fo_arc3,
                      SUM(ps.bf_arc1) as bf_arc1, SUM(ps.bf_arc2) as bf_arc2, SUM(ps.bf_arc3) as bf_arc3,
                      SUM(ps.is_starter) as gs,
                      COUNT(*) as g
               FROM {_PSTATS_DEDUP_SQL} ps
               JOIN players p ON ps.player_id = p.id
               JOIN teams   t ON ps.team_id = t.id
               GROUP BY p.id
               HAVING SUM(ps.outs_recorded) >= ?""",
            (min_outs,),
        )
        wl = _pitcher_wl_map()
        _aggregate_pitcher_rows(pitching, wl, baselines=baselines)
        top["w"]     = sorted(pitching, key=lambda x: x["w"], reverse=True)[:5]
        top["werra"] = sorted(pitching, key=lambda x: x["werra"])[:5]
        top["k"]     = sorted(pitching, key=lambda x: x["k"] or 0, reverse=True)[:5]

    return _serve("index.html",
                           today=today,
                           today_games=today_games,
                           prev_date=prev_date,
                           next_date=next_date,
                           yesterday=yesterday,
                           yesterday_games=yesterday_games,
                           divisions=divs,
                           top=top,
                           win_pct=_win_pct,
                           gb=_gb)


@app.route("/standings")
def standings():
    leagues = _leagues_with_divisions()

    extras: dict[int, dict] = {}
    teams = db.fetchall("SELECT id FROM teams")
    for t in teams:
        tid = t["id"]
        played = db.fetchall(
            """SELECT g.id, g.game_date, g.home_team_id, g.away_team_id,
                      g.home_score, g.away_score, g.winner_id
               FROM games g
               WHERE g.played = 1 AND (g.home_team_id = ? OR g.away_team_id = ?)
               ORDER BY g.game_date, g.id""",
            (tid, tid),
        )
        rs = ra = w10 = l10 = 0
        for g in played:
            if g["home_team_id"] == tid:
                rs += g["home_score"] or 0
                ra += g["away_score"] or 0
            else:
                rs += g["away_score"] or 0
                ra += g["home_score"] or 0
        for g in played[-10:]:
            if g["winner_id"] == tid:
                w10 += 1
            else:
                l10 += 1
        streak = ""
        if played:
            last_won = (played[-1]["winner_id"] == tid)
            count = 0
            for g in reversed(played):
                if (g["winner_id"] == tid) == last_won:
                    count += 1
                else:
                    break
            streak = ("W" if last_won else "L") + str(count)
        last5 = [("w" if g["winner_id"] == tid else "l") for g in played[-5:]]
        # Pythagorean W% — RS² / (RS² + RA²) over actual run differential.
        # Bill James's original 2.0 exponent is fine for O27 (the
        # exponent doesn't change much by run environment for simple
        # reasoning; the pythagopat extension would be tighter but
        # adds complexity for marginal gain).
        if rs + ra > 0:
            pyth_win_pct = (rs * rs) / (rs * rs + ra * ra)
        else:
            pyth_win_pct = 0.5
        # Pythagorean expected W-L in the same number of games played.
        n_games = len(played)
        pyth_w  = round(pyth_win_pct * n_games)
        pyth_l  = n_games - pyth_w
        extras[tid] = {
            "l10":      f"{w10}-{l10}",
            "streak":   streak,
            "rs":       rs,
            "ra":       ra,
            "diff":     rs - ra,
            "last5":    last5,
            "pyth_pct": pyth_win_pct,
            "pyth_wl":  f"{pyth_w}-{pyth_l}",
        }

    cfg = _active_config()
    is_tiered = bool(cfg and cfg.get("schedule_mode") == "tiered")
    tier_meta: dict[str, dict] = {}
    tier_order_list: list[str] = []
    tiered_view: dict[str, list[dict]] = {}
    if is_tiered and cfg is not None:
        tiered_view, tier_meta = _tiered_standings(cfg)
        tier_order_list = list(cfg.get("tier_order") or cfg.get("leagues") or [])

    payrolls = {
        row["team_id"]: int(row["payroll"] or 0)
        for row in db.fetchall(
            "SELECT team_id, COALESCE(SUM(salary), 0) AS payroll "
            "FROM players WHERE team_id IS NOT NULL GROUP BY team_id"
        )
    }

    return _serve("standings.html",
                           leagues=leagues,
                           extras=extras,
                           payrolls=payrolls,
                           win_pct=_win_pct,
                           gb=_gb,
                           is_tiered=is_tiered,
                           tier_order=tier_order_list,
                           tier_meta=tier_meta,
                           tiered_view=tiered_view,
                           all_games_played=_all_games_played())


@app.route("/schedule")
def schedule():
    team_id = request.args.get("team", type=int)
    status  = request.args.get("status", "all")

    sql = """
        SELECT g.*,
               ht.name as home_name, ht.abbrev as home_abbrev,
               at.name as away_name, at.abbrev as away_abbrev,
               wt.abbrev as winner_abbrev
        FROM games g
        JOIN teams ht ON g.home_team_id = ht.id
        JOIN teams at ON g.away_team_id = at.id
        LEFT JOIN teams wt ON g.winner_id = wt.id
    """
    where_clauses = []
    params: list = []

    if team_id:
        where_clauses.append("(g.home_team_id = ? OR g.away_team_id = ?)")
        params += [team_id, team_id]
    if status == "played":
        where_clauses.append("g.played = 1")
    elif status == "unplayed":
        where_clauses.append("g.played = 0")

    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += " ORDER BY g.game_date, g.id LIMIT 200"

    games       = db.fetchall(sql, tuple(params))
    teams       = db.fetchall("SELECT id, name, abbrev FROM teams ORDER BY name")
    selected_team = None
    if team_id:
        selected_team = db.fetchone("SELECT * FROM teams WHERE id = ?", (team_id,))

    return _serve("schedule.html",
                           games=games,
                           teams=teams,
                           selected_team=selected_team,
                           status=status)


@app.route("/game/<int:game_id>")
def game_detail(game_id: int):
    game = db.fetchone(
        """SELECT g.*,
                  ht.name as home_name, ht.abbrev as home_abbrev,
                  ht.park_name as home_park_name,
                  ht.park_dimensions as home_park_dimensions,
                  ht.park_shape as home_park_shape,
                  ht.park_quirks as home_park_quirks,
                  ht.park_hr as home_park_hr, ht.park_hits as home_park_hits,
                  at.name as away_name, at.abbrev as away_abbrev,
                  wt.name as winner_name
           FROM games g
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           LEFT JOIN teams wt ON g.winner_id = wt.id
           WHERE g.id = ?""", (game_id,)
    )
    if not game:
        abort(404)

    prev_game = db.fetchone(
        """SELECT id FROM games
           WHERE played = 1
             AND (game_date < ? OR (game_date = ? AND id < ?))
           ORDER BY game_date DESC, id DESC LIMIT 1""",
        (game["game_date"], game["game_date"], game_id),
    )
    next_game = db.fetchone(
        """SELECT id FROM games
           WHERE played = 1
             AND (game_date > ? OR (game_date = ? AND id > ?))
           ORDER BY game_date ASC, id ASC LIMIT 1""",
        (game["game_date"], game["game_date"], game_id),
    )

    # Task #58: pull per-phase rows and group them. Phase 0 = regulation;
    # phase N>=1 = super-inning round N. We also build per-phase totals
    # rows (suitable for the Game Totals section in the template).
    away_batting_rows = db.fetchall(
        """SELECT bs.*, p.name as player_name, p.country as player_country,
                  p.bats as player_bats, p.throws as player_throws,
                  CASE WHEN p.is_joker = 1 THEN 'J' ELSE p.position END as position,
                  COALESCE(NULLIF(bs.game_position, ''),
                           CASE WHEN p.is_joker = 1 THEN 'J' ELSE p.position END) AS box_position,
                  COALESCE(bs.entry_type, 'starter') AS entry_type,
                  bs.replaced_player_id AS replaced_player_id
           FROM game_batter_stats bs JOIN players p ON bs.player_id = p.id
           WHERE bs.game_id = ? AND bs.team_id = ? ORDER BY bs.phase, bs.id""",
        (game_id, game["away_team_id"]))
    home_batting_rows = db.fetchall(
        """SELECT bs.*, p.name as player_name, p.country as player_country,
                  p.bats as player_bats, p.throws as player_throws,
                  CASE WHEN p.is_joker = 1 THEN 'J' ELSE p.position END as position,
                  COALESCE(NULLIF(bs.game_position, ''),
                           CASE WHEN p.is_joker = 1 THEN 'J' ELSE p.position END) AS box_position,
                  COALESCE(bs.entry_type, 'starter') AS entry_type,
                  bs.replaced_player_id AS replaced_player_id
           FROM game_batter_stats bs JOIN players p ON bs.player_id = p.id
           WHERE bs.game_id = ? AND bs.team_id = ? ORDER BY bs.phase, bs.id""",
        (game_id, game["home_team_id"]))
    away_pitching_rows = db.fetchall(
        """SELECT ps.*, p.name as player_name, p.country as player_country,
                  p.throws as player_throws
           FROM game_pitcher_stats ps JOIN players p ON ps.player_id = p.id
           WHERE ps.game_id = ? AND ps.team_id = ? ORDER BY ps.phase, ps.id""",
        (game_id, game["away_team_id"]))
    home_pitching_rows = db.fetchall(
        """SELECT ps.*, p.name as player_name, p.country as player_country,
                  p.throws as player_throws
           FROM game_pitcher_stats ps JOIN players p ON ps.player_id = p.id
           WHERE ps.game_id = ? AND ps.team_id = ? ORDER BY ps.phase, ps.id""",
        (game_id, game["home_team_id"]))

    team_phase_outs_rows = db.fetchall(
        """SELECT team_id, phase, unattributed_outs FROM team_phase_outs
           WHERE game_id = ?""", (game_id,))

    # Batted-ball physics — BIP events with EV/LA/spray for the spray
    # chart. Joined to players for the hover label. Pre-compute plot
    # (x, y) for the SVG so the template doesn't need math beyond
    # cosmetic rendering.
    spray_bips_raw = db.fetchall(
        """SELECT pa.team_id, pa.batter_id, pa.exit_velocity AS ev,
                  pa.launch_angle  AS la, pa.spray_angle  AS spray,
                  pa.hit_type, p.name AS batter_name
           FROM game_pa_log pa
           JOIN players p ON pa.batter_id = p.id
           WHERE pa.game_id = ?
             AND pa.exit_velocity IS NOT NULL
           ORDER BY pa.ab_seq, pa.swing_idx""",
        (game_id,),
    )

    import math as _math
    _OUT_KINDS = {"ground_out", "fly_out", "line_out", "fielders_choice",
                  "double_play", "triple_play"}

    def _hit_class(ht: str) -> str:
        if ht in ("hr", "home_run"):           return "hr"
        if ht in ("double", "triple"):         return "xbh"
        if ht in ("single", "infield_single"): return "single"
        if ht == "error":                       return "error"
        return "out"

    def _bip_distance_ft(ev: float, la: float) -> float:
        """Heuristic batted-ball distance from EV / LA. Not physical —
        just produces visually plausible spray-chart points.
        Grounders cluster on the infield; line drives reach the
        outfield; high LA + high EV reach the wall.
        """
        if la is None or ev is None:
            return 0.0
        # Below 8° = grounder / chopper — stays in the infield.
        if la < 8:
            return max(40.0, ev * 0.9)
        # Approximate projectile range, with a softening factor so that
        # a 100mph 28° line drive doesn't fly out of the canvas.
        rad = la * _math.pi / 180.0
        d = (ev * ev * _math.sin(2 * rad)) / 36.0
        return max(60.0, min(d, 430.0))

    # SVG layout constants — match the template.
    _SVG_W, _SVG_H = 560.0, 420.0
    _HP_X, _HP_Y = _SVG_W / 2.0, _SVG_H - 30.0
    _FT_TO_PX = 0.85

    def _bip_xy(spray: float, distance_ft: float) -> tuple[float, float]:
        rad = (spray or 0.0) * _math.pi / 180.0
        dx = distance_ft * _math.sin(rad)
        dy = distance_ft * _math.cos(rad)
        return (_HP_X + dx * _FT_TO_PX, _HP_Y - dy * _FT_TO_PX)

    away_bips, home_bips = [], []
    for r in spray_bips_raw:
        d = dict(r)
        dist = _bip_distance_ft(d.get("ev") or 0, d.get("la") or 0)
        x, y = _bip_xy(d.get("spray") or 0.0, dist)
        d["dist_ft"]   = round(dist)
        d["x"]         = round(x, 1)
        d["y"]         = round(y, 1)
        d["hit_class"] = _hit_class(d.get("hit_type") or "")
        if d["team_id"] == game["away_team_id"]:
            away_bips.append(d)
        elif d["team_id"] == game["home_team_id"]:
            home_bips.append(d)

    # Park dimensions for the spray-chart fence. Both teams batted at
    # the home park, so both charts share the same outline. Pre-compute
    # the 5 fence points (LF → LCF → CF → RCF → RF) in SVG coords so
    # the template can draw a path directly.
    import json as _json_loc
    park_dims = {}
    try:
        if game.get("home_park_dimensions"):
            park_dims = _json_loc.loads(game["home_park_dimensions"]) or {}
    except (ValueError, TypeError):
        park_dims = {}
    fence_points = []
    if park_dims:
        # Angles from CF outward — symmetrical around 0°.
        # Foul lines are ±45°; left-center / right-center sit at ±22.5°.
        for (angle_deg, key) in (
            (-45.0, "lf"),
            (-22.5, "lcf"),
            (  0.0, "cf"),
            ( 22.5, "rcf"),
            ( 45.0, "rf"),
        ):
            d_ft = float(park_dims.get(key, 380))
            x, y = _bip_xy(angle_deg, d_ft)
            fence_points.append((round(x, 1), round(y, 1)))

    # Legacy data (pre-Task-#58) often has duplicate rows for the same
    # (player_id, game_id) because the schema lacked a UNIQUE constraint
    # and re-sims of the same game inserted parallel copies. New rows
    # are unique on (player_id, game_id, phase). Aggregate duplicates
    # here so the box score never shows the same player twice in one
    # phase or double-counts totals.
    _BAT_NUM = ("pa", "ab", "runs", "hits", "doubles", "triples",
                "hr", "rbi", "bb", "k", "stays", "outs_recorded",
                "hbp", "sb", "cs", "fo", "multi_hit_abs", "stay_rbi",
                "stay_hits", "gidp", "gitp", "roe", "po", "e")
    _PIT_NUM = ("batters_faced", "outs_recorded", "hits_allowed",
                "runs_allowed", "er", "bb", "k", "hr_allowed", "pitches",
                "hbp_allowed", "unearned_runs",
                "sb_allowed", "cs_caught", "fo_induced",
                "er_arc1", "er_arc2", "er_arc3",
                "k_arc1",  "k_arc2",  "k_arc3",
                "fo_arc1", "fo_arc2", "fo_arc3",
                "bf_arc1", "bf_arc2", "bf_arc3",
                "is_starter")

    def _dedup_by_player_phase(rows: list, num_fields: tuple) -> list:
        merged: dict[tuple, dict] = {}
        order: list[tuple] = []
        for r in rows:
            key = (r["phase"] or 0, r["player_id"])
            if key not in merged:
                merged[key] = dict(r)
                order.append(key)
            else:
                acc = merged[key]
                for f in num_fields:
                    acc[f] = (acc.get(f) or 0) + (r[f] or 0)
        return [merged[k] for k in order]

    def _group_by_phase(rows: list) -> dict:
        out: dict[int, list] = {}
        for r in rows:
            out.setdefault(r["phase"] or 0, []).append(r)
        return out

    def _aggregate_batting(rows: list) -> dict:
        agg = {f: 0 for f in _BAT_NUM}
        for r in rows:
            for f in agg:
                agg[f] += (r[f] or 0)
        return agg

    def _aggregate_pitching(rows: list) -> dict:
        agg = {f: 0 for f in _PIT_NUM}
        for r in rows:
            for f in agg:
                agg[f] += (r[f] or 0)
        return agg

    away_batting_rows = _dedup_by_player_phase(away_batting_rows, _BAT_NUM)
    home_batting_rows = _dedup_by_player_phase(home_batting_rows, _BAT_NUM)
    away_pitching_rows = _dedup_by_player_phase(away_pitching_rows, _PIT_NUM)
    home_pitching_rows = _dedup_by_player_phase(home_pitching_rows, _PIT_NUM)

    # Per-player Game Totals (one row per player across all phases).
    # Spec: "Game Totals — one consolidated row per player across all
    # phases." Distinct from the team-totals row at the bottom of each
    # per-phase table.
    def _consolidate_per_player(rows: list, num_fields: tuple) -> list:
        merged: dict[int, dict] = {}
        order: list[int] = []
        for r in rows:
            pid = r["player_id"]
            if pid not in merged:
                base = dict(r)
                base["phase"] = None  # consolidated row spans phases
                merged[pid] = base
                order.append(pid)
            else:
                acc = merged[pid]
                for f in num_fields:
                    acc[f] = (acc.get(f) or 0) + (r[f] or 0)
        return [merged[k] for k in order]

    away_batting_consolidated = _consolidate_per_player(away_batting_rows, _BAT_NUM)
    home_batting_consolidated = _consolidate_per_player(home_batting_rows, _BAT_NUM)
    away_pitching_consolidated = _consolidate_per_player(away_pitching_rows, _PIT_NUM)
    home_pitching_consolidated = _consolidate_per_player(home_pitching_rows, _PIT_NUM)

    # Run consolidated rows through the shared aggregator helpers so the
    # box score gets the full sabermetric suite (PAVG/OBP/SLG/OPS/wOBA
    # for batters; ERA/FIP/WHIP/K-27 for pitchers). The aggregators expect
    # short-form keys (h, d2, d3, hr_allowed) — map from the SQL column
    # names in-place before calling them.
    baselines = _league_baselines()
    wl = _pitcher_wl_map()

    def _decorate_batters(rows: list) -> None:
        for r in rows:
            r["h"]  = r.get("hits", 0)
            r["d2"] = r.get("doubles", 0)
            r["d3"] = r.get("triples", 0)
            r["g"]  = 1   # one game; aggregator divides by g for some rates
        _aggregate_batter_rows(rows, baselines=baselines)

    def _decorate_pitchers(rows: list) -> None:
        for r in rows:
            r["bf"]   = r.get("batters_faced", 0)
            r["outs"] = r.get("outs_recorded", 0)
            r["h"]    = r.get("hits_allowed", 0)
            r["r"]    = r.get("runs_allowed", 0)
            r["g"]    = 1
        _aggregate_pitcher_rows(rows, wl=wl, baselines=baselines)

    _decorate_batters(away_batting_consolidated)
    _decorate_batters(home_batting_consolidated)
    _decorate_pitchers(away_pitching_consolidated)
    _decorate_pitchers(home_pitching_consolidated)
    # Decorate per-phase rows too so each row in the box score carries
    # GSc / K-BB% / oOPS for the new column set.
    _decorate_pitchers(away_pitching_rows)
    _decorate_pitchers(home_pitching_rows)

    def _decorate_team_totals(rows: list) -> dict:
        """Build the per-team Totals row used in the pitching macro and
        run it through the aggregator so it carries gsc_avg / werra etc."""
        agg = _aggregate_pitching(rows)
        # _aggregate_pitching only sums the _PIT_NUM fields. Stamp the
        # short keys the aggregator expects, then decorate.
        agg["bf"]   = agg.get("batters_faced", 0)
        agg["outs"] = agg.get("outs_recorded", 0)
        agg["h"]    = agg.get("hits_allowed", 0)
        agg["r"]    = agg.get("runs_allowed", 0)
        agg["g"]    = max(1, len(rows))
        _aggregate_pitcher_rows([agg], wl=wl, baselines=baselines)
        return agg

    away_pitching_total = _decorate_team_totals(away_pitching_rows)
    home_pitching_total = _decorate_team_totals(home_pitching_rows)

    away_batting_by_phase = _group_by_phase(away_batting_rows)
    home_batting_by_phase = _group_by_phase(home_batting_rows)
    away_pitching_by_phase = _group_by_phase(away_pitching_rows)
    home_pitching_by_phase = _group_by_phase(home_pitching_rows)

    # Determine which phases to render. Always include 0; include N>=1
    # only if any side actually played that phase (super-inning round).
    all_phases: set[int] = {0}
    for d in (away_batting_by_phase, home_batting_by_phase,
              away_pitching_by_phase, home_pitching_by_phase):
        all_phases.update(d.keys())
    phases = sorted(all_phases)
    si_rounds = max(0, max(phases) if phases else 0)

    # Line score: runs/hits per phase, plus team errors-committed.
    # Errors are stored per-player in game_batter_stats.e (player as a
    # fielder), so the team's E = sum of `e` across that team's batter rows.
    def _line_score(b_by_phase: dict) -> dict:
        runs_per = {ph: sum(r["runs"] or 0 for r in rows)
                    for ph, rows in b_by_phase.items()}
        hits_per = {ph: sum(r["hits"] or 0 for r in rows)
                    for ph, rows in b_by_phase.items()}
        errs_per = {ph: sum((r["e"] or 0) for r in rows)
                    for ph, rows in b_by_phase.items()}
        return {
            "runs":   runs_per,
            "hits":   hits_per,
            "errors": errs_per,
            "total_r": sum(runs_per.values()),
            "total_h": sum(hits_per.values()),
            "total_e": sum(errs_per.values()),
        }

    away_line = _line_score(away_batting_by_phase)
    home_line = _line_score(home_batting_by_phase)

    # Game Notes: per-side unattributed outs by phase (CS / FC / pickoff
    # outs the engine couldn't charge to a specific batter).
    notes: list[dict] = []
    team_name_by_id = {
        game["away_team_id"]: game["away_name"],
        game["home_team_id"]: game["home_name"],
    }
    for r in team_phase_outs_rows:
        if (r["unattributed_outs"] or 0) <= 0:
            continue
        phase_label = "Regulation" if r["phase"] == 0 else f"SI Round {r['phase']}"
        notes.append({
            "team":  team_name_by_id.get(r["team_id"], "?"),
            "phase": r["phase"],
            "phase_label": phase_label,
            "outs":  r["unattributed_outs"],
        })

    from o27.engine.weather import Weather
    weather_label = Weather.from_row(game).short_label()

    # Season HR totals through this game — for the box-score "HR: Smith (12)"
    # annotation. One round-trip per side; cheap.
    def _season_hr_through(team_id: int) -> dict[int, int]:
        rows = db.fetchall(
            """SELECT bs.player_id AS pid, SUM(bs.hr) AS hr_total
               FROM game_batter_stats bs JOIN games g ON bs.game_id = g.id
               WHERE bs.team_id = ?
                 AND (g.game_date < ? OR (g.game_date = ? AND g.id <= ?))
               GROUP BY bs.player_id""",
            (team_id, game["game_date"], game["game_date"], game_id),
        )
        return {r["pid"]: int(r["hr_total"] or 0) for r in rows}
    away_season_hr = _season_hr_through(game["away_team_id"])
    home_season_hr = _season_hr_through(game["home_team_id"])
    for r in away_batting_consolidated:
        r["season_hr"] = away_season_hr.get(r["player_id"], 0)
    for r in home_batting_consolidated:
        r["season_hr"] = home_season_hr.get(r["player_id"], 0)

    # Newspaper-style plaintext box score. Built from the consolidated
    # per-player rows and the line-score totals computed above. Rendered
    # in the template as a single <pre> block — no internal HTML chrome.
    from .box_score import render_box_score as _render_box_score
    game_for_box = dict(game)
    game_for_box["weather_label"] = weather_label
    # Decisions map: pitcher_id → "W" / "L" / "S".
    _decisions: dict[int, str] = {}
    for prow in away_pitching_consolidated + home_pitching_consolidated:
        pid = prow.get("player_id")
        if pid is None:
            continue
        if (prow.get("w") or 0) > 0:
            _decisions[pid] = "W"
        elif (prow.get("l") or 0) > 0:
            _decisions[pid] = "L"
        elif (prow.get("sv") or 0) > 0:
            _decisions[pid] = "S"
    box_score_text = _render_box_score(
        game=game_for_box,
        phases=phases,
        away_line=away_line,
        home_line=home_line,
        away_batting=away_batting_consolidated,
        home_batting=home_batting_consolidated,
        away_pitching=away_pitching_consolidated,
        home_pitching=home_pitching_consolidated,
        decisions=_decisions,
    )

    return _serve(
        "game.html",
        game=game,
        phases=phases,
        si_rounds=si_rounds,
        away_batting_by_phase=away_batting_by_phase,
        home_batting_by_phase=home_batting_by_phase,
        away_pitching_by_phase=away_pitching_by_phase,
        home_pitching_by_phase=home_pitching_by_phase,
        away_batting_total=_aggregate_batting(away_batting_rows),
        home_batting_total=_aggregate_batting(home_batting_rows),
        away_pitching_total=away_pitching_total,
        home_pitching_total=home_pitching_total,
        away_batting_consolidated=away_batting_consolidated,
        home_batting_consolidated=home_batting_consolidated,
        away_pitching_consolidated=away_pitching_consolidated,
        home_pitching_consolidated=home_pitching_consolidated,
        away_line=away_line,
        home_line=home_line,
        game_notes=notes,
        weather_label=weather_label,
        box_score_text=box_score_text,
        away_bips=away_bips,
        home_bips=home_bips,
        park_dims=park_dims,
        fence_points=fence_points,
        prev_game_id=(prev_game["id"] if prev_game else None),
        next_game_id=(next_game["id"] if next_game else None),
    )


# ---------------------------------------------------------------------------
# Markdown text-export endpoints — for forum / GitHub / LLM paste.
# Each returns Content-Type: text/plain; charset=utf-8 so browsers display
# the markdown source verbatim instead of trying to render it as HTML.
# ---------------------------------------------------------------------------

def _md_response(text: str):
    return Response(text, mimetype="text/plain; charset=utf-8")


@app.route("/game/<int:game_id>/export.md")
def game_detail_export(game_id: int):
    """Markdown box-score export. Re-uses the same data-prep as the HTML
    route but skips the phase splits, navigation, game notes, etc."""
    game = db.fetchone(
        """SELECT g.*,
                  ht.name as home_name, ht.abbrev as home_abbrev,
                  at.name as away_name, at.abbrev as away_abbrev
           FROM games g
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE g.id = ?""", (game_id,)
    )
    if not game:
        abort(404)

    away_b = db.fetchall(
        """SELECT bs.*, p.name as player_name,
                  CASE WHEN p.is_joker = 1 THEN 'J' ELSE p.position END as position
           FROM game_batter_stats bs JOIN players p ON bs.player_id = p.id
           WHERE bs.game_id = ? AND bs.team_id = ?""",
        (game_id, game["away_team_id"]))
    home_b = db.fetchall(
        """SELECT bs.*, p.name as player_name,
                  CASE WHEN p.is_joker = 1 THEN 'J' ELSE p.position END as position
           FROM game_batter_stats bs JOIN players p ON bs.player_id = p.id
           WHERE bs.game_id = ? AND bs.team_id = ?""",
        (game_id, game["home_team_id"]))
    away_p = db.fetchall(
        """SELECT ps.*, p.name as player_name
           FROM game_pitcher_stats ps JOIN players p ON ps.player_id = p.id
           WHERE ps.game_id = ? AND ps.team_id = ?""",
        (game_id, game["away_team_id"]))
    home_p = db.fetchall(
        """SELECT ps.*, p.name as player_name
           FROM game_pitcher_stats ps JOIN players p ON ps.player_id = p.id
           WHERE ps.game_id = ? AND ps.team_id = ?""",
        (game_id, game["home_team_id"]))

    # Consolidate per-player across phases. Same shape as game_detail's
    # _consolidate_per_player but inlined here so the export is independent.
    _BAT_NUM = ("pa", "ab", "runs", "hits", "doubles", "triples", "hr",
                "rbi", "bb", "k", "stays", "outs_recorded", "hbp", "sb",
                "cs", "fo", "multi_hit_abs", "stay_rbi", "stay_hits",
                "roe", "po", "e")
    _PIT_NUM = ("batters_faced", "outs_recorded", "hits_allowed",
                "runs_allowed", "er", "bb", "k", "hr_allowed", "pitches",
                "hbp_allowed", "unearned_runs", "sb_allowed", "cs_caught",
                "fo_induced",
                "er_arc1", "er_arc2", "er_arc3",
                "k_arc1",  "k_arc2",  "k_arc3",
                "fo_arc1", "fo_arc2", "fo_arc3",
                "bf_arc1", "bf_arc2", "bf_arc3", "is_starter")

    def _consolidate(rows, num_fields):
        merged: dict[int, dict] = {}
        order: list[int] = []
        for r in rows:
            pid = r["player_id"]
            if pid not in merged:
                merged[pid] = dict(r)
                order.append(pid)
            else:
                acc = merged[pid]
                for f in num_fields:
                    acc[f] = (acc.get(f) or 0) + (r[f] or 0)
        return [merged[k] for k in order]

    away_b_c = _consolidate(away_b, _BAT_NUM)
    home_b_c = _consolidate(home_b, _BAT_NUM)
    away_p_c = _consolidate(away_p, _PIT_NUM)
    home_p_c = _consolidate(home_p, _PIT_NUM)

    # Decorate the pitcher rows so each carries gsc_avg.
    baselines = _league_baselines()
    for rows in (away_p_c, home_p_c):
        for r in rows:
            r["bf"]   = r.get("batters_faced", 0)
            r["outs"] = r.get("outs_recorded", 0)
            r["h"]    = r.get("hits_allowed", 0)
            r["r"]    = r.get("runs_allowed", 0)
            r["g"]    = 1
        _aggregate_pitcher_rows(rows, baselines=baselines)

    # Line score per phase.
    phases_set = {0}
    for rs in (away_b, home_b, away_p, home_p):
        for r in rs:
            phases_set.add(r["phase"] or 0)
    phases = sorted(phases_set)

    def _line(b_rows):
        runs = {p: 0 for p in phases}
        hits = {p: 0 for p in phases}
        errs = {p: 0 for p in phases}
        for r in b_rows:
            ph = r["phase"] or 0
            runs[ph] = runs.get(ph, 0) + (r["runs"] or 0)
            hits[ph] = hits.get(ph, 0) + (r["hits"] or 0)
            errs[ph] = errs.get(ph, 0) + (r["e"] or 0)
        return {
            "runs":   runs, "hits": hits, "errors": errs,
            "total_r": sum(runs.values()),
            "total_h": sum(hits.values()),
            "total_e": sum(errs.values()),
        }

    return _md_response(text_export.export_box_score(
        dict(game),
        away_p_c, home_p_c,
        away_b_c, home_b_c,
        _line(away_b), _line(home_b),
        phases,
    ))


@app.route("/player/<int:player_id>/export.md")
def player_detail_export(player_id: int):
    """Markdown player season card."""
    player = db.fetchone(
        """SELECT p.*, t.abbrev as team_abbrev, t.name as team_name, t.id as team_id
           FROM players p JOIN teams t ON p.team_id = t.id WHERE p.id = ?""",
        (player_id,))
    if not player:
        abort(404)

    bt = db.fetchone(
        """SELECT COUNT(*) as g, SUM(pa) as pa, SUM(ab) as ab, SUM(hits) as h,
                  SUM(doubles) as d2, SUM(triples) as d3, SUM(hr) as hr,
                  SUM(runs) as r, SUM(rbi) as rbi, SUM(bb) as bb, SUM(k) as k,
                  SUM(stays) as stays,
                  COALESCE(SUM(hbp),0) as hbp,
                  COALESCE(SUM(sb),0)  as sb,
                  COALESCE(SUM(cs),0)  as cs,
                  COALESCE(SUM(fo),0)  as fo,
                  COALESCE(SUM(multi_hit_abs),0) as mhab,
                  COALESCE(SUM(stay_rbi),0)     as stay_rbi,
                  COALESCE(SUM(stay_hits),0)    as stay_hits
           FROM game_batter_stats WHERE player_id = ?""", (player_id,))
    fld = db.fetchone(
        """SELECT COALESCE(SUM(po),0) AS po, COALESCE(SUM(a),0) AS a, COALESCE(SUM(e),0) AS e
           FROM game_batter_stats WHERE player_id = ?""", (player_id,))
    pt = db.fetchone(
        f"""SELECT COUNT(*) as g, SUM(batters_faced) as bf, SUM(outs_recorded) as outs,
                   SUM(hits_allowed) as h, SUM(runs_allowed) as r, SUM(er) as er,
                   SUM(bb) as bb, SUM(k) as k, SUM(hr_allowed) as hr_allowed,
                   COALESCE(SUM(hbp_allowed),0) as hbp_allowed,
                   COALESCE(SUM(unearned_runs),0) as unearned_runs,
                   COALESCE(SUM(unearned_runs),0) as uer,
                   COALESCE(SUM(sb_allowed),0) as sb_allowed,
                   COALESCE(SUM(cs_caught),0) as cs_caught,
                   COALESCE(SUM(fo_induced),0) as fo_induced,
                   COALESCE(SUM(pitches),0) as pitches,
                   COALESCE(SUM(er_arc1),0) as er_arc1, COALESCE(SUM(er_arc2),0) as er_arc2, COALESCE(SUM(er_arc3),0) as er_arc3,
                   COALESCE(SUM(k_arc1),0) as k_arc1, COALESCE(SUM(k_arc2),0) as k_arc2, COALESCE(SUM(k_arc3),0) as k_arc3,
                   COALESCE(SUM(fo_arc1),0) as fo_arc1, COALESCE(SUM(fo_arc2),0) as fo_arc2, COALESCE(SUM(fo_arc3),0) as fo_arc3,
                   COALESCE(SUM(bf_arc1),0) as bf_arc1, COALESCE(SUM(bf_arc2),0) as bf_arc2, COALESCE(SUM(bf_arc3),0) as bf_arc3,
                   COALESCE(SUM(is_starter),0) as gs,
                   COALESCE(SUM(singles_allowed),0) as singles_allowed,
                   COALESCE(SUM(doubles_allowed),0) as doubles_allowed,
                   COALESCE(SUM(triples_allowed),0) as triples_allowed,
                   COALESCE(AVG(NULLIF(fastball_pct,0)),0) as fastball_pct,
                   COALESCE(AVG(NULLIF(breaking_pct,0)),0) as breaking_pct,
                   COALESCE(AVG(NULLIF(offspeed_pct,0)),0) as offspeed_pct
            FROM {_PSTATS_DEDUP_SQL} ps WHERE ps.player_id = ?""", (player_id,))

    baselines = _league_baselines()
    wl = _pitcher_wl_map()

    bt_totals = None
    if bt and bt["pa"]:
        bt_totals = dict(bt)
        bt_totals["position"]         = player.get("position")
        bt_totals["defense"]          = player.get("defense")
        bt_totals["defense_infield"]  = player.get("defense_infield")
        bt_totals["defense_outfield"] = player.get("defense_outfield")
        bt_totals["defense_catcher"]  = player.get("defense_catcher")
        _aggregate_batter_rows([bt_totals], baselines=baselines)

    pt_totals = None
    if pt and pt["outs"]:
        pt_totals = dict(pt)
        pt_totals["player_id"] = player_id
        _aggregate_pitcher_rows([pt_totals], wl=wl, baselines=baselines)

    po_v = (fld["po"] if fld else 0) or 0
    a_v  = (fld["a"]  if fld and "a" in fld.keys() else 0) or 0
    e_v  = (fld["e"]  if fld else 0) or 0
    fld_totals = {
        "po": po_v,
        "a":  a_v,
        "e":  e_v,
        "chances": po_v + a_v + e_v,
        "fld_pct": ((po_v + a_v) / (po_v + a_v + e_v)) if (po_v + a_v + e_v) > 0 else None,
    }

    batting_log = db.fetchall(
        """SELECT bs.*, g.game_date, g.id as game_id, g.home_team_id, g.away_team_id,
                  ht.abbrev as home_abbrev, at.abbrev as away_abbrev
           FROM game_batter_stats bs
           JOIN games g ON bs.game_id = g.id
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE bs.player_id = ?
           ORDER BY g.game_date DESC, g.id DESC LIMIT 10""", (player_id,))
    pitching_log = db.fetchall(
        f"""SELECT ps.*, g.game_date, g.id as game_id, g.home_team_id, g.away_team_id,
                  ht.abbrev as home_abbrev, at.abbrev as away_abbrev
           FROM {_PSTATS_DEDUP_SQL} ps
           JOIN games g ON ps.game_id = g.id
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE ps.player_id = ?
           ORDER BY g.game_date DESC, g.id DESC LIMIT 10""", (player_id,))

    return _md_response(text_export.export_player_card(
        dict(player), bt_totals, pt_totals, fld_totals,
        batting_log=[dict(r) for r in batting_log],
        pitching_log=[dict(r) for r in pitching_log],
    ))


@app.route("/standings/export.md")
def standings_export():
    return _md_response(text_export.export_standings(
        _leagues_with_divisions(), _win_pct, _gb,
    ))


@app.route("/leaders/export.md")
def leaders_export():
    """Reuse the leaders() route's data prep."""
    games_played = db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")["n"]
    if games_played == 0:
        return _md_response("# Leaders\n\n_No games played yet._\n")
    num_teams = db.fetchone("SELECT COUNT(*) as n FROM teams")["n"] or 2
    games_per_team = max(1, (games_played * 2) // num_teams)
    min_pa   = max(3, games_per_team)
    min_outs = max(3, games_per_team)
    baselines = _league_baselines()

    batting = db.fetchall(
        """SELECT p.id as player_id, p.name as player_name, p.position,
                  p.defense as defense, p.arm as arm,
                  p.defense_infield as defense_infield,
                  p.defense_outfield as defense_outfield,
                  p.defense_catcher as defense_catcher,
                  t.id as team_id, t.abbrev as team_abbrev,
                  SUM(bs.pa) as pa, SUM(bs.ab) as ab, SUM(bs.hits) as h,
                  SUM(bs.doubles) as d2, SUM(bs.triples) as d3, SUM(bs.hr) as hr,
                  SUM(bs.runs) as r, SUM(bs.rbi) as rbi,
                  SUM(bs.bb) as bb, SUM(bs.k) as k,
                  COALESCE(SUM(bs.hbp),0) as hbp,
                  COALESCE(SUM(bs.sb),0)  as sb,
                  COALESCE(SUM(bs.cs),0)  as cs,
                  COALESCE(SUM(bs.fo),0)  as fo,
                  COALESCE(SUM(bs.stays),0) as stays,
                  COALESCE(SUM(bs.multi_hit_abs),0) as mhab,
                  COALESCE(SUM(bs.stay_rbi),0)     as stay_rbi,
                  COALESCE(SUM(bs.stay_hits),0)    as stay_hits,
                  COALESCE(SUM(bs.roe),0) as roe,
                  COALESCE(SUM(bs.po),0)  as po,
                  COALESCE(SUM(bs.e),0)   as e
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           JOIN teams   t ON bs.team_id = t.id
           GROUP BY p.id
           HAVING SUM(bs.pa) >= ?""", (min_pa,))
    _aggregate_batter_rows(batting, baselines=baselines)

    pitching = db.fetchall(
        f"""SELECT p.id as player_id, p.name as player_name,
                  p.pitcher_skill as r_stuff, p.command as r_command,
                  p.movement as r_movement, p.stamina as r_stamina,
                  t.abbrev as team_abbrev, t.id as team_id,
                  COUNT(ps.game_id) as g,
                  SUM(ps.batters_faced)  as bf,
                  SUM(ps.outs_recorded)  as outs,
                  SUM(ps.hits_allowed)   as h,
                  SUM(ps.runs_allowed)   as r,
                  SUM(ps.er)             as er,
                  SUM(ps.bb)             as bb,
                  SUM(ps.k)              as k,
                  SUM(ps.hr_allowed)     as hr_allowed,
                  COALESCE(SUM(ps.hbp_allowed),0) as hbp_allowed,
                  COALESCE(SUM(ps.unearned_runs),0) as unearned_runs,
                  COALESCE(SUM(ps.fo_induced),0) as fo_induced,
                  COALESCE(SUM(ps.pitches),0) as pitches,
                  COALESCE(SUM(ps.er_arc1),0) as er_arc1, COALESCE(SUM(ps.er_arc2),0) as er_arc2, COALESCE(SUM(ps.er_arc3),0) as er_arc3,
                  COALESCE(SUM(ps.k_arc1),0) as k_arc1, COALESCE(SUM(ps.k_arc2),0) as k_arc2, COALESCE(SUM(ps.k_arc3),0) as k_arc3,
                  COALESCE(SUM(ps.fo_arc1),0) as fo_arc1, COALESCE(SUM(ps.fo_arc2),0) as fo_arc2, COALESCE(SUM(ps.fo_arc3),0) as fo_arc3,
                  COALESCE(SUM(ps.bf_arc1),0) as bf_arc1, COALESCE(SUM(ps.bf_arc2),0) as bf_arc2, COALESCE(SUM(ps.bf_arc3),0) as bf_arc3,
                  COALESCE(SUM(ps.is_starter),0) as gs
           FROM {_PSTATS_DEDUP_SQL} ps
           JOIN players p ON ps.player_id = p.id
           JOIN teams   t ON ps.team_id = t.id
           GROUP BY p.id
           HAVING SUM(ps.outs_recorded) >= ?""", (min_outs,))
    _aggregate_pitcher_rows(pitching, _pitcher_wl_map(), baselines=baselines)

    return _md_response(text_export.export_leaders(
        [dict(r) for r in batting],
        [dict(r) for r in pitching],
    ))


@app.route("/team/<int:team_id>/export.md")
def team_detail_export(team_id: int):
    """Markdown team summary."""
    team = db.fetchone("SELECT * FROM teams WHERE id = ?", (team_id,))
    if not team:
        abort(404)
    roster = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? ORDER BY is_pitcher, position, id",
        (team_id,))
    ids = [p["id"] for p in roster]
    if not ids:
        return _md_response(text_export.export_team(dict(team), [], [], 0, 0))

    ph = ",".join("?" * len(ids))
    bstats = {
        r["player_id"]: r for r in db.fetchall(
            f"""SELECT bs.player_id, COUNT(bs.game_id) as gp,
                       SUM(bs.pa) as pa, SUM(bs.ab) as ab, SUM(bs.hits) as h,
                       SUM(bs.doubles) as d2, SUM(bs.triples) as d3, SUM(bs.hr) as hr,
                       SUM(bs.runs) as r, SUM(bs.rbi) as rbi,
                       SUM(bs.bb) as bb, SUM(bs.k) as k,
                       COALESCE(SUM(bs.hbp),0) as hbp,
                       COALESCE(SUM(bs.sb),0) as sb, COALESCE(SUM(bs.cs),0) as cs,
                       COALESCE(SUM(bs.fo),0) as fo,
                       COALESCE(SUM(bs.stays),0) as stays
               FROM game_batter_stats bs
               WHERE bs.player_id IN ({ph}) GROUP BY bs.player_id""",
            tuple(ids))
    }
    pstats = {
        r["player_id"]: r for r in db.fetchall(
            f"""SELECT ps.player_id, COUNT(ps.game_id) as gp, COUNT(ps.game_id) as g,
                       SUM(ps.batters_faced) AS bf, SUM(ps.outs_recorded) AS outs,
                       SUM(ps.hits_allowed) AS h, SUM(ps.runs_allowed) AS r,
                       SUM(ps.er) AS er, SUM(ps.bb) AS bb, SUM(ps.k) AS k,
                       SUM(ps.hr_allowed) AS hr_allowed,
                       SUM(ps.pitches) AS pitches,
                       COALESCE(SUM(ps.hbp_allowed),0) AS hbp_allowed,
                       COALESCE(SUM(ps.unearned_runs),0) AS unearned_runs,
                       COALESCE(SUM(ps.fo_induced),0) AS fo_induced,
                       COALESCE(SUM(ps.er_arc1),0) AS er_arc1, COALESCE(SUM(ps.er_arc2),0) AS er_arc2, COALESCE(SUM(ps.er_arc3),0) AS er_arc3,
                       COALESCE(SUM(ps.k_arc1),0) AS k_arc1, COALESCE(SUM(ps.k_arc2),0) AS k_arc2, COALESCE(SUM(ps.k_arc3),0) AS k_arc3,
                       COALESCE(SUM(ps.fo_arc1),0) AS fo_arc1, COALESCE(SUM(ps.fo_arc2),0) AS fo_arc2, COALESCE(SUM(ps.fo_arc3),0) AS fo_arc3,
                       COALESCE(SUM(ps.bf_arc1),0) AS bf_arc1, COALESCE(SUM(ps.bf_arc2),0) AS bf_arc2, COALESCE(SUM(ps.bf_arc3),0) AS bf_arc3,
                       COALESCE(SUM(ps.is_starter),0) AS gs
               FROM {_PSTATS_DEDUP_SQL} ps
               WHERE ps.player_id IN ({ph}) GROUP BY ps.player_id""",
            tuple(ids))
    }

    wl = _pitcher_wl_map()
    baselines = _league_baselines()
    batters: list[dict] = []
    pitchers: list[dict] = []
    for p in roster:
        if p["is_pitcher"]:
            row = dict(p)
            row.update(pstats.get(p["id"], {}))
            _aggregate_pitcher_rows([row], wl, baselines=baselines)
            pitchers.append(row)
        else:
            row = dict(p)
            row.update(bstats.get(p["id"], {}))
            _aggregate_batter_rows([row], baselines=baselines)
            batters.append(row)

    # Wins / losses for the team — sum of game-level results.
    record = db.fetchone(
        """SELECT
             SUM(CASE WHEN winner_id = ? THEN 1 ELSE 0 END) as w,
             SUM(CASE WHEN played=1 AND winner_id IS NOT NULL AND winner_id != ?
                       AND (home_team_id = ? OR away_team_id = ?) THEN 1 ELSE 0 END) as l
           FROM games WHERE played = 1
             AND (home_team_id = ? OR away_team_id = ?)""",
        (team_id, team_id, team_id, team_id, team_id, team_id))
    wins = (record or {}).get("w") or 0
    losses = (record or {}).get("l") or 0

    return _md_response(text_export.export_team(
        dict(team), batters, pitchers, wins, losses,
    ))


# ---------------------------------------------------------------------------
# Players index (NEW) + leaders (renamed from /stats)
# ---------------------------------------------------------------------------

@app.route("/players")
def players():
    kind = request.args.get("kind", "batters")
    if kind not in ("batters", "pitchers", "both", "ratings"):
        kind = "batters"
    selected_team_id = request.args.get("team", type=int)
    selected_pos = request.args.get("pos", "") or ""
    q = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 50

    # Rating-filter URL params. Each is a min threshold on the named
    # column on `players`. Surfaces the scout-grade attributes that
    # otherwise weren't filterable anywhere in the app.
    _RATING_FILTERS = (
        # column,            label,              applies-to (None = anyone)
        ("skill",            "Min Skill",        "hitter"),
        ("power",            "Min Power",        "hitter"),
        ("contact",          "Min Contact",      "hitter"),
        ("eye",              "Min Eye",          "hitter"),
        ("speed",            "Min Speed",        None),
        ("pitcher_skill",    "Min Stuff",        "pitcher"),
        ("command",          "Min Command",      "pitcher"),
        ("movement",         "Min Movement",     "pitcher"),
        ("stamina",          "Min Stamina",      "pitcher"),
        ("defense",          "Min Defense",      None),
        ("arm",              "Min Arm",          None),
        ("defense_infield",  "Min Def-Infield",  None),
        ("defense_outfield", "Min Def-Outfield", None),
        ("defense_catcher",  "Min Def-Catcher",  None),
    )
    rating_filters: dict[str, int] = {}
    for col, _label, _scope in _RATING_FILTERS:
        v = request.args.get(f"min_{col}")
        if v:
            try:
                rating_filters[col] = int(v)
            except ValueError:
                pass

    where = []
    params: list = []
    if selected_team_id:
        where.append("p.team_id = ?")
        params.append(selected_team_id)
    if selected_pos:
        where.append("p.position = ?")
        params.append(selected_pos)
    if q:
        where.append("LOWER(p.name) LIKE ?")
        params.append(f"%{q.lower()}%")
    if kind == "batters":
        where.append("p.is_pitcher = 0")
    elif kind == "pitchers":
        where.append("p.is_pitcher = 1")
    # Ratings view doesn't pre-filter by is_pitcher — show the whole league.
    for col, threshold in rating_filters.items():
        where.append(f"p.{col} >= ?")
        params.append(threshold)

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    # Hoisted up so the batter loop below sees baselines too — without
    # this, _aggregate_batter_rows fell through to runs_per_win=10.0
    # and inflated WAR by ~75% in O27's 24-R/G environment.
    baselines = _league_baselines()

    total_row = db.fetchone(f"SELECT COUNT(*) AS n FROM players p{where_sql}", tuple(params))
    total = total_row["n"] if total_row else 0
    pages = max(1, math.ceil(total / per_page))
    if page > pages:
        page = pages
    offset = (page - 1) * per_page

    base = db.fetchall(
        f"""SELECT p.id, p.name, p.country, p.team_id, p.position, p.age, p.is_pitcher, p.is_joker, p.pitcher_role,
                   p.skill, p.power, p.contact, p.eye, p.speed,
                   p.pitcher_skill, p.command, p.movement, p.stamina,
                   p.defense, p.arm, p.defense_infield, p.defense_outfield, p.defense_catcher,
                   p.archetype,
                   t.abbrev AS team_abbrev
            FROM players p JOIN teams t ON p.team_id = t.id
            {where_sql}
            ORDER BY p.name
            LIMIT ? OFFSET ?""",
        tuple(params) + (per_page, offset),
    )
    page_ids = [p["id"] for p in base]

    # If this is the ratings view, no further per-game-stat aggregation
    # is needed — just hand the base rows to the template.
    if kind == "ratings":
        return _serve(
            "players.html",
            kind=kind,
            ratings_rows=base,
            rating_filters=rating_filters,
            rating_filter_specs=_RATING_FILTERS,
            batters=[], pitchers=[],
            all_teams=db.fetchall("SELECT id, name, abbrev FROM teams ORDER BY name"),
            all_positions=[r["position"] for r in db.fetchall("SELECT DISTINCT position FROM players ORDER BY position")],
            selected_team_id=selected_team_id, selected_pos=selected_pos, q=q,
            total=total, page=page, pages=pages,
        )

    if not page_ids:
        return _serve(
            "players.html",
            kind=kind, batters=[], pitchers=[],
            rating_filters=rating_filters,
            rating_filter_specs=_RATING_FILTERS,
            all_teams=db.fetchall("SELECT id, name, abbrev FROM teams ORDER BY name"),
            all_positions=[r["position"] for r in db.fetchall("SELECT DISTINCT position FROM players ORDER BY position")],
            selected_team_id=selected_team_id, selected_pos=selected_pos, q=q,
            total=total, page=page, pages=pages,
        )

    ph = ",".join("?" * len(page_ids))

    batter_rows = []
    pitcher_rows = []

    if kind in ("batters", "both"):
        bstats = {
            r["player_id"]: r for r in db.fetchall(
                f"""SELECT bs.player_id,
                           COUNT(bs.game_id) AS gp,
                           SUM(bs.pa) AS pa, SUM(bs.ab) AS ab, SUM(bs.hits) AS h,
                           SUM(bs.doubles) AS d2, SUM(bs.triples) AS d3, SUM(bs.hr) AS hr,
                           SUM(bs.runs) AS r, SUM(bs.rbi) AS rbi,
                           SUM(bs.bb) AS bb, SUM(bs.k) AS k
                    FROM game_batter_stats bs
                    WHERE bs.player_id IN ({ph})
                    GROUP BY bs.player_id""",
                tuple(page_ids),
            )
        }
        for p in base:
            if p["is_pitcher"] and kind == "both":
                continue
            row = dict(p)
            s = bstats.get(p["id"], {})
            row.update(s)
            _aggregate_batter_rows([row], baselines=baselines)
            batter_rows.append(row)

    if kind in ("pitchers", "both"):
        pstats = {
            r["player_id"]: r for r in db.fetchall(
                f"""SELECT ps.player_id,
                           COUNT(ps.game_id) AS gp,
                           COUNT(ps.game_id) AS g,
                           SUM(ps.batters_faced) AS bf,
                           SUM(ps.outs_recorded) AS outs,
                           SUM(ps.hits_allowed) AS h, SUM(ps.runs_allowed) AS r,
                           SUM(ps.er) AS er,
                           SUM(ps.bb) AS bb, SUM(ps.k) AS k,
                           SUM(ps.hr_allowed) AS hr_allowed,
                           SUM(ps.pitches) AS pitches,
                           SUM(ps.hbp_allowed) AS hbp_allowed,
                           SUM(ps.unearned_runs) AS unearned_runs,
                           SUM(ps.fo_induced) AS fo_induced,
                           SUM(ps.er_arc1) AS er_arc1, SUM(ps.er_arc2) AS er_arc2, SUM(ps.er_arc3) AS er_arc3,
                           SUM(ps.k_arc1)  AS k_arc1,  SUM(ps.k_arc2)  AS k_arc2,  SUM(ps.k_arc3)  AS k_arc3,
                           SUM(ps.fo_arc1) AS fo_arc1, SUM(ps.fo_arc2) AS fo_arc2, SUM(ps.fo_arc3) AS fo_arc3,
                           SUM(ps.bf_arc1) AS bf_arc1, SUM(ps.bf_arc2) AS bf_arc2, SUM(ps.bf_arc3) AS bf_arc3,
                           SUM(ps.is_starter) AS gs
                    FROM {_PSTATS_DEDUP_SQL} ps
                    WHERE ps.player_id IN ({ph})
                    GROUP BY ps.player_id""",
                tuple(page_ids),
            )
        }
        wl = _pitcher_wl_map()
        # baselines hoisted earlier in the route — reuse.
        for p in base:
            if not p["is_pitcher"] and kind == "both":
                continue
            row = dict(p)
            s = pstats.get(p["id"], {})
            row.update(s)
            _aggregate_pitcher_rows([row], wl, baselines=baselines)
            pitcher_rows.append(row)

    return _serve(
        "players.html",
        kind=kind,
        batters=batter_rows,
        pitchers=pitcher_rows,
        rating_filters=rating_filters,
        rating_filter_specs=_RATING_FILTERS,
        all_teams=db.fetchall("SELECT id, name, abbrev FROM teams ORDER BY name"),
        all_positions=[r["position"] for r in db.fetchall("SELECT DISTINCT position FROM players ORDER BY position")],
        selected_team_id=selected_team_id,
        selected_pos=selected_pos,
        q=q,
        total=total, page=page, pages=pages,
    )


@app.route("/stats")
def stats_browse():
    """Full sortable, filterable batting + pitching tables.

    Query params:
      side=bat|pit             — which table to show (default: bat)
      view=standard|advanced|all — column verbosity (default: standard)
      team=<id|all>            — restrict to one team
      pos=<all|hitter|pitcher|C|1B|2B|3B|SS|LF|CF|RF|DH|UT|P>
                                — restrict by position class or specific slot
      min_pa=<int>             — minimum PA gate for batting
      min_outs=<int>           — minimum outs gate for pitching
      qualified=1|0            — convenience: ~3.1 PA per team-game / 1 out per team-game
    """
    side       = (request.args.get("side") or "bat").lower()
    view       = (request.args.get("view") or "standard").lower()
    if view not in ("standard", "advanced", "all"):
        view = "standard"
    team_arg   = request.args.get("team")  or "all"
    pos_arg    = request.args.get("pos") or "all"
    qualified  = request.args.get("qualified") == "1"
    name_query = (request.args.get("q") or "").strip()

    # All distinct on-field positions, surfaced as filter options.
    _SPECIFIC_POSITIONS = ("P", "C", "1B", "2B", "3B", "SS",
                           "LF", "CF", "RF", "DH", "UT")

    games_played = db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")["n"] or 0
    teams_total  = db.fetchone("SELECT COUNT(*) as n FROM teams")["n"] or 30
    games_per_team = (games_played * 2) // max(1, teams_total)   # both teams play in each game

    # Qualified-only thresholds, MLB-equivalent scaled to O27.
    # Batting: ~3.1 PA per team-game (MLB uses 3.1 PA/G).
    # Pitching: ~1 out per team-game (rough O27 analog of 1 IP/G).
    qual_pa   = max(1, int(round(games_per_team * 3.1))) if games_per_team else 1
    qual_outs = max(1, games_per_team) if games_per_team else 1

    try:
        min_pa = int(request.args.get("min_pa", "0") or 0)
    except ValueError:
        min_pa = 0
    try:
        min_outs = int(request.args.get("min_outs", "0") or 0)
    except ValueError:
        min_outs = 0
    if qualified:
        min_pa   = max(min_pa, qual_pa)
        min_outs = max(min_outs, qual_outs)

    # Team filter param resolution.
    team_filter_id = None
    if team_arg.isdigit():
        team_filter_id = int(team_arg)

    teams_list = db.fetchall(
        "SELECT id, abbrev, name, league, division FROM teams ORDER BY abbrev"
    )
    baselines = _league_baselines()

    # ----- Batting table -----
    batters: list[dict] = []
    pitchers: list[dict] = []

    if side == "bat":
        where_clauses = ["bs.pa > 0"]
        params: list = []
        if team_filter_id is not None:
            where_clauses.append("bs.team_id = ?")
            params.append(team_filter_id)
        if pos_arg.lower() in ("hitter", "non_pitcher"):
            where_clauses.append("p.is_pitcher = 0")
        elif pos_arg.lower() in ("pitcher",):
            where_clauses.append("p.is_pitcher = 1")
        elif pos_arg in _SPECIFIC_POSITIONS:
            where_clauses.append("p.position = ?")
            params.append(pos_arg)
        if name_query:
            where_clauses.append("p.name LIKE ?")
            params.append(f"%{name_query}%")
        where_sql = " AND ".join(where_clauses)
        params.append(min_pa)

        batters = db.fetchall(
            f"""SELECT p.id as player_id, p.name as player_name,
                       p.position as position, t.abbrev as team_abbrev, t.id as team_id,
                       p.is_pitcher as is_pitcher,
                       p.power as r_power, p.contact as r_contact, p.eye as r_eye,
                       p.defense as defense, p.arm as arm,
                       p.defense_infield as defense_infield,
                       p.defense_outfield as defense_outfield,
                       p.defense_catcher as defense_catcher,
                       COUNT(bs.game_id) as g,
                       SUM(bs.pa) as pa, SUM(bs.ab) as ab, SUM(bs.hits) as h,
                       SUM(bs.doubles) as d2, SUM(bs.triples) as d3, SUM(bs.hr) as hr,
                       SUM(bs.runs) as r, SUM(bs.rbi) as rbi,
                       SUM(bs.bb) as bb, SUM(bs.k) as k, SUM(bs.stays) as stays,
                       COALESCE(SUM(bs.hbp),0) as hbp,
                       COALESCE(SUM(bs.sb),0)  as sb,
                       COALESCE(SUM(bs.cs),0)  as cs,
                       COALESCE(SUM(bs.fo),0)  as fo,
                       COALESCE(SUM(bs.multi_hit_abs),0) as mhab,
                       COALESCE(SUM(bs.stay_rbi),0)     as stay_rbi,
                  COALESCE(SUM(bs.stay_hits),0)    as stay_hits,
                       COALESCE(SUM(bs.roe),0)          as roe
                FROM game_batter_stats bs
                JOIN players p ON bs.player_id = p.id
                JOIN teams   t ON bs.team_id = t.id
                WHERE {where_sql}
                GROUP BY p.id
                HAVING SUM(bs.pa) >= ?
                ORDER BY SUM(bs.pa) DESC""",
            tuple(params),
        )
        _aggregate_batter_rows(batters, baselines=baselines)

    elif side == "pit":
        where_clauses = ["ps.outs_recorded > 0"]
        params = []
        if team_filter_id is not None:
            where_clauses.append("ps.team_id = ?")
            params.append(team_filter_id)
        # Pitching always implies pitchers.
        where_clauses.append("p.is_pitcher = 1")
        if name_query:
            where_clauses.append("p.name LIKE ?")
            params.append(f"%{name_query}%")
        where_sql = " AND ".join(where_clauses)
        params.append(min_outs)

        pitchers = db.fetchall(
            f"""SELECT p.id as player_id, p.name as player_name,
                       p.position as position, t.abbrev as team_abbrev, t.id as team_id,
                       p.pitcher_skill as r_stuff, p.command as r_command,
                       p.movement as r_movement, p.stamina as r_stamina,
                       COUNT(ps.game_id) as g,
                       SUM(ps.batters_faced)  as bf,
                       SUM(ps.outs_recorded)  as outs,
                       SUM(ps.hits_allowed)   as h,
                       SUM(ps.runs_allowed)   as r,
                       SUM(ps.er)             as er,
                       SUM(ps.bb)             as bb,
                       SUM(ps.k)              as k,
                       SUM(ps.hr_allowed)     as hr_allowed,
                       COALESCE(SUM(ps.hbp_allowed),0)   as hbp_allowed,
                       COALESCE(SUM(ps.unearned_runs),0) as unearned_runs,
                       COALESCE(SUM(ps.unearned_runs),0) as uer,
                       COALESCE(SUM(ps.sb_allowed),0)    as sb_allowed,
                       COALESCE(SUM(ps.cs_caught),0)     as cs_caught,
                       COALESCE(SUM(ps.fo_induced),0)    as fo_induced,
                       COALESCE(SUM(ps.pitches),0)       as pitches,
                       COALESCE(SUM(ps.er_arc1),0) as er_arc1, COALESCE(SUM(ps.er_arc2),0) as er_arc2, COALESCE(SUM(ps.er_arc3),0) as er_arc3,
                       COALESCE(SUM(ps.k_arc1),0)  as k_arc1,  COALESCE(SUM(ps.k_arc2),0)  as k_arc2,  COALESCE(SUM(ps.k_arc3),0)  as k_arc3,
                       COALESCE(SUM(ps.fo_arc1),0) as fo_arc1, COALESCE(SUM(ps.fo_arc2),0) as fo_arc2, COALESCE(SUM(ps.fo_arc3),0) as fo_arc3,
                       COALESCE(SUM(ps.bf_arc1),0) as bf_arc1, COALESCE(SUM(ps.bf_arc2),0) as bf_arc2, COALESCE(SUM(ps.bf_arc3),0) as bf_arc3,
                       COALESCE(SUM(ps.is_starter),0) as gs
                FROM {_PSTATS_DEDUP_SQL} ps
                JOIN players p ON ps.player_id = p.id
                JOIN teams   t ON ps.team_id = t.id
                WHERE {where_sql}
                GROUP BY p.id
                HAVING SUM(ps.outs_recorded) >= ?
                ORDER BY SUM(ps.outs_recorded) DESC""",
            tuple(params),
        )
        wl = _pitcher_wl_map()
        _aggregate_pitcher_rows(pitchers, wl, baselines=baselines)
        for p in pitchers:
            outs = p["outs"] or 0
            p["os_pct"] = (outs / (27.0 * p["g"])) if p["g"] else 0.0

    return _serve(
        "stats_browse.html",
        side=side,
        view=view,
        team_arg=team_arg,
        pos_arg=pos_arg,
        min_pa=min_pa,
        min_outs=min_outs,
        qualified=qualified,
        qual_pa=qual_pa,
        qual_outs=qual_outs,
        name_query=name_query,
        teams_list=teams_list,
        batters=batters,
        pitchers=pitchers,
        games_played=games_played,
    )


@app.route("/leaders")
def leaders():
    games_played = db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")["n"]
    if games_played == 0:
        # Scouting tables don't depend on games — surface them so a fresh
        # league still gets the talent census while waiting for sim data.
        talent_hitters = db.fetchall(
            """SELECT p.id as player_id, p.name as player_name, p.position,
                      p.power as r_power, p.contact as r_contact,
                      p.eye as r_eye, p.speed as r_speed,
                      t.abbrev as team_abbrev, t.id as team_id
               FROM players p JOIN teams t ON p.team_id = t.id
               WHERE p.is_pitcher = 0""",
        )
        talent_pitchers = db.fetchall(
            """SELECT p.id as player_id, p.name as player_name,
                      p.pitcher_skill as r_stuff, p.command as r_command,
                      p.movement as r_movement, p.stamina as r_stamina,
                      t.abbrev as team_abbrev, t.id as team_id
               FROM players p JOIN teams t ON p.team_id = t.id
               WHERE p.is_pitcher = 1""",
        )
        return _serve("leaders.html",
                               games_played=0, batting=[], pitching=[],
                               min_pa=0, min_outs=0,
                               talent_hitters=talent_hitters,
                               talent_pitchers=talent_pitchers)

    # Scale qualifying minimums by games-per-team, not by total league games.
    # MLB rule of thumb: 3.1 PA/team-game for batting, 1 IP/team-game for
    # pitching. Same threshold as the dashboard widget so the two views
    # don't disagree on who qualifies.
    min_pa, min_outs = _qualifying_thresholds(games_played)

    batting = db.fetchall(
        """SELECT p.id as player_id, p.name as player_name, p.position,
                  p.power as r_power, p.contact as r_contact, p.eye as r_eye,
                  p.defense as defense, p.arm as arm,
                  p.defense_infield as defense_infield,
                  p.defense_outfield as defense_outfield,
                  p.defense_catcher as defense_catcher,
                  t.abbrev as team_abbrev, t.id as team_id,
                  COUNT(bs.game_id) as g,
                  SUM(bs.pa) as pa, SUM(bs.ab) as ab, SUM(bs.hits) as h,
                  SUM(bs.doubles) as d2, SUM(bs.triples) as d3, SUM(bs.hr) as hr,
                  SUM(bs.runs) as r, SUM(bs.rbi) as rbi,
                  SUM(bs.bb) as bb, SUM(bs.k) as k, SUM(bs.stays) as stays,
                  COALESCE(SUM(bs.hbp),0) as hbp,
                  COALESCE(SUM(bs.sb),0)  as sb,
                  COALESCE(SUM(bs.cs),0)  as cs,
                  COALESCE(SUM(bs.fo),0)  as fo,
                  COALESCE(SUM(bs.multi_hit_abs),0) as mhab,
                  COALESCE(SUM(bs.stay_rbi),0)     as stay_rbi,
                  COALESCE(SUM(bs.stay_hits),0)    as stay_hits,
                  COALESCE(SUM(bs.roe),0)          as roe,
                  COALESCE(SUM(bs.po),0)           as po,
                  COALESCE(SUM(bs.e),0)            as e
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           JOIN teams   t ON bs.team_id = t.id
           GROUP BY p.id
           HAVING SUM(bs.pa) >= ?""",
        (min_pa,),
    )
    baselines = _league_baselines()
    _aggregate_batter_rows(batting, baselines=baselines)

    pitching = db.fetchall(
        f"""SELECT p.id as player_id, p.name as player_name,
                  p.pitcher_skill as r_stuff, p.command as r_command,
                  p.movement as r_movement, p.stamina as r_stamina,
                  t.abbrev as team_abbrev, t.id as team_id,
                  COUNT(ps.game_id) as g,
                  SUM(ps.batters_faced)  as bf,
                  SUM(ps.outs_recorded)  as outs,
                  SUM(ps.hits_allowed)   as h,
                  SUM(ps.runs_allowed)   as r,
                  SUM(ps.er)             as er,
                  SUM(ps.bb)             as bb,
                  SUM(ps.k)              as k,
                  SUM(ps.hr_allowed)     as hr_allowed,
                  COALESCE(SUM(ps.hbp_allowed),0)   as hbp_allowed,
                  COALESCE(SUM(ps.unearned_runs),0) as unearned_runs,
                  COALESCE(SUM(ps.unearned_runs),0) as uer,
                  COALESCE(SUM(ps.sb_allowed),0)    as sb_allowed,
                  COALESCE(SUM(ps.cs_caught),0)     as cs_caught,
                  COALESCE(SUM(ps.fo_induced),0)    as fo_induced,
                  COALESCE(SUM(ps.pitches),0)       as pitches,
                  COALESCE(SUM(ps.er_arc1),0) as er_arc1, COALESCE(SUM(ps.er_arc2),0) as er_arc2, COALESCE(SUM(ps.er_arc3),0) as er_arc3,
                  COALESCE(SUM(ps.k_arc1),0)  as k_arc1,  COALESCE(SUM(ps.k_arc2),0)  as k_arc2,  COALESCE(SUM(ps.k_arc3),0)  as k_arc3,
                  COALESCE(SUM(ps.fo_arc1),0) as fo_arc1, COALESCE(SUM(ps.fo_arc2),0) as fo_arc2, COALESCE(SUM(ps.fo_arc3),0) as fo_arc3,
                  COALESCE(SUM(ps.bf_arc1),0) as bf_arc1, COALESCE(SUM(ps.bf_arc2),0) as bf_arc2, COALESCE(SUM(ps.bf_arc3),0) as bf_arc3,
                  COALESCE(SUM(ps.is_starter),0) as gs,
                  COALESCE(SUM(ps.singles_allowed),0) as singles_allowed,
                  COALESCE(SUM(ps.doubles_allowed),0) as doubles_allowed,
                  COALESCE(SUM(ps.triples_allowed),0) as triples_allowed,
                  COALESCE(AVG(NULLIF(ps.fastball_pct,0)) * 100,0) as fastball_pct,
                  COALESCE(AVG(NULLIF(ps.breaking_pct,0)) * 100,0) as breaking_pct,
                  COALESCE(AVG(NULLIF(ps.offspeed_pct,0)) * 100,0) as offspeed_pct
           FROM {_PSTATS_DEDUP_SQL} ps
           JOIN players p ON ps.player_id = p.id
           JOIN teams   t ON ps.team_id = t.id
           GROUP BY p.id
           HAVING SUM(ps.outs_recorded) >= ?""",
        (min_outs,),
    )
    # Shared helper now produces wERA / xFIP / Decay / GSc / OS+ / AOR / etc.
    wl = _pitcher_wl_map()
    _aggregate_pitcher_rows(pitching, wl, baselines=baselines)
    for p in pitching:
        outs = p["outs"] or 0
        # OS% = share of a complete game (27 outs) recorded per appearance.
        p["os_pct"] = (outs / (27.0 * p["g"])) if p["g"] else 0.0

    # Fielding leaders are sourced from a dedicated query because PO/E are
    # credited to the player who made the play (potentially a pitcher with
    # zero PA), so the PA-qualified batting set would exclude them. We
    # qualify on total chances (PO + E) instead — a single great or terrible
    # play can't top the board.
    num_teams_row = db.fetchone("SELECT COUNT(*) as n FROM teams")
    num_teams = (num_teams_row["n"] if num_teams_row else 0) or 2
    games_per_team = max(1, (games_played * 2) // num_teams)
    min_chances = max(3, games_per_team)
    fielding = db.fetchall(
        """SELECT p.id as player_id, p.name as player_name, p.position,
                  t.abbrev as team_abbrev, t.id as team_id,
                  COUNT(bs.game_id) as g,
                  COALESCE(SUM(bs.po),0) as po,
                  COALESCE(SUM(bs.a),0)  as a,
                  COALESCE(SUM(bs.e),0)  as e,
                  COALESCE(SUM(bs.outs_recorded),0) as out_share
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           JOIN teams   t ON bs.team_id = t.id
           GROUP BY p.id
           HAVING (COALESCE(SUM(bs.po),0) + COALESCE(SUM(bs.a),0) + COALESCE(SUM(bs.e),0)) > 0""",
    )
    for f in fielding:
        po_v = f.get("po") or 0
        a_v  = f.get("a")  or 0
        e_v  = f.get("e")  or 0
        f["chances"] = po_v + a_v + e_v
        f["fld_pct"] = ((po_v + a_v) / (po_v + a_v + e_v)) if (po_v + a_v + e_v) > 0 else None
        # Range factor — (PO + A) per 27 outs. Uses the player's own
        # outs_recorded as the denominator proxy (their fielding time).
        # Falls back to chances/9 when out_share is zero (legacy rows).
        out_share = f.get("out_share") or 0
        if out_share > 0:
            f["rf"] = (po_v + a_v) * 27.0 / out_share
        else:
            f["rf"] = None
    fielding_qual = [f for f in fielding if f["chances"] >= min_chances]

    salaries = db.fetchall(
        """SELECT p.id as player_id, p.name as player_name, p.position,
                  p.is_pitcher, p.salary,
                  t.abbrev as team_abbrev, t.id as team_id
           FROM players p
           JOIN teams t ON p.team_id = t.id
           WHERE p.salary > 0
           ORDER BY p.salary DESC
           LIMIT 25""",
    )

    # Scouting board — every signed player ranked by raw 20-80 tool grades,
    # independent of playing time. Surfaces hidden depth (reserves with elite
    # Stamina, bench bats with elite Power) that the qualified-leader views
    # above filter out.
    talent_hitters = db.fetchall(
        """SELECT p.id as player_id, p.name as player_name, p.position,
                  p.power as r_power, p.contact as r_contact,
                  p.eye as r_eye, p.speed as r_speed,
                  t.abbrev as team_abbrev, t.id as team_id
           FROM players p
           JOIN teams t ON p.team_id = t.id
           WHERE p.is_pitcher = 0""",
    )
    talent_pitchers = db.fetchall(
        """SELECT p.id as player_id, p.name as player_name,
                  p.pitcher_skill as r_stuff, p.command as r_command,
                  p.movement as r_movement, p.stamina as r_stamina,
                  t.abbrev as team_abbrev, t.id as team_id
           FROM players p
           JOIN teams t ON p.team_id = t.id
           WHERE p.is_pitcher = 1""",
    )

    return _serve(
        "leaders.html",
        games_played=games_played,
        min_pa=min_pa, min_outs=min_outs, min_chances=min_chances,
        batting=batting, pitching=pitching,
        fielding=fielding, fielding_qual=fielding_qual,
        salaries=salaries,
        talent_hitters=talent_hitters,
        talent_pitchers=talent_pitchers,
    )


def _player_handedness_split_batter(player_id: int, throws: str) -> dict | None:
    """Batter contact-event split vs pitchers of the given handedness.

    Aggregates `game_pa_log` rows where the batter is the given player
    and the pitcher's `throws` matches `L` or `R`. K and BB are NOT
    included (pa_log only captures ball-in-play events), so this reads
    as "contact production vs L/R-handed pitchers."
    """
    row = db.fetchone(
        """SELECT COUNT(*) AS bip,
                  SUM(CASE WHEN pa.hit_type IN ('single','infield_single','double','triple','hr','home_run') THEN 1 ELSE 0 END) AS h,
                  SUM(CASE WHEN pa.hit_type = 'double' THEN 1 ELSE 0 END) AS d2,
                  SUM(CASE WHEN pa.hit_type = 'triple' THEN 1 ELSE 0 END) AS d3,
                  SUM(CASE WHEN pa.hit_type IN ('hr','home_run') THEN 1 ELSE 0 END) AS hr,
                  SUM(CASE WHEN pa.hit_type = 'error' THEN 1 ELSE 0 END) AS roe,
                  SUM(COALESCE(pa.runs_scored, 0)) AS rbi
           FROM game_pa_log pa
           JOIN players pi ON pa.pitcher_id = pi.id
           WHERE pa.batter_id = ? AND pi.throws = ?""",
        (player_id, throws),
    )
    if not row or not (row.get("bip") or 0):
        return None
    bip = row["bip"] or 0
    h   = row["h"]  or 0
    d2  = row["d2"] or 0
    d3  = row["d3"] or 0
    hr  = row["hr"] or 0
    singles = max(0, h - d2 - d3 - hr)
    tb = singles + 2 * d2 + 3 * d3 + 4 * hr
    return {
        "bip":  bip,
        "h":    h,
        "d2":   d2,
        "d3":   d3,
        "hr":   hr,
        "rbi":  row["rbi"] or 0,
        "ba":   (h / bip) if bip else 0.0,
        "iso":  ((tb - h) / bip) if bip else 0.0,
        "slg":  (tb / bip) if bip else 0.0,
    }


def _player_handedness_split_pitcher(player_id: int, bats: str) -> dict | None:
    """Pitcher contact-allowed split vs batters of the given handedness.

    `bats='L'` and `bats='R'` are the standard split buckets. Switch
    hitters (bats='S') are excluded here — they show up in neither L
    nor R column. Could be folded in once the engine resolves their
    effective side per AB.
    """
    row = db.fetchone(
        """SELECT COUNT(*) AS bip,
                  SUM(CASE WHEN pa.hit_type IN ('single','infield_single','double','triple','hr','home_run') THEN 1 ELSE 0 END) AS h,
                  SUM(CASE WHEN pa.hit_type = 'double' THEN 1 ELSE 0 END) AS d2,
                  SUM(CASE WHEN pa.hit_type = 'triple' THEN 1 ELSE 0 END) AS d3,
                  SUM(CASE WHEN pa.hit_type IN ('hr','home_run') THEN 1 ELSE 0 END) AS hr,
                  SUM(COALESCE(pa.runs_scored, 0)) AS r
           FROM game_pa_log pa
           JOIN players ba ON pa.batter_id = ba.id
           WHERE pa.pitcher_id = ? AND ba.bats = ?""",
        (player_id, bats),
    )
    if not row or not (row.get("bip") or 0):
        return None
    bip = row["bip"] or 0
    h   = row["h"]  or 0
    d2  = row["d2"] or 0
    d3  = row["d3"] or 0
    hr  = row["hr"] or 0
    singles = max(0, h - d2 - d3 - hr)
    tb = singles + 2 * d2 + 3 * d3 + 4 * hr
    return {
        "bip":  bip,
        "h":    h,
        "d2":   d2,
        "d3":   d3,
        "hr":   hr,
        "r":    row["r"] or 0,
        "ba":   (h / bip) if bip else 0.0,
        "slg":  (tb / bip) if bip else 0.0,
    }


def _player_batting_split(player_id: int, team_id: int,
                          where_extra: str, params_extra: tuple,
                          baselines: dict) -> dict | None:
    """Aggregate one batting split for a player.

    where_extra: SQL fragment appended to WHERE (must start with ' AND ').
    params_extra: tuple of params bound into where_extra.
    Returns a fully-decorated bt_totals dict (post _aggregate_batter_rows),
    or None if the player has no PA in this split.
    """
    bt = db.fetchone(
        f"""SELECT COUNT(*) as g, SUM(bs.pa) as pa, SUM(bs.ab) as ab,
                   SUM(bs.hits) as h,
                   SUM(bs.doubles) as d2, SUM(bs.triples) as d3,
                   SUM(bs.hr) as hr, SUM(bs.runs) as r, SUM(bs.rbi) as rbi,
                   SUM(bs.bb) as bb, SUM(bs.k) as k, SUM(bs.stays) as stays,
                   COALESCE(SUM(bs.hbp),0) as hbp,
                   COALESCE(SUM(bs.sb),0)  as sb,
                   COALESCE(SUM(bs.cs),0)  as cs,
                   COALESCE(SUM(bs.fo),0)  as fo,
                   COALESCE(SUM(bs.multi_hit_abs),0) as mhab,
                   COALESCE(SUM(bs.stay_rbi),0) as stay_rbi,
                  COALESCE(SUM(bs.stay_hits),0)    as stay_hits,
                   COALESCE(SUM(bs.roe),0) as roe
            FROM game_batter_stats bs
            JOIN games g ON bs.game_id = g.id
            WHERE bs.player_id = ?{where_extra}""",
        (player_id, *params_extra),
    )
    if not bt or not (bt.get("pa") or 0):
        return None
    row = dict(bt)
    # Stamp position info so the aggregator can compute pos_def/DRS/dWAR.
    p = db.fetchone(
        "SELECT position, defense, defense_infield, defense_outfield, defense_catcher "
        "FROM players WHERE id = ?", (player_id,))
    if p:
        row["position"]         = p["position"]
        row["defense"]          = p["defense"]
        row["defense_infield"]  = p["defense_infield"]
        row["defense_outfield"] = p["defense_outfield"]
        row["defense_catcher"]  = p["defense_catcher"]
    _aggregate_batter_rows([row], baselines=baselines)
    return row


def _player_pitching_split(player_id: int,
                           where_extra: str, params_extra: tuple,
                           wl: dict, baselines: dict) -> dict | None:
    """Aggregate one pitching split. Same shape as _player_batting_split."""
    pt = db.fetchone(
        f"""SELECT COUNT(*) as g, SUM(ps.batters_faced) as bf,
                   SUM(ps.outs_recorded) as outs,
                   SUM(ps.hits_allowed) as h, SUM(ps.runs_allowed) as r,
                   SUM(ps.er) as er, SUM(ps.bb) as bb, SUM(ps.k) as k,
                   SUM(ps.hr_allowed) as hr_allowed,
                   COALESCE(SUM(ps.hbp_allowed),0)   as hbp_allowed,
                   COALESCE(SUM(ps.unearned_runs),0) as unearned_runs,
                   COALESCE(SUM(ps.unearned_runs),0) as uer,
                   COALESCE(SUM(ps.sb_allowed),0)    as sb_allowed,
                   COALESCE(SUM(ps.cs_caught),0)     as cs_caught,
                   COALESCE(SUM(ps.fo_induced),0)    as fo_induced,
                   COALESCE(SUM(ps.pitches),0)       as pitches,
                   COALESCE(SUM(ps.er_arc1),0) as er_arc1, COALESCE(SUM(ps.er_arc2),0) as er_arc2, COALESCE(SUM(ps.er_arc3),0) as er_arc3,
                   COALESCE(SUM(ps.k_arc1),0)  as k_arc1,  COALESCE(SUM(ps.k_arc2),0)  as k_arc2,  COALESCE(SUM(ps.k_arc3),0)  as k_arc3,
                   COALESCE(SUM(ps.fo_arc1),0) as fo_arc1, COALESCE(SUM(ps.fo_arc2),0) as fo_arc2, COALESCE(SUM(ps.fo_arc3),0) as fo_arc3,
                   COALESCE(SUM(ps.bf_arc1),0) as bf_arc1, COALESCE(SUM(ps.bf_arc2),0) as bf_arc2, COALESCE(SUM(ps.bf_arc3),0) as bf_arc3,
                   COALESCE(SUM(ps.is_starter),0) as gs
            FROM {_PSTATS_DEDUP_SQL} ps
            JOIN games g ON ps.game_id = g.id
            WHERE ps.player_id = ?{where_extra}""",
        (player_id, *params_extra),
    )
    if not pt or not (pt.get("outs") or 0):
        return None
    row = dict(pt)
    row["player_id"] = player_id
    _aggregate_pitcher_rows([row], wl=wl, baselines=baselines)
    return row


def _fetch_player_overview(player_id: int,
                           baselines: dict | None = None,
                           wl: dict | None = None) -> dict | None:
    """Self-contained fetch of one player's player-row + season totals
    (batting + pitching + fielding). Used by both /player/<id> and
    /compare. Returns None if the player doesn't exist.

    Keep this lean — no game logs, no splits — so /compare with 4
    players doesn't fan out to 12+ extra queries.
    """
    player = db.fetchone(
        """SELECT p.*, t.abbrev as team_abbrev, t.name as team_name,
                  t.id as team_id, t.league as team_league, t.division as team_division
           FROM players p JOIN teams t ON p.team_id = t.id
           WHERE p.id = ?""",
        (player_id,),
    )
    if not player:
        return None
    if baselines is None:
        baselines = _league_baselines()
    if wl is None:
        wl = _pitcher_wl_map()

    bt = db.fetchone(
        """SELECT COUNT(*) as g, SUM(pa) as pa, SUM(ab) as ab, SUM(hits) as h,
                  SUM(doubles) as d2, SUM(triples) as d3, SUM(hr) as hr,
                  SUM(runs) as r, SUM(rbi) as rbi, SUM(bb) as bb, SUM(k) as k,
                  SUM(stays) as stays,
                  COALESCE(SUM(hbp),0) as hbp,
                  COALESCE(SUM(sb),0)  as sb,
                  COALESCE(SUM(cs),0)  as cs,
                  COALESCE(SUM(fo),0)  as fo,
                  COALESCE(SUM(multi_hit_abs),0) as mhab,
                  COALESCE(SUM(stay_rbi),0)     as stay_rbi,
                  COALESCE(SUM(stay_hits),0)    as stay_hits
           FROM game_batter_stats WHERE player_id = ?""", (player_id,))
    fld = db.fetchone(
        """SELECT COALESCE(SUM(po),0) AS po, COALESCE(SUM(a),0) AS a, COALESCE(SUM(e),0) AS e
           FROM game_batter_stats WHERE player_id = ?""", (player_id,))
    pt = db.fetchone(
        f"""SELECT COUNT(*) as g, SUM(batters_faced) as bf, SUM(outs_recorded) as outs,
                   SUM(hits_allowed) as h, SUM(runs_allowed) as r,
                   SUM(er) as er, SUM(bb) as bb, SUM(k) as k,
                   SUM(hr_allowed) as hr_allowed,
                   COALESCE(SUM(hbp_allowed),0)   as hbp_allowed,
                   COALESCE(SUM(unearned_runs),0) as unearned_runs,
                   COALESCE(SUM(unearned_runs),0) as uer,
                   COALESCE(SUM(sb_allowed),0)    as sb_allowed,
                   COALESCE(SUM(cs_caught),0)     as cs_caught,
                   COALESCE(SUM(fo_induced),0)    as fo_induced,
                   COALESCE(SUM(pitches),0)       as pitches,
                   COALESCE(SUM(er_arc1),0) as er_arc1, COALESCE(SUM(er_arc2),0) as er_arc2, COALESCE(SUM(er_arc3),0) as er_arc3,
                   COALESCE(SUM(k_arc1),0)  as k_arc1,  COALESCE(SUM(k_arc2),0)  as k_arc2,  COALESCE(SUM(k_arc3),0)  as k_arc3,
                   COALESCE(SUM(fo_arc1),0) as fo_arc1, COALESCE(SUM(fo_arc2),0) as fo_arc2, COALESCE(SUM(fo_arc3),0) as fo_arc3,
                   COALESCE(SUM(bf_arc1),0) as bf_arc1, COALESCE(SUM(bf_arc2),0) as bf_arc2, COALESCE(SUM(bf_arc3),0) as bf_arc3,
                   COALESCE(SUM(is_starter),0) as gs,
                   COALESCE(SUM(singles_allowed),0) as singles_allowed,
                   COALESCE(SUM(doubles_allowed),0) as doubles_allowed,
                   COALESCE(SUM(triples_allowed),0) as triples_allowed,
                   COALESCE(AVG(NULLIF(fastball_pct,0)),0) as fastball_pct,
                   COALESCE(AVG(NULLIF(breaking_pct,0)),0) as breaking_pct,
                   COALESCE(AVG(NULLIF(offspeed_pct,0)),0) as offspeed_pct
            FROM {_PSTATS_DEDUP_SQL} ps WHERE ps.player_id = ?""", (player_id,))

    bt_totals = None
    if bt and bt["pa"]:
        bt_totals = dict(bt)
        bt_totals["position"]         = player.get("position")
        bt_totals["defense"]          = player.get("defense")
        bt_totals["defense_infield"]  = player.get("defense_infield")
        bt_totals["defense_outfield"] = player.get("defense_outfield")
        bt_totals["defense_catcher"]  = player.get("defense_catcher")
        _aggregate_batter_rows([bt_totals], baselines=baselines)

    pt_totals = None
    if pt and pt["outs"]:
        pt_totals = dict(pt)
        pt_totals["player_id"] = player_id
        _aggregate_pitcher_rows([pt_totals], wl=wl, baselines=baselines)

    po = (fld["po"] if fld else 0) or 0
    a  = (fld["a"]  if fld and "a" in fld.keys() else 0) or 0
    e  = (fld["e"]  if fld else 0) or 0
    fld_totals = {
        "po": po, "a": a, "e": e, "chances": po + a + e,
        "fld_pct": ((po + a) / (po + a + e)) if (po + a + e) > 0 else None,
    }

    return {
        "player":     dict(player),
        "bt_totals":  bt_totals,
        "pt_totals":  pt_totals,
        "fld_totals": fld_totals,
    }


@app.route("/compare")
def compare():
    """Side-by-side comparison of 2-4 players.

    URL: /compare?ids=123,456,789
    JSON: append &format=json for the structured payload.
    The page also has a name-search picker so callers don't need to
    know IDs in advance.
    """
    raw = request.args.get("ids") or request.args.get("id") or ""
    try:
        ids = [int(x) for x in raw.replace(" ", "").split(",") if x]
    except ValueError:
        ids = []
    ids = ids[:4]  # cap at 4 — table gets unreadable past that

    baselines = _league_baselines()
    wl = _pitcher_wl_map()

    overviews: list[dict] = []
    for pid in ids:
        ov = _fetch_player_overview(pid, baselines=baselines, wl=wl)
        if ov is not None:
            overviews.append(ov)

    # Lightweight roster index for the picker (id, name, team_abbrev,
    # position). Keep this skinny — a 30-team league has ~1400 players
    # and the datalist needs only "name (TM)" labels.
    all_players = db.fetchall(
        """SELECT p.id, p.name, p.position, p.is_pitcher,
                  t.abbrev AS team_abbrev
           FROM players p JOIN teams t ON p.team_id = t.id
           ORDER BY p.name"""
    )

    return _serve(
        "compare.html",
        ids=ids,
        overviews=overviews,
        all_players=all_players,
        baselines=baselines,
    )


@app.route("/player/<int:player_id>")
def player_detail(player_id: int):
    player = db.fetchone(
        """SELECT p.*, t.abbrev as team_abbrev, t.name as team_name, t.id as team_id
           FROM players p JOIN teams t ON p.team_id = t.id
           WHERE p.id = ?""",
        (player_id,),
    )
    if not player:
        abort(404)

    batting_log = db.fetchall(
        """SELECT bs.*, g.game_date, g.id as game_id, g.home_team_id, g.away_team_id,
                  ht.abbrev as home_abbrev, at.abbrev as away_abbrev
           FROM game_batter_stats bs
           JOIN games g ON bs.game_id = g.id
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE bs.player_id = ?
           ORDER BY g.game_date DESC, g.id DESC LIMIT 50""",
        (player_id,),
    )
    # Dedup pitching log to one row per game appearance (Task #57 audit).
    pitching_log = db.fetchall(
        f"""SELECT ps.*, g.game_date, g.id as game_id, g.home_team_id, g.away_team_id,
                  ht.abbrev as home_abbrev, at.abbrev as away_abbrev
           FROM {_PSTATS_DEDUP_SQL} ps
           JOIN games g ON ps.game_id = g.id
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE ps.player_id = ?
           ORDER BY g.game_date DESC, g.id DESC LIMIT 50""",
        (player_id,),
    )

    bt = db.fetchone(
        """SELECT COUNT(*) as g, SUM(pa) as pa, SUM(ab) as ab, SUM(hits) as h,
                  SUM(doubles) as d2, SUM(triples) as d3, SUM(hr) as hr,
                  SUM(runs) as r, SUM(rbi) as rbi, SUM(bb) as bb, SUM(k) as k,
                  SUM(stays) as stays,
                  COALESCE(SUM(hbp),0) as hbp,
                  COALESCE(SUM(sb),0)  as sb,
                  COALESCE(SUM(cs),0)  as cs,
                  COALESCE(SUM(fo),0)  as fo,
                  COALESCE(SUM(multi_hit_abs),0) as mhab,
                  COALESCE(SUM(stay_rbi),0)     as stay_rbi,
                  COALESCE(SUM(stay_hits),0)    as stay_hits
           FROM game_batter_stats WHERE player_id = ?""",
        (player_id,),
    )
    fld = db.fetchone(
        """SELECT COALESCE(SUM(po),0) AS po, COALESCE(SUM(a),0) AS a, COALESCE(SUM(e),0) AS e
           FROM game_batter_stats WHERE player_id = ?""",
        (player_id,),
    ) or {"po": 0, "a": 0, "e": 0}

    pt = db.fetchone(
        f"""SELECT COUNT(*) as g, SUM(batters_faced) as bf, SUM(outs_recorded) as outs,
                   SUM(hits_allowed) as h, SUM(runs_allowed) as r,
                   SUM(er) as er,
                   SUM(bb) as bb, SUM(k) as k,
                   SUM(hr_allowed) as hr_allowed,
                   COALESCE(SUM(hbp_allowed),0)   as hbp_allowed,
                   COALESCE(SUM(unearned_runs),0) as unearned_runs,
                   COALESCE(SUM(unearned_runs),0) as uer,
                   COALESCE(SUM(sb_allowed),0)    as sb_allowed,
                   COALESCE(SUM(cs_caught),0)     as cs_caught,
                   COALESCE(SUM(fo_induced),0)    as fo_induced,
                   COALESCE(SUM(pitches),0)       as pitches,
                   COALESCE(SUM(er_arc1),0) as er_arc1, COALESCE(SUM(er_arc2),0) as er_arc2, COALESCE(SUM(er_arc3),0) as er_arc3,
                   COALESCE(SUM(k_arc1),0)  as k_arc1,  COALESCE(SUM(k_arc2),0)  as k_arc2,  COALESCE(SUM(k_arc3),0)  as k_arc3,
                   COALESCE(SUM(fo_arc1),0) as fo_arc1, COALESCE(SUM(fo_arc2),0) as fo_arc2, COALESCE(SUM(fo_arc3),0) as fo_arc3,
                   COALESCE(SUM(bf_arc1),0) as bf_arc1, COALESCE(SUM(bf_arc2),0) as bf_arc2, COALESCE(SUM(bf_arc3),0) as bf_arc3,
                   COALESCE(SUM(is_starter),0) as gs,
                   COALESCE(SUM(singles_allowed),0) as singles_allowed,
                   COALESCE(SUM(doubles_allowed),0) as doubles_allowed,
                   COALESCE(SUM(triples_allowed),0) as triples_allowed,
                   COALESCE(AVG(NULLIF(fastball_pct,0)),0) as fastball_pct,
                   COALESCE(AVG(NULLIF(breaking_pct,0)),0) as breaking_pct,
                   COALESCE(AVG(NULLIF(offspeed_pct,0)),0) as offspeed_pct
            FROM {_PSTATS_DEDUP_SQL} ps WHERE ps.player_id = ?""",
        (player_id,),
    )

    baselines = _league_baselines()
    wl = _pitcher_wl_map()

    bt_totals = None
    if bt and bt["pa"]:
        # Player-detail batter row needs `position` + the defense ratings
        # for DRS/dWAR; pull them from the player record so the aggregator
        # can compute the full sabermetric suite consistently.
        bt_totals = dict(bt)
        bt_totals["position"]         = player.get("position")
        bt_totals["defense"]          = player.get("defense")
        bt_totals["defense_infield"]  = player.get("defense_infield")
        bt_totals["defense_outfield"] = player.get("defense_outfield")
        bt_totals["defense_catcher"]  = player.get("defense_catcher")
        _aggregate_batter_rows([bt_totals], baselines=baselines)

    # Per-fielder defense totals (PO + A + E). Assist crediting was
    # added when pitch types were activated — the renderer now also
    # credits A on throwing outs and a pivot-fielder on DPs/TPs. Pre-
    # activation games have a=0 by migration default.
    po = fld["po"] or 0
    a  = (fld["a"] if "a" in fld.keys() else 0) or 0
    e  = fld["e"] or 0
    fld_totals = {
        "po": po,
        "a":  a,
        "e":  e,
        "chances": po + a + e,
        "fld_pct": ((po + a) / (po + a + e)) if (po + a + e) > 0 else None,
    }

    pt_totals = None
    if pt and pt["outs"]:
        outs = pt["outs"] or 0
        pt_totals = dict(pt)
        pt_totals["player_id"] = player_id
        _aggregate_pitcher_rows([pt_totals], wl=wl, baselines=baselines)
        pt_totals["os_pct"] = (outs / (27.0 * pt["g"])) if pt["g"] else 0.0
        # Workhorse Start %: count of starts (is_starter=1, phase=0) where
        # outs >= 18 AND er <= 6, over total starts. Per-row data is the
        # only honest way to compute this — aggregate counts can't tell
        # us per-game distribution.
        ws_row = db.fetchone(
            f"""SELECT
                  COALESCE(SUM(CASE WHEN is_starter=1 THEN 1 ELSE 0 END),0) AS gs,
                  COALESCE(SUM(CASE WHEN is_starter=1
                                     AND outs_recorded >= 18
                                     AND er <= 6
                                THEN 1 ELSE 0 END),0) AS ws
                FROM {_PSTATS_DEDUP_SQL} ps WHERE ps.player_id = ?""",
            (player_id,),
        ) or {}
        ws_starts = ws_row.get("gs") or 0
        ws_qual   = ws_row.get("ws") or 0
        pt_totals["ws_pct"] = (ws_qual / ws_starts) if ws_starts else 0.0
        pt_totals["gs"]     = ws_starts

    # ---------------------------------------------------------------
    # Splits (home / away / last 30 days). Computed only if the player
    # has overall stats — saves the extra queries for cold rows.
    # ---------------------------------------------------------------
    splits: dict[str, dict] = {}
    if bt_totals or pt_totals:
        last_played = db.fetchone(
            "SELECT MAX(game_date) AS d FROM games WHERE played = 1"
        )
        last_date = (last_played or {}).get("d")
        last30_cutoff = None
        if last_date:
            try:
                last30_cutoff = (_dt.date.fromisoformat(last_date)
                                 - _dt.timedelta(days=30)).isoformat()
            except Exception:
                last30_cutoff = None

        # Each split is (label, where_extra, params_extra). The team_id
        # comes from the player; "home" means the player's team was home.
        team_id = player["team_id"]
        split_specs: list[tuple[str, str, tuple]] = [
            ("Home", " AND g.home_team_id = ?", (team_id,)),
            ("Away", " AND g.away_team_id = ?", (team_id,)),
        ]
        if last30_cutoff:
            split_specs.append(
                ("Last 30 days", " AND g.game_date >= ?", (last30_cutoff,))
            )

        for label, extra, ext_params in split_specs:
            split: dict = {}
            if bt_totals:
                split["bt"] = _player_batting_split(
                    player_id, team_id, extra, ext_params, baselines)
            if pt_totals:
                split["pt"] = _player_pitching_split(
                    player_id, extra, ext_params, wl, baselines)
            splits[label] = split


    team_row = db.fetchone(
        "SELECT league FROM teams WHERE id = ?", (player["team_id"],),
    )
    league_name = team_row["league"] if team_row else None
    player_est_value = valuation.estimate_player_value(
        dict(player), league_name=league_name,
    )

    handedness_splits: dict = {}
    if bt_totals:
        handedness_splits["bat_vs_lhp"] = _player_handedness_split_batter(player_id, "L")
        handedness_splits["bat_vs_rhp"] = _player_handedness_split_batter(player_id, "R")
    if pt_totals:
        handedness_splits["pit_vs_lhb"] = _player_handedness_split_pitcher(player_id, "L")
        handedness_splits["pit_vs_rhb"] = _player_handedness_split_pitcher(player_id, "R")

    return _serve(
        "player.html",
        player=player,
        batting_log=batting_log,
        pitching_log=pitching_log,
        bt_totals=bt_totals,
        pt_totals=pt_totals,
        fld_totals=fld_totals,
        splits=splits,
        handedness_splits=handedness_splits,
        baselines=baselines,
        player_est_value=player_est_value,
    )


# ---------------------------------------------------------------------------
# /league — commissioner-style dashboard. Aggregates per-player rows into
# per-team rows, then layers league-wide context: percentile ranks,
# Pythagorean residuals, distributions, and outliers.
# ---------------------------------------------------------------------------

def _team_record_rows() -> list[dict]:
    """Per-team W / L / R / RA / RDiff from the games table."""
    return db.fetchall(
        """SELECT t.id, t.name, t.abbrev, t.league, t.division,
                  COALESCE(SUM(CASE WHEN g.winner_id = t.id THEN 1 ELSE 0 END), 0) AS w,
                  COALESCE(SUM(CASE WHEN g.played = 1
                                     AND g.winner_id IS NOT NULL
                                     AND g.winner_id <> t.id
                                     AND (g.home_team_id = t.id OR g.away_team_id = t.id)
                                    THEN 1 ELSE 0 END), 0) AS l,
                  COALESCE(SUM(CASE WHEN g.played = 1 AND g.home_team_id = t.id
                                    THEN g.home_score
                                    WHEN g.played = 1 AND g.away_team_id = t.id
                                    THEN g.away_score
                                    ELSE 0 END), 0) AS r,
                  COALESCE(SUM(CASE WHEN g.played = 1 AND g.home_team_id = t.id
                                    THEN g.away_score
                                    WHEN g.played = 1 AND g.away_team_id = t.id
                                    THEN g.home_score
                                    ELSE 0 END), 0) AS ra,
                  COALESCE(SUM(CASE WHEN g.played = 1
                                     AND (g.home_team_id = t.id OR g.away_team_id = t.id)
                                    THEN 1 ELSE 0 END), 0) AS gp
           FROM teams t
           LEFT JOIN games g ON (g.home_team_id = t.id OR g.away_team_id = t.id)
           GROUP BY t.id
           ORDER BY t.league, t.division, t.abbrev"""
    )


def _team_batting_rows(baselines: dict) -> list[dict]:
    """Per-team batting aggregate, decorated with the rate-stat suite."""
    rows = db.fetchall(
        """SELECT t.id AS team_id, t.name AS team_name, t.abbrev AS team_abbrev,
                  COUNT(DISTINCT bs.game_id) AS g,
                  SUM(bs.pa) AS pa, SUM(bs.ab) AS ab, SUM(bs.hits) AS h,
                  SUM(bs.doubles) AS d2, SUM(bs.triples) AS d3, SUM(bs.hr) AS hr,
                  SUM(bs.runs) AS r, SUM(bs.rbi) AS rbi,
                  SUM(bs.bb) AS bb, SUM(bs.k) AS k,
                  SUM(bs.stays) AS stays,
                  COALESCE(SUM(bs.hbp),0) AS hbp,
                  COALESCE(SUM(bs.sb),0)  AS sb,
                  COALESCE(SUM(bs.cs),0)  AS cs,
                  COALESCE(SUM(bs.fo),0)  AS fo,
                  COALESCE(SUM(bs.multi_hit_abs),0) AS mhab,
                  COALESCE(SUM(bs.stay_rbi),0)     AS stay_rbi,
                  COALESCE(SUM(bs.stay_hits),0)    as stay_hits,
                  COALESCE(SUM(bs.roe),0) AS roe,
                  COALESCE(SUM(bs.po),0) AS po,
                  COALESCE(SUM(bs.e),0)  AS e
           FROM teams t
           LEFT JOIN game_batter_stats bs ON bs.team_id = t.id
           GROUP BY t.id"""
    )
    rows = [dict(r) for r in rows]
    # Decorate each team row with the same sabermetric pipeline a player
    # row gets. The aggregator only needs `position`/defense for DRS;
    # team-level DRS isn't meaningful, so stamp neutral values.
    for r in rows:
        r["position"]         = "TEAM"
        r["defense"]          = 0.5
        r["defense_infield"]  = 0.5
        r["defense_outfield"] = 0.5
        r["defense_catcher"]  = 0.5
    _aggregate_batter_rows(rows, baselines=baselines)
    return rows


def _team_pitching_rows(baselines: dict) -> list[dict]:
    """Per-team pitching aggregate, decorated with the wERA / xFIP / Decay
    suite."""
    rows = db.fetchall(
        f"""SELECT t.id AS team_id, t.name AS team_name, t.abbrev AS team_abbrev,
                   COUNT(DISTINCT ps.game_id) AS g,
                   SUM(ps.batters_faced) AS bf,
                   SUM(ps.outs_recorded) AS outs,
                   SUM(ps.hits_allowed) AS h,
                   SUM(ps.runs_allowed) AS r,
                   SUM(ps.er) AS er,
                   SUM(ps.bb) AS bb, SUM(ps.k) AS k,
                   SUM(ps.hr_allowed) AS hr_allowed,
                   COALESCE(SUM(ps.hbp_allowed),0) AS hbp_allowed,
                   COALESCE(SUM(ps.unearned_runs),0) AS unearned_runs,
                   COALESCE(SUM(ps.sb_allowed),0) AS sb_allowed,
                   COALESCE(SUM(ps.cs_caught),0) AS cs_caught,
                   COALESCE(SUM(ps.fo_induced),0) AS fo_induced,
                   COALESCE(SUM(ps.pitches),0) AS pitches,
                   COALESCE(SUM(ps.er_arc1),0) AS er_arc1,
                   COALESCE(SUM(ps.er_arc2),0) AS er_arc2,
                   COALESCE(SUM(ps.er_arc3),0) AS er_arc3,
                   COALESCE(SUM(ps.k_arc1),0)  AS k_arc1,
                   COALESCE(SUM(ps.k_arc2),0)  AS k_arc2,
                   COALESCE(SUM(ps.k_arc3),0)  AS k_arc3,
                   COALESCE(SUM(ps.fo_arc1),0) AS fo_arc1,
                   COALESCE(SUM(ps.fo_arc2),0) AS fo_arc2,
                   COALESCE(SUM(ps.fo_arc3),0) AS fo_arc3,
                   COALESCE(SUM(ps.bf_arc1),0) AS bf_arc1,
                   COALESCE(SUM(ps.bf_arc2),0) AS bf_arc2,
                   COALESCE(SUM(ps.bf_arc3),0) AS bf_arc3,
                   COALESCE(SUM(ps.is_starter),0) AS gs
           FROM teams t
           LEFT JOIN {_PSTATS_DEDUP_SQL} ps ON ps.team_id = t.id
           GROUP BY t.id"""
    )
    rows = [dict(r) for r in rows]
    _aggregate_pitcher_rows(rows, wl=None, baselines=baselines)
    return rows


def _percentile_ranks(rows: list[dict], key: str, reverse: bool = False) -> None:
    """Stamp `<key>_rank` (1 = best) and `<key>_pctile` (0..100, where
    100 = best) onto every row. `reverse=True` means lower is better
    (e.g. wERA, BB%)."""
    valid = [r for r in rows if r.get(key) is not None]
    if not valid:
        return
    valid.sort(key=lambda r: r[key], reverse=not reverse)
    n = len(valid)
    for i, r in enumerate(valid):
        r[f"{key}_rank"] = i + 1
        r[f"{key}_pctile"] = round((1 - i / max(1, n - 1)) * 100, 1) if n > 1 else 100.0


def _league_distribution(rows: list[dict], key: str) -> dict:
    """Min / Q1 / median / Q3 / max / mean / std for a numeric column
    across the rows. Drops None values."""
    import statistics as _st
    vals = sorted([r[key] for r in rows if r.get(key) is not None])
    if not vals:
        return {"n": 0}
    n = len(vals)
    def _q(p):
        # Linear interpolation; matches numpy's default percentile.
        if n == 1:
            return vals[0]
        idx = (n - 1) * p
        lo = int(idx); hi = min(lo + 1, n - 1)
        frac = idx - lo
        return vals[lo] + (vals[hi] - vals[lo]) * frac
    return {
        "n":      n,
        "min":    vals[0],
        "q1":     _q(0.25),
        "median": _q(0.5),
        "q3":     _q(0.75),
        "max":    vals[-1],
        "mean":   sum(vals) / n,
        "std":    _st.pstdev(vals) if n > 1 else 0.0,
    }


def _outliers(rows: list[dict], key: str, *, n: int = 3,
              reverse: bool = False, label_key: str = "team_abbrev") -> list[dict]:
    """Top-`n` and bottom-`n` rows on a metric, with z-scores. `reverse`
    flips the polarity so "low is better" stats (wERA, BB%) put the
    lowest-value team in the `top` slot.
    """
    import statistics as _st
    valid = [r for r in rows if r.get(key) is not None]
    if not valid:
        return []
    vals = [r[key] for r in valid]
    mean = sum(vals) / len(vals)
    std  = _st.pstdev(vals) if len(vals) > 1 else 1.0
    annotated = [
        {
            "label":  r.get(label_key) or "—",
            "value":  r[key],
            "z":      ((r[key] - mean) / std) if std > 0 else 0.0,
        }
        for r in valid
    ]
    annotated.sort(key=lambda x: x["value"], reverse=not reverse)
    return annotated


def _pythag(r: int, ra: int, exp: float = 1.83) -> float:
    """Pythagorean win expectancy. exp=1.83 is the Bill James-fitted MLB
    exponent; O27 has higher run scoring but the formula's first-order
    insight (luck residual relative to RDiff) carries over."""
    if r <= 0 and ra <= 0:
        return 0.5
    return (r ** exp) / (r ** exp + ra ** exp)


def _league_team_aggregate(baselines: dict) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Build the per-team data structures shared by /league and
    /distributions?scope=teams.

    Returns (records, batting, pitching, teams_combined) where
    teams_combined has the fully-stitched per-team row with W / L / R /
    RA / Pythag / OPS / wERA / xRA / etc. plus stamped percentile
    ranks. The other three are the raw aggregate sources kept around so
    callers can do their own roll-ups (Pulse uses the batting/pitching
    SUMs).
    """
    records   = [dict(r) for r in _team_record_rows()]
    batting   = _team_batting_rows(baselines)
    pitching  = _team_pitching_rows(baselines)
    bat_by_id = {r["team_id"]: r for r in batting}
    pit_by_id = {r["team_id"]: r for r in pitching}
    teams_combined: list[dict] = []
    for rec in records:
        bat = bat_by_id.get(rec["id"], {})
        pit = pit_by_id.get(rec["id"], {})
        gp  = rec.get("gp") or 0
        r   = rec.get("r")  or 0
        ra  = rec.get("ra") or 0
        pythag_w = _pythag(r, ra)
        actual_w = (rec.get("w") / gp) if gp else 0.0
        teams_combined.append({
            "team_id":     rec["id"],
            "team_abbrev": rec["abbrev"],
            "team_name":   rec["name"],
            "league":      rec.get("league") or "",
            "division":    rec.get("division") or "",
            "gp":          gp,
            "w":           rec.get("w") or 0,
            "l":           rec.get("l") or 0,
            "win_pct":     actual_w,
            "r":           r,
            "ra":          ra,
            "rdiff":       r - ra,
            "pythag_pct":  pythag_w,
            "pythag_w":    round(pythag_w * gp) if gp else 0,
            "pythag_diff": round((pythag_w - actual_w) * gp, 1) if gp else 0.0,
            "ops":         bat.get("ops") or 0,
            "ops_plus":    bat.get("ops_plus") or 100,
            "pavg":        bat.get("pavg") or 0,
            "woba":        bat.get("woba") or 0,
            "iso":         bat.get("iso") or 0,
            "stay_pct":    bat.get("stay_pct") or 0,
            "k_pct_bat":   bat.get("k_pct") or 0,
            "bb_pct_bat":  bat.get("bb_pct") or 0,
            "hr_pct_bat":  bat.get("hr_pct") or 0,
            "war_bat":     bat.get("war") or 0,
            "werra":       pit.get("werra") or 0,
            "xra":         pit.get("xra") or 0,
            "decay":       pit.get("decay"),
            "decay_known": pit.get("decay_known", False),
            "gsc_avg":     pit.get("gsc_avg") or 0,
            "gsc_plus":    pit.get("gsc_plus") or 100,
            "k_pct_pit":   pit.get("k_pct") or 0,
            "bb_pct_pit":  pit.get("bb_pct") or 0,
            "k_minus_bb_pct": pit.get("k_minus_bb_pct") or 0,
            "war_pit":     pit.get("war") or 0,
        })
    for k, lower_better in [
        ("ops", False), ("ops_plus", False), ("pavg", False),
        ("woba", False), ("iso", False), ("war_bat", False),
        ("werra", True), ("xra", True), ("gsc_avg", False), ("gsc_plus", False),
        ("war_pit", False), ("rdiff", False), ("pythag_pct", False),
    ]:
        _percentile_ranks(teams_combined, k, reverse=lower_better)
    return records, batting, pitching, teams_combined


@app.route("/league")
def league():
    """Commissioner dashboard — league-wide aggregates + Pulse + Pythag
    residuals + per-team headline table. Distributions and outlier panels
    moved to /distributions?scope=teams (use that for analytical depth).

    Renders HTML; supports ?format=json via the existing _serve() helper.
    """
    games_played = db.fetchone(
        "SELECT COUNT(*) as n FROM games WHERE played = 1"
    )["n"] or 0
    if games_played == 0:
        return _serve("league.html",
                      games_played=0, pulse=None, teams=[], records=[],
                      batting=[], pitching=[], parity={})

    baselines = _league_baselines()
    records, batting, pitching, teams_combined = _league_team_aggregate(baselines)

    # --- Parity / competitive-balance roll-up.
    win_pcts = [t["win_pct"] for t in teams_combined if t["gp"] > 0]
    n_teams  = len(teams_combined)
    if win_pcts:
        import statistics as _st
        wp_mean   = sum(win_pcts) / len(win_pcts)
        wp_std    = _st.pstdev(win_pcts) if len(win_pcts) > 1 else 0.0
        wp_range  = max(win_pcts) - min(win_pcts)
    else:
        wp_mean = wp_std = wp_range = 0.0
    parity = {
        "win_pct_std":    wp_std,
        "win_pct_spread": wp_range,
        "win_pct_mean":   wp_mean,
    }

    # --- Pulse: TL;DR card at the top of the page.
    total_runs = sum((t["r"] or 0) + (t["ra"] or 0) for t in teams_combined) // 2
    avg_r_per_g = (total_runs / games_played) if games_played else 0
    league_total_pa = sum(b.get("pa") or 0 for b in batting)
    league_total_outs = sum(p.get("outs") or 0 for p in pitching)
    league_total_p = sum(p.get("pitches") or 0 for p in pitching)
    league_total_k_bat = sum(b.get("k") or 0 for b in batting)
    league_total_bb_bat = sum(b.get("bb") or 0 for b in batting)
    league_total_hr_bat = sum(b.get("hr") or 0 for b in batting)
    league_total_stays = sum(b.get("stays") or 0 for b in batting)
    league_total_fo = sum(b.get("fo") or 0 for b in batting)
    pulse = {
        "games_played":       games_played,
        "n_teams":             n_teams,
        "avg_runs_per_game":   round(avg_r_per_g, 2),
        "league_pa":           league_total_pa,
        "league_outs":         league_total_outs,
        "league_pitches":      league_total_p,
        "league_k_pct":        (league_total_k_bat / league_total_pa) if league_total_pa else 0,
        "league_bb_pct":       (league_total_bb_bat / league_total_pa) if league_total_pa else 0,
        "league_hr_pct":       (league_total_hr_bat / league_total_pa) if league_total_pa else 0,
        "league_stay_pct":     (league_total_stays / league_total_pa) if league_total_pa else 0,
        "league_fo_pct":       (league_total_fo / league_total_pa) if league_total_pa else 0,
        "league_pa_per_game":  round((league_total_pa / games_played / 2), 1) if games_played else 0,
        "league_p_per_game":   round((league_total_p / games_played / 2), 1) if games_played else 0,
        "league_werra":        baselines.get("league_werra"),
        "league_gsc_avg":      baselines.get("gsc_avg"),
    }

    return _serve(
        "league.html",
        games_played=games_played,
        pulse=pulse,
        teams=teams_combined,
        records=records,
        batting=batting,
        pitching=pitching,
        parity=parity,
    )


# ---------------------------------------------------------------------------
# /analytics — SABR-flavoured context-tier metrics: RE24-O27, the run-
# expectancy curve by outs-remaining, expected wOBA, and an empirically
# refit Pythagorean exponent. All derived from game_pa_log state stamps
# and team R/RA aggregates — no engine replay required.
# ---------------------------------------------------------------------------

@app.route("/analytics")
def analytics():
    """SABR analytics dashboard. Renders the four context-tier suites
    that sit on top of the rate-tier stats in /leaders and /league:

      * RE24-O27 — Run expectancy by (bases, outs-bucket).
      * RE-by-outs-remaining — 1-D curve, 27 → 1 outs.
      * Expected wOBA — strips BABIP variance via contact-quality bins.
      * Pythagorean exponent — empirically refit for O27's run env.
    """
    games_played = db.fetchone(
        "SELECT COUNT(*) AS n FROM games WHERE played = 1"
    )["n"] or 0
    if games_played == 0:
        return _serve("analytics.html",
                      games_played=0, re_table=None, re_curve=None,
                      xwoba=None, pythag=None, base_runs=None,
                      lin_w=None, gsc_summary=None)

    from o27v2.analytics import (
        build_re_table, build_re_by_outs_remaining,
        build_xwoba_table, refit_pythag_exponent,
        build_base_runs_table,
    )

    # Scale qualifier to season completeness: full-season convention is
    # 162 PA (matches /leaders); 2,430 / 15 = 162.
    min_pa = max(20, games_played // 15)

    re_table  = build_re_table()
    re_curve  = build_re_by_outs_remaining()
    xwoba     = build_xwoba_table(min_pa=min_pa)
    pythag    = refit_pythag_exponent()
    base_runs = build_base_runs_table()
    lin_w     = _linear_weights()

    # League mean / median Game Score across all starter outings, so the
    # linear-weights panel can show the auto-tune result vs the target of 50.
    gsc_dist = db.fetchall(
        """
        SELECT outs_recorded AS o, k, hits_allowed AS h, er,
               unearned_runs AS uer, bb, hr_allowed AS hr,
               fo_induced AS fo
        FROM game_pitcher_stats
        WHERE phase = 0 AND is_starter = 1
        """
    )
    if gsc_dist:
        gscs = sorted(
            _pitcher_game_score(
                r["o"] or 0, r["k"] or 0, r["h"] or 0, r["er"] or 0,
                r["uer"] or 0, r["bb"] or 0, r["hr"] or 0, r["fo"] or 0,
            )
            for r in gsc_dist
        )
        n_g = len(gscs)
        gsc_summary = {
            "n":      n_g,
            "mean":   round(sum(gscs) / n_g, 2),
            "p25":    round(gscs[n_g // 4], 1),
            "median": round(gscs[n_g // 2], 1),
            "p75":    round(gscs[3 * n_g // 4], 1),
            "min":    round(gscs[0], 1),
            "max":    round(gscs[-1], 1),
        }
    else:
        gsc_summary = None

    return _serve(
        "analytics.html",
        games_played=games_played,
        re_table=re_table,
        re_curve=re_curve,
        xwoba=xwoba,
        pythag=pythag,
        base_runs=base_runs,
        lin_w=lin_w,
        gsc_summary=gsc_summary,
        min_pa=min_pa,
    )


# ---------------------------------------------------------------------------
# /distributions — per-stat histograms + percentile tables for the
# qualifying-player population. Pairs with /league (which does the same
# thing at the team level) and answers "where does any one player sit on
# the league shape?"
# ---------------------------------------------------------------------------

def _histogram(values: list[float], n_buckets: int = 12,
               lo: float | None = None, hi: float | None = None) -> dict:
    """Equal-width histogram. Returns
        {"buckets": [{"lo","hi","count","pct"}, ...],
         "min", "max", "n",
         "percentiles": {"p10","p25","p50","p75","p90","p95","p99"}}.
    Drops None values. `pct` is each bucket's count / max-bucket-count
    (0..100), so the template can render bars with `width: NN%`.
    """
    import statistics as _st
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {"n": 0, "buckets": [], "percentiles": {}, "min": 0, "max": 0}
    v_min = lo if lo is not None else vals[0]
    v_max = hi if hi is not None else vals[-1]
    if v_max <= v_min:
        v_max = v_min + 1e-9
    width = (v_max - v_min) / n_buckets
    buckets = []
    for i in range(n_buckets):
        b_lo = v_min + i * width
        b_hi = v_min + (i + 1) * width
        if i == n_buckets - 1:
            count = sum(1 for v in vals if b_lo <= v <= b_hi)
        else:
            count = sum(1 for v in vals if b_lo <= v < b_hi)
        buckets.append({"lo": b_lo, "hi": b_hi, "count": count})
    max_count = max((b["count"] for b in buckets), default=1) or 1
    for b in buckets:
        b["pct"] = round((b["count"] / max_count) * 100, 1)

    n = len(vals)
    def _q(p):
        if n == 1:
            return vals[0]
        idx = (n - 1) * p
        i = int(idx); j = min(i + 1, n - 1)
        return vals[i] + (vals[j] - vals[i]) * (idx - i)
    return {
        "n":     n,
        "min":   vals[0],
        "max":   vals[-1],
        "mean":  sum(vals) / n,
        "std":   _st.pstdev(vals) if n > 1 else 0.0,
        "buckets": buckets,
        "percentiles": {
            "p10": _q(0.10), "p25": _q(0.25), "p50": _q(0.50),
            "p75": _q(0.75), "p90": _q(0.90), "p95": _q(0.95), "p99": _q(0.99),
        },
    }


@app.route("/distributions")
def distributions():
    """Per-stat histograms + percentile tables.

    URL:  /distributions                       — players (default)
          /distributions?scope=teams           — team-level distributions
                                                 (quartile table + outliers)
          /distributions?highlight=42          — players scope, mark where
                                                 player 42 sits on each chart
          /distributions?format=json           — structured payload
    """
    scope = (request.args.get("scope") or "players").lower()
    if scope not in ("players", "teams"):
        scope = "players"

    games_played = db.fetchone(
        "SELECT COUNT(*) as n FROM games WHERE played = 1"
    )["n"] or 0
    if games_played == 0:
        return _serve("distributions.html",
                      scope=scope, games_played=0,
                      bat_dists={}, pit_dists={},
                      team_dists={}, team_outliers={},
                      highlight=None, highlight_player=None)

    baselines = _league_baselines()

    # Teams scope: pull team-aggregate rows + reuse the
    # _league_distribution + _outliers helpers we already had.
    if scope == "teams":
        _, _, _, teams_combined = _league_team_aggregate(baselines)
        team_dists = {
            "OPS":       _league_distribution(teams_combined, "ops"),
            "OPS+":      _league_distribution(teams_combined, "ops_plus"),
            "PAVG":      _league_distribution(teams_combined, "pavg"),
            "wERA":      _league_distribution(teams_combined, "werra"),
            "xRA":       _league_distribution(teams_combined, "xra"),
            "GSc avg":   _league_distribution(teams_combined, "gsc_avg"),
            "Run diff":  _league_distribution(teams_combined, "rdiff"),
            "Win %":     _league_distribution(teams_combined, "win_pct"),
            "Pythag %":  _league_distribution(teams_combined, "pythag_pct"),
        }
        team_outliers = {
            "Run diff":      _outliers(teams_combined, "rdiff"),
            "OPS":           _outliers(teams_combined, "ops"),
            "wERA":          _outliers(teams_combined, "werra", reverse=True),
            "xRA":           _outliers(teams_combined, "xra", reverse=True),
            "Pythag luck":   _outliers(teams_combined, "pythag_diff"),
        }
        return _serve(
            "distributions.html",
            scope="teams",
            games_played=games_played,
            n_teams=len(teams_combined),
            team_dists=team_dists,
            team_outliers=team_outliers,
            # Player-scope keys still required by the template
            # so the macros don't trip:
            bat_dists={}, pit_dists={},
            n_batters=0, n_pitchers=0,
            min_pa=0, min_outs=0,
            highlight=None, highlight_player=None,
        )

    num_teams      = db.fetchone("SELECT COUNT(*) as n FROM teams")["n"] or 2
    games_per_team = max(1, (games_played * 2) // num_teams)
    min_pa   = max(3, games_per_team)
    min_outs = max(3, games_per_team)

    # Reuse the leaders-style aggregate. Same shape, same threshold.
    batting = db.fetchall(
        """SELECT p.id as player_id, p.name as player_name, p.position,
                  p.defense as defense, p.arm as arm,
                  p.defense_infield as defense_infield,
                  p.defense_outfield as defense_outfield,
                  p.defense_catcher as defense_catcher,
                  t.id as team_id, t.abbrev as team_abbrev,
                  SUM(bs.pa) as pa, SUM(bs.ab) as ab, SUM(bs.hits) as h,
                  SUM(bs.doubles) as d2, SUM(bs.triples) as d3, SUM(bs.hr) as hr,
                  SUM(bs.runs) as r, SUM(bs.rbi) as rbi,
                  SUM(bs.bb) as bb, SUM(bs.k) as k,
                  COALESCE(SUM(bs.hbp),0) as hbp,
                  COALESCE(SUM(bs.sb),0)  as sb,
                  COALESCE(SUM(bs.cs),0)  as cs,
                  COALESCE(SUM(bs.fo),0)  as fo,
                  COALESCE(SUM(bs.stays),0) as stays,
                  COALESCE(SUM(bs.multi_hit_abs),0) as mhab,
                  COALESCE(SUM(bs.stay_rbi),0)     as stay_rbi,
                  COALESCE(SUM(bs.stay_hits),0)    as stay_hits,
                  COALESCE(SUM(bs.roe),0) as roe
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           JOIN teams   t ON bs.team_id = t.id
           GROUP BY p.id
           HAVING SUM(bs.pa) >= ?""",
        (min_pa,),
    )
    batting = [dict(r) for r in batting]
    _aggregate_batter_rows(batting, baselines=baselines)

    pitching = db.fetchall(
        f"""SELECT p.id as player_id, p.name as player_name,
                  p.pitcher_skill as r_stuff, p.command as r_command,
                  p.movement as r_movement, p.stamina as r_stamina,
                  t.abbrev as team_abbrev, t.id as team_id,
                  COUNT(ps.game_id) as g,
                  SUM(ps.batters_faced)  as bf,
                  SUM(ps.outs_recorded)  as outs,
                  SUM(ps.hits_allowed)   as h, SUM(ps.runs_allowed) as r,
                  SUM(ps.er) as er, SUM(ps.bb) as bb, SUM(ps.k) as k,
                  SUM(ps.hr_allowed) as hr_allowed,
                  COALESCE(SUM(ps.hbp_allowed),0) as hbp_allowed,
                  COALESCE(SUM(ps.unearned_runs),0) as unearned_runs,
                  COALESCE(SUM(ps.fo_induced),0) as fo_induced,
                  COALESCE(SUM(ps.pitches),0) as pitches,
                  COALESCE(SUM(ps.er_arc1),0) as er_arc1, COALESCE(SUM(ps.er_arc2),0) as er_arc2, COALESCE(SUM(ps.er_arc3),0) as er_arc3,
                  COALESCE(SUM(ps.k_arc1),0) as k_arc1, COALESCE(SUM(ps.k_arc2),0) as k_arc2, COALESCE(SUM(ps.k_arc3),0) as k_arc3,
                  COALESCE(SUM(ps.fo_arc1),0) as fo_arc1, COALESCE(SUM(ps.fo_arc2),0) as fo_arc2, COALESCE(SUM(ps.fo_arc3),0) as fo_arc3,
                  COALESCE(SUM(ps.bf_arc1),0) as bf_arc1, COALESCE(SUM(ps.bf_arc2),0) as bf_arc2, COALESCE(SUM(ps.bf_arc3),0) as bf_arc3,
                  COALESCE(SUM(ps.is_starter),0) as gs,
                  COALESCE(SUM(ps.singles_allowed),0) as singles_allowed,
                  COALESCE(SUM(ps.doubles_allowed),0) as doubles_allowed,
                  COALESCE(SUM(ps.triples_allowed),0) as triples_allowed,
                  COALESCE(AVG(NULLIF(ps.fastball_pct,0)) * 100,0) as fastball_pct,
                  COALESCE(AVG(NULLIF(ps.breaking_pct,0)) * 100,0) as breaking_pct,
                  COALESCE(AVG(NULLIF(ps.offspeed_pct,0)) * 100,0) as offspeed_pct
           FROM {_PSTATS_DEDUP_SQL} ps
           JOIN players p ON ps.player_id = p.id
           JOIN teams   t ON ps.team_id = t.id
           GROUP BY p.id
           HAVING SUM(ps.outs_recorded) >= ?""",
        (min_outs,),
    )
    pitching = [dict(r) for r in pitching]
    _aggregate_pitcher_rows(pitching, _pitcher_wl_map(), baselines=baselines)

    # Highlight: optionally mark where one specific player sits.
    highlight_id_str = request.args.get("highlight") or request.args.get("h") or ""
    highlight_id = None
    highlight_player = None
    if highlight_id_str.isdigit():
        highlight_id = int(highlight_id_str)
        # Find the row in either dataset.
        for r in batting + pitching:
            if r.get("player_id") == highlight_id:
                highlight_player = {
                    "player_id":   r.get("player_id"),
                    "player_name": r.get("player_name"),
                    "team_abbrev": r.get("team_abbrev"),
                }
                break

    # Stat catalog: (key, label, fmt, side='bat'|'pit', is_pct, hi_lo='lo' if lower-is-better)
    bat_specs = [
        ("pavg",          "PAVG",     "%.3f", False),
        ("ops",           "OPS",      "%.3f", False),
        ("ops_plus",      "OPS+",     "%.0f", False),
        ("woba",          "wOBA",     "%.3f", False),
        ("bavg",          "BAVG",     "%.3f", False),
        ("iso",           "ISO",      "%.3f", False),
        ("babip",         "BABIP",    "%.3f", False),
        ("k_pct",         "K%",       "%.1f%%", True),
        ("bb_pct",        "BB%",      "%.1f%%", True),
        ("stay_rbi_pct",  "2C-RBI%",  "%.1f%%", True),
        ("stay_conv_pct", "2C-Conv%", "%.1f%%", True),
        ("mhab_pct",      "MhAB%",    "%.1f%%", True),
        ("war",           "WAR",      "%.2f", False),
    ]
    pit_specs = [
        ("werra",         "wERA",      "%.2f", False),
        ("xra",           "xRA",       "%.2f", False),
        ("decay",         "Decay",     "%+.1f", False),
        ("gsc_avg",       "GSc avg",   "%.1f", False),
        ("gsc_plus",      "GSc+",      "%.0f", False),
        ("os_plus",       "OS+",       "%.0f", False),
        ("k_pct",         "K%",        "%.1f%%", True),
        ("bb_pct",        "BB%",       "%.1f%%", True),
        ("hr_pct",        "HR%",       "%.1f%%", True),
        ("k_minus_bb_pct","K-BB%",     "%.1f%%", True),
        ("oavg",          "oAVG",      "%.3f", False),
        ("war",           "WAR",       "%.2f", False),
    ]

    def _build(rows: list[dict], specs: list, side: str) -> dict:
        out: dict[str, dict] = {}
        for key, label, fmt, is_pct in specs:
            vals = [r.get(key) for r in rows if r.get(key) is not None]
            # For Decay specifically, only include rows where the player
            # has cross-arc sample (decay_known).
            if key == "decay":
                vals = [r.get(key) for r in rows
                        if r.get("decay_known") and r.get(key) is not None]
            h = _histogram(vals, n_buckets=12)
            h["label"] = label
            h["fmt"]   = fmt
            h["is_pct"] = is_pct
            h["side"]  = side
            # Where does the highlighted player sit?
            if highlight_id is not None:
                for r in rows:
                    if r.get("player_id") == highlight_id:
                        h["highlight_value"] = r.get(key)
                        break
                else:
                    h["highlight_value"] = None
            out[key] = h
        return out

    bat_dists = _build(batting, bat_specs, "bat")
    pit_dists = _build(pitching, pit_specs, "pit")

    return _serve(
        "distributions.html",
        scope="players",
        games_played=games_played,
        n_batters=len(batting),
        n_pitchers=len(pitching),
        min_pa=min_pa,
        min_outs=min_outs,
        bat_dists=bat_dists,
        pit_dists=pit_dists,
        team_dists={}, team_outliers={},
        highlight=highlight_id,
        highlight_player=highlight_player,
    )


@app.route("/teams")
def teams():
    teams_list = db.fetchall(
        """SELECT t.*, COUNT(p.id) as player_count
           FROM teams t LEFT JOIN players p ON p.team_id = t.id
           GROUP BY t.id
           ORDER BY t.league, t.division, t.name"""
    )
    return _serve("teams.html", teams=teams_list, win_pct=_win_pct)


@app.route("/team/<int:team_id>")
def team_detail(team_id: int):
    team = db.fetchone("SELECT * FROM teams WHERE id = ?", (team_id,))
    if not team:
        abort(404)

    roster = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? ORDER BY is_pitcher, position, id",
        (team_id,),
    )
    ids = [p["id"] for p in roster]
    bstats: dict[int, dict] = {}
    pstats: dict[int, dict] = {}
    if ids:
        ph = ",".join("?" * len(ids))
        for r in db.fetchall(
            f"""SELECT player_id,
                       COUNT(game_id) AS gp,
                       SUM(pa) AS pa, SUM(ab) AS ab, SUM(hits) AS h,
                       SUM(doubles) AS d2, SUM(triples) AS d3, SUM(hr) AS hr,
                       SUM(runs) AS r, SUM(rbi) AS rbi,
                       SUM(bb) AS bb, SUM(k) AS k,
                       COALESCE(SUM(hbp),0) AS hbp,
                       COALESCE(SUM(sb),0)  AS sb,
                       COALESCE(SUM(cs),0)  AS cs,
                       COALESCE(SUM(fo),0)  AS fo,
                       COALESCE(SUM(multi_hit_abs),0) AS mhab
                FROM game_batter_stats
                WHERE player_id IN ({ph}) GROUP BY player_id""",
            tuple(ids),
        ):
            bstats[r["player_id"]] = r
        for r in db.fetchall(
            f"""SELECT ps.player_id,
                       COUNT(ps.game_id) AS gp,
                       SUM(ps.batters_faced) AS bf,
                       SUM(ps.outs_recorded) AS outs,
                       SUM(ps.hits_allowed) AS h, SUM(ps.runs_allowed) AS r, SUM(ps.er) AS er,
                       SUM(ps.bb) AS bb, SUM(ps.k) AS k,
                       SUM(ps.hr_allowed) AS hr_allowed,
                       SUM(ps.pitches) AS pitches,
                       COUNT(*) AS g,
                       COALESCE(SUM(ps.hbp_allowed),0)   AS hbp_allowed,
                       COALESCE(SUM(ps.unearned_runs),0) AS unearned_runs,
                       COALESCE(SUM(ps.unearned_runs),0) AS uer,
                       COALESCE(SUM(ps.sb_allowed),0)    AS sb_allowed,
                       COALESCE(SUM(ps.cs_caught),0)     AS cs_caught,
                       COALESCE(SUM(ps.fo_induced),0)    AS fo_induced,
                       COALESCE(SUM(ps.er_arc1),0) AS er_arc1, COALESCE(SUM(ps.er_arc2),0) AS er_arc2, COALESCE(SUM(ps.er_arc3),0) AS er_arc3,
                       COALESCE(SUM(ps.k_arc1),0)  AS k_arc1,  COALESCE(SUM(ps.k_arc2),0)  AS k_arc2,  COALESCE(SUM(ps.k_arc3),0)  AS k_arc3,
                       COALESCE(SUM(ps.fo_arc1),0) AS fo_arc1, COALESCE(SUM(ps.fo_arc2),0) AS fo_arc2, COALESCE(SUM(ps.fo_arc3),0) AS fo_arc3,
                       COALESCE(SUM(ps.bf_arc1),0) AS bf_arc1, COALESCE(SUM(ps.bf_arc2),0) AS bf_arc2, COALESCE(SUM(ps.bf_arc3),0) AS bf_arc3,
                       COALESCE(SUM(ps.is_starter),0) AS gs
                FROM {_PSTATS_DEDUP_SQL} ps
                WHERE ps.player_id IN ({ph}) GROUP BY ps.player_id""",
            tuple(ids),
        ):
            pstats[r["player_id"]] = r

    wl = _pitcher_wl_map()
    baselines = _league_baselines()
    batters: list[dict] = []
    pitchers: list[dict] = []
    for p in roster:
        if p["is_pitcher"]:
            row = dict(p)
            row.update(pstats.get(p["id"], {}))
            _aggregate_pitcher_rows([row], wl, baselines=baselines)
            pitchers.append(row)
        else:
            row = dict(p)
            row.update(bstats.get(p["id"], {}))
            _aggregate_batter_rows([row], baselines=baselines)
            batters.append(row)

    recent = db.fetchall(
        """SELECT g.*,
                  ht.name as home_name, ht.abbrev as home_abbrev,
                  at.name as away_name, at.abbrev as away_abbrev,
                  wt.abbrev as winner_abbrev
           FROM games g
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           LEFT JOIN teams wt ON g.winner_id = wt.id
           WHERE g.played = 1 AND (g.home_team_id = ? OR g.away_team_id = ?)
           ORDER BY g.game_date DESC LIMIT 10""",
        (team_id, team_id),
    )
    team_payroll = valuation.estimate_team_payroll(team_id)
    return _serve("team.html",
                           team=team,
                           batters=batters,
                           pitchers=pitchers,
                           recent=recent,
                           win_pct=_win_pct,
                           team_payroll=team_payroll)


@app.route("/team/<int:team_id>/edit", methods=["GET"])
def team_edit_get(team_id: int):
    from o27.engine.weather import archetype_for_city
    team = db.fetchone("SELECT * FROM teams WHERE id = ?", (team_id,))
    if not team:
        abort(404)
    return _serve("team_edit.html",
                           team=team,
                           current_archetype=archetype_for_city(team["city"] or ""))


@app.route("/team/<int:team_id>/edit", methods=["POST"])
def team_edit_post(team_id: int):
    from flask import flash
    from o27.engine.weather import draw_weather, archetype_for_city
    import random as _random

    team = db.fetchone("SELECT * FROM teams WHERE id = ?", (team_id,))
    if not team:
        abort(404)

    new_name   = (request.form.get("name", "") or "").strip()
    new_abbrev = (request.form.get("abbrev", "") or "").strip().upper()
    new_city   = (request.form.get("city", "") or "").strip()

    # Validation: bounce back to the form with a flash on error so the
    # user keeps their typed values via the rendered form fields.
    err: str | None = None
    if not new_name:
        err = "Team name cannot be blank."
    elif not (2 <= len(new_abbrev) <= 4) or not new_abbrev.isalnum():
        err = "Abbreviation must be 2-4 alphanumeric characters."
    if err:
        flash(err, "error")
        return redirect(url_for("team_edit_get", team_id=team_id))

    # Detect collision: another team already using this abbrev.
    clash = db.fetchone(
        "SELECT id FROM teams WHERE abbrev = ? AND id != ?",
        (new_abbrev, team_id),
    )
    if clash:
        flash(f"Abbreviation {new_abbrev} is already used by another team.", "error")
        return redirect(url_for("team_edit_get", team_id=team_id))

    city_changed = (new_city != (team["city"] or ""))

    db.execute(
        "UPDATE teams SET name = ?, abbrev = ?, city = ? WHERE id = ?",
        (new_name, new_abbrev, new_city, team_id),
    )

    # Re-roll weather for unplayed home games when the city changes —
    # archetype_for_city is called fresh per-game inside draw_weather, so
    # changing the city alone changes the climatology. A new RNG forked
    # off the team_id keeps the reseed deterministic per team.
    rerolled = 0
    if city_changed:
        unplayed_home = db.fetchall(
            "SELECT id, game_date FROM games WHERE home_team_id = ? AND played = 0",
            (team_id,),
        )
        if unplayed_home:
            rng = _random.Random(team_id ^ 0xCAFE_BABE)
            for g in unplayed_home:
                w = draw_weather(rng, new_city, g["game_date"])
                db.execute(
                    """UPDATE games SET temperature_tier=?, wind_tier=?,
                       humidity_tier=?, precip_tier=?, cloud_tier=?
                       WHERE id=?""",
                    (w.temperature, w.wind, w.humidity, w.precip, w.cloud, g["id"]),
                )
                rerolled += 1

    if city_changed:
        flash(
            f"Team updated. Re-rolled weather for {rerolled} unplayed home games "
            f"({archetype_for_city(new_city)} archetype).",
            "info",
        )
    else:
        flash("Team updated.", "info")

    return redirect(url_for("team_detail", team_id=team_id))


@app.route("/transactions")
def transactions():
    from o27v2.transactions import get_transactions
    team_id    = request.args.get("team", type=int)
    event_type = request.args.get("type")

    txns  = get_transactions(team_id=team_id, event_type=event_type or None, limit=300)
    teams = db.fetchall("SELECT id, name, abbrev FROM teams ORDER BY name")

    event_types = ["injury", "return", "promotion", "penalty",
                   "deadline_trade", "inseason_trade",
                   "waiver_claim", "waiver_release"]
    selected_team = None
    if team_id:
        selected_team = db.fetchone("SELECT * FROM teams WHERE id = ?", (team_id,))

    counts = {et: 0 for et in event_types}
    all_txns = get_transactions(limit=50000)
    for tx in all_txns:
        et = tx.get("event_type", "")
        if et in counts:
            counts[et] += 1

    return _serve("transactions.html",
                           transactions=txns,
                           teams=teams,
                           selected_team=selected_team,
                           event_type=event_type or "",
                           event_types=event_types,
                           counts=counts)


@app.route("/playoffs")
def playoffs_view():
    """Bracket + awards for the current season. Shows the field and
    each round's series with current win counts and the champion when
    the final concludes."""
    from o27v2.playoffs import (
        get_bracket, champion as _champion, compute_field,
        playoffs_initiated, regular_season_complete,
    )
    from o27v2.awards import get_awards, get_award_results

    bracket = get_bracket()

    # Attach the list of games played (or scheduled) in each series so
    # the bracket tile can render clickable G1/G2/… rows linking to the
    # box score. Single bulk query, then group in Python.
    series_ids = [s["id"] for s in bracket]
    games_by_series: dict[int, list[dict]] = {}
    if series_ids:
        qmarks = ",".join("?" * len(series_ids))
        game_rows = db.fetchall(
            f"""SELECT id, series_id, game_date, played,
                       home_team_id, away_team_id,
                       home_score, away_score, winner_id
                FROM games
                WHERE series_id IN ({qmarks})
                ORDER BY series_id, game_date, id""",
            tuple(series_ids),
        )
        for r in game_rows:
            games_by_series.setdefault(r["series_id"], []).append(r)
    for s in bracket:
        s["games"] = games_by_series.get(s["id"], [])

    # Group by round for the template.
    rounds: dict[int, list[dict]] = {}
    for s in bracket:
        rounds.setdefault(s["round_idx"], []).append(s)
    rounds_sorted = sorted(rounds.items())

    # team_id → abbrev lookup for the bracket games list. Reused for the
    # projected-field branch below.
    team_rows = db.fetchall(
        "SELECT id, name, abbrev, league, division, wins, losses FROM teams"
    )
    team_abbrev = {t["id"]: t["abbrev"] for t in team_rows}

    # Round names — count from the final backwards.
    def _round_name(rounds_to_final: int) -> str:
        return ["Final", "Semifinals", "Quarterfinals", "Wild Card"][
            min(rounds_to_final, 3)]

    # If playoffs haven't initiated yet, surface the projected field.
    projected_field: list[dict] = []
    if not playoffs_initiated() and not regular_season_complete():
        try:
            projected_field = compute_field(team_rows)
        except Exception:
            projected_field = []

    # BBWAA-style top-5 per category. Falls through to the single-winner
    # `awards` list for seasons that pre-date the ballots table.
    award_results: dict[str, list[dict]] = {}
    try:
        for cat in ("mvp", "cy_young", "roy", "ws_mvp"):
            rows = get_award_results(category=cat, limit=5)
            if rows:
                award_results[cat] = rows
    except Exception:
        award_results = {}

    return _serve(
        "playoffs.html",
        rounds=rounds_sorted,
        round_name=_round_name,
        champion=_champion(),
        awards=get_awards(),
        award_results=award_results,
        team_abbrev=team_abbrev,
        projected_field=projected_field,
        playoffs_initiated=playoffs_initiated(),
        regular_season_complete=regular_season_complete(),
    )


@app.route("/free-agents")
def free_agents():
    """Browse the free-agent pool. Players in this list have team_id NULL
    and are eligible to be claimed by the weekly Sunday match-day sweep
    (see o27v2/waivers.py)."""
    from o27v2.waivers import _player_overall, _last_sweep_date

    pos_filter = (request.args.get("pos") or "").strip().upper()
    kind       = (request.args.get("kind") or "all").strip().lower()  # all|hitters|pitchers
    sort       = (request.args.get("sort") or "ovr").strip().lower()

    where = ["team_id IS NULL"]
    params: list = []
    if pos_filter:
        where.append("position = ?")
        params.append(pos_filter)
    if kind == "hitters":
        where.append("is_pitcher = 0")
    elif kind == "pitchers":
        where.append("is_pitcher = 1")

    rows = db.fetchall(
        f"SELECT * FROM players WHERE {' AND '.join(where)}",
        tuple(params),
    )
    for r in rows:
        r["overall"] = _player_overall(r)

    if sort == "name":
        rows.sort(key=lambda r: r["name"])
    elif sort == "pos":
        rows.sort(key=lambda r: (r["position"], -r["overall"]))
    else:  # default: best-overall first
        rows.sort(key=lambda r: -r["overall"])

    # Group by position for the per-bucket counts
    by_pos: dict[str, int] = {}
    for r in db.fetchall("SELECT position FROM players WHERE team_id IS NULL"):
        by_pos[r["position"]] = by_pos.get(r["position"], 0) + 1

    return _serve("free_agents.html",
                  free_agents=rows,
                  total=len(rows),
                  by_pos=sorted(by_pos.items(), key=lambda kv: -kv[1]),
                  selected_pos=pos_filter,
                  kind=kind,
                  sort=sort,
                  last_sweep=_last_sweep_date())


@app.route("/new-league", methods=["GET"])
def new_league_get():
    from o27v2.league import get_name_region_presets, get_name_regions
    configs = get_league_configs()
    current_team_count = db.fetchone("SELECT COUNT(*) as n FROM teams")
    current_n = current_team_count["n"] if current_team_count else 0
    return _serve("new_league.html",
                           configs=configs,
                           current_team_count=current_n,
                           name_region_presets=get_name_region_presets(),
                           name_regions=get_name_regions())


@app.route("/new-league", methods=["POST"])
def new_league_post():
    from o27v2.league import seed_league, build_custom_config
    from o27v2.schedule import seed_schedule
    from o27v2.season_archive import set_active_league_meta, multi_season_status
    from flask import flash

    # Refuse to start if the multi-season runner is active. Both flows
    # call db.drop_all() / seed_league / seed_schedule on the same DB
    # without coordination — running them concurrently produces a race
    # where the runner's drop wipes teams between this flow's seed_league
    # and seed_schedule, surfacing as `FOREIGN KEY constraint failed` on
    # the games-table insert.
    status = multi_season_status()
    if status.get("running"):
        cur = status.get("current_season_index") or 0
        tgt = status.get("target_seasons") or 0
        flash(
            f"Multi-season test sim is running (season {cur}/{tgt}). "
            f"Wait for it to finish before creating a new league.",
            "error",
        )
        return redirect(url_for("new_league_get"))

    rng_seed = int(request.form.get("rng_seed", 42) or 42)
    mode     = (request.form.get("mode") or "preset").strip()

    # Two paths: a named preset (the JSON files in data/league_configs/)
    # or a custom config built from the form fields.
    if mode == "preset":
        config_id = request.form.get("config_id", "30teams")
        configs   = get_league_configs()
        if config_id not in configs:
            abort(400, f"Unknown config: {config_id}")
        custom_cfg  = None
        meta_cfg_id = config_id
    else:
        # Custom: build a config dict from the form. Validation lives in
        # build_custom_config; surface the message to the user instead of
        # showing a 400 page.
        try:
            dows = request.form.getlist("weekly_off_dows")
            custom_cfg = build_custom_config(
                team_count           = int(request.form.get("team_count", 30) or 30),
                leagues_count        = int(request.form.get("leagues_count", 2) or 2),
                divisions_per_league = int(request.form.get("divisions_per_league", 3) or 3),
                games_per_team       = int(request.form.get("games_per_team", 162) or 162),
                season_days          = int(request.form.get("season_days", 186) or 186),
                intra_division_weight = float(request.form.get("intra_division_weight", 0.46) or 0.46),
                inter_division_weight = float(request.form.get("inter_division_weight", 0.54) or 0.54),
                season_year          = int(request.form.get("season_year", 2026) or 2026),
                season_start_month   = int(request.form.get("season_start_month", 4) or 4),
                season_start_day     = int(request.form.get("season_start_day", 1) or 1),
                weekly_off_dows      = [int(d) for d in dows if d.strip().isdigit()],
                max_consecutive_game_days = int(request.form.get("max_consecutive_game_days", 20) or 20),
                target_stand_length       = int(request.form.get("target_stand_length", 3) or 3),
                level                = request.form.get("level", "MLB") or "MLB",
                label                = request.form.get("label") or None,
                gender               = request.form.get("gender", "male") or "male",
                name_region_preset   = request.form.get("name_region_preset") or None,
            )
        except (ValueError, TypeError) as e:
            flash(f"League configuration error: {e}", "error")
            return redirect(url_for("new_league_get"))
        meta_cfg_id = "custom"

    db.drop_all()
    db.init_db()
    seed_league(rng_seed=rng_seed,
                config_id=meta_cfg_id if custom_cfg is None else "custom",
                config=custom_cfg)
    seed_schedule(rng_seed=rng_seed,
                  config_id=meta_cfg_id if custom_cfg is None else "custom",
                  config=custom_cfg)
    set_active_league_meta(rng_seed, meta_cfg_id)

    # Surface a schedule-quality report so the user sees imbalance
    # warnings (uneven opponent counts, off-day spread, etc.) before
    # they get deep into a season and notice a lopsided play sample.
    try:
        from o27v2.schedule import verify_opponent_balance
        teams_rows = db.fetchall("SELECT id, division FROM teams")
        games_rows = db.fetchall("SELECT game_date, home_team_id, away_team_id FROM games")
        report = verify_opponent_balance(
            [dict(g) for g in games_rows],
            [dict(t) for t in teams_rows],
            custom_cfg,
        )
        msg = (f"League ready: {len(games_rows)} games · "
               f"{report['intra_avg']:.1f} games per intra-div opponent · "
               f"{report['inter_avg']:.1f} per inter-div · "
               f"off-days {report['off_day_min']}-{report['off_day_max']}/team.")
        flash(msg, "info")
        for w in report.get("warnings", []):
            flash(f"Schedule warning: {w}", "warning")
    except Exception as e:
        app.logger.exception("verify_opponent_balance failed: %s", e)

    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.route("/api/sim", methods=["POST"])
def api_sim():
    data      = request.get_json(silent=True) or {}
    n         = int(data.get("n", 5))
    n         = max(1, min(n, 50))
    seed_base = data.get("seed_base")
    results   = simulate_next_n(n, seed_base=seed_base)
    resync_sim_clock()
    invalidate_linear_weights()
    return jsonify({"simulated": len(results), "results": results})


@app.route("/api/sim/today", methods=["POST"])
def api_sim_today():
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    results = simulate_date(current)
    # Only advance the clock if every game on `current` actually played.
    # Otherwise we'd desync: schedule shows unplayed games on a past day
    # and the user has no obvious way to retry them.
    if not _had_errors(results):
        next_day = (_dt.date.fromisoformat(current) + _dt.timedelta(days=1)).isoformat()
        advance_sim_clock(_clamp_to_last(next_day))
        # Also catch the clock up to reality. If a previous bulk sim
        # request was dropped by the proxy / mobile Safari but the Flask
        # thread kept running, games may have been played far past the
        # clock — resync jumps the clock forward to the earliest
        # un-played day so the standings match the date again.
        resync_sim_clock()
    invalidate_linear_weights()
    return jsonify(_sim_response(current, current, results))


# Bulk-sim chunk budget. Each /api/sim/{week,month,all-star,season} call
# bails out of simulate_through after this many seconds and returns
# `done=False` so the JS can immediately POST the same endpoint again.
# Keeps each round-trip well under both the Fly proxy idle window and
# mobile Safari's fetch timeout (the original "Load failed" symptom).
_BULK_SIM_MAX_SECONDS = 8.0


def _run_bulk_sim_chunk(current: str, target: str) -> tuple[list, bool]:
    """Sim toward `target` for up to _BULK_SIM_MAX_SECONDS, then advance the
    clock to reflect actual progress. Returns (results, done) where
    `done=True` means no unplayed games <= target remain."""
    results = simulate_through(target, max_seconds=_BULK_SIM_MAX_SECONDS)
    if _had_errors(results):
        resync_sim_clock()
        # Errors stop the loop on the JS side regardless of done flag.
        return results, True
    earliest = get_earliest_unplayed_date()
    done = earliest is None or earliest > target
    if done:
        next_day = (_dt.date.fromisoformat(target) + _dt.timedelta(days=1)).isoformat()
        advance_sim_clock(_clamp_to_last(next_day))
    else:
        # Partial chunk: advance the clock to the next unplayed day so the
        # dashboard badge updates between chunks. `earliest` is the next
        # unplayed day's date (= the day the user is "currently on").
        advance_sim_clock(earliest)
    return results, done


@app.route("/api/sim/week", methods=["POST"])
def api_sim_week():
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    target  = (_dt.date.fromisoformat(current) + _dt.timedelta(days=6)).isoformat()
    results, done = _run_bulk_sim_chunk(current, target)
    invalidate_linear_weights()
    return jsonify(_sim_response(current, target, results, done=done))


@app.route("/api/sim/month", methods=["POST"])
def api_sim_month():
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    target  = _end_of_month(_dt.date.fromisoformat(current)).isoformat()
    results, done = _run_bulk_sim_chunk(current, target)
    invalidate_linear_weights()
    return jsonify(_sim_response(current, target, results, done=done))


@app.route("/api/sim/all-star", methods=["POST"])
def api_sim_all_star():
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    target  = get_all_star_date()
    if target is None or current > target:
        return jsonify(_sim_response(current, target, []))
    results, done = _run_bulk_sim_chunk(current, target)
    invalidate_linear_weights()
    return jsonify(_sim_response(current, target, results, done=done))


@app.route("/api/sim/season", methods=["POST"])
def api_sim_season():
    from o27v2.season_archive import archive_current_season
    if is_season_complete():
        # Already complete — archive if we haven't snapshotted this season yet.
        sid = archive_current_season(run_invariants=True)
        resp = _sim_response(None, None, [])
        resp["archived_season_id"] = sid
        return jsonify(resp)
    current = get_current_sim_date()
    target  = get_last_scheduled_date()

    # Sim one chunk's worth toward the schedule's current end. The JS
    # loops until done — each call also re-targets `get_last_scheduled_date`,
    # which grows as playoff initiation + post-game series scheduling
    # add games dated after the regular-season finale.
    if target is None:
        return jsonify(_sim_response(current, None, [], done=True))
    results = simulate_through(target, max_seconds=_BULK_SIM_MAX_SECONDS)

    # Done = the season is fully complete (regular + all playoff rounds).
    # Note: at the end of a chunk, the schedule may have grown (a series
    # advanced and added a game). We always loop again until is_season_complete.
    archived_id = None
    if _had_errors(results):
        resync_sim_clock()
        done = True  # stop the JS loop on errors
    else:
        earliest = get_earliest_unplayed_date()
        if earliest is not None:
            advance_sim_clock(earliest)
            done = False
        else:
            final_last = get_last_scheduled_date() or target
            next_day = (_dt.date.fromisoformat(final_last) + _dt.timedelta(days=1)).isoformat()
            advance_sim_clock(next_day)
            done = is_season_complete()
            if done:
                try:
                    archived_id = archive_current_season(run_invariants=True)
                except Exception as e:
                    archived_id = None
                    app.logger.exception("auto-archive after /api/sim/season failed: %s", e)
    invalidate_linear_weights()
    resp = _sim_response(current, target, results, done=done)
    resp["archived_season_id"] = archived_id
    return jsonify(resp)


# ---------------------------------------------------------------------------
# Task #62: season lifecycle (reset + multi-season + history)
# ---------------------------------------------------------------------------

@app.route("/api/sim/multi-season", methods=["POST"])
def api_sim_multi_season():
    """Start an N-season run in the background. Returns 202; the dashboard
    polls /api/sim/multi-season/status for progress (current season number,
    games simmed) and redirects to /seasons when the run completes."""
    from o27v2.season_archive import start_multi_season
    data = request.get_json(silent=True) or {}
    n         = int(data.get("n", 3))
    base_seed = int(data.get("seed", 42))
    config_id = (data.get("config_id") or "30teams").strip()
    if config_id not in get_league_configs():
        return jsonify({"ok": False, "error": f"unknown config: {config_id}"}), 400
    started, msg = start_multi_season(n, base_seed=base_seed, config_id=config_id)
    return jsonify({"ok": started, "message": msg}), (202 if started else 409)


@app.route("/api/sim/multi-season/status")
def api_sim_multi_season_status():
    from o27v2.season_archive import multi_season_status
    return jsonify(multi_season_status())


@app.route("/api/season/promote-relegate", methods=["POST"])
def api_season_promote_relegate():
    """Run the tiered-config promotion/relegation pass.

    Body (all optional):
      {
        "dry_run":   bool,   # default false — apply DB updates if false
        "rng_seed":  int,    # seeds the playoff Bernoulli draw
      }

    Returns the structured report produced by
    `o27v2.promotion.apply_promotion_relegation`. 400s if the active
    league config isn't tiered.
    """
    from o27v2 import promotion
    cfg = _active_config()
    if not cfg or cfg.get("schedule_mode") != "tiered":
        return jsonify({
            "ok": False,
            "error": "Active league config is not tiered. Pick a "
                     "promotion/relegation config from /new-league first.",
        }), 400

    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get("dry_run", False))
    rng_seed = int(data.get("rng_seed") or 0)

    try:
        report = promotion.apply_promotion_relegation(
            cfg, rng_seed=rng_seed, dry_run=dry_run,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "report": report})


@app.route("/api/season/archive", methods=["POST"])
def api_season_archive():
    """Snapshot the current DB into the seasons history (no reset)."""
    from o27v2.season_archive import archive_current_season
    sid = archive_current_season(run_invariants=True)
    if sid is None:
        return jsonify({"ok": False, "message": "Nothing to archive (no played games)."}), 400
    return jsonify({"ok": True, "season_id": sid})


@app.route("/api/season/<int:season_id>", methods=["DELETE"])
def api_season_delete(season_id: int):
    """Delete a single archived season + its standings/leaders rows.

    If the season being deleted is the one marked as the current league's
    archive (`current_season_archived_id` in sim_meta), clear that marker
    so a fresh archive can be written for the live league.
    """
    row = db.fetchone("SELECT id FROM seasons WHERE id = ?", (season_id,))
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    db.execute("DELETE FROM season_standings        WHERE season_id = ?", (season_id,))
    db.execute("DELETE FROM season_batting_leaders  WHERE season_id = ?", (season_id,))
    db.execute("DELETE FROM season_pitching_leaders WHERE season_id = ?", (season_id,))
    db.execute("DELETE FROM seasons                 WHERE id = ?",        (season_id,))
    marker = db.fetchone(
        "SELECT value FROM sim_meta WHERE key = 'current_season_archived_id'"
    )
    if marker and str(marker.get("value")) == str(season_id):
        db.execute("DELETE FROM sim_meta WHERE key = 'current_season_archived_id'")
    return jsonify({"ok": True, "deleted_id": season_id})


@app.route("/api/seasons/clear", methods=["POST"])
def api_seasons_clear():
    """Delete every archived season. Used when starting fresh."""
    db.execute("DELETE FROM season_standings")
    db.execute("DELETE FROM season_batting_leaders")
    db.execute("DELETE FROM season_pitching_leaders")
    db.execute("DELETE FROM seasons")
    db.execute("DELETE FROM sim_meta WHERE key = 'current_season_archived_id'")
    return jsonify({"ok": True})


@app.route("/api/season/advance", methods=["POST"])
def api_season_advance():
    """Advance to the next season — preserves rosters / team identities,
    ages every player +1, runs the development pass (per-attribute
    growth/decline driven by age + org_strength), rolls new org_strength
    for each team via the bond-market formula, resets W-L, wipes the
    games + playoff_series tables, and re-generates the schedule for
    the new year. This is the dynasty-mode rollover.

    Body: {rng_seed: int (optional — defaults to a per-season hash)}

    Returns a summary including the org_strength deltas (so the UI can
    show "your org climbed from 64 to 71 after the championship run")
    and per-team development counts.
    """
    from o27v2.season_archive import (multi_season_status,
                                       archive_current_season,
                                       set_active_league_meta)
    from o27v2.development import run_offseason
    from o27v2.schedule import seed_schedule
    from o27v2.playoffs import champion as _champion, playoffs_initiated

    status = multi_season_status()
    if status.get("running"):
        return jsonify({"ok": False, "error": "multi-season runner is active"}), 409

    # Allow advance only after playoffs have crowned a champion.
    ch = _champion()
    if ch is None:
        return jsonify({"ok": False,
                        "error": "no champion yet — finish the playoffs first"}), 400

    data = request.get_json(silent=True) or {}
    rng_seed = data.get("rng_seed")

    # Read the current season number off sim_meta; bump it.
    cur_row = db.fetchone(
        "SELECT value FROM sim_meta WHERE key = 'season_number'")
    season_no = int((cur_row or {}).get("value") or 1)
    next_season = season_no + 1

    # Optionally archive the just-completed season.
    archived_id = None
    try:
        archived_id = archive_current_season(run_invariants=False)
    except Exception:
        pass

    # Run the off-season development + org-strength roll.
    summary = run_offseason(season=season_no, rng_seed=rng_seed or season_no * 17)

    # Youth league: run the season's tournament (group stage + knockout)
    # FIRST so the prospects play their tournament with this year's
    # rosters; THEN run aging + graduation. If the tournament was
    # already run mid-season via the manual button, run_youth_tournament
    # is a no-op.
    youth_tournament_summary = None
    youth_summary = None
    try:
        from o27v2 import youth
        youth_tournament_summary = youth.run_youth_tournament(
            rng_seed=(rng_seed or season_no * 17),
        )
        youth_summary = youth.advance_youth_year(
            rng_seed=(rng_seed or season_no * 17),
            new_season_year=next_season,
        )
    except Exception:
        # Either the youth tables don't exist (legacy save) or one of
        # the steps blew up. We deliberately swallow so the pro-side
        # rollover still completes.
        pass

    # Tiered configs: apply promotion/relegation BEFORE wiping wins/losses.
    # The function reads live standings off the teams table, so it must
    # run while the just-completed season's W-L is still there.
    # Auction (also tiered-only) runs immediately AFTER promotion/relegation
    # so the auction pool is sized against the final tier slots.
    pr_report = None
    auction_report = None
    cfg_active = _active_config()
    if cfg_active and cfg_active.get("schedule_mode") == "tiered":
        from o27v2 import promotion
        try:
            pr_report = promotion.apply_promotion_relegation(
                cfg_active, rng_seed=(rng_seed or season_no * 17),
                dry_run=False,
            )
        except Exception:
            pr_report = None
        if (cfg_active.get("auction") or {}).get("enabled", True):
            from o27v2 import auction as _auction
            try:
                auction_report = _auction.apply_auction(
                    cfg_active,
                    rng_seed=(rng_seed or season_no * 17),
                    season=season_no,
                )
            except Exception:
                auction_report = None

    # Reset W-L and wipe per-game tables; the league + roster stay.
    db.execute("UPDATE teams SET wins = 0, losses = 0")
    db.execute("DELETE FROM game_pa_log")
    db.execute("DELETE FROM game_pitcher_stats")
    db.execute("DELETE FROM game_batter_stats")
    db.execute("DELETE FROM team_phase_outs")
    db.execute("DELETE FROM games")
    db.execute("DELETE FROM playoff_series")
    db.execute("DELETE FROM season_awards")
    db.execute("DELETE FROM transactions")
    db.execute("DELETE FROM sim_meta WHERE key IN ('sim_date', 'last_match_day')")

    # Bump season counter.
    db.execute(
        "INSERT OR REPLACE INTO sim_meta (key, value) VALUES ('season_number', ?)",
        (str(next_season),),
    )

    # Re-seed the schedule for the new year. Re-use whatever config was
    # last used; bump season_year forward so the calendar lands in a
    # new year.
    cfg_row = db.fetchone("SELECT value FROM sim_meta WHERE key = 'league_config'")
    cfg_id  = (cfg_row or {}).get("value") or "30teams"
    seed_row = db.fetchone("SELECT value FROM sim_meta WHERE key = 'league_seed'")
    last_seed = int((seed_row or {}).get("value") or 42)
    new_seed = last_seed + 1   # rotate per season so weather / sim differ

    # Bump the season year on the config (only matters when a custom cfg
    # was supplied; presets rebuild from disk and roll their own year).
    seed_schedule(rng_seed=new_seed, config_id=cfg_id)
    set_active_league_meta(new_seed, cfg_id)

    return jsonify({
        "ok":              True,
        "from_season":     season_no,
        "to_season":       next_season,
        "archived_season": archived_id,
        "champion":        ch,
        "development":     summary,
        "promotion_relegation":   pr_report,
        "auction":                auction_report,
        "youth_league":           youth_summary,
        "youth_tournament":       youth_tournament_summary,
    })


@app.route("/api/season/reset", methods=["POST"])
def api_season_reset():
    """One-click 'New season' — optionally archive first, then drop+reseed.

    Body: {archive: bool, config_id: str, rng_seed: int}
    """
    from o27v2.league import seed_league
    from o27v2.schedule import seed_schedule
    from o27v2.season_archive import (archive_current_season, set_active_league_meta,
                                       multi_season_status)

    # Same drop-during-runner race as /new-league — refuse if the
    # multi-season runner is active.
    status = multi_season_status()
    if status.get("running"):
        return jsonify({
            "ok": False,
            "error": "multi-season test sim is running — wait for it to finish",
            "current_season_index": status.get("current_season_index"),
            "target_seasons": status.get("target_seasons"),
        }), 409

    data = request.get_json(silent=True) or {}
    new_config_id = (data.get("config_id") or "30teams").strip()
    new_rng_seed  = int(data.get("rng_seed", 42))
    do_archive    = bool(data.get("archive", True))

    if new_config_id not in get_league_configs():
        return jsonify({"ok": False, "error": f"unknown config: {new_config_id}"}), 400

    # Archive the *current* season FIRST, attributed to the seed/config that
    # actually produced it (read from sim_meta inside archive_current_season).
    # Do not pass the new seed/config — that would mislabel the archived row.
    archived_id = None
    if do_archive:
        try:
            archived_id = archive_current_season(run_invariants=True)
        except Exception as e:
            return jsonify({"ok": False, "error": f"archive failed: {e}"}), 500

    # Now drop + reseed for the new season, and record the new meta so the
    # *next* archive will be attributed correctly.
    db.drop_all()
    db.init_db()
    seed_league(rng_seed=new_rng_seed, config_id=new_config_id)
    seed_schedule(config_id=new_config_id, rng_seed=new_rng_seed)
    set_active_league_meta(new_rng_seed, new_config_id)
    resync_sim_clock()
    return jsonify({"ok": True, "archived_season_id": archived_id})


@app.route("/seasons")
def seasons_index():
    from o27v2.season_archive import compute_live_season
    rows = db.fetchall(
        "SELECT * FROM seasons ORDER BY season_number DESC"
    )
    live = compute_live_season()
    return _serve("seasons.html", seasons=rows, live=live)


@app.route("/seasons/<int:season_id>")
def season_detail(season_id: int):
    season = db.fetchone("SELECT * FROM seasons WHERE id = ?", (season_id,))
    if not season:
        abort(404)
    standings = db.fetchall(
        """SELECT * FROM season_standings
            WHERE season_id = ?
            ORDER BY league, division,
                     (wins * 1.0 / NULLIF(wins+losses,0)) DESC,
                     wins DESC""",
        (season_id,),
    )
    bat = db.fetchall(
        """SELECT * FROM season_batting_leaders
            WHERE season_id = ? ORDER BY category, rank""",
        (season_id,),
    )
    pit = db.fetchall(
        """SELECT * FROM season_pitching_leaders
            WHERE season_id = ? ORDER BY category, rank""",
        (season_id,),
    )
    bat_by_cat: dict[str, list[dict]] = {}
    for r in bat:
        bat_by_cat.setdefault(r["category"], []).append(r)
    pit_by_cat: dict[str, list[dict]] = {}
    for r in pit:
        pit_by_cat.setdefault(r["category"], []).append(r)

    # Group standings by league/division
    leagues: dict[str, dict[str, list[dict]]] = {}
    for r in standings:
        leagues.setdefault(r["league"] or "—", {}).setdefault(
            r["division"] or "—", []
        ).append(r)

    return _serve(
        "season_detail.html",
        season=season,
        leagues=leagues,
        batting=bat_by_cat,
        pitching=pit_by_cat,
    )


@app.route("/api/sim/<int:game_id>", methods=["POST"])
def api_sim_game(game_id: int):
    data = request.get_json(silent=True) or {}
    seed = data.get("seed")
    try:
        result = simulate_game(game_id, seed=seed)
        resync_sim_clock()
        return jsonify(result)
    except ValueError as e:
        # Expected: e.g. "Game already played" / "Game not found"
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        # Unexpected engine/DB failure. Log a full traceback server-side
        # so deployments surface the actual cause, and return enough info
        # for the schedule-page JS to show a useful error to the user.
        app.logger.exception("simulate_game(%s) failed", game_id)
        return jsonify({
            "error": f"{type(e).__name__}: {e}",
            "game_id": game_id,
        }), 500


@app.route("/youth")
def youth_view():
    from o27v2 import youth as _youth
    archetype = (request.args.get("archetype") or "bat").strip()
    if archetype not in ("bat", "arm", "stars"):
        archetype = "bat"
    teams_rows  = _youth.youth_teams()
    prospects   = _youth.top_prospects(limit=25, archetype=archetype)

    # Bucket teams by geographic region. Empty regions get dropped so a
    # config without African teams (say) doesn't render an empty header.
    by_region: dict[str, list[dict]] = {r: [] for r in _youth.REGION_ORDER}
    by_region["Other"] = []
    for t in teams_rows:
        by_region.setdefault(_youth.country_region(t.get("country_code", "")), []).append(t)
    region_groups = [(r, by_region[r]) for r in _youth.REGION_ORDER + ["Other"]
                     if by_region.get(r)]

    return _serve("youth.html",
                  teams=teams_rows,
                  region_groups=region_groups,
                  prospects=prospects,
                  archetype=archetype,
                  archetype_options=("bat", "arm", "stars"))


@app.route("/youth/team/<int:team_id>")
def youth_team_view(team_id: int):
    from o27v2 import youth as _youth
    team = db.fetchone(
        "SELECT * FROM youth_teams WHERE id = ?", (team_id,)
    )
    if not team:
        abort(404)
    roster = _youth.youth_roster(team_id)
    # Attach observed tournament stats per player so the page can show
    # stats without revealing ratings.
    enriched: list[dict] = []
    for p in roster:
        stats = _youth.player_observed_stats(p["id"])
        merged = dict(p)
        merged["bat_obs"] = stats.get("bat") or {}
        merged["pit_obs"] = stats.get("pit") or {}
        enriched.append(merged)
    return _serve("youth_team.html", team=dict(team), roster=enriched)


@app.route("/youth/player/<int:player_id>")
def youth_player_view(player_id: int):
    from o27v2 import youth as _youth
    player = db.fetchone(
        """SELECT p.*, t.id AS team_id, t.name AS team_name,
                  t.abbrev AS team_abbrev, t.country_code AS team_country
           FROM youth_players p
           JOIN youth_teams t ON t.id = p.youth_team_id
           WHERE p.id = ?""",
        (player_id,),
    )
    if not player:
        abort(404)

    obs = _youth.player_observed_stats(player_id)

    bat_log = db.fetchall(
        """SELECT gb.*, g.bracket_round, g.season,
                  ht.abbrev AS home_abbrev, at.abbrev AS away_abbrev,
                  g.home_team_id, g.away_team_id, g.home_score, g.away_score
           FROM game_youth_batter_stats gb
           JOIN youth_games g ON g.id = gb.game_id
           JOIN youth_teams ht ON ht.id = g.home_team_id
           JOIN youth_teams at ON at.id = g.away_team_id
           WHERE gb.player_id = ?
           ORDER BY g.id DESC""",
        (player_id,),
    )
    pit_log = db.fetchall(
        """SELECT gp.*, g.bracket_round, g.season,
                  ht.abbrev AS home_abbrev, at.abbrev AS away_abbrev,
                  g.home_team_id, g.away_team_id, g.home_score, g.away_score
           FROM game_youth_pitcher_stats gp
           JOIN youth_games g ON g.id = gp.game_id
           JOIN youth_teams ht ON ht.id = g.home_team_id
           JOIN youth_teams at ON at.id = g.away_team_id
           WHERE gp.player_id = ?
           ORDER BY g.id DESC""",
        (player_id,),
    )

    return _serve("youth_player.html",
                  player=dict(player),
                  bat_obs=obs.get("bat") or {},
                  pit_obs=obs.get("pit") or {},
                  bat_log=[dict(r) for r in bat_log],
                  pit_log=[dict(r) for r in pit_log],
                  region=_youth.country_region(player.get("team_country") or ""))


@app.route("/api/youth/seed", methods=["POST"])
def api_youth_seed():
    """Manually attach the youth league to an existing save (for users
    who created their league before the youth feature shipped, or who
    opted out at seed time and changed their mind)."""
    from o27v2 import youth as _youth
    data = request.get_json(silent=True) or {}
    rng_seed = int(data.get("rng_seed") or 0)
    n = _youth.seed_youth_league(rng_seed=rng_seed, seed_year=1)
    return jsonify({"ok": True, "teams_inserted": n})


@app.route("/youth/tournament")
def youth_tournament_view():
    from o27v2 import youth as _youth
    summary = _youth.get_tournament()
    return _serve("youth_tournament.html",
                  tournament=summary,
                  season=(summary or {}).get("season"))


@app.route("/youth/game/<int:game_id>")
def youth_game_view(game_id: int):
    from o27v2 import youth_sim
    from .box_score import render_box_score as _render_box_score
    box = youth_sim.get_box_score(game_id)
    if not box:
        abort(404)
    g = box["game"]
    away_id, home_id = g["away_team_id"], g["home_team_id"]
    away_batting = [dict(r) for r in box["batters"]  if r["team_id"] == away_id]
    home_batting = [dict(r) for r in box["batters"]  if r["team_id"] == home_id]
    away_pitching = [dict(r) for r in box["pitchers"] if r["team_id"] == away_id]
    home_pitching = [dict(r) for r in box["pitchers"] if r["team_id"] == home_id]
    # Youth schema lacks the optional pro fields the renderer reads; the
    # renderer falls back to 0/blank via .get() so we only need to plug
    # the defaults that affect layout (position label, season HR caption).
    for r in away_batting + home_batting:
        r.setdefault("entry_type", "starter")
        r.setdefault("box_position", r.get("position") or "")
        r["season_hr"] = r.get("hr") or 0
    line_for = lambda rows: {
        "runs":    {0: sum((r.get("runs") or 0) for r in rows)},
        "hits":    {0: sum((r.get("hits") or 0) for r in rows)},
        "errors":  {0: 0},
        "total_r": sum((r.get("runs") or 0) for r in rows),
        "total_h": sum((r.get("hits") or 0) for r in rows),
        "total_e": 0,
    }
    away_line = line_for(away_batting)
    home_line = line_for(home_batting)
    decisions: dict[int, str] = {}
    winner_id = g.get("winner_id")
    if winner_id is not None:
        win_pitchers  = away_pitching if winner_id == away_id else home_pitching
        lose_pitchers = home_pitching if winner_id == away_id else away_pitching
        if win_pitchers:
            decisions[win_pitchers[0]["player_id"]] = "W"
        if lose_pitchers:
            decisions[lose_pitchers[0]["player_id"]] = "L"
    box_score_text = _render_box_score(
        game=g,
        phases=[0],
        away_line=away_line,
        home_line=home_line,
        away_batting=away_batting,
        home_batting=home_batting,
        away_pitching=away_pitching,
        home_pitching=home_pitching,
        decisions=decisions,
    )
    return _serve("youth_box_score.html", box=box, box_score_text=box_score_text)


@app.route("/api/youth/tournament/run", methods=["POST"])
def api_youth_tournament_run():
    """Run (or re-run via reset=true) the youth tournament for the
    current season."""
    from o27v2 import youth as _youth
    data = request.get_json(silent=True) or {}
    reset    = bool(data.get("reset"))
    rng_seed = int(data.get("rng_seed") or 0)
    if reset:
        _youth.reset_youth_tournament()
    summary = _youth.run_youth_tournament(rng_seed=rng_seed)
    return jsonify({"ok": True, "tournament": summary})


@app.route("/auction")
def auction_view():
    from o27v2 import auction as _auction
    summary = _auction.get_auction()
    cfg = _active_config()
    is_tiered = bool(cfg and cfg.get("schedule_mode") == "tiered")
    return _serve("auction.html",
                  auction=summary,
                  is_tiered=is_tiered,
                  config_summary=(cfg.get("auction") if cfg else None))


@app.route("/auction/live")
def auction_live_view():
    from o27v2 import auction as _auction
    feed = _auction.get_live_auction()
    cfg = _active_config()
    is_tiered = bool(cfg and cfg.get("schedule_mode") == "tiered")
    return _serve("auction_live.html",
                  feed=feed,
                  is_tiered=is_tiered)


@app.route("/api/auction/live")
def api_auction_live():
    from o27v2 import auction as _auction
    feed = _auction.get_live_auction()
    if feed is None:
        return jsonify({"ok": False, "error": "No auction has been run yet."}), 404
    return jsonify({"ok": True, "feed": feed})


@app.route("/api/auction/run", methods=["POST"])
def api_auction_run():
    """Run the Vickrey auction against the current league state. Tiered
    configs only; refuses on non-tiered."""
    from o27v2 import auction as _auction
    cfg = _active_config()
    if not cfg or cfg.get("schedule_mode") != "tiered":
        return jsonify({
            "ok": False,
            "error": "Auction is only available for tiered configs.",
        }), 400
    data = request.get_json(silent=True) or {}
    rng_seed = int(data.get("rng_seed") or 0)
    try:
        report = _auction.apply_auction(cfg, rng_seed=rng_seed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "auction": report})


@app.route("/api/league-configs")
def api_league_configs():
    return jsonify(list(get_league_configs().values()))


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})
