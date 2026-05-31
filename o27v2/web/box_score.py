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


def _name_pos_with_dots(name: str, pos: str, indent: int = 0,
                        footnote: str = "") -> str:
    """'Biggio       ss .......' — name + position followed by dot leaders
    out to NAME_POS_WIDTH. `indent` (0 or 2) shifts the name right by that
    many spaces while preserving total prefix width — the stat columns
    still align under any starter.

    `footnote` (e.g. "a") prefixes the name as 'a-' for substitutes whose
    role is summarized in a footnote line below the table. The footnote
    prefix consumes part of the indent allowance so column alignment is
    preserved."""
    last = _last_name(name)
    pos_s = _pos_short(pos)
    fn = f"{footnote}-" if footnote else ""
    name_cap = max(4, 11 - indent - len(fn))
    head = (" " * indent) + fn + f"{last[:name_cap]:<{name_cap}} {pos_s:<2}"
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
    """Title line + venue line — newspaper-style.

        Mariners 4, Angels 1                           Date · #id
        at <Park Name>

    Winner team comes first (real AP/BR convention). Team mascot only
    on the headline; city/full identifier appears in the line score
    rows below so the headline reads as the league knows them.
    """
    away_mascot = game.get("away_name") or game.get("away_abbrev") or "Away"
    home_mascot = game.get("home_name") or game.get("home_abbrev") or "Home"
    # Winner first.
    if home_total_r >= away_total_r:
        winner_n, winner_r = home_mascot, home_total_r
        loser_n,  loser_r  = away_mascot, away_total_r
    else:
        winner_n, winner_r = away_mascot, away_total_r
        loser_n,  loser_r  = home_mascot, home_total_r
    title = f"{winner_n} {winner_r}, {loser_n} {loser_r}"
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
    """Line score rows. Three possible columns:

    - "1"  — regulation (phase 0).
    - "2"  — seconds round. Each team batted in at most one seconds round
             per game, so the values across seconds phases collapse into a
             single per-team cell.
    - "S"  — super-inning. Both teams bat in every SI round; values
             collapse across SI phases into a single per-team cell.

    Non-regulation cells render as `runs(outs)` so the round's out-budget
    is visible (a team's 5(3) means 5 runs in 3 banked outs). A team that
    didn't bat in that bucket renders as `-`.
    """
    seconds_count = int(bool(game.get("away_seconds_used"))) \
                  + int(bool(game.get("home_seconds_used")))

    def _bucket(ph: int) -> str:
        if ph == 0:
            return "1"
        if ph <= seconds_count:
            return "2"
        return "S"

    def _collapse(line: dict, bucket: str):
        runs = outs = 0
        played = False
        for ph in phases:
            if _bucket(ph) != bucket:
                continue
            r = line["runs"].get(ph)
            o = line.get("outs", {}).get(ph) or 0
            if r is None and not o:
                continue
            played = True
            runs += int(r or 0)
            outs += int(o or 0)
        return runs, outs, played

    # Decide which columns to show.
    show_buckets = ["1"]
    if seconds_count > 0:
        show_buckets.append("2")
    if int(game.get("super_inning") or 0) > 0:
        show_buckets.append("S")

    phase_w = 7
    final_w = 5
    header = " " * 18 + "".join(f"{c:>{phase_w}}" for c in show_buckets) \
                       + "".join(f"{c:>{final_w}}" for c in ("R", "H", "E"))

    def _cell(line: dict, bucket: str) -> str:
        runs, _outs, played = _collapse(line, bucket)
        return "-" if not played else f"{runs}"

    def _row(team_name: str, line: dict) -> str:
        row = f"{team_name:<18}"
        for b in show_buckets:
            row += f"{_cell(line, b):>{phase_w}}"
        row += f"{line['total_r']:>{final_w}}{line['total_h']:>{final_w}}{line['total_e']:>{final_w}}"
        return row

    away_label = game.get("away_city") or game.get("away_name", "Away")
    home_label = game.get("home_city") or game.get("home_name", "Home")
    return header + "\n" + _row(away_label, away_line) \
                  + "\n" + _row(home_label, home_line)


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


def _assign_footnotes(ordered: list[tuple[dict, int]]) -> dict[int, str]:
    """Walk the lineup-ordered rows top to bottom; assign a, b, c, ... to
    each sub row (entry_type in PH/PR/DEF/joker/joker_field). Keyed by
    `id(row)` so callers can look up the letter for a given row dict.
    Letters run a..z then aa, ab, ... (a 12-batter team with one slot
    cycled twice can reach 6+ subs theoretically; cap is courtesy)."""
    out: dict[int, str] = {}
    n = 0
    for r, indent in ordered:
        if not indent:
            continue
        et = r.get("entry_type", "starter")
        if et not in ("PH", "PR", "DEF", "joker", "joker_field"):
            continue
        # a, b, ... z, aa, ab, ...
        if n < 26:
            letter = chr(ord("a") + n)
        else:
            letter = chr(ord("a") + (n // 26) - 1) + chr(ord("a") + (n % 26))
        out[id(r)] = letter
        n += 1
    return out


def _ordinal(n: int) -> str:
    """1 → 1st, 2 → 2nd, 3 → 3rd, 4 → 4th, ..."""
    if 10 <= (n % 100) <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _sub_outcome_phrase(r: dict) -> str:
    """Compose the verb phrase for a PH footnote from cumulative stats.
    Works because a pinch-hitter typically has exactly one PA; multi-PA
    PHs (lineup cycled) get a generic 'batted' phrasing."""
    pa  = int(r.get("pa", 0) or 0)
    ab  = int(r.get("ab", 0) or 0)
    h   = int(r.get("hits", 0) or 0)
    hr  = int(r.get("hr", 0) or 0)
    d3  = int(r.get("triples", 0) or 0)
    d2  = int(r.get("doubles", 0) or 0)
    bb  = int(r.get("bb", 0) or 0)
    k   = int(r.get("k", 0) or 0)
    hbp = int(r.get("hbp", 0) or 0)
    if pa == 0:
        return "Pinch-hit"   # never came to bat (rare — game ended first)
    if pa == 1:
        if hr:  return "Homered"
        if d3:  return "Tripled"
        if d2:  return "Doubled"
        if h:   return "Singled"
        if bb:  return "Walked"
        if hbp: return "Was hit by a pitch"
        if k:   return "Struck out"
        return "Grounded out"
    return "Batted"


def _render_sub_footnotes(
    rows: Iterable[dict],
    footnotes: dict[int, str],
    row_by_id: dict[int, dict],
) -> str:
    """Emit the footnote block:
        a-Singled for Skanes in the 5th.
        b-Ran for Rosas in the 7th.
    `row_by_id` maps player_id → row (starter or prior sub) so we can name
    who the sub came in for. Indented two spaces, matching the
    annotations block convention."""
    lines: list[str] = []
    for r in rows:
        letter = footnotes.get(id(r))
        if not letter:
            continue
        et = r.get("entry_type", "starter")
        replaced_pid = r.get("replaced_player_id")
        replaced_name = "—"
        if replaced_pid is not None:
            rep = row_by_id.get(int(replaced_pid))
            if rep is not None:
                replaced_name = _last_name(rep.get("player_name", "")) or "—"
        inning = int(r.get("entered_inning", 0) or 0)
        inning_phrase = f" in the {_ordinal(inning)}" if inning else ""
        if et == "PH":
            verb = _sub_outcome_phrase(r)
            lines.append(f"  {letter}-{verb} for {replaced_name}{inning_phrase}.")
        elif et == "PR":
            lines.append(f"  {letter}-Ran for {replaced_name}{inning_phrase}.")
        elif et == "DEF":
            pos = (r.get("box_position") or r.get("position") or "").upper()
            slot = f" at {pos}" if pos else ""
            lines.append(f"  {letter}-Replaced {replaced_name}{slot}{inning_phrase}.")
        elif et == "joker":
            lines.append(f"  {letter}-Pinch-hit (joker) for {replaced_name}{inning_phrase}.")
        elif et == "joker_field":
            pos = (r.get("box_position") or r.get("position") or "").upper()
            slot = f" at {pos}" if pos else ""
            lines.append(f"  {letter}-Replaced {replaced_name}{slot} (joker to field){inning_phrase}.")
    return "\n".join(lines)


def _validate_pr_no_ab(rows: Iterable[dict]) -> None:
    """Sanity check: a pure pinch-runner who never came to bat (lineup
    didn't cycle back) must have AB=0. The MLB no-reentry rule plus
    the sim's PR handling (PR takes the lifted runner's lineup slot,
    they only get an AB if their slot's turn comes up later) keeps
    this honest; failure indicates either a sim bug or a hand-edited
    row. Asserts in debug — a violation is corrupt data."""
    for r in rows:
        if r.get("entry_type") != "PR":
            continue
        pa = int(r.get("pa", 0) or 0)
        ab = int(r.get("ab", 0) or 0)
        # PR with PA == 0 means they only ran; AB must also be 0.
        if pa == 0 and ab != 0:
            raise AssertionError(
                f"PR {r.get('player_name')!r} has ab={ab} but pa=0 — "
                f"stat accounting bug."
            )


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
    footnotes = _assign_footnotes(ordered)
    _validate_pr_no_ab(rows)

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
            footnote=footnotes.get(id(r), ""),
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


def render_batting_annotations(
    rows: Iterable[dict],
    hr_off_pitchers: Optional[dict] = None,
) -> str:
    """  2B: Lopez, Fletcher.
         HR: Smith (12), off Hernandez; Trout (1), off Weaver.
         SB: ...
         E: Edwards.
    Each line indented two spaces. Multi-event same-player formatted as
    'Smith 2 (12)' = "two HR, season totals 12 and 13" → we collapse to
    "Smith 2 (12)" because we only track the running season HR count, not
    the per-event sequence.

    hr_off_pitchers — optional map of {batter_player_id_str → [pitcher
    last names, in order]}, used to append "off Pitcher" to each HR
    note in AP/newspaper style.
    """
    rows = list(rows)
    lines: list[str] = []

    def _xbh_items(game_field: str, season_field: str) -> list[str]:
        """2B/3B with a season-to-date total in parens, matching the HR
        line and real newspaper convention: 'Konan 2 (9)' = 2 doubles
        today, 9 on the season; 'Beard (3)' = his 3rd of the season."""
        out: list[str] = []
        for r in rows:
            n = r.get(game_field, 0) or 0
            if n <= 0:
                continue
            last = _last_name(r.get("player_name") or "")
            season = r.get(season_field) or n
            out.append(f"{last} {n} ({season})" if n > 1 else f"{last} ({season})")
        return out

    d2 = _xbh_items("doubles", "season_doubles")
    if d2:
        lines.append(f"  2B: {', '.join(d2)}.")
    d3 = _xbh_items("triples", "season_triples")
    if d3:
        lines.append(f"  3B: {', '.join(d3)}.")
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
            item = f"{last} {n} ({season})"
        else:
            item = f"{last} ({season})"
        # Newspaper convention: "off Hernandez" appended to each HR.
        if hr_off_pitchers:
            pid = str(r.get("player_id") or "")
            pitchers = hr_off_pitchers.get(pid) or []
            if pitchers:
                # If batter hit 2 HRs off two different pitchers, join the
                # surnames; if all off the same pitcher, list once.
                uniq = []
                for p in pitchers:
                    if p and p not in uniq:
                        uniq.append(p)
                item += f", off {', '.join(uniq)}"
        hr_items.append(item)
    if hr_items:
        lines.append(f"  HR: {'; '.join(hr_items)}.")
    # Every remaining counting line carries the same season-to-date
    # parenthetical (label, game field, season field).
    for label, game_field, season_field in (
        ("SB",   "sb",   "season_sb"),
        ("CS",   "cs",   "season_cs"),
        ("E",    "e",    "season_e"),
        ("HBP",  "hbp",  "season_hbp"),
        ("GIDP", "gidp", "season_gidp"),
        ("GITP", "gitp", "season_gitp"),
    ):
        items = _xbh_items(game_field, season_field)
        if items:
            lines.append(f"  {label}: {', '.join(items)}.")

    return "\n".join(lines)


def render_pitching_table(team_name: str, rows: Iterable[dict],
                          decisions: Optional[dict[int, str]] = None,
                          season_wl: Optional[dict] = None) -> str:
    """Per-team pitching block: TEAM PITCHING, header row, pitcher rows
    with W/L/S inline by name. When `season_wl` is supplied the decision
    carries the pitcher's season W-L through this game — '(W, 5-3)' —
    real-newspaper style."""
    rows = list(rows)
    decisions = decisions or {}
    season_wl = season_wl or {}
    out = [f"{team_name.upper()} PITCHING"]
    cols = ["BF", "OUT", "OS%", "H", "R", "ER", "BB", "K", "HR", "P"]
    header = " " * NAME_POS_WIDTH + "".join(f"{c:>{PIT_STAT_W}}" for c in cols)
    out.append(header)

    for r in rows:
        last = _last_name(r.get("player_name") or "")
        pid  = r.get("player_id")
        dec  = decisions.get(pid, "")
        if dec:
            rec = season_wl.get(pid)
            if rec:
                head = f"{last} ({dec}, {rec.get('w', 0)}-{rec.get('l', 0)})"
            else:
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
    """Footer: weather, attendance, super-innings, declarations / seconds, seed."""
    parts: list[str] = []

    # Batting order: in O27 the home team may elect to bat first. Spell it
    # out so a reader (or downstream agent) doesn't misread a line-score
    # cell — a team that never came up renders the same '0'/'-' as one that
    # batted and was held scoreless. Emitted only when the choice is known.
    hbf = game.get("home_bats_first")
    if hbf is not None:
        home_name = game.get("home_name") or game.get("home_abbrev") or "Home"
        away_name = game.get("away_name") or game.get("away_abbrev") or "Away"
        if int(hbf or 0):
            parts.append(f"  Batting order: {home_name} (home) batted first; "
                         f"{away_name} batted second.")
        else:
            parts.append(f"  Batting order: {home_name} (home) batted second; "
                         f"{away_name} batted first.")

    si = int(game.get("super_inning") or 0)
    if si:
        parts.append(f"  Super-innings: {si}.")

    # Declared Seconds: balk-style one-liner. Each declaration formats as
    #   `TEAM oN (X-Y)`
    # where the score is (team_referred-opponent), team_referred always first.
    # Multiple declarations comma-separated under one "Seconds:" prefix.
    away_decl = game.get("away_declared_at")
    home_decl = game.get("home_declared_at")
    if away_decl is not None or home_decl is not None:
        away_name = game.get("away_abbrev") or "AWAY"
        home_name = game.get("home_abbrev") or "HOME"
        entries: list[str] = []
        if away_decl is not None:
            sf = game.get("away_declare_score_for")
            sa = game.get("away_declare_score_against")
            score = f" ({sf}-{sa})" if sf is not None and sa is not None else ""
            entries.append(f"{away_name} o{away_decl}{score}")
        if home_decl is not None:
            sf = game.get("home_declare_score_for")
            sa = game.get("home_declare_score_against")
            score = f" ({sf}-{sa})" if sf is not None and sa is not None else ""
            entries.append(f"{home_name} o{home_decl}{score}")
        parts.append("  Seconds: " + ", ".join(entries) + ".")

    bits = []
    from o27.engine.gametime import format_start
    first_pitch = format_start(game.get("start_minute"), game.get("start_utc_offset"))
    if first_pitch:
        bits.append(f"First pitch {first_pitch}")
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

def _sub_footnotes_for(rows: list[dict]) -> str:
    """Public-facing helper: re-derive footnote letters + starter map from
    rows so render_box_score can emit the footnote lines beneath each
    team's annotations block. Returns "" if no subs."""
    ordered = _ordered_rows_with_indent(list(rows))
    footnotes = _assign_footnotes(ordered)
    if not footnotes:
        return ""
    # Index every row by player_id — not just starters. A sub can come in
    # for another sub (a chained substitution: PR for a PH, a DEF for a PR),
    # in which case replaced_player_id points at the prior sub's row. Limiting
    # this map to starters left those footnotes unresolved ("for —").
    row_by_id: dict[int, dict] = {}
    for r, _indent in ordered:
        pid = r.get("player_id")
        if pid is not None:
            row_by_id[int(pid)] = r
    # Emit in lineup order so a precedes b precedes c.
    lineup_ordered = [r for r, _ in ordered if id(r) in footnotes]
    return _render_sub_footnotes(lineup_ordered, footnotes, row_by_id)


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
    hr_off_pitchers: Optional[dict] = None,
    season_wl: Optional[dict] = None,
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
        render_batting_annotations(away_batting, hr_off_pitchers),
        _sub_footnotes_for(away_batting),
        "",
        render_batting_table(game.get("home_name", "Home"), home_batting),
        render_batting_annotations(home_batting, hr_off_pitchers),
        _sub_footnotes_for(home_batting),
        "",
        render_pitching_table(game.get("away_name", "Away"), away_pitching, decisions, season_wl),
        "",
        render_pitching_table(game.get("home_name", "Home"), home_pitching, decisions, season_wl),
        "",
        render_game_notes(game),
        rule,
    ]
    # Drop empty section results (annotations may produce "") but keep the
    # surrounding blank lines for breathing room.
    return "\n".join(s for s in sections if s != "" or True)
