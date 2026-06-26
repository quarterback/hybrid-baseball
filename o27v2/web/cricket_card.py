"""Render an O27 game as a cricket-style scorecard.

An exercise in idiom translation: can a baseball box score read like the BBC's
Test-match card — a compact "top order + attack + match state" panel per side?
It mostly works because O27 already shares cricket's spine: there are no innings,
each side bats one continuous 27-out half. The top bats first, the bottom chases
a target — two halves of ONE regulation innings, not cricket's two separate
innings. A side only ever bats a *second* time in a super inning (the tiebreak),
so super innings are the true "2nd innings, and so forth."

Baseball → cricket mapping
--------------------------
* **Team total `R/W`** — runs for outs. An out is a wicket; an innings closes at
  27, so a completed side reads `7/27` (cf. cricket's "all out" at 10). The PA
  count rides alongside as the "balls faced" depth.
* **A batter's innings = TRI** (Total Runner Influence = own total bases + RAD,
  the graded bases of runner movement he caused). Plain total bases misses the
  point of O27 — the signature bat skill is *moving runners* with the stay — so
  TRI is the runner-moving cousin of total bases, with RBI as its scoring tail.
  Balls faced = plate appearances; a batter who never made an out (outs == 0,
  ≥1 PA) is **not out**, marked `*` like a cricket `74*`. The card lists the top
  order by TRI, as cricket lists the highest scorers.
* **A pitcher's figures `W–R`** — outs recorded for runs allowed (e.g. `9–3`),
  read aloud "nine for three". Outs are wickets, so the attack's wickets sum to
  27, mirroring a bowling card summing to 10.
* **Match state** — the bottom side chases the top's total. O27 plays both
  27-out halves out (no walk-off), so a decided game is always "won by N runs".
  A level score after regulation goes to **super innings** (super inning 1, 2,
  …), the only place a side bats a second time — rendered as their own block.

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


def _runners_advanced(b: dict) -> int:
    """RAD — graded bases each runner gained off this PA (the runner-movement
    analog of total bases)."""
    return (b.get("rad_1b") or 0) + (b.get("rad_2b") or 0) + (b.get("rad_3b") or 0)


def _tri(b: dict) -> int:
    """TRI — a batter's own total bases PLUS RAD (bases of runner movement he
    caused). O27's signature bat skill is moving runners with the stay, so TRI
    is the headline "innings" number here: the runner-moving cousin of total
    bases, with RBI as its scoring tail."""
    return _total_bases(b) + _runners_advanced(b)


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
            "b.runs, b.rbi, b.rad_1b, b.rad_2b, b.rad_3b, "
            "b.outs_recorded AS outs FROM game_batter_stats b "
            "JOIN players p ON p.id=b.player_id "
            "WHERE b.game_id=? AND b.team_id=? AND b.pa>0",
            (game_id, team_id))
        batters = [{
            "name": b["name"], "score": _tri(b), "balls": b["pa"],
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

        runs = sum(b["runs"] for b in bats)
        # A completed regulation half always bats until 27 outs ("all out") —
        # the defining O27 rule — so the wicket count is 27 by definition. (The
        # per-batter outs_recorded ledger undercounts: baserunning outs aren't
        # charged to a batter.) Super innings carry their own shorter out count.
        return {"team": team_name, "runs": runs, "wickets": 27,
                "pa": sum(b["pa"] for b in bats),
                "batting_first": batting_first,
                "batters": batters, "bowlers": bowlers}

    away = innings(g["away_team_id"], g["away_name"], batting_first=True)
    home = innings(g["home_team_id"], g["home_name"], batting_first=False)

    # Super innings: O27 has no second regulation innings — a side only bats
    # again to break a tie. Each extra round is its own "innings" (super inning
    # 1, 2, …), drawn from the play-by-play super_* halves when present.
    supers = db.fetchall(
        "SELECT half, MAX(visitors_score) v, MAX(home_score) h "
        "FROM game_scoring_events WHERE game_id=? AND half LIKE 'super%' "
        "GROUP BY half", (game_id,))
    super_innings = [{"half": s["half"], "v": s["v"], "h": s["h"]} for s in supers]

    return {"date": g["game_date"], "id": game_id,
            "innings": [away, home], "super_innings": super_innings,
            "away_score": g["away_score"], "home_score": g["home_score"],
            "winner_id": g["winner_id"],
            "away_id": g["away_team_id"], "home_id": g["home_team_id"]}


def _opponent(g: dict, team_id: int) -> int:
    return g["home_team_id"] if team_id == g["away_team_id"] else g["away_team_id"]


def _result_line(card: dict) -> str:
    away, home = card["innings"][0], card["innings"][1]
    # O27 plays both 27-out halves out (no walk-off), so regulation is decided
    # on runs. A level score after regulation goes to super innings.
    if away["runs"] != home["runs"]:
        winner, loser = ((home, away) if home["runs"] > away["runs"]
                         else (away, home))
        margin = winner["runs"] - loser["runs"]
        return (f"  {winner['team']} beat {loser['team']} "
                f"by {margin} run{'s' if margin != 1 else ''}")
    # Tied after regulation → super innings settled it (use the result of record).
    if card["super_innings"]:
        n = len(card["super_innings"])
        win = away["team"] if card["winner_id"] == card["away_id"] else home["team"]
        return (f"  Level at {away['runs']} after regulation — "
                f"{win} win in super inning{'s' if n != 1 else ''} ({n})")
    return f"  Match level — {away['runs']} apiece"


def _render_innings(inn: dict, target: Optional[int]) -> list[str]:
    # O27 is one 27-out half per side: the top bats first, the bottom chases.
    # These are halves of the single regulation innings, NOT cricket's two
    # separate innings — a side only bats again in a super inning.
    if inn["batting_first"]:
        label = "top · batting first"
    else:
        label = f"bottom · chasing {target}" if target is not None else "bottom"
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


def _render_super_innings(card: dict) -> list[str]:
    if not card["super_innings"]:
        return []
    a, h = card["innings"]
    lines = ["", "  Super innings"]
    for i, s in enumerate(card["super_innings"], 1):
        bats_away = "top" in s["half"]
        team = a["team"] if bats_away else h["team"]
        runs = s["v"] if bats_away else s["h"]
        lines.append(f"    {i}.  {team:<22} {runs if runs is not None else '—'}")
    return lines


def render_cricket_card(game_id: int, db) -> str:
    """The full BBC-style card as one string for a <pre> block."""
    card = build_cricket_card(game_id, db)
    if card is None:
        return f"(no game #{game_id})"
    a, h = card["innings"]
    target = a["runs"] + 1   # the chasing (bottom) side needs to pass this
    title = f"{a['team']} v {h['team']} — O27, {card['date']}  · #{card['id']}"
    out = [_RULE, f"  {title}", "  Regulation — 27 outs a side", _THIN]
    out += _render_innings(a, target)
    out.append("")
    out += _render_innings(h, target)
    out += _render_super_innings(card)
    out += [_THIN, _result_line(card),
            "  batters: TRI (total bases + runners advanced) · balls · * not out",
            _RULE]
    return "\n".join(out)
