"""
Live FX rates for the financials page.

Primary provider: open.er-api.com (Exchange Rate API, free, no key, 166
currencies — covers everything the game references including the small
Caribbean/Pacific components of the guilder basket that ECB feeds skip).
Cached in-process with a TTL so we don't hammer the endpoint on every
pageview; first cold load on a fresh worker takes the HTTP hit,
subsequent loads hit the cache.

Used by:
  * o27v2.currency.zora_usd() — if `apply_to_zora_basket()` has been
    called, the four zora-basket components (JPY/KRW/USD/CNY) get their
    `ZORA_CURRENT_RATES` overwritten with live values, so the headline
    zora_usd floats with real markets.
  * o27v2.web routes that show the financials page — surface the raw
    rates + fetch metadata for the basket panels.

Fallback: if the fetch fails (network outage, provider down, environment
without outbound HTTP), `get_rates()` returns whatever's in the last-good
cache; if there's no cache at all, returns None and callers should treat
the rate as stale/unavailable.
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Optional

# Every currency the game touches anywhere — both fictional baskets +
# the EUR/USD anchors + reserve war chest + a few extras (GBP, CAD, AUD,
# BRL, MXN) that show up in player nationality / scouting contexts.
TRACKED_SYMBOLS: tuple[str, ...] = (
    # Zora basket
    "JPY", "KRW", "CNY",
    # Guilder basket (Caribbean-Pacific)
    "HTG", "JMD", "PHP", "GYD", "TTD", "DOP", "FJD", "XCD", "VUV",
    # Anchor / cross
    "EUR",
    # Zora reserve war chest (CHF + SGD + EUR + gold; gold = XAU, not
    # quoted by open.er-api so omitted from the live pull)
    "SGD", "CHF",
    # Other major currencies referenced in player nationality / scouting
    "GBP", "CAD", "AUD", "BRL", "MXN", "INR", "ZAR", "TRY", "RUB",
)

# 1-hour cache TTL; page can force-refresh via the `force` flag.
_CACHE_TTL_SECONDS = 60 * 60

_cache: dict = {
    "rates":      None,    # dict[str, float] — units per 1 USD
    "fetched_at": 0.0,     # unix timestamp
    "as_of":      None,    # provider-reported "last update" string
    "provider":   "open.er-api.com",
    "error":      None,    # last error message if a fetch failed
}


def _fetch_open_er_api() -> dict:
    """One HTTP call to open.er-api.com. Raises on any failure."""
    url = "https://open.er-api.com/v6/latest/USD"
    req = urllib.request.Request(url, headers={
        "User-Agent": "o27v2-financials/1.0",
        "Accept":     "application/json",
    })
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def get_rates(*, force: bool = False) -> Optional[dict]:
    """Return {"rates": {SYM: rate, ...}, "as_of": "...", "fetched_at":
    <unix>, "provider": ..., "stale": bool, "error": <str|None>}, or
    None if no rates are available at all (first call, fetch failed).

    `rates` are units per 1 USD (so JPY 150 means ¥150 = $1).
    `stale = True` when the response is from cache past TTL but no
    fresh fetch succeeded.
    """
    now = time.time()
    age = now - (_cache["fetched_at"] or 0)
    have_cache = _cache["rates"] is not None
    if have_cache and not force and age < _CACHE_TTL_SECONDS:
        return _snapshot(stale=False)

    try:
        data = _fetch_open_er_api()
        if data.get("result") != "success":
            raise ValueError(f"provider returned non-success: {data.get('result')}")
        all_rates = data.get("rates") or {}
        _cache["rates"] = {s: float(all_rates[s])
                           for s in TRACKED_SYMBOLS if s in all_rates}
        _cache["fetched_at"] = now
        _cache["as_of"]      = data.get("time_last_update_utc")
        _cache["error"]      = None
        return _snapshot(stale=False)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        _cache["error"] = f"{type(e).__name__}: {e}"
        if have_cache:
            return _snapshot(stale=True)
        return None


def _snapshot(*, stale: bool) -> dict:
    return {
        "rates":      dict(_cache["rates"] or {}),
        "as_of":      _cache["as_of"],
        "fetched_at": _cache["fetched_at"],
        "provider":   _cache["provider"],
        "stale":      stale,
        "error":      _cache["error"],
    }


def apply_to_zora_basket(*, force: bool = False) -> Optional[dict]:
    """Pull live rates and write the JPY/KRW/USD/CNY values into the
    zora basket's `ZORA_CURRENT_RATES` so `currency.zora_usd()` reflects
    real markets. Returns the snapshot used (or None if no rates).
    """
    snap = get_rates(force=force)
    if not snap or not snap["rates"]:
        return snap
    from o27v2 import currency as _cur
    for code in ("JPY", "KRW", "CNY"):
        if code in snap["rates"]:
            _cur.ZORA_CURRENT_RATES[code] = snap["rates"][code]
    _cur.ZORA_CURRENT_RATES["USD"] = 1.0   # USD is always 1.0 vs itself
    return snap
