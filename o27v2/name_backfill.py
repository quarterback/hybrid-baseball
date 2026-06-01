"""One-shot backfill that fixes junk player names already in the DB.

A name is "junk" when:
  * NULL or empty after stripping
  * any whitespace-split token matches ^[A-Z]{1,3}$ (e.g. 'CB FF',
    'PSV Arias') — same pattern the pool scrub now filters out at
    generation time
  * any token is in `_KNOWN_JUNK` — a curated set of sports-club
    abbreviations, common nouns, and category labels that slipped
    into the upstream name source

Junk-named players get a fresh draw from the same US-region mixed-
gender picker the college rollover + initial roster generators use,
so the replacements blend in with the rest of the league. Country is
preserved if already set, otherwise filled from the picker.

Covers both `players` (pro) and `college_players` tables. Idempotent —
re-running on a clean DB is a no-op.
"""
from __future__ import annotations

import random
import re
from typing import Any

from o27v2 import db


_JUNK_TOKEN_RE = re.compile(r"^[A-Z]{1,3}$")
_ALLCAPS_LONG_RE = re.compile(r"^[A-Z]{4,}$")

_KNOWN_JUNK = {
    "Royals", "Stars", "Twins", "Cubs", "Yankees", "Rangers",
    "Bike", "Baloncesto", "Ciclista", "Tigres", "Municipal",
    "Jr.", "Sr.", "sisters", "brothers", "USA",
}


def is_junk_name(name: Any) -> bool:
    if not name or not isinstance(name, str):
        return True
    stripped = name.strip()
    if not stripped:
        return True
    tokens = stripped.split()
    for t in tokens:
        if _JUNK_TOKEN_RE.match(t):
            return True
        if _ALLCAPS_LONG_RE.match(t):
            return True
        if t in _KNOWN_JUNK:
            return True
    return False


def backfill_junk_names(*, rng_seed: int = 0) -> dict:
    """Rename every junk-named row in `players` and `college_players`.

    Returns counts + a small sample of (old, new) pairs for the UI.
    """
    from o27v2.league import make_name_picker
    rng = random.Random((rng_seed or 0) ^ 0xBACFFE17)
    picker = make_name_picker(rng, gender="mixed",
                              region_weights={"us": 1.0})

    sample_pro: list[dict] = []
    sample_college: list[dict] = []
    n_pro = 0
    n_college = 0

    pros = db.fetchall("SELECT id, name, country FROM players")
    for r in pros:
        if not is_junk_name(r["name"]):
            continue
        nm, ctry = picker()
        existing_country = (r["country"] or "").strip() if r["country"] else ""
        new_country = existing_country or ctry
        db.execute(
            "UPDATE players SET name = ?, country = ? WHERE id = ?",
            (nm, new_country, r["id"]),
        )
        n_pro += 1
        if len(sample_pro) < 25:
            sample_pro.append({"id": r["id"], "old": r["name"], "new": nm})

    has_college = db.fetchone(
        "SELECT 1 AS x FROM sqlite_master "
        "WHERE type='table' AND name='college_players'"
    )
    if has_college:
        cps = db.fetchall("SELECT id, name, country FROM college_players")
        for r in cps:
            if not is_junk_name(r["name"]):
                continue
            nm, ctry = picker()
            existing_country = (r["country"] or "").strip() if r["country"] else ""
            new_country = existing_country or ctry
            db.execute(
                "UPDATE college_players SET name = ?, country = ? WHERE id = ?",
                (nm, new_country, r["id"]),
            )
            n_college += 1
            if len(sample_college) < 25:
                sample_college.append({"id": r["id"], "old": r["name"], "new": nm})

    return {
        "ok":              True,
        "renamed_pro":     n_pro,
        "renamed_college": n_college,
        "sample_pro":      sample_pro,
        "sample_college":  sample_college,
    }
