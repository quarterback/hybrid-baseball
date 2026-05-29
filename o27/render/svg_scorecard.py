"""O27 scorecard rendered as inline SVG for the web app.

The visual vocabulary mirrors the Metapost fork in `scorecard/`:
- 12-row lineup grid (8 fielders + SP + 3 DH)
- Out# ruler at the top of each cell column (running outs)
- Diamond in each PA cell with outcome label
- Stay tickmarks (top-left dots) when stays > 0
- Joker glyph (J<id>, top-right) for joker insertions
- Walk-Back marker on HR cells
- Declared Seconds: bold divider + notes line
- Pitcher arc bar at the footer plotted against the out ruler

The renderer is pure — it takes structured PA records and team info,
emits an SVG string. PA records are extracted from the engine's text
PBP log via `extract_pa_records` (a temporary adapter — the cleaner
path is for the engine to emit structured PA events directly).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape


@dataclass
class PARecord:
    half: str             # "top" | "bot"
    half_idx: int         # 1-based half number (1 = top of 1st, ...)
    seq: int              # 1-based PA index within the half
    batter: str           # display name
    outcome: str          # short label, e.g. "K", "1B", "F8", "HR", "BB"
    is_out: bool          # did this PA produce an out
    is_joker: bool = False
    joker_id: int | None = None
    is_walk_back: bool = False
    stays: int = 0
    out_at_end: int = 0   # running outs after this PA in the half


# --- Log parsing ----------------------------------------------------------

_BATTER_RE = re.compile(r"^--- Now batting: (.+?) ---$")
_PITCH_RE = re.compile(r"^\[(\d+) outs \| (\d+)-(\d+) \| ([^\]]*)\] (.+)$")
_JOKER_RE = re.compile(r"joker (.+?) for an extra plate appearance \(joker #(\d+)\)")
_OUTCOME_TAGS = {
    "STRIKEOUT": ("K", True),
    "FOUL OUT": ("FO", True),
    "GROUND OUT": ("G", True),
    "FLY OUT": ("F", True),
    "LINE OUT": ("L", True),
    "POP OUT": ("P", True),
    "DOUBLE PLAY": ("DP", True),
    "TRIPLE PLAY": ("TP", True),
    "FIELDER'S CHOICE": ("FC", True),
    "INTENTIONAL WALK": ("IBB", False),
    "WALK": ("BB", False),
    "HIT BY PITCH": ("HBP", False),
    "HOME RUN": ("HR", False),
    "TRIPLE": ("3B", False),
    "DOUBLE": ("2B", False),
    "SINGLE": ("1B", False),
}


def _classify_outcome(text: str) -> tuple[str, bool] | None:
    upper = text.upper()
    for tag, (label, is_out) in _OUTCOME_TAGS.items():
        if tag in upper:
            return label, is_out
    return None


def extract_pa_records(log_lines: list[str]) -> list[PARecord]:
    """Parse the engine's text PBP into structured PA records.

    Temporary adapter — the cleaner path is for the engine to emit
    structured PA events directly. Misses anything the text log
    doesn't explicitly carry (e.g. Walk-Back chain reconstruction).
    """
    records: list[PARecord] = []
    half = ""
    half_idx = 0
    seq = 0
    out_running = 0
    pending: dict | None = None

    def commit():
        nonlocal pending, seq, out_running
        if pending and pending.get("outcome"):
            seq += 1
            if pending["is_out"]:
                out_running += 1
            records.append(PARecord(
                half=half, half_idx=half_idx, seq=seq,
                batter=pending["batter"], outcome=pending["outcome"],
                is_out=pending["is_out"], is_joker=pending.get("is_joker", False),
                joker_id=pending.get("joker_id"),
                is_walk_back=pending.get("is_walk_back", False),
                stays=pending.get("stays", 0),
                out_at_end=out_running,
            ))
        pending = None

    pending_joker: dict | None = None

    for raw in log_lines:
        for line in raw.splitlines():
            s = line.strip()
            if not s:
                continue
            if "TOP HALF" in s:
                commit()
                half = "top"; half_idx += 1; seq = 0; out_running = 0
                pending_joker = None
                continue
            if "BOTTOM HALF" in s:
                commit()
                half = "bot"; half_idx += 1; seq = 0; out_running = 0
                pending_joker = None
                continue
            m = _BATTER_RE.match(s)
            if m:
                commit()
                pending = {
                    "batter": m.group(1).strip(),
                    "outcome": None, "is_out": False,
                    "stays": 0,
                    "is_joker": bool(pending_joker),
                    "joker_id": pending_joker["id"] if pending_joker else None,
                }
                pending_joker = None
                continue
            jm = _JOKER_RE.search(s)
            if jm:
                pending_joker = {"name": jm.group(1).strip(), "id": int(jm.group(2))}
                continue
            if pending is None:
                continue
            classified = _classify_outcome(s)
            if classified:
                label, is_out = classified
                if pending["outcome"] is None or is_out:
                    pending["outcome"] = label
                    pending["is_out"] = is_out
                    if label == "HR":
                        pending["is_walk_back"] = True
            if "stays at the plate" in s.lower() or "stays — runners advance" in s.lower():
                pending["stays"] = pending.get("stays", 0) + 1

    commit()
    return records


# --- SVG rendering --------------------------------------------------------

# Cell + grid geometry (px). Designed to fit ~30 PA columns in a
# 1400-wide viewBox; viewer can scale via CSS.
_CELL_W = 42
_CELL_H = 52
_LINEUP_W = 170
_HEADER_H = 30
_FOOTER_H = 90
_ROWS = 12


def _diamond_path(cx: float, cy: float, r: float = 14) -> str:
    return f"M{cx},{cy-r} L{cx+r},{cy} L{cx},{cy+r} L{cx-r},{cy} Z"


def _draw_pa_cell(x: float, y: float, pa: PARecord, outs_so_far: int) -> str:
    """Inner content for a PA cell."""
    cx, cy = x + _CELL_W / 2, y + _CELL_H / 2 + 2
    out = []
    out.append(f'<path d="{_diamond_path(cx, cy)}" fill="none" stroke="#999" stroke-width="0.8"/>')
    if pa.outcome:
        cls = "out" if pa.is_out else "hit"
        out.append(
            f'<text x="{cx:.1f}" y="{cy + 4:.1f}" class="pa-outcome {cls}" '
            f'text-anchor="middle">{escape(pa.outcome)}</text>'
        )
    for i in range(min(pa.stays, 3)):
        out.append(
            f'<circle cx="{x + 5 + i * 4:.1f}" cy="{y + 6:.1f}" r="1.5" '
            f'fill="#c1392b"/>'
        )
    if pa.is_joker:
        jid = pa.joker_id if pa.joker_id else ""
        out.append(
            f'<text x="{x + _CELL_W - 4:.1f}" y="{y + 9:.1f}" class="joker" '
            f'text-anchor="end">J{jid}</text>'
        )
    if pa.is_walk_back:
        out.append(
            f'<text x="{x + _CELL_W - 4:.1f}" y="{y + _CELL_H - 4:.1f}" '
            f'class="walkback" text-anchor="end">WB→3</text>'
        )
    return "".join(out)


def _render_half(
    half_label: str,
    lineup: list[dict],
    pa_records: list[PARecord],
    declared_at: int | None = None,
    pitcher_arc: list[tuple[int, int, str]] | None = None,
) -> str:
    """One team's scorecard SVG (a single half)."""
    n_cols = max(20, ((len(pa_records) + _ROWS - 1) // _ROWS) * 2 + 1)
    n_cols = min(n_cols, 30)
    grid_w = _LINEUP_W + n_cols * _CELL_W
    grid_h = _HEADER_H + _ROWS * _CELL_H
    total_h = grid_h + _FOOTER_H
    view_w = grid_w + 24
    view_h = total_h + 24

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {view_w} {view_h}" class="o27-scorecard" '
        f'role="img" aria-label="O27 scorecard">'
    )
    parts.append(
        '<style>'
        '.o27-scorecard{font-family:Helvetica,Arial,sans-serif;background:#fffdf6;}'
        '.title{font-size:13px;font-weight:600;fill:#222;}'
        '.col-hd{font-size:9px;fill:#444;}'
        '.row-hd{font-size:10px;fill:#222;}'
        '.pa-outcome{font-size:10px;font-weight:600;}'
        '.pa-outcome.out{fill:#222;}'
        '.pa-outcome.hit{fill:#1a6f3a;}'
        '.joker{font-size:8px;fill:#a05a00;font-weight:700;}'
        '.walkback{font-size:7px;fill:#a05a00;font-style:italic;}'
        '.declared{stroke:#a05a00;stroke-width:2;}'
        '.declared-lbl{font-size:9px;fill:#a05a00;font-weight:700;}'
        '.arc-frame{fill:none;stroke:#444;stroke-width:0.8;}'
        '.arc-seg{stroke-width:6;stroke-linecap:butt;}'
        '.notes{font-size:10px;fill:#a05a00;}'
        '</style>'
    )

    ox, oy = 12, 12
    # Title
    parts.append(
        f'<text x="{ox}" y="{oy + 14}" class="title">{escape(half_label)}</text>'
    )

    grid_x = ox
    grid_y = oy + 22

    # Column header row with running-out labels and column ticks.
    parts.append(
        f'<text x="{grid_x + 6}" y="{grid_y + 18}" class="col-hd">OUT #</text>'
    )
    for c in range(n_cols):
        x = grid_x + _LINEUP_W + c * _CELL_W
        idx = c  # column index, 0-based
        # vertical column line
        parts.append(
            f'<line x1="{x}" y1="{grid_y}" x2="{x}" y2="{grid_y + grid_h}" '
            f'stroke="#ccc" stroke-width="0.5"/>'
        )

    # The 12 row gridlines + player names.
    parts.append(
        f'<text x="{grid_x + 6}" y="{grid_y + 36}" class="col-hd">POS</text>'
    )
    for r in range(_ROWS + 1):
        y = grid_y + _HEADER_H + r * _CELL_H
        parts.append(
            f'<line x1="{grid_x}" y1="{y}" x2="{grid_x + _LINEUP_W + n_cols * _CELL_W}" '
            f'y2="{y}" stroke="#ccc" stroke-width="0.5"/>'
        )

    # Lineup column
    for r in range(_ROWS):
        y = grid_y + _HEADER_H + r * _CELL_H + _CELL_H / 2 + 4
        player = lineup[r] if r < len(lineup) else {"name": "", "pos": ""}
        name = player.get("name", "")
        pos = player.get("pos", "")
        parts.append(
            f'<text x="{grid_x + 6}" y="{y:.1f}" class="row-hd">'
            f'{r+1}. {escape(name)} <tspan fill="#777">{escape(pos)}</tspan></text>'
        )

    # Per-PA cells. Sequence PAs into columns: each column holds up to
    # 12 PAs vertically, columns flow left-to-right. The row each PA
    # occupies is its lineup position if known; otherwise its sequence
    # number modulo 12.
    lineup_by_name = {p.get("name", ""): i for i, p in enumerate(lineup)}
    col_index = 0
    row_cursor = 0
    last_col_running_out_start = 0
    for pa in pa_records:
        row = lineup_by_name.get(pa.batter, row_cursor % _ROWS)
        x = grid_x + _LINEUP_W + col_index * _CELL_W
        y = grid_y + _HEADER_H + row * _CELL_H
        parts.append(_draw_pa_cell(x, y, pa, 0))
        row_cursor += 1
        if row_cursor % _ROWS == 0:
            # Label the column we just filled with running out at top
            # and bottom.
            col_x_center = grid_x + _LINEUP_W + col_index * _CELL_W + _CELL_W / 2
            parts.append(
                f'<text x="{col_x_center:.1f}" y="{grid_y + _HEADER_H - 4:.1f}" '
                f'class="col-hd" text-anchor="middle">{pa.out_at_end}</text>'
            )
            last_col_running_out_start = pa.out_at_end
            col_index += 1
            if col_index >= n_cols:
                break

    # Declared Seconds divider + notes line.
    if declared_at is not None:
        # Place divider at the column boundary right after the declared
        # column. For v1, just put it at column ceil(declared_at / 12).
        div_col = (declared_at + _ROWS - 1) // _ROWS
        x = grid_x + _LINEUP_W + div_col * _CELL_W
        parts.append(
            f'<line x1="{x}" y1="{grid_y}" x2="{x}" y2="{grid_y + grid_h}" '
            f'class="declared"/>'
        )
        parts.append(
            f'<text x="{x + 4}" y="{grid_y + 10}" class="declared-lbl">DECLARED</text>'
        )

    # Footer: pitcher arc + notes.
    fy = grid_y + grid_h + 18
    arc_left = grid_x + _LINEUP_W
    arc_right = grid_x + _LINEUP_W + 27 * 18 + 40  # 18 px per out, 27 outs
    parts.append(
        f'<text x="{grid_x + 6}" y="{fy + 4}" class="col-hd">Pitcher arc</text>'
    )
    parts.append(
        f'<rect x="{arc_left}" y="{fy - 6}" width="{27 * 18}" height="22" '
        f'class="arc-frame"/>'
    )
    for tick in range(0, 28, 3):
        tx = arc_left + tick * 18
        parts.append(
            f'<line x1="{tx}" y1="{fy + 16}" x2="{tx}" y2="{fy + 22}" '
            f'stroke="#444" stroke-width="0.6"/>'
        )
        parts.append(
            f'<text x="{tx}" y="{fy + 32}" class="col-hd" '
            f'text-anchor="middle">{tick}</text>'
        )
    if pitcher_arc:
        for start, end, name in pitcher_arc:
            sx = arc_left + start * 18
            ex = arc_left + end * 18
            parts.append(
                f'<line x1="{sx}" y1="{fy + 5}" x2="{ex}" y2="{fy + 5}" '
                f'class="arc-seg" stroke="#1a6f3a"/>'
            )
            parts.append(
                f'<text x="{(sx + ex) / 2:.1f}" y="{fy - 2}" class="col-hd" '
                f'text-anchor="middle">{escape(name)}</text>'
            )

    if declared_at is not None:
        parts.append(
            f'<text x="{grid_x + 6}" y="{fy + 56}" class="notes">'
            f'Notes: declared at out {declared_at}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def render_scorecard(
    *,
    visitors_name: str,
    home_name: str,
    visitors_lineup: list[dict],
    home_lineup: list[dict],
    pa_records: list[PARecord],
    declared_visitors: int | None = None,
    declared_home: int | None = None,
    visitors_pitcher_arc: list[tuple[int, int, str]] | None = None,
    home_pitcher_arc: list[tuple[int, int, str]] | None = None,
) -> dict[str, str]:
    """Return the two-half SVG payloads keyed by "visitors" and "home"."""
    visitors_records = [p for p in pa_records if p.half == "top"]
    home_records = [p for p in pa_records if p.half == "bot"]
    return {
        "visitors": _render_half(
            f"{visitors_name} (batting top)",
            visitors_lineup, visitors_records,
            declared_visitors, visitors_pitcher_arc,
        ),
        "home": _render_half(
            f"{home_name} (batting bottom)",
            home_lineup, home_records,
            declared_home, home_pitcher_arc,
        ),
    }
