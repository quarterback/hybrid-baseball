"""Per-nation talent-generation metrics — now with a live, drifting store.

Two 0-100 ratings per country (default 50 = league-neutral):
  * investment — top-end funding (academies, pro pathways). Drives the
    elite-talent spike: the chance a generated player is world-class.
  * grassroots — development breadth. Drives the average-quality lift
    applied to every player from that nation.

Effects at player generation (see `league._make_hitter` / `_make_pitcher`):
  * Elite spike: `elite_probability` scales from 1/1000 at the low end to
    1/100 at the top, HARD-CAPPED at 1/100. When a player's elite roll
    hits, the maker floors their marquee grades into the world-class band.
  * Average lift: `talent_shift` is an additive scout-grade shift in
    [-LIFT_CAP, +LIFT_CAP] applied to every tier roll. Neutral (50) = 0,
    so a default nation reproduces the league's prior behaviour exactly.

Investment leans on the elite spike (stars); grassroots leans on the lift
(depth). The dice still roll exactly as before — these ratings only nudge
the inputs.

Persistence & drift
-------------------
data/nation_talent.json is the *seed*. The live ratings live in a SQLite
table (`nation_talent`) so they can ebb and flow across seasons:
  * On first DB access the table is created and seeded from the JSON.
  * `drift_from_worldcup(season)` nudges every nation after each World Cup:
    deep tournament runs raise *investment* (federations fund a winning
    programme), simply qualifying/participating grows *grassroots*, and a
    gentle mean-reversion pulls everyone back toward 50 so dynasties
    plateau and dormant programmes decay.
  * `set_rating()` lets the UI edit a nation's ratings directly.

Everything degrades gracefully: with no usable DB, reads fall back to the
JSON seed, then to neutral — so pure-unit player generation never needs a
database.
"""
from __future__ import annotations

import json
import os

_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "nation_talent.json")

NEUTRAL = 50

# Elite spike: probability a generated player is world-class, as a function
# of the (investment-weighted) talent index. 1/1000 floor, 1/100 ceiling.
ELITE_MIN_P = 0.001     # 1 in 1000 — the weakest programmes
ELITE_MAX_P = 0.010     # 1 in 100  — never higher, by design

# Average lift: a tier-roll shift of (index-50) * LIFT_K, clamped to ±LIFT_CAP.
LIFT_K   = 0.16         # ≈ ±8 grade points across the 0-100 range
LIFT_CAP = 8

# When the elite roll hits, marquee grades are floored into these bands so
# the player reads as genuinely world-class at seed time. Elite+ (81-95)
# stays earned via development — the seed ceiling is still 80.
ELITE_HEADLINE = (74, 80)   # primary rating (skill / pitcher_skill)
ELITE_SUPPORT  = (68, 80)   # supporting ratings

# --- Season-to-season drift -------------------------------------------------
# Per-World-Cup nudges keyed by how far a nation advanced. Knockout success
# funds investment (stars); breadth of participation grows grassroots.
_STAGE_RANK = {"group": 1, "r16": 2, "qf": 3, "sf": 4, "final": 5}
_CHAMPION_RANK = 6
#                     champ  final   sf    qf   r16  group  entered-only
INV_DELTA   = {6: 4, 5: 2, 4: 1, 3: 1, 2: 0, 1: 0, 0: -1}
GRASS_DELTA = {6: 2, 5: 1, 4: 1, 3: 1, 2: 1, 1: 1, 0:  0}
MEAN_REVERT = 0.05      # fraction of the gap to 50 closed each season
RATING_MIN, RATING_MAX = 1, 100

_cache: dict[str, tuple[int, int]] | None = None


# ---------------------------------------------------------------------------
# JSON seed
# ---------------------------------------------------------------------------
def _load_json() -> dict[str, tuple[int, int]]:
    try:
        with open(_DATA_PATH, encoding="utf-8") as fh:
            raw = json.load(fh).get("ratings", {}) or {}
    except (OSError, ValueError):
        return {}
    out: dict[str, tuple[int, int]] = {}
    for cc, row in raw.items():
        out[cc.upper()] = (int(row.get("investment", NEUTRAL)),
                           int(row.get("grassroots", NEUTRAL)))
    return out


# ---------------------------------------------------------------------------
# Live DB store (self-bootstrapping; degrades to JSON when no DB is present)
# ---------------------------------------------------------------------------
def _db():
    try:
        from o27v2 import db as _db_mod
        return _db_mod
    except Exception:
        return None


def _ensure_table(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS nation_talent ("
        "  country_code   TEXT PRIMARY KEY,"
        "  investment     INTEGER NOT NULL,"
        "  grassroots     INTEGER NOT NULL,"
        "  updated_season INTEGER DEFAULT 0"
        ")"
    )


def _db_ratings() -> dict[str, tuple[int, int]] | None:
    """Live ratings from the DB, seeding the table from JSON on first use.
    Returns None if no DB is usable (callers then fall back to JSON)."""
    db = _db()
    if db is None:
        return None
    try:
        _ensure_table(db)
        rows = db.fetchall("SELECT country_code, investment, grassroots "
                           "FROM nation_talent")
        if not rows:
            for cc, (inv, grass) in _load_json().items():
                db.execute(
                    "INSERT OR IGNORE INTO nation_talent"
                    " (country_code, investment, grassroots, updated_season)"
                    " VALUES (?, ?, ?, 0)", (cc, inv, grass))
            rows = db.fetchall("SELECT country_code, investment, grassroots "
                               "FROM nation_talent")
        return {str(r["country_code"]).upper():
                (int(r["investment"]), int(r["grassroots"])) for r in rows}
    except Exception:
        return None


def _load() -> dict[str, tuple[int, int]]:
    """Effective ratings = JSON seed overlaid by the live DB store."""
    global _cache
    if _cache is None:
        eff = _load_json()
        live = _db_ratings()
        if live:
            eff.update(live)
        _cache = eff
    return _cache


def reset_cache() -> None:
    """Drop the cached ratings (call after editing the store)."""
    global _cache
    _cache = None


# ---------------------------------------------------------------------------
# Public reads
# ---------------------------------------------------------------------------
def ratings(country_code: str) -> tuple[int, int]:
    """(investment, grassroots) for a country, defaulting to neutral."""
    return _load().get((country_code or "").upper(), (NEUTRAL, NEUTRAL))


def all_ratings() -> dict[str, tuple[int, int]]:
    """Effective ratings for every nation currently on record."""
    return dict(_load())


def _elite_index(country_code: str) -> float:
    inv, grass = ratings(country_code)
    return 0.7 * inv + 0.3 * grass


def _lift_index(country_code: str) -> float:
    inv, grass = ratings(country_code)
    return 0.4 * inv + 0.6 * grass


def elite_probability(country_code: str) -> float:
    """Chance a single generated player from this nation is world-class."""
    idx = _elite_index(country_code)
    p = ELITE_MIN_P + (idx / 100.0) * (ELITE_MAX_P - ELITE_MIN_P)
    return max(ELITE_MIN_P, min(ELITE_MAX_P, p))


def talent_shift(country_code: str) -> int:
    """Additive scout-grade shift applied to every tier roll for this
    nation's players. 0 for a neutral (50/50) nation."""
    shift = round((_lift_index(country_code) - NEUTRAL) * LIFT_K)
    return max(-LIFT_CAP, min(LIFT_CAP, shift))


def roll_elite(country_code: str, rng) -> bool:
    """True if a freshly generated player from this nation rolls elite."""
    return rng.random() < elite_probability(country_code)


def describe(country_code: str) -> dict:
    """Display bundle for a nation: ratings + derived generation effects."""
    inv, grass = ratings(country_code)
    p = elite_probability(country_code)
    return {
        "country_code": (country_code or "").upper(),
        "investment":   inv,
        "grassroots":   grass,
        "elite_one_in": int(round(1.0 / p)) if p > 0 else 0,
        "talent_shift": talent_shift(country_code),
    }


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def _clamp_rating(v: float) -> int:
    return max(RATING_MIN, min(RATING_MAX, int(round(v))))


def set_rating(country_code: str, investment: int, grassroots: int,
               season: int = 0) -> None:
    """Persist a nation's ratings (UI edits + drift both flow through here)."""
    cc = (country_code or "").upper()
    if not cc:
        return
    inv, grass = _clamp_rating(investment), _clamp_rating(grassroots)
    db = _db()
    if db is not None:
        try:
            _ensure_table(db)
            db.execute(
                "INSERT INTO nation_talent"
                " (country_code, investment, grassroots, updated_season)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(country_code) DO UPDATE SET"
                "   investment=excluded.investment,"
                "   grassroots=excluded.grassroots,"
                "   updated_season=excluded.updated_season",
                (cc, inv, grass, int(season)))
        except Exception:
            pass
    reset_cache()


# ---------------------------------------------------------------------------
# Season-to-season drift from World Cup results
# ---------------------------------------------------------------------------
def _reached_by_country(db, season: int) -> dict[str, int]:
    """Map country_code -> furthest stage rank reached in `season`'s WC.
    Entered-but-eliminated-in-qualifying = 0; group = 1 … champion = 6."""
    teams = db.fetchall(
        "SELECT id, country_code, final_position FROM wc_teams WHERE season = ?",
        (season,))
    id2cc = {t["id"]: str(t["country_code"]).upper() for t in teams}
    reached = {cc: 0 for cc in id2cc.values()}
    games = db.fetchall(
        "SELECT home_wc_team_id, away_wc_team_id, phase FROM wc_games"
        " WHERE season = ? AND phase IN ('group','r16','qf','sf','final')",
        (season,))
    for g in games:
        rank = _STAGE_RANK.get(g["phase"], 0)
        for side in ("home_wc_team_id", "away_wc_team_id"):
            cc = id2cc.get(g[side])
            if cc and rank > reached[cc]:
                reached[cc] = rank
    for t in teams:
        if t["final_position"] == "champion":
            reached[str(t["country_code"]).upper()] = _CHAMPION_RANK
    return reached


def drift_from_worldcup(season: int) -> dict[str, tuple[int, int]]:
    """Nudge every nation's ratings on the back of `season`'s World Cup.

    Idempotent per season (guarded via `updated_season`). Returns the new
    ratings for every nation that moved.
    """
    db = _db()
    if db is None:
        return {}
    try:
        _ensure_table(db)
        # Idempotency guard: skip if we already drifted for this season.
        if db.fetchone("SELECT 1 FROM nation_talent WHERE updated_season = ?"
                       " LIMIT 1", (season,)):
            return {}
        reached = _reached_by_country(db, season)
    except Exception:
        return {}

    # Make sure the JSON-seeded ratings are loaded so they mean-revert too.
    _load()
    affected = set(all_ratings().keys()) | set(reached.keys())
    changed: dict[str, tuple[int, int]] = {}
    for cc in affected:
        inv, grass = ratings(cc)
        new_inv   = inv   + MEAN_REVERT * (NEUTRAL - inv)
        new_grass = grass + MEAN_REVERT * (NEUTRAL - grass)
        r = reached.get(cc)
        if r is not None:
            new_inv   += INV_DELTA[r]
            new_grass += GRASS_DELTA[r]
        ci, cg = _clamp_rating(new_inv), _clamp_rating(new_grass)
        if (ci, cg) != (inv, grass) or r is not None:
            set_rating(cc, ci, cg, season)
            changed[cc] = (ci, cg)
    return changed
