"""Render an O27 game as a cricket-style scorecard.

An exercise in idiom translation: can a baseball box score read like the BBC's
Test-match card — two innings, each a compact "top order + attack + match state"
panel? The mapping below is deliberate, and it mostly works because O27 already
shares cricket's spine (one continuous 27-out half per side = an innings; the
side batting second is chasing a target).

Baseball → cricket mapping
--------------------------
* **Team total `R/W`** — runs for outs. An out is a wicket; an innings closes at
  27, so a completed side reads `7/27` (cf. cricket's "all out" at 10). The PA
  count rides alongside as the "balls faced" depth.
* **A batter's innings = total bases** (1B=1, 2B=2, 3B=3, HR=4) — the cleanest
  analog to a batsman accumulating runs off the bat. Balls faced = plate
  appearances. A batter who never made an out (outs == 0, ≥1 PA) is **not out**,
  marked `*`, exactly like a cricket `74*`. The card lists the top order by
  innings score, just as cricket lists the highest scorers.
* **A pitcher's figures `W–R`** — outs recorded for runs allowed (e.g. `9–3`),
  read aloud "nine for three". Outs are wickets, so the attack's wickets sum to
  27, mirroring a bowling card summing to 10.
* **Match state** — the side batting second chases. If it gets there with outs
  in hand it wins "by N wickets" (outs remaining); otherwise the side that
  batted first defended its total and wins "by N runs". The losing chase trails
  "by N runs".

Pure rendering: it reads the persisted per-game tables (game_batter_stats,
game_pitcher_stats, games, players) and returns a string for a <pre> block.
"""
from __future__ import annotations

from typing import Optional

_RULE = "═" * 60
_THIN = "─" * 60
_TOP_BATS = 5      # top-order batters shown per innings
_TOP_BOWL = 4      # bowlers shown in the attack line


def _short(name: str) -> str:
    """'Devon Conway' → 'D. Conway'; single tokens pass through."""
    parts = (name or "").strip().split()
    if len(parts) < 2:
        return name or "?"
    return f"{parts[0][0]}. {parts[-1]}"


def _total_bases(b: dict) -> int:
    singles = b["hits"] - b["doubles"] - b["triples"] - b["hr"]
    return singles + 2 * b["doubles"] + 3 * b["triples"] + 4 * b["hr"]


def build_cricket_card(game_id: int, db) -> Optional[dict]:
    """Assemble the structured card for a finished game, or None if unknown.

    Innings are ordered first-to-bat first (away, then the chasing home side)."""
    g = db.fetchone(
        "SELECT g.*, a.name AS away_name, h.name AS home_name "
        "FROM games g JOIN teams a ON a.id=g.away_team_id "
        "JOIN teams h ON h.id=g.home_team_id WHERE g.id=?", (game_id,))
    if not g:
        return None

    def innings(team_id: int, team_name: str, batting_first: bool) -> dict:
        bats = db.fetchall(
            "SELECT p.name AS name, b.pa, b.hits, b.doubles, b.triples, b.hr, "
            "b.runs, b.rbi, b.outs_recorded AS outs FROM game_batter_stats b "
            "JOIN players p ON p.id=b.player_id "
            "WHERE b.game_id=? AND b.team_id=? AND b.pa>0",
            (game_id, team_id))
        batters = [{
            "name": b["name"], "score": _total_bases(b), "balls": b["pa"],
            "runs": b["runs"], "rbi": b["rbi"],
            "not_out": (b["outs"] or 0) == 0,
        } for b in bats]
        batters.sort(key=lambda x: (-x["score"], -x["rbi"], -x["balls"]))

        bowls = db.fetchall(
            "SELECT p.name AS name, w.outs_recorded AS wkts, w.runs_allowed AS runs, "
            "w.k FROM game_pitcher_stats w JOIN players p ON p.id=w.player_id "
            "WHERE w.game_id=? AND w.team_id=?",
            (game_id, _opponent(g, team_id)))
        bowlers = [{"name": b["name"], "wkts": b["wkts"] or 0,
                    "runs": b["runs"] or 0, "k": b["k"] or 0} for b in bowls]
        bowlers.sort(key=lambda x: (-x["wkts"], x["runs"]))

        outs = sum(b["outs"] or 0 for b in bats)
        runs = sum(b["runs"] for b in bats)
        return {"team": team_name, "runs": runs, "wickets": outs,
                "pa": sum(b["pa"] for b in bats),
                "batting_first": batting_first,
                "batters": batters, "bowlers": bowlers}

    away = innings(g["away_team_id"], g["away_name"], batting_first=True)
    home = innings(g["home_team_id"], g["home_name"], batting_first=False)
    return {"date": g["game_date"], "id": game_id,
            "innings": [away, home],
            "away_score": g["away_score"], "home_score": g["home_score"],
            "winner_id": g["winner_id"],
            "away_id": g["away_team_id"], "home_id": g["home_team_id"]}


def _opponent(g: dict, team_id: int) -> int:
    return g["home_team_id"] if team_id == g["away_team_id"] else g["away_team_id"]


def _result_line(card: dict) -> str:
    first, second = card["innings"][0], card["innings"][1]
    if first["runs"] == second["runs"]:
        return f"  Match tied — {first['runs']} apiece"
    winner, loser = ((second, first) if second["runs"] > first["runs"]
                     else (first, second))
    margin = winner["runs"] - loser["runs"]
    if winner is second and second["wickets"] < 27:
        # Chased the target down with outs in hand → "by N wickets".
        return (f"  {winner['team']} beat {loser['team']} "
                f"by {27 - second['wickets']} wicket"
                f"{'s' if 27 - second['wickets'] != 1 else ''}")
    return (f"  {winner['team']} beat {loser['team']} "
            f"by {margin} run{'s' if margin != 1 else ''}")


def _render_innings(inn: dict) -> list[str]:
    label = "1st innings" if inn["batting_first"] else "2nd innings (chasing)"
    head = (f"  {inn['team']:<22} {inn['runs']:>3}/{inn['wickets']:<2}"
            f"   {inn['pa']:>3} PA      {label}")
    lines = [head]
    for b in inn["batters"][:_TOP_BATS]:
        star = "*" if b["not_out"] else " "
        lines.append(f"      {_short(b['name']):<16} {b['score']:>3}{star} "
                     f"({b['balls']})")
    attack = ",  ".join(f"{_short(b['name'])} {b['wkts']}–{b['runs']}"
                        for b in inn["bowlers"][:_TOP_BOWL] if b["wkts"] or b["runs"])
    if attack:
        lines.append(f"      Attack:  {attack}")
    return lines


def render_cricket_card(game_id: int, db) -> str:
    """The full BBC-style card as one string for a <pre> block."""
    card = build_cricket_card(game_id, db)
    if card is None:
        return f"(no game #{game_id})"
    a, h = card["innings"]
    title = f"{a['team']} v {h['team']} — O27, {card['date']}  · #{card['id']}"
    out = [_RULE, f"  {title}", _THIN]
    out += _render_innings(a)
    out.append("")
    out += _render_innings(h)
    out += [_THIN, _result_line(card), _RULE]
    return "\n".join(out)
