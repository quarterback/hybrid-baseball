"""
Youth-league game simulator — wires the real O27 PA-by-PA engine into
the youth tournament.

This replaces the heuristic in `o27v2/youth.py:_simulate_unplayed_games`
with the same engine path the pro league uses (`o27.engine.run_game`),
adapted for youth roster shape:

  * 9-batter lineup (8 hitters + SP), matching the original O27
    rules. No DH and no jokers — youth rosters are 8 hitters + 4
    pitchers, period.
  * Bench is empty; once a player exits, they're done.
  * Per-team manager fields use league-mean defaults (no archetype
    drift) since youth teams don't have a managers row.
  * No injury post-processing, no workload tracking — youth play one
    short tournament per season, not a 162-game grind.

Per-game stats are persisted in `game_youth_batter_stats` and
`game_youth_pitcher_stats`. Schemas mirror the pro side just enough
to render a recognisable box score; advanced columns the youth UI
doesn't surface (entry_type, replacement chains, c2 advancement
breakdowns) are intentionally omitted to keep the table narrow.
"""
from __future__ import annotations

import random
from typing import Any

from o27v2 import db
from o27v2 import scout as _scout

from o27.engine.state import Player, Team, GameState
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.render.render import Renderer


# ---------------------------------------------------------------------------
# Schema (per-game stat tables)
# ---------------------------------------------------------------------------

_SCHEMA_BATTER = """
CREATE TABLE IF NOT EXISTS game_youth_batter_stats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id     INTEGER NOT NULL REFERENCES youth_games(id),
    team_id     INTEGER NOT NULL REFERENCES youth_teams(id),
    player_id   INTEGER NOT NULL REFERENCES youth_players(id),
    pa          INTEGER DEFAULT 0,
    ab          INTEGER DEFAULT 0,
    runs        INTEGER DEFAULT 0,
    hits        INTEGER DEFAULT 0,
    doubles     INTEGER DEFAULT 0,
    triples     INTEGER DEFAULT 0,
    hr          INTEGER DEFAULT 0,
    rbi         INTEGER DEFAULT 0,
    bb          INTEGER DEFAULT 0,
    k           INTEGER DEFAULT 0,
    stays       INTEGER DEFAULT 0,
    outs_recorded INTEGER DEFAULT 0
);
"""

_SCHEMA_PITCHER = """
CREATE TABLE IF NOT EXISTS game_youth_pitcher_stats (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id       INTEGER NOT NULL REFERENCES youth_games(id),
    team_id       INTEGER NOT NULL REFERENCES youth_teams(id),
    player_id     INTEGER NOT NULL REFERENCES youth_players(id),
    is_starter    INTEGER DEFAULT 0,
    batters_faced INTEGER DEFAULT 0,
    outs_recorded INTEGER DEFAULT 0,
    hits_allowed  INTEGER DEFAULT 0,
    runs_allowed  INTEGER DEFAULT 0,
    er            INTEGER DEFAULT 0,
    bb            INTEGER DEFAULT 0,
    k             INTEGER DEFAULT 0,
    hr_allowed    INTEGER DEFAULT 0,
    pitches       INTEGER DEFAULT 0
);
"""


def init_youth_sim_schema() -> None:
    db.execute(_SCHEMA_BATTER)
    db.execute(_SCHEMA_PITCHER)


# ---------------------------------------------------------------------------
# Roster → engine team conversion
# ---------------------------------------------------------------------------

def _make_engine_player(p: dict, *, home_bonus: float = 0.0) -> Player:
    """Build one engine Player from a youth_players row. Mirrors
    `o27v2.sim._db_team_to_engine`'s field assignment but skips the
    pro-league-only fields (work_ethic-cup interaction, archetype
    deltas, pitcher_role string)."""
    stamina_grade = p.get("stamina") or p.get("pitcher_skill") or 50
    return Player(
        player_id=str(p["id"]),
        name=p["name"],
        is_pitcher=bool(p["is_pitcher"]),
        skill=_scout.to_unit(p["skill"]) + home_bonus,
        speed=_scout.to_unit(p["speed"]),
        pitcher_skill=_scout.to_unit(p["pitcher_skill"]),
        stamina=_scout.to_unit(stamina_grade),
        stay_aggressiveness=0.30,
        contact_quality_threshold=0.50,
        archetype="",
        pitcher_role="",
        hard_contact_delta=0.0,
        hr_weight_bonus=0.0,
        contact=_scout.to_unit(p.get("contact") or 50),
        power=_scout.to_unit(p.get("power") or 50),
        eye=_scout.to_unit(p.get("eye") or 50),
        command=_scout.to_unit(p.get("command") or 50),
        movement=_scout.to_unit(p.get("movement") or 50),
        bats=str(p.get("bats") or "R"),
        throws=str(p.get("throws") or "R"),
        defense=_scout.to_unit(p.get("defense") or 50),
        arm=_scout.to_unit(p.get("arm") or 50),
        defense_infield=_scout.to_unit(p.get("defense_infield") or 50),
        defense_outfield=_scout.to_unit(p.get("defense_outfield") or 50),
        defense_catcher=_scout.to_unit(p.get("defense_catcher") or 50),
        baserunning=_scout.to_unit(p.get("baserunning") or 50),
        run_aggressiveness=_scout.to_unit(p.get("run_aggressiveness") or 50),
        position=str(p.get("position") or ("P" if p.get("is_pitcher") else "DH")),
    )


def _pick_youth_starter(youth_team_id: int, season: int,
                        rng: random.Random) -> int | None:
    """Pick today's SP for a youth team. Strategy: from the 4 pitchers,
    take the one with the fewest tournament starts so far in this
    season. Ties go to highest pitcher_skill, then lowest id (stable).
    """
    pitchers = db.fetchall(
        "SELECT id, pitcher_skill FROM youth_players "
        "WHERE youth_team_id = ? AND is_pitcher = 1",
        (youth_team_id,),
    )
    if not pitchers:
        return None

    starts = db.fetchall(
        "SELECT player_id, COUNT(*) AS n "
        "FROM game_youth_pitcher_stats gp "
        "JOIN youth_games yg ON yg.id = gp.game_id "
        "WHERE gp.team_id = ? AND gp.is_starter = 1 AND yg.season = ? "
        "GROUP BY player_id",
        (youth_team_id, season),
    )
    starts_by_pid = {r["player_id"]: r["n"] for r in starts}
    ranked = sorted(
        (dict(p) for p in pitchers),
        key=lambda p: (
            starts_by_pid.get(p["id"], 0),
            -int(p["pitcher_skill"] or 50),
            p["id"],
        ),
    )
    return ranked[0]["id"]


def _build_youth_engine_team(
    youth_team_id: int,
    team_role: str,        # "home" | "visitors"
    season: int,
    rng: random.Random,
) -> tuple[Team, list[dict], int]:
    """Build a fully populated engine Team for a youth side. Returns
    (team, original_player_rows, starter_pid)."""
    team_row = db.fetchone(
        "SELECT * FROM youth_teams WHERE id = ?",
        (youth_team_id,),
    )
    if team_row is None:
        raise ValueError(f"Youth team {youth_team_id} not found")

    rows = db.fetchall(
        "SELECT * FROM youth_players WHERE youth_team_id = ?",
        (youth_team_id,),
    )
    players = [dict(r) for r in rows]
    if not players:
        raise ValueError(f"Youth team {youth_team_id} has no players")

    starter_pid = _pick_youth_starter(youth_team_id, season, rng)
    if starter_pid is None:
        raise ValueError(f"Youth team {youth_team_id} has no pitchers")

    # Build engine players. Home bonus is small — kids field a less-
    # established home advantage.
    HOME_BONUS = 0.005 if team_role == "home" else 0.0
    engine_players: list[Player] = []
    hitters: list[Player] = []
    pitchers: list[Player] = []
    starter_engine: Player | None = None
    for p in players:
        ep = _make_engine_player(p, home_bonus=HOME_BONUS)
        engine_players.append(ep)
        if p["is_pitcher"]:
            pitchers.append(ep)
            if p["id"] == starter_pid:
                starter_engine = ep
        else:
            hitters.append(ep)

    if starter_engine is None:
        # Fallback: highest-stamina pitcher if the rotation pick failed.
        starter_engine = max(
            pitchers,
            key=lambda x: getattr(x, "stamina", 0.5),
        )

    # 9-batter lineup: 8 hitters in canonical order + SP last (matches
    # the original O27 rule that the starter must bat).
    HITTER_ORDER = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C"]
    hitters_by_pos = {h.position: h for h in hitters}
    lineup_hitters: list[Player] = []
    used: set[str] = set()
    for pos in HITTER_ORDER:
        h = hitters_by_pos.get(pos)
        if h and h.player_id not in used:
            lineup_hitters.append(h)
            used.add(h.player_id)
    # Backfill with whatever hitters are left if a position is missing.
    for h in hitters:
        if len(lineup_hitters) >= 8:
            break
        if h.player_id not in used:
            lineup_hitters.append(h)
            used.add(h.player_id)
    lineup = lineup_hitters[:8] + [starter_engine]

    team = Team(
        team_id=team_role,
        name=team_row["name"],
        roster=engine_players,
        lineup=lineup,
        park_hr=1.0,
        park_hits=1.0,
        defense_rating=0.5,
        catcher_arm=0.5,
        # League-mean manager — no archetype drift on youth squads.
        manager_archetype="",
        mgr_quick_hook=0.5,
        mgr_bullpen_aggression=0.5,
        mgr_leverage_aware=0.5,
        mgr_joker_aggression=0.0,    # disabled — no jokers on youth
        mgr_pinch_hit_aggression=0.5,
        mgr_platoon_aggression=0.5,
        mgr_run_game=0.5,
        mgr_bench_usage=0.5,
        jokers_available=[],
    )
    return team, players, int(starter_engine.player_id)


# ---------------------------------------------------------------------------
# Stat extraction (lean version — only the columns the youth box score
# actually surfaces)
# ---------------------------------------------------------------------------

def _extract_youth_batter_rows(
    renderer: Renderer,
    team_id: int,
    players: list[dict],
) -> list[dict]:
    pids = {p["id"] for p in players}
    out: list[dict] = []
    phases = renderer.phases_seen() or [0]
    for phase in phases:
        phase_stats = (renderer.batter_stats_for_phase(phase)
                       if phases != [0] or renderer.phases_seen()
                       else dict(renderer._batter_stats))
        for engine_pid, bstat in phase_stats.items():
            try:
                pid = int(engine_pid)
            except (TypeError, ValueError):
                continue
            if pid not in pids:
                continue
            out.append({
                "team_id":   team_id,
                "player_id": pid,
                "pa":        bstat.pa,
                "ab":        bstat.ab,
                "runs":      bstat.runs,
                "hits":      bstat.hits,
                "doubles":   bstat.doubles,
                "triples":   bstat.triples,
                "hr":        bstat.hr,
                "rbi":       bstat.rbi,
                "bb":        bstat.bb,
                "k":         bstat.k,
                "stays":     getattr(bstat, "sty", 0),
                "outs_recorded": bstat.outs_recorded,
            })
    # Aggregate to one row per player (collapse super-inning phases).
    agg: dict[int, dict] = {}
    for r in out:
        a = agg.setdefault(r["player_id"], {**r})
        if a is r:
            continue
        for k, v in r.items():
            if isinstance(v, int) and k not in ("team_id", "player_id"):
                a[k] = a.get(k, 0) + v
    return list(agg.values())


def _extract_youth_pitcher_rows(
    state: GameState,
    team_id: int,
    players: list[dict],
    starter_pid: int,
) -> list[dict]:
    from o27.stats.pitcher import PitcherStats
    pids = {p["id"] for p in players}
    by_pid: dict[int, list] = {}
    for rec in state.spell_log:
        try:
            pid = int(rec.pitcher_id)
        except (TypeError, ValueError):
            continue
        if pid not in pids:
            continue
        by_pid.setdefault(pid, []).append(rec)

    out: list[dict] = []
    for pid, spells in by_pid.items():
        ps = PitcherStats.from_spell_log(spells, str(pid), "")
        out.append({
            "team_id":       team_id,
            "player_id":     pid,
            "is_starter":    1 if pid == starter_pid else 0,
            "batters_faced": ps.batters_faced,
            "outs_recorded": ps.outs_recorded,
            "hits_allowed":  ps.hits_allowed,
            "runs_allowed":  ps.runs_allowed,
            "er":            max(0, ps.runs_allowed - getattr(ps, "unearned_runs", 0)),
            "bb":            ps.bb,
            "k":             ps.k,
            "hr_allowed":    ps.hr_allowed,
            "pitches":       ps.pitches_thrown,
        })
    return out


def _insert_batter_rows(game_id: int, rows: list[dict]) -> None:
    if not rows:
        return
    cols = ("game_id", "team_id", "player_id", "pa", "ab", "runs", "hits",
            "doubles", "triples", "hr", "rbi", "bb", "k", "stays",
            "outs_recorded")
    sql = (f"INSERT INTO game_youth_batter_stats ({', '.join(cols)}) "
           f"VALUES ({', '.join('?' for _ in cols)})")
    for r in rows:
        db.execute(sql, tuple([game_id] + [r.get(c, 0) for c in cols[1:]]))


def _insert_pitcher_rows(game_id: int, rows: list[dict]) -> None:
    if not rows:
        return
    cols = ("game_id", "team_id", "player_id", "is_starter",
            "batters_faced", "outs_recorded", "hits_allowed", "runs_allowed",
            "er", "bb", "k", "hr_allowed", "pitches")
    sql = (f"INSERT INTO game_youth_pitcher_stats ({', '.join(cols)}) "
           f"VALUES ({', '.join('?' for _ in cols)})")
    for r in rows:
        db.execute(sql, tuple([game_id] + [r.get(c, 0) for c in cols[1:]]))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def simulate_youth_game(game_id: int, seed: int | None = None) -> dict:
    """Run the real O27 engine for one youth tournament game. Persists
    score, winner, and per-player stats. Returns a summary dict."""
    init_youth_sim_schema()
    game = db.fetchone("SELECT * FROM youth_games WHERE id = ?", (game_id,))
    if game is None:
        raise ValueError(f"Youth game {game_id} not found")
    if game["played"]:
        return {"game_id": game_id, "skipped": "already played"}

    season = int(game["season"])
    if seed is None:
        seed = int(game["seed"] or random.randint(1, 2**31 - 1))
    rng = random.Random(seed)

    visitors_team, away_players, away_starter_pid = _build_youth_engine_team(
        int(game["away_team_id"]), "visitors", season, rng,
    )
    home_team, home_players, home_starter_pid = _build_youth_engine_team(
        int(game["home_team_id"]), "home", season, rng,
    )

    state = GameState(visitors=visitors_team, home=home_team)
    provider = ProbabilisticProvider(rng)
    renderer = Renderer()
    final_state, _ = run_game(state, provider, renderer)

    home_score = int(final_state.score.get("home", 0))
    away_score = int(final_state.score.get("visitors", 0))
    winner = (game["home_team_id"] if home_score > away_score
              else game["away_team_id"])

    # Persist game result.
    db.execute(
        "UPDATE youth_games SET home_score = ?, away_score = ?, "
        "winner_id = ?, played = 1 WHERE id = ?",
        (home_score, away_score, winner, game_id),
    )

    # Persist per-player stats.
    away_brows = _extract_youth_batter_rows(renderer, int(game["away_team_id"]), away_players)
    home_brows = _extract_youth_batter_rows(renderer, int(game["home_team_id"]), home_players)
    away_prows = _extract_youth_pitcher_rows(final_state, int(game["away_team_id"]),
                                             away_players, away_starter_pid)
    home_prows = _extract_youth_pitcher_rows(final_state, int(game["home_team_id"]),
                                             home_players, home_starter_pid)
    _insert_batter_rows(game_id, away_brows + home_brows)
    _insert_pitcher_rows(game_id, away_prows + home_prows)

    return {
        "game_id":    game_id,
        "home_score": home_score,
        "away_score": away_score,
        "winner":     winner,
        "super_inning": getattr(final_state, "super_inning_number", 0),
    }


# ---------------------------------------------------------------------------
# Box-score read helper for the UI
# ---------------------------------------------------------------------------

def get_box_score(game_id: int) -> dict | None:
    init_youth_sim_schema()
    game = db.fetchone(
        "SELECT yg.*, "
        "       ht.name AS home_name, ht.abbrev AS home_abbrev, "
        "       at.name AS away_name, at.abbrev AS away_abbrev "
        "FROM youth_games yg "
        "LEFT JOIN youth_teams ht ON ht.id = yg.home_team_id "
        "LEFT JOIN youth_teams at ON at.id = yg.away_team_id "
        "WHERE yg.id = ?",
        (game_id,),
    )
    if not game:
        return None
    batters = db.fetchall(
        "SELECT b.*, p.name AS player_name, p.position, p.is_pitcher "
        "FROM game_youth_batter_stats b "
        "JOIN youth_players p ON p.id = b.player_id "
        "WHERE b.game_id = ? "
        "ORDER BY b.team_id, p.is_pitcher, p.position",
        (game_id,),
    )
    pitchers = db.fetchall(
        "SELECT pi.*, p.name AS player_name "
        "FROM game_youth_pitcher_stats pi "
        "JOIN youth_players p ON p.id = pi.player_id "
        "WHERE pi.game_id = ? "
        "ORDER BY pi.team_id, pi.is_starter DESC, pi.outs_recorded DESC",
        (game_id,),
    )
    return {
        "game":     dict(game),
        "batters":  [dict(r) for r in batters],
        "pitchers": [dict(r) for r in pitchers],
    }
