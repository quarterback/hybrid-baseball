"""
Scout-grade helpers: 20-80 integer storage <-> 0.0-1.0 unit float.

The 20-80 scale is the canonical scouting scale used in baseball:
    20 = bottom of the league
    50 = league average
    80 = elite / top of the league

DB storage of skill / speed / pitcher_skill is INTEGER on the 20-80 scale
(Task #47 requirement). The probability engine still works in [0.0, 1.0]
unit floats; convert at the engine boundary using to_unit().

Scale mapping (matches the original web display filter exactly):
    unit 0.15 -> grade 20
    unit 0.50 -> grade 50
    unit 0.85 -> grade 80
i.e. grade = 20 + (unit - 0.15) / 0.70 * 60
     unit  = 0.15 + (grade - 20) / 60 * 0.70
"""
from __future__ import annotations


def to_grade(unit: float) -> int:
    """Convert a [0.0, 1.0] unit float to a clamped 20-80 integer grade."""
    try:
        v = float(unit)
    except (TypeError, ValueError):
        return 50
    grade = 20 + (v - 0.15) / 0.70 * 60
    return max(20, min(80, int(round(grade))))


def to_unit(grade) -> float:
    """Convert a 20-80 integer grade back to a [0.0, 1.0] unit float.

    Accepts ints already on the 20-80 scale (canonical) OR legacy floats
    in [0.0, 1.0] (returns them unchanged, clamped). This dual handling
    keeps older DB rows readable during migration.
    """
    try:
        g = float(grade)
    except (TypeError, ValueError):
        return 0.5
    if g <= 1.0:
        # Legacy unit-float row.
        return max(0.0, min(1.0, g))
    unit = 0.15 + (g - 20.0) / 60.0 * 0.70
    return max(0.0, min(1.0, unit))
