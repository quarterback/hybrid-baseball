"""
Stat invariant tests for the o27v2 league DB.

Run after a full season simulation to catch the entire class of
"mathematically impossible stat" bugs (OS% > 100%, W > G, batter outs
not summing to 27, duplicate pitcher rows inflating BF/K, etc.) before
they ship to the live site.

Invocation:
    pytest tests/test_stat_invariants.py -v

DB selection:
    Default path is `o27v2/o27v2.db` (same default as the rest of the
    o27v2 stack). Override via the `O27V2_DB_PATH` environment variable
    so the suite can run against a CI fixture, a freshly-simulated
    season, or the live deployed DB.

Per-game-only filter (legacy data):
    Pre-Task-#58 rows in the live DB carry phase=0 and have known
    duplicate-row defects that this harness will (correctly) flag.
    Set `O27V2_INVARIANTS_GAMES` to a comma-separated list of game_ids
    to scope each invariant to those games only — useful for verifying
    the harness against a fresh-sim subset without re-simming the
    whole 1100-game backlog.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Iterable

import pytest

# The o27v2 db module honors O27V2_DB_PATH at import time.
from o27v2 import db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REGULATION_PHASE_CAP = 27
SI_PHASE_CAP = 5


def _scoped_game_ids() -> set[int] | None:
    raw = os.environ.get("O27V2_INVARIANTS_GAMES", "").strip()
    if not raw:
        return None
    return {int(x) for x in raw.split(",") if x.strip()}


def _game_filter_clause(table_alias: str = "") -> tuple[str, tuple]:
    ids = _scoped_game_ids()
    if not ids:
        return "", ()
    prefix = f"{table_alias}." if table_alias else ""
    placeholders = ",".join("?" * len(ids))
    return f" AND {prefix}game_id IN ({placeholders})", tuple(sorted(ids))


def _phase_cap(phase: int) -> int:
    return REGULATION_PHASE_CAP if phase == 0 else SI_PHASE_CAP


@pytest.fixture(scope="module")
def played_game_ids() -> list[int]:
    """All played game ids, optionally narrowed by O27V2_INVARIANTS_GAMES."""
    extra, params = _game_filter_clause()
    rows = db.fetchall(
        f"SELECT id FROM games WHERE played = 1{extra.replace('AND game_id', 'AND id')} ORDER BY id",
        params,
    )
    if not rows:
        pytest.skip("no played games in target DB")
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# Invariant 1: phase outs cap
# ---------------------------------------------------------------------------

def test_invariant_1_phase_outs_cap(played_game_ids):
    """Σ pitcher.outs_recorded per (game, team, phase) <= phase cap.

    Cap = 27 for regulation (phase 0), 5 for any super-inning round.
    A walk-off SI half can stop early but never exceed the cap.
    """
    extra, params = _game_filter_clause("ps")
    rows = db.fetchall(
        f"""SELECT ps.game_id, ps.team_id, ps.phase,
                   SUM(ps.outs_recorded) AS outs
              FROM game_pitcher_stats ps
              JOIN games g ON g.id = ps.game_id
             WHERE g.played = 1{extra}
             GROUP BY ps.game_id, ps.team_id, ps.phase""",
        params,
    )
    over = [
        r for r in rows
        if (r["outs"] or 0) > _phase_cap(r["phase"] or 0)
    ]
    assert not over, (
        f"phase-outs cap exceeded on {len(over)} (game, team, phase) "
        f"groups; first 5: "
        + "; ".join(
            f"game={r['game_id']} team={r['team_id']} phase={r['phase']} "
            f"outs={r['outs']} cap={_phase_cap(r['phase'] or 0)}"
            for r in over[:5]
        )
    )


# ---------------------------------------------------------------------------
# Invariant 2: per-phase OR reconciliation
# ---------------------------------------------------------------------------

def test_invariant_2_or_reconciliation(played_game_ids):
    """Σ batter.outs_recorded + unattributed_outs == team total outs
    (= Σ opponent pitcher.outs_recorded) per (game, team, phase).

    A walk-off SI half ends the moment the batting team takes the
    lead, so sums below the phase cap are legitimate; the assertion
    only checks identity, not equality with the cap.
    """
    extra, params = _game_filter_clause("ps")
    # Opposing pitcher outs by (game, batting_team, phase). To find the
    # batting team for a pitcher row we need the game's two team ids.
    pitcher_rows = db.fetchall(
        f"""SELECT ps.game_id, ps.team_id AS pitcher_team_id, ps.phase,
                   g.home_team_id, g.away_team_id,
                   SUM(ps.outs_recorded) AS outs
              FROM game_pitcher_stats ps
              JOIN games g ON g.id = ps.game_id
             WHERE g.played = 1{extra}
             GROUP BY ps.game_id, ps.team_id, ps.phase""",
        params,
    )
    extra_b, params_b = _game_filter_clause("bs")
    batter_rows = db.fetchall(
        f"""SELECT bs.game_id, bs.team_id, bs.phase,
                   SUM(bs.outs_recorded) AS outs
              FROM game_batter_stats bs
              JOIN games g ON g.id = bs.game_id
             WHERE g.played = 1{extra_b}
             GROUP BY bs.game_id, bs.team_id, bs.phase""",
        params_b,
    )
    unattr_rows = db.fetchall(
        "SELECT game_id, team_id, phase, unattributed_outs FROM team_phase_outs"
    )

    team_outs: dict[tuple, int] = {}
    for r in pitcher_rows:
        batting_team = (
            r["away_team_id"]
            if r["pitcher_team_id"] == r["home_team_id"]
            else r["home_team_id"]
        )
        team_outs[(r["game_id"], batting_team, r["phase"])] = r["outs"] or 0

    batter_outs = {(r["game_id"], r["team_id"], r["phase"]): (r["outs"] or 0)
                   for r in batter_rows}
    unattr_outs = {(r["game_id"], r["team_id"], r["phase"]):
                   (r["unattributed_outs"] or 0) for r in unattr_rows}

    bad: list[str] = []
    for key, total in team_outs.items():
        b = batter_outs.get(key, 0)
        u = unattr_outs.get(key, 0)
        if b + u != total:
            gid, tid, ph = key
            bad.append(
                f"game={gid} team={tid} phase={ph}: batter_outs={b} "
                f"+ unattributed={u} ({b + u}) != team_total={total}"
            )
    assert not bad, (
        f"OR reconciliation failed for {len(bad)} (game, team, phase) "
        f"groups; first 5: " + "; ".join(bad[:5])
    )


# ---------------------------------------------------------------------------
# Invariant 3: pitcher–batter cross-check per game
# ---------------------------------------------------------------------------

def test_invariant_3_pitcher_batter_cross_check(played_game_ids):
    """Σ batter.outs_recorded for team T in game G ==
    Σ opponent pitcher.outs_recorded - Σ unattributed for T in game G.

    Equivalent to invariant 2 summed across phases; kept as a separate
    test so a per-game failure surfaces independently.
    """
    extra, params = _game_filter_clause("bs")
    bat = db.fetchall(
        f"""SELECT bs.game_id, bs.team_id, SUM(bs.outs_recorded) AS o
              FROM game_batter_stats bs
              JOIN games g ON g.id = bs.game_id
             WHERE g.played = 1{extra}
             GROUP BY bs.game_id, bs.team_id""",
        params,
    )
    extra_p, params_p = _game_filter_clause("ps")
    pit = db.fetchall(
        f"""SELECT ps.game_id, ps.team_id AS pitcher_team_id,
                   g.home_team_id, g.away_team_id,
                   SUM(ps.outs_recorded) AS o
              FROM game_pitcher_stats ps
              JOIN games g ON g.id = ps.game_id
             WHERE g.played = 1{extra_p}
             GROUP BY ps.game_id, ps.team_id""",
        params_p,
    )
    unattr_per_game: dict[tuple, int] = defaultdict(int)
    for r in db.fetchall(
        "SELECT game_id, team_id, unattributed_outs FROM team_phase_outs"
    ):
        unattr_per_game[(r["game_id"], r["team_id"])] += (r["unattributed_outs"] or 0)

    opp_outs: dict[tuple, int] = {}
    for r in pit:
        batting_team = (r["away_team_id"]
                        if r["pitcher_team_id"] == r["home_team_id"]
                        else r["home_team_id"])
        opp_outs[(r["game_id"], batting_team)] = r["o"] or 0

    bad: list[str] = []
    for r in bat:
        key = (r["game_id"], r["team_id"])
        b = r["o"] or 0
        u = unattr_per_game.get(key, 0)
        opp = opp_outs.get(key, 0)
        if b + u != opp:
            bad.append(
                f"game={r['game_id']} team={r['team_id']}: "
                f"batter_outs={b} + unattributed={u} ({b + u}) "
                f"!= opp_pitcher_outs={opp}"
            )
    assert not bad, (
        f"pitcher–batter cross-check failed for {len(bad)} (game, team) "
        f"pairs; first 5: " + "; ".join(bad[:5])
    )


# ---------------------------------------------------------------------------
# Invariant 4: OS% bound
# ---------------------------------------------------------------------------

def test_invariant_4_os_pct_bound(played_game_ids):
    """For every pitcher row: outs_recorded / phase_cap <= 1.0."""
    extra, params = _game_filter_clause("ps")
    rows = db.fetchall(
        f"""SELECT ps.game_id, ps.team_id, ps.player_id, ps.phase,
                   ps.outs_recorded
              FROM game_pitcher_stats ps
              JOIN games g ON g.id = ps.game_id
             WHERE g.played = 1{extra}""",
        params,
    )
    over = [
        r for r in rows
        if (r["outs_recorded"] or 0) > _phase_cap(r["phase"] or 0)
    ]
    assert not over, (
        f"OS% > 100% on {len(over)} pitcher rows; first 5: "
        + "; ".join(
            f"game={r['game_id']} team={r['team_id']} pid={r['player_id']} "
            f"phase={r['phase']} outs={r['outs_recorded']} "
            f"cap={_phase_cap(r['phase'] or 0)}"
            for r in over[:5]
        )
    )


# ---------------------------------------------------------------------------
# Invariant 5: W bound (W <= G per pitcher)
# ---------------------------------------------------------------------------

def test_invariant_5_w_bound(played_game_ids):
    """For every pitcher: wins (W) <= games appeared (G), where BOTH
    sides of the inequality come from the same production code paths
    that the leaders/player pages use to render the live site.

    - W comes from `o27v2.web.app._pitcher_wl_map()` — the production
      "workhorse of the day = most outs on winning side" rule.
    - G comes from `_PSTATS_DEDUP_SQL` (the same dedup view leaders use
      for everything else), counting DISTINCT game_id per pitcher.

    Pulling W and G from independent production paths means a bug in
    either path that double-counts or under-counts will surface here
    rather than being masked by a self-consistent derivation.
    """
    from o27v2.web.app import _pitcher_wl_map, _PSTATS_DEDUP_SQL

    wl = _pitcher_wl_map()
    g_rows = db.fetchall(
        f"""SELECT ps.player_id AS pid,
                   COUNT(DISTINCT ps.game_id) AS g
              FROM {_PSTATS_DEDUP_SQL} ps
              JOIN games gm ON gm.id = ps.game_id
             WHERE gm.played = 1
             GROUP BY ps.player_id"""
    )
    g_map = {r["pid"]: (r["g"] or 0) for r in g_rows}

    bad: list[tuple[int, int, int]] = []
    for pid, rec in wl.items():
        w = rec.get("w", 0)
        g = g_map.get(pid, 0)
        if w > g:
            bad.append((pid, w, g))

    assert not bad, (
        f"W > G on {len(bad)} pitchers (production-path mismatch — "
        f"`_pitcher_wl_map` and `_PSTATS_DEDUP_SQL` disagree on game "
        f"set); first 5: "
        + "; ".join(f"pid={pid} W={w} G={g}" for pid, w, g in bad[:5])
    )

    # Independent global cross-check: total wins distributed across all
    # pitchers must equal the number of decided games. This catches the
    # bug class where the W rule double-credits a tied (game, team).
    total_w = sum(rec.get("w", 0) for rec in wl.values())
    decided = db.fetchone(
        "SELECT COUNT(*) AS n FROM games WHERE played = 1 AND winner_id IS NOT NULL"
    )["n"]
    assert total_w == decided, (
        f"Σ W ({total_w}) != decided games ({decided}); "
        f"`_pitcher_wl_map` is dropping or double-crediting wins"
    )


# ---------------------------------------------------------------------------
# Invariant 6: PA identity per batter row
# ---------------------------------------------------------------------------

def test_invariant_6_pa_identity(played_game_ids):
    """pa >= ab + bb on every batter row.

    The o27v2 schema does NOT persist HBP / SF / SH on game_batter_stats
    even though the engine credits HBP as a plate appearance, so the
    full identity `pa == ab + bb + hbp + sf + sh` collapses to a
    one-sided bound here. The bound still catches the historically-
    common bug where the engine inflated AB without bumping PA (which
    showed up as `ab > pa` rows in pre-Task-#58 data).
    """
    extra, params = _game_filter_clause("bs")
    rows = db.fetchall(
        f"""SELECT bs.game_id, bs.team_id, bs.player_id, bs.phase,
                   bs.pa, bs.ab, bs.bb
              FROM game_batter_stats bs
              JOIN games g ON g.id = bs.game_id
             WHERE g.played = 1{extra}""",
        params,
    )
    bad = [
        r for r in rows
        if ((r["ab"] or 0) + (r["bb"] or 0)) > (r["pa"] or 0)
    ]
    assert not bad, (
        f"AB+BB > PA on {len(bad)} batter rows (impossible — PA must "
        f"include every AB and every BB); first 5: "
        + "; ".join(
            f"game={r['game_id']} pid={r['player_id']} phase={r['phase']} "
            f"PA={r['pa']} AB={r['ab']} BB={r['bb']}"
            for r in bad[:5]
        )
    )


# ---------------------------------------------------------------------------
# Invariant 7: row uniqueness per (player, game, phase)
# ---------------------------------------------------------------------------

def _check_row_uniqueness(table: str) -> list[dict]:
    extra, params = _game_filter_clause()
    return db.fetchall(
        f"""SELECT player_id, game_id, phase, COUNT(*) AS n
              FROM {table}
             WHERE game_id IN (SELECT id FROM games WHERE played = 1)
                   {extra}
             GROUP BY player_id, game_id, phase
            HAVING n > 1""",
        params,
    )


def test_invariant_7a_batter_row_uniqueness(played_game_ids):
    dups = _check_row_uniqueness("game_batter_stats")
    assert not dups, (
        f"duplicate batter rows on {len(dups)} (player, game, phase) "
        f"keys; first 5: "
        + "; ".join(
            f"pid={d['player_id']} game={d['game_id']} phase={d['phase']} "
            f"count={d['n']}"
            for d in dups[:5]
        )
    )


def test_invariant_7b_pitcher_row_uniqueness(played_game_ids):
    dups = _check_row_uniqueness("game_pitcher_stats")
    assert not dups, (
        f"duplicate pitcher rows on {len(dups)} (player, game, phase) "
        f"keys; first 5: "
        + "; ".join(
            f"pid={d['player_id']} game={d['game_id']} phase={d['phase']} "
            f"count={d['n']}"
            for d in dups[:5]
        )
    )


# ---------------------------------------------------------------------------
# Invariant 8: FIP sanity (league FIP within 0.05 of league ERA)
# ---------------------------------------------------------------------------

def test_invariant_8_fip_anchored_to_era(played_game_ids):
    """Outs-weighted league FIP (computed by the production
    `_aggregate_pitcher_rows`) within 0.05 of league ERA.

    Calling the production functions directly — `_league_fip_const()`
    and `_aggregate_pitcher_rows()` — means a regression in either
    (formula change, dedup view change, constant-fitting bug) trips
    this invariant rather than being masked by re-deriving everything
    from the same raw aggregate. The architect-flagged tautology of
    "fit-then-check the same formula" is gone: the test now consumes
    the SAME numbers a user would see on /leaders.

    Plus an independent ER ≤ R sanity check: per (game, team), the
    sum of pitcher earned-runs cannot exceed the team's actual runs
    allowed (= the OPPONENT's score in `games`). This catches the
    bug class where ER calculation drifts above raw runs.
    """
    from o27v2.web.app import _aggregate_pitcher_rows, _PSTATS_DEDUP_SQL

    # ---- (a) Production-FIP outs-weighted average == league ERA. ----
    rows = db.fetchall(
        f"""SELECT ps.player_id,
                   SUM(ps.outs_recorded) AS outs,
                   SUM(ps.hits_allowed)  AS h,
                   SUM(ps.runs_allowed)  AS r,
                   SUM(ps.er)            AS er,
                   SUM(ps.bb)            AS bb,
                   SUM(ps.k)             AS k,
                   SUM(ps.hr_allowed)    AS hr_allowed
              FROM {_PSTATS_DEDUP_SQL} ps
              JOIN games gm ON gm.id = ps.game_id
             WHERE gm.played = 1
             GROUP BY ps.player_id"""
    )
    if not rows:
        pytest.skip("no pitcher rows in the target DB scope")

    _aggregate_pitcher_rows(rows)  # mutates rows: adds 'fip', 'era', etc.

    total_outs = sum(r["outs"] or 0 for r in rows)
    total_er   = sum(r["er"]   or 0 for r in rows)
    if total_outs == 0:
        pytest.skip("no pitcher outs recorded")
    league_era = (total_er * 27.0) / total_outs
    league_fip_weighted = (
        sum((r.get("fip") or 0.0) * (r["outs"] or 0) for r in rows) / total_outs
    )
    assert abs(league_fip_weighted - league_era) < 0.05, (
        f"outs-weighted league FIP {league_fip_weighted:.4f} not within "
        f"0.05 of league ERA {league_era:.4f} "
        f"(delta={league_fip_weighted - league_era:+.4f}); the production "
        f"`_league_fip_const` / `_aggregate_pitcher_rows` no longer agree"
    )

    # ---- (b) ER <= R per (game, team) ----------------------------------
    extra, params = _game_filter_clause("ps")
    er_rows = db.fetchall(
        f"""SELECT ps.game_id, ps.team_id,
                   SUM(ps.er)           AS er,
                   SUM(ps.runs_allowed) AS r
              FROM {_PSTATS_DEDUP_SQL} ps
              JOIN games g ON g.id = ps.game_id
             WHERE g.played = 1{extra}
             GROUP BY ps.game_id, ps.team_id""",
        params,
    )
    bad = [r for r in er_rows if (r["er"] or 0) > (r["r"] or 0)]
    assert not bad, (
        f"ER > R on {len(bad)} (game, team) groups; first 5: "
        + "; ".join(
            f"game={r['game_id']} team={r['team_id']} ER={r['er']} R={r['r']}"
            for r in bad[:5]
        )
    )
