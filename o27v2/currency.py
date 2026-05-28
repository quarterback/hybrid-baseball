"""
O27 financial register — the guilder (ƒ) currency system.

The guilder is O27's canonical unit. Internal storage and all callers pass
plain Python ints in guilders; this module owns every display decision:
Indian-style numbering (lakh / crore), USD / EUR / zora conversion via a
synthetic basket anchor, and the per-mode entry point used by the Jinja
`money` filter and the matching JS toggle.

Worldbuilding anchors (intentional, documented):
  • USD anchor: ƒ100 = $1 USD (so 1 crore guilders ≈ $100,000 USD;
    100 crore ≈ $10M USD).
  • EUR anchor: derived from the basket so the rate floats with config.
    BASKET_NOMINAL_RATES is given in "currency units per 1 USD"; the
    weighted sum produces an effective USD-per-guilder, which we turn
    around into EUR via a fixed EUR/USD nominal.
  • Zora (Zaryanovia, ZRZ): a post-1993 Slavic-rooted national currency,
    ₴250 = $1 USD by anchor convention (weaker than the guilder, fitting
    a frontier post-Soviet economy). See ZORA section below for the
    full symbol / pluralization / subdivision lore.

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


def format_crore_prose(g: int) -> str:
    """Prose form of a guilder amount: spelled in crore / lakh, no ƒ
    prefix. Suitable for sentences like "Trinidad signed Bonacini for
    312 crore over six years." Use `format_money` (or the `money` Jinja
    filter) for headline / table rendering where the symbol matters."""
    return format_crore(g)


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
# Zora — Zaryanovia's national currency (ZRZ)
# ---------------------------------------------------------------------------
#
# The Zaryan zora is a STRONG, high-PPP currency — 1 ₳ ≈ $13.50 USD at
# baseline. Prices are small dignified numbers (Swiss franc / Kuwaiti dinar
# psychology, not yen / won). The country is a Pacific-Rim resource economy
# on the Norwegian model — a sovereign wealth fund sterilizes oil/gas/gold
# revenue, so the currency floats freely on real prosperity rather than
# being defended at a peg.
#
# Symbol — there is one canonical symbol:
#   ₳   (U+20B3)  ZORA_SYMBOL  — austral sign. Three-stroke, fast, written
#                                everywhere (banknotes, signage, handwriting,
#                                ledgers). The currency wears one face.
#
# Heritage glyph (worldbuilding only — not rendered):
#   ЗР  (U+0417 U+0420)        — the dead Russian-era ruble mark. Lingers as
#                                a generational + border-region habit among
#                                older Zaryans, fading as that cohort ages
#                                out. Documented for the country-info page.
#
# Code: ZRZ (Zaryanovia, reusing the ZR ISO 3166-1 root). ZRN/ZRZ were the
# defunct Zaire codes — taken over here.
#
# Subdivision: 100 luchi = 1 zora ("rays" compose the dawn — zarya is the
# Russian word for dawn; luchi is rays). Singular: luch. All in-game amounts
# are integer guilders so luchi never surface in formatters today; the
# subdivision is lore plus the helper below for any prose context.
#
# Plurals (creole-regularized two-way):
#     zora  (singular)
#     zory  (plural)
# The bare genitive `zor` survives only in archaic/formal register; modern
# Zaryans default to the two-form pair the way creoles always do.

ZORA_SYMBOL = "₳"   # The one canonical zora symbol.
ZORA_DEAD   = "ЗР"  # Dead Russian-era ruble (lore-only, not rendered).

# Backwards-compat alias — early scaffolding called this ZORA_DISPLAY before
# the wiki fixed the symbol to ₳ outright. Kept so external callers don't
# break; new code should reference ZORA_SYMBOL.
ZORA_DISPLAY = ZORA_SYMBOL

ZORA_CODE: str = "ZRZ"

# ---------- Basket-driven rate ----------
#
# The zora floats against a trade-weighted basket:
#     35% Japanese yen   (high-tech manufacturing alignment)
#     35% South Korean won (semiconductor & aerospace integration)
#     15% US dollar      (energy trading + maritime logistics anchor)
#     15% Chinese yuan   (land-border supply chain)
# The Russian ruble is deliberately excluded — being the *stable
# alternative* to the ruble is the whole haven value proposition.
#
# Reserve war chest (backs the float Norwegian-model, not pegged):
#     SGD, CHF, EUR, gold. Worldbuilding only — no code path uses these.
#
# Per-currency index = baseline / current (so a stronger member currency
# means index > 1). Weighted sum is the basket multiplier; baseline
# zora_usd of 13.50 is scaled by it and clamped to [6.55, 19.97].
ZORA_BASKET_WEIGHTS: dict[str, float] = {
    "JPY": 0.35,
    "KRW": 0.35,
    "USD": 0.15,
    "CNY": 0.15,
}
# Baseline FX rates: units per 1 USD. USD itself is by definition 1.0.
ZORA_BASELINE_RATES: dict[str, float] = {
    "JPY":  150.0,
    "KRW": 1350.0,
    "USD":    1.0,
    "CNY":    7.20,
}
# Current FX rates — start at baseline so the headline matches the spec
# rate of $13.50 / zora out of the box. Adjust these and call
# `zora_usd()` to see the basket move the headline.
ZORA_CURRENT_RATES: dict[str, float] = dict(ZORA_BASELINE_RATES)

ZORA_USD_BASELINE: float = 13.50
ZORA_USD_FLOOR:    float = 6.55
ZORA_USD_CEIL:     float = 19.97

ZORA_RESERVE_ASSETS: tuple[str, ...] = ("SGD", "CHF", "EUR", "XAU")  # gold = XAU


def zora_usd() -> float:
    """Current USD value of 1 zora, derived from the basket.

      index_c   = baseline_c / current_c        # >1 ⇒ currency c strengthened
      mult      = Σ weight_c * index_c
      zora_usd  = clamp(13.50 * mult, 6.55, 19.97)
    """
    mult = 0.0
    for code, w in ZORA_BASKET_WEIGHTS.items():
        baseline = ZORA_BASELINE_RATES.get(code, 1.0)
        current  = ZORA_CURRENT_RATES.get(code, baseline) or baseline
        idx = baseline / current
        mult += w * idx
    rate = ZORA_USD_BASELINE * mult
    if rate < ZORA_USD_FLOOR: return ZORA_USD_FLOOR
    if rate > ZORA_USD_CEIL:  return ZORA_USD_CEIL
    return rate


def guilder_per_zora() -> float:
    """Computed via the USD anchor: ƒ100 = $1 → guilders per zora =
    100 * (USD per zora)."""
    return GUILDER_PER_USD * zora_usd()


def to_zora(g: int) -> float:
    return int(g) / guilder_per_zora()


def format_zora(g: int) -> str:
    """Render a guilder amount in zora. Strong-currency formatter —
    small dignified numbers, Swiss-franc psychology. Sub-zora amounts
    render in luchi (₳0.18 would look weak; "18 luchi" is the natural
    Zaryan idiom — same as cents to a dollar, kopeks to a ruble)."""
    z_raw = to_zora(g)
    if 0 < z_raw < 1:
        # Sub-zora: show in luchi (100 luchi = 1 zora).
        luchi = int(round(z_raw * 100))
        if luchi <= 0:
            return f"{ZORA_SYMBOL}0"
        return f"{luchi} luchi" if luchi != 1 else "1 luch"
    z = int(round(z_raw))
    return f"{ZORA_SYMBOL}{z:,}"


def zora_plural(n: int) -> str:
    """Two-form creole vernacular: zora (n == 1), zory (n != 1).
    The archaic/formal genitive `zor` survives in old register but is
    not produced by this helper. Exposed for any prose context."""
    return "zora" if abs(int(n)) == 1 else "zory"


def luch_plural(n: int) -> str:
    """Subunit plural: luch (1) / luchi (else). 100 luchi = 1 zora."""
    return "luch" if abs(int(n)) == 1 else "luchi"


# ---------------------------------------------------------------------------
# Top-level dispatch — used by the Jinja `money` filter
# ---------------------------------------------------------------------------

Mode = Literal["guilder", "usd", "eur", "zora"]


def format_money(g: int, mode: Mode = "guilder") -> str:
    """Render a guilder amount in the requested display mode."""
    g = int(g)
    if mode == "usd":
        return format_usd(g)
    if mode == "eur":
        return format_eur(g)
    if mode == "zora":
        return format_zora(g)
    return f"{GUILDER}{format_crore(g)}"


# ---------------------------------------------------------------------------
# Rates dict — exported to JS so the toggle re-renders without a roundtrip.
# ---------------------------------------------------------------------------

def rates_for_js() -> dict:
    """Snapshot of every constant the front-end toggle needs. Keys match
    `o27v2/web/templates/base.html` JS access patterns."""
    return {
        "symbol":           GUILDER,
        "guilderPerUsd":    GUILDER_PER_USD,
        "guilderPerEur":    GUILDER_PER_EUR,
        "guilderPerZora":   guilder_per_zora(),
        "zoraSymbol":       ZORA_SYMBOL,
        "zoraUsd":          zora_usd(),
        "zoraBasketWeights": dict(ZORA_BASKET_WEIGHTS),
        "lakh":             LAKH,
        "crore":            CRORE,
        "basketWeights":    dict(BASKET_WEIGHTS),
    }
