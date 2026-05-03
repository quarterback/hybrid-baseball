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
_RECENT: list[str]      = []        # ordered list of game_ids (oldest first / newest last)
_MAX_RECENT             = 200


# ---------------------------------------------------------------------------
# o27v2 bridge
#
# When the o27v2 SQLite DB is populated (i.e. the v2 league simulator is the
# primary game source), all stats-site queries below transparently delegate to
# o27/v2_bridge.py so /stats/standings, /stats/schedule, /stats/leaders,
# /stats/game/<id>, /stats/team/<abbrev>, /stats/player/<id> all reflect live
# v2 league data. The in-memory _RECENT/_GAMES paths remain the fallback for
# the legacy operational GUI when v2 is not active.
# ---------------------------------------------------------------------------

def _v2():
    """Lazy import to avoid a circular dependency at module load time."""
    from o27 import v2_bridge
    return v2_bridge


def _v2_active() -> bool:
    try:
        return _v2().is_active()
    except Exception:
        return False

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
    if _v2_active():
        return _v2().load_teams()
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
    if _v2_active():
        return _v2().get_team(abbrev)
    for t in load_teams():
        if t["abbrev"] == abbrev:
            return t
    return None


def player_id(team_abbrev: str, player_name: str) -> str:
    """Canonical player ID: TEAM_Name_With_Underscores (dots removed)."""
    return f"{team_abbrev}_{player_name.replace(' ', '_').replace('.', '')}"


def get_player(pid: str) -> Optional[dict]:
    """Look up a player by canonical player_id (e.g. 'NYY_Christopher_Almora')."""
    if _v2_active():
        return _v2().get_player(pid)
    for t in load_teams():
        for p in t["players"]:
            if player_id(t["abbrev"], p["name"]) == pid:
                return {"team": t, "player": p, "player_id": player_id(t["abbrev"], p["name"])}
    return None


def get_player_by_team_slug(team_abbrev: str, slug: str) -> Optional[dict]:
    """Look up player by team abbrev + lowercase slug (backward-compat alias)."""
    if _v2_active():
        return _v2().get_player_by_team_slug(team_abbrev, slug)
    team = get_team(team_abbrev)
    if not team:
        return None
    for p in team["players"]:
        p_slug = p["name"].lower().replace(" ", "_").replace(".", "")
        if p_slug == slug:
            pid = player_id(team_abbrev, p["name"])
            return {"team": team, "player": p, "player_id": pid}
    return None


def get_standings() -> list[dict]:
    """Compute win/loss standings with GB, L10, Streak."""
    if _v2_active():
        return _v2().get_standings()
    record: dict[str, dict] = {}
    team_hist: dict[str, list[bool]] = {}  # True=W, False=L, oldest→newest

    for gid in _RECENT:
        g = _GAMES.get(gid)
        if not g:
            continue
        v_won = g["winner_id"] == "visitors"
        for side, abbrev, won, rf, ra in [
            ("v", g["visitors_abbrev"], v_won,     g["v_score"], g["h_score"]),
            ("h", g["home_abbrev"],     not v_won,  g["h_score"], g["v_score"]),
        ]:
            if abbrev not in record:
                t = get_team(abbrev)
                record[abbrev] = {
                    "abbrev": abbrev,
                    "name":   t["display"] if t else abbrev,
                    "level":  t["level"]   if t else "—",
                    "w": 0, "l": 0, "r_for": 0, "r_against": 0,
                }
                team_hist[abbrev] = []
            rec = record[abbrev]
            rec["r_for"]     += rf
            rec["r_against"] += ra
            if won:
                rec["w"] += 1
            else:
                rec["l"] += 1
            team_hist[abbrev].append(won)

    rows = list(record.values())
    for r in rows:
        gp  = r["w"] + r["l"]
        r["gp"]  = gp
        r["pct"] = r["w"] / gp if gp else 0.0
        r["rd"]  = r["r_for"] - r["r_against"]
        r["rpg"]  = (r["r_for"]     / gp) if gp else 0.0
        r["rapg"] = (r["r_against"] / gp) if gp else 0.0
        hist     = team_hist.get(r["abbrev"], [])
        last10       = hist[-10:]
        r["l10_w"]   = sum(1 for x in last10 if x)
        r["l10_l"]   = len(last10) - r["l10_w"]
        streak_n = 0
        if hist:
            cur = hist[-1]
            for result in reversed(hist):
                if result == cur:
                    streak_n += 1
                else:
                    break
        r["streak"] = (f"W{streak_n}" if (hist and hist[-1]) else f"L{streak_n}") if streak_n > 0 else "—"

    rows.sort(key=lambda x: (-x["pct"], -x["rd"]))

    if rows:
        lw = rows[0]["w"]
        ll = rows[0]["l"]
        for r in rows:
            gb = ((lw - r["w"]) + (r["l"] - ll)) / 2
            r["gb"] = "—" if gb <= 0 else (str(int(gb)) if gb == int(gb) else f"{gb:.1f}")

    return rows


def get_schedule(limit: int = 40, team: str = "") -> list[dict]:
    """Return recent games newest-first, optionally filtered by team abbrev."""
    if _v2_active():
        return _v2().get_schedule(limit, team)
    games = [_GAMES[gid] for gid in reversed(_RECENT) if gid in _GAMES]
    if team:
        games = [g for g in games
                 if g.get("visitors_abbrev") == team or g.get("home_abbrev") == team]
    return games[:limit]


def get_upcoming(n: int = 3) -> list[dict]:
    """Return n suggested upcoming matchups."""
    if _v2_active():
        return _v2().get_upcoming(n)
    teams = load_teams()
    if len(teams) < 2:
        return []
    rng = random.Random(len(_RECENT) // 3)
    sampled = list(teams)
    rng.shuffle(sampled)
    pairs: list[dict] = []
    for i in range(0, len(sampled) - 1, 2):
        pairs.append({
            "visitors_abbrev": sampled[i]["abbrev"],
            "visitors_name":   sampled[i]["display"],
            "home_abbrev":     sampled[i + 1]["abbrev"],
            "home_name":       sampled[i + 1]["display"],
        })
        if len(pairs) >= n:
            break
    return pairs[:n]


def get_game(game_id: str) -> Optional[dict]:
    if _v2_active():
        return _v2().get_game(game_id)
    return _GAMES.get(game_id)


def get_leaders(stat: str = "hits", limit: int = 10) -> list[dict]:
    """Aggregate batting stats across all stored games."""
    if _v2_active():
        return _v2().get_leaders(stat, limit)
    agg: dict[str, dict] = {}
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
                        "rbi": 0, "bb": 0, "k": 0, "or_": 0, "runs": 0,
                    }
                r = agg[key]
                for f in ("pa", "ab", "hits", "hr", "rbi", "bb", "k", "runs"):
                    r[f] += row.get(f, 0)
                r["or_"] += row.get("or_", 0)

    for r in agg.values():
        r["avg"]  = r["hits"] / r["ab"] if r["ab"] > 0 else 0.0
        r["h_ab"] = r["hits"] / r["ab"] if r["ab"] > 0 else 0.0

    rows = list(agg.values())
    if stat == "avg":
        rows = [r for r in rows if r["ab"] >= 3]
        rows.sort(key=lambda x: -x["avg"])
    elif stat == "h_ab":
        rows = [r for r in rows if r["ab"] >= 3]
        rows.sort(key=lambda x: -x["h_ab"])
    else:
        rows.sort(key=lambda x: -x.get(stat, 0))
    return rows[:limit]


def get_pitching_leaders(stat: str = "k", limit: int = 10) -> list[dict]:
    """Aggregate pitching stats across all stored games.
    stat: 'k' (strikeouts), 'outs' (outs share), 'era' (computed)
    """
    if _v2_active():
        return _v2().get_pitching_leaders(stat, limit)
    agg: dict[str, dict] = {}  # 'name|team' → dict

    for gid in _RECENT:
        g = _GAMES.get(gid)
        if not g:
            continue
        for side in ("v", "h"):
            pitching = g.get(f"{side}_pitching", [])
            abbrev   = g.get("visitors_abbrev" if side == "v" else "home_abbrev", "")
            for row in pitching:
                key = f"{row['name']}|{abbrev}"
                if key not in agg:
                    agg[key] = {
                        "name": row["name"], "team": abbrev,
                        "g": 0, "bf": 0, "outs": 0,
                        "h": 0, "r": 0, "er": 0, "bb": 0, "k": 0,
                        "hr": 0, "p": 0, "out_sum": 0,
                    }
                r = agg[key]
                r["g"] += 1
                for f in ("bf", "outs", "h", "r", "bb", "k", "hr", "p"):
                    r[f] += row.get(f, 0)
                r["er"] += row.get("er", row.get("r", 0))
                r["out_sum"] += row.get("out", 0)

    for r in agg.values():
        outs = r["outs"]
        g    = max(r["g"], 1)
        r["os_pct"] = f"{round(outs / (g * 27) * 100)}%" if outs > 0 else "0%"
        r["era"]    = round(r["r"] / outs * 27, 2) if outs > 0 else 99.99
        r["whip"]   = round((r["bb"] + r["h"]) / outs * 27, 2) if outs > 0 else 99.99
        r["aor"]    = round(r["out_sum"] / g, 1) if g > 0 else 0.0
        r["k9"]     = round(r["k"]  / outs * 27, 2) if outs > 0 else 0.0
        r["bb9"]    = round(r["bb"] / outs * 27, 2) if outs > 0 else 0.0
        # FIP recalibrated to O27 baseline (~11.50 league ERA).
        # FIP = (13*HR + 3*BB - 2*K) / IP + C, where IP = outs/3 and C ≈ 11.50.
        ip = outs / 3.0
        r["fip"] = round((13 * r["hr"] + 3 * r["bb"] - 2 * r["k"]) / ip + 11.50, 2) if ip > 0 else 0.0

    rows = list(agg.values())
    if stat == "era":
        rows = [r for r in rows if r["outs"] >= 9]
        rows.sort(key=lambda x: x["era"])
    elif stat == "whip":
        rows = [r for r in rows if r["outs"] >= 9]
        rows.sort(key=lambda x: x["whip"])
    elif stat == "outs":
        rows.sort(key=lambda x: -x["outs"])
    else:
        rows.sort(key=lambda x: -x.get(stat, 0))
    return rows[:limit]


def get_team_batting(abbrev: str) -> list[dict]:
    """Aggregate batting stats for all players on a team."""
    if _v2_active():
        return _v2().get_team_batting(abbrev)
    agg: dict[str, dict] = {}
    for gid in _RECENT:
        g = _GAMES.get(gid)
        if not g:
            continue
        for side in ("v", "h"):
            if g.get("visitors_abbrev" if side == "v" else "home_abbrev") != abbrev:
                continue
            for row in g.get(f"{side}_batting", []):
                key = row["name"]
                if key not in agg:
                    agg[key] = {
                        "name": row["name"], "pos": row.get("pos", ""),
                        "is_joker": row.get("is_joker", False),
                        "archetype": row.get("archetype", ""),
                        "gp": 0, "pa": 0, "ab": 0, "runs": 0, "hits": 0,
                        "doubles": 0, "triples": 0, "hr": 0,
                        "rbi": 0, "bb": 0, "k": 0, "hbp": 0, "or_": 0,
                    }
                r = agg[key]
                if row.get("pa", 0) > 0:
                    r["gp"] += 1
                for f in ("pa", "ab", "runs", "hits", "doubles", "triples",
                          "hr", "rbi", "bb", "k", "hbp"):
                    r[f] += row.get(f, 0)
                r["or_"] += row.get("or_", 0)

    rows = list(agg.values())
    for r in rows:
        r["avg"] = f"{r['hits']/r['ab']:.3f}" if r["ab"] > 0 else ".000"
    rows.sort(key=lambda x: (-x["pa"], x["name"]))
    return rows


def get_team_pitching(abbrev: str) -> list[dict]:
    """Aggregate pitching stats for all pitchers on a team."""
    if _v2_active():
        return _v2().get_team_pitching(abbrev)
    agg: dict[str, dict] = {}
    for gid in _RECENT:
        g = _GAMES.get(gid)
        if not g:
            continue
        for side in ("v", "h"):
            if g.get("visitors_abbrev" if side == "v" else "home_abbrev") != abbrev:
                continue
            for row in g.get(f"{side}_pitching", []):
                key = row["name"]
                if key not in agg:
                    agg[key] = {
                        "name": row["name"],
                        "g": 0, "bf": 0, "outs": 0, "p": 0,
                        "h": 0, "r": 0, "er": 0, "bb": 0, "k": 0,
                        "hr": 0, "out_sum": 0,
                    }
                r = agg[key]
                r["g"] += 1
                for f in ("bf", "outs", "h", "r", "bb", "k", "hr", "p"):
                    r[f] += row.get(f, 0)
                r["er"] += row.get("er", row.get("r", 0))
                r["out_sum"] += row.get("out", 0)

    rows = list(agg.values())
    for r in rows:
        outs = r["outs"]
        g    = max(r["g"], 1)
        ip   = outs / 3.0
        r["os_pct"] = f"{round(outs / (g * 27) * 100)}%" if outs > 0 else "0%"
        r["era"]    = f"{r['r'] / outs * 27:.2f}" if outs > 0 else "—"
        r["whip"]   = f"{(r['bb'] + r['h']) / outs * 27:.2f}" if outs > 0 else "—"
        r["aor"]    = f"{r['out_sum'] / g:.1f}" if g > 0 else "—"
        r["k9"]     = f"{r['k']  / outs * 27:.2f}" if outs > 0 else "—"
        r["bb9"]    = f"{r['bb'] / outs * 27:.2f}" if outs > 0 else "—"
        r["fip"]    = f"{(13 * r['hr'] + 3 * r['bb'] - 2 * r['k']) / ip + 11.50:.2f}" if ip > 0 else "—"
    rows.sort(key=lambda x: (-x["outs"], x["name"]))
    return rows


def get_player_stats(team_abbrev: str, player_name: str) -> Optional[dict]:
    """Return accumulated batting stats for one player."""
    if _v2_active():
        return _v2().get_player_stats(team_abbrev, player_name)
    agg: dict = {}
    for gid in _RECENT:
        g = _GAMES.get(gid)
        if not g:
            continue
        for side in ("v", "h"):
            if g.get("visitors_abbrev" if side == "v" else "home_abbrev") != team_abbrev:
                continue
            for row in g.get(f"{side}_batting", []):
                if row["name"] != player_name:
                    continue
                if not agg:
                    agg = {
                        "name": row["name"], "pos": row.get("pos", ""),
                        "is_joker": row.get("is_joker", False),
                        "archetype": row.get("archetype", ""),
                        "gp": 0, "pa": 0, "ab": 0, "runs": 0, "hits": 0,
                        "doubles": 0, "triples": 0, "hr": 0,
                        "rbi": 0, "bb": 0, "k": 0, "hbp": 0, "or_": 0,
                    }
                if row.get("pa", 0) > 0:
                    agg["gp"] += 1
                for f in ("pa", "ab", "runs", "hits", "doubles", "triples",
                          "hr", "rbi", "bb", "k", "hbp"):
                    agg[f] += row.get(f, 0)
                agg["or_"] += row.get("or_", 0)
    if agg:
        agg["avg"] = f"{agg['hits']/agg['ab']:.3f}" if agg["ab"] > 0 else ".000"
    return agg or None


def get_pitcher_game_log(team_abbrev: str, player_name: str,
                         limit: int = 15) -> list[dict]:
    """Return per-game pitching entries for one pitcher (newest first)."""
    if _v2_active():
        return _v2().get_pitcher_game_log(team_abbrev, player_name, limit)
    log: list[dict] = []
    for gid in reversed(_RECENT):
        g = _GAMES.get(gid)
        if not g:
            continue
        for side in ("v", "h"):
            if g.get("visitors_abbrev" if side == "v" else "home_abbrev") != team_abbrev:
                continue
            for row in g.get(f"{side}_pitching", []):
                if row["name"] != player_name:
                    continue
                outs = row.get("outs", 0)
                log.append({
                    "game_id":  g["game_id"],
                    "opp":      g["home_abbrev"] if side == "v" else g["visitors_abbrev"],
                    "ha":       "@" if side == "v" else "vs",
                    "outs":     outs,
                    "os_pct":   round(outs / 27 * 100),
                    "bf":       row.get("bf", 0),
                    "p":        row.get("p", 0),
                    "out":      row.get("out", 0),
                    "r":        row.get("r", 0),
                    "er":       row.get("er", row.get("r", 0)),
                    "h":        row.get("h", 0),
                    "bb":       row.get("bb", 0),
                    "k":        row.get("k", 0),
                    "hr":       row.get("hr", 0),
                    "era":      row.get("era", "—"),
                })
                break
        if len(log) >= limit:
            break
    return log


# ---------------------------------------------------------------------------
# Game storage
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Neutral helpers — used by the stats site so it doesn't reach into
# _RECENT / _GAMES directly. These dispatch to v2_bridge when active.
# ---------------------------------------------------------------------------

def has_data() -> bool:
    """True iff at least one game (v2 or in-memory) is available for stats."""
    if _v2_active():
        return _v2().has_data()
    return bool(_RECENT)


def total_games() -> int:
    if _v2_active():
        return _v2().total_games()
    return len(_RECENT)


def recent_game_ids(limit: int = 200) -> list[str]:
    """Return played game IDs newest → oldest, for prev/next navigation."""
    if _v2_active():
        return _v2().recent_game_ids(limit)
    return list(reversed(_RECENT[-limit:]))


def avg_scores() -> tuple[float, float]:
    """Return (avg visitors score, avg home score) across played games."""
    if _v2_active():
        from o27v2 import db as _v2db
        row = _v2db.fetchone(
            "SELECT AVG(away_score) AS av, AVG(home_score) AS ah, "
            "COUNT(*) AS n FROM games WHERE played = 1"
        )
        if not row or not row["n"]:
            return 0.0, 0.0
        return round(row["av"] or 0.0, 1), round(row["ah"] or 0.0, 1)
    games = [_GAMES[gid] for gid in _RECENT if gid in _GAMES]
    if not games:
        return 0.0, 0.0
    av = round(sum(g["v_score"] for g in games) / len(games), 1)
    ah = round(sum(g["h_score"] for g in games) / len(games), 1)
    return av, ah


def get_stays_leaders(limit: int = 10) -> list[dict]:
    if _v2_active():
        return _v2().get_stays_leaders(limit)
    agg: dict[str, dict] = {}
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
                    agg[key] = {"name": row["name"], "team": abbrev, "stays": 0}
                agg[key]["stays"] += row.get("stays", 0)
    return sorted(agg.values(), key=lambda x: -x["stays"])[:limit]


def get_wins_leaders(limit: int = 10) -> list[dict]:
    if _v2_active():
        return _v2().get_wins_leaders(limit)
    wins: dict[str, dict] = {}
    for gid in _RECENT:
        g = _GAMES.get(gid)
        if not g:
            continue
        wp = g.get("winner_pitcher")
        if not wp or wp == "—":
            continue
        winner_id = g.get("winner_id", "")
        abbrev = g.get(
            "visitors_abbrev" if winner_id == "visitors" else "home_abbrev", ""
        )
        key = f"{wp}|{abbrev}"
        if key not in wins:
            wins[key] = {"name": wp, "team": abbrev, "wins": 0, "g": 0}
        wins[key]["wins"] += 1
        wins[key]["g"] += 1
    return sorted(wins.values(), key=lambda x: -x["wins"])[:limit]


def store_game(game_id: str, result: dict) -> None:
    _GAMES[game_id] = result
    if game_id in _RECENT:
        _RECENT.remove(game_id)
    _RECENT.append(game_id)
    while len(_RECENT) > _MAX_RECENT:
        old = _RECENT.pop(0)
        _GAMES.pop(old, None)


def make_game_id(seed: int, v_abbrev: str, h_abbrev: str) -> str:
    return f"{seed}_{v_abbrev}_{h_abbrev}"
