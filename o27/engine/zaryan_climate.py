"""
Zaryanovia climate data — real-anchored monthly weather profiles + UTC
offsets for the country's 28 primary cities (+ Gannibal).

The country spans ~28° of latitude (42°N Slavyan to 71°N Arctic) and
five time zones (UTC+9 to UTC+13). It reaches east of the 180°
meridian at Provideniya, but the International Date Line jogs east
around Chukotka so the whole country sits on one calendar day. The
climate spread is too wide for a single archetype. This module gives every Zaryan city a per-month
high/low/precip series anchored to its real-world parallel:

  Seaward (Garrison, etc.)   ≈ Vladivostok (Dwb humid continental)
  Stratton (founding core)   ≈ Birobidzhan / inland JAO
  Lower/Upper Amur heartland ≈ Khabarovsk
  Sakhalin                   ≈ Yuzhno-Sakhalinsk (Dfb snowy maritime)
  Kamchatka                  ≈ Petropavlovsk-Kamchatsky (Dfc volcanic)
  Zolotoy / Magadan          ≈ Magadan (Dfc subarctic)
  Chukotka                   ≈ Anadyr (ET Arctic tundra)

The data flows two ways:

  1. `o27/engine/weather.py:draw_weather()` checks `city_zaryan_profile()`
     first; when present, the in-season tier weights are derived from the
     profile's hi/precip rather than the archetype tables. So a sim game
     in Neftezma in August samples Yuzhno-Sakhalinsk-style weather
     (cool wet typhoon-season), not the generic continental_cold of
     a default lat/lon match.

  2. External climate viz / future Zaryanovia country page can read the
     full 12-month arrays directly via `monthly_profile(city)`.

UTC offsets are exported as data (`ZARYAN_CITY_UTC_OFFSET`); the game's
scheduler still runs on calendar dates only, so nothing consumes them
yet — they're ready for the day the engine gains time-of-day awareness.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Climate profiles — real-anchored 12-month hi/lo/precip series
# ---------------------------------------------------------------------------
# Indexed Jan -> Dec. Temperatures in °F, precipitation in inches/month.

ZARYAN_CLIMATE_PROFILES: dict[str, dict] = {
    "seaward_garrison": {
        "label":        "Warm south (Seaward / Vladivostok-anchor)",
        "real_anchor":  "Vladivostok",
        "hi_F":      [18, 25, 36, 48, 57, 64, 72, 75, 68, 55, 37, 23],
        "lo_F":      [ 9, 14, 27, 37, 46, 54, 61, 64, 55, 43, 27, 14],
        "precip_in": [0.6, 0.7, 1.0, 1.6, 2.4, 3.0, 4.3, 5.9, 5.1, 2.4, 1.2, 0.7],
    },
    "seaward_gannibal": {
        "label":        "Warm south (Gannibal academic city)",
        "real_anchor":  "Vladivostok (drier inland)",
        "hi_F":      [16, 23, 36, 52, 63, 70, 75, 77, 68, 54, 34, 19],
        "lo_F":      [ 5, 10, 25, 36, 46, 55, 63, 64, 52, 37, 21,  9],
        "precip_in": [0.5, 0.6, 0.9, 1.8, 2.8, 3.1, 4.7, 5.3, 3.5, 2.0, 1.0, 0.6],
    },
    "heartland_amargrad": {
        "label":        "Continental heartland (Lower Amur / Khabarovsk-anchor)",
        "real_anchor":  "Khabarovsk",
        "hi_F":      [ 5, 14, 30, 52, 66, 75, 81, 77, 68, 52, 27,  7],
        "lo_F":      [-9, -4, 13, 32, 45, 55, 62, 60, 49, 34, 14, -6],
        "precip_in": [0.5, 0.5, 0.9, 1.5, 2.8, 3.3, 5.4, 5.6, 3.3, 1.9, 1.0, 0.7],
    },
    "heartland_stratton": {
        "label":        "Continental heartland (Stratton founding core)",
        "real_anchor":  "Birobidzhan / inland JAO",
        "hi_F":      [  3, 12, 28, 50, 64, 73, 79, 75, 64, 48, 25,  5],
        "lo_F":      [-11, -6, 10, 30, 43, 54, 61, 59, 46, 32, 12, -8],
        "precip_in": [0.5, 0.4, 0.8, 1.4, 2.7, 3.2, 5.1, 5.4, 3.2, 1.8, 0.9, 0.6],
    },
    "sakhalin_neftezma": {
        "label":        "Snowy maritime (Sakhalin / Yuzhno-Sakhalinsk-anchor)",
        "real_anchor":  "Yuzhno-Sakhalinsk",
        "hi_F":      [18, 19, 30, 43, 52, 61, 68, 72, 64, 52, 36, 23],
        "lo_F":      [ 3,  3, 16, 30, 39, 48, 55, 59, 50, 37, 21,  9],
        "precip_in": [2.2, 1.8, 2.0, 2.2, 2.4, 2.2, 3.0, 3.9, 4.3, 3.5, 3.0, 2.6],
    },
    "kamchatka_vulkangrad": {
        "label":        "Volcanic maritime (Kamchatka / Petropavlovsk-anchor)",
        "real_anchor":  "Petropavlovsk-Kamchatsky",
        "hi_F":      [23, 23, 30, 37, 46, 55, 63, 64, 57, 46, 34, 27],
        "lo_F":      [12, 10, 18, 27, 36, 43, 50, 52, 45, 36, 25, 16],
        "precip_in": [3.1, 2.8, 2.6, 2.2, 2.2, 2.0, 2.2, 2.8, 3.3, 3.7, 3.5, 3.3],
    },
    "zolotoy_magadan": {
        "label":        "Subarctic (Zolotoy / Magadan-anchor)",
        "real_anchor":  "Magadan",
        "hi_F":      [ 5,  7, 18, 30, 41, 52, 59, 57, 48, 34, 18,  9],
        "lo_F":      [-8, -8,  3, 18, 32, 41, 48, 48, 39, 25,  7, -4],
        "precip_in": [0.8, 0.7, 0.6, 0.7, 0.9, 1.6, 2.2, 2.6, 2.2, 1.6, 1.2, 1.0],
    },
    "chukotka_anadyr": {
        "label":        "Arctic tundra (Chukotka / Anadyr-anchor)",
        "real_anchor":  "Anadyr",
        "hi_F":      [ -2,  -2,   5,  19,  32,  46,  55,  54,  45,  28,  12,   3],
        "lo_F":      [-15, -15,  -9,   5,  25,  36,  43,  43,  36,  19,   0,  -9],
        "precip_in": [0.9, 0.7, 0.6, 0.6, 0.7, 1.1, 1.5, 1.8, 1.6, 1.4, 1.1, 1.0],
    },
}

# Per-city assignments (per the master spec).
ZARYAN_CITY_TO_PROFILE: dict[str, str] = {
    # Seaward — Garrison profile, except Gannibal (its own academic-inland)
    # and New Eldorado which the spec marks ~2°F warmer (still uses Garrison
    # profile; the warmer-than-anywhere note is a flavor mark for the wiki).
    "Garrison":          "seaward_garrison",
    "Gannibal":          "seaward_gannibal",
    "New Eldorado":      "seaward_garrison",
    "Vostok Harbor":     "seaward_garrison",
    "Ussuri":            "seaward_garrison",
    "Partizansk":        "seaward_garrison",
    "Slavyan":           "seaward_garrison",
    # Stratton founding core — Stratton profile
    "Stratton":          "heartland_stratton",
    "New Philadelphia":  "heartland_stratton",
    "Cummings":          "heartland_stratton",
    # Lower / Upper Amur heartland — Amargrad profile (some trend cooler
    # the farther north, but we keep the same profile; the lat/lon-driven
    # archetype lookup in weather.py picks up extra cold for far-north
    # secondary cities)
    "Amargrad":          "heartland_amargrad",
    "Komsa":             "heartland_amargrad",
    "Amursk":            "heartland_amargrad",
    "Nikol":             "heartland_amargrad",
    "Verkhnegrad":       "heartland_amargrad",
    "Svobodny":          "heartland_amargrad",
    "Tynda":             "heartland_amargrad",
    # Zolotoy (gold north) — Magadan profile
    "Magadan City":      "zolotoy_magadan",
    "Susuman":           "zolotoy_magadan",   # spec notes much colder (-40°F lows)
    "Ola":               "zolotoy_magadan",
    # Sakhalin — Neftezma profile
    "Neftezma":          "sakhalin_neftezma",
    "Korsa":             "sakhalin_neftezma",
    "Okha":              "sakhalin_neftezma", # spec notes runs colder than Neftezma
    "Nogliki":           "sakhalin_neftezma",
    # Kamchatka — Vulkangrad profile
    "Vulkangrad":        "kamchatka_vulkangrad",
    "Klyuch":            "kamchatka_vulkangrad",
    "Yelizovo":          "kamchatka_vulkangrad",
    # Chukotka — Anadyr profile
    "Anadyr":            "chukotka_anadyr",
    "Provideniya":       "chukotka_anadyr",
}


# ---------------------------------------------------------------------------
# UTC offsets
# ---------------------------------------------------------------------------
# Country spans 5 time zones, UTC+9 to UTC+13. Provideniya (UTC+13) is
# 4 hours ahead of the capital Garrison (UTC+9) but on the SAME calendar
# day — Provideniya sits geographically east of 180° in the Western
# Hemisphere by longitude, but the International Date Line jogs east
# around Chukotka so the entire country stays on one calendar.

ZARYAN_CITY_UTC_OFFSET: dict[str, int] = {
    # UTC+9 — Seaward populous south + Upper Amur China-border west
    "Garrison":          9,  "Gannibal":          9,  "New Eldorado":     9,
    "Vostok Harbor":     9,  "Ussuri":            9,  "Slavyan":          9,
    "Partizansk":        9,
    "Verkhnegrad":       9,  "Svobodny":          9,  "Tynda":            9,
    # UTC+10 — economic + demographic core (Stratton, Lower Amur, all Sakhalin)
    "Stratton":         10,  "New Philadelphia": 10,  "Cummings":        10,
    "Amargrad":         10,  "Komsa":            10,  "Amursk":          10,
    "Nikol":            10,
    "Neftezma":         10,  "Korsa":            10,  "Okha":            10,
    "Nogliki":          10,
    # UTC+11 — gold north
    "Magadan City":     11,  "Susuman":          11,  "Ola":             11,
    # UTC+12 — Kamchatka peninsula + Anadyr (western Chukotka)
    "Vulkangrad":       12,  "Klyuch":           12,  "Yelizovo":        12,
    "Anadyr":           12,
    # UTC+13 — Provideniya, 4 hours ahead of Garrison, same calendar day
    "Provideniya":      13,
}


# ---------------------------------------------------------------------------
# Public lookups
# ---------------------------------------------------------------------------

def monthly_profile(city: str) -> dict | None:
    """Full 12-month hi/lo/precip for `city`, or None if unknown."""
    prof = ZARYAN_CITY_TO_PROFILE.get(city)
    return ZARYAN_CLIMATE_PROFILES.get(prof) if prof else None


def utc_offset(city: str) -> int | None:
    """UTC offset for `city`, or None if unknown."""
    return ZARYAN_CITY_UTC_OFFSET.get(city)


# ---------------------------------------------------------------------------
# Conversion to in-season tier-weight tables (for the weather sampler)
# ---------------------------------------------------------------------------
# The sampler in o27/engine/weather.py only covers Apr-Sep (baseball
# season). For each profile + month we convert the raw hi_F / precip_in
# into the tier weights the sampler expects:
#
#   temperature ∈ {cold=52°F, mild=66°F, warm=78°F, hot=90°F}
#   precip      ∈ {none, light, heavy}
#   humidity    ∈ {dry, normal, humid}
#
# Cached on first call so we don't recompute every game.

_TIER_CENTERS = {"cold": 52, "mild": 66, "warm": 78, "hot": 90}
_TIER_SIGMA   = 8.5
_IN_SEASON_MONTHS = [(4, "apr"), (5, "may"), (6, "jun"),
                     (7, "jul"), (8, "aug"), (9, "sep")]

_table_cache: dict[str, dict] = {}


def _temp_weights(target_hi: float) -> dict[str, int]:
    """Gaussian-shaped weight over the four temperature tiers around the
    target high. Returns integer weights summing to ~10."""
    import math
    raw = {}
    for tier, c in _TIER_CENTERS.items():
        d = target_hi - c
        raw[tier] = math.exp(-d * d / (2 * _TIER_SIGMA * _TIER_SIGMA))
    total = sum(raw.values()) or 1.0
    norm = {t: w / total for t, w in raw.items()}
    # Scale to integer weights with min 0 (drop very-low tiers) but at
    # least one non-zero tier guaranteed by the dominant.
    out = {t: round(w * 10) for t, w in norm.items()}
    if all(v == 0 for v in out.values()):
        dom = max(norm, key=norm.get)
        out[dom] = 1
    return {t: v for t, v in out.items() if v > 0}


def _precip_weights(monthly_in: float) -> dict[str, int]:
    """Tier weights for precipitation. Calibrated against the codebase's
    existing archetype tables (continental_cold sits ~14/5/1 in-season)
    so the Zaryan profiles slot in alongside without re-tuning the
    engine modifiers."""
    if monthly_in < 0.8:
        return {"none": 18, "light": 1, "heavy": 1}
    if monthly_in < 1.6:
        return {"none": 16, "light": 3, "heavy": 1}
    if monthly_in < 2.6:
        return {"none": 14, "light": 5, "heavy": 1}
    if monthly_in < 3.8:
        return {"none": 12, "light": 6, "heavy": 2}
    if monthly_in < 5.0:
        return {"none": 10, "light": 7, "heavy": 3}
    return {"none": 8, "light": 8, "heavy": 4}


def _humid_weights(monthly_in: float, target_hi: float) -> dict[str, int]:
    """Humidity tier weights derived from precip + temperature. The
    Zaryan summers are Pacific-monsoon-influenced (humid in Jul-Sep,
    drier in spring) so we couple humidity to monthly precip."""
    if monthly_in < 1.5:
        return {"dry": 3, "normal": 5, "humid": 2}
    if monthly_in < 3.5:
        return {"dry": 2, "normal": 6, "humid": 2} if target_hi < 70 else {"dry": 1, "normal": 6, "humid": 3}
    return {"dry": 1, "normal": 5, "humid": 4}


def tier_table_for_profile(profile_key: str) -> dict | None:
    """Per-month tier-weight dict (apr→sep) for the named profile, in the
    same shape `weather.py:_TABLES[archetype][month_key]` returns."""
    if profile_key in _table_cache:
        return _table_cache[profile_key]
    prof = ZARYAN_CLIMATE_PROFILES.get(profile_key)
    if not prof:
        return None
    out: dict[str, dict] = {}
    for month_idx, key in _IN_SEASON_MONTHS:
        hi  = prof["hi_F"][month_idx - 1]
        pre = prof["precip_in"][month_idx - 1]
        out[key] = {
            "temperature": _temp_weights(hi),
            "humidity":    _humid_weights(pre, hi),
            "precip":      _precip_weights(pre),
        }
    _table_cache[profile_key] = out
    return out


def tier_table_for_city(city: str) -> dict | None:
    """Per-month tier-weight dict for `city`, or None if the city isn't
    in the Zaryan profile map."""
    prof_key = ZARYAN_CITY_TO_PROFILE.get(city)
    return tier_table_for_profile(prof_key) if prof_key else None
