"""
o27.almanac.loader — Pulls raw season rows out of whatever source the
user pointed us at and returns plain-dict payloads.

Two source modes:

  * sqlite path → read the o27v2 schema directly (teams, players,
    games, game_batter_stats, game_pitcher_stats, etc.).
  * JSON archive → read a previously-exported season bundle
    (`exports/season-bundle.json` inside an almanac build). This is the
    "data-in" wiring — drop a bundle from one season into another
    almanac build and the site rebuilds against it.

Loader output is intentionally close to raw rows — derived stats
(ERA/wOBA/etc.) live in compute.py so both ingestion modes share one
math layer.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any


# ---------------------------------------------------------------------------
# Public dataset shape
# ---------------------------------------------------------------------------
#
# Every loader returns the same dict, keyed by table:
#
#   {
#     "meta":     {"source": str, "loaded_at": iso, "season": int|None,
#                  "schema_version": "almanac/1"},
#     "teams":    [ {id, abbrev, name, city, league, division, w, l,
#                    park_name, manager_name, ...}, ... ],
#     "players":  [ {id, team_id, name, position, is_pitcher, bats,
#                    throws, country, age, archetype, attrs...}, ... ],
#     "games":    [ {id, season, game_date, home_team_id, away_team_id,
#                    home_score, away_score, winner_id, played,
#                    super_inning, is_playoff}, ... ],
#     "batting":  [ per-game per-player batting rows (raw counters) ],
#     "pitching": [ per-game per-player pitching rows (raw counters) ],
#   }
#
# Downstream stages (compute.py, render.py) treat this dict as the
# canonical input regardless of source.
# ---------------------------------------------------------------------------


SCHEMA_VERSION = "almanac/1"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def load(source: str) -> dict[str, Any]:
    """Load a dataset from `source`. Resolution rules:

      * source == "live"    → use $O27V2_DB_PATH or repo-default DB path.
      * source ends .json   → read as season-archive JSON.
      * source ends .db /
        .sqlite / .sqlite3  → read as SQLite.
      * otherwise           → try SQLite first; fall back to JSON if the
        file doesn't look like SQLite.
    """
    if source == "live":
        path = _default_db_path()
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"--source live: no DB at {path}. Run a season in o27v2 "
                f"or pass --source <path/to/season.json>."
            )
        return _load_sqlite(path)

    if not os.path.exists(source):
        raise FileNotFoundError(f"--source {source}: file not found.")

    ext = os.path.splitext(source)[1].lower()
    if ext == ".json":
        return _load_json(source)
    if ext in (".db", ".sqlite", ".sqlite3"):
        return _load_sqlite(source)
    if _looks_like_sqlite(source):
        return _load_sqlite(source)
    return _load_json(source)


def _default_db_path() -> str:
    return os.environ.get(
        "O27V2_DB_PATH",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "o27v2", "o27v2.db",
        ),
    )


def _looks_like_sqlite(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16).startswith(b"SQLite format 3")
    except OSError:
        return False


# ---------------------------------------------------------------------------
# SQLite loader
# ---------------------------------------------------------------------------

def _load_sqlite(path: str) -> dict[str, Any]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        teams    = [dict(r) for r in conn.execute("SELECT * FROM teams")]
        players  = [dict(r) for r in conn.execute("SELECT * FROM players")]
        games    = [dict(r) for r in conn.execute(
            "SELECT * FROM games WHERE played = 1 ORDER BY id"
        )]
        batting  = [dict(r) for r in conn.execute(
            "SELECT * FROM game_batter_stats"
        )]
        pitching = [dict(r) for r in conn.execute(
            "SELECT * FROM game_pitcher_stats"
        )]
        seasons = _try_query(conn, "SELECT * FROM seasons ORDER BY season_number")
        awards  = _try_query(conn, "SELECT * FROM season_awards ORDER BY season, category")
        scoring_events = _try_query(conn,
            "SELECT * FROM game_scoring_events ORDER BY game_id, seq")
        pa_log = _try_query(conn, "SELECT * FROM game_pa_log")
        team_phase_outs = _try_query(conn, "SELECT * FROM team_phase_outs")
        playoff_series = _try_query(conn,
            "SELECT * FROM playoff_series ORDER BY round_idx, bracket_position")
        award_ballots = _try_query(conn,
            "SELECT * FROM award_ballots ORDER BY season, category, voter_id, rank")
        season_standings = _try_query(conn, "SELECT * FROM season_standings")
        season_batting_leaders = _try_query(conn,
            "SELECT * FROM season_batting_leaders")
        season_pitching_leaders = _try_query(conn,
            "SELECT * FROM season_pitching_leaders")
    finally:
        conn.close()

    return {
        "meta": {
            "source": path,
            "source_kind": "sqlite",
            "schema_version": SCHEMA_VERSION,
            "season": _infer_season(seasons, games),
            "game_count": len(games),
            "team_count": len(teams),
            "player_count": len(players),
        },
        "teams": teams,
        "players": players,
        "games": games,
        "batting": batting,
        "pitching": pitching,
        "seasons": seasons,
        "awards": awards,
        "scoring_events": scoring_events,
        "pa_log": pa_log,
        "team_phase_outs": team_phase_outs,
        "playoff_series": playoff_series,
        "award_ballots": award_ballots,
        "season_standings_archive":      season_standings,
        "season_batting_leaders_archive": season_batting_leaders,
        "season_pitching_leaders_archive": season_pitching_leaders,
    }


def _try_query(conn, sql: str) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(sql)]
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------------------
# JSON loader
# ---------------------------------------------------------------------------

def _load_json(path: str) -> dict[str, Any]:
    with open(path) as f:
        data = json.load(f)

    if not isinstance(data, dict) or "teams" not in data:
        raise ValueError(
            f"{path} doesn't look like a season-archive JSON bundle "
            f"(expected keys: teams, players, games, batting, pitching)."
        )

    out: dict[str, Any] = {
        "meta": dict(data.get("meta") or {}),
        "teams":    data.get("teams")    or [],
        "players":  data.get("players")  or [],
        "games":    data.get("games")    or [],
        "batting":  data.get("batting")  or [],
        "pitching": data.get("pitching") or [],
        "seasons":  data.get("seasons")  or [],
        "awards":   data.get("awards")   or [],
        "scoring_events":            data.get("scoring_events")            or [],
        "pa_log":                    data.get("pa_log")                    or [],
        "team_phase_outs":           data.get("team_phase_outs")           or [],
        "playoff_series":            data.get("playoff_series")            or [],
        "award_ballots":             data.get("award_ballots")             or [],
        "season_standings_archive":  data.get("season_standings_archive")  or [],
        "season_batting_leaders_archive":  data.get("season_batting_leaders_archive")  or [],
        "season_pitching_leaders_archive": data.get("season_pitching_leaders_archive") or [],
    }
    out["meta"]["source"]      = path
    out["meta"]["source_kind"] = "json"
    out["meta"]["schema_version"] = (
        out["meta"].get("schema_version") or SCHEMA_VERSION
    )
    out["meta"]["game_count"]   = len(out["games"])
    out["meta"]["team_count"]   = len(out["teams"])
    out["meta"]["player_count"] = len(out["players"])
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_season(seasons: list[dict], games: list[dict]) -> int | None:
    if seasons:
        return seasons[-1].get("season_number")
    for g in reversed(games):
        s = g.get("season")
        if s:
            return s
    return None
