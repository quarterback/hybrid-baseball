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
SI_PHASE_CAP = 3   # super-innings are normal 3-out innings


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


# Declared Seconds: per-game phase>=1 cap.
#
# In a seconds round the cap is the batting team's banked outs — and BOTH
# the batting and fielding (pitcher) team's phase>=1 rows are bounded by
# that value since they belong to the same half.
#
# Edge case: when a seconds round ends in a tie, the engine then fires SI
# rounds AFTER seconds and bumps super_inning_number past seconds_phase_number
# so the two don't share a phase index. The SI rounds still write phase>=1
# rows (just at a different number). To keep this cap helper safe, we widen
# the cap to MAX(banked, SI_PHASE_CAP=5) for any game that ran both.
def _load_seconds_caps() -> dict[int, int]:
    """Return {game_id: phase>=1 cap} for every game with seconds activity."""
    try:
        rows = db.fetchall(
            "SELECT id, away_seconds_used, home_seconds_used, super_inning "
            "FROM games"
        )
    except Exception:
        return {}
    out: dict[int, int] = {}
    for r in rows:
        aw = int(r.get("away_seconds_used") or 0)
        hm = int(r.get("home_seconds_used") or 0)
        si = int(r.get("super_inning") or 0)
        if aw > 0 or hm > 0:
            banked_cap = max(aw, hm)
            if si > 0:
                # Game also ran SI after the seconds round — phase>=1 rows
                # may be either the seconds cap or the SI cap (5).
                out[r["id"]] = max(banked_cap, SI_PHASE_CAP)
            else:
                out[r["id"]] = banked_cap
    return out


_SECONDS_CAPS = _load_seconds_caps()


def _phase_cap(phase: int, game_id: int | None = None,
               team_id: int | None = None) -> int:
    """Per-(game, team, phase) outs cap.

    - phase 0  → 27 (regulation)
    - phase >= 1 + seconds round → banked outs for the batting team
    - phase >= 1 + super-inning  → 5 (legacy SI fixed cap)
    """
    if phase == 0:
        return REGULATION_PHASE_CAP
    if game_id is not None:
        banked = _SECONDS_CAPS.get(game_id)
        if banked is not None:
            return banked
    return SI_PHASE_CAP


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
        if (r["outs"] or 0) > _phase_cap(r["phase"] or 0, r["game_id"], r["team_id"])
    ]
    assert not over, (
        f"phase-outs cap exceeded on {len(over)} (game, team, phase) "
        f"groups; first 5: "
        + "; ".join(
            f"game={r['game_id']} team={r['team_id']} phase={r['phase']} "
            f"outs={r['outs']} cap={_phase_cap(r['phase'] or 0, r['game_id'], r['team_id'])}"
            for r in over[:5]
        )
    )


# ---------------------------------------------------------------------------
# Invariant 2: per-phase OR reconciliation
# ---------------------------------------------------------------------------

def test_invariant_2_or_reconciliation(played_game_ids):
    """Per (game, team, phase): batter_outs + unattributed_outs == phase
    cap (27 reg / 5 SI), with one allowed exception — a walk-off half
    by the winning home team in the game's last phase, where the cap
    can fall short because the half ends the moment the home team
    retakes the lead.

    Also asserts the opposing-pitcher cross-check
    (batter_outs + unattr == opponent.pitcher_outs) so a paired
    under-count on both sides can't slip through.
    """
    extra, params = _game_filter_clause("ps")
    pitcher_rows = db.fetchall(
        f"""SELECT ps.game_id, ps.team_id AS pitcher_team_id, ps.phase,
                   g.home_team_id, g.away_team_id, g.winner_id,
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

    # Declared Seconds: per-(game, team) declaration markers. A team that
    # declared mid-half legitimately ends its regulation half short of 27
    # outs (the declaration banked the rest for a seconds round).
    declared_at: dict[tuple[int, int], int] = {}
    for r in db.fetchall(
        "SELECT id, home_team_id, away_team_id, "
        "home_declared_at, away_declared_at FROM games WHERE played=1"
    ):
        if r.get("home_declared_at") is not None:
            declared_at[(r["id"], r["home_team_id"])] = int(r["home_declared_at"])
        if r.get("away_declared_at") is not None:
            declared_at[(r["id"], r["away_team_id"])] = int(r["away_declared_at"])

    # Build helpers
    home_id: dict[int, int] = {}
    winner: dict[int, int | None] = {}
    last_phase: dict[int, int] = {}
    opp_outs: dict[tuple, int] = {}
    for r in pitcher_rows:
        gid = r["game_id"]
        home_id[gid] = r["home_team_id"]
        winner[gid] = r["winner_id"]
        ph = r["phase"]
        last_phase[gid] = max(last_phase.get(gid, 0), ph or 0)
        batting_team = (r["away_team_id"]
                        if r["pitcher_team_id"] == r["home_team_id"]
                        else r["home_team_id"])
        opp_outs[(gid, batting_team, ph)] = r["outs"] or 0

    batter_outs = {(r["game_id"], r["team_id"], r["phase"]): (r["outs"] or 0)
                   for r in batter_rows}
    unattr = {(r["game_id"], r["team_id"], r["phase"]):
              (r["unattributed_outs"] or 0) for r in unattr_rows}

    bad: list[str] = []
    # Iterate every batting half we have any record of.
    keys = set(opp_outs) | set(batter_outs)
    for (gid, tid, ph) in keys:
        b = batter_outs.get((gid, tid, ph), 0)
        u = unattr.get((gid, tid, ph), 0)
        opp = opp_outs.get((gid, tid, ph), 0)
        cap = _phase_cap(ph or 0, gid, tid)
        total = b + u

        # (a) Cross-check with opposing pitcher outs (rules out paired
        #     undercounts on both sides).
        if total != opp:
            bad.append(
                f"game={gid} team={tid} phase={ph}: batter_outs={b} "
                f"+ unattr={u} ({total}) != opp_pitcher_outs={opp}"
            )
            continue

        # (b) Cap reconciliation. Two legitimate undershoots:
        #     - Walk-off in the game's last phase (either team can be the
        #       comeback winner under Declared Seconds, not just home).
        #     - Phase 0 of a team that DECLARED — they banked outs and
        #       ended their regulation half early on purpose.
        is_walkoff = (
            winner.get(gid) == tid
            and (ph or 0) == last_phase.get(gid, 0)
        )
        is_declared = (
            (ph or 0) == 0
            and (gid, tid) in declared_at
            and total == declared_at[(gid, tid)]
        )
        if total > cap:
            bad.append(
                f"game={gid} team={tid} phase={ph}: total_outs={total} "
                f"exceeds cap={cap}"
            )
        elif total < cap and (ph or 0) == 0 and not (is_walkoff or is_declared):
            # Phase 0 undershoots need a reason (walk-off or declaration).
            # Phase>0 rounds (seconds / SI) can legitimately end early via
            # walk-off in the round itself, and we don't track those
            # boundaries in the schema, so don't flag under-cap there.
            bad.append(
                f"game={gid} team={tid} phase={ph}: total_outs={total} "
                f"< cap={cap} and not a walk-off / declaration "
                f"(winner={winner.get(gid)} home={home_id.get(gid)} "
                f"last_phase={last_phase.get(gid, 0)})"
            )
    assert not bad, (
        f"OR reconciliation failed on {len(bad)} (game, team, phase) "
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
        if (r["outs_recorded"] or 0) > _phase_cap(r["phase"] or 0, r["game_id"], r["team_id"])
    ]
    assert not over, (
        f"OS% > 100% on {len(over)} pitcher rows; first 5: "
        + "; ".join(
            f"game={r['game_id']} team={r['team_id']} pid={r['player_id']} "
            f"phase={r['phase']} outs={r['outs_recorded']} "
            f"cap={_phase_cap(r['phase'] or 0, r['game_id'], r['team_id'])}"
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

    # W comes straight from the production decision logic the live site uses
    # (`_pitcher_wl_map`: SP-outs threshold + most-effective-reliever), not a
    # re-derivation here. A local "max outs per team-game" re-derivation used
    # to live here and drifted out of sync with that rule; deriving W from the
    # production path directly is both authoritative and self-maintaining. G
    # comes from the production dedup view, so an over-count in either path
    # still surfaces as W > G.
    #
    # NOTE: `_pitcher_wl_map` is inherently whole-DB, so this invariant runs
    # unscoped (it ignores O27V2_INVARIANTS_GAMES).
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
        f"W > G on {len(bad)} pitchers; first 5: "
        + "; ".join(f"pid={pid} W={w} G={g}" for pid, w, g in bad[:5])
    )

    # Independent global cross-check: total wins distributed across all
    # pitchers must equal the number of decided games.
    # Regular season only: pitcher W-L (and teams.wins) track the regular
    # season; playoff decisions live on playoff_series, not on pitcher lines,
    # so the cross-check is against regular-season decided games.
    total_w = sum(rec.get("w", 0) for rec in wl.values())
    decided = db.fetchone(
        "SELECT COUNT(*) AS n FROM games WHERE played = 1 "
        "AND winner_id IS NOT NULL AND COALESCE(is_playoff, 0) = 0"
    )["n"]
    assert total_w == decided, (
        f"Σ W ({total_w}) != decided games ({decided}); "
        f"win attribution is dropping or double-crediting wins"
    )


# ---------------------------------------------------------------------------
# Invariant 6: PA identity per batter row
# ---------------------------------------------------------------------------

def test_invariant_6_pa_identity(played_game_ids):
    """pa == ab + bb + hbp on every batter row.

    SF/SH aren't persisted on game_batter_stats yet (sacrifice events
    fall through the renderer's leftover-out path and don't credit a
    batter PA at all, so they don't break this identity — they leak as
    a separate counted-vs-recorded mismatch tracked by other invariants).

    Any deviation is either a real engine bug (AB > PA) or a stat path
    that increments PA without crediting the matching AB/BB/HBP.
    """
    extra, params = _game_filter_clause("bs")
    rows = db.fetchall(
        f"""SELECT bs.game_id, bs.team_id, bs.player_id, bs.phase,
                   bs.pa, bs.ab, bs.bb, bs.hbp, bs.sh
              FROM game_batter_stats bs
              JOIN games g ON g.id = bs.game_id
             WHERE g.played = 1{extra}""",
        params,
    )
    # PA == AB + BB + HBP + SH. A successful sacrifice bunt (sh) is a plate
    # appearance but not an at-bat, so it's the fourth term of the identity now
    # that sh is persisted (it used to be silently dropped).
    bad = [
        r for r in rows
        if (r["pa"] or 0) != ((r["ab"] or 0) + (r["bb"] or 0)
                              + (r["hbp"] or 0) + (r["sh"] or 0))
    ]
    assert not bad, (
        f"PA != AB+BB+HBP+SH on {len(bad)} batter rows; first 5: "
        + "; ".join(
            f"game={r['game_id']} pid={r['player_id']} phase={r['phase']} "
            f"PA={r['pa']} AB={r['ab']} BB={r['bb']} HBP={r['hbp']} SH={r['sh']}"
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
# Invariant 8: xRA anchored to the realized run environment
# ---------------------------------------------------------------------------

def test_invariant_8_fip_anchored_to_era(played_game_ids):
    """league xRA (outs-weighted) within 0.05 of league RA per 27 outs.

    wERA RETIRED (its arc-weighting baked in a false "late runs cost more"
    theory that has nothing to stand on in a single continuous 27-out half).
    xRA — expected runs allowed from the actual events — is the run-prevention
    headline now. This invariant is the spirit-preserved replacement for the
    old wERA-vs-ER/27 check: the EXPECTED stat must center on the REALIZED run
    environment (league RA/27, all runs), so a miscalibrated `xra_norm` trips
    it immediately. (`werra` is kept as an alias of `xra`; we guard that too.)

    Plus an independent ER ≤ R sanity check: per (game, team), the
    sum of pitcher earned-runs cannot exceed the team's actual runs
    allowed (= the OPPONENT's score in `games`).
    """
    from o27v2.web.app import _aggregate_pitcher_rows, _PSTATS_DEDUP_SQL

    extra_a, params_a = _game_filter_clause("ps")
    rows = db.fetchall(
        f"""SELECT ps.player_id,
                   SUM(ps.outs_recorded) AS outs,
                   SUM(ps.batters_faced) AS bf,
                   SUM(ps.hits_allowed)  AS h,
                   COALESCE(SUM(ps.singles_allowed),0) AS singles_allowed,
                   COALESCE(SUM(ps.doubles_allowed),0) AS doubles_allowed,
                   COALESCE(SUM(ps.triples_allowed),0) AS triples_allowed,
                   SUM(ps.runs_allowed)  AS r,
                   SUM(ps.er)            AS er,
                   SUM(ps.bb)            AS bb,
                   SUM(ps.k)             AS k,
                   SUM(ps.hr_allowed)    AS hr_allowed,
                   COALESCE(SUM(ps.hbp_allowed),0) AS hbp_allowed,
                   COALESCE(SUM(ps.unearned_runs),0) AS unearned_runs,
                   COALESCE(SUM(ps.fo_induced),0) AS fo_induced,
                   COALESCE(SUM(ps.pitches),0) AS pitches,
                   COALESCE(SUM(ps.er_arc1),0) AS er_arc1,
                   COALESCE(SUM(ps.er_arc2),0) AS er_arc2,
                   COALESCE(SUM(ps.er_arc3),0) AS er_arc3,
                   COALESCE(SUM(ps.k_arc1),0) AS k_arc1,
                   COALESCE(SUM(ps.k_arc2),0) AS k_arc2,
                   COALESCE(SUM(ps.k_arc3),0) AS k_arc3,
                   COALESCE(SUM(ps.fo_arc1),0) AS fo_arc1,
                   COALESCE(SUM(ps.fo_arc2),0) AS fo_arc2,
                   COALESCE(SUM(ps.fo_arc3),0) AS fo_arc3,
                   COALESCE(SUM(ps.bf_arc1),0) AS bf_arc1,
                   COALESCE(SUM(ps.bf_arc2),0) AS bf_arc2,
                   COALESCE(SUM(ps.bf_arc3),0) AS bf_arc3,
                   COUNT(*) AS g
              FROM {_PSTATS_DEDUP_SQL} ps
              JOIN games gm ON gm.id = ps.game_id
             WHERE gm.played = 1 AND COALESCE(gm.is_playoff, 0) = 0{extra_a}
             GROUP BY ps.player_id""",
        params_a,
    )
    if not rows:
        pytest.skip("no pitcher rows in the target DB scope")
    # Regular season only: the run environment that `xra_norm` anchors to is
    # the regular season; postseason stats are a separate population.

    rows = [dict(r) for r in rows]
    _aggregate_pitcher_rows(rows)

    total_outs = sum(r["outs"] or 0 for r in rows)
    total_ra   = sum(r["r"]    or 0 for r in rows)
    if total_outs == 0:
        pytest.skip("no pitcher outs recorded")
    league_ra27 = (total_ra * 27.0) / total_outs
    league_xra_weighted = (
        sum((r.get("xra") or 0.0) * (r["outs"] or 0) for r in rows) / total_outs
    )
    assert abs(league_xra_weighted - league_ra27) < 0.05, (
        f"outs-weighted league xRA {league_xra_weighted:.4f} not within 0.05 of "
        f"league RA/27 {league_ra27:.4f}; the xRA constant (`xra_norm`) is no "
        f"longer anchored to the realized run environment"
    )
    # wERA retired → its key aliases xRA; guard the alias so nothing resurrects
    # a divergent arc-weighted value.
    league_werra_weighted = (
        sum((r.get("werra") or 0.0) * (r["outs"] or 0) for r in rows) / total_outs
    )
    assert abs(league_werra_weighted - league_xra_weighted) < 1e-6, (
        f"werra ({league_werra_weighted:.4f}) must alias xra "
        f"({league_xra_weighted:.4f}) since wERA retired"
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


def test_invariant_9_walk_back_runs_le_faced(played_game_ids):
    """Walk-Back invariant: per pitcher (and aggregate), the number of
    Walk-Back runs allowed cannot exceed the number of Walk-Back PAs faced.
    Holds because every Walk-Back run must come from a Walk-Back PA.

    Also asserts wb_runs is a subset of unearned_runs at the per-game-team
    level (Walk-Back runs are by rule unearned — Manfred-runner precedent).
    """
    from o27v2.web.app import _PSTATS_DEDUP_SQL

    extra, params = _game_filter_clause("ps")
    rows = db.fetchall(
        f"""SELECT ps.player_id,
                   COALESCE(SUM(ps.wb_faced), 0) AS faced,
                   COALESCE(SUM(ps.wb_runs),  0) AS runs
              FROM {_PSTATS_DEDUP_SQL} ps
              JOIN games g ON g.id = ps.game_id
             WHERE g.played = 1{extra}
             GROUP BY ps.player_id""",
        params,
    )
    if not rows:
        pytest.skip("no pitcher rows in scope")

    bad = [r for r in rows if (r["runs"] or 0) > (r["faced"] or 0)]
    assert not bad, (
        f"wb_runs > wb_faced for {len(bad)} pitchers; first 5: "
        + "; ".join(
            f"player_id={r['player_id']} faced={r['faced']} runs={r['runs']}"
            for r in bad[:5]
        )
    )

    # Walk-Back runs must be a subset of unearned_runs per (game, team).
    rows2 = db.fetchall(
        f"""SELECT ps.game_id, ps.team_id,
                   COALESCE(SUM(ps.wb_runs), 0)      AS wb,
                   COALESCE(SUM(ps.unearned_runs),0) AS uer
              FROM {_PSTATS_DEDUP_SQL} ps
              JOIN games g ON g.id = ps.game_id
             WHERE g.played = 1{extra}
             GROUP BY ps.game_id, ps.team_id""",
        params,
    )
    bad2 = [r for r in rows2 if (r["wb"] or 0) > (r["uer"] or 0)]
    assert not bad2, (
        f"wb_runs > unearned_runs on {len(bad2)} (game, team) groups; first 5: "
        + "; ".join(
            f"game={r['game_id']} team={r['team_id']} wb={r['wb']} uer={r['uer']}"
            for r in bad2[:5]
        )
    )


def test_invariant_10_tto_buckets_reconcile(played_game_ids):
    """Times-through-the-order buckets must reconcile with the headline
    counters: per pitcher row, bf_tto1+2+3 == batters_faced (every PA ticks
    exactly one look bucket) and k_tto1+2+3 == k (every strikeout ticks one).
    Guards the whole TTO accumulation → SpellRecord → DB pipeline.

    Legacy rows written before the TTO buckets existed carry all-zero tto
    columns; those are skipped (bf_tto sum 0 but batters_faced > 0).
    """
    from o27v2.web.app import _PSTATS_DEDUP_SQL

    extra, params = _game_filter_clause("ps")
    rows = db.fetchall(
        f"""SELECT ps.player_id, ps.game_id,
                   COALESCE(ps.batters_faced, 0) AS bf,
                   COALESCE(ps.k, 0) AS k,
                   COALESCE(ps.bf_tto1,0)+COALESCE(ps.bf_tto2,0)+COALESCE(ps.bf_tto3,0) AS bf_tto,
                   COALESCE(ps.k_tto1,0) +COALESCE(ps.k_tto2,0) +COALESCE(ps.k_tto3,0)  AS k_tto
              FROM {_PSTATS_DEDUP_SQL} ps
              JOIN games g ON g.id = ps.game_id
             WHERE g.played = 1{extra}""",
        params,
    )
    # Only rows that actually carry TTO data (post-migration sims).
    live = [r for r in rows if (r["bf_tto"] or 0) > 0]
    if not live:
        pytest.skip("no TTO-bucketed pitcher rows in scope")

    bf_bad = [r for r in live if r["bf_tto"] != r["bf"]]
    assert not bf_bad, (
        f"bf_tto sum != batters_faced for {len(bf_bad)} rows; first 5: "
        + "; ".join(
            f"player={r['player_id']} game={r['game_id']} bf={r['bf']} bf_tto={r['bf_tto']}"
            for r in bf_bad[:5]
        )
    )
    k_bad = [r for r in live if r["k_tto"] != r["k"]]
    assert not k_bad, (
        f"k_tto sum != k for {len(k_bad)} rows; first 5: "
        + "; ".join(
            f"player={r['player_id']} game={r['game_id']} k={r['k']} k_tto={r['k_tto']}"
            for r in k_bad[:5]
        )
    )


# ---------------------------------------------------------------------------
# Invariant 11: WAR's surfaced components == the metrics displayed elsewhere
# ---------------------------------------------------------------------------

def test_invariant_11_war_components_match_displayed_metrics(played_game_ids):
    """Every defensive/baserunning component inside a player's WAR must equal the
    metric shown on the fielding/Savant surface for that same player, within
    tolerance — and WAR must equal the sum of its surfaced parts.

    This is the invariant whose absence let the WAR/OAA divergence ship: the
    season-card WAR read a rating-derived scout DRS while the Savant page showed
    event-based OAA/Field Runs, and nothing checked that the two agreed. WAR is
    now anchored to the same regressed Field Run Value the surface displays (for
    qualified fielders) and the same O27-native BSR, so:

      * `def_runs` == displayed Field Runs (source must be the event metric, not
        the scout fallback) for qualified fielders, and
      * `bsr_runs` == displayed BSR, and
      * WAR == war_off + dwar + bwar_base.

    Skips cleanly if flask (and thus o27v2.web.app) is unavailable, matching the
    rest of the suite's environmental tolerance.
    """
    try:
        from o27v2.web.app import (
            _aggregate_batter_rows, _league_baselines_compute,
            _WAR_FIELDING_MIN_CHANCES,
        )
        from o27v2.analytics.expanded import (
            build_fielding_value, build_baserunning_value,
        )
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"web/app or analytics unavailable: {exc}")

    baselines = _league_baselines_compute()
    field = {f["player_id"]: f for f in build_fielding_value(min_chances=1)["leaders"]}
    baserun = {b["player_id"]: b for b in build_baserunning_value(min_op=1)["leaders"]}

    # Qualified fielders only — below the gate WAR uses the scout projection by
    # design, so the surface (event) and WAR (scout) are *expected* to differ.
    qualified = [pid for pid, f in field.items()
                 if f["chances"] >= _WAR_FIELDING_MIN_CHANCES]
    if not qualified:
        pytest.skip("no qualified fielders in scope")

    rows = db.fetchall(
        """SELECT bs.player_id, p.position, p.team_id,
                  p.defense, p.defense_outfield, p.defense_infield, p.defense_catcher,
                  COUNT(DISTINCT bs.game_id) AS g,
                  SUM(bs.pa) AS pa, SUM(bs.ab) AS ab, SUM(bs.hits) AS hits,
                  SUM(bs.doubles) AS doubles, SUM(bs.triples) AS triples, SUM(bs.hr) AS hr,
                  SUM(bs.bb) AS bb, SUM(bs.hbp) AS hbp, SUM(bs.k) AS k,
                  SUM(bs.runs) AS runs, SUM(bs.rbi) AS rbi, SUM(bs.sb) AS sb, SUM(bs.sh) AS sh
             FROM game_batter_stats bs JOIN players p ON p.id = bs.player_id
            WHERE bs.phase = 0 AND bs.player_id IN (%s)
            GROUP BY bs.player_id""" % ",".join("?" * len(qualified)),
        tuple(qualified),
    )
    rows = [dict(r) for r in rows]
    _aggregate_batter_rows(rows, baselines=baselines)

    mismatches = []
    bsr_bad = []
    sum_bad = []
    for r in rows:
        disp = field[r["player_id"]]["frv"]
        if abs(r["def_runs"] - disp) > 0.1 or r["def_runs_source"] != "field":
            mismatches.append((r["player_id"], r["def_runs"], disp, r["def_runs_source"]))
        # Baserunning: WAR's bsr_runs must equal the displayed BSR for the same
        # player (displayed only when the player cleared build_baserunning_value's
        # opportunity gate; the per-player value is identical regardless of gate).
        if r["player_id"] in baserun:
            disp_bsr = baserun[r["player_id"]]["bsr"]
            if abs(r.get("bsr_runs", 0.0) - disp_bsr) > 0.1:
                bsr_bad.append((r["player_id"], r.get("bsr_runs"), disp_bsr))
        # Component identity: WAR == batting + defense + baserunning.
        if abs(r["war"] - (r["war_off"] + r["dwar"] + r["bwar_base"])) > 1e-6:
            sum_bad.append((r["player_id"], r["war"], r["war_off"], r["dwar"], r["bwar_base"]))

    assert not mismatches, (
        f"WAR def_runs != displayed Field Runs for {len(mismatches)} qualified "
        f"fielders; first 5 (pid, def_runs, displayed_frv, source): {mismatches[:5]}"
    )
    assert not bsr_bad, (
        f"WAR bsr_runs != displayed BSR for {len(bsr_bad)} players; "
        f"first 5 (pid, war_bsr, displayed_bsr): {bsr_bad[:5]}"
    )
    assert not sum_bad, (
        f"WAR != war_off + dwar + bwar_base for {len(sum_bad)} rows; "
        f"first 5 (pid, war, off, dwar, bwar_base): {sum_bad[:5]}"
    )
