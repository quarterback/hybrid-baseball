"""
O27v2 Flask web application.

Routes:
  GET  /                  Scores dashboard — today's games, recent finals, division leaders, top-5 leaders
  GET  /standings         Full standings — one wide table per league, sortable
  GET  /schedule          Full schedule (filter: team, status)
  GET  /game/<id>         Box score for a completed game
  GET  /players           Browseable player index (server-paginated, sortable, filterable)
  GET  /player/<id>       Single player season + game log
  GET  /teams             Team list
  GET  /team/<id>         Team header + batting roster + pitching roster + last 10 games
  GET  /leaders           Season-to-date leaderboards (replaces /stats; /stats redirects here)
  GET  /transactions      League transaction log (filterable by team / type)
  GET  /new-league        League-creation screen
  POST /new-league        Apply the chosen config (reset DB + reseed)
  POST /api/sim           Simulate the next N games (JSON response)
"""
from __future__ import annotations
import math
import os
import sys

_workspace = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _workspace not in sys.path:
    sys.path.insert(0, _workspace)

from flask import Flask, render_template, request, redirect, url_for, jsonify, abort

from o27v2 import db
from o27v2.sim import (
    simulate_game,
    simulate_next_n,
    simulate_date,
    simulate_through,
    get_current_sim_date,
    get_last_scheduled_date,
    get_all_star_date,
    is_season_complete,
    advance_sim_clock,
    resync_sim_clock,
)
from o27v2.league import get_league_configs

import datetime as _dt

app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = "o27v2-dev-key"


def _scout(val) -> int:
    """Render a stored attribute as a 20–80 scout grade.
    Task #47 stores grades natively as ints in [20, 80]; legacy float values
    in [0.0, 1.0] are converted on the fly via the 0.15 / 0.50 / 0.85 anchors."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return 50
    if v > 1.0:  # already a grade (int storage from Task #47)
        return max(20, min(80, int(round(v))))
    grade = 20 + (v - 0.15) / 0.70 * 60
    return max(20, min(80, int(round(grade))))


app.jinja_env.filters["scout"] = _scout


@app.context_processor
def inject_sim_state():
    return {"sim": {
        "current_date":   get_current_sim_date(),
        "all_star_date":  get_all_star_date(),
        "last_date":      get_last_scheduled_date(),
        "season_complete": is_season_complete(),
    }}


def _end_of_month(d: _dt.date) -> _dt.date:
    if d.month == 12:
        return _dt.date(d.year, 12, 31)
    return _dt.date(d.year, d.month + 1, 1) - _dt.timedelta(days=1)


def _sim_response(from_date: str | None, to_date: str | None, results: list) -> dict:
    return {
        "simulated":       len(results),
        "from_date":       from_date,
        "to_date":         to_date,
        "current_date":    get_current_sim_date(),
        "season_complete": is_season_complete(),
    }


def _clamp_to_last(date_str: str) -> str:
    last = get_last_scheduled_date()
    if last is None:
        return date_str
    last_plus_one = (_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat()
    return min(date_str, last_plus_one)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _divisions() -> dict[str, list[dict]]:
    teams = db.fetchall("SELECT * FROM teams ORDER BY division, wins DESC, losses ASC")
    divs: dict[str, list[dict]] = {}
    for t in teams:
        divs.setdefault(t["division"], []).append(t)
    return divs


def _leagues_with_divisions() -> dict[str, dict[str, list[dict]]]:
    """Return {league_name: {division_name: [team, ...]}} sorted by win pct."""
    teams = db.fetchall(
        "SELECT * FROM teams ORDER BY league, division, wins DESC, losses ASC"
    )
    out: dict[str, dict[str, list[dict]]] = {}
    for t in teams:
        out.setdefault(t["league"], {}).setdefault(t["division"], []).append(t)
    return out


def _win_pct(t: dict) -> str:
    total = t["wins"] + t["losses"]
    if total == 0:
        return ".000"
    return f".{int(t['wins'] / total * 1000):03d}"


def _gb(leader: dict, team: dict) -> str:
    diff = (leader["wins"] - team["wins"] + team["losses"] - leader["losses"]) / 2
    if diff == 0:
        return "—"
    return f"{diff:.1f}"


# Dedup subquery: collapse duplicate (player_id, game_id) rows in
# game_pitcher_stats (Task #57 audit — pre-#58 the engine could re-insert a
# pitcher's line if they appeared in multiple half-innings, inflating BF/K/G).
# We pick ONE real row per (game_id, player_id) — the row with the most outs,
# breaking ties by lowest rowid (earliest appearance). This avoids the
# "Frankenstein" totals you get from MAX-per-column, which can mix maxima from
# different duplicate rows and overstate stats. Task #58 will add a UNIQUE
# constraint on (player_id, game_id, phase) so this subquery becomes a no-op.
_PSTATS_DEDUP_SQL = """(
    SELECT game_id, player_id, team_id, batters_faced, outs_recorded,
           hits_allowed, runs_allowed, er, bb, k, hr_allowed, pitches,
           hbp_allowed, unearned_runs, sb_allowed, cs_caught, fo_induced
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY game_id, player_id
                   ORDER BY outs_recorded DESC, rowid ASC
               ) AS _rn
        FROM game_pitcher_stats
    )
    WHERE _rn = 1
)"""


_SP_OUTS_THRESHOLD = 12  # MLB-style: 5 IP minimum scaled to O27 = 12 outs

# Defensive position-value factors (approx. runs / 162 games range).
# A player with elite defense at SS saves ~12 runs vs neutral over a full
# season; at 1B that's ~4. Pure position value, used for DRS / dWAR.
_POSITION_DRS_RANGE: dict[str, float] = {
    "C":  15.0,
    "SS": 12.0,
    "2B":  8.0,
    "CF":  8.0,
    "3B":  7.0,
    "LF":  5.0,
    "RF":  5.0,
    "1B":  4.0,
    "DH":  0.0,
    "UT":  6.0,    # utility — gets average-of-positions bump
    "P":   2.0,    # pitchers field comebackers / cover bases — small effect
}


_INFIELD_POS_SET  = frozenset(("1B", "2B", "3B", "SS"))
_OUTFIELD_POS_SET = frozenset(("LF", "CF", "RF"))


def _position_defense_for_row(row: dict) -> float:
    """Return the player's effective defense rating at their position
    using a 60% sub-group + 40% general blend. All inputs come from the
    SUM-aggregated row; ints (20-95 grade) and floats (0..1 unit) both
    work via the scout-style 100-divide fallback.
    """
    pos = str(row.get("position") or "")

    def _norm(v):
        if v is None:
            return 0.5
        v = float(v)
        if v <= 1.0:
            return v
        # 20-95 grade scale: extend past 0.85 for elite-plus.
        if v <= 80.0:
            return 0.15 + (v - 20.0) / 60.0 * 0.70
        return 0.85 + (v - 80.0) / 15.0 * 0.15

    general = _norm(row.get("defense"))
    if pos == "C":
        sub = _norm(row.get("defense_catcher"))
    elif pos in _INFIELD_POS_SET:
        sub = _norm(row.get("defense_infield"))
    elif pos in _OUTFIELD_POS_SET:
        sub = _norm(row.get("defense_outfield"))
    else:
        sub = general
    return 0.6 * sub + 0.4 * general


def _pitcher_wl_map() -> dict[int, dict[str, int]]:
    """Award W/L per MLB-style rules adapted to the O27 27-out-per-side
    structure.

    Winning team:
      - Starting pitcher (earliest appearance, lowest game_pitcher_stats
        rowid) gets the W if they recorded at least _SP_OUTS_THRESHOLD
        (12) outs.
      - Otherwise the W goes to the reliever on the winning team who
        was most effective: max(outs - ER), with a tiebreaker on outs.
        This is a reasonable approximation of the MLB scorer's "most
        effective reliever" rule without modeling lead-state per inning.

    Losing team:
      - The pitcher with the most earned runs allowed gets the L. Ties
        broken toward the pitcher who appeared earlier (took the lead
        loss). This sidesteps the full "pitcher of record at lead change"
        rule but produces stable, defensible attribution.

    Saves are intentionally NOT computed — the user flagged this as
    "hard to figure out how" and it requires lead-state tracking we
    don't currently capture in game_pitcher_stats.
    """
    rows = db.fetchall(
        """SELECT ps.game_id, ps.team_id, ps.player_id,
                  ps.outs_recorded AS outs,
                  ps.runs_allowed  AS runs,
                  ps.er            AS er,
                  ps.rowid         AS rowid,
                  g.winner_id
             FROM game_pitcher_stats ps
             JOIN games g ON g.id = ps.game_id
            WHERE g.played = 1
            ORDER BY ps.game_id, ps.team_id, ps.rowid"""
    )

    # Group by (game_id, team_id) so we can apply the W/L decision logic
    # on each team-game in isolation.
    by_team_game: dict[tuple[int, int], list[dict]] = {}
    winners: dict[int, int | None] = {}
    for r in rows:
        key = (r["game_id"], r["team_id"])
        by_team_game.setdefault(key, []).append(r)
        winners[r["game_id"]] = r["winner_id"]

    out: dict[int, dict[str, int]] = {}
    for (game_id, team_id), pitchers in by_team_game.items():
        # rowid order = appearance order. First entry is the SP.
        winner_id = winners.get(game_id)
        if winner_id is None:
            continue   # tied / unfinished game (shouldn't happen post-SI)

        is_winner = winner_id == team_id
        if is_winner:
            sp = pitchers[0]
            credited = None
            if (sp["outs"] or 0) >= _SP_OUTS_THRESHOLD:
                credited = sp["player_id"]
            else:
                # Most effective reliever: max(outs - ER), tiebreak on outs.
                relievers = pitchers[1:] or pitchers   # fall back to SP if solo
                relievers = sorted(
                    relievers,
                    key=lambda p: ((p["outs"] or 0) - (p["er"] or 0),
                                   p["outs"] or 0),
                    reverse=True,
                )
                credited = relievers[0]["player_id"]
            if credited is not None:
                rec = out.setdefault(credited, {"w": 0, "l": 0})
                rec["w"] += 1
        else:
            # L: pitcher with most ER. Tiebreak: earliest appearance.
            losers = sorted(
                pitchers,
                key=lambda p: (-(p["er"] or 0), p["rowid"]),
            )
            charged = losers[0]["player_id"]
            rec = out.setdefault(charged, {"w": 0, "l": 0})
            rec["l"] += 1
    return out


def _attach_hits(games: list[dict]) -> None:
    """Sum hits per (game_id, team_id) from game_batter_stats and attach
    home_hits / away_hits to each game row. Pure roll-up of sim output —
    nothing is invented; if a game wasn't played, both hit totals are None."""
    if not games:
        return
    ids = [g["id"] for g in games]
    ph = ",".join("?" * len(ids))
    rows = db.fetchall(
        f"""SELECT game_id, team_id, SUM(hits) AS h
            FROM game_batter_stats
            WHERE game_id IN ({ph})
            GROUP BY game_id, team_id""",
        tuple(ids),
    )
    by_game: dict[int, dict[int, int]] = {}
    for r in rows:
        by_game.setdefault(r["game_id"], {})[r["team_id"]] = r["h"] or 0
    for g in games:
        team_hits = by_game.get(g["id"], {})
        g["home_hits"] = team_hits.get(g["home_team_id"]) if g.get("played") else None
        g["away_hits"] = team_hits.get(g["away_team_id"]) if g.get("played") else None


def _aggregate_batter_rows(rows: list[dict], baselines: dict | None = None) -> None:
    """Mutates rows in place to add classical, advanced, and O27-native
    sabermetrics.

    Adds (classical):  avg, obp (HBP-aware), slg, ops
    Adds (advanced):   iso, babip, k_pct, bb_pct, hr_pct, bb_k, sb_pct
    Adds (O27-native): woba, stay_pct, stay_rbi_per_stay, fo_pct, mhab_pct
    Adds (relative):   ops_plus, woba_plus    [if baselines provided]

    Pass `baselines=_league_baselines()` to enable OPS+ / wOBA+. Without
    baselines the row keys are still set (to 100.0) for templating sanity.
    """
    if baselines is None:
        baselines = {"obp": 0.0, "slg": 0.0, "ops": 0.0, "woba": 0.0}
    # O27 stat semantics:
    #   AVG, SLG, ISO are PA-denominated (NOT AB-denominated). Stays inside
    #   an AB make AB-denominated rates produce strange numbers (you can put
    #   up huge total-base counts inside a small AB sample), so we use PA
    #   throughout. AB is preserved and surfaced as H/AB — a stayer-vs-runner
    #   profile metric — but the canonical batting average is H/PA.
    #   Targets in this run environment: league AVG ~.350-.380, top hitters
    #   .450+; league SLG ~.550-.600, top sluggers approach 1.000.
    for b in rows:
        ab = b.get("ab") or 0
        h = b.get("h") or 0
        bb = b.get("bb") or 0
        pa = b.get("pa") or 0
        d2 = b.get("d2") or 0
        d3 = b.get("d3") or 0
        hr = b.get("hr") or 0
        k = b.get("k") or 0
        hbp = b.get("hbp") or 0
        sb = b.get("sb") or 0
        cs = b.get("cs") or 0
        # PAVG = H/PA — the headline batting average in O27. Bounded
        # 0.000-1.000. League-wide. (The legacy "avg" key is kept as an
        # alias for templates / leader queries that haven't migrated yet.)
        b["pavg"] = (h / pa) if pa else 0.0
        b["avg"]  = b["pavg"]
        # OBP — already PA-denominated as in MLB.
        b["obp"] = ((h + bb + hbp) / pa) if pa else 0.0
        # SLG = total bases / PA (O27 semantic). Per-PA reads cleanly across
        # multi-hit ABs; bounded ~0..1 (a hitter averaging a base per PA is
        # at the ceiling of slugging in this sport).
        singles = h - d2 - d3 - hr
        tb = singles + 2 * d2 + 3 * d3 + 4 * hr
        b["slg"] = (tb / pa) if pa else 0.0
        b["ops"] = b["obp"] + b["slg"]
        # BAVG = H/AB — the secondary "stayer profile" metric. Inherits
        # MLB's batting-average semantics (per-AB rate). In O27 it can
        # exceed 1.000 because multi-hit ABs are real (max 3 hits in 1 AB
        # via stays). Read together with PAVG it diagnoses style:
        #   high PAVG, BAVG ≈ 1.000  → slap-and-go contact hitter
        #   high PAVG, BAVG > 1.0   → productive stayer
        #   low PAVG,  BAVG > 1.0   → tries to stay but gets caught out
        b["bavg"]     = (h / ab) if ab else 0.0
        b["h_per_ab"] = b["bavg"]   # legacy alias
        # Stay differential — how much of the BAVG comes from stays.
        b["stay_diff"] = b["bavg"] - b["pavg"]
        # ISO = SLG - AVG (still works; both PA-denominated).
        b["iso"]    = b["slg"] - b["avg"]
        # BABIP redefined for O27: hits-on-balls-in-play / balls-in-play,
        # where a "ball in play" = any contact event (run-chosen and stay-
        # chosen). Stays count as both numerator (the hit was credited)
        # and denominator (a ball was put in play). The denominator is
        # PA - K - BB - HBP - HR (subtract events that aren't BIPs).
        bip_denom = pa - k - bb - hbp - hr
        b["babip"]  = ((h - hr) / bip_denom) if bip_denom > 0 else 0.0
        b["k_pct"]  = (k  / pa) if pa else 0.0
        b["bb_pct"] = (bb / pa) if pa else 0.0
        b["hr_pct"] = (hr / pa) if pa else 0.0
        b["bb_k"]   = (bb / k)  if k  else (bb * 1.0)
        # Stolen-base success rate on attempts.
        attempts = sb + cs
        b["sb_pct"] = (sb / attempts) if attempts else 0.0

        # --- O27-native sabermetrics ---
        # wOBA with O27-tuned linear weights, PA-denominated. 1B and BB
        # nudged up vs MLB because the stay mechanic lets baserunners
        # advance more freely on singles and walks, raising those events'
        # run-value contribution. HR weight slightly trimmed because
        # runners are already moving easily under stays.
        # Denominator is PA (NOT AB+BB+HBP) since each PA represents one
        # opportunity; stays inside an AB are separate PAs.
        singles = h - d2 - d3 - hr
        woba_num = (
            0.72 * bb + 0.74 * hbp + 0.95 * singles +
            1.30 * d2 + 1.70 * d3  + 2.05 * hr
        )
        b["woba"] = (woba_num / pa) if pa else 0.0

        # Stay% — share of PAs in which the batter chose to stay (dance
        # the runners). Distinctively O27 — no MLB analog.
        stays_v = b.get("stays") or 0
        b["stay_pct"] = (stays_v / pa) if pa else 0.0
        # Stay-RBI per stay — efficiency of stays. Stays don't always score
        # runners; this surfaces who actually drives in runs while staying.
        stay_rbi = b.get("stay_rbi") or 0
        b["stay_rbi_per_stay"] = (stay_rbi / stays_v) if stays_v else 0.0
        # Foul-out rate (O27's 3-foul cap). High FO% = batter prone to
        # fouling himself out — a real cost in this rule set.
        fo = b.get("fo") or 0
        b["fo_pct"] = (fo / pa) if pa else 0.0
        # Multi-hit AB% — share of ABs with 2+ credited hits (a stay-led
        # hit-fest in a single AB).
        mhab = b.get("mhab") or 0
        b["mhab_pct"] = (mhab / ab) if ab else 0.0

        # OPS+ / wOBA+ — relativized to live league baselines.
        league_ops  = baselines.get("ops")  or 0
        league_woba = baselines.get("woba") or 0
        b["ops_plus"]  = (b["ops"]  / league_ops  * 100.0) if league_ops  else 100.0
        b["woba_plus"] = (b["woba"] / league_woba * 100.0) if league_woba else 100.0

        # bVORP — value over replacement, in runs.
        # (wOBA - replacement_wOBA) × PA / wOBA_scale ≈ runs above replacement.
        # Uses a simplified wOBA scale of 1.20 (FanGraphs convention).
        repl_woba = baselines.get("replacement_woba") or 0
        woba_scale = 1.20
        b["vorp"] = ((b["woba"] - repl_woba) * pa / woba_scale) if (pa and league_woba) else 0.0

        # --- Defensive value ---
        # DRS = (player_position_defense - 0.5) × 2 × games_played / 162
        #       × position_drs_range. Scales linearly with games played.
        # dWAR = DRS / runs_per_win.
        rpw = baselines.get("runs_per_win") or 10.0
        pos = str(b.get("position") or "")
        games = b.get("g") or 0
        pos_def = _position_defense_for_row(b)
        b["pos_def"] = pos_def
        drs_range = _POSITION_DRS_RANGE.get(pos, 4.0)
        b["drs"] = (pos_def - 0.5) * 2.0 * (games / 162.0) * drs_range if games else 0.0
        b["dwar"] = b["drs"] / rpw if rpw else 0.0
        # bWAR — total batter value = batting WAR + defensive WAR.
        bwar_off = b["vorp"] / rpw if rpw else 0.0
        b["war_off"] = bwar_off
        b["war"] = bwar_off + b["dwar"]


def _league_fip_const() -> float:
    """Compute the FIP constant for the per-27-outs stat model.

    Standard MLB FIP = (13*HR + 3*BB - 2*K) / IP * 9 + C, where C is set so
    that league FIP equals league ERA. In the O27 per-27-outs model we use
    27/outs in place of 9/IP, and C is re-fit each time against the live
    league totals so FIP stays anchored to ERA across calibration cycles.

    Falls back to 3.10 (a reasonable per-game baseline) if no games yet.
    """
    row = db.fetchone(
        f"""SELECT COALESCE(SUM(hr_allowed),0) as hr,
                   COALESCE(SUM(bb),0)         as bb,
                   COALESCE(SUM(k),0)          as k,
                   COALESCE(SUM(er),0)         as er,
                   COALESCE(SUM(outs_recorded),0) as outs
            FROM {_PSTATS_DEDUP_SQL} ps"""
    )
    outs = (row or {}).get("outs") or 0
    if not outs:
        return 3.10
    league_era = (row["er"] * 27.0) / outs
    raw_fip    = ((13 * row["hr"]) + (3 * row["bb"]) - (2 * row["k"])) * 27.0 / outs
    return league_era - raw_fip


def _league_baselines() -> dict[str, float]:
    """Compute league baselines for OPS+/ERA+/wOBA+/WAR/VORP relativization.

    Refit every render cycle so the baselines track wherever the live league
    has actually settled — same pattern as the FIP constant. Falls back to
    sensible defaults if no games have been played yet.

    Returns:
      obp, slg, ops, woba         — league-average rate stats
      era, ra27                   — league-average pitching
      replacement_woba            — ~85% of league wOBA (replacement hitter)
      replacement_era             — ~120% of league ERA (replacement pitcher)
      runs_per_win                — Pythagorean-derived; ~18 for O27 vs ~10 MLB
      total_pa, total_outs        — for sample-size sanity in callers
    """
    bat = db.fetchone(
        """SELECT COALESCE(SUM(pa),0)  AS pa,
                  COALESCE(SUM(ab),0)  AS ab,
                  COALESCE(SUM(hits),0) AS h,
                  COALESCE(SUM(doubles),0) AS d2,
                  COALESCE(SUM(triples),0) AS d3,
                  COALESCE(SUM(hr),0)   AS hr,
                  COALESCE(SUM(bb),0)   AS bb,
                  COALESCE(SUM(hbp),0)  AS hbp,
                  COALESCE(SUM(runs),0) AS r
             FROM game_batter_stats"""
    ) or {}
    pit = db.fetchone(
        f"""SELECT COALESCE(SUM(er),0)            AS er,
                   COALESCE(SUM(runs_allowed),0)  AS r,
                   COALESCE(SUM(outs_recorded),0) AS outs
              FROM {_PSTATS_DEDUP_SQL} ps"""
    ) or {}

    out: dict[str, float] = {
        "obp": 0.330, "slg": 0.420, "ops": 0.750, "era": 5.00, "ra27": 5.00,
        "woba": 0.330, "replacement_woba": 0.280, "replacement_era": 6.00,
        "runs_per_win": 10.0,
        "total_pa": 0.0, "total_outs": 0.0,
    }

    pa = bat.get("pa", 0) or 0
    ab = bat.get("ab", 0) or 0
    h  = bat.get("h", 0)  or 0
    d2 = bat.get("d2", 0) or 0
    d3 = bat.get("d3", 0) or 0
    hr = bat.get("hr", 0) or 0
    bb = bat.get("bb", 0) or 0
    hbp= bat.get("hbp", 0) or 0
    if pa and ab:
        # PA-denominated rate stats (O27 semantic — see _aggregate_batter_rows).
        singles = h - d2 - d3 - hr
        tb      = singles + 2 * d2 + 3 * d3 + 4 * hr
        out["obp"] = (h + bb + hbp) / pa
        out["slg"] = tb / pa
        out["ops"] = out["obp"] + out["slg"]
        # wOBA stays PA-denominated as in MLB; weights tuned for O27 in the
        # batter aggregator and mirrored here so league mean tracks.
        woba_num = 0.72 * bb + 0.74 * hbp + 0.95 * singles + 1.30 * d2 + 1.70 * d3 + 2.05 * hr
        woba_den = pa   # NOT (AB + BB + HBP) — full PA-denominator in O27.
        out["woba"] = (woba_num / woba_den) if woba_den else 0.0
        # Replacement hitter sits ~85% of league wOBA — same convention
        # FanGraphs uses, and it's an easy mental anchor for users.
        out["replacement_woba"] = out["woba"] * 0.85
        out["total_pa"] = float(pa)

    pit_outs = pit.get("outs", 0) or 0
    if pit_outs:
        out["era"]  = (pit.get("er", 0) or 0) * 27.0 / pit_outs
        out["ra27"] = (pit.get("r", 0)  or 0) * 27.0 / pit_outs
        # Replacement pitcher allows ~20% more runs than league average.
        out["replacement_era"] = out["era"] * 1.20
        out["total_outs"] = float(pit_outs)

    # Runs-per-win for WAR. Pythagorean-flavored heuristic: in MLB
    # (~9 R/G total), it's ~10. In O27 (~25 R/G total) it's ~18.
    # Formula 9 + sqrt(R/G - per-team) lands roughly correct for both.
    if pit_outs:
        # Total runs across all teams over all games / games-played.
        games_played = db.fetchone("SELECT COUNT(*) AS n FROM games WHERE played=1")["n"] or 0
        if games_played > 0:
            r_per_game = ((pit.get("r", 0) or 0) * 2.0) / games_played   # both teams
            out["runs_per_win"] = max(8.0, 9.0 + (r_per_game / 4.0) ** 0.5 * 3.5)

    return out


def _aggregate_pitcher_rows(
    rows: list[dict],
    wl: dict[int, dict[str, int]] | None = None,
    fip_const: float | None = None,
    baselines: dict | None = None,
) -> None:
    if fip_const is None:
        fip_const = _league_fip_const()
    if baselines is None:
        baselines = {"era": 0.0}
    for p in rows:
        outs = p.get("outs") or 0
        ip = outs / 3.0
        h = p.get("h") or 0
        bb = p.get("bb") or 0
        r = p.get("r") or 0
        er = p.get("er") or 0
        k = p.get("k") or 0
        hr = p.get("hr_allowed") or p.get("hra") or 0
        bf = p.get("bf") or p.get("batters_faced") or 0
        hbp_a = p.get("hbp_allowed") or 0
        p["ip"] = ip
        # ERA uses earned runs and the per-27-outs denominator (Task #48).
        p["era"]   = (er * 27.0 / outs) if outs else 0.0
        # WHIP / K / BB are now per-27 outs (one full O27 game) instead of per-9 IP.
        p["whip"]  = ((bb + h) * 27.0 / outs) if outs else 0.0
        p["k27"]   = (k  * 27.0 / outs) if outs else 0.0
        p["bb27"]  = (bb * 27.0 / outs) if outs else 0.0
        p["hr27"]  = (hr * 27.0 / outs) if outs else 0.0
        p["ra27"]  = (r  * 27.0 / outs) if outs else 0.0
        # Kept under the legacy keys so older templates still render sensibly.
        p["k9"]    = p["k27"]
        p["bb9"]   = p["bb27"]
        p["so_bb"] = (k / bb) if bb else (k * 1.0)
        # FIP, fit against league ERA each batch (Task #50 calibration).
        if outs:
            p["fip"] = ((13 * hr) + (3 * bb) - (2 * k)) * 27.0 / outs + fip_const
        else:
            p["fip"] = 0.0
        # Per-BF rate stats (independent of outs — make small samples meaningful).
        p["k_pct"]  = (k  / bf) if bf else 0.0
        p["bb_pct"] = (bb / bf) if bf else 0.0
        p["hr_pct"] = (hr / bf) if bf else 0.0
        # Opponent batting average + BABIP allowed.
        # AB faced = BF - BB - HBP. (SF, IBB unavailable in O27.)
        ab_faced = max(0, bf - bb - hbp_a)
        p["oavg"] = (h / ab_faced) if ab_faced > 0 else 0.0
        bip_denom = ab_faced - k - hr
        p["babip_allowed"] = ((h - hr) / bip_denom) if bip_denom > 0 else 0.0

        # --- O27-native sabermetrics ---
        # Pitcher efficiency: outs per pitch. A high-Command groundballer
        # with cheap outs sits near 0.40+; a max-effort whiffer with deep
        # counts sits near 0.25.
        pitches = p.get("pitches") or 0
        p["outs_per_pitch"] = (outs / pitches) if pitches else 0.0
        # Pitches per BF — patience-induced pitch count (the inverse: how
        # hard does each batter make this pitcher work).
        p["p_per_bf"] = (pitches / bf) if bf else 0.0
        # FO-induced rate per BF (3-foul-out cap is O27-specific).
        fo_ind = p.get("fo_induced") or 0
        p["fo_pct_pit"] = (fo_ind / bf) if bf else 0.0
        # ERA+ — ERA relativized to live league ERA (lower is better, so
        # the formula inverts: 100 = league average; >100 = better than
        # league; <100 = worse).
        league_era = baselines.get("era") or 0
        if league_era > 0 and p["era"] > 0:
            p["era_plus"] = (league_era / p["era"]) * 100.0
        else:
            p["era_plus"] = 100.0

        # pVORP — runs saved relative to a replacement-level pitcher with
        # the same outs-recorded workload. Replacement allows ~120% of
        # league ERA. Keeping it in run units (not wins) so it's comparable
        # to bVORP which is also in runs.
        repl_era = baselines.get("replacement_era") or 0
        if outs and repl_era:
            # Runs saved = (replacement RA - my RA) × innings.
            # Use ra27 (run-against per 27 outs, not er-only) for VORP
            # because total runs allowed is what matters for value.
            p["vorp"] = (repl_era - p["era"]) * (outs / 27.0)
        else:
            p["vorp"] = 0.0
        # pWAR — value over replacement converted to wins.
        rpw = baselines.get("runs_per_win") or 10.0
        p["war"] = p["vorp"] / rpw if rpw else 0.0

        if wl is not None:
            pid = p.get("player_id") or p.get("id")
            d = wl.get(pid, {"w": 0, "l": 0})
            p["w"] = d["w"]
            p["l"] = d["l"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    team_count = db.fetchone("SELECT COUNT(*) as n FROM teams")
    if not team_count or team_count["n"] == 0:
        return redirect(url_for("new_league_get"))

    today = get_current_sim_date()
    today_games = []
    if today:
        today_games = db.fetchall(
            """SELECT g.*,
                      ht.name as home_name, ht.abbrev as home_abbrev,
                      at.name as away_name, at.abbrev as away_abbrev
               FROM games g
               JOIN teams ht ON g.home_team_id = ht.id
               JOIN teams at ON g.away_team_id = at.id
               WHERE g.game_date = ?
               ORDER BY g.id""",
            (today,),
        )
        _attach_hits(today_games)

    # Yesterday's finals = the most recent date < today with played=1 games.
    yesterday = None
    yesterday_games: list[dict] = []
    last_played = db.fetchone(
        "SELECT MAX(game_date) AS d FROM games WHERE played = 1"
        + (" AND game_date < ?" if today else ""),
        (today,) if today else (),
    )
    if last_played and last_played["d"]:
        yesterday = last_played["d"]
        yesterday_games = db.fetchall(
            """SELECT g.*,
                      ht.name as home_name, ht.abbrev as home_abbrev,
                      at.name as away_name, at.abbrev as away_abbrev
               FROM games g
               JOIN teams ht ON g.home_team_id = ht.id
               JOIN teams at ON g.away_team_id = at.id
               WHERE g.played = 1 AND g.game_date = ?
               ORDER BY g.id""",
            (yesterday,),
        )
        _attach_hits(yesterday_games)

    divs = _divisions()

    # Top-5 leaders for AVG / HR / RBI / W / ERA / K
    games_played_row = db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")
    games_played = games_played_row["n"] if games_played_row else 0
    min_pa = max(20, games_played // 30 * 8)
    min_outs = max(9, games_played // 30 * 5)

    top = {"avg": [], "hr": [], "rbi": [], "w": [], "era": [], "k": []}
    if games_played > 0:
        batting = db.fetchall(
            """SELECT p.id as player_id, p.name as player_name,
                      t.id as team_id, t.abbrev as team_abbrev,
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
        top["avg"] = sorted(batting, key=lambda x: x["avg"], reverse=True)[:5]
        top["hr"]  = sorted(batting, key=lambda x: x["hr"] or 0, reverse=True)[:5]
        top["rbi"] = sorted(batting, key=lambda x: x["rbi"] or 0, reverse=True)[:5]

        pitching = db.fetchall(
            f"""SELECT p.id as player_id, p.name as player_name,
                      t.id as team_id, t.abbrev as team_abbrev,
                      SUM(ps.outs_recorded) as outs,
                      SUM(ps.hits_allowed) as h, SUM(ps.runs_allowed) as r,
                      SUM(ps.er) as er,
                      SUM(ps.bb) as bb, SUM(ps.k) as k,
                      SUM(ps.hr_allowed) as hr_allowed
               FROM {_PSTATS_DEDUP_SQL} ps
               JOIN players p ON ps.player_id = p.id
               JOIN teams   t ON ps.team_id = t.id
               GROUP BY p.id
               HAVING SUM(ps.outs_recorded) >= ?""",
            (min_outs,),
        )
        wl = _pitcher_wl_map()
        _aggregate_pitcher_rows(pitching, wl)
        top["w"]   = sorted(pitching, key=lambda x: x["w"], reverse=True)[:5]
        top["era"] = sorted(pitching, key=lambda x: x["era"])[:5]
        top["k"]   = sorted(pitching, key=lambda x: x["k"] or 0, reverse=True)[:5]

    return render_template("index.html",
                           today=today,
                           today_games=today_games,
                           yesterday=yesterday,
                           yesterday_games=yesterday_games,
                           divisions=divs,
                           top=top,
                           win_pct=_win_pct,
                           gb=_gb)


@app.route("/standings")
def standings():
    leagues = _leagues_with_divisions()

    extras: dict[int, dict] = {}
    teams = db.fetchall("SELECT id FROM teams")
    for t in teams:
        tid = t["id"]
        played = db.fetchall(
            """SELECT g.id, g.game_date, g.home_team_id, g.away_team_id,
                      g.home_score, g.away_score, g.winner_id
               FROM games g
               WHERE g.played = 1 AND (g.home_team_id = ? OR g.away_team_id = ?)
               ORDER BY g.game_date, g.id""",
            (tid, tid),
        )
        rs = ra = w10 = l10 = 0
        for g in played:
            if g["home_team_id"] == tid:
                rs += g["home_score"] or 0
                ra += g["away_score"] or 0
            else:
                rs += g["away_score"] or 0
                ra += g["home_score"] or 0
        for g in played[-10:]:
            if g["winner_id"] == tid:
                w10 += 1
            else:
                l10 += 1
        streak = ""
        if played:
            last_won = (played[-1]["winner_id"] == tid)
            count = 0
            for g in reversed(played):
                if (g["winner_id"] == tid) == last_won:
                    count += 1
                else:
                    break
            streak = ("W" if last_won else "L") + str(count)
        last5 = [("w" if g["winner_id"] == tid else "l") for g in played[-5:]]
        # Pythagorean W% — RS² / (RS² + RA²) over actual run differential.
        # Bill James's original 2.0 exponent is fine for O27 (the
        # exponent doesn't change much by run environment for simple
        # reasoning; the pythagopat extension would be tighter but
        # adds complexity for marginal gain).
        if rs + ra > 0:
            pyth_win_pct = (rs * rs) / (rs * rs + ra * ra)
        else:
            pyth_win_pct = 0.5
        # Pythagorean expected W-L in the same number of games played.
        n_games = len(played)
        pyth_w  = round(pyth_win_pct * n_games)
        pyth_l  = n_games - pyth_w
        extras[tid] = {
            "l10":      f"{w10}-{l10}",
            "streak":   streak,
            "rs":       rs,
            "ra":       ra,
            "diff":     rs - ra,
            "last5":    last5,
            "pyth_pct": pyth_win_pct,
            "pyth_wl":  f"{pyth_w}-{pyth_l}",
        }

    return render_template("standings.html",
                           leagues=leagues,
                           extras=extras,
                           win_pct=_win_pct,
                           gb=_gb)


@app.route("/schedule")
def schedule():
    team_id = request.args.get("team", type=int)
    status  = request.args.get("status", "all")

    sql = """
        SELECT g.*,
               ht.name as home_name, ht.abbrev as home_abbrev,
               at.name as away_name, at.abbrev as away_abbrev,
               wt.abbrev as winner_abbrev
        FROM games g
        JOIN teams ht ON g.home_team_id = ht.id
        JOIN teams at ON g.away_team_id = at.id
        LEFT JOIN teams wt ON g.winner_id = wt.id
    """
    where_clauses = []
    params: list = []

    if team_id:
        where_clauses.append("(g.home_team_id = ? OR g.away_team_id = ?)")
        params += [team_id, team_id]
    if status == "played":
        where_clauses.append("g.played = 1")
    elif status == "unplayed":
        where_clauses.append("g.played = 0")

    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += " ORDER BY g.game_date, g.id LIMIT 200"

    games       = db.fetchall(sql, tuple(params))
    teams       = db.fetchall("SELECT id, name, abbrev FROM teams ORDER BY name")
    selected_team = None
    if team_id:
        selected_team = db.fetchone("SELECT * FROM teams WHERE id = ?", (team_id,))

    return render_template("schedule.html",
                           games=games,
                           teams=teams,
                           selected_team=selected_team,
                           status=status)


@app.route("/game/<int:game_id>")
def game_detail(game_id: int):
    game = db.fetchone(
        """SELECT g.*,
                  ht.name as home_name, ht.abbrev as home_abbrev,
                  at.name as away_name, at.abbrev as away_abbrev,
                  wt.name as winner_name
           FROM games g
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           LEFT JOIN teams wt ON g.winner_id = wt.id
           WHERE g.id = ?""", (game_id,)
    )
    if not game:
        abort(404)

    prev_game = db.fetchone(
        """SELECT id FROM games
           WHERE played = 1
             AND (game_date < ? OR (game_date = ? AND id < ?))
           ORDER BY game_date DESC, id DESC LIMIT 1""",
        (game["game_date"], game["game_date"], game_id),
    )
    next_game = db.fetchone(
        """SELECT id FROM games
           WHERE played = 1
             AND (game_date > ? OR (game_date = ? AND id > ?))
           ORDER BY game_date ASC, id ASC LIMIT 1""",
        (game["game_date"], game["game_date"], game_id),
    )

    # Task #58: pull per-phase rows and group them. Phase 0 = regulation;
    # phase N>=1 = super-inning round N. We also build per-phase totals
    # rows (suitable for the Game Totals section in the template).
    away_batting_rows = db.fetchall(
        """SELECT bs.*, p.name as player_name, p.position
           FROM game_batter_stats bs JOIN players p ON bs.player_id = p.id
           WHERE bs.game_id = ? AND bs.team_id = ? ORDER BY bs.phase, bs.id""",
        (game_id, game["away_team_id"]))
    home_batting_rows = db.fetchall(
        """SELECT bs.*, p.name as player_name, p.position
           FROM game_batter_stats bs JOIN players p ON bs.player_id = p.id
           WHERE bs.game_id = ? AND bs.team_id = ? ORDER BY bs.phase, bs.id""",
        (game_id, game["home_team_id"]))
    away_pitching_rows = db.fetchall(
        """SELECT ps.*, p.name as player_name
           FROM game_pitcher_stats ps JOIN players p ON ps.player_id = p.id
           WHERE ps.game_id = ? AND ps.team_id = ? ORDER BY ps.phase, ps.id""",
        (game_id, game["away_team_id"]))
    home_pitching_rows = db.fetchall(
        """SELECT ps.*, p.name as player_name
           FROM game_pitcher_stats ps JOIN players p ON ps.player_id = p.id
           WHERE ps.game_id = ? AND ps.team_id = ? ORDER BY ps.phase, ps.id""",
        (game_id, game["home_team_id"]))

    team_phase_outs_rows = db.fetchall(
        """SELECT team_id, phase, unattributed_outs FROM team_phase_outs
           WHERE game_id = ?""", (game_id,))

    # Legacy data (pre-Task-#58) often has duplicate rows for the same
    # (player_id, game_id) because the schema lacked a UNIQUE constraint
    # and re-sims of the same game inserted parallel copies. New rows
    # are unique on (player_id, game_id, phase). Aggregate duplicates
    # here so the box score never shows the same player twice in one
    # phase or double-counts totals.
    _BAT_NUM = ("pa", "ab", "runs", "hits", "doubles", "triples",
                "hr", "rbi", "bb", "k", "stays", "outs_recorded")
    _PIT_NUM = ("batters_faced", "outs_recorded", "hits_allowed",
                "runs_allowed", "er", "bb", "k")

    def _dedup_by_player_phase(rows: list, num_fields: tuple) -> list:
        merged: dict[tuple, dict] = {}
        order: list[tuple] = []
        for r in rows:
            key = (r["phase"] or 0, r["player_id"])
            if key not in merged:
                merged[key] = dict(r)
                order.append(key)
            else:
                acc = merged[key]
                for f in num_fields:
                    acc[f] = (acc.get(f) or 0) + (r[f] or 0)
        return [merged[k] for k in order]

    def _group_by_phase(rows: list) -> dict:
        out: dict[int, list] = {}
        for r in rows:
            out.setdefault(r["phase"] or 0, []).append(r)
        return out

    def _aggregate_batting(rows: list) -> dict:
        agg = {f: 0 for f in ("pa", "ab", "runs", "hits", "doubles", "triples",
                              "hr", "rbi", "bb", "k", "stays", "outs_recorded")}
        for r in rows:
            for f in agg:
                agg[f] += (r[f] or 0)
        return agg

    def _aggregate_pitching(rows: list) -> dict:
        agg = {f: 0 for f in ("batters_faced", "outs_recorded", "hits_allowed",
                              "runs_allowed", "er", "bb", "k")}
        for r in rows:
            for f in agg:
                agg[f] += (r[f] or 0)
        return agg

    away_batting_rows = _dedup_by_player_phase(away_batting_rows, _BAT_NUM)
    home_batting_rows = _dedup_by_player_phase(home_batting_rows, _BAT_NUM)
    away_pitching_rows = _dedup_by_player_phase(away_pitching_rows, _PIT_NUM)
    home_pitching_rows = _dedup_by_player_phase(home_pitching_rows, _PIT_NUM)

    # Per-player Game Totals (one row per player across all phases).
    # Spec: "Game Totals — one consolidated row per player across all
    # phases." Distinct from the team-totals row at the bottom of each
    # per-phase table.
    def _consolidate_per_player(rows: list, num_fields: tuple) -> list:
        merged: dict[int, dict] = {}
        order: list[int] = []
        for r in rows:
            pid = r["player_id"]
            if pid not in merged:
                base = dict(r)
                base["phase"] = None  # consolidated row spans phases
                merged[pid] = base
                order.append(pid)
            else:
                acc = merged[pid]
                for f in num_fields:
                    acc[f] = (acc.get(f) or 0) + (r[f] or 0)
        return [merged[k] for k in order]

    away_batting_consolidated = _consolidate_per_player(away_batting_rows, _BAT_NUM)
    home_batting_consolidated = _consolidate_per_player(home_batting_rows, _BAT_NUM)
    away_pitching_consolidated = _consolidate_per_player(away_pitching_rows, _PIT_NUM)
    home_pitching_consolidated = _consolidate_per_player(home_pitching_rows, _PIT_NUM)

    away_batting_by_phase = _group_by_phase(away_batting_rows)
    home_batting_by_phase = _group_by_phase(home_batting_rows)
    away_pitching_by_phase = _group_by_phase(away_pitching_rows)
    home_pitching_by_phase = _group_by_phase(home_pitching_rows)

    # Determine which phases to render. Always include 0; include N>=1
    # only if any side actually played that phase (super-inning round).
    all_phases: set[int] = {0}
    for d in (away_batting_by_phase, home_batting_by_phase,
              away_pitching_by_phase, home_pitching_by_phase):
        all_phases.update(d.keys())
    phases = sorted(all_phases)
    si_rounds = max(0, max(phases) if phases else 0)

    # Line score: runs per phase, plus H and "team errors" placeholder.
    def _line_score(b_by_phase: dict) -> dict:
        runs_per = {ph: sum(r["runs"] or 0 for r in rows)
                    for ph, rows in b_by_phase.items()}
        hits_per = {ph: sum(r["hits"] or 0 for r in rows)
                    for ph, rows in b_by_phase.items()}
        return {
            "runs":  runs_per,
            "hits":  hits_per,
            "total_r": sum(runs_per.values()),
            "total_h": sum(hits_per.values()),
        }

    away_line = _line_score(away_batting_by_phase)
    home_line = _line_score(home_batting_by_phase)

    # Game Notes: per-side unattributed outs by phase (CS / FC / pickoff
    # outs the engine couldn't charge to a specific batter).
    notes: list[dict] = []
    team_name_by_id = {
        game["away_team_id"]: game["away_name"],
        game["home_team_id"]: game["home_name"],
    }
    for r in team_phase_outs_rows:
        if (r["unattributed_outs"] or 0) <= 0:
            continue
        phase_label = "Regulation" if r["phase"] == 0 else f"SI Round {r['phase']}"
        notes.append({
            "team":  team_name_by_id.get(r["team_id"], "?"),
            "phase": r["phase"],
            "phase_label": phase_label,
            "outs":  r["unattributed_outs"],
        })

    return render_template(
        "game.html",
        game=game,
        phases=phases,
        si_rounds=si_rounds,
        away_batting_by_phase=away_batting_by_phase,
        home_batting_by_phase=home_batting_by_phase,
        away_pitching_by_phase=away_pitching_by_phase,
        home_pitching_by_phase=home_pitching_by_phase,
        away_batting_total=_aggregate_batting(away_batting_rows),
        home_batting_total=_aggregate_batting(home_batting_rows),
        away_pitching_total=_aggregate_pitching(away_pitching_rows),
        home_pitching_total=_aggregate_pitching(home_pitching_rows),
        away_batting_consolidated=away_batting_consolidated,
        home_batting_consolidated=home_batting_consolidated,
        away_pitching_consolidated=away_pitching_consolidated,
        home_pitching_consolidated=home_pitching_consolidated,
        away_line=away_line,
        home_line=home_line,
        game_notes=notes,
        prev_game_id=(prev_game["id"] if prev_game else None),
        next_game_id=(next_game["id"] if next_game else None),
    )


# ---------------------------------------------------------------------------
# Players index (NEW) + leaders (renamed from /stats)
# ---------------------------------------------------------------------------

@app.route("/players")
def players():
    kind = request.args.get("kind", "batters")
    if kind not in ("batters", "pitchers", "both"):
        kind = "batters"
    selected_team_id = request.args.get("team", type=int)
    selected_pos = request.args.get("pos", "") or ""
    q = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 50

    where = []
    params: list = []
    if selected_team_id:
        where.append("p.team_id = ?")
        params.append(selected_team_id)
    if selected_pos:
        where.append("p.position = ?")
        params.append(selected_pos)
    if q:
        where.append("LOWER(p.name) LIKE ?")
        params.append(f"%{q.lower()}%")
    if kind == "batters":
        where.append("p.is_pitcher = 0")
    elif kind == "pitchers":
        where.append("p.is_pitcher = 1")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    total_row = db.fetchone(f"SELECT COUNT(*) AS n FROM players p{where_sql}", tuple(params))
    total = total_row["n"] if total_row else 0
    pages = max(1, math.ceil(total / per_page))
    if page > pages:
        page = pages
    offset = (page - 1) * per_page

    base = db.fetchall(
        f"""SELECT p.id, p.name, p.team_id, p.position, p.age, p.is_pitcher, p.is_joker, p.pitcher_role,
                   t.abbrev AS team_abbrev
            FROM players p JOIN teams t ON p.team_id = t.id
            {where_sql}
            ORDER BY p.name
            LIMIT ? OFFSET ?""",
        tuple(params) + (per_page, offset),
    )
    page_ids = [p["id"] for p in base]
    if not page_ids:
        return render_template(
            "players.html",
            kind=kind, batters=[], pitchers=[],
            all_teams=db.fetchall("SELECT id, name, abbrev FROM teams ORDER BY name"),
            all_positions=[r["position"] for r in db.fetchall("SELECT DISTINCT position FROM players ORDER BY position")],
            selected_team_id=selected_team_id, selected_pos=selected_pos, q=q,
            total=total, page=page, pages=pages,
        )

    ph = ",".join("?" * len(page_ids))

    batter_rows = []
    pitcher_rows = []

    if kind in ("batters", "both"):
        bstats = {
            r["player_id"]: r for r in db.fetchall(
                f"""SELECT bs.player_id,
                           COUNT(bs.game_id) AS gp,
                           SUM(bs.pa) AS pa, SUM(bs.ab) AS ab, SUM(bs.hits) AS h,
                           SUM(bs.doubles) AS d2, SUM(bs.triples) AS d3, SUM(bs.hr) AS hr,
                           SUM(bs.runs) AS r, SUM(bs.rbi) AS rbi,
                           SUM(bs.bb) AS bb, SUM(bs.k) AS k
                    FROM game_batter_stats bs
                    WHERE bs.player_id IN ({ph})
                    GROUP BY bs.player_id""",
                tuple(page_ids),
            )
        }
        for p in base:
            if p["is_pitcher"] and kind == "both":
                continue
            row = dict(p)
            s = bstats.get(p["id"], {})
            row.update(s)
            _aggregate_batter_rows([row])
            batter_rows.append(row)

    if kind in ("pitchers", "both"):
        pstats = {
            r["player_id"]: r for r in db.fetchall(
                f"""SELECT ps.player_id,
                           COUNT(ps.game_id) AS gp,
                           SUM(ps.outs_recorded) AS outs,
                           SUM(ps.hits_allowed) AS h, SUM(ps.runs_allowed) AS r,
                           SUM(ps.er) AS er,
                           SUM(ps.bb) AS bb, SUM(ps.k) AS k,
                           SUM(ps.hr_allowed) AS hr_allowed
                    FROM {_PSTATS_DEDUP_SQL} ps
                    WHERE ps.player_id IN ({ph})
                    GROUP BY ps.player_id""",
                tuple(page_ids),
            )
        }
        wl = _pitcher_wl_map()
        for p in base:
            if not p["is_pitcher"] and kind == "both":
                continue
            row = dict(p)
            s = pstats.get(p["id"], {})
            row.update(s)
            _aggregate_pitcher_rows([row], wl)
            pitcher_rows.append(row)

    return render_template(
        "players.html",
        kind=kind,
        batters=batter_rows,
        pitchers=pitcher_rows,
        all_teams=db.fetchall("SELECT id, name, abbrev FROM teams ORDER BY name"),
        all_positions=[r["position"] for r in db.fetchall("SELECT DISTINCT position FROM players ORDER BY position")],
        selected_team_id=selected_team_id,
        selected_pos=selected_pos,
        q=q,
        total=total, page=page, pages=pages,
    )


@app.route("/stats")
def stats_browse():
    """Full sortable, filterable batting + pitching tables.

    Query params:
      side=bat|pit         — which table to show (default: bat)
      team=<id|all>        — restrict to one team
      pos=<P|hitter|all>   — restrict by position class
      min_pa=<int>         — minimum PA gate for batting
      min_outs=<int>       — minimum outs gate for pitching
      qualified=1|0        — convenience: ~3.1 PA per team-game / 1 out per team-game
    """
    side       = (request.args.get("side") or "bat").lower()
    team_arg   = request.args.get("team")  or "all"
    pos_arg    = (request.args.get("pos") or "all").lower()
    qualified  = request.args.get("qualified") == "1"
    name_query = (request.args.get("q") or "").strip()

    games_played = db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")["n"] or 0
    teams_total  = db.fetchone("SELECT COUNT(*) as n FROM teams")["n"] or 30
    games_per_team = (games_played * 2) // max(1, teams_total)   # both teams play in each game

    # Qualified-only thresholds, MLB-equivalent scaled to O27.
    # Batting: ~3.1 PA per team-game (MLB uses 3.1 PA/G).
    # Pitching: ~1 out per team-game (rough O27 analog of 1 IP/G).
    qual_pa   = max(1, int(round(games_per_team * 3.1))) if games_per_team else 1
    qual_outs = max(1, games_per_team) if games_per_team else 1

    try:
        min_pa = int(request.args.get("min_pa", "0") or 0)
    except ValueError:
        min_pa = 0
    try:
        min_outs = int(request.args.get("min_outs", "0") or 0)
    except ValueError:
        min_outs = 0
    if qualified:
        min_pa   = max(min_pa, qual_pa)
        min_outs = max(min_outs, qual_outs)

    # Team filter param resolution.
    team_filter_id = None
    if team_arg.isdigit():
        team_filter_id = int(team_arg)

    teams_list = db.fetchall(
        "SELECT id, abbrev, name, league, division FROM teams ORDER BY abbrev"
    )
    baselines = _league_baselines()

    # ----- Batting table -----
    batters: list[dict] = []
    pitchers: list[dict] = []

    if side == "bat":
        where_clauses = ["bs.pa > 0"]
        params: list = []
        if team_filter_id is not None:
            where_clauses.append("bs.team_id = ?")
            params.append(team_filter_id)
        if pos_arg in ("hitter", "non_pitcher"):
            where_clauses.append("p.is_pitcher = 0")
        elif pos_arg in ("p", "pitcher"):
            where_clauses.append("p.is_pitcher = 1")
        if name_query:
            where_clauses.append("p.name LIKE ?")
            params.append(f"%{name_query}%")
        where_sql = " AND ".join(where_clauses)
        params.append(min_pa)

        batters = db.fetchall(
            f"""SELECT p.id as player_id, p.name as player_name,
                       p.position as position, t.abbrev as team_abbrev, t.id as team_id,
                       p.is_pitcher as is_pitcher,
                       p.defense as defense, p.arm as arm,
                       p.defense_infield as defense_infield,
                       p.defense_outfield as defense_outfield,
                       p.defense_catcher as defense_catcher,
                       COUNT(bs.game_id) as g,
                       SUM(bs.pa) as pa, SUM(bs.ab) as ab, SUM(bs.hits) as h,
                       SUM(bs.doubles) as d2, SUM(bs.triples) as d3, SUM(bs.hr) as hr,
                       SUM(bs.runs) as r, SUM(bs.rbi) as rbi,
                       SUM(bs.bb) as bb, SUM(bs.k) as k, SUM(bs.stays) as stays,
                       COALESCE(SUM(bs.hbp),0) as hbp,
                       COALESCE(SUM(bs.sb),0)  as sb,
                       COALESCE(SUM(bs.cs),0)  as cs,
                       COALESCE(SUM(bs.fo),0)  as fo,
                       COALESCE(SUM(bs.multi_hit_abs),0) as mhab,
                       COALESCE(SUM(bs.stay_rbi),0)     as stay_rbi,
                       COALESCE(SUM(bs.roe),0)          as roe
                FROM game_batter_stats bs
                JOIN players p ON bs.player_id = p.id
                JOIN teams   t ON bs.team_id = t.id
                WHERE {where_sql}
                GROUP BY p.id
                HAVING SUM(bs.pa) >= ?
                ORDER BY SUM(bs.pa) DESC""",
            tuple(params),
        )
        _aggregate_batter_rows(batters, baselines=baselines)

    elif side == "pit":
        where_clauses = ["ps.outs_recorded > 0"]
        params = []
        if team_filter_id is not None:
            where_clauses.append("ps.team_id = ?")
            params.append(team_filter_id)
        # Pitching always implies pitchers.
        where_clauses.append("p.is_pitcher = 1")
        if name_query:
            where_clauses.append("p.name LIKE ?")
            params.append(f"%{name_query}%")
        where_sql = " AND ".join(where_clauses)
        params.append(min_outs)

        pitchers = db.fetchall(
            f"""SELECT p.id as player_id, p.name as player_name,
                       p.position as position, t.abbrev as team_abbrev, t.id as team_id,
                       COUNT(ps.game_id) as g,
                       SUM(ps.batters_faced)  as bf,
                       SUM(ps.outs_recorded)  as outs,
                       SUM(ps.hits_allowed)   as h,
                       SUM(ps.runs_allowed)   as r,
                       SUM(ps.er)             as er,
                       SUM(ps.bb)             as bb,
                       SUM(ps.k)              as k,
                       SUM(ps.hr_allowed)     as hr_allowed,
                       COALESCE(SUM(ps.hbp_allowed),0)   as hbp_allowed,
                       COALESCE(SUM(ps.unearned_runs),0) as uer,
                       COALESCE(SUM(ps.sb_allowed),0)    as sb_allowed,
                       COALESCE(SUM(ps.cs_caught),0)     as cs_caught,
                       COALESCE(SUM(ps.fo_induced),0)    as fo_induced,
                       COALESCE(SUM(ps.pitches),0)       as pitches
                FROM {_PSTATS_DEDUP_SQL} ps
                JOIN players p ON ps.player_id = p.id
                JOIN teams   t ON ps.team_id = t.id
                WHERE {where_sql}
                GROUP BY p.id
                HAVING SUM(ps.outs_recorded) >= ?
                ORDER BY SUM(ps.outs_recorded) DESC""",
            tuple(params),
        )
        wl = _pitcher_wl_map()
        _aggregate_pitcher_rows(pitchers, wl, baselines=baselines)
        for p in pitchers:
            outs = p["outs"] or 0
            p["os_pct"] = (outs / (27.0 * p["g"])) if p["g"] else 0.0

    return render_template(
        "stats_browse.html",
        side=side,
        team_arg=team_arg,
        pos_arg=pos_arg,
        min_pa=min_pa,
        min_outs=min_outs,
        qualified=qualified,
        qual_pa=qual_pa,
        qual_outs=qual_outs,
        name_query=name_query,
        teams_list=teams_list,
        batters=batters,
        pitchers=pitchers,
        games_played=games_played,
    )


@app.route("/leaders")
def leaders():
    games_played = db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")["n"]
    if games_played == 0:
        return render_template("leaders.html",
                               games_played=0, batting=[], pitching=[],
                               min_pa=0, min_outs=0)

    # Scale qualifying minimums by games-per-team, not by total league games.
    # MLB rule of thumb: 3.1 PA/team-game qualifies for batting title; here we
    # use ~1× games/team for batting and ~1× games/team in outs for pitching,
    # so leaders are visible from week one and grow naturally with the season.
    num_teams = db.fetchone("SELECT COUNT(*) as n FROM teams")["n"] or 2
    games_per_team = max(1, (games_played * 2) // num_teams)
    min_pa   = max(3, games_per_team)        # ~1 PA/team-game
    min_outs = max(3, games_per_team)        # ~1 out/team-game (very lenient)

    batting = db.fetchall(
        """SELECT p.id as player_id, p.name as player_name, p.position,
                  t.abbrev as team_abbrev, t.id as team_id,
                  COUNT(bs.game_id) as g,
                  SUM(bs.pa) as pa, SUM(bs.ab) as ab, SUM(bs.hits) as h,
                  SUM(bs.doubles) as d2, SUM(bs.triples) as d3, SUM(bs.hr) as hr,
                  SUM(bs.runs) as r, SUM(bs.rbi) as rbi,
                  SUM(bs.bb) as bb, SUM(bs.k) as k, SUM(bs.stays) as stays,
                  COALESCE(SUM(bs.hbp),0) as hbp,
                  COALESCE(SUM(bs.sb),0)  as sb,
                  COALESCE(SUM(bs.cs),0)  as cs,
                  COALESCE(SUM(bs.fo),0)  as fo,
                  COALESCE(SUM(bs.multi_hit_abs),0) as mhab,
                  COALESCE(SUM(bs.stay_rbi),0)     as stay_rbi
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           JOIN teams   t ON bs.team_id = t.id
           GROUP BY p.id
           HAVING SUM(bs.pa) >= ?""",
        (min_pa,),
    )
    baselines = _league_baselines()
    _aggregate_batter_rows(batting, baselines=baselines)

    pitching = db.fetchall(
        f"""SELECT p.id as player_id, p.name as player_name,
                  t.abbrev as team_abbrev, t.id as team_id,
                  COUNT(ps.game_id) as g,
                  SUM(ps.batters_faced)  as bf,
                  SUM(ps.outs_recorded)  as outs,
                  SUM(ps.hits_allowed)   as h,
                  SUM(ps.runs_allowed)   as r,
                  SUM(ps.er)             as er,
                  SUM(ps.bb)             as bb,
                  SUM(ps.k)              as k,
                  SUM(ps.hr_allowed)     as hr_allowed,
                  COALESCE(SUM(ps.hbp_allowed),0)   as hbp_allowed,
                  COALESCE(SUM(ps.unearned_runs),0) as uer,
                  COALESCE(SUM(ps.sb_allowed),0)    as sb_allowed,
                  COALESCE(SUM(ps.cs_caught),0)     as cs_caught,
                  COALESCE(SUM(ps.fo_induced),0)    as fo_induced,
                  COALESCE(SUM(ps.pitches),0)       as pitches
           FROM {_PSTATS_DEDUP_SQL} ps
           JOIN players p ON ps.player_id = p.id
           JOIN teams   t ON ps.team_id = t.id
           GROUP BY p.id
           HAVING SUM(ps.outs_recorded) >= ?""",
        (min_outs,),
    )
    # Shared helper now produces era/whip/k27/bb27/fip + advanced stats.
    wl = _pitcher_wl_map()
    _aggregate_pitcher_rows(pitching, wl, baselines=baselines)
    for p in pitching:
        outs = p["outs"] or 0
        # OS% = share of a complete game (27 outs) recorded per appearance.
        p["os_pct"] = (outs / (27.0 * p["g"])) if p["g"] else 0.0

    return render_template(
        "leaders.html",
        games_played=games_played,
        min_pa=min_pa, min_outs=min_outs,
        batting=batting, pitching=pitching,
    )


@app.route("/player/<int:player_id>")
def player_detail(player_id: int):
    player = db.fetchone(
        """SELECT p.*, t.abbrev as team_abbrev, t.name as team_name, t.id as team_id
           FROM players p JOIN teams t ON p.team_id = t.id
           WHERE p.id = ?""",
        (player_id,),
    )
    if not player:
        abort(404)

    batting_log = db.fetchall(
        """SELECT bs.*, g.game_date, g.id as game_id, g.home_team_id, g.away_team_id,
                  ht.abbrev as home_abbrev, at.abbrev as away_abbrev
           FROM game_batter_stats bs
           JOIN games g ON bs.game_id = g.id
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE bs.player_id = ?
           ORDER BY g.game_date DESC, g.id DESC LIMIT 50""",
        (player_id,),
    )
    # Dedup pitching log to one row per game appearance (Task #57 audit).
    pitching_log = db.fetchall(
        f"""SELECT ps.*, g.game_date, g.id as game_id, g.home_team_id, g.away_team_id,
                  ht.abbrev as home_abbrev, at.abbrev as away_abbrev
           FROM {_PSTATS_DEDUP_SQL} ps
           JOIN games g ON ps.game_id = g.id
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE ps.player_id = ?
           ORDER BY g.game_date DESC, g.id DESC LIMIT 50""",
        (player_id,),
    )

    bt = db.fetchone(
        """SELECT COUNT(*) as g, SUM(pa) as pa, SUM(ab) as ab, SUM(hits) as h,
                  SUM(doubles) as d2, SUM(triples) as d3, SUM(hr) as hr,
                  SUM(runs) as r, SUM(rbi) as rbi, SUM(bb) as bb, SUM(k) as k,
                  SUM(stays) as stays,
                  COALESCE(SUM(hbp),0) as hbp,
                  COALESCE(SUM(sb),0)  as sb,
                  COALESCE(SUM(cs),0)  as cs,
                  COALESCE(SUM(fo),0)  as fo,
                  COALESCE(SUM(multi_hit_abs),0) as mhab,
                  COALESCE(SUM(stay_rbi),0)     as stay_rbi
           FROM game_batter_stats WHERE player_id = ?""",
        (player_id,),
    )
    pt = db.fetchone(
        f"""SELECT COUNT(*) as g, SUM(batters_faced) as bf, SUM(outs_recorded) as outs,
                   SUM(hits_allowed) as h, SUM(runs_allowed) as r,
                   SUM(er) as er,
                   SUM(bb) as bb, SUM(k) as k,
                   SUM(hr_allowed) as hr_allowed,
                   COALESCE(SUM(hbp_allowed),0)   as hbp_allowed,
                   COALESCE(SUM(unearned_runs),0) as uer,
                   COALESCE(SUM(sb_allowed),0)    as sb_allowed,
                   COALESCE(SUM(cs_caught),0)     as cs_caught,
                   COALESCE(SUM(fo_induced),0)    as fo_induced,
                   COALESCE(SUM(pitches),0)       as pitches
            FROM {_PSTATS_DEDUP_SQL} ps WHERE ps.player_id = ?""",
        (player_id,),
    )

    baselines = _league_baselines()
    wl = _pitcher_wl_map()

    bt_totals = None
    if bt and bt["pa"]:
        # Player-detail batter row needs `position` + the defense ratings
        # for DRS/dWAR; pull them from the player record so the aggregator
        # can compute the full sabermetric suite consistently.
        bt_totals = dict(bt)
        bt_totals["position"]         = player.get("position")
        bt_totals["defense"]          = player.get("defense")
        bt_totals["defense_infield"]  = player.get("defense_infield")
        bt_totals["defense_outfield"] = player.get("defense_outfield")
        bt_totals["defense_catcher"]  = player.get("defense_catcher")
        _aggregate_batter_rows([bt_totals], baselines=baselines)

    pt_totals = None
    if pt and pt["outs"]:
        outs = pt["outs"] or 0
        pt_totals = dict(pt)
        pt_totals["player_id"] = player_id
        _aggregate_pitcher_rows([pt_totals], wl=wl, baselines=baselines)
        pt_totals["os_pct"] = (outs / (27.0 * pt["g"])) if pt["g"] else 0.0

    return render_template(
        "player.html",
        player=player,
        batting_log=batting_log,
        pitching_log=pitching_log,
        bt_totals=bt_totals,
        pt_totals=pt_totals,
        baselines=baselines,
    )


@app.route("/teams")
def teams():
    teams_list = db.fetchall(
        """SELECT t.*, COUNT(p.id) as player_count
           FROM teams t LEFT JOIN players p ON p.team_id = t.id
           GROUP BY t.id
           ORDER BY t.league, t.division, t.name"""
    )
    return render_template("teams.html", teams=teams_list, win_pct=_win_pct)


@app.route("/team/<int:team_id>")
def team_detail(team_id: int):
    team = db.fetchone("SELECT * FROM teams WHERE id = ?", (team_id,))
    if not team:
        abort(404)

    roster = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? ORDER BY is_pitcher, position, id",
        (team_id,),
    )
    ids = [p["id"] for p in roster]
    bstats: dict[int, dict] = {}
    pstats: dict[int, dict] = {}
    if ids:
        ph = ",".join("?" * len(ids))
        for r in db.fetchall(
            f"""SELECT player_id,
                       COUNT(game_id) AS gp,
                       SUM(pa) AS pa, SUM(ab) AS ab, SUM(hits) AS h,
                       SUM(doubles) AS d2, SUM(triples) AS d3, SUM(hr) AS hr,
                       SUM(runs) AS r, SUM(rbi) AS rbi,
                       SUM(bb) AS bb, SUM(k) AS k,
                       COALESCE(SUM(hbp),0) AS hbp,
                       COALESCE(SUM(sb),0)  AS sb,
                       COALESCE(SUM(cs),0)  AS cs,
                       COALESCE(SUM(fo),0)  AS fo,
                       COALESCE(SUM(multi_hit_abs),0) AS mhab
                FROM game_batter_stats
                WHERE player_id IN ({ph}) GROUP BY player_id""",
            tuple(ids),
        ):
            bstats[r["player_id"]] = r
        for r in db.fetchall(
            f"""SELECT ps.player_id,
                       COUNT(ps.game_id) AS gp,
                       SUM(ps.batters_faced) AS bf,
                       SUM(ps.outs_recorded) AS outs,
                       SUM(ps.hits_allowed) AS h, SUM(ps.runs_allowed) AS r, SUM(ps.er) AS er,
                       SUM(ps.bb) AS bb, SUM(ps.k) AS k,
                       SUM(ps.hr_allowed) AS hr_allowed,
                       COALESCE(SUM(ps.hbp_allowed),0)   AS hbp_allowed,
                       COALESCE(SUM(ps.unearned_runs),0) AS uer,
                       COALESCE(SUM(ps.sb_allowed),0)    AS sb_allowed,
                       COALESCE(SUM(ps.cs_caught),0)     AS cs_caught,
                       COALESCE(SUM(ps.fo_induced),0)    AS fo_induced
                FROM {_PSTATS_DEDUP_SQL} ps
                WHERE ps.player_id IN ({ph}) GROUP BY ps.player_id""",
            tuple(ids),
        ):
            pstats[r["player_id"]] = r

    wl = _pitcher_wl_map()
    batters: list[dict] = []
    pitchers: list[dict] = []
    for p in roster:
        if p["is_pitcher"]:
            row = dict(p)
            row.update(pstats.get(p["id"], {}))
            _aggregate_pitcher_rows([row], wl)
            pitchers.append(row)
        else:
            row = dict(p)
            row.update(bstats.get(p["id"], {}))
            _aggregate_batter_rows([row])
            batters.append(row)

    recent = db.fetchall(
        """SELECT g.*,
                  ht.name as home_name, ht.abbrev as home_abbrev,
                  at.name as away_name, at.abbrev as away_abbrev,
                  wt.abbrev as winner_abbrev
           FROM games g
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           LEFT JOIN teams wt ON g.winner_id = wt.id
           WHERE g.played = 1 AND (g.home_team_id = ? OR g.away_team_id = ?)
           ORDER BY g.game_date DESC LIMIT 10""",
        (team_id, team_id),
    )
    return render_template("team.html",
                           team=team,
                           batters=batters,
                           pitchers=pitchers,
                           recent=recent,
                           win_pct=_win_pct)


@app.route("/transactions")
def transactions():
    from o27v2.transactions import get_transactions
    team_id    = request.args.get("team", type=int)
    event_type = request.args.get("type")

    txns  = get_transactions(team_id=team_id, event_type=event_type or None, limit=300)
    teams = db.fetchall("SELECT id, name, abbrev FROM teams ORDER BY name")

    event_types = ["injury", "return", "promotion", "penalty", "deadline_trade", "inseason_trade", "waiver"]
    selected_team = None
    if team_id:
        selected_team = db.fetchone("SELECT * FROM teams WHERE id = ?", (team_id,))

    counts = {et: 0 for et in event_types}
    all_txns = get_transactions(limit=50000)
    for tx in all_txns:
        et = tx.get("event_type", "")
        if et in counts:
            counts[et] += 1

    return render_template("transactions.html",
                           transactions=txns,
                           teams=teams,
                           selected_team=selected_team,
                           event_type=event_type or "",
                           event_types=event_types,
                           counts=counts)


@app.route("/new-league", methods=["GET"])
def new_league_get():
    configs = get_league_configs()
    current_team_count = db.fetchone("SELECT COUNT(*) as n FROM teams")
    current_n = current_team_count["n"] if current_team_count else 0
    return render_template("new_league.html",
                           configs=configs,
                           current_team_count=current_n)


@app.route("/new-league", methods=["POST"])
def new_league_post():
    from o27v2.league import seed_league
    from o27v2.schedule import seed_schedule

    config_id  = request.form.get("config_id", "30teams")
    rng_seed   = int(request.form.get("rng_seed", 42))

    configs = get_league_configs()
    if config_id not in configs:
        abort(400, f"Unknown config: {config_id}")

    from o27v2.season_archive import set_active_league_meta
    db.drop_all()
    db.init_db()
    seed_league(rng_seed=rng_seed, config_id=config_id)
    seed_schedule(config_id=config_id, rng_seed=rng_seed)
    set_active_league_meta(rng_seed, config_id)

    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.route("/api/sim", methods=["POST"])
def api_sim():
    data      = request.get_json(silent=True) or {}
    n         = int(data.get("n", 5))
    n         = max(1, min(n, 50))
    seed_base = data.get("seed_base")
    results   = simulate_next_n(n, seed_base=seed_base)
    resync_sim_clock()
    return jsonify({"simulated": len(results), "results": results})


@app.route("/api/sim/today", methods=["POST"])
def api_sim_today():
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    results = simulate_date(current)
    next_day = (_dt.date.fromisoformat(current) + _dt.timedelta(days=1)).isoformat()
    advance_sim_clock(_clamp_to_last(next_day))
    return jsonify(_sim_response(current, current, results))


@app.route("/api/sim/week", methods=["POST"])
def api_sim_week():
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    target  = (_dt.date.fromisoformat(current) + _dt.timedelta(days=6)).isoformat()
    results = simulate_through(target)
    next_day = (_dt.date.fromisoformat(target) + _dt.timedelta(days=1)).isoformat()
    advance_sim_clock(_clamp_to_last(next_day))
    return jsonify(_sim_response(current, target, results))


@app.route("/api/sim/month", methods=["POST"])
def api_sim_month():
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    target  = _end_of_month(_dt.date.fromisoformat(current)).isoformat()
    results = simulate_through(target)
    next_day = (_dt.date.fromisoformat(target) + _dt.timedelta(days=1)).isoformat()
    advance_sim_clock(_clamp_to_last(next_day))
    return jsonify(_sim_response(current, target, results))


@app.route("/api/sim/all-star", methods=["POST"])
def api_sim_all_star():
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    target  = get_all_star_date()
    if target is None or current > target:
        return jsonify(_sim_response(current, target, []))
    results = simulate_through(target)
    next_day = (_dt.date.fromisoformat(target) + _dt.timedelta(days=1)).isoformat()
    advance_sim_clock(_clamp_to_last(next_day))
    return jsonify(_sim_response(current, target, results))


@app.route("/api/sim/season", methods=["POST"])
def api_sim_season():
    from o27v2.season_archive import archive_current_season
    if is_season_complete():
        # Already complete — archive if we haven't snapshotted this season yet.
        sid = archive_current_season(run_invariants=True)
        resp = _sim_response(None, None, [])
        resp["archived_season_id"] = sid
        return jsonify(resp)
    current = get_current_sim_date()
    last    = get_last_scheduled_date()
    results = simulate_through(last)
    next_day = (_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat()
    advance_sim_clock(next_day)
    # Auto-archive on completion: simulating through the last scheduled date
    # finishes the season, so snapshot leaders/standings/invariants now.
    archived_id = None
    if is_season_complete():
        try:
            archived_id = archive_current_season(run_invariants=True)
        except Exception as e:
            archived_id = None
            app.logger.exception("auto-archive after /api/sim/season failed: %s", e)
    resp = _sim_response(current, last, results)
    resp["archived_season_id"] = archived_id
    return jsonify(resp)


# ---------------------------------------------------------------------------
# Task #62: season lifecycle (reset + multi-season + history)
# ---------------------------------------------------------------------------

@app.route("/api/sim/multi-season", methods=["POST"])
def api_sim_multi_season():
    """Start an N-season run in the background. Returns 202; the dashboard
    polls /api/sim/multi-season/status for progress (current season number,
    games simmed) and redirects to /seasons when the run completes."""
    from o27v2.season_archive import start_multi_season
    data = request.get_json(silent=True) or {}
    n         = int(data.get("n", 3))
    base_seed = int(data.get("seed", 42))
    config_id = (data.get("config_id") or "30teams").strip()
    if config_id not in get_league_configs():
        return jsonify({"ok": False, "error": f"unknown config: {config_id}"}), 400
    started, msg = start_multi_season(n, base_seed=base_seed, config_id=config_id)
    return jsonify({"ok": started, "message": msg}), (202 if started else 409)


@app.route("/api/sim/multi-season/status")
def api_sim_multi_season_status():
    from o27v2.season_archive import multi_season_status
    return jsonify(multi_season_status())


@app.route("/api/season/archive", methods=["POST"])
def api_season_archive():
    """Snapshot the current DB into the seasons history (no reset)."""
    from o27v2.season_archive import archive_current_season
    sid = archive_current_season(run_invariants=True)
    if sid is None:
        return jsonify({"ok": False, "message": "Nothing to archive (no played games)."}), 400
    return jsonify({"ok": True, "season_id": sid})


@app.route("/api/season/reset", methods=["POST"])
def api_season_reset():
    """One-click 'New season' — optionally archive first, then drop+reseed.

    Body: {archive: bool, config_id: str, rng_seed: int}
    """
    from o27v2.league import seed_league
    from o27v2.schedule import seed_schedule
    from o27v2.season_archive import archive_current_season, set_active_league_meta

    data = request.get_json(silent=True) or {}
    new_config_id = (data.get("config_id") or "30teams").strip()
    new_rng_seed  = int(data.get("rng_seed", 42))
    do_archive    = bool(data.get("archive", True))

    if new_config_id not in get_league_configs():
        return jsonify({"ok": False, "error": f"unknown config: {new_config_id}"}), 400

    # Archive the *current* season FIRST, attributed to the seed/config that
    # actually produced it (read from sim_meta inside archive_current_season).
    # Do not pass the new seed/config — that would mislabel the archived row.
    archived_id = None
    if do_archive:
        try:
            archived_id = archive_current_season(run_invariants=True)
        except Exception as e:
            return jsonify({"ok": False, "error": f"archive failed: {e}"}), 500

    # Now drop + reseed for the new season, and record the new meta so the
    # *next* archive will be attributed correctly.
    db.drop_all()
    db.init_db()
    seed_league(rng_seed=new_rng_seed, config_id=new_config_id)
    seed_schedule(config_id=new_config_id, rng_seed=new_rng_seed)
    set_active_league_meta(new_rng_seed, new_config_id)
    resync_sim_clock()
    return jsonify({"ok": True, "archived_season_id": archived_id})


@app.route("/seasons")
def seasons_index():
    from o27v2.season_archive import compute_live_season
    rows = db.fetchall(
        "SELECT * FROM seasons ORDER BY season_number DESC"
    )
    live = compute_live_season()
    return render_template("seasons.html", seasons=rows, live=live)


@app.route("/seasons/<int:season_id>")
def season_detail(season_id: int):
    season = db.fetchone("SELECT * FROM seasons WHERE id = ?", (season_id,))
    if not season:
        abort(404)
    standings = db.fetchall(
        """SELECT * FROM season_standings
            WHERE season_id = ?
            ORDER BY league, division,
                     (wins * 1.0 / NULLIF(wins+losses,0)) DESC,
                     wins DESC""",
        (season_id,),
    )
    bat = db.fetchall(
        """SELECT * FROM season_batting_leaders
            WHERE season_id = ? ORDER BY category, rank""",
        (season_id,),
    )
    pit = db.fetchall(
        """SELECT * FROM season_pitching_leaders
            WHERE season_id = ? ORDER BY category, rank""",
        (season_id,),
    )
    bat_by_cat: dict[str, list[dict]] = {}
    for r in bat:
        bat_by_cat.setdefault(r["category"], []).append(r)
    pit_by_cat: dict[str, list[dict]] = {}
    for r in pit:
        pit_by_cat.setdefault(r["category"], []).append(r)

    # Group standings by league/division
    leagues: dict[str, dict[str, list[dict]]] = {}
    for r in standings:
        leagues.setdefault(r["league"] or "—", {}).setdefault(
            r["division"] or "—", []
        ).append(r)

    return render_template(
        "season_detail.html",
        season=season,
        leagues=leagues,
        batting=bat_by_cat,
        pitching=pit_by_cat,
    )


@app.route("/api/sim/<int:game_id>", methods=["POST"])
def api_sim_game(game_id: int):
    data = request.get_json(silent=True) or {}
    seed = data.get("seed")
    try:
        result = simulate_game(game_id, seed=seed)
        resync_sim_clock()
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/league-configs")
def api_league_configs():
    return jsonify(list(get_league_configs().values()))


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})
