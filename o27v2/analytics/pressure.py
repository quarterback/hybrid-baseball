"""Pressure-Adjusted Impact (PAI) for O27.

PAI is a chase-pressure-weighted run-value metric. It uses the existing
RE-by-base/out matrix as the run-value backbone, then scales each PA by a
simple chase proxy derived from live deficit and outs remaining. It is not a
replacement for WPA: WPA asks whether the PA moved win probability; PAI asks
whether the PA beat the live run requirement inside the 27-out envelope.
"""
from __future__ import annotations
from collections import defaultdict

from o27v2 import db
from o27v2.analytics.run_expectancy import build_re_table
from o27.stats.team import required_run_rate_3o


def _team_in(team_ids, col="team_id"):
    if not team_ids:
        return ""
    ids = ",".join(str(int(t)) for t in team_ids)
    return f" AND {col} IN ({ids})"


def _outs_bucket(outs: int | None) -> int:
    if outs is None:
        return 0
    return min(8, max(0, int(outs) // 3))


def _pressure_multiplier(score_diff: int | None, outs_before: int | None) -> float:
    """Chase pressure proxy from required runs per three outs.

    When the batting team trails, estimate the runs needed to pull ahead and
    spread them over the remaining out envelope. Tied/ahead states are neutral.
    The cap prevents tiny-sample endgame states from dominating season totals.
    """
    if score_diff is None or outs_before is None or score_diff >= 0:
        return 1.0
    # Runs needed to pull ahead, paced over the remaining 27-out envelope.
    # Shares the one canonical RRR/3O definition (o27/stats/team.py) used by the
    # chase analytics and the manager AI, so the pressure concept can't drift.
    runs_to_lead = abs(int(score_diff)) + 1
    rrr_3o = required_run_rate_3o(runs_to_lead, 0, int(outs_before)) or 0.0
    return 1.0 + min(3.0, rrr_3o / 6.0)


def build_pressure_impact(min_pa: int = 1, team_ids=None) -> dict:
    """Build batter Pressure-Adjusted Impact leader rows.

    Returns `pai` (weighted run value), `pai_per_pa`, and `trr_plus`, where
    TRR+ is a 100=league-average index of pressure-weighted run value per PA.
    """
    re = build_re_table(team_ids)
    matrix = re.get("matrix", {})

    def re_value(bases, outs):
        if bases is None or outs is None:
            return 0.0
        return float((matrix.get(int(bases), {}).get(_outs_bucket(outs), {}) or {}).get("re", 0.0))

    rows = db.fetchall(
        """
        SELECT batter_id AS player_id, team_id, runs_scored,
               outs_before, bases_before, score_diff_before,
               outs_after, bases_after
        FROM game_pa_log
        WHERE phase = 0
          AND batter_id IS NOT NULL
          AND outs_before IS NOT NULL
          AND outs_after IS NOT NULL
          AND bases_before IS NOT NULL
          AND bases_after IS NOT NULL"""
        + _team_in(team_ids, "team_id")
    )
    agg: dict[int, dict] = defaultdict(lambda: {"pai": 0.0, "rv": 0.0, "pa": 0, "pressure": 0.0})
    total_pai = 0.0
    total_pa = 0
    for r in rows:
        pid = r["player_id"]
        before = re_value(r["bases_before"], r["outs_before"])
        after = 0.0 if int(r["outs_after"] or 0) >= 27 else re_value(r["bases_after"], r["outs_after"])
        rv = (r["runs_scored"] or 0) + after - before
        pressure = _pressure_multiplier(r["score_diff_before"], r["outs_before"])
        pai = rv * pressure
        agg[pid]["pai"] += pai
        agg[pid]["rv"] += rv
        agg[pid]["pa"] += 1
        agg[pid]["pressure"] += pressure
        total_pai += pai
        total_pa += 1

    league_pai_pa = (total_pai / total_pa) if total_pa else 0.0
    names = {}
    if agg:
        ids = ",".join(str(int(pid)) for pid in agg)
        names = {r["id"]: r for r in db.fetchall(
            f"""SELECT p.id, p.name AS player_name, t.abbrev AS team_abbrev, t.id AS team_id
                FROM players p LEFT JOIN teams t ON t.id = p.team_id
                WHERE p.id IN ({ids})"""
        )}

    leaders = []
    by_player = {}
    for pid, v in agg.items():
        pa = v["pa"]
        if pa <= 0:
            continue
        pai_pa = v["pai"] / pa
        trr_plus = (100 * pai_pa / league_pai_pa) if league_pai_pa else None
        meta = names.get(pid, {})
        row = {
            "player_id": pid,
            "player_name": meta.get("player_name", f"#{pid}"),
            "team_abbrev": meta.get("team_abbrev", ""),
            "team_id": meta.get("team_id"),
            "pa": pa,
            "pai": round(v["pai"], 2),
            "pai_per_pa": round(pai_pa, 3),
            "trr_plus": round(trr_plus) if trr_plus is not None else None,
            "avg_pressure": round(v["pressure"] / pa, 2),
            "run_value": round(v["rv"], 2),
        }
        by_player[pid] = row
        if pa >= min_pa:
            leaders.append(row)
    leaders.sort(key=lambda x: x["pai"], reverse=True)
    return {"leaders": leaders, "by_player": by_player, "league_pai_per_pa": league_pai_pa}


_CHASE_HIT_TYPES = {"single", "double", "triple", "hr", "infield_single"}


def build_chase_split_table(min_pa: int = 1, team_ids=None) -> dict:
    """Per-batter production split — contact while the team is TRAILING (the chase).

    The legible companion to PAI: where PAI weights run value by RRR/3O
    continuously, this is the plain split fans sort by — how often a hitter gets
    a hit when his side is behind and needs runs. `game_pa_log` only reliably
    logs contact events (no walks/strikeouts), so `chase_ba` is a contact
    average (hits / balls put in play while trailing), not full PAVG — named and
    tooltipped accordingly. `chase_pa` is the (contact) sample size.

    Trailing is the chase trigger (`score_diff_before < 0`); the continuous
    RRR/3O severity is already captured by PAI's pressure weight, which shares
    the one canonical `required_run_rate_3o`.
    """
    rows = db.fetchall(
        f"""SELECT batter_id AS player_id, hit_type, score_diff_before
            FROM game_pa_log
            WHERE phase = 0 AND batter_id IS NOT NULL AND hit_type IS NOT NULL
              AND score_diff_before IS NOT NULL{_team_in(team_ids, "team_id")}"""
    )
    agg: dict[int, dict] = defaultdict(lambda: {"cpa": 0, "chits": 0, "chr": 0})
    for r in rows:
        if int(r["score_diff_before"]) >= 0:
            continue                       # only count trailing (chase) PAs
        a = agg[r["player_id"]]
        a["cpa"] += 1
        ht = r["hit_type"]
        if ht in _CHASE_HIT_TYPES:
            a["chits"] += 1
            if ht == "hr":
                a["chr"] += 1

    names = {}
    if agg:
        ids = ",".join(str(int(pid)) for pid in agg)
        names = {r["id"]: r for r in db.fetchall(
            f"""SELECT p.id, p.name AS player_name, t.abbrev AS team_abbrev
                FROM players p LEFT JOIN teams t ON t.id = p.team_id
                WHERE p.id IN ({ids})"""
        )}

    leaders = []
    by_player = {}
    for pid, v in agg.items():
        cpa = v["cpa"]
        if cpa <= 0:
            continue
        meta = names.get(pid, {})
        row = {
            "player_id": pid,
            "player_name": meta.get("player_name", f"#{pid}"),
            "team_abbrev": meta.get("team_abbrev", ""),
            "chase_pa": cpa,
            "chase_hits": v["chits"],
            "chase_hr": v["chr"],
            "chase_ba": round(v["chits"] / cpa, 3),
        }
        by_player[pid] = row
        if cpa >= min_pa:
            leaders.append(row)
    leaders.sort(key=lambda x: -x["chase_ba"])
    return {"leaders": leaders, "by_player": by_player}
