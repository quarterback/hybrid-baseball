"""
Task #62 — Season lifecycle helpers.

Provides:
  * archive_current_season(...)   — snapshot standings + leaders + champion
                                     + invariant pass/fail into the
                                     `seasons` / `season_*` tables.
  * run_invariant_harness()       — execute the Task #59 invariant suite
                                     in-process and return (pass, fail, summary).
  * start_multi_season(...)       — spawn a background thread that loops
                                     N seasons end-to-end (sim → archive →
                                     reset → reseed) and writes progress
                                     into `_MULTI_STATE` for the status
                                     endpoint to poll.
  * multi_season_status()         — return a snapshot of the runner state.

The seasons / season_standings / season_batting_leaders /
season_pitching_leaders tables are intentionally NOT in db.drop_all()'s
DROP list so multi-season history persists across each in-loop reset.
"""
from __future__ import annotations

import datetime as _dt
import threading
import time
import traceback
from typing import Any

from o27v2 import db


# ---------------------------------------------------------------------------
# Invariant harness — run the Task #59 suite in-process.
# ---------------------------------------------------------------------------

def run_invariant_harness() -> tuple[int, int, str]:
    """Execute every invariant test against the current DB.

    Returns (passed, failed, summary). Imports test functions directly so
    we don't pay subprocess startup cost inside the multi-season loop.
    """
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
        """SELECT league, division, name, abbrev, wins, losses
             FROM teams ORDER BY league, division, wins DESC, losses ASC"""
    )
    # RS / RA per team from games
    rs_ra: dict[int, tuple[int, int]] = {}
    for t in db.fetchall("SELECT id FROM teams"):
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
    # Build name → (rs,ra) map via separate query
    teams_full = db.fetchall("SELECT id, name FROM teams")
    name_to_id = {t["name"]: t["id"] for t in teams_full}
    for r in rows:
        rs, ra = rs_ra.get(name_to_id.get(r["name"], -1), (0, 0))
        db.execute(
            """INSERT OR REPLACE INTO season_standings
               (season_id, league, division, team_name, team_abbrev,
                wins, losses, rs, ra)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (season_id, r["league"], r["division"], r["name"], r["abbrev"],
             r["wins"], r["losses"], rs, ra),
        )


def _snapshot_leaders(season_id: int) -> None:
    """Snapshot top-10 per category using the production helpers so the
    archived numbers match what the live /leaders page rendered."""
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

    def _save_pitching(category: str, ranked: list[dict]) -> None:
        for i, r in enumerate(ranked[:10], start=1):
            db.execute(
                """INSERT OR REPLACE INTO season_pitching_leaders
                   (season_id, category, rank, player_name, team_abbrev,
                    g, w, l, outs, er, k, bb, era, fip, whip)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (season_id, category, i, r["player_name"], r["team_abbrev"],
                 r.get("g") or 0, r.get("w") or 0, r.get("l") or 0,
                 r.get("outs") or 0, r.get("er") or 0,
                 r.get("k") or 0, r.get("bb") or 0,
                 float(r.get("era") or 0), float(r.get("fip") or 0),
                 float(r.get("whip") or 0)),
            )

    _save_pitching("w",   sorted(pitching, key=lambda x: x["w"], reverse=True))
    _save_pitching("era", sorted(pitching, key=lambda x: x["era"]))
    _save_pitching("fip", sorted(pitching, key=lambda x: x["fip"]))
    _save_pitching("k",   sorted(pitching, key=lambda x: x["k"] or 0, reverse=True))


def archive_current_season(
    rng_seed: int | None = None,
    config_id: str | None = None,
    started_at: str | None = None,
    run_invariants: bool = True,
) -> int | None:
    """Snapshot the current DB state into the seasons archive.

    Returns the new seasons.id, or None if there's nothing to archive
    (e.g. zero teams or zero played games).
    """
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

    # Next season number
    last = db.fetchone("SELECT MAX(season_number) AS n FROM seasons")
    season_number = ((last and last["n"]) or 0) + 1

    inv_pass = inv_fail = 0
    inv_summary = ""
    if run_invariants:
        try:
            inv_pass, inv_fail, inv_summary = run_invariant_harness()
        except Exception as e:
            inv_summary = f"harness crashed: {e}"

    new_id = db.execute(
        """INSERT INTO seasons
           (season_number, rng_seed, config_id, team_count,
            started_at, ended_at,
            champion_team_name, champion_abbrev, champion_w, champion_l,
            games_played, invariant_pass, invariant_fail, invariant_summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (season_number, rng_seed, config_id, team_count,
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
# Multi-season runner — background thread + polled status.
# ---------------------------------------------------------------------------

_MULTI_LOCK = threading.Lock()
_MULTI_STATE: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "target_seasons": 0,
    "completed_seasons": 0,
    "current_season_number": None,
    "current_phase": "idle",     # idle | seeding | simulating | archiving | done | error
    "current_progress_pct": 0,
    "log": [],                    # list[str] — most-recent first
    "error": None,
    "last_season_id": None,
}


def multi_season_status() -> dict[str, Any]:
    with _MULTI_LOCK:
        return dict(_MULTI_STATE)


def _state_update(**kw) -> None:
    with _MULTI_LOCK:
        _MULTI_STATE.update(kw)


def _state_log(msg: str) -> None:
    with _MULTI_LOCK:
        log = _MULTI_STATE.get("log") or []
        ts = _dt.datetime.utcnow().strftime("%H:%M:%S")
        log.insert(0, f"[{ts}] {msg}")
        _MULTI_STATE["log"] = log[:60]


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
            number = (db.fetchone(
                "SELECT MAX(season_number) AS n FROM seasons"
            )["n"] or 0) + 1
            started = _dt.datetime.utcnow().isoformat(timespec="seconds")
            _state_update(
                current_season_number=number,
                current_phase="seeding",
                current_progress_pct=0,
            )
            _state_log(f"Season {number}: seeding (seed={seed}, config={config_id})")

            db.drop_all()
            db.init_db()
            seed_league(rng_seed=seed, config_id=config_id)
            seed_schedule(config_id=config_id, rng_seed=seed)
            resync_sim_clock()

            last = get_last_scheduled_date()
            if last is None:
                raise RuntimeError("seed_schedule produced no games")

            _state_update(current_phase="simulating", current_progress_pct=5)
            _state_log(f"Season {number}: simulating to {last}")

            # Sim in 14-day chunks so the UI gets progress ticks.
            start_clk = get_current_sim_date() or last
            start_date = _dt.date.fromisoformat(start_clk)
            end_date   = _dt.date.fromisoformat(last)
            total_days = max(1, (end_date - start_date).days + 1)
            cur = start_date
            while cur <= end_date:
                step_to = min(end_date, cur + _dt.timedelta(days=14))
                simulate_through(step_to.isoformat())
                done_days = (step_to - start_date).days + 1
                pct = 5 + int((done_days / total_days) * 85)
                _state_update(current_progress_pct=max(5, min(90, pct)))
                cur = step_to + _dt.timedelta(days=1)

            _state_update(current_phase="archiving", current_progress_pct=92)
            _state_log(f"Season {number}: archiving + invariants")
            sid = archive_current_season(
                rng_seed=seed,
                config_id=config_id,
                started_at=started,
                run_invariants=True,
            )
            row = db.fetchone(
                "SELECT champion_team_name, invariant_pass, invariant_fail "
                "FROM seasons WHERE id = ?", (sid,))
            champ = (row or {}).get("champion_team_name") or "?"
            ip = (row or {}).get("invariant_pass") or 0
            ifail = (row or {}).get("invariant_fail") or 0
            _state_log(
                f"Season {number}: champion={champ} · invariants {ip} pass / {ifail} fail"
            )
            with _MULTI_LOCK:
                _MULTI_STATE["completed_seasons"] = i + 1
                _MULTI_STATE["last_season_id"] = sid
                _MULTI_STATE["current_progress_pct"] = 100

        _state_update(
            running=False,
            current_phase="done",
            finished_at=_dt.datetime.utcnow().isoformat(timespec="seconds"),
        )
        _state_log(f"All {n_seasons} season(s) complete.")
    except Exception as e:
        tb = traceback.format_exc(limit=4)
        _state_update(
            running=False,
            current_phase="error",
            error=f"{type(e).__name__}: {e}",
            finished_at=_dt.datetime.utcnow().isoformat(timespec="seconds"),
        )
        _state_log(f"ERROR: {e}\n{tb}")


def start_multi_season(
    n_seasons: int,
    base_seed: int = 42,
    config_id: str = "30teams",
) -> tuple[bool, str]:
    """Spawn the runner thread. Returns (started, message)."""
    n_seasons = max(1, min(int(n_seasons), 20))
    with _MULTI_LOCK:
        if _MULTI_STATE.get("running"):
            return False, "A multi-season run is already in progress."
        _MULTI_STATE.update({
            "running": True,
            "started_at": _dt.datetime.utcnow().isoformat(timespec="seconds"),
            "finished_at": None,
            "target_seasons": n_seasons,
            "completed_seasons": 0,
            "current_season_number": None,
            "current_phase": "starting",
            "current_progress_pct": 0,
            "log": [],
            "error": None,
            "last_season_id": None,
        })
    t = threading.Thread(
        target=_run_multi_season_thread,
        args=(n_seasons, base_seed, config_id),
        daemon=True,
    )
    t.start()
    return True, f"Started multi-season run for {n_seasons} season(s)."
