"""
o27/data.py — Shared data access layer.

All query helpers used by the web UI and (later) the stats site.
Owns the in-memory game store, team loader, and aggregation helpers.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from typing import Optional

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_TEAMS_DB_PATH = os.path.join(_root, "o27v2", "data", "teams_database.json")
_NAMES_DIR     = os.path.join(_root, "o27v2", "data", "names")

_REGION_WEIGHTS = [("usa", 0.50), ("latin", 0.30), ("japan_korea", 0.10), ("other", 0.10)]
_POSITIONS      = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C", "P"]

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

_GAMES: dict[str, dict] = {}        # game_id -> full game result dict
_RECENT: list[str]      = []        # ordered list of game_ids (newest last)
_MAX_RECENT             = 20

_teams_cache:      list[dict] | None = None
_name_pools_cache: dict       | None = None


# ---------------------------------------------------------------------------
# Name / roster generation helpers
# ---------------------------------------------------------------------------

def _load_name_pools() -> dict:
    pools: dict = {}
    for region in ("usa", "latin", "japan_korea", "other"):
        p = os.path.join(_NAMES_DIR, f"{region}.json")
        if os.path.exists(p):
            with open(p) as fh:
                pools[region] = json.load(fh)
    return pools


def _name_pools() -> dict:
    global _name_pools_cache
    if _name_pools_cache is None:
        _name_pools_cache = _load_name_pools()
    return _name_pools_cache


def _weighted_region(rng: random.Random) -> str:
    r = rng.random()
    cum = 0.0
    for region, w in _REGION_WEIGHTS:
        cum += w
        if r < cum:
            return region
    return "usa"


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _generate_roster(team_seed: int) -> list[dict]:
    rng = random.Random(team_seed)
    pools = _name_pools()
    used: set[str] = set()

    def _name() -> str:
        for _ in range(200):
            region = _weighted_region(rng)
            pool = pools.get(region, {})
            firsts = pool.get("first_names", ["J."])
            lasts  = pool.get("last_names",  ["Smith"])
            n = f"{rng.choice(firsts)} {rng.choice(lasts)}"
            if n not in used:
                used.add(n)
                return n
        return f"Player {rng.randint(100, 999)}"

    profile     = team_seed % 5
    skill_base  = [0.52, 0.50, 0.54, 0.48, 0.51][profile]
    speed_base  = [0.52, 0.60, 0.48, 0.55, 0.50][profile]
    pitcher_base= [0.54, 0.48, 0.52, 0.56, 0.50][profile]

    players = []
    for pos in _POSITIONS:
        is_p   = pos == "P"
        skill  = _clamp(rng.gauss(skill_base,   0.10))
        speed  = _clamp(rng.gauss(speed_base,   0.12))
        pskill = _clamp(rng.gauss(pitcher_base, 0.12)) if is_p else _clamp(rng.gauss(0.35, 0.08))
        stay_a = _clamp(rng.gauss(0.40, 0.12))
        cqt    = _clamp(rng.gauss(0.45, 0.08))
        players.append({
            "name": _name(), "position": pos,
            "is_pitcher": is_p, "is_joker": False,
            "joker_archetype": "",
            "skill": round(skill, 3), "speed": round(speed, 3),
            "pitcher_skill": round(pskill, 3),
            "stay_aggressiveness": round(stay_a, 3),
            "contact_quality_threshold": round(cqt, 3),
        })

    joker_archetypes = [
        {"label": "Power",   "skill_mu": 0.68, "speed_mu": 0.42, "stay_mu": 0.25},
        {"label": "Speed",   "skill_mu": 0.62, "speed_mu": 0.78, "stay_mu": 0.55},
        {"label": "Contact", "skill_mu": 0.65, "speed_mu": 0.58, "stay_mu": 0.65},
    ]
    for arch in joker_archetypes:
        skill  = _clamp(rng.gauss(arch["skill_mu"], 0.07))
        speed  = _clamp(rng.gauss(arch["speed_mu"], 0.08))
        stay_a = _clamp(rng.gauss(arch["stay_mu"],  0.08))
        cqt    = _clamp(rng.gauss(0.40, 0.07))
        players.append({
            "name": _name(),
            "position": f"JKR-{arch['label'][:3]}",
            "is_pitcher": False, "is_joker": True,
            "joker_archetype": arch["label"],
            "skill": round(skill, 3), "speed": round(speed, 3),
            "pitcher_skill": round(rng.gauss(0.38, 0.08), 3),
            "stay_aggressiveness": round(stay_a, 3),
            "contact_quality_threshold": round(cqt, 3),
        })
    return players


# ---------------------------------------------------------------------------
# Team loading
# ---------------------------------------------------------------------------

def load_teams() -> list[dict]:
    """Load all teams with generated rosters. Cached after first call."""
    global _teams_cache
    if _teams_cache is not None:
        return _teams_cache

    if not os.path.exists(_TEAMS_DB_PATH):
        from o27.main import make_foxes, make_bears
        foxes = make_foxes()
        bears = make_bears()
        _teams_cache = [
            _engine_team_to_dict(foxes, "FOX", "", "Classic"),
            _engine_team_to_dict(bears, "BEA", "", "Classic"),
        ]
        return _teams_cache

    with open(_TEAMS_DB_PATH) as fh:
        raw = json.load(fh)

    teams = []
    for i, t in enumerate(raw):
        abbrev    = t.get("abbreviation") or t.get("abbrev", f"T{i:02d}")
        key       = (abbrev + t.get("name", "")).encode()
        team_seed = int(hashlib.sha256(key).hexdigest(), 16) & 0xFFFFFFFF
        teams.append({
            "abbrev":  abbrev,
            "name":    t.get("name", abbrev),
            "city":    t.get("city", ""),
            "level":   t.get("level", "MLB"),
            "display": f"{t.get('city', '')} {t.get('name', abbrev)}".strip(),
            "players": _generate_roster(team_seed),
        })

    _teams_cache = teams
    return teams


def _engine_team_to_dict(team, abbrev: str, city: str = "", level: str = "") -> dict:
    pos_labels = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C", "P"]
    players = []
    pos_idx = 0
    for p in team.roster:
        pos = pos_labels[pos_idx] if not p.is_joker and pos_idx < len(pos_labels) else ""
        if not p.is_joker:
            pos_idx += 1
        archetype = ""
        if p.is_joker:
            archetype = "Power" if p.speed < 0.50 else ("Speed" if p.speed > 0.65 else "Contact")
            pos = f"JKR-{archetype[:3]}"
        elif p.is_pitcher:
            pos = "P"
        players.append({
            "name": p.name, "position": pos,
            "is_pitcher": p.is_pitcher, "is_joker": p.is_joker,
            "joker_archetype": archetype,
            "skill": round(p.skill, 3), "speed": round(p.speed, 3),
            "pitcher_skill": round(p.pitcher_skill, 3),
            "stay_aggressiveness": round(p.stay_aggressiveness, 3),
            "contact_quality_threshold": round(p.contact_quality_threshold, 3),
        })
    full_name = f"{city} {team.name}".strip() if city else team.name
    return {
        "abbrev": abbrev, "name": team.name, "city": city,
        "level": level or "Classic", "display": full_name, "players": players,
    }


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_team(abbrev: str) -> Optional[dict]:
    """Return team dict by abbreviation, or None."""
    for t in load_teams():
        if t["abbrev"] == abbrev:
            return t
    return None


def get_player(player_id: str) -> Optional[dict]:
    """Return (team_dict, player_dict) tuple by searching all rosters."""
    for t in load_teams():
        for p in t["players"]:
            pid = f"{t['abbrev']}_{p['name'].replace(' ', '_')}"
            if pid == player_id or p["name"] == player_id:
                return {"team": t, "player": p, "player_id": pid}
    return None


def get_standings() -> list[dict]:
    """Compute win/loss standings from stored recent games, grouped by level."""
    record: dict[str, dict] = {}
    for gid in _RECENT:
        g = _GAMES.get(gid)
        if not g:
            continue
        for side in ("visitors", "home"):
            abbrev = g[f"{side}_abbrev"]
            if abbrev not in record:
                t = get_team(abbrev)
                record[abbrev] = {
                    "abbrev": abbrev,
                    "name": t["display"] if t else abbrev,
                    "level": t["level"] if t else "—",
                    "w": 0, "l": 0, "r_for": 0, "r_against": 0,
                }
            rec = record[abbrev]
            if side == "visitors":
                rec["r_for"]     += g["v_score"]
                rec["r_against"] += g["h_score"]
                if g["winner_id"] == "visitors":
                    rec["w"] += 1
                else:
                    rec["l"] += 1
            else:
                rec["r_for"]     += g["h_score"]
                rec["r_against"] += g["v_score"]
                if g["winner_id"] == "home":
                    rec["w"] += 1
                else:
                    rec["l"] += 1

    rows = list(record.values())
    for r in rows:
        gp = r["w"] + r["l"]
        r["gp"] = gp
        r["pct"] = r["w"] / gp if gp else 0.0
        r["rd"] = r["r_for"] - r["r_against"]
    rows.sort(key=lambda x: (-x["pct"], -x["rd"]))
    return rows


def get_schedule(limit: int = 20) -> list[dict]:
    """Return recent games, newest first."""
    recent = list(reversed(_RECENT[-limit:]))
    return [_GAMES[gid] for gid in recent if gid in _GAMES]


def get_game(game_id: str) -> Optional[dict]:
    """Return full game result dict by game_id."""
    return _GAMES.get(game_id)


def get_leaders(stat: str = "hits", limit: int = 10) -> list[dict]:
    """
    Aggregate batting stats across all stored games.
    stat: one of 'hits','hr','rbi','sty','bb','k','avg'
    """
    agg: dict[str, dict] = {}  # player_name_team -> dict
    for gid in _RECENT:
        g = _GAMES.get(gid)
        if not g:
            continue
        for side in ("v", "h"):
            batting = g.get(f"{side}_batting", [])
            abbrev  = g.get("visitors_abbrev" if side == "v" else "home_abbrev", "")
            for row in batting:
                key = f"{row['name']}|{abbrev}"
                if key not in agg:
                    agg[key] = {
                        "name": row["name"], "team": abbrev,
                        "pa": 0, "ab": 0, "hits": 0, "hr": 0,
                        "rbi": 0, "bb": 0, "k": 0, "sty": 0, "runs": 0,
                    }
                r = agg[key]
                r["pa"]   += row.get("pa", 0)
                r["ab"]   += row.get("ab", 0)
                r["hits"] += row.get("hits", 0)
                r["hr"]   += row.get("hr", 0)
                r["rbi"]  += row.get("rbi", 0)
                r["bb"]   += row.get("bb", 0)
                r["k"]    += row.get("k", 0)
                r["sty"]  += row.get("sty", 0)
                r["runs"] += row.get("runs", 0)

    for r in agg.values():
        r["avg"] = r["hits"] / r["ab"] if r["ab"] > 0 else 0.0

    rows = list(agg.values())
    if stat == "avg":
        rows = [r for r in rows if r["ab"] >= 3]
        rows.sort(key=lambda x: -x["avg"])
    else:
        rows.sort(key=lambda x: -x.get(stat, 0))
    return rows[:limit]


# ---------------------------------------------------------------------------
# Game storage (called by app.py after each sim)
# ---------------------------------------------------------------------------

def store_game(game_id: str, result: dict) -> None:
    """Store a completed game result. Trims _RECENT to _MAX_RECENT."""
    _GAMES[game_id] = result
    if game_id in _RECENT:
        _RECENT.remove(game_id)
    _RECENT.append(game_id)
    # evict oldest beyond cap
    while len(_RECENT) > _MAX_RECENT:
        old = _RECENT.pop(0)
        _GAMES.pop(old, None)


def make_game_id(seed: int, v_abbrev: str, h_abbrev: str) -> str:
    return f"{seed}_{v_abbrev}_{h_abbrev}"
