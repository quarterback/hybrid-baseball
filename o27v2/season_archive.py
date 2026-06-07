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

def _locate_invariant_test_file() -> str:
    """Find the Task #59 invariant suite. The file lives at
    <repo>/tests/test_stat_invariants.py. We search a list of candidates
    (relative to this module) and raise loudly if none exist so a missing
    suite never silently archives 0/0."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))           # .../o27v2
    repo = os.path.dirname(here)                                # .../<repo>
    candidates = [
        os.path.join(repo, "tests", "test_stat_invariants.py"),  # actual location
        os.path.join(here, "tests", "test_stat_invariants.py"),  # fallback if moved
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(
        "Invariant suite not found. Looked in:\n  " + "\n  ".join(candidates)
    )


def run_invariant_harness() -> tuple[int, int, str]:
    import importlib.util
    import sys

    test_path = _locate_invariant_test_file()
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
        _REG_PSTATS_DEDUP_SQL,
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
                  t.id as team_id, t.abbrev as team_abbrev,
                  COUNT(bs.game_id) as g,
                  SUM(bs.pa) as pa, SUM(bs.ab) as ab, SUM(bs.hits) as h,
                  SUM(bs.doubles) as d2, SUM(bs.triples) as d3, SUM(bs.hr) as hr,
                  SUM(bs.rbi) as rbi, SUM(bs.bb) as bb
             FROM (SELECT * FROM game_batter_stats WHERE COALESCE(is_playoff,0) = 0) bs
             JOIN players p ON bs.player_id = p.id
             JOIN teams   t ON bs.team_id = t.id
            GROUP BY p.id
           HAVING SUM(bs.pa) >= ?""",
        (min_pa,),
    )
    _aggregate_batter_rows(batting)

    # Stamp WPA / LI onto the rows so the persisted leaders carry the
    # win-probability columns alongside the rate-stat columns. WPA needs
    # the empirical WP model rebuilt here (one walk through game_pa_log).
    try:
        from o27v2.analytics.wpa import build_player_wpa
        _wpa_data = build_player_wpa()
    except Exception:
        _wpa_data = {"by_batter": {}, "by_pitcher": {}}
    for r in batting:
        pid = r.get("player_id")
        wb = _wpa_data["by_batter"].get(pid) if pid is not None else None
        r["wpa"]    = wb["wpa"]    if wb else 0.0
        r["li_avg"] = wb["li_avg"] if wb else 0.0

    def _save_batting(category: str, ranked: list[dict]) -> None:
        for i, r in enumerate(ranked[:10], start=1):
            db.execute(
                """INSERT OR REPLACE INTO season_batting_leaders
                   (season_id, category, rank, player_name, team_abbrev,
                    g, pa, ab, h, hr, rbi, bb, avg, obp, slg, ops,
                    wrc_plus, wpa, li_avg, ops_plus)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?)""",
                (season_id, category, i, r["player_name"], r["team_abbrev"],
                 r.get("g") or 0, r.get("pa") or 0, r.get("ab") or 0,
                 r.get("h") or 0, r.get("hr") or 0, r.get("rbi") or 0,
                 r.get("bb") or 0,
                 float(r.get("avg") or 0), float(r.get("obp") or 0),
                 float(r.get("slg") or 0), float(r.get("ops") or 0),
                 float(r.get("wrc_plus") or 100),
                 float(r.get("wpa") or 0),
                 float(r.get("li_avg") or 0),
                 float(r.get("ops_plus") or 100)),
            )

    _save_batting("avg", sorted(batting, key=lambda x: x["avg"], reverse=True))
    _save_batting("hr",  sorted(batting, key=lambda x: x["hr"] or 0, reverse=True))
    _save_batting("rbi", sorted(batting, key=lambda x: x["rbi"] or 0, reverse=True))
    _save_batting("ops", sorted(batting, key=lambda x: x["ops"], reverse=True))
    # OPS+ substitutes for wRC+ here: wRC+'s wOBA weights are derived from
    # game_pa_log, which fast-sim (detail="lite") skips, collapsing wRC+ to
    # one constant for every batter. OPS+ is OPS-relative (box-score only),
    # so it ranks correctly in both fast and full archives.
    _save_batting("ops_plus",
                  sorted(batting, key=lambda x: x.get("ops_plus") or 0, reverse=True))
    # WPA dropped: it's derived from game_pa_log, which fast-sim
    # (detail="lite") skips, so it reads +0.00 for every batter.

    # NOTE: this MUST select the arc-bucketed columns (er_arc*/bf_arc*/etc.)
    # and the true-outcome columns that `_aggregate_pitcher_rows` consumes.
    # Omitting them (the original bug) made the aggregator read every arc as
    # 0, so archived wERA collapsed to 0.00 and wERA+ fell back to 100 for
    # every pitcher. The arc columns are written even by fast-sim
    # (detail="lite"); only the per-PA log is skipped there.
    pitching = db.fetchall(
        f"""SELECT p.id as player_id, p.name as player_name,
                   t.id as team_id, t.abbrev as team_abbrev,
                   COUNT(ps.game_id) as g,
                   SUM(ps.outs_recorded) as outs,
                   SUM(ps.batters_faced) as bf,
                   SUM(ps.hits_allowed)  as h,
                   SUM(ps.runs_allowed)  as r,
                   SUM(ps.er)            as er,
                   SUM(ps.bb)            as bb,
                   SUM(ps.k)             as k,
                   SUM(ps.hr_allowed)    as hr_allowed,
                   COALESCE(SUM(ps.hbp_allowed),0)   as hbp_allowed,
                   COALESCE(SUM(ps.unearned_runs),0) as unearned_runs,
                   COALESCE(SUM(ps.fo_induced),0)    as fo_induced,
                   COALESCE(SUM(ps.pitches),0)       as pitches,
                   COALESCE(SUM(ps.er_arc1),0) as er_arc1, COALESCE(SUM(ps.er_arc2),0) as er_arc2, COALESCE(SUM(ps.er_arc3),0) as er_arc3,
                   COALESCE(SUM(ps.k_arc1),0)  as k_arc1,  COALESCE(SUM(ps.k_arc2),0)  as k_arc2,  COALESCE(SUM(ps.k_arc3),0)  as k_arc3,
                   COALESCE(SUM(ps.fo_arc1),0) as fo_arc1, COALESCE(SUM(ps.fo_arc2),0) as fo_arc2, COALESCE(SUM(ps.fo_arc3),0) as fo_arc3,
                   COALESCE(SUM(ps.bf_arc1),0) as bf_arc1, COALESCE(SUM(ps.bf_arc2),0) as bf_arc2, COALESCE(SUM(ps.bf_arc3),0) as bf_arc3,
                   COALESCE(SUM(ps.singles_allowed),0) as singles_allowed,
                   COALESCE(SUM(ps.doubles_allowed),0) as doubles_allowed,
                   COALESCE(SUM(ps.triples_allowed),0) as triples_allowed,
                   COALESCE(SUM(ps.is_starter),0) as gs
              FROM {_REG_PSTATS_DEDUP_SQL} ps
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

    # Stamp WPA / LI onto pitcher rows from the same model built above.
    for r in pitching:
        pid = r.get("player_id")
        wp = _wpa_data["by_pitcher"].get(pid) if pid is not None else None
        r["wpa"]    = wp["wpa"]    if wp else 0.0
        r["li_avg"] = wp["li_avg"] if wp else 0.0

    def _save_pitching(category: str, ranked: list[dict]) -> None:
        for i, r in enumerate(ranked[:10], start=1):
            # Schema's `era` / `fip` / `whip` columns are reused as the
            # wERA / xRA / GSc-avg slots for go-forward archives. Old
            # seasons keep their original ERA/FIP/WHIP semantics; new
            # seasons store wERA / xRA / GSc-avg under the same column
            # names. (Pre-fix versions of this writer tried to read
            # `xfip` off the aggregated row, which `_aggregate_pitcher_rows`
            # doesn't stamp — only `xra` is — so the writer crashed
            # with KeyError on the xfip sort.)
            db.execute(
                """INSERT OR REPLACE INTO season_pitching_leaders
                   (season_id, category, rank, player_name, team_abbrev,
                    g, w, l, outs, er, k, bb, era, fip, whip, oavg,
                    wera_plus, gsc_index, wpa, li_avg,
                    fip_dips, kbb, whip_v, k9)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?)""",
                (season_id, category, i, r["player_name"], r["team_abbrev"],
                 r.get("g") or 0, r.get("w") or 0, r.get("l") or 0,
                 r.get("outs") or 0, r.get("er") or 0,
                 r.get("k") or 0, r.get("bb") or 0,
                 float(r.get("werra") or 0), float(r.get("xra") or 0),
                 float(r.get("gsc_avg") or 0), float(r.get("oavg") or 0),
                 float(r.get("wera_plus") or 100),
                 float(r.get("gsc_index") or 100),
                 float(r.get("wpa") or 0),
                 float(r.get("li_avg") or 0),
                 float(r.get("fip") or 0),
                 float(r.get("kbb") or 0),
                 float(r.get("whip") or 0),
                 float(r.get("k9") or 0)),
            )

    _save_pitching("w",     sorted(pitching, key=lambda x: x["w"], reverse=True))
    _save_pitching("werra", sorted(pitching, key=lambda x: x["werra"]))
    _save_pitching("xra",   sorted(pitching, key=lambda x: x.get("xra") or 0))
    _save_pitching("k",     sorted(pitching, key=lambda x: x["k"] or 0, reverse=True))
    _save_pitching("oavg",  sorted(pitching, key=lambda x: x["oavg"]))
    _save_pitching("wera_plus",
                   sorted(pitching, key=lambda x: x.get("wera_plus") or 0, reverse=True))
    _save_pitching("gsc_index",
                   sorted(pitching, key=lambda x: x.get("gsc_index") or 0, reverse=True))
    # Outs-based DIPS leaderboards (replace the pa_log-derived WPA board,
    # which reads 0 for every pitcher in fast-sim archives). FIP / WHIP sort
    # ascending (lower is better); K/BB / K/9 sort descending.
    _save_pitching("fip",
                   sorted(pitching, key=lambda x: x.get("fip") if x.get("fip") else 999.0))
    _save_pitching("kbb",
                   sorted(pitching, key=lambda x: x.get("kbb") or 0, reverse=True))
    _save_pitching("whip",
                   sorted(pitching, key=lambda x: x.get("whip") if x.get("whip") else 999.0))
    _save_pitching("k9",
                   sorted(pitching, key=lambda x: x.get("k9") or 0, reverse=True))


def _snapshot_career_lines(season_id: int, season_number: int,
                           year: int | None) -> None:
    """Persist EVERY qualified player's full season line into
    player_career_lines before the per-game stats get wiped at rollover.

    This is the Hall of Fame's source of truth for career totals — the
    season_* leader tables only keep the top 10 per category, so without
    this snapshot a non-leader's career is unrecoverable after the offseason
    reset. Mirrors the qualification thresholds _snapshot_leaders uses.
    """
    from o27v2.web.app import (
        _PSTATS_DEDUP_SQL,
        _REG_PSTATS_DEDUP_SQL,
        _aggregate_batter_rows,
        _aggregate_pitcher_rows,
        _pitcher_wl_map,
        _league_baselines,
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
    baselines = _league_baselines()

    batting = db.fetchall(
        """SELECT p.id as player_id, p.name as player_name, p.age as age,
                  p.position as position, t.id as team_id, t.abbrev as team_abbrev,
                  COUNT(bs.game_id) as g,
                  SUM(bs.pa) as pa, SUM(bs.ab) as ab, SUM(bs.hits) as h,
                  SUM(bs.doubles) as d2, SUM(bs.triples) as d3, SUM(bs.hr) as hr,
                  SUM(bs.runs) as r, SUM(bs.rbi) as rbi, SUM(bs.bb) as bb,
                  SUM(bs.k) as k, COALESCE(SUM(bs.sb),0) as sb
             FROM (SELECT * FROM game_batter_stats WHERE COALESCE(is_playoff,0) = 0) bs
             JOIN players p ON bs.player_id = p.id
             JOIN teams   t ON bs.team_id = t.id
            GROUP BY p.id
           HAVING SUM(bs.pa) >= ?""",
        (min_pa,),
    )
    _aggregate_batter_rows(batting, baselines=baselines)
    for r in batting:
        db.execute(
            """INSERT OR REPLACE INTO player_career_lines
               (season_id, season_number, year, player_id, player_name,
                team_abbrev, is_pitcher, position, age,
                g, pa, ab, h, d2, d3, hr, r, rbi, bb, k, sb,
                avg, obp, slg, ops, wrc_plus)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?)""",
            (season_id, season_number, year, r["player_id"], r["player_name"],
             r["team_abbrev"], r.get("position") or "", r.get("age"),
             r.get("g") or 0, r.get("pa") or 0, r.get("ab") or 0,
             r.get("h") or 0, r.get("d2") or 0, r.get("d3") or 0,
             r.get("hr") or 0, r.get("r") or 0, r.get("rbi") or 0,
             r.get("bb") or 0, r.get("k") or 0, r.get("sb") or 0,
             float(r.get("avg") or 0), float(r.get("obp") or 0),
             float(r.get("slg") or 0), float(r.get("ops") or 0),
             float(r.get("wrc_plus") or 100)),
        )

    pitching = db.fetchall(
        f"""SELECT p.id as player_id, p.name as player_name, p.age as age,
                   p.position as position, t.id as team_id, t.abbrev as team_abbrev,
                   COUNT(ps.game_id) as g,
                   SUM(ps.outs_recorded) as outs,
                   SUM(ps.hits_allowed)  as h,
                   SUM(ps.er)            as er,
                   SUM(ps.bb)            as bb,
                   SUM(ps.k)             as k,
                   COALESCE(SUM(ps.er_arc1),0) AS er_arc1, COALESCE(SUM(ps.er_arc2),0) AS er_arc2, COALESCE(SUM(ps.er_arc3),0) AS er_arc3,
                   COALESCE(SUM(ps.k_arc1),0)  AS k_arc1,  COALESCE(SUM(ps.k_arc2),0)  AS k_arc2,  COALESCE(SUM(ps.k_arc3),0)  AS k_arc3,
                   COALESCE(SUM(ps.fo_arc1),0) AS fo_arc1, COALESCE(SUM(ps.fo_arc2),0) AS fo_arc2, COALESCE(SUM(ps.fo_arc3),0) AS fo_arc3,
                   COALESCE(SUM(ps.bf_arc1),0) AS bf_arc1, COALESCE(SUM(ps.bf_arc2),0) AS bf_arc2, COALESCE(SUM(ps.bf_arc3),0) AS bf_arc3,
                   COALESCE(SUM(ps.is_starter),0) AS gs
              FROM {_REG_PSTATS_DEDUP_SQL} ps
              JOIN players p ON ps.player_id = p.id
              JOIN teams   t ON ps.team_id = t.id
             GROUP BY p.id
            HAVING SUM(ps.outs_recorded) >= ?""",
        (min_outs,),
    )
    wl = _pitcher_wl_map()
    _aggregate_pitcher_rows(pitching, wl, baselines=baselines)
    for r in pitching:
        outs = float(r.get("outs") or 0)
        innings = outs / 3.0
        whip = ((float(r.get("bb") or 0) + float(r.get("h") or 0)) / innings) \
            if innings > 0 else 0.0
        rec = wl.get(r["player_id"], {}) if wl else {}
        db.execute(
            """INSERT OR REPLACE INTO player_career_lines
               (season_id, season_number, year, player_id, player_name,
                team_abbrev, is_pitcher, position, age,
                p_g, w, l, outs, er, p_k, p_bb, p_h, wera, whip, wera_plus)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?)""",
            (season_id, season_number, year, r["player_id"], r["player_name"],
             r["team_abbrev"], r.get("position") or "", r.get("age"),
             r.get("g") or 0, rec.get("w") or 0, rec.get("l") or 0,
             int(outs), r.get("er") or 0, r.get("k") or 0, r.get("bb") or 0,
             r.get("h") or 0,
             float(r.get("werra") or 0), float(whip),
             float(r.get("wera_plus") or 100)),
        )


def _snapshot_transactions(season_id: int) -> None:
    """Phase E archive — copy the season's transactions log into
    `season_transactions` keyed by season_id. The live `transactions`
    table gets wiped at season reset; this snapshot preserves the
    auction signs, FA signings, college sign-throughs, post-auction
    reconciliation trades, and any in-season trades for the AAR.

    Player name + team abbrev are denormalised so the row stays
    meaningful even after rosters churn.
    """
    db.execute(
        """INSERT INTO season_transactions
             (season_id, game_date, event_type, team_id, team_abbrev,
              player_id, player_name, detail)
           SELECT ?,
                  tx.game_date, tx.event_type,
                  tx.team_id, tm.abbrev,
                  tx.player_id, p.name,
                  tx.detail
             FROM transactions tx
             LEFT JOIN teams tm   ON tm.id = tx.team_id
             LEFT JOIN players p  ON p.id  = tx.player_id
            ORDER BY tx.id""",
        (season_id,),
    )


def _snapshot_auction_results(season_id: int) -> None:
    """Phase E archive — copy auction lot results into
    `season_auction_results`. Auction settings (keepers, purse, demand
    scale) aren't here — they're inferable from team_budgets and the
    league config; this table just preserves the per-lot ledger so an
    archived season's auction page can still render the top sales,
    Vickrey winners, and any post-clear sellback trades.

    Player position + winner abbrev denormalised so the row survives
    roster wipes.
    """
    rows = db.fetchall(
        """SELECT ar.lot_order, ar.player_id, p.name AS player_name,
                  p.position AS player_position, ar.player_overall,
                  ar.winner_team_id, tm.abbrev AS winner_abbrev,
                  ar.winning_bid, ar.second_bid, ar.price,
                  ar.traded_to_team_id, tt.abbrev AS traded_to_abbrev,
                  ar.trade_price
             FROM auction_results ar
             LEFT JOIN players p  ON p.id  = ar.player_id
             LEFT JOIN teams   tm ON tm.id = ar.winner_team_id
             LEFT JOIN teams   tt ON tt.id = ar.traded_to_team_id
            ORDER BY ar.lot_order"""
    )
    if not rows:
        return
    db.executemany(
        """INSERT INTO season_auction_results
             (season_id, lot_order, player_id, player_name,
              player_position, player_overall,
              winner_team_id, winner_abbrev,
              winning_bid, second_bid, price,
              traded_to_team_id, traded_to_abbrev, trade_price)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(season_id, r["lot_order"], r["player_id"], r["player_name"],
          r["player_position"], r["player_overall"],
          r["winner_team_id"], r["winner_abbrev"],
          r["winning_bid"], r["second_bid"], r["price"],
          r["traded_to_team_id"], r["traded_to_abbrev"], r["trade_price"])
         for r in rows]
    )


def _snapshot_player_lines(season_id: int) -> None:
    """Persist every player's full batting/pitching season line so career
    (multi-season) leaderboards can aggregate by a stable player_id. The
    season_*_leaders tables only keep top-10 lists keyed by name; these rows
    keep the complete field keyed by the player row that survives history-
    mode season rollovers. Team / league are taken from the player's current
    club at archive time."""
    from o27v2.web.app import _PSTATS_DEDUP_SQL, _REG_PSTATS_DEDUP_SQL, _pitcher_wl_map

    bat = db.fetchall(
        """SELECT bs.player_id AS player_id, p.name AS player_name,
                  t.abbrev AS team_abbrev, t.league AS league,
                  COUNT(DISTINCT bs.game_id) AS g,
                  SUM(bs.pa) AS pa, SUM(bs.ab) AS ab, SUM(bs.runs) AS r,
                  SUM(bs.hits) AS h, SUM(bs.doubles) AS d2,
                  SUM(bs.triples) AS d3, SUM(bs.hr) AS hr,
                  SUM(bs.rbi) AS rbi, SUM(bs.bb) AS bb, SUM(bs.k) AS k,
                  SUM(bs.sb) AS sb, SUM(bs.hbp) AS hbp,
                  COALESCE(SUM(bs.risp_pa),0) AS risp_pa,
                  COALESCE(SUM(bs.risp_ab),0) AS risp_ab,
                  COALESCE(SUM(bs.risp_h),0)  AS risp_h,
                  COALESCE(SUM(bs.risp_2b),0) AS risp_2b,
                  COALESCE(SUM(bs.risp_3b),0) AS risp_3b,
                  COALESCE(SUM(bs.risp_hr),0) AS risp_hr,
                  COALESCE(SUM(bs.risp_bb),0) AS risp_bb,
                  COALESCE(SUM(bs.risp_hbp),0) AS risp_hbp,
                  COALESCE(SUM(bs.risp_rbi),0) AS risp_rbi,
                  COALESCE(SUM(bs.sh),0)        AS sh,
                  COALESCE(SUM(bs.bunt_att),0)  AS bunt_att,
                  COALESCE(SUM(bs.bunt_hits),0) AS bunt_hits,
                  COALESCE(SUM(bs.sqz),0)       AS sqz,
                  COALESCE(SUM(bs.sqz_rbi),0)   AS sqz_rbi
             FROM (SELECT * FROM game_batter_stats WHERE COALESCE(is_playoff,0) = 0) bs
             JOIN players p ON bs.player_id = p.id
             LEFT JOIN teams t ON p.team_id = t.id
            GROUP BY bs.player_id
           HAVING SUM(bs.pa) > 0"""
    )
    for r in bat:
        db.execute(
            """INSERT OR REPLACE INTO season_player_batting
               (season_id, player_id, player_name, team_abbrev, league,
                g, pa, ab, r, h, doubles, triples, hr, rbi, bb, k, sb, hbp,
                risp_pa, risp_ab, risp_h, risp_2b, risp_3b, risp_hr,
                risp_bb, risp_hbp, risp_rbi,
                sh, bunt_att, bunt_hits, sqz, sqz_rbi)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (season_id, r["player_id"], r["player_name"], r["team_abbrev"],
             r["league"], r["g"] or 0, r["pa"] or 0, r["ab"] or 0, r["r"] or 0,
             r["h"] or 0, r["d2"] or 0, r["d3"] or 0, r["hr"] or 0,
             r["rbi"] or 0, r["bb"] or 0, r["k"] or 0, r["sb"] or 0,
             r["hbp"] or 0,
             r["risp_pa"], r["risp_ab"], r["risp_h"], r["risp_2b"],
             r["risp_3b"], r["risp_hr"], r["risp_bb"], r["risp_hbp"],
             r["risp_rbi"], r["sh"], r["bunt_att"], r["bunt_hits"],
             r["sqz"], r["sqz_rbi"]),
        )

    wl = _pitcher_wl_map()
    pit = db.fetchall(
        f"""SELECT ps.player_id AS player_id, p.name AS player_name,
                   t.abbrev AS team_abbrev, t.league AS league,
                   COUNT(DISTINCT ps.game_id) AS g,
                   SUM(ps.is_starter) AS gs,
                   SUM(ps.outs_recorded) AS outs,
                   SUM(ps.hits_allowed) AS h, SUM(ps.runs_allowed) AS r,
                   SUM(ps.er) AS er, SUM(ps.bb) AS bb, SUM(ps.k) AS k,
                   SUM(ps.hr_allowed) AS hr,
                   COALESCE(SUM(ps.ir_inherited),0)  AS ir_inherited,
                   COALESCE(SUM(ps.ir_scored),0)     AS ir_scored,
                   COALESCE(SUM(ps.terminal_outs),0) AS terminal_outs,
                   COALESCE(SUM(ps.quality_finish),0) AS quality_finish,
                   COALESCE(SUM(ps.lead_entries),0)  AS lead_entries,
                   COALESCE(SUM(ps.lead_held),0)     AS lead_held
              FROM {_REG_PSTATS_DEDUP_SQL} ps
              JOIN players p ON ps.player_id = p.id
              LEFT JOIN teams t ON p.team_id = t.id
             GROUP BY ps.player_id
            HAVING SUM(ps.outs_recorded) > 0"""
    )
    for r in pit:
        rec = wl.get(r["player_id"], {"w": 0, "l": 0})
        db.execute(
            """INSERT OR REPLACE INTO season_player_pitching
               (season_id, player_id, player_name, team_abbrev, league,
                g, gs, w, l, outs, h, r, er, bb, k, hr,
                ir_inherited, ir_scored, terminal_outs, quality_finish,
                lead_entries, lead_held)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?)""",
            (season_id, r["player_id"], r["player_name"], r["team_abbrev"],
             r["league"], r["g"] or 0, r["gs"] or 0, rec["w"], rec["l"],
             r["outs"] or 0, r["h"] or 0, r["r"] or 0, r["er"] or 0,
             r["bb"] or 0, r["k"] or 0, r["hr"] or 0,
             r["ir_inherited"], r["ir_scored"], r["terminal_outs"],
             r["quality_finish"], r["lead_entries"], r["lead_held"]),
        )


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


def get_active_league_meta() -> tuple[int | None, str | None]:
    """Read the rng_seed/config_id used to build the *current* live league
    from sim_meta. Returns (None, None) if not recorded."""
    s = db.fetchone("SELECT value FROM sim_meta WHERE key = 'league_seed'")
    c = db.fetchone("SELECT value FROM sim_meta WHERE key = 'league_config'")
    seed = int(s["value"]) if s and s.get("value") is not None else None
    cfg  = c["value"] if c else None
    return seed, cfg


def set_active_league_meta(rng_seed: int, config_id: str) -> None:
    """Persist the seed/config used to build the live league so future
    archives can attribute the season to the correct setup. Also clears
    the 'already archived' marker since a fresh league has nothing
    archived yet."""
    db.execute(
        "INSERT OR REPLACE INTO sim_meta (key, value) VALUES ('league_seed', ?)",
        (str(int(rng_seed)),),
    )
    db.execute(
        "INSERT OR REPLACE INTO sim_meta (key, value) VALUES ('league_config', ?)",
        (str(config_id),),
    )
    db.execute("DELETE FROM sim_meta WHERE key = 'current_season_archived_id'")


def get_current_archived_season_id() -> int | None:
    """Returns the seasons.id for the live league if it's already been
    archived, else None. Used to prevent duplicate archive rows."""
    row = db.fetchone(
        "SELECT value FROM sim_meta WHERE key = 'current_season_archived_id'"
    )
    if row and row.get("value"):
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return None
    return None


def _mark_current_season_archived(season_id: int) -> None:
    db.execute(
        "INSERT OR REPLACE INTO sim_meta (key, value) "
        "VALUES ('current_season_archived_id', ?)",
        (str(int(season_id)),),
    )


def archive_current_season(
    rng_seed: int | None = None,
    config_id: str | None = None,
    started_at: str | None = None,
    run_invariants: bool = True,
) -> int | None:
    # Guard against duplicate archive rows for the same live league. If the
    # current season has already been archived, return that existing id
    # instead of writing a second snapshot.
    existing = get_current_archived_season_id()
    if existing is not None:
        row = db.fetchone("SELECT id FROM seasons WHERE id = ?", (existing,))
        if row:
            return existing
        # Stale marker (row was deleted) — fall through and re-archive.

    games_played = db.fetchone(
        "SELECT COUNT(*) as n FROM games WHERE played = 1"
    )
    if not games_played or not games_played["n"]:
        return None

    # Default to the seed/config that produced the *current* league so the
    # archived row reflects the season we're closing — not whatever new seed
    # the caller is about to seed next.
    if rng_seed is None or config_id is None:
        meta_seed, meta_cfg = get_active_league_meta()
        if rng_seed   is None: rng_seed   = meta_seed
        if config_id  is None: config_id  = meta_cfg

    teams = db.fetchall(
        "SELECT id, name, abbrev, wins, losses FROM teams "
        "ORDER BY (wins * 1.0 / NULLIF(wins+losses, 0)) DESC, "
        "wins DESC, losses ASC"
    )
    team_count = len(teams)

    # Champion: the postseason winner when a bracket was played (World Series
    # winner, or the lone league's champion). Fall back to the best
    # regular-season team only for the soccer model (postseason disabled) or
    # when no champion has been crowned yet — the table winner is the title.
    champ = teams[0] if teams else None
    try:
        from o27v2 import playoffs as _po
        won = _po.champion()
        if won and won.get("winner_team_id"):
            by_id = {t["id"]: t for t in teams}
            champ = by_id.get(won["winner_team_id"], champ)
    except Exception:
        pass

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
    _snapshot_player_lines(new_id)
    # Phase E archive — transactions + auction ledger. Wrapped so an
    # archive bug doesn't abort the rest of the season-close.
    try:
        _snapshot_transactions(new_id)
        _snapshot_auction_results(new_id)
    except Exception:
        traceback.print_exc()
    # Hall of Fame: snapshot every qualified player's full season line (the
    # only surviving source of career totals once the offseason wipes the
    # per-game stats) and then evaluate inductions. Wrapped so a HOF bug
    # never aborts the season archive itself.
    try:
        _snapshot_career_lines(new_id, season_number, year)
        from o27v2 import hof
        hof.run_inductions(season_number, year)
    except Exception:
        traceback.print_exc()
    _mark_current_season_archived(new_id)
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
    "detail": "lite",                    # "lite"|"full" per-game sim detail
    "games_simmed_current": 0,           # games played so far in the active season
    "games_simmed_total": 0,             # cumulative across whole run
    "seasons": [],                       # per-season summaries (appended on archive)
    "error": None,
}


def multi_season_status() -> dict[str, Any]:
    with _MULTI_LOCK:
        snap = {k: (list(v) if isinstance(v, list) else v)
                for k, v in _MULTI_STATE.items()}
    # Derive throughput + ETA so the dashboard can show live progress.
    games_per_sec = 0.0
    eta_seconds: int | None = None
    started = snap.get("started_at")
    total_done = snap.get("games_simmed_total", 0) or 0
    if started and total_done > 0:
        try:
            end = (snap.get("finished_at")
                   and _dt.datetime.fromisoformat(snap["finished_at"])) \
                  or _dt.datetime.utcnow()
            elapsed = (end - _dt.datetime.fromisoformat(started)).total_seconds()
            if elapsed > 0:
                games_per_sec = round(total_done / elapsed, 2)
                seasons_left = max(0, (snap.get("target_seasons", 0) or 0)
                                   - (snap.get("completed_seasons", 0) or 0))
                done_seasons = snap.get("completed_seasons", 0) or 0
                if games_per_sec > 0 and done_seasons > 0 and seasons_left > 0:
                    avg_games_per_season = total_done / done_seasons
                    eta_seconds = int(
                        (avg_games_per_season * seasons_left) / games_per_sec
                    )
        except (ValueError, TypeError):
            pass
    snap["games_per_sec"] = games_per_sec
    snap["eta_seconds"] = eta_seconds
    return snap


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
    detail: str = "lite",
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
            set_active_league_meta(seed, config_id)
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
                simulate_through(step_to.isoformat(), detail=detail)
                _tick_games_simmed()
                cur = step_to + _dt.timedelta(days=1)

            # Drain the playoff bracket. Regular-season chunks above stop
            # at `last_date` (= last regular-season date), but playoff
            # initiation and post-game series scheduling insert games
            # dated AFTER that — each round's games appear only once the
            # prior round's series resolve. Loop until the schedule stops
            # extending; otherwise archive_current_season would snapshot
            # a season with no champion.
            from o27v2.sim import is_season_complete
            prev_target: str | None = None
            drain_safety = 40
            while drain_safety > 0:
                drain_safety -= 1
                if is_season_complete():
                    break
                target = get_last_scheduled_date()
                if target is None or target == prev_target:
                    break
                simulate_through(target, detail=detail)
                _tick_games_simmed()
                prev_target = target

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
    detail: str = "lite",
) -> tuple[bool, str]:
    """Spawn the runner thread. Returns (started, message). N is clamped 1-10.

    `detail` ("lite"|"full") is forwarded to the per-game sim. Defaults to
    "lite" — multi-season runs only keep end-of-season snapshots, so the
    per-PA logs / play-by-play text are discarded on the next reset anyway;
    skipping those writes is a free speedup with no effect on the archived
    standings / leaders.
    """
    n_seasons = max(1, min(int(n_seasons), 10))
    detail = "full" if detail == "full" else "lite"
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
            "detail": detail,
            "games_simmed_current": 0,
            "games_simmed_total": 0,
            "seasons": [],
            "error": None,
        })
    t = threading.Thread(
        target=_run_multi_season_thread,
        args=(n_seasons, base_seed, config_id, detail),
        daemon=True,
    )
    t.start()
    mode_label = "fast" if detail == "lite" else "full-detail"
    return True, f"Started {mode_label} multi-season run for {n_seasons} season(s)."


# ---------------------------------------------------------------------------
# Pre-simulated history — carry-forward runner (Phase B).
#
# Unlike the multi-season runner above (which drop_all()s and reseeds a fresh
# league every season — independent snapshots, used as the invariant test
# bed), the history runner seeds the league ONCE and then plays season after
# season with the SAME franchises and players. Between seasons it ages and
# develops every roster via development.run_offseason and advances the
# calendar year, so a freshly-built league acquires a believable, connected
# backstory: persisting teams, players who rise and decline, and an
# accumulating record of champions, standings and statistical leaders.
# ---------------------------------------------------------------------------

_HISTORY_LOCK = threading.Lock()
_HISTORY_STATE: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "target_seasons": 0,
    "completed_seasons": 0,
    "current_season_index": 0,
    "current_year": None,
    "current_phase": "idle",      # idle|seeding|simulating|archiving|aging|done|error
    "config_id": None,
    "detail": "lite",
    "games_simmed_current": 0,
    "games_simmed_total": 0,
    "seasons": [],
    "error": None,
}


def history_status() -> dict[str, Any]:
    with _HISTORY_LOCK:
        snap = {k: (list(v) if isinstance(v, list) else v)
                for k, v in _HISTORY_STATE.items()}
    games_per_sec = 0.0
    started = snap.get("started_at")
    total = snap.get("games_simmed_total", 0) or 0
    if started and total > 0:
        try:
            end = (snap.get("finished_at")
                   and _dt.datetime.fromisoformat(snap["finished_at"])) \
                  or _dt.datetime.utcnow()
            elapsed = (end - _dt.datetime.fromisoformat(started)).total_seconds()
            if elapsed > 0:
                games_per_sec = round(total / elapsed, 2)
        except (ValueError, TypeError):
            pass
    snap["games_per_sec"] = games_per_sec
    return snap


def _history_state_update(**kw) -> None:
    with _HISTORY_LOCK:
        _HISTORY_STATE.update(kw)


def _reset_for_next_history_season() -> None:
    """Clear the prior season's games, per-game stats and playoff bracket,
    and zero team W/L — while KEEPING players, teams, and the persistent
    season_* archive tables. This is the carry-forward counterpart to
    db.drop_all(): it resets the playable surface for a new season without
    destroying roster continuity or the accumulated history.

    Child rows (stats / logs referencing games) are deleted before the
    games rows so foreign keys resolve cleanly.
    """
    for tbl in (
        "game_pa_log", "game_pitcher_stats", "game_batter_stats",
        "team_phase_outs", "game_pbp", "game_scoring_events",
        "playoff_series", "games", "transactions",
    ):
        try:
            db.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    db.execute("UPDATE teams SET wins = 0, losses = 0")
    db.execute(
        "DELETE FROM sim_meta "
        "WHERE key IN ('sim_date', 'current_season_archived_id')"
    )


def _run_history_thread(
    n_seasons: int,
    base_seed: int,
    config_id: str,
    detail: str,
) -> None:
    """Loop body for the carry-forward history runner. Never raises out."""
    from o27v2.league import seed_league, get_config
    from o27v2.schedule import seed_schedule
    from o27v2.development import run_offseason
    from o27v2.sim import (
        simulate_through, get_last_scheduled_date, resync_sim_clock,
        is_season_complete,
    )

    def _tick() -> None:
        n = db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")["n"] or 0
        with _HISTORY_LOCK:
            _HISTORY_STATE["games_simmed_current"] = n

    try:
        # Seed the league ONCE — no per-season drop_all.
        _history_state_update(current_phase="seeding", current_season_index=0)
        db.drop_all()
        db.init_db()
        seed_league(rng_seed=base_seed, config_id=config_id)
        set_active_league_meta(base_seed, config_id)

        base_cfg = get_config(config_id)
        base_year = int(base_cfg.get("season_year", 2026))

        for i in range(n_seasons):
            year = base_year + i
            schedule_seed = base_seed + i
            started_season = _dt.datetime.utcnow().isoformat(timespec="seconds")
            _history_state_update(
                current_season_index=i + 1,
                current_year=year,
                current_phase="seeding",
                games_simmed_current=0,
            )

            # Reset the playable surface for every season after the first
            # (the freshly-seeded league already has an empty games table).
            if i > 0:
                _reset_for_next_history_season()

            # Schedule this season under its own year so each archived
            # season gets a distinct calendar year (_derive_year reads the
            # latest played game date). A per-season schedule seed varies the
            # matchup order; the empty games table means seed_schedule always
            # regenerates here.
            cfg = dict(base_cfg)
            cfg["season_year"] = year
            seed_schedule(config_id=config_id, rng_seed=schedule_seed, config=cfg)
            resync_sim_clock()

            last_date = get_last_scheduled_date()
            if last_date is None:
                raise RuntimeError("seed_schedule produced no games")

            _history_state_update(current_phase="simulating")
            simulate_through(last_date, detail=detail)
            _tick()

            # Drain the playoff bracket (same pattern as the multi-season runner).
            prev_target: str | None = None
            drain_safety = 40
            while drain_safety > 0:
                drain_safety -= 1
                if is_season_complete():
                    break
                target = get_last_scheduled_date()
                if target is None or target == prev_target:
                    break
                simulate_through(target, detail=detail)
                _tick()
                prev_target = target

            # Archive. Clear the duplicate-archive guard first so each season
            # in the lineage gets its own snapshot row.
            db.execute("DELETE FROM sim_meta WHERE key = 'current_season_archived_id'")
            _history_state_update(current_phase="archiving")
            sid = archive_current_season(
                rng_seed=schedule_seed, config_id=config_id,
                started_at=started_season, run_invariants=True,
            )

            # Age + develop every roster for the next season (carry-forward).
            _history_state_update(current_phase="aging")
            try:
                run_offseason(season=i + 1, rng_seed=schedule_seed)
            except Exception:
                pass  # never let an offseason bug abort the whole lineage

            row = db.fetchone(
                "SELECT season_number, year, champion_team_name, champion_abbrev, "
                "champion_w, champion_l, invariant_pass, invariant_fail "
                "FROM seasons WHERE id = ?", (sid,)) if sid else None
            games_simmed = db.fetchone(
                "SELECT COUNT(*) as n FROM games WHERE played = 1")["n"] or 0
            summary = {
                "season_id": sid,
                "season_number": (row or {}).get("season_number"),
                "year": (row or {}).get("year") or year,
                "seed": schedule_seed,
                "games_simmed": games_simmed,
                "champion_name": (row or {}).get("champion_team_name"),
                "champion_abbrev": (row or {}).get("champion_abbrev"),
                "champion_w": (row or {}).get("champion_w") or 0,
                "champion_l": (row or {}).get("champion_l") or 0,
                "invariant_pass": (row or {}).get("invariant_pass") or 0,
                "invariant_fail": (row or {}).get("invariant_fail") or 0,
            }
            with _HISTORY_LOCK:
                _HISTORY_STATE["seasons"].append(summary)
                _HISTORY_STATE["completed_seasons"] = i + 1
                _HISTORY_STATE["games_simmed_total"] += games_simmed

        _history_state_update(
            running=False, current_phase="done",
            finished_at=_dt.datetime.utcnow().isoformat(timespec="seconds"),
        )
    except Exception as e:
        tb = traceback.format_exc(limit=4)
        _history_state_update(
            running=False, current_phase="error",
            error=f"{type(e).__name__}: {e}\n{tb}",
            finished_at=_dt.datetime.utcnow().isoformat(timespec="seconds"),
        )


def start_history(
    n_seasons: int,
    base_seed: int = 42,
    config_id: str = "30teams",
    detail: str = "lite",
) -> tuple[bool, str]:
    """Spawn the carry-forward history runner. Returns (started, message).

    Builds a fresh league, then plays `n_seasons` consecutive seasons with
    the SAME franchises/players — aging and developing rosters between
    seasons — so the league ends up with a connected, browsable past. N is
    clamped 1-20. `detail` defaults to "lite" (fast)."""
    n_seasons = max(1, min(int(n_seasons), 20))
    detail = "full" if detail == "full" else "lite"
    with _HISTORY_LOCK:
        if _HISTORY_STATE.get("running"):
            return False, "A pre-sim history run is already in progress."
        _HISTORY_STATE.update({
            "running": True,
            "started_at": _dt.datetime.utcnow().isoformat(timespec="seconds"),
            "finished_at": None,
            "target_seasons": n_seasons,
            "completed_seasons": 0,
            "current_season_index": 0,
            "current_year": None,
            "current_phase": "starting",
            "config_id": config_id,
            "detail": detail,
            "games_simmed_current": 0,
            "games_simmed_total": 0,
            "seasons": [],
            "error": None,
        })
    # Guard against colliding with the multi-season runner (both reseed/reset
    # the live DB).
    if _MULTI_STATE.get("running"):
        _history_state_update(running=False, current_phase="idle")
        return False, "A multi-season run is already in progress."
    t = threading.Thread(
        target=_run_history_thread,
        args=(n_seasons, base_seed, config_id, detail),
        daemon=True,
    )
    t.start()
    return True, f"Building {n_seasons} season(s) of league history."
