"""
Newspaper-style plaintext box score renderer for O27.

Emits a single monospace string suitable for wrapping in a <pre> block
or writing to a .txt file. The format is modeled on 1990s newspaper
box scores: dot leaders, fixed column widths, no internal grid lines,
sections separated by blank lines.

Adaptations for O27:
  - Line score uses phase columns (REG, SI1, SI2, ...) instead of innings.
  - Pitching tables show OUT and OS% instead of IP.
  - Batting tables include a 2C column (Second-Chance ABs).
  - Pitcher decision (W/L/S) is inline with the name; no separate column.
  - Position column shows the actual fielding position the player played
    (`game_position`) — never "UT" — with "J" for jokers.

The renderer is data-only: it expects fully-aggregated per-game rows and
emits text. No DB access here; the caller (web/app.py:game_detail) builds
the row sets and hands them in.
"""
from __future__ import annotations

from typing import Iterable, Optional


# --------------------------------------------------------------------------
# Column widths — change here to retune the whole layout consistently.
# --------------------------------------------------------------------------

NAME_POS_WIDTH = 22   # "Lastname  pos" + dot leaders, total width
STAT_W = 4            # each batting stat column
PIT_STAT_W = 5        # each pitching stat column
RULE_WIDTH = 78


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _last_name(full: str) -> str:
    """Pull the last token from a full name. Handles 'José A. Rojas' → 'Rojas'."""
    full = (full or "").strip()
    if not full:
        return ""
    return full.rsplit(" ", 1)[-1]


def _pos_short(pos: str) -> str:
    """Render the fielding position lowercase, max 2-3 chars. 'UT' shouldn't
    appear by the time we get here — game_position is always concrete — but
    if it does, it falls through unchanged."""
    return (pos or "").lower()


def _name_pos_with_dots(name: str, pos: str, indent: int = 0) -> str:
    """'Biggio       ss .......' — name + position followed by dot leaders
    out to NAME_POS_WIDTH. `indent` (0 or 2) shifts the name right by that
    many spaces while preserving total prefix width — the stat columns
    still align under any starter."""
    last = _last_name(name)
    pos_s = _pos_short(pos)
    name_cap = max(4, 11 - indent)
    head = (" " * indent) + f"{last[:name_cap]:<{name_cap}} {pos_s:<2}"
    if len(head) >= NAME_POS_WIDTH - 1:
        head = head[: NAME_POS_WIDTH - 2]
    pad = NAME_POS_WIDTH - len(head) - 1
    return head + " " + ("." * (pad - 1)) + " "


def _rj(value: object, w: int = STAT_W) -> str:
    if value is None:
        return " " * (w - 1) + "-"
    if isinstance(value, float):
        return f"{value:>{w}.3f}"
    return f"{value!s:>{w}}"


def _rate(num: int, den: int, places: int = 3) -> str:
    if not den:
        return f"{'.000':>{STAT_W + 3}}"
    val = num / den
    return f"{val:>{STAT_W + 3}.{places}f}"


# --------------------------------------------------------------------------
# Section renderers
# --------------------------------------------------------------------------

def render_header(game: dict, away_total_r: int, home_total_r: int) -> str:
    """Title line + venue line:

        AWAY 12, HOME 11                            Date · #id
        at <Park Name>
    """
    away = (game.get("away_abbrev") or game.get("away_name") or "AWAY").upper()
    home = (game.get("home_abbrev") or game.get("home_name") or "HOME").upper()
    title = f"{away} {away_total_r}, {home} {home_total_r}"
    si = int(game.get("super_inning") or 0)
    if si:
        title += f"  ({si} SI)"
    date = str(game.get("game_date") or "")
    gid  = f"#{game.get('id', '?')}"
    right = f"{date} · {gid}"
    pad = max(1, RULE_WIDTH - len(title) - len(right))
    line1 = title + (" " * pad) + right
    park = (game.get("home_park_name") or "").strip()
    if park:
        line2 = f"at {park}"
        return line1 + "\n" + line2
    return line1


def render_line_score(
    game: dict,
    phases: list[int],
    away_line: dict,
    home_line: dict,
) -> str:
    """Line score rows. Phase 0 is 'REG'; phase 1+ is 'SI1', 'SI2', ..."""
    # Column labels.
    cols = []
    for ph in phases:
        cols.append("REG" if ph == 0 else f"SI{ph}")
    cols += ["R", "H", "E"]

    header = " " * 18 + "".join(f"{c:>5}" for c in cols)

    def _row(team_name: str, line: dict) -> str:
        row = f"{team_name:<18}"
        for ph in phases:
            row += f"{(line['runs'].get(ph) or 0):>5}"
        row += f"{line['total_r']:>5}{line['total_h']:>5}{line['total_e']:>5}"
        return row

    return header + "\n" + _row(game.get("away_name", "Away"), away_line) \
                  + "\n" + _row(game.get("home_name", "Home"), home_line)


def _ordered_rows_with_indent(rows: list[dict]) -> list[tuple[dict, int]]:
    """Order batting rows for newspaper-style display.

    - Starters in their original game order (lineup order is preserved by
      the caller; we don't re-sort starters).
    - PH / sub rows immediately follow the starter they replaced, indented.
    - Joker rows trail at the end, un-indented but flagged with position
      "J" so the reader sees they're tactical pinch-hitters.

    Returns list of (row, indent_level) tuples. indent_level is 0 for
    starters/jokers, 1 for PH/sub.
    """
    starters = [r for r in rows if r.get("entry_type", "starter") == "starter"]
    phs      = [r for r in rows if r.get("entry_type") in ("PH", "PR", "DEF", "sub", "joker_field")]
    jokers   = [r for r in rows if r.get("entry_type") == "joker"]

    # Index PH/sub rows under the starter they replaced.
    by_replaced: dict = {}
    for r in phs:
        rp = r.get("replaced_player_id")
        if rp is None:
            continue
        by_replaced.setdefault(int(rp), []).append(r)

    out: list[tuple[dict, int]] = []
    for s in starters:
        out.append((s, 0))
        for sub in by_replaced.get(s.get("player_id"), []):
            out.append((sub, 1))
    # Any PH/sub rows that we couldn't pair (legacy rows missing
    # replaced_player_id, or replacement of a player not in the table)
    # land at the end indented but un-paired.
    placed = {id(t[0]) for t in out}
    for r in phs:
        if id(r) not in placed:
            out.append((r, 1))
    # Jokers trail.
    for j in jokers:
        out.append((j, 0))
    return out


def render_batting_table(team_name: str, rows: Iterable[dict]) -> str:
    """Per-team batting block: TEAM NAME, header row, player rows w/ dot
    leaders, totals row. PA is intentionally omitted (implied; box-score
    convention is to show AB and let the reader infer)."""
    rows = list(rows)
    out = [team_name.upper()]

    # Column header — name+pos area is blank, stats right-aligned.
    cols = ["AB", "R", "H", "2B", "3B", "HR", "RBI", "BB", "K", "2C"]
    header = " " * NAME_POS_WIDTH + "".join(_rj(c) for c in cols) + f"{'H/AB':>{STAT_W + 3}}"
    out.append(header)

    totals = {k.lower(): 0 for k in cols}
    ordered = _ordered_rows_with_indent(rows)

    for r, indent in ordered:
        ab  = r.get("ab", 0) or 0
        runs = r.get("runs", 0) or 0
        h   = r.get("hits", 0) or 0
        d2  = r.get("doubles", 0) or 0
        d3  = r.get("triples", 0) or 0
        hr  = r.get("hr", 0) or 0
        rbi = r.get("rbi", 0) or 0
        bb  = r.get("bb", 0) or 0
        k   = r.get("k",  0) or 0
        c2  = r.get("stays", 0) or 0   # internal "sty" maps to 2C count

        # Position label depends on entry type:
        #   PH  → "ph"
        #   PR  → "pr"
        #   DEF → the fielding slot they took
        #   sub → same
        #   joker_field → the slot they took (e.g. "J→SS"), already on box_position
        #   joker / starter → their concrete game_position
        et = r.get("entry_type", "starter")
        if et == "PH":
            pos = "ph"
        elif et == "PR":
            pos = "pr"
        else:
            pos = r.get("box_position") or r.get("position", "")

        prefix = _name_pos_with_dots(
            r.get("player_name", ""), pos,
            indent=2 if indent else 0,
        )

        line = (
            prefix
            + _rj(ab) + _rj(runs) + _rj(h)
            + _rj(d2) + _rj(d3) + _rj(hr)
            + _rj(rbi) + _rj(bb) + _rj(k) + _rj(c2)
            + _rate(h, ab)
        )
        out.append(line)

        totals["ab"]  += ab;  totals["r"]  += runs; totals["h"] += h
        totals["2b"]  += d2;  totals["3b"] += d3;   totals["hr"] += hr
        totals["rbi"] += rbi; totals["bb"] += bb;   totals["k"] += k
        totals["2c"]  += c2

    # Totals row — left-justify "Totals" inside the name area, then numbers.
    label = "Totals".ljust(NAME_POS_WIDTH)
    tline = (
        label
        + _rj(totals["ab"]) + _rj(totals["r"])  + _rj(totals["h"])
        + _rj(totals["2b"]) + _rj(totals["3b"]) + _rj(totals["hr"])
        + _rj(totals["rbi"]) + _rj(totals["bb"]) + _rj(totals["k"])
        + _rj(totals["2c"])
        + _rate(totals["h"], totals["ab"])
    )
    out.append(tline)
    return "\n".join(out)


def render_batting_annotations(rows: Iterable[dict]) -> str:
    """  2B: Lopez, Fletcher.
         HR: Smith (12).
         SB: ...
         E: Edwards.
    Each line indented two spaces. Multi-event same-player formatted as
    'Smith 2 (12)' = "two HR, season totals 12 and 13" → we collapse to
    "Smith 2 (12)" because we only track the running season HR count, not
    the per-event sequence."""
    rows = list(rows)
    lines: list[str] = []

    def _pick(field: str) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for r in rows:
            n = r.get(field, 0) or 0
            if n > 0:
                out.append((_last_name(r.get("player_name") or ""), n))
        return out

    def _items(pairs: list[tuple[str, int]]) -> str:
        parts = []
        for name, n in pairs:
            parts.append(f"{name} {n}" if n > 1 else name)
        return ", ".join(parts)

    pairs = _pick("doubles")
    if pairs:
        lines.append(f"  2B: {_items(pairs)}.")
    pairs = _pick("triples")
    if pairs:
        lines.append(f"  3B: {_items(pairs)}.")
    # HR: include season total in parentheses, real-newspaper convention.
    # "Smith (12)"  =  hit a HR; that was his 12th of the season.
    # "Smith 2 (12)" = hit 2 HR today; season total stands at 12.
    hr_items: list[str] = []
    for r in rows:
        n = r.get("hr", 0) or 0
        if n <= 0:
            continue
        last = _last_name(r.get("player_name") or "")
        season = r.get("season_hr") or n
        if n > 1:
            hr_items.append(f"{last} {n} ({season})")
        else:
            hr_items.append(f"{last} ({season})")
    if hr_items:
        lines.append(f"  HR: {', '.join(hr_items)}.")
    pairs = _pick("sb")
    if pairs:
        lines.append(f"  SB: {_items(pairs)}.")
    pairs = _pick("cs")
    if pairs:
        lines.append(f"  CS: {_items(pairs)}.")
    pairs = _pick("e")
    if pairs:
        lines.append(f"  E: {_items(pairs)}.")
    pairs = _pick("hbp")
    if pairs:
        lines.append(f"  HBP: {_items(pairs)}.")
    pairs = _pick("gidp")
    if pairs:
        lines.append(f"  GIDP: {_items(pairs)}.")
    pairs = _pick("gitp")
    if pairs:
        lines.append(f"  GITP: {_items(pairs)}.")

    return "\n".join(lines)


def render_pitching_table(team_name: str, rows: Iterable[dict],
                          decisions: Optional[dict[int, str]] = None) -> str:
    """Per-team pitching block: TEAM PITCHING, header row, pitcher rows
    with W/L/S inline by name."""
    rows = list(rows)
    decisions = decisions or {}
    out = [f"{team_name.upper()} PITCHING"]
    cols = ["BF", "OUT", "OS%", "H", "R", "ER", "BB", "K", "HR", "P"]
    header = " " * NAME_POS_WIDTH + "".join(f"{c:>{PIT_STAT_W}}" for c in cols)
    out.append(header)

    for r in rows:
        last = _last_name(r.get("player_name") or "")
        pid  = r.get("player_id")
        dec  = decisions.get(pid, "")
        if dec:
            head = f"{last} ({dec})"
        else:
            head = last
        if len(head) > NAME_POS_WIDTH - 1:
            head = head[: NAME_POS_WIDTH - 1]
        pad = NAME_POS_WIDTH - len(head) - 1
        prefix = head + " " + ("." * (pad - 1)) + " "

        outs = r.get("outs_recorded", 0) or 0
        os_pct = f"{int(round(outs / 27 * 100))}%" if outs else "0%"

        line = (
            prefix
            + f"{(r.get('batters_faced') or 0):>{PIT_STAT_W}}"
            + f"{outs:>{PIT_STAT_W}}"
            + f"{os_pct:>{PIT_STAT_W}}"
            + f"{(r.get('hits_allowed') or 0):>{PIT_STAT_W}}"
            + f"{(r.get('runs_allowed') or 0):>{PIT_STAT_W}}"
            + f"{(r.get('er') or r.get('runs_allowed') or 0):>{PIT_STAT_W}}"
            + f"{(r.get('bb') or 0):>{PIT_STAT_W}}"
            + f"{(r.get('k') or 0):>{PIT_STAT_W}}"
            + f"{(r.get('hr_allowed') or 0):>{PIT_STAT_W}}"
            + f"{(r.get('pitches') or 0):>{PIT_STAT_W}}"
        )
        out.append(line)
    return "\n".join(out)


def render_game_notes(game: dict) -> str:
    """Footer: weather, attendance, super-innings, seed."""
    parts: list[str] = []
    si = int(game.get("super_inning") or 0)
    if si:
        parts.append(f"  Super-innings: {si}.")

    bits = []
    weather = game.get("weather") or game.get("weather_label")
    if weather:
        bits.append(f"Weather: {weather}")
    seed = game.get("seed")
    if seed:
        bits.append(f"seed {seed}")
    if bits:
        parts.append("  " + ". ".join(bits) + ".")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Top-level
# --------------------------------------------------------------------------

def render_box_score(
    game: dict,
    phases: list[int],
    away_line: dict,
    home_line: dict,
    away_batting: list[dict],
    home_batting: list[dict],
    away_pitching: list[dict],
    home_pitching: list[dict],
    decisions: Optional[dict[int, str]] = None,
) -> str:
    rule = "=" * RULE_WIDTH

    sections = [
        rule,
        render_header(game, away_line["total_r"], home_line["total_r"]),
        rule,
        "",
        render_line_score(game, phases, away_line, home_line),
        "",
        render_batting_table(game.get("away_name", "Away"), away_batting),
        render_batting_annotations(away_batting),
        "",
        render_batting_table(game.get("home_name", "Home"), home_batting),
        render_batting_annotations(home_batting),
        "",
        render_pitching_table(game.get("away_name", "Away"), away_pitching, decisions),
        "",
        render_pitching_table(game.get("home_name", "Home"), home_pitching, decisions),
        "",
        render_game_notes(game),
        rule,
    ]
    # Drop empty section results (annotations may produce "") but keep the
    # surrounding blank lines for breathing room.
    return "\n".join(s for s in sections if s != "" or True)
