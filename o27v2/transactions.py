"""
Phase 9: Transaction log helpers for O27v2.

Every roster move (injury, return, trade, waiver claim) is recorded in the
transactions table so the full history is browsable in the web app.
"""
from __future__ import annotations

from o27v2 import db


def log_transaction(
    season: int,
    game_date: str,
    event_type: str,
    team_id: int | None,
    player_id: int | None,
    detail: str,
) -> int:
    """Insert one transaction row; returns the new row id."""
    return db.execute(
        "INSERT INTO transactions (season, game_date, event_type, team_id, player_id, detail) "
        "VALUES (?,?,?,?,?,?)",
        (season, game_date, event_type, team_id, player_id, detail),
    )


def log_many(season: int, game_date: str, events: list[dict]) -> None:
    """Bulk-log a list of event dicts (each has event_type, team_id, player_id, detail)."""
    if not events:
        return
    db.executemany(
        "INSERT INTO transactions (season, game_date, event_type, team_id, player_id, detail) "
        "VALUES (?,?,?,?,?,?)",
        [
            (season, game_date, e["event_type"], e.get("team_id"), e.get("player_id"), e["detail"])
            for e in events
        ],
    )


def get_transactions(
    team_id: int | None = None,
    event_type: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """
    Fetch recent transactions with optional filters.
    Joins team and player tables for display names.
    """
    sql = """
        SELECT tx.*,
               t.name  AS team_name,
               t.abbrev AS team_abbrev,
               p.name  AS player_name,
               p.position AS player_pos
        FROM transactions tx
        LEFT JOIN teams t  ON t.id  = tx.team_id
        LEFT JOIN players p ON p.id = tx.player_id
    """
    where: list[str] = []
    params: list = []

    if team_id is not None:
        where.append("tx.team_id = ?")
        params.append(team_id)
    if event_type:
        where.append("tx.event_type = ?")
        params.append(event_type)

    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY tx.id DESC LIMIT ?"
    params.append(limit)

    return db.fetchall(sql, tuple(params))
