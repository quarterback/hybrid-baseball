"""Read-only adapter for tennis-team-manager SQLite database."""
from __future__ import annotations
import json
import os
import sqlite3
from typing import Any


def _conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _db_path() -> str | None:
    return os.environ.get("TENNIS_DB") or None


def get_recent_scores(limit_per_source: int = 8) -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        conn = _conn(path)
        for lg in conn.execute("SELECT id, name FROM gtt_leagues ORDER BY id").fetchall():
            rows = conn.execute("""
                SELECT d.id, d.week, d.home_points, d.away_points,
                       hf.name AS home_name, hf.abbrev AS home_abbrev,
                       af.name AS away_name, af.abbrev AS away_abbrev
                FROM gtt_duals d
                JOIN gtt_franchises hf ON hf.id = d.home AND hf.league_id = d.league_id
                JOIN gtt_franchises af ON af.id = d.away AND af.league_id = d.league_id
                WHERE d.league_id = ? AND d.status = 'complete'
                ORDER BY d.id DESC LIMIT ?
            """, (lg["id"], limit_per_source)).fetchall()
            for r in rows:
                out.append({
                    "source": "gtt", "league": lg["name"], "league_id": lg["id"],
                    "id": r["id"], "week": r["week"],
                    "home_name": r["home_name"], "home_abbrev": r["home_abbrev"],
                    "away_name": r["away_name"], "away_abbrev": r["away_abbrev"],
                    "home_points": r["home_points"], "away_points": r["away_points"],
                })
        for s in conn.execute("SELECT id, division, gender FROM seasons ORDER BY id").fetchall():
            label = f"{s['division'].upper()} {s['gender'].title()}"
            rows = conn.execute("""
                SELECT id, week, home, away, home_points, away_points
                FROM duals WHERE season_id = ? AND status = 'complete'
                ORDER BY id DESC LIMIT ?
            """, (s["id"], limit_per_source)).fetchall()
            for r in rows:
                out.append({
                    "source": "ncaa", "league": label, "season_id": s["id"],
                    "id": r["id"], "week": r["week"],
                    "home_name": r["home"], "away_name": r["away"],
                    "home_points": r["home_points"], "away_points": r["away_points"],
                })
        conn.close()
    except Exception:
        pass
    return out


def get_standings() -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        conn = _conn(path)
        for lg in conn.execute("SELECT id, name FROM gtt_leagues ORDER BY id").fetchall():
            franchises = {
                r["id"]: {"name": r["name"], "abbrev": r["abbrev"], "wins": 0, "losses": 0}
                for r in conn.execute(
                    "SELECT id, name, abbrev FROM gtt_franchises WHERE league_id=?", (lg["id"],)
                ).fetchall()
            }
            for d in conn.execute(
                "SELECT home, away, winner FROM gtt_duals WHERE league_id=? AND status='complete'",
                (lg["id"],)
            ).fetchall():
                if d["winner"] in franchises:
                    franchises[d["winner"]]["wins"] += 1
                    loser = d["away"] if d["winner"] == d["home"] else d["home"]
                    if loser in franchises:
                        franchises[loser]["losses"] += 1
            out.append({"league": lg["name"], "source": "gtt",
                        "teams": sorted(franchises.values(), key=lambda t: (-t["wins"], t["losses"]))})
        for s in conn.execute("SELECT id, division, gender FROM seasons ORDER BY id").fetchall():
            label = f"{s['division'].upper()} {s['gender'].title()}"
            rows = conn.execute("""
                SELECT home AS school, SUM(CASE WHEN winner=1 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN winner=0 THEN 1 ELSE 0 END) AS losses
                FROM duals WHERE season_id=? AND status='complete' GROUP BY home
                UNION ALL
                SELECT away, SUM(CASE WHEN winner=0 THEN 1 ELSE 0 END),
                       SUM(CASE WHEN winner=1 THEN 1 ELSE 0 END)
                FROM duals WHERE season_id=? AND status='complete' GROUP BY away
            """, (s["id"], s["id"])).fetchall()
            agg: dict[str, dict] = {}
            for r in rows:
                sch = r["school"]
                if sch not in agg:
                    agg[sch] = {"name": sch, "wins": 0, "losses": 0}
                agg[sch]["wins"] += r["wins"]
                agg[sch]["losses"] += r["losses"]
            out.append({"league": label, "source": "ncaa",
                        "teams": sorted(agg.values(), key=lambda t: (-t["wins"], t["losses"]))})
        conn.close()
    except Exception:
        pass
    return out


def get_stat_leaders(limit: int = 10) -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    try:
        conn = _conn(path)
        rows = conn.execute("""
            SELECT p.name, p.school, p.rating,
                   SUM(ms.winners) AS total_winners, SUM(ms.aces) AS total_aces,
                   SUM(ms.unforced_errors) AS total_ue, COUNT(ms.match_id) AS matches_played
            FROM match_stats ms
            JOIN matches m ON m.id = ms.match_id
            JOIN players p ON (ms.side=0 AND p.id=m.p0_id) OR (ms.side=1 AND p.id=m.p1_id)
            WHERE p.name IS NOT NULL
            GROUP BY p.id HAVING matches_played >= 3
            ORDER BY total_winners DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_game_detail(source: str, dual_id: int) -> dict[str, Any] | None:
    path = _db_path()
    if not path or not os.path.exists(path):
        return None
    try:
        conn = _conn(path)
        if source == "gtt":
            dual = conn.execute("""
                SELECT d.*, hf.name AS home_name, hf.abbrev AS home_abbrev,
                             af.name AS away_name, af.abbrev AS away_abbrev,
                             lg.name AS league_name
                FROM gtt_duals d
                JOIN gtt_franchises hf ON hf.id=d.home AND hf.league_id=d.league_id
                JOIN gtt_franchises af ON af.id=d.away AND af.league_id=d.league_id
                JOIN gtt_leagues lg ON lg.id=d.league_id
                WHERE d.id=?
            """, (dual_id,)).fetchone()
        else:
            dual = conn.execute("""
                SELECT d.*, s.division, s.gender
                FROM duals d JOIN seasons s ON s.id=d.season_id WHERE d.id=?
            """, (dual_id,)).fetchone()
        conn.close()
        if not dual:
            return None
        d = dict(dual)
        d["lines"] = json.loads(d["lines_json"]) if d.get("lines_json") else []
        d["source"] = source
        return d
    except Exception:
        return None
