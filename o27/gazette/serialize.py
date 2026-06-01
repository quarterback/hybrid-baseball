"""o27.gazette.serialize — turn a day's finished games into a structured
"Game Context" payload an LLM (or a person) can render into a newspaper.

This is the data feed. It is deliberately separate from the prose layer
(`o27.gazette.prompt`): this module only *describes* what happened as plain
dicts, so the same payload can be piped to a model, dumped to a file,
hand-edited, and fed back in. The hard part of a good generated recap is
serialization, not the model call.

The inflection-point signal is real Win Probability Added, computed from
the league's own empirical WP table (`o27v2.analytics.wpa`) over the per-PA
game-state stamps in `game_pa_log` — no MLB-borrowed numbers. Reads go
through `o27v2.db`, so this tracks whichever save the host app has active.

No engine or schema changes; everything here is read-only aggregation.
"""
from __future__ import annotations

from o27v2 import db
from o27v2.analytics import wpa as _wpa


# ---------------------------------------------------------------------------
# Small describers
# ---------------------------------------------------------------------------

def _bases_label(mask: int | None) -> str:
    """Decode the 3-bit base mask (bit0=1B, bit1=2B, bit2=3B) into prose."""
    if not mask:
        return "bases empty"
    occ = []
    if mask & 1:
        occ.append("1st")
    if mask & 2:
        occ.append("2nd")
    if mask & 4:
        occ.append("3rd")
    if len(occ) == 3:
        return "bases loaded"
    if len(occ) == 1:
        return f"runner on {occ[0]}"
    return "runners on " + " and ".join(occ)


def _last_name(full: str | None) -> str:
    full = (full or "").strip()
    return full.rsplit(" ", 1)[-1] if full else ""


# Map raw engine hit_type into a short action phrase.
_HIT_PHRASE = {
    "single": "singles",
    "double": "doubles",
    "triple": "triples",
    "hr": "homers",
    "home_run": "homers",
    "out": "is retired",
    "k": "strikes out",
    "strikeout": "strikes out",
    "bb": "draws a walk",
    "walk": "draws a walk",
    "hbp": "is hit by a pitch",
}


def _runs_clause(runs: int) -> str:
    """'1 run scores' / '2 runs score' — subject-verb agreement intact."""
    if not runs:
        return ""
    noun = "run" if runs == 1 else "runs"
    verb = "scores" if runs == 1 else "score"
    return f", {runs} {noun} {verb}"


def _event_phrase(row: dict) -> str:
    """A compact human note for an inflection PA, best-effort from stamps."""
    batter = _last_name(row.get("batter_name")) or "The batter"
    ht = (row.get("hit_type") or "").lower()
    stayed = bool(row.get("was_stay")) or (row.get("choice") == "stay")
    verb = "stays" if stayed else _HIT_PHRASE.get(ht, "puts it in play")
    return f"{batter} {verb}{_runs_clause(row.get('runs_scored') or 0)}"


# ---------------------------------------------------------------------------
# Per-game context
# ---------------------------------------------------------------------------

def _inflection_points(game_id: int, home_team_id: int,
                       wp_table: dict, *, top_n: int = 4,
                       min_swing: float = 0.05) -> list[dict]:
    """Rank a game's plate appearances by absolute win-probability swing.

    WP before/after each PA is looked up from the empirical table using the
    state stamps on `game_pa_log`; the swing is from the batting team's
    perspective (positive = the batting team gained ground). The empirical
    table is only as smooth as the league has games — early in a season the
    swings are noisy; over a full schedule they settle."""
    rows = db.fetchall(
        """SELECT pa.*, b.name AS batter_name, p.name AS pitcher_name,
                  t.abbrev AS bat_abbrev
           FROM game_pa_log pa
           JOIN players b ON pa.batter_id = b.id
           LEFT JOIN players p ON pa.pitcher_id = p.id
           JOIN teams t ON pa.team_id = t.id
           WHERE pa.game_id = ? AND pa.phase = 0
             AND pa.outs_before IS NOT NULL
             AND pa.score_diff_before IS NOT NULL
           ORDER BY pa.ab_seq, pa.swing_idx""",
        (game_id,),
    )
    scored: list[dict] = []
    for r in rows:
        batting_is_home = 1 if r["team_id"] == home_team_id else 0
        wp_before = _wpa.lookup_wp(
            wp_table, batting_is_home,
            r["outs_before"], r["bases_before"], r["score_diff_before"],
        )
        wp_after = _wpa.lookup_wp(
            wp_table, batting_is_home,
            r["outs_after"], r["bases_after"], r["score_diff_after"],
        )
        if wp_before is None or wp_after is None:
            continue
        swing = wp_after - wp_before
        if abs(swing) < min_swing:
            continue
        scored.append({
            "out_window": r["outs_before"],
            "bases": _bases_label(r["bases_before"]),
            "batter": _last_name(r["batter_name"]),
            "pitcher": _last_name(r["pitcher_name"]),
            "stay": bool(r["was_stay"]) or (r["choice"] == "stay"),
            "runs_scored": r["runs_scored"] or 0,
            "win_prob_swing_pct": round(swing * 100, 1),
            "swing_for": r["bat_abbrev"],
            "note": _event_phrase(r),
            "_abs": abs(swing),
        })
    scored.sort(key=lambda d: d["_abs"], reverse=True)
    top = scored[:top_n]
    for d in top:
        d.pop("_abs", None)
    # Re-order the chosen few chronologically so the story reads forward.
    top.sort(key=lambda d: d["out_window"])
    return top


def _scoring_summary(game_id: int, away_abbrev: str, home_abbrev: str,
                     limit: int = 14) -> list[dict]:
    rows = db.fetchall(
        """SELECT se.*, b.name AS batter_name, r.name AS runner_name
           FROM game_scoring_events se
           JOIN players b ON se.batter_id = b.id
           JOIN players r ON se.runner_id = r.id
           WHERE se.game_id = ?
           ORDER BY se.seq""",
        (game_id,),
    )
    base_lbl = {0: "from 1st", 1: "from 2nd", 2: "from 3rd", 3: "on his own swing"}
    out: list[dict] = []
    for r in rows:
        out.append({
            "out_window": r["outs_before"],
            "half": r["half"],
            "batter": _last_name(r["batter_name"]),
            "runner": _last_name(r["runner_name"]),
            "how": base_lbl.get(r["runner_from_base"], ""),
            "score": f"{away_abbrev} {r['visitors_score']}, {home_abbrev} {r['home_score']}",
        })
    if len(out) > limit:
        # Keep the opening runs and the decisive late ones.
        out = out[: limit // 2] + out[-(limit - limit // 2):]
    return out


def _standouts(game_id: int, away_id: int, home_id: int) -> dict:
    """Pick one batting line and one pitching line worth naming per side.

    Note O27 hits can exceed AB (a stay-heavy AB yields multiple hits), so
    the batting line leads with counting stats rather than an MLB
    'H-for-AB' that would read as nonsense when H > AB."""
    def _bat(team_id):
        rows = db.fetchall(
            """SELECT p.name AS name,
                      SUM(bs.hits) AS h, SUM(bs.ab) AS ab, SUM(bs.hr) AS hr,
                      SUM(bs.rbi) AS rbi, SUM(bs.runs) AS r, SUM(bs.stays) AS stays
               FROM game_batter_stats bs JOIN players p ON bs.player_id = p.id
               WHERE bs.game_id = ? AND bs.team_id = ?
               GROUP BY bs.player_id""",
            (game_id, team_id),
        )
        if not rows:
            return None
        # Light game-score: RBI and HR carry, hits and runs add.
        best = max(rows, key=lambda x: (x["rbi"] or 0) * 1.5
                   + (x["hr"] or 0) * 2 + (x["h"] or 0) + (x["r"] or 0) * 0.5)
        if not (best["h"] or best["rbi"] or best["hr"]):
            return None
        parts = [f"{best['h'] or 0} H"]
        if best["hr"]:
            parts.append(f"{best['hr']} HR")
        if best["rbi"]:
            parts.append(f"{best['rbi']} RBI")
        if best["stays"]:
            parts.append(f"{best['stays']} 2C")
        return {"name": _last_name(best["name"]),
                "line": ", ".join(parts), "ab": best["ab"] or 0}

    def _pit(team_id):
        rows = db.fetchall(
            """SELECT p.name AS name,
                      SUM(ps.outs_recorded) AS outs, SUM(ps.k) AS k,
                      SUM(ps.runs_allowed) AS r, SUM(ps.er) AS er,
                      SUM(ps.hits_allowed) AS h, SUM(ps.is_starter) AS gs
               FROM game_pitcher_stats ps JOIN players p ON ps.player_id = p.id
               WHERE ps.game_id = ? AND ps.team_id = ?
               GROUP BY ps.player_id""",
            (game_id, team_id),
        )
        starters = [r for r in rows if r["gs"]] or rows
        if not starters:
            return None
        best = max(starters, key=lambda x: (x["outs"] or 0) - (x["er"] or 0) * 3
                   + (x["k"] or 0))
        return {"name": _last_name(best["name"]),
                "line": f"{best['outs'] or 0} outs, {best['k'] or 0} K, "
                        f"{best['er'] or 0} ER"}

    return {
        "away_bat": _bat(away_id), "home_bat": _bat(home_id),
        "away_arm": _pit(away_id), "home_arm": _pit(home_id),
    }


def _power_play(game_id: int) -> list[dict]:
    """Nickel (10th-fielder) deployments worth a notebook line, if any."""
    rows = db.fetchall(
        """SELECT p.name AS name, t.abbrev AS team,
                  pp.pp_deploys, pp.pp_outs, pp.pp_xbh_held, pp.pp_hits_converted
           FROM game_power_play_stats pp
           JOIN players p ON pp.player_id = p.id
           JOIN teams t ON pp.team_id = t.id
           WHERE pp.game_id = ? AND pp.pp_deploys > 0""",
        (game_id,),
    )
    return [{
        "fielder": _last_name(r["name"]),
        "team": r["team"],
        "windows": r["pp_deploys"],
        "outs_covered": r["pp_outs"],
        "xbh_held": r["pp_xbh_held"],
        "hits_run_down": r["pp_hits_converted"],
    } for r in rows]


def _declared_seconds(game: dict) -> dict | None:
    """Surface a Declared Seconds gamble (banked outs / comeback round)."""
    away_dec = game.get("away_declared_at")
    home_dec = game.get("home_declared_at")
    if away_dec is None and home_dec is None:
        return None
    out: dict = {"outcome": game.get("seconds_outcome")}
    if away_dec is not None:
        out["away"] = {
            "declared_at_out": away_dec,
            "context": game.get("away_declare_context"),
            "seconds_used": game.get("away_seconds_used") or 0,
        }
    if home_dec is not None:
        out["home"] = {
            "declared_at_out": home_dec,
            "context": game.get("home_declare_context"),
            "seconds_used": game.get("home_seconds_used") or 0,
        }
    return out


def _game_context(game: dict, wp_table: dict) -> dict:
    away_abbrev = game["away_abbrev"]
    home_abbrev = game["home_abbrev"]
    home_score = game["home_score"] or 0
    away_score = game["away_score"] or 0
    winner_abbrev = home_abbrev if home_score > away_score else away_abbrev
    loser_abbrev = away_abbrev if winner_abbrev == home_abbrev else home_abbrev

    return {
        "game_id": game["id"],
        "matchup": {
            "away": {"name": game["away_name"], "abbrev": away_abbrev,
                     "city": game.get("away_city")},
            "home": {"name": game["home_name"], "abbrev": home_abbrev,
                     "city": game.get("home_city"),
                     "park": game.get("home_park_name") or None},
        },
        "final": {
            "away_score": away_score,
            "home_score": home_score,
            "winner": winner_abbrev,
            "loser": loser_abbrev,
            "margin": abs(home_score - away_score),
        },
        "went_to_extras": bool(game.get("super_inning")),
        "weather": {
            "temperature": game.get("temperature_tier"),
            "wind": game.get("wind_tier"),
            "precip": game.get("precip_tier"),
        },
        "inflection_points": _inflection_points(
            game["id"], game["home_team_id"], wp_table),
        "scoring": _scoring_summary(game["id"], away_abbrev, home_abbrev),
        "standouts": _standouts(
            game["id"], game["away_team_id"], game["home_team_id"]),
        "declared_seconds": _declared_seconds(game),
        "power_play": _power_play(game["id"]) or None,
    }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def latest_slate_date() -> str | None:
    """The most recent date with any played games — the natural default
    'edition' for the gazette (yesterday's finals)."""
    row = db.fetchone("SELECT MAX(game_date) AS d FROM games WHERE played = 1")
    return row["d"] if row and row["d"] else None


def adjacent_slate_dates(slate_date: str) -> tuple[str | None, str | None]:
    """(prev, next) played-slate dates for the edition navigator arrows."""
    prev = db.fetchone(
        "SELECT MAX(game_date) AS d FROM games "
        "WHERE played = 1 AND game_date < ?", (slate_date,))
    nxt = db.fetchone(
        "SELECT MIN(game_date) AS d FROM games "
        "WHERE played = 1 AND game_date > ?", (slate_date,))
    return (prev["d"] if prev else None, nxt["d"] if nxt else None)


def build_daily_payload(slate_date: str, *, wp_table: dict | None = None) -> dict:
    """The structured Game Context payload for every finished game on a date.

    Pass `wp_table` to reuse a prebuilt empirical table across calls; omit it
    and the table is built once from the whole league."""
    games = db.fetchall(
        """SELECT g.*,
                  ht.name AS home_name, ht.abbrev AS home_abbrev,
                  ht.city AS home_city, ht.park_name AS home_park_name,
                  at.name AS away_name, at.abbrev AS away_abbrev,
                  at.city AS away_city
           FROM games g
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE g.game_date = ? AND g.played = 1
           ORDER BY g.id""",
        (slate_date,),
    )
    if games and wp_table is None:
        wp_table = _wpa.build_wp_table()

    game_ctx = [_game_context(dict(g), wp_table or {}) for g in games]
    return {
        "publication": "The O27 Gazette",
        "sport": "O27",
        "edition_date": slate_date,
        "games_played": len(game_ctx),
        "games": game_ctx,
    }
