"""
Markdown text-export helpers for forum / GitHub / LLM paste.

Exports follow these principles:
- Pure markdown — renders correctly on GitHub, BBcode forums (most pass MD
  through), and Discord, and reads cleanly when pasted into an LLM prompt.
- One H1 per export so a paste lands as a self-contained snippet.
- Tables use pipe syntax with right-alignment for numerics.
- No HTML, no inline styles, no Jinja-isms.

Each public function takes already-prepared row data (the same shape the
HTML routes receive) and returns a string. Routes in `app.py` call these
and serve the result with `Content-Type: text/plain; charset=utf-8`.
"""
from __future__ import annotations
from typing import Iterable


# ---------------------------------------------------------------------------
# Low-level table builders
# ---------------------------------------------------------------------------

def _md_table(headers: list[str], rows: list[list[str]],
              align: list[str] | None = None) -> str:
    """Build a markdown pipe-table.

    align: list of 'l' / 'r' / 'c' per column. Defaults to left.
    """
    if not headers:
        return ""
    if align is None:
        align = ["l"] * len(headers)
    sep_map = {"l": ":---", "r": "---:", "c": ":---:"}
    sep = [sep_map.get(a, "---") for a in align]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(sep) + "|",
    ]
    for r in rows:
        # Pad short rows; truncate long rows defensively.
        cells = [str(c) if c is not None else "" for c in r[:len(headers)]]
        while len(cells) < len(headers):
            cells.append("")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _fmt_num(v, fmt: str = "%d", default: str = "—") -> str:
    if v is None:
        return default
    try:
        return fmt % v
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Game box score
# ---------------------------------------------------------------------------

def export_box_score(game: dict,
                     away_pitching: list[dict],
                     home_pitching: list[dict],
                     away_batting: list[dict],
                     home_batting: list[dict],
                     away_line: dict,
                     home_line: dict,
                     phases: list[int]) -> str:
    """Markdown box score for a played game.

    Inputs match what `game_detail()` already builds:
    - game: row from `games` joined with team names/abbrevs
    - away/home_pitching: consolidated per-pitcher rows (post _decorate_pitchers)
    - away/home_batting:  consolidated per-batter rows  (post _decorate_batters)
    - away/home_line:     line-score dict {runs, hits, errors, total_r/h/e}
    - phases:             [0, 1, 2, ...] (0 = regulation; super-innings = N>=1)
    """
    out: list[str] = []
    away_name = game.get("away_name") or game.get("away_abbrev") or "Away"
    home_name = game.get("home_name") or game.get("home_abbrev") or "Home"
    away_abv  = game.get("away_abbrev") or "AWY"
    home_abv  = game.get("home_abbrev") or "HOM"
    date      = game.get("game_date") or ""
    away_s    = game.get("away_score")
    home_s    = game.get("home_score")
    si        = game.get("super_inning") or 0

    winner_tag = ""
    if game.get("winner_id"):
        if game["winner_id"] == game.get("away_team_id"):
            winner_tag = f"  ·  **{away_abv} wins**"
        elif game["winner_id"] == game.get("home_team_id"):
            winner_tag = f"  ·  **{home_abv} wins**"

    out.append(f"# {away_name} {away_s} – {home_s} {home_name}")
    out.append("")
    out.append(f"_{date}_{winner_tag}{'  ·  Super-Innings' if si else ''}")
    out.append("")

    # ---- Line score ----
    phase_labels = []
    for p in phases:
        if p == 0:
            phase_labels.append("REG")
        else:
            phase_labels.append(f"SI{p}")
    line_headers = ["Team"] + phase_labels + ["R", "H", "E"]
    line_align   = ["l"] + ["r"] * (len(phase_labels) + 3)
    line_rows = []
    for team_label, line in [(away_name, away_line), (home_name, home_line)]:
        row = [team_label]
        for p in phases:
            row.append(str(line["runs"].get(p, 0)))
        row += [str(line["total_r"]), str(line["total_h"]), str(line["total_e"])]
        line_rows.append(row)
    out.append("## Line Score")
    out.append("")
    out.append(_md_table(line_headers, line_rows, line_align))
    out.append("")

    # ---- Pitching tables ----
    pitching_headers = ["Pitcher", "GSc", "OUT", "BF", "H", "R", "ER",
                        "BB", "K", "HR", "FO", "P"]
    pitching_align   = ["l"] + ["r"] * 11

    def _pitching_rows(rows: list[dict]) -> list[list[str]]:
        out_rows = []
        for r in rows:
            out_rows.append([
                r.get("player_name") or "",
                _fmt_num(r.get("gsc_avg"), "%.0f"),
                _fmt_num(r.get("outs_recorded"), "%d", "0"),
                _fmt_num(r.get("batters_faced"), "%d", "0"),
                _fmt_num(r.get("hits_allowed"), "%d", "0"),
                _fmt_num(r.get("runs_allowed"), "%d", "0"),
                _fmt_num(r.get("er"), "%d", "0"),
                _fmt_num(r.get("bb"), "%d", "0"),
                _fmt_num(r.get("k"), "%d", "0"),
                _fmt_num(r.get("hr_allowed"), "%d", "0"),
                _fmt_num(r.get("fo_induced"), "%d", "0"),
                _fmt_num(r.get("pitches"), "%d", "0"),
            ])
        return out_rows

    out.append(f"## Pitching — {away_name}")
    out.append("")
    out.append(_md_table(pitching_headers, _pitching_rows(away_pitching), pitching_align))
    out.append("")
    out.append(f"## Pitching — {home_name}")
    out.append("")
    out.append(_md_table(pitching_headers, _pitching_rows(home_pitching), pitching_align))
    out.append("")

    # ---- Batting tables ----
    batting_headers = ["Batter", "Pos", "PA", "AB", "R", "H", "2B", "3B",
                       "HR", "RBI", "BB", "SO", "SB", "Stays"]
    batting_align   = ["l", "l"] + ["r"] * 12

    def _batting_rows(rows: list[dict]) -> list[list[str]]:
        out_rows = []
        for r in rows:
            out_rows.append([
                r.get("player_name") or "",
                r.get("position") or "",
                _fmt_num(r.get("pa"), "%d", "0"),
                _fmt_num(r.get("ab"), "%d", "0"),
                _fmt_num(r.get("runs"), "%d", "0"),
                _fmt_num(r.get("hits"), "%d", "0"),
                _fmt_num(r.get("doubles"), "%d", "0"),
                _fmt_num(r.get("triples"), "%d", "0"),
                _fmt_num(r.get("hr"), "%d", "0"),
                _fmt_num(r.get("rbi"), "%d", "0"),
                _fmt_num(r.get("bb"), "%d", "0"),
                _fmt_num(r.get("k"), "%d", "0"),
                _fmt_num(r.get("sb"), "%d", "0"),
                _fmt_num(r.get("stays"), "%d", "0"),
            ])
        return out_rows

    out.append(f"## Batting — {away_name}")
    out.append("")
    out.append(_md_table(batting_headers, _batting_rows(away_batting), batting_align))
    out.append("")
    out.append(f"## Batting — {home_name}")
    out.append("")
    out.append(_md_table(batting_headers, _batting_rows(home_batting), batting_align))
    out.append("")

    out.append(f"_O27 League · seed {game.get('seed', '—')} · "
               f"game id {game.get('id', '—')}_")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Player season card
# ---------------------------------------------------------------------------

def export_player_card(player: dict,
                       bt_totals: dict | None,
                       pt_totals: dict | None,
                       fld_totals: dict | None,
                       batting_log: list[dict] | None = None,
                       pitching_log: list[dict] | None = None) -> str:
    """Markdown season card for a single player.

    Includes header, batting/pitching season totals (whichever exist),
    fielding line, and the most-recent game log (truncated)."""
    out: list[str] = []

    name = player.get("name", "Player")
    pos  = player.get("position", "")
    team = player.get("team_name") or player.get("team_abbrev") or ""
    age  = player.get("age", "")
    bats = player.get("bats") or "—"
    throws = player.get("throws") or "—"
    is_p = player.get("is_pitcher")

    out.append(f"# {name}")
    out.append("")
    out.append(f"_{pos} · {team} · Age {age} · Bats {bats} · Throws {throws}_"
               f"{'  ·  Pitcher' if is_p else ''}")
    out.append("")

    # ---- Batting ----
    if bt_totals:
        out.append("## Batting — season totals")
        out.append("")
        out.append(_md_table(
            ["G", "PA", "AB", "R", "H", "2B", "3B", "HR", "RBI", "BB", "SO",
             "SB", "PAVG", "OBP", "SLG", "OPS", "OPS+", "wOBA", "Stay%", "WAR"],
            [[
                _fmt_num(bt_totals.get("g")),
                _fmt_num(bt_totals.get("pa")),
                _fmt_num(bt_totals.get("ab")),
                _fmt_num(bt_totals.get("r")),
                _fmt_num(bt_totals.get("h")),
                _fmt_num(bt_totals.get("d2")),
                _fmt_num(bt_totals.get("d3")),
                _fmt_num(bt_totals.get("hr")),
                _fmt_num(bt_totals.get("rbi")),
                _fmt_num(bt_totals.get("bb")),
                _fmt_num(bt_totals.get("k")),
                _fmt_num(bt_totals.get("sb")),
                _fmt_num(bt_totals.get("pavg"), "%.3f"),
                _fmt_num(bt_totals.get("obp"), "%.3f"),
                _fmt_num(bt_totals.get("slg"), "%.3f"),
                _fmt_num(bt_totals.get("ops"), "%.3f"),
                _fmt_num(bt_totals.get("ops_plus"), "%d"),
                _fmt_num(bt_totals.get("woba"), "%.3f"),
                _fmt_num((bt_totals.get("stay_pct") or 0) * 100, "%.1f%%"),
                _fmt_num(bt_totals.get("war"), "%.2f"),
            ]],
            ["r"] * 20,
        ))
        out.append("")

    # ---- Pitching ----
    if pt_totals:
        out.append("## Pitching — season totals")
        out.append("")
        out.append(_md_table(
            ["G", "GS", "W", "L", "BF", "Outs", "H", "R", "ER", "BB", "SO",
             "HR", "FO", "P", "wERA", "xFIP", "Decay", "GSc", "GSc+",
             "OS+", "K%", "BB%", "WAR"],
            [[
                _fmt_num(pt_totals.get("g")),
                _fmt_num(pt_totals.get("gs")),
                _fmt_num(pt_totals.get("w")),
                _fmt_num(pt_totals.get("l")),
                _fmt_num(pt_totals.get("bf")),
                _fmt_num(pt_totals.get("outs")),
                _fmt_num(pt_totals.get("h")),
                _fmt_num(pt_totals.get("r")),
                _fmt_num(pt_totals.get("er")),
                _fmt_num(pt_totals.get("bb")),
                _fmt_num(pt_totals.get("k")),
                _fmt_num(pt_totals.get("hr_allowed")),
                _fmt_num(pt_totals.get("fo_induced")),
                _fmt_num(pt_totals.get("pitches")),
                _fmt_num(pt_totals.get("werra"), "%.2f"),
                _fmt_num(pt_totals.get("xfip"), "%.2f"),
                ("%+.1f" % pt_totals["decay"]) if pt_totals.get("decay_known") else "—",
                _fmt_num(pt_totals.get("gsc_avg"), "%.1f"),
                _fmt_num(pt_totals.get("gsc_plus"), "%d"),
                _fmt_num(pt_totals.get("os_plus"), "%d"),
                _fmt_num((pt_totals.get("k_pct") or 0) * 100, "%.1f%%"),
                _fmt_num((pt_totals.get("bb_pct") or 0) * 100, "%.1f%%"),
                _fmt_num(pt_totals.get("war"), "%.2f"),
            ]],
            ["r"] * 23,
        ))
        out.append("")

    # ---- Fielding ----
    if fld_totals and (fld_totals.get("po") or fld_totals.get("e")):
        out.append("## Fielding")
        out.append("")
        out.append(_md_table(
            ["Pos", "PO", "E", "TC", "FldPct"],
            [[
                pos,
                _fmt_num(fld_totals.get("po")),
                _fmt_num(fld_totals.get("e")),
                _fmt_num(fld_totals.get("chances")),
                _fmt_num(fld_totals.get("fld_pct"), "%.3f"),
            ]],
            ["l", "r", "r", "r", "r"],
        ))
        out.append("")

    # ---- Game log (last 10) ----
    if batting_log:
        out.append("## Recent Batting Log")
        out.append("")
        log_rows = []
        for g in batting_log[:10]:
            is_home = (g.get("home_team_id") == player.get("team_id"))
            opp = g.get("away_abbrev") if is_home else g.get("home_abbrev")
            vs  = "vs" if is_home else "@"
            log_rows.append([
                g.get("game_date", ""),
                f"{vs} {opp}",
                _fmt_num(g.get("pa"), "%d", "0"),
                _fmt_num(g.get("ab"), "%d", "0"),
                _fmt_num(g.get("hits"), "%d", "0"),
                _fmt_num(g.get("hr"), "%d", "0"),
                _fmt_num(g.get("rbi"), "%d", "0"),
                _fmt_num(g.get("bb"), "%d", "0"),
                _fmt_num(g.get("k"), "%d", "0"),
                _fmt_num(g.get("stays"), "%d", "0"),
            ])
        out.append(_md_table(
            ["Date", "Opp", "PA", "AB", "H", "HR", "RBI", "BB", "SO", "Stays"],
            log_rows,
            ["l", "l"] + ["r"] * 8,
        ))
        out.append("")

    if pitching_log:
        out.append("## Recent Pitching Log")
        out.append("")
        log_rows = []
        for g in pitching_log[:10]:
            is_home = (g.get("home_team_id") == player.get("team_id"))
            opp = g.get("away_abbrev") if is_home else g.get("home_abbrev")
            vs  = "vs" if is_home else "@"
            # Inline GSc per row.
            outs = g.get("outs_recorded") or 0
            k    = g.get("k") or 0
            h    = g.get("hits_allowed") or 0
            er   = g.get("er") or 0
            uer  = g.get("unearned_runs") or 0
            bb   = g.get("bb") or 0
            hr   = g.get("hr_allowed") or 0
            fo   = g.get("fo_induced") or 0
            gsc  = max(0, min(100,
                50 + outs + 2 * max(0, k - 3)
                - 2 * h - 4 * er - 2 * uer - bb - 4 * hr + fo))
            log_rows.append([
                g.get("game_date", ""),
                f"{vs} {opp}",
                str(int(gsc)),
                str(outs),
                str(g.get("batters_faced") or 0),
                str(h), str(g.get("runs_allowed") or 0), str(er),
                str(bb), str(k), str(hr), str(fo),
                str(g.get("pitches") or 0),
            ])
        out.append(_md_table(
            ["Date", "Opp", "GSc", "Outs", "BF", "H", "R", "ER",
             "BB", "K", "HR", "FO", "P"],
            log_rows,
            ["l", "l"] + ["r"] * 11,
        ))
        out.append("")

    out.append("_O27 League · player season card_")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

def export_standings(leagues_with_divisions: dict, win_pct,
                     gb_calc=None) -> str:
    """Markdown standings, grouped by league/division."""
    out: list[str] = ["# Standings", ""]
    for league_name, divs in leagues_with_divisions.items():
        out.append(f"## {league_name}")
        out.append("")
        for div_name, teams in divs.items():
            out.append(f"### {div_name}")
            out.append("")
            rows = []
            leader = teams[0] if teams else None
            for t in teams:
                gb = "—"
                if leader and t["id"] != leader["id"] and gb_calc:
                    gb = gb_calc(leader, t)
                rows.append([
                    t.get("abbrev") or "",
                    t.get("name") or "",
                    str(t.get("wins") or 0),
                    str(t.get("losses") or 0),
                    win_pct(t),
                    gb,
                ])
            out.append(_md_table(
                ["Tm", "Team", "W", "L", "Pct", "GB"],
                rows,
                ["l", "l", "r", "r", "r", "r"],
            ))
            out.append("")
    out.append("_O27 League · standings export_")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Leaders
# ---------------------------------------------------------------------------

def export_leaders(batting: list[dict], pitching: list[dict]) -> str:
    """Markdown leader tables — top 10 across the curated stat list."""
    out: list[str] = ["# Leaders", ""]

    def _top(rows: list[dict], key: str, reverse: bool = True, n: int = 10) -> list[dict]:
        good = [r for r in rows if r.get(key) is not None]
        return sorted(good, key=lambda r: r.get(key, 0), reverse=reverse)[:n]

    def _table(label: str, rows: list[dict], key: str, fmt: str,
               reverse: bool = True) -> None:
        if not rows:
            return
        ranked = _top(rows, key, reverse=reverse, n=10)
        if not ranked:
            return
        out.append(f"### {label}")
        out.append("")
        body = []
        for i, r in enumerate(ranked, start=1):
            body.append([
                str(i),
                r.get("player_name") or "",
                r.get("team_abbrev") or "",
                _fmt_num(r.get(key), fmt),
            ])
        out.append(_md_table(
            ["#", "Player", "Tm", label.split(" ")[0]],
            body,
            ["r", "l", "l", "r"],
        ))
        out.append("")

    out.append("## Batting")
    out.append("")
    _table("PAVG",  batting, "pavg",     "%.3f")
    _table("OPS",   batting, "ops",      "%.3f")
    _table("OPS+",  batting, "ops_plus", "%d")
    _table("HR",    batting, "hr",       "%d")
    _table("RBI",   batting, "rbi",      "%d")
    _table("WAR",   batting, "war",      "%.2f")

    out.append("## Pitching")
    out.append("")
    _table("wERA (low)",      pitching, "werra",    "%.2f", reverse=False)
    _table("xFIP (low)",      pitching, "xfip",     "%.2f", reverse=False)
    _table("Decay (low)",     pitching, "decay",    "%+.1f", reverse=False)
    _table("GSc avg",         pitching, "gsc_avg",  "%.1f")
    _table("GSc+",            pitching, "gsc_plus", "%d")
    _table("OS+",             pitching, "os_plus",  "%d")
    _table("K",               pitching, "k",        "%d")
    _table("WAR",             pitching, "war",      "%.2f")

    out.append("_O27 League · leaders export · pitcher metrics: wERA / xFIP / Decay are O27-native (arc-weighted ERA, defense-independent O27 FIP, late-arc K-rate fade)_")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Team page
# ---------------------------------------------------------------------------

def export_team(team: dict, batters: list[dict], pitchers: list[dict],
                wins: int, losses: int) -> str:
    """Markdown team summary — record, hitter table, pitcher table."""
    out: list[str] = []
    name   = team.get("name", "Team")
    abbrev = team.get("abbrev", "")
    out.append(f"# {name} ({abbrev})")
    out.append("")
    out.append(f"_Record: **{wins}-{losses}**_")
    out.append("")

    if batters:
        out.append("## Hitters")
        out.append("")
        rows = []
        for p in batters:
            rows.append([
                p.get("name") or "",
                p.get("position") or "",
                _fmt_num(p.get("age")),
                _fmt_num(p.get("g") or p.get("gp"), "%d", "0"),
                _fmt_num(p.get("pa"), "%d", "0"),
                _fmt_num(p.get("h"), "%d", "0"),
                _fmt_num(p.get("hr"), "%d", "0"),
                _fmt_num(p.get("rbi"), "%d", "0"),
                _fmt_num(p.get("pavg"), "%.3f"),
                _fmt_num(p.get("ops"), "%.3f"),
                _fmt_num(p.get("war"), "%.2f"),
            ])
        out.append(_md_table(
            ["Name", "Pos", "Age", "G", "PA", "H", "HR", "RBI", "PAVG", "OPS", "WAR"],
            rows,
            ["l", "l", "r", "r", "r", "r", "r", "r", "r", "r", "r"],
        ))
        out.append("")

    if pitchers:
        out.append("## Pitchers")
        out.append("")
        rows = []
        for p in pitchers:
            rows.append([
                p.get("name") or "",
                _fmt_num(p.get("age")),
                _fmt_num(p.get("g") or p.get("gp"), "%d", "0"),
                _fmt_num(p.get("gs"), "%d", "0"),
                _fmt_num(p.get("w"), "%d", "0"),
                _fmt_num(p.get("l"), "%d", "0"),
                _fmt_num(p.get("outs"), "%d", "0"),
                _fmt_num(p.get("k"), "%d", "0"),
                _fmt_num(p.get("werra"), "%.2f"),
                _fmt_num(p.get("xfip"), "%.2f"),
                _fmt_num((p.get("k_pct") or 0) * 100, "%.1f%%"),
                _fmt_num(p.get("war"), "%.2f"),
            ])
        out.append(_md_table(
            ["Name", "Age", "G", "GS", "W", "L", "Outs", "K", "wERA",
             "xFIP", "K%", "WAR"],
            rows,
            ["l", "r", "r", "r", "r", "r", "r", "r", "r", "r", "r", "r"],
        ))
        out.append("")

    out.append("_O27 League · team export_")
    out.append("")
    return "\n".join(out)
