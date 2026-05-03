"""
Task #62 — Season lifecycle helpers.

Provides:
  * archive_current_season(...)   — snapshot standings + leaders + champion
                                     + invariant pass/fail into the
                                     `seasons` / `season_*` tables.
  * run_invariant_harness()       — execute the Task #59 invariant suite
                                     in-process and return (pass, fail, summary).
  * start_multi_season(...)       — start a background N-season run; the
                                     POST endpoint returns immediately and
                                     the dashboard polls multi_season_status()
                                     for progress (current season, games
                                     simmed) and a final redirect.
  * multi_season_status()         — snapshot of the runner state.
  * compute_live_season(...)      — peek at the current (in-progress) DB
                                     state so Season History can show it
                                     alongside archived seasons.

The seasons / season_standings / season_batting_leaders /
season_pitching_leaders tables are intentionally NOT in db.drop_all()'s
DROP list so multi-season history persists across each in-loop reset.
"""
from __future__ import annotations

import datetime as _dt
import threading
import traceback
from typing import Any

from o27v2 import db


# ---------------------------------------------------------------------------
# Invariant harness — run the Task #59 suite in-process.
# ---------------------------------------------------------------------------

def run_invariant_harness() -> tuple[int, int, str]:
    import importlib.util
    import os
    import sys

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    test_path = os.path.join(here, "tests", "test_stat_invariants.py")
    spec = importlib.util.spec_from_file_location("o27_inv_tests", test_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["o27_inv_tests"] = mod
    spec.loader.exec_module(mod)

    played = [r["id"] for r in db.fetchall(
        "SELECT id FROM games WHERE played = 1 ORDER BY id"
    )]
    if not played:
        return (0, 0, "no played games")

    tests = [
        mod.test_invariant_1_phase_outs_cap,
        mod.test_invariant_2_or_reconciliation,
        mod.test_invariant_3_pitcher_batter_cross_check,
        mod.test_invariant_4_os_pct_bound,
        mod.test_invariant_5_w_bound,
        mod.test_invariant_6_pa_identity,
        mod.test_invariant_7a_batter_row_uniqueness,
        mod.test_invariant_7b_pitcher_row_uniqueness,
        mod.test_invariant_8_fip_anchored_to_era,
    ]
    passed = failed = 0
    notes: list[str] = []
    for t in tests:
        try:
            t(played)
            passed += 1
        except AssertionError as e:
            failed += 1
            msg = str(e).splitlines()[0][:240]
            notes.append(f"{t.__name__}: FAIL {msg}")
        except Exception as e:
            failed += 1
            notes.append(f"{t.__name__}: ERROR {type(e).__name__}: {e}")
    return passed, failed, "\n".join(notes)


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

def _snapshot_standings(season_id: int) -> None:
    rows = db.fetchall(
        """SELECT id, league, division, name, abbrev, wins, losses
             FROM teams ORDER BY league, division, wins DESC, losses ASC"""
    )
    rs_ra: dict[int, tuple[int, int]] = {}
    for t in rows:
        played = db.fetchall(
            """SELECT home_team_id, away_team_id, home_score, away_score
                 FROM games
                WHERE played = 1
                  AND (home_team_id = ? OR away_team_id = ?)""",
            (t["id"], t["id"]),
        )
        rs = ra = 0
        for g in played:
            if g["home_team_id"] == t["id"]:
                rs += g["home_score"] or 0
                ra += g["away_score"] or 0
            else:
                rs += g["away_score"] or 0
                ra += g["home_score"] or 0
        rs_ra[t["id"]] = (rs, ra)
    for r in rows:
        rs, ra = rs_ra.get(r["id"], (0, 0))
        db.execute(
            """INSERT OR REPLACE INTO season_standings
               (season_id, league, division, team_name, team_abbrev,
                wins, losses, rs, ra)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (season_id, r["league"], r["division"], r["name"], r["abbrev"],
             r["wins"], r["losses"], rs, ra),
        )


def _snapshot_leaders(season_id: int) -> None:
    from o27v2.web.app import (
        _PSTATS_DEDUP_SQL,
        _aggregate_batter_rows,
        _aggregate_pitcher_rows,
        _pitcher_wl_map,
    )

    games_played = db.fetchone(
        "SELECT COUNT(*) as n FROM games WHERE played = 1"
    )["n"] or 0
    if games_played == 0:
        return

    num_teams = db.fetchone("SELECT COUNT(*) as n FROM teams")["n"] or 2
    games_per_team = max(1, (games_played * 2) // num_teams)
    min_pa   = max(3, games_per_team)
    min_outs = max(3, games_per_team)

    batting = db.fetchall(
        """SELECT p.id as player_id, p.name as player_name,
                  t.abbrev as team_abbrev,
                  COUNT(bs.game_id) as g,
                  SUM(bs.pa) as pa, SUM(bs.ab) as ab, SUM(bs.hits) as h,
                  SUM(bs.doubles) as d2, SUM(bs.triples) as d3, SUM(bs.hr) as hr,
                  SUM(bs.rbi) as rbi, SUM(bs.bb) as bb
             FROM game_batter_stats bs
             JOIN players p ON bs.player_id = p.id
             JOIN teams   t ON bs.team_id = t.id
            GROUP BY p.id
           HAVING SUM(bs.pa) >= ?""",
        (min_pa,),
    )
    _aggregate_batter_rows(batting)

    def _save_batting(category: str, ranked: list[dict]) -> None:
        for i, r in enumerate(ranked[:10], start=1):
            db.execute(
                """INSERT OR REPLACE INTO season_batting_leaders
                   (season_id, category, rank, player_name, team_abbrev,
                    g, pa, ab, h, hr, rbi, bb, avg, obp, slg, ops)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (season_id, category, i, r["player_name"], r["team_abbrev"],
                 r.get("g") or 0, r.get("pa") or 0, r.get("ab") or 0,
                 r.get("h") or 0, r.get("hr") or 0, r.get("rbi") or 0,
                 r.get("bb") or 0,
                 float(r.get("avg") or 0), float(r.get("obp") or 0),
                 float(r.get("slg") or 0), float(r.get("ops") or 0)),
            )

    _save_batting("avg", sorted(batting, key=lambda x: x["avg"], reverse=True))
    _save_batting("hr",  sorted(batting, key=lambda x: x["hr"] or 0, reverse=True))
    _save_batting("rbi", sorted(batting, key=lambda x: x["rbi"] or 0, reverse=True))
    _save_batting("ops", sorted(batting, key=lambda x: x["ops"], reverse=True))

    pitching = db.fetchall(
        f"""SELECT p.id as player_id, p.name as player_name,
                   t.abbrev as team_abbrev,
                   COUNT(ps.game_id) as g,
                   SUM(ps.outs_recorded) as outs,
                   SUM(ps.hits_allowed)  as h,
                   SUM(ps.runs_allowed)  as r,
                   SUM(ps.er)            as er,
                   SUM(ps.bb)            as bb,
                   SUM(ps.k)             as k,
                   SUM(ps.hr_allowed)    as hr_allowed
              FROM {_PSTATS_DEDUP_SQL} ps
              JOIN players p ON ps.player_id = p.id
              JOIN teams   t ON ps.team_id = t.id
             GROUP BY p.id
            HAVING SUM(ps.outs_recorded) >= ?""",
        (min_outs,),
    )
    wl = _pitcher_wl_map()
    _aggregate_pitcher_rows(pitching, wl)

    # Opponent batting average (AVG-against): H / (BF − BB).
    # Approximate batters faced as outs + hits + walks (HBP/SF unavailable
    # on game_pitcher_stats — see follow-up #61); thus BF − BB ≈ outs + H.
    for r in pitching:
        h    = float(r.get("h")    or 0)
        outs = float(r.get("outs") or 0)
        denom = outs + h
        r["oavg"] = (h / denom) if denom > 0 else 0.0

    def _save_pitching(category: str, ranked: list[dict]) -> None:
        for i, r in enumerate(ranked[:10], start=1):
            db.execute(
                """INSERT OR REPLACE INTO season_pitching_leaders
                   (season_id, category, rank, player_name, team_abbrev,
                    g, w, l, outs, er, k, bb, era, fip, whip, oavg)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (season_id, category, i, r["player_name"], r["team_abbrev"],
                 r.get("g") or 0, r.get("w") or 0, r.get("l") or 0,
                 r.get("outs") or 0, r.get("er") or 0,
                 r.get("k") or 0, r.get("bb") or 0,
                 float(r.get("era") or 0), float(r.get("fip") or 0),
                 float(r.get("whip") or 0), float(r.get("oavg") or 0)),
            )

    _save_pitching("w",    sorted(pitching, key=lambda x: x["w"], reverse=True))
    _save_pitching("era",  sorted(pitching, key=lambda x: x["era"]))
    _save_pitching("fip",  sorted(pitching, key=lambda x: x["fip"]))
    _save_pitching("k",    sorted(pitching, key=lambda x: x["k"] or 0, reverse=True))
    _save_pitching("oavg", sorted(pitching, key=lambda x: x["oavg"]))


def _derive_year() -> int | None:
    """Pull the calendar year from the latest played game's date.
    Falls back to today's year if no games or unparseable."""
    row = db.fetchone(
        "SELECT MAX(game_date) AS d FROM games WHERE played = 1"
    )
    if row and row.get("d"):
        try:
            return int(str(row["d"])[:4])
        except Exception:
            pass
    return _dt.date.today().year


def archive_current_season(
    rng_seed: int | None = None,
    config_id: str | None = None,
    started_at: str | None = None,
    run_invariants: bool = True,
) -> int | None:
    games_played = db.fetchone(
        "SELECT COUNT(*) as n FROM games WHERE played = 1"
    )
    if not games_played or not games_played["n"]:
        return None

    teams = db.fetchall(
        "SELECT name, abbrev, wins, losses FROM teams "
        "ORDER BY (wins * 1.0 / NULLIF(wins+losses, 0)) DESC, "
        "wins DESC, losses ASC"
    )
    champ = teams[0] if teams else None
    team_count = len(teams)

    last = db.fetchone("SELECT MAX(season_number) AS n FROM seasons")
    season_number = ((last and last["n"]) or 0) + 1
    year = _derive_year()

    inv_pass = inv_fail = 0
    inv_summary = ""
    if run_invariants:
        try:
            inv_pass, inv_fail, inv_summary = run_invariant_harness()
        except Exception as e:
            inv_summary = f"harness crashed: {e}"

    new_id = db.execute(
        """INSERT INTO seasons
           (season_number, year, rng_seed, config_id, team_count,
            started_at, ended_at,
            champion_team_name, champion_abbrev, champion_w, champion_l,
            games_played, invariant_pass, invariant_fail, invariant_summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (season_number, year, rng_seed, config_id, team_count,
         started_at, _dt.datetime.utcnow().isoformat(timespec="seconds"),
         (champ["name"]   if champ else None),
         (champ["abbrev"] if champ else None),
         (champ["wins"]   if champ else 0),
         (champ["losses"] if champ else 0),
         games_played["n"],
         inv_pass, inv_fail, inv_summary),
    )
    _snapshot_standings(new_id)
    _snapshot_leaders(new_id)
    return new_id


# ---------------------------------------------------------------------------
# Live (in-progress) season peek for Season History.
# ---------------------------------------------------------------------------

def compute_live_season() -> dict[str, Any] | None:
    teams = db.fetchall(
        "SELECT name, abbrev, wins, losses FROM teams "
        "ORDER BY (wins * 1.0 / NULLIF(wins+losses, 0)) DESC, "
        "wins DESC, losses ASC"
    )
    if not teams:
        return None
    games_played = db.fetchone(
        "SELECT COUNT(*) as n FROM games WHERE played = 1"
    )["n"] or 0
    games_total = db.fetchone(
        "SELECT COUNT(*) as n FROM games"
    )["n"] or 0
    last = db.fetchone("SELECT MAX(season_number) AS n FROM seasons")
    next_number = ((last and last["n"]) or 0) + 1
    leader = teams[0]
    return {
        "season_number": next_number,
        "year": _derive_year(),
        "team_count": len(teams),
        "games_played": games_played,
        "games_total": games_total,
        "leader_name": leader["name"],
        "leader_abbrev": leader["abbrev"],
        "leader_w": leader["wins"],
        "leader_l": leader["losses"],
        "complete": (games_total > 0 and games_played >= games_total),
    }


# ---------------------------------------------------------------------------
# Multi-season runner — background thread + polled status endpoint.
# ---------------------------------------------------------------------------

_MULTI_LOCK = threading.Lock()
_MULTI_STATE: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "target_seasons": 0,
    "completed_seasons": 0,
    "current_season_index": 0,           # 1-based index of season being run
    "current_season_number": None,
    "current_phase": "idle",             # idle|seeding|simulating|archiving|done|error
    "current_seed": None,
    "config_id": None,
    "games_simmed_current": 0,           # games played so far in the active season
    "games_simmed_total": 0,             # cumulative across whole run
    "seasons": [],                       # per-season summaries (appended on archive)
    "error": None,
}


def multi_season_status() -> dict[str, Any]:
    with _MULTI_LOCK:
        return {k: (list(v) if isinstance(v, list) else v)
                for k, v in _MULTI_STATE.items()}


def _state_update(**kw) -> None:
    with _MULTI_LOCK:
        _MULTI_STATE.update(kw)


def _tick_games_simmed() -> None:
    """Refresh games_simmed_current by querying the DB. Cheap: COUNT(*)."""
    n = db.fetchone(
        "SELECT COUNT(*) as n FROM games WHERE played = 1"
    )["n"] or 0
    with _MULTI_LOCK:
        _MULTI_STATE["games_simmed_current"] = n


def _run_multi_season_thread(
    n_seasons: int,
    base_seed: int,
    config_id: str,
) -> None:
    """Loop body — never raises out of the thread."""
    from o27v2.league import seed_league
    from o27v2.schedule import seed_schedule
    from o27v2.sim import (
        simulate_through, get_last_scheduled_date,
        get_current_sim_date, resync_sim_clock,
    )

    try:
        for i in range(n_seasons):
            seed = base_seed + i
            started_season = _dt.datetime.utcnow().isoformat(timespec="seconds")
            _state_update(
                current_season_index=i + 1,
                current_seed=seed,
                current_phase="seeding",
                games_simmed_current=0,
            )

            db.drop_all()
            db.init_db()
            seed_league(rng_seed=seed, config_id=config_id)
            seed_schedule(config_id=config_id, rng_seed=seed)
            resync_sim_clock()

            last_date = get_last_scheduled_date()
            if last_date is None:
                raise RuntimeError("seed_schedule produced no games")

            _state_update(current_phase="simulating")

            # Sim in 14-day chunks so the dashboard can show progress.
            start_clk = get_current_sim_date() or last_date
            start_date = _dt.date.fromisoformat(start_clk)
            end_date   = _dt.date.fromisoformat(last_date)
            cur = start_date
            while cur <= end_date:
                step_to = min(end_date, cur + _dt.timedelta(days=14))
                simulate_through(step_to.isoformat())
                _tick_games_simmed()
                cur = step_to + _dt.timedelta(days=1)

            _state_update(current_phase="archiving")
            sid = archive_current_season(
                rng_seed=seed,
                config_id=config_id,
                started_at=started_season,
                run_invariants=True,
            )
            row = db.fetchone(
                "SELECT season_number, year, champion_team_name, champion_abbrev, "
                "champion_w, champion_l, invariant_pass, invariant_fail "
                "FROM seasons WHERE id = ?", (sid,))
            games_simmed = db.fetchone(
                "SELECT COUNT(*) as n FROM games WHERE played = 1"
            )["n"] or 0
            summary = {
                "season_id": sid,
                "season_number": (row or {}).get("season_number"),
                "year":          (row or {}).get("year"),
                "seed": seed,
                "games_simmed": games_simmed,
                "champion_name":   (row or {}).get("champion_team_name"),
                "champion_abbrev": (row or {}).get("champion_abbrev"),
                "champion_w":      (row or {}).get("champion_w") or 0,
                "champion_l":      (row or {}).get("champion_l") or 0,
                "invariant_pass":  (row or {}).get("invariant_pass") or 0,
                "invariant_fail":  (row or {}).get("invariant_fail") or 0,
            }
            with _MULTI_LOCK:
                _MULTI_STATE["seasons"].append(summary)
                _MULTI_STATE["completed_seasons"] = i + 1
                _MULTI_STATE["games_simmed_total"] += games_simmed
                _MULTI_STATE["current_season_number"] = summary["season_number"]

        _state_update(
            running=False,
            current_phase="done",
            finished_at=_dt.datetime.utcnow().isoformat(timespec="seconds"),
        )
    except Exception as e:
        tb = traceback.format_exc(limit=4)
        _state_update(
            running=False,
            current_phase="error",
            error=f"{type(e).__name__}: {e}\n{tb}",
            finished_at=_dt.datetime.utcnow().isoformat(timespec="seconds"),
        )


def start_multi_season(
    n_seasons: int,
    base_seed: int = 42,
    config_id: str = "30teams",
) -> tuple[bool, str]:
    """Spawn the runner thread. Returns (started, message). N is clamped 1-10."""
    n_seasons = max(1, min(int(n_seasons), 10))
    with _MULTI_LOCK:
        if _MULTI_STATE.get("running"):
            return False, "A multi-season run is already in progress."
        _MULTI_STATE.update({
            "running": True,
            "started_at": _dt.datetime.utcnow().isoformat(timespec="seconds"),
            "finished_at": None,
            "target_seasons": n_seasons,
            "completed_seasons": 0,
            "current_season_index": 0,
            "current_season_number": None,
            "current_phase": "starting",
            "current_seed": None,
            "config_id": config_id,
            "games_simmed_current": 0,
            "games_simmed_total": 0,
            "seasons": [],
            "error": None,
        })
    t = threading.Thread(
        target=_run_multi_season_thread,
        args=(n_seasons, base_seed, config_id),
        daemon=True,
    )
    t.start()
    return True, f"Started multi-season run for {n_seasons} season(s)."
