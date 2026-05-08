"""Tests for o27v2.currency — Indian-style guilder formatting + USD/EUR conversion."""
from __future__ import annotations

import pytest

from o27v2 import currency as c


# ---------------------------------------------------------------------------
# Indian comma formatter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n, expected", [
    (0,                "0"),
    (1,                "1"),
    (999,              "999"),
    (1_000,            "1,000"),
    (99_999,           "99,999"),
    (1_00_000,         "1,00,000"),         # 1 lakh
    (12_34_567,        "12,34,567"),
    (9_99_99_999,      "9,99,99,999"),
    (1_00_00_000,      "1,00,00,000"),      # 1 crore
    (4_70_00_00_000,   "4,70,00,00,000"),   # the canonical "470 crore" example
])
def test_format_indian(n, expected):
    assert c.format_indian(n) == expected


def test_format_indian_negative():
    assert c.format_indian(-1_00_000) == "-1,00,000"


# ---------------------------------------------------------------------------
# Lakh / crore speller
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n, expected", [
    (0,                  "0"),
    (50_000,             "50,000"),         # < 1 lakh → fall back to comma form
    (99_999,             "99,999"),
    (1_00_000,           "1 lakh"),
    (30_00_000,          "30 lakh"),
    (50_00_000,          "50 lakh"),
    (1_00_00_000,        "1 crore"),
    (1_50_00_000,        "1.5 crore"),
    (15_00_00_000,       "15 crore"),
    (470_00_00_000,      "470 crore"),
    (5_00_00_00_000,     "500 crore"),
])
def test_format_crore(n, expected):
    assert c.format_crore(n) == expected


# ---------------------------------------------------------------------------
# USD / EUR conversion + western formatter
# ---------------------------------------------------------------------------

def test_usd_anchor():
    # Anchor: ƒ100 = $1, so 100 crore guilders ≈ $10M USD
    assert c.to_usd(100 * c.CRORE) == pytest.approx(10_000_000)
    assert c.to_usd(c.CRORE) == pytest.approx(100_000)


def test_eur_via_basket_anchor():
    # ƒ ≈ $0.01; €1 ≈ $1.08 → 1 EUR ≈ ƒ108
    eur = c.to_eur(108)
    assert eur == pytest.approx(1.0, rel=0.01)


def test_format_usd_compact():
    assert c.format_usd(100 * c.CRORE) == "$10.0M"
    assert c.format_usd(1000 * c.CRORE) == "$100.0M"
    assert c.format_usd(10_000 * c.CRORE) == "$1.0B"


def test_format_eur_compact():
    # ƒ470 cr / 108 ≈ €43.5M
    rendered = c.format_eur(470 * c.CRORE)
    assert rendered.startswith("€")
    assert rendered.endswith("M")


# ---------------------------------------------------------------------------
# format_money dispatch
# ---------------------------------------------------------------------------

def test_format_money_guilder_default():
    assert c.format_money(470 * c.CRORE) == "ƒ470 crore"


def test_format_money_modes():
    g = 312 * c.CRORE
    assert c.format_money(g, "guilder").startswith("ƒ")
    assert c.format_money(g, "usd").startswith("$")
    assert c.format_money(g, "eur").startswith("€")


def test_format_money_zero():
    assert c.format_money(0, "guilder") == "ƒ0"
    assert c.format_money(0, "usd").startswith("$")
    assert c.format_money(0, "eur").startswith("€")


def test_rates_for_js_shape():
    rates = c.rates_for_js()
    assert rates["symbol"] == "ƒ"
    assert rates["lakh"] == 1_00_000
    assert rates["crore"] == 1_00_00_000
    assert rates["guilderPerUsd"] == 100.0
    assert rates["guilderPerEur"] > rates["guilderPerUsd"]
