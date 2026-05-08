"""
O27 financial register — the guilder (ƒ) currency system.

The guilder is O27's canonical unit. Internal storage and all callers pass
plain Python ints in guilders; this module owns every display decision:
Indian-style numbering (lakh / crore), USD and EUR conversion via a
synthetic basket anchor, and the per-mode entry point used by the Jinja
`money` filter and the matching JS toggle.

Worldbuilding anchors (intentional, documented):
  • USD anchor: ƒ100 = $1 USD (so 1 crore guilders ≈ $100,000 USD;
    100 crore ≈ $10M USD).
  • EUR anchor: derived from the basket so the rate floats with config.
    BASKET_NOMINAL_RATES is given in "currency units per 1 USD"; the
    weighted sum produces an effective USD-per-guilder, which we turn
    around into EUR via a fixed EUR/USD nominal.

The basket is config-only here — we don't actually re-anchor the guilder
each time the basket moves. Baking the rate this way keeps the headline
values stable across pageloads while still showing the basket as the
worldbuilding source of truth.
"""
from __future__ import annotations

from typing import Literal

# ---------------------------------------------------------------------------
# Symbol + basket
# ---------------------------------------------------------------------------

GUILDER = "ƒ"  # U+0192 — Latin Small Letter F With Hook

LAKH  = 1_00_000      # 100,000
CRORE = 1_00_00_000   # 10,000,000

# Basket weights — sum to 1.0. Caribbean-Pacific anchor.
BASKET_WEIGHTS: dict[str, float] = {
    "HTG": 0.25,   # Haitian gourde
    "JMD": 0.15,   # Jamaican dollar
    "PHP": 0.15,   # Philippine peso
    "GYD": 0.10,   # Guyanese dollar
    "TTD": 0.10,   # Trinidad & Tobago dollar
    "DOP": 0.10,   # Dominican peso
    "FJD": 0.05,   # Fijian dollar
    "XCD": 0.05,   # Eastern Caribbean dollar
    "VUV": 0.05,   # Vanuatu vatu
}

# Nominal rates: units per 1 USD (rough 2026 frame; deliberately tidy).
# Used only as a worldbuilding artifact — see module docstring.
BASKET_NOMINAL_RATES: dict[str, float] = {
    "HTG": 132.0,
    "JMD": 156.0,
    "PHP":  56.0,
    "GYD": 209.0,
    "TTD":   6.8,
    "DOP":  60.0,
    "FJD":   2.25,
    "XCD":   2.70,
    "VUV": 119.0,
}

# Anchor — independent of the basket. The basket informs flavor; the
# headline conversion ƒ100 = $1 USD is stable.
GUILDER_PER_USD: float = 100.0

# EUR via a fixed EUR/USD nominal (~1 EUR = 1.08 USD).
EUR_PER_USD: float = 1.0 / 1.08
GUILDER_PER_EUR: float = GUILDER_PER_USD / EUR_PER_USD  # ≈ 108.0


def basket_synthetic_usd_per_guilder() -> float:
    """The basket's effective USD/guilder if we re-derived the anchor each
    time. Surfaced for tests + the league-info page; not used by the
    headline formatter (which uses the fixed GUILDER_PER_USD anchor)."""
    if not BASKET_WEIGHTS:
        return 1.0 / GUILDER_PER_USD
    weighted_units_per_usd = sum(
        BASKET_NOMINAL_RATES[code] * weight
        for code, weight in BASKET_WEIGHTS.items()
    )
    # 1 guilder buys 1/100 of a USD basket-unit by anchor convention.
    return (1.0 / weighted_units_per_usd) * (weighted_units_per_usd / GUILDER_PER_USD)


# ---------------------------------------------------------------------------
# Indian-style number formatting
# ---------------------------------------------------------------------------

def format_indian(n: int) -> str:
    """Group an integer in the Indian convention: last 3 digits, then
    pairs. 4_70_00_00_000 → "4,70,00,00,000"."""
    n = int(n)
    if n < 0:
        return "-" + format_indian(-n)
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    # Group `head` in pairs from the right.
    groups: list[str] = []
    while len(head) > 2:
        groups.append(head[-2:])
        head = head[:-2]
    if head:
        groups.append(head)
    return ",".join(reversed(groups)) + "," + tail


def format_crore(n: int) -> str:
    """Spell a guilder amount in lakh / crore. Doesn't include the ƒ
    symbol — callers prepend it. Negative values are allowed but shouldn't
    occur in practice."""
    n = int(n)
    if n < 0:
        return "-" + format_crore(-n)
    if n >= CRORE:
        crores = n / CRORE
        # One decimal when the fractional part adds information; whole
        # number when the value is already a clean integer multiple.
        if crores >= 100 or crores == int(crores):
            return f"{int(round(crores))} crore"
        return f"{crores:.1f} crore"
    if n >= LAKH:
        lakhs = n / LAKH
        if lakhs >= 100 or lakhs == int(lakhs):
            return f"{int(round(lakhs))} lakh"
        return f"{lakhs:.1f} lakh"
    return format_indian(n)


# ---------------------------------------------------------------------------
# USD / EUR conversion
# ---------------------------------------------------------------------------

def to_usd(g: int) -> float:
    return int(g) / GUILDER_PER_USD


def to_eur(g: int) -> float:
    return int(g) / GUILDER_PER_EUR


def _format_western(amount: float, symbol: str) -> str:
    """Compact M / B / K formatter for headline USD / EUR display."""
    sign = "-" if amount < 0 else ""
    a = abs(amount)
    if a >= 1_000_000_000:
        return f"{sign}{symbol}{a / 1_000_000_000:.1f}B"
    if a >= 1_000_000:
        return f"{sign}{symbol}{a / 1_000_000:.1f}M"
    if a >= 10_000:
        return f"{sign}{symbol}{a / 1_000:.0f}K"
    return f"{sign}{symbol}{a:,.0f}"


def format_usd(g: int) -> str:
    return _format_western(to_usd(g), "$")


def format_eur(g: int) -> str:
    return _format_western(to_eur(g), "€")


# ---------------------------------------------------------------------------
# Top-level dispatch — used by the Jinja `money` filter
# ---------------------------------------------------------------------------

Mode = Literal["guilder", "usd", "eur"]


def format_money(g: int, mode: Mode = "guilder") -> str:
    """Render a guilder amount in the requested display mode."""
    g = int(g)
    if mode == "usd":
        return format_usd(g)
    if mode == "eur":
        return format_eur(g)
    return f"{GUILDER}{format_crore(g)}"


# ---------------------------------------------------------------------------
# Rates dict — exported to JS so the toggle re-renders without a roundtrip.
# ---------------------------------------------------------------------------

def rates_for_js() -> dict:
    """Snapshot of every constant the front-end toggle needs. Keys match
    `o27v2/web/templates/base.html` JS access patterns."""
    return {
        "symbol":          GUILDER,
        "guilderPerUsd":   GUILDER_PER_USD,
        "guilderPerEur":   GUILDER_PER_EUR,
        "lakh":            LAKH,
        "crore":           CRORE,
        "basketWeights":   dict(BASKET_WEIGHTS),
    }
