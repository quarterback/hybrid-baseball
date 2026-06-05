"""Stage 1 — Gather. Pure read-only pulls from the live O27 database.

Reuses ``o27v2.db`` (the documented read interface) so DB-path resolution —
``O27V2_DB_PATH`` and the multi-save registry — works exactly like the app.
No writes, ever; the audio service is a consumer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from o27v2 import db


@dataclass
class GameData:
    """Everything the script writer needs about one game."""

    game_id: int
    season: int
    game_date: str
    home: dict[str, Any]
    away: dict[str, Any]
    home_score: int
    away_score: int
    winner_id: int | None
    super_inning: bool
    is_playoff: bool
    pbp_text: str
    scoring_events: list[dict[str, Any]] = field(default_factory=list)
    batting_stars: list[dict[str, Any]] = field(default_factory=list)
    pitching_lines: list[dict[str, Any]] = field(default_factory=list)

    @property
    def winner(self) -> dict[str, Any] | None:
        if self.winner_id == self.home["id"]:
            return self.home
        if self.winner_id == self.away["id"]:
            return self.away
        return None

    @property
    def margin(self) -> int:
        return abs(self.home_score - self.away_score)


def _team(team_id: int) -> dict[str, Any]:
    row = db.fetchone(
        "SELECT id, name, abbrev, city, division, league, park_name "
        "FROM teams WHERE id = ?",
        (team_id,),
    )
    if not row:
        raise ValueError(f"team {team_id} not found")
    return row


def load_game(game_id: int) -> GameData:
    """Assemble a :class:`GameData` for ``game_id`` (must be a played game)."""
    g = db.fetchone(
        "SELECT id, season, game_date, home_team_id, away_team_id, home_score, "
        "away_score, winner_id, super_inning, played, is_playoff "
        "FROM games WHERE id = ?",
        (game_id,),
    )
    if not g:
        raise ValueError(f"game {game_id} not found")
    if not g["played"]:
        raise ValueError(f"game {game_id} has not been played yet")

    pbp = db.fetchone("SELECT pbp_text FROM game_pbp WHERE game_id = ?", (game_id,))
    pbp_text = pbp["pbp_text"] if pbp else ""
    if not pbp_text:
        # Legacy games (simmed before pbp persistence) can't be narrated well.
        raise ValueError(
            f"game {game_id} has no stored play-by-play (legacy game); "
            "re-sim or pick a newer game"
        )

    home = _team(g["home_team_id"])
    away = _team(g["away_team_id"])

    return GameData(
        game_id=game_id,
        season=g["season"],
        game_date=g["game_date"],
        home=home,
        away=away,
        home_score=g["home_score"],
        away_score=g["away_score"],
        winner_id=g["winner_id"],
        super_inning=bool(g["super_inning"]),
        is_playoff=bool(g["is_playoff"]),
        pbp_text=pbp_text,
        scoring_events=_scoring_events(game_id),
        batting_stars=_batting_stars(game_id),
        pitching_lines=_pitching_lines(game_id),
    )


def _scoring_events(game_id: int) -> list[dict[str, Any]]:
    rows = db.fetchall(
        "SELECT se.seq, se.half, se.outs_before, se.visitors_score, se.home_score, "
        "       b.name AS batter, r.name AS runner "
        "FROM game_scoring_events se "
        "JOIN players b ON b.id = se.batter_id "
        "JOIN players r ON r.id = se.runner_id "
        "WHERE se.game_id = ? ORDER BY se.seq",
        (game_id,),
    )
    return rows


def _batting_stars(game_id: int, limit: int = 6) -> list[dict[str, Any]]:
    """Top batters by a simple impact score (HR, RBI, runs, hits), summed
    across phases (regulation + super-innings)."""
    rows = db.fetchall(
        "SELECT p.name AS name, t.abbrev AS team, "
        "       SUM(s.ab) AS ab, SUM(s.hits) AS h, SUM(s.hr) AS hr, "
        "       SUM(s.rbi) AS rbi, SUM(s.runs) AS r, SUM(s.bb) AS bb, "
        "       SUM(s.sb) AS sb "
        "FROM game_batter_stats s "
        "JOIN players p ON p.id = s.player_id "
        "JOIN teams t ON t.id = s.team_id "
        "WHERE s.game_id = ? "
        "GROUP BY s.player_id "
        "ORDER BY (SUM(s.hr) * 4 + SUM(s.rbi) * 2 + SUM(s.runs) + SUM(s.hits)) DESC "
        "LIMIT ?",
        (game_id, limit),
    )
    return rows


def _pitching_lines(game_id: int, limit: int = 4) -> list[dict[str, Any]]:
    rows = db.fetchall(
        "SELECT p.name AS name, t.abbrev AS team, "
        "       SUM(s.outs_recorded) AS outs, SUM(s.k) AS k, SUM(s.bb) AS bb, "
        "       SUM(s.hits_allowed) AS h, SUM(s.runs_allowed) AS r, "
        "       SUM(s.er) AS er, SUM(s.hr_allowed) AS hr "
        "FROM game_pitcher_stats s "
        "JOIN players p ON p.id = s.player_id "
        "JOIN teams t ON t.id = s.team_id "
        "WHERE s.game_id = ? "
        "GROUP BY s.player_id "
        "ORDER BY SUM(s.outs_recorded) DESC "
        "LIMIT ?",
        (game_id, limit),
    )
    return rows
