#!/usr/bin/env python3
"""Build o27v2/data/real_parks.json from the source Google Sheet workbook.

The owner maintains a spreadsheet of real affiliated-baseball stadiums — MLB,
Triple-A (IL/PCL), Double-A (EL/SL/TL), High-A (MWL/NWL/SAL), Single-A
(CAL/CAR/FSL), the complex/spring leagues (ACL/FCL) and the Dominican academies
(DSL/MLBDL). Each tab is laid out with parks as COLUMNS (a Dist + Wall pair per
park) and attributes as ROWS. This script flattens every level tab into one
normalized JSON list that the sim consumes (see o27v2/real_parks.py).

Usage:
    python3 scripts/build_real_parks.py [path/to/workbook.xlsx]

With no argument it downloads the published workbook. Pass a local .xlsx to
rebuild offline. Only the stdlib is used (xlsx is a zip of XML), so this runs
in a bare sandbox.

The generated JSON is the committed source of truth — re-run this only when the
spreadsheet changes.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

SHEET_ID = "14vzfHwMBmsE6HKQHlizyfozplYny5Agnm3sdP992dng"
XLSX_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Tab name -> internal worksheet file. The workbook also carries Template /
# Old|New Park Factors helper tabs we deliberately skip.
LEVEL_SHEETS = {
    "MLB": "sheet4",
    "AAA-IL": "sheet5", "AAA-PCL": "sheet6",
    "AA-EL": "sheet7", "AA-SL": "sheet8", "AA-TL": "sheet9",
    "A+-MWL": "sheet10", "A+-NWL": "sheet11", "A+-SAL": "sheet12",
    "A-CAL": "sheet13", "A-CAR": "sheet14", "A-FSL": "sheet15",
    "R-ACL": "sheet16", "R-FCL": "sheet17", "R-DSL": "sheet18",
    "R-MLBDL": "sheet19",
}

# Coarse tier for each level tab (matches teams_database.json levels where
# possible; complex/DR roll up to "R").
LEVEL_TIER = {
    "MLB": "MLB",
    "AAA-IL": "AAA", "AAA-PCL": "AAA",
    "AA-EL": "AA", "AA-SL": "AA", "AA-TL": "AA",
    "A+-MWL": "A+", "A+-NWL": "A+", "A+-SAL": "A+",
    "A-CAL": "A", "A-CAR": "A", "A-FSL": "A",
    "R-ACL": "R", "R-FCL": "R", "R-DSL": "R", "R-MLBDL": "R",
}

DIM_ROWS = ["Left Line", "Left Field", "Left-Center", "Center Field",
            "Right-Center", "Right Field", "Right Line"]
DIM_KEYS = ["left_line", "left_field", "left_center", "center",
            "right_center", "right_field", "right_line"]
MONTHS = ["April", "May", "June", "July", "August", "September", "October"]
PF_ROWS = {"AVG": "avg", "AVG LHB": "avg_lhb", "AVG RHB": "avg_rhb",
           "2B": "2b", "3B": "3b", "HR": "hr", "LHB HR": "hr_lhb",
           "RHB HR": "hr_rhb", "Overall": "overall"}


def _col_to_idx(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(t.text or "" for t in si.iter(f"{NS}t"))
            for si in root.findall(f"{NS}si")]


def _parse_sheet(z: zipfile.ZipFile, ss: list[str], name: str) -> dict:
    root = ET.fromstring(z.read(f"xl/worksheets/{name}.xml"))
    grid: dict[tuple[int, int], str] = {}
    for c in root.iter(f"{NS}c"):
        m = re.match(r"([A-Z]+)(\d+)", c.get("r"))
        col, row = _col_to_idx(m.group(1)), int(m.group(2))
        t = c.get("t")
        v = c.find(f"{NS}v")
        isn = c.find(f"{NS}is")
        if t == "s" and v is not None:
            val = ss[int(v.text)]
        elif t == "inlineStr" and isn is not None:
            val = "".join(x.text or "" for x in isn.iter(f"{NS}t"))
        elif v is not None:
            val = v.text
        else:
            val = ""
        val = (val or "").strip()
        if val:
            grid[(row, col)] = val
    return grid


def _num(s, ndigits=None):
    if s is None or s == "":
        return None
    s = s.replace(",", "")
    try:
        f = float(s)
    except ValueError:
        return None
    if ndigits is not None:
        f = round(f, ndigits)
    return int(f) if f == int(f) else f


def _parse_level(z, ss, level, sheet) -> list[dict]:
    grid = _parse_sheet(z, ss, sheet)
    # First occurrence of each column-A label wins: the MLB tab repeats every
    # label lower down in an averaged "Overall Statistics" helper block.
    label_row: dict[str, int] = {}
    for (r, c), v in sorted(grid.items()):
        if c == 0 and v not in label_row:
            label_row[v] = r

    park_cols = sorted({c for (r, c), v in grid.items() if r == 1 and c >= 1})
    out: list[dict] = []
    for c in park_cols:
        park = grid.get((1, c), "")
        if not park:
            continue
        rec: dict = {"park": park, "level": level, "tier": LEVEL_TIER[level]}
        rec["source_row2"] = grid.get((2, c), "")

        dist, wall = {}, {}
        for lbl, key in zip(DIM_ROWS, DIM_KEYS):
            r = label_row.get(lbl)
            if r is None:
                continue
            dist[key] = _num(grid.get((r, c)))
            wall[key] = _num(grid.get((r, c + 1)))
        rec["dist"], rec["wall"] = dist, wall

        wx = {}
        for mo in MONTHS:
            r = label_row.get(mo)
            if r is None:
                continue
            wx[mo.lower()] = {"temp": _num(grid.get((r, c))),
                              "humidity": _num(grid.get((r, c + 1)))}
        rec["weather"] = wx

        r = label_row.get("Ballpark")
        if r is not None:
            rec["roof"] = (grid.get((r, c)) or "").lower() or None
            rec["surface"] = (grid.get((r, c + 1)) or "").lower() or None
        r = label_row.get("Other")
        if r is not None:
            rec["altitude_ft"] = _num(grid.get((r, c)))
        r = label_row.get("GPS Coords")
        if r is not None:
            rec["lat"] = _num(grid.get((r, c)))
            rec["lon"] = _num(grid.get((r, c + 1)))
        r = label_row.get("Closest City")
        if r is not None:
            rec["city"] = grid.get((r, c), "") or None
        r = label_row.get("Seating")
        if r is not None:
            rec["seating"] = _num(grid.get((r, c)))

        pf = {}
        for lbl, key in PF_ROWS.items():
            r = label_row.get(lbl)
            if r is None:
                continue
            pf[key] = _num(grid.get((r, c)), ndigits=3)
        rec["park_factors"] = pf

        # MLB row 2 is the team abbreviation; minor-league row 2 is a club /
        # locale label that we keep but don't treat as an MLB abbrev.
        rec["team"] = rec["source_row2"] if level == "MLB" else None
        out.append(rec)
    return out


def _backfill_athletics(parks: list[dict]) -> None:
    """The Athletics' MLB-tab entry (Sutter Health Park) is a placeholder with
    no dimensions — the real park lives on the AAA-PCL tab (Sacramento). Copy
    its geometry/weather/factors onto the MLB record so the A's are playable."""
    mlb = next((p for p in parks if p.get("team") == "ATH"), None)
    src = next((p for p in parks if p["level"] == "AAA-PCL"
                and p["park"] == "Sutter Health Park"), None)
    if not mlb or not src:
        return
    if any(v is not None for v in mlb["dist"].values()):
        return  # already populated — nothing to do
    for k in ("dist", "wall", "weather", "roof", "surface", "altitude_ft",
              "lat", "lon", "city", "seating", "park_factors"):
        mlb[k] = json.loads(json.dumps(src[k]))
    mlb["notes"] = "Athletics' interim home; geometry from AAA-PCL Sacramento."


def main() -> None:
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = os.path.join(os.path.dirname(__file__), "_real_parks.xlsx")
        if not os.path.exists(path):
            print(f"downloading workbook -> {path}")
            urllib.request.urlretrieve(XLSX_URL, path)

    with zipfile.ZipFile(path) as z:
        ss = _shared_strings(z)
        parks: list[dict] = []
        for level, sheet in LEVEL_SHEETS.items():
            parks.extend(_parse_level(z, ss, level, sheet))

    _backfill_athletics(parks)

    out_path = os.path.join(os.path.dirname(__file__), "..", "o27v2", "data",
                            "real_parks.json")
    out_path = os.path.normpath(out_path)
    with open(out_path, "w") as f:
        json.dump(parks, f, indent=1, ensure_ascii=False)
        f.write("\n")

    from collections import Counter
    by_level = Counter(p["level"] for p in parks)
    print(f"wrote {len(parks)} parks -> {out_path}")
    for lvl in LEVEL_SHEETS:
        print(f"  {lvl:10s} {by_level[lvl]}")


if __name__ == "__main__":
    main()
