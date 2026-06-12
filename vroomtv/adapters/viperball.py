"""Read-only adapter for viperball SQLite database (JSON blob store)."""
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
    return os.environ.get("VIPERBALL_DB") or None


def _load_leagues(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT save_key, label, data FROM saves WHERE save_type='pro_league' ORDER BY updated_at DESC"
    ).fetchall()
    leagues = []
    for row in rows:
        try:
            blob = json.loads(row["data"])
            leagues.append({"save_key": row["save_key"], "label": row["label"] or row["save_key"], "blob": blob})
        except (json.JSONDecodeError, TypeError):
            continue
    return leagues


def get_recent_scores(limit_per_league: int = 8) -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    results = []
    try:
        conn = _conn(path)
        leagues = _load_leagues(conn)
        conn.close()
        for lg in leagues:
            blob = lg["blob"]
            current_week = blob.get("current_week", 0)
            collected = 0
            for w in range(current_week, 0, -1):
                week_results = blob.get("results", {}).get(str(w), {})
                for matchup_key, res in week_results.items():
                    if collected >= limit_per_league:
                        break
                    results.append({
                        "league": lg["label"],
                        "save_key": lg["save_key"],
                        "week": w,
                        "matchup_key": matchup_key,
                        "home_name": res.get("home_name", ""),
                        "away_name": res.get("away_name", ""),
                        "home_score": res.get("home_score", 0),
                        "away_score": res.get("away_score", 0),
                    })
                    collected += 1
                if collected >= limit_per_league:
                    break
    except Exception:
        pass
    return results


def get_standings() -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        conn = _conn(path)
        leagues = _load_leagues(conn)
        conn.close()
        for lg in leagues:
            blob = lg["blob"]
            teams = []
            for team_key, ts in blob.get("standings", {}).items():
                if isinstance(ts, dict):
                    teams.append({
                        "team_key": team_key,
                        "team_name": ts.get("team_name", team_key),
                        "wins": ts.get("wins", 0),
                        "losses": ts.get("losses", 0),
                        "ties": ts.get("ties", 0),
                        "streak": ts.get("streak", ""),
                        "streak_type": ts.get("streak_type", ""),
                    })
            teams.sort(key=lambda t: (-t["wins"], t["losses"]))
            out.append({"league": lg["label"], "save_key": lg["save_key"], "teams": teams})
    except Exception:
        pass
    return out


def get_stat_leaders(limit: int = 10) -> list[dict]:
    path = _db_path()
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        conn = _conn(path)
        leagues = _load_leagues(conn)
        conn.close()
        for lg in leagues:
            blob = lg["blob"]
            all_players: list[dict] = []
            for team_key, team_players in blob.get("player_season_stats", {}).items():
                for pid, ps in team_players.items():
                    all_players.append({
                        "name": ps.get("name", pid),
                        "team_key": team_key,
                        "position": ps.get("position", ""),
                        "games": ps.get("games", 0),
                        "rushing_yards": ps.get("rushing_yards", 0),
                        "touchdowns": ps.get("touchdowns", 0),
                        "kick_pass_yards": ps.get("kick_pass_yards", 0),
                        "total_yards": ps.get("total_yards", 0),
                    })
            all_players.sort(key=lambda p: -p["rushing_yards"])
            out.append({"league": lg["label"], "save_key": lg["save_key"], "leaders": all_players[:limit]})
    except Exception:
        pass
    return out


def get_game_detail(save_key: str, week: int, matchup_key: str) -> dict[str, Any] | None:
    path = _db_path()
    if not path or not os.path.exists(path):
        return None
    try:
        conn = _conn(path)
        row = conn.execute(
            "SELECT label, data FROM saves WHERE save_type='pro_league' AND save_key=?",
            (save_key,)
        ).fetchone()
        conn.close()
        if not row:
            return None
        blob = json.loads(row["data"])
        game = blob.get("results", {}).get(str(week), {}).get(matchup_key)
        if not game:
            return None
        return {
            "league": row["label"] or save_key,
            "save_key": save_key,
            "week": week,
            "matchup_key": matchup_key,
            "home_name": game.get("home_name", ""),
            "away_name": game.get("away_name", ""),
            "home_score": game.get("home_score", 0),
            "away_score": game.get("away_score", 0),
            "result": game.get("result", {}),
        }
    except Exception:
        return None
