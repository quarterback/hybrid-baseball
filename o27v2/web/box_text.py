"""
Newspaper-style monospace plaintext box score rendering.

Pure rendering helper — consumes the same per-game data the existing
`game_detail` route already builds (consolidated batter / pitcher rows,
line-score dict, weather, etc.) and emits a single string ready to drop
inside a <pre> block.

No engine, aggregation, or data-model changes. Output is one long string
with embedded newlines; widths are fixed for monospace alignment.

Layout (top to bottom):
  ===== rule =====
  Title line (TEAMS SCORE  date · #N)
  ===== rule =====
  Line score
  blank
  Visiting batting + annotations
  blank
  Home batting + annotations
  blank
  Visiting pitching
  blank
  Home pitching + pitching annotations
  blank
  Footer (weather, seed)
  ===== rule =====

W/L heuristic (data-model-clean — no new column):
  W = winning team's last pitcher (final pitcher of record proxy).
  L = losing team's pitcher with the most runs allowed. Ties broken by
      appearance order.
"""
from __future__ import annotations
import datetime


# Fixed column widths. Width changes ripple through every header / row,
# so any tweak should be checked against a real game.
_RULE_WIDTH    = 68
_NAME_FIELD    = 13   # left-padded player name (truncated to FIELD-1 so a
                      # space always separates name from position).
                      # 13 fits 'F. Lastname' with up to a 9-char surname.
_POS_FIELD     = 2    # 2-char lowercase position
_DOTS_FIELD    = 13   # " ........... " (1 + 11 dots + 1)
_PRE_NUM       = _NAME_FIELD + _POS_FIELD + _DOTS_FIELD  # 27 cols


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _short_pos(pos: str) -> str:
    """Lowercase 2-char position. 'P' -> 'p ', 'SS' -> 'ss', 'DH' -> 'dh'."""
    if not pos:
        return "  "
    return pos.lower()[:2].ljust(2)


def _short_name(name: str) -> str:
    """Newspaper-style abbreviated name: 'F. Lastname'.

    Real-world convention from AP / NYT / USA Today wire box scores:
    first initial, period, space, last word of the surname. Single-token
    names pass through unchanged. Truncates only if the result still
    overflows _NAME_FIELD - 1 characters (rare).
    """
    if not name:
        return ""
    parts = name.strip().split()
    if len(parts) <= 1:
        short = parts[0] if parts else ""
    else:
        short = f"{parts[0][0]}. {parts[-1]}"
    return short[:_NAME_FIELD - 1]


def _name_pos(name: str, pos: str) -> str:
    """First 27 cols: 'K. Aviles   3b ........... '."""
    nm = _short_name(name).ljust(_NAME_FIELD)
    p  = _short_pos(pos)
    dots = "." * (_DOTS_FIELD - 2)
    return f"{nm}{p} {dots} "


def _name_no_pos(label: str) -> str:
    """For TOTALS / pitcher rows. Pitchers come through `_short_name`
    upstream so they look like 'K. Aviles' too; non-name labels (TOTALS)
    pass through verbatim."""
    return (label or "")[:_PRE_NUM].ljust(_PRE_NUM)


def _avg(num: int, den: int) -> str:
    """Three-decimal H/AB string like '.400' or '1.000'. 0/0 -> '.000'."""
    if not den:
        return ".000"
    val = num / den
    if val >= 1.0:
        return f"{val:.3f}"  # e.g. 1.000
    return f"{val:.3f}".lstrip("0")  # .400


def _pct(num: int, den: int) -> str:
    if not den:
        return "0%"
    return f"{int(round(100 * num / den))}%"


def _format_date(iso: str) -> str:
    try:
        d = datetime.date.fromisoformat(iso)
        return d.strftime("%b %-d, %Y")
    except (ValueError, TypeError):
        return iso or ""


def _join_names(items: list[tuple[str, int]]) -> str:
    """Render annotation items: 'Lefebvre, Nishida 2, Fraser.'"""
    parts = []
    for nm, count in items:
        if count > 1:
            parts.append(f"{nm} {count}")
        else:
            parts.append(nm)
    return ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _title_line(game: dict) -> str:
    away = (game.get("away_name") or "").upper()
    home = (game.get("home_name") or "").upper()
    away_s = game.get("away_score") or 0
    home_s = game.get("home_score") or 0
    left  = f"{away} {away_s}, {home} {home_s}"
    right = f"{_format_date(game.get('game_date'))} · #{game.get('id')}"
    pad = _RULE_WIDTH - len(left) - len(right)
    if pad < 1:
        pad = 1
    return left + (" " * pad) + right


def _line_score(game: dict, phases: list[int],
                away_line: dict, home_line: dict) -> list[str]:
    """Single REG column (regulation only) plus optional SI roundsl
    plus R/H/E. Phase labels: REG, SI1, SI2, ..."""
    ph_labels = ["REG" if p == 0 else f"SI{p}" for p in phases]
    # Header: 18-char team col, then 5-char phase cols, then R/H/E
    header_left = "".ljust(18)
    header_phases = "".join(f"{lbl:>5}" for lbl in ph_labels)
    header_rhe = f"{'R':>5}{'H':>5}{'E':>5}"
    out = [header_left + header_phases + header_rhe]

    for nm, line in (
        (game.get("away_name") or "", away_line),
        (game.get("home_name") or "", home_line),
    ):
        row = nm[:18].ljust(18)
        for p in phases:
            row += f"{(line['runs'].get(p, 0)):>5}"
        row += f"{line['total_r']:>5}{line['total_h']:>5}{line['total_e']:>5}"
        out.append(row)
    return out


# Batting header / row layout
#
# Cols 1-27   name + pos + dots
# 28-30  AB    %3d
# 31-33  R     %3d
# 34-36  H     %3d
# 37-40  HR    %4d
# 41-44  RBI   %4d
# 45-47  BB    %3d
# 48-50  K     %3d
# 51-56  H/AB  %6s

_BAT_HEADER = (
    "AB".rjust(3) + "R".rjust(3) + "H".rjust(3)
    + "HR".rjust(4) + "RBI".rjust(4) + "BB".rjust(3) + "K".rjust(3)
    + "H/AB".rjust(6)
)


def _batting_block(team_name: str, rows: list[dict]) -> list[str]:
    head = (team_name or "").upper()[:_PRE_NUM].ljust(_PRE_NUM) + _BAT_HEADER
    out = [head]

    tot = {f: 0 for f in ("ab", "r", "h", "hr", "rbi", "bb", "k")}

    for r in rows:
        ab  = r.get("ab", 0) or 0
        ru  = r.get("runs", 0) or 0
        h   = r.get("hits", 0) or 0
        hr  = r.get("hr", 0) or 0
        rbi = r.get("rbi", 0) or 0
        bb  = r.get("bb", 0) or 0
        k   = r.get("k", 0) or 0
        line = (
            _name_pos(r.get("player_name", ""), r.get("position", ""))
            + f"{ab:3d}{ru:3d}{h:3d}{hr:4d}{rbi:4d}{bb:3d}{k:3d}"
            + _avg(h, ab).rjust(6)
        )
        out.append(line)
        tot["ab"]  += ab
        tot["r"]   += ru
        tot["h"]   += h
        tot["hr"]  += hr
        tot["rbi"] += rbi
        tot["bb"]  += bb
        tot["k"]   += k

    totals = (
        _name_no_pos("TOTALS")
        + f"{tot['ab']:3d}{tot['r']:3d}{tot['h']:3d}"
        + f"{tot['hr']:4d}{tot['rbi']:4d}{tot['bb']:3d}{tot['k']:3d}"
        + _avg(tot["h"], tot["ab"]).rjust(6)
    )
    out.append(totals)
    return out


def _batting_annotations(rows: list[dict]) -> list[str]:
    """2B/3B/HR/SB lines, indented two spaces, period-terminated."""
    def _collect(field: str) -> list[tuple[str, int]]:
        items = []
        for r in rows:
            n = r.get(field) or 0
            if n > 0:
                items.append((_short_name(r.get("player_name", "?")), n))
        return items

    out = []
    parts = []
    for label, field in (("2B", "doubles"), ("3B", "triples"),
                         ("HR", "hr"), ("SB", "sb")):
        items = _collect(field)
        if items:
            parts.append(f"{label}: {_join_names(items)}")
    if parts:
        out.append("  " + " ".join(parts))
    return out


# Pitching header / row layout
#
# Cols 1-27   name (with optional " (W)" / " (L)" inline)
# 28-31  BF    %4d
# 32-35  P     %4d
# 36-40  OS%   %5s   (e.g. ' 22%')
# 41-44  OUT   %4d
# 45-47  H     %3d
# 48-50  R     %3d
# 51-53  ER    %3d
# 54-56  BB    %3d
# 57-59  K     %3d
# 60-62  HR    %3d
# 63-66  GSc   %4d

_PIT_HEADER = (
    "BF".rjust(4) + "P".rjust(4) + "OS%".rjust(5) + "OUT".rjust(4)
    + "H".rjust(3) + "R".rjust(3) + "ER".rjust(3) + "BB".rjust(3)
    + "K".rjust(3) + "HR".rjust(3) + "GSc".rjust(4)
)


def _pick_decisions(away_rows: list[dict], home_rows: list[dict],
                    winner_id: int | None,
                    away_team_id: int, home_team_id: int) -> tuple:
    """Return (winner_pid, loser_pid). Heuristic-only — no data-model change.

    W: last pitcher (by row order, which approximates appearance order) on
       the winning side who actually recorded an out.
    L: pitcher on the losing side with the most runs allowed. Ties broken
       by appearance order (earliest wins → starter takes the L by default).
    """
    if winner_id is None:
        return None, None
    if winner_id == away_team_id:
        win_rows, lose_rows = away_rows, home_rows
    elif winner_id == home_team_id:
        win_rows, lose_rows = home_rows, away_rows
    else:
        return None, None

    win_pid = None
    for r in reversed(win_rows):
        if (r.get("outs_recorded") or 0) > 0:
            win_pid = r.get("player_id")
            break

    lose_pid = None
    if lose_rows:
        worst = max(lose_rows, key=lambda r: (r.get("runs_allowed") or 0))
        lose_pid = worst.get("player_id") if (worst.get("runs_allowed") or 0) > 0 else None

    return win_pid, lose_pid


def _pitching_block(team_name: str, rows: list[dict],
                    win_pid: int | None, lose_pid: int | None,
                    denom_outs: int) -> list[str]:
    head = (
        ((team_name or "").upper() + " PITCHING")[:_PRE_NUM].ljust(_PRE_NUM)
        + _PIT_HEADER
    )
    out = [head]

    for r in rows:
        nm = _short_name(r.get("player_name", ""))
        pid = r.get("player_id")
        if pid == win_pid:
            nm = f"{nm} (W)"
        elif pid == lose_pid:
            nm = f"{nm} (L)"

        bf  = r.get("batters_faced", 0) or 0
        pi  = r.get("pitches", 0) or 0
        out_rec = r.get("outs_recorded", 0) or 0
        h   = r.get("hits_allowed", 0) or 0
        ru  = r.get("runs_allowed", 0) or 0
        er  = r.get("er") if r.get("er") is not None else ru
        bb  = r.get("bb", 0) or 0
        k   = r.get("k", 0) or 0
        hr  = r.get("hr_allowed", 0) or 0
        gsc = int(round(r.get("gsc_avg") or 0))
        os  = _pct(out_rec, denom_outs)

        line = (
            _name_no_pos(nm)
            + f"{bf:4d}{pi:4d}{os:>5}{out_rec:4d}"
            + f"{h:3d}{ru:3d}{er:3d}{bb:3d}{k:3d}{hr:3d}{gsc:4d}"
        )
        out.append(line)
    return out


def _pitching_annotations(away_rows: list[dict], home_rows: list[dict]) -> list[str]:
    """FO + HBP annotations. WP/balk skipped — engine doesn't track them."""
    out = []

    def _items(rows, field):
        items = []
        for r in rows:
            n = r.get(field) or 0
            if n > 0:
                items.append((_short_name(r.get("player_name", "?")), n))
        return items

    fo_items = _items(away_rows, "fo_induced") + _items(home_rows, "fo_induced")
    hbp_items = _items(away_rows, "hbp_allowed") + _items(home_rows, "hbp_allowed")

    parts = []
    if fo_items:
        parts.append(f"FO: {_join_names(fo_items)}")
    if hbp_items:
        parts.append(f"HBP: {_join_names(hbp_items)}")
    if parts:
        out.append("  " + " ".join(parts))
    return out


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def render_box_score(
    *,
    game: dict,
    phases: list[int],
    away_line: dict,
    home_line: dict,
    away_batting: list[dict],
    home_batting: list[dict],
    away_pitching: list[dict],
    home_pitching: list[dict],
    weather,  # o27.engine.weather.Weather
) -> str:
    rule = "=" * _RULE_WIDTH

    si_rounds = max(0, max(phases) if phases else 0)
    pitch_denom = 27 + 5 * si_rounds

    win_pid, lose_pid = _pick_decisions(
        away_pitching, home_pitching,
        winner_id=game.get("winner_id"),
        away_team_id=game.get("away_team_id"),
        home_team_id=game.get("home_team_id"),
    )

    lines: list[str] = []
    lines.append(rule)
    lines.append(_title_line(game))
    lines.append(rule)
    lines.append("")
    lines.extend(_line_score(game, phases, away_line, home_line))
    lines.append("")
    lines.extend(_batting_block(game.get("away_name", ""), away_batting))
    lines.extend(_batting_annotations(away_batting))
    lines.append("")
    lines.extend(_batting_block(game.get("home_name", ""), home_batting))
    lines.extend(_batting_annotations(home_batting))
    lines.append("")
    lines.extend(_pitching_block(
        game.get("away_name", ""), away_pitching, win_pid, lose_pid, pitch_denom,
    ))
    lines.append("")
    lines.extend(_pitching_block(
        game.get("home_name", ""), home_pitching, win_pid, lose_pid, pitch_denom,
    ))
    pa = _pitching_annotations(away_pitching, home_pitching)
    if pa:
        lines.append("")
        lines.extend(pa)
    lines.append("")

    footer_bits = []
    if weather is not None:
        footer_bits.append(f"Weather: {weather.box_score_line()}")
    seed = game.get("seed")
    if seed is not None:
        footer_bits.append(f"seed {seed}")
    if footer_bits:
        lines.append(" ".join(footer_bits))
    lines.append(rule)

    return "\n".join(lines)
