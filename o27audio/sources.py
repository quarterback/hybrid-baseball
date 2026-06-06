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


# ---------------------------------------------------------------------------
# League roundup ("sports radio") — Stage 2
# ---------------------------------------------------------------------------

@dataclass
class RoundupData:
    """Material for a league roundup show covering one game-day."""

    date: str
    season: int
    slate: list[dict[str, Any]] = field(default_factory=list)
    standings: list[dict[str, Any]] = field(default_factory=list)
    hr_leaders: list[dict[str, Any]] = field(default_factory=list)
    rbi_leaders: list[dict[str, Any]] = field(default_factory=list)
    k_leaders: list[dict[str, Any]] = field(default_factory=list)
    transactions: list[dict[str, Any]] = field(default_factory=list)


def latest_played_date() -> str | None:
    row = db.fetchone("SELECT MAX(game_date) AS d FROM games WHERE played = 1")
    return row["d"] if row and row["d"] else None


def load_roundup(date: str | None = None) -> RoundupData:
    """Assemble a roundup for ``date`` (defaults to the latest played date)."""
    date = date or latest_played_date()
    if not date:
        raise ValueError("no played games yet — nothing to round up")

    season_row = db.fetchone(
        "SELECT season FROM games WHERE played = 1 AND game_date = ? "
        "ORDER BY id DESC LIMIT 1",
        (date,),
    )
    season = season_row["season"] if season_row else 1

    slate = db.fetchall(
        "SELECT g.id, g.home_score, g.away_score, g.super_inning, "
        "       at.name AS away, at.abbrev AS away_abbrev, at.city AS away_city, "
        "       ht.name AS home, ht.abbrev AS home_abbrev, ht.city AS home_city, "
        "       wt.name AS winner "
        "FROM games g "
        "JOIN teams at ON at.id = g.away_team_id "
        "JOIN teams ht ON ht.id = g.home_team_id "
        "LEFT JOIN teams wt ON wt.id = g.winner_id "
        "WHERE g.played = 1 AND g.game_date = ? ORDER BY g.id",
        (date,),
    )

    standings = db.fetchall(
        "SELECT name, abbrev, city, division, league, wins, losses "
        "FROM teams ORDER BY league, division, wins DESC, losses ASC",
    )

    hr_leaders = _season_bat_leaders(season, "SUM(s.hr)", "hr")
    rbi_leaders = _season_bat_leaders(season, "SUM(s.rbi)", "rbi")
    k_leaders = db.fetchall(
        "SELECT p.name AS name, t.abbrev AS team, SUM(s.k) AS k "
        "FROM game_pitcher_stats s "
        "JOIN games g ON g.id = s.game_id "
        "JOIN players p ON p.id = s.player_id "
        "JOIN teams t ON t.id = s.team_id "
        "WHERE g.season = ? GROUP BY s.player_id "
        "ORDER BY SUM(s.k) DESC LIMIT 5",
        (season,),
    )

    transactions = db.fetchall(
        "SELECT tr.game_date, tr.event_type, tr.detail, "
        "       t.abbrev AS team, p.name AS player "
        "FROM transactions tr "
        "LEFT JOIN teams t ON t.id = tr.team_id "
        "LEFT JOIN players p ON p.id = tr.player_id "
        "ORDER BY tr.id DESC LIMIT 12",
    )

    return RoundupData(
        date=date, season=season, slate=slate, standings=standings,
        hr_leaders=hr_leaders, rbi_leaders=rbi_leaders, k_leaders=k_leaders,
        transactions=transactions,
    )


def _season_bat_leaders(season: int, order_expr: str, key: str,
                        limit: int = 5) -> list[dict[str, Any]]:
    return db.fetchall(
        f"SELECT p.name AS name, t.abbrev AS team, "
        f"       SUM(s.hr) AS hr, SUM(s.rbi) AS rbi, SUM(s.hits) AS h, "
        f"       SUM(s.runs) AS r, {order_expr} AS sort_val "
        f"FROM game_batter_stats s "
        f"JOIN games g ON g.id = s.game_id "
        f"JOIN players p ON p.id = s.player_id "
        f"JOIN teams t ON t.id = s.team_id "
        f"WHERE g.season = ? GROUP BY s.player_id "
        f"ORDER BY {order_expr} DESC LIMIT ?",
        (season, limit),
    )


def pick_game_of_the_day(date: str | None = None) -> int | None:
    """The most broadcast-worthy game on ``date`` — Super-Innings first, then
    most total runs, then closest. Used by the auto-generate worker."""
    date = date or latest_played_date()
    if not date:
        return None
    row = db.fetchone(
        "SELECT id FROM games WHERE played = 1 AND game_date = ? "
        "ORDER BY super_inning DESC, (home_score + away_score) DESC, "
        "         ABS(home_score - away_score) ASC LIMIT 1",
        (date,),
    )
    return row["id"] if row else None

