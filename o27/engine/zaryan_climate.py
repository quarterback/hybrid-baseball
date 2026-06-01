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
# Coordinates
# ---------------------------------------------------------------------------
# Real-world coordinates for the Russian Far East territory each city is
# anchored to (decimal degrees). Provideniya sits east of the 180° meridian
# in the WESTERN hemisphere, so its longitude is negative. These feed the
# weather city gazetteer (archetype + nearest-anchor lookup), sunset-based
# low-light, and the team-coordinate backfill — so a Zaryan club resolves
# exactly like a real-world one.

ZARYAN_CITY_COORDS: dict[str, tuple[float, float]] = {
    # Seaward (UTC+9)
    "Garrison":          (43.12, 131.89), "Gannibal":      (43.70, 132.50),
    "New Eldorado":      (42.82, 132.89), "Vostok Harbor": (42.90, 132.34),
    "Ussuri":            (43.80, 131.95), "Partizansk":    (43.13, 133.13),
    "Slavyan":           (42.86, 131.38),
    # Stratton (UTC+10)
    "Stratton":          (48.79, 132.92), "New Philadelphia": (48.60, 132.20),
    "Cummings":          (49.10, 131.50),
    # Lower Amur (UTC+10)
    "Amargrad":          (48.48, 135.07), "Komsa":         (50.55, 137.01),
    "Amursk":            (50.23, 136.90), "Nikol":         (53.15, 140.73),
    # Upper Amur (UTC+9)
    "Verkhnegrad":       (50.29, 127.54), "Svobodny":      (51.38, 128.13),
    "Tynda":             (55.15, 124.72),
    # Zolotoy / Magadan (UTC+11)
    "Magadan City":      (59.56, 150.80), "Susuman":       (62.78, 148.15),
    "Ola":               (59.58, 151.29),
    # Sakhalin (UTC+10)
    "Neftezma":          (46.96, 142.74), "Korsa":         (46.63, 142.78),
    "Okha":              (53.59, 142.95), "Nogliki":       (51.83, 143.17),
    # Kamchatka (UTC+12)
    "Vulkangrad":        (53.02, 158.65), "Klyuch":        (56.31, 160.84),
    "Yelizovo":          (53.18, 158.38),
    # Chukotka (UTC+12 / +13) — Provideniya crosses 180° → WEST longitude
    "Anadyr":            (64.73, 177.51), "Provideniya":   (64.42, -173.23),
}


# Secondary (farm / B-league) and tertiary (rookie / semi-pro) towns —
# minor markets and random-draw hometowns across the eight oblasts. Each
# carries its own coordinates and UTC offset (several Chukotka points sit
# in the WESTERN hemisphere, so a longitude rule would mis-zone them) and
# inherits its oblast's climate profile. One source table, folded into the
# three lookups below so every Zaryan place resolves like the primaries.
# Fields: (name, lat, lon, utc_offset, profile_key).
_ZARYAN_MINOR_TOWNS: list[tuple[str, float, float, int, str]] = [
    # Seaward → Garrison profile (UTC+9)
    ("Garsun-Posad",       43.05, 131.95,  9, "seaward_garrison"),
    ("Yeldrado-Pristan",   42.79, 132.91,  9, "seaward_garrison"),
    ("Kitavoda",           43.40, 131.60,  9, "seaward_garrison"),
    ("Svetlobor",          43.95, 132.40,  9, "seaward_garrison"),
    ("Frivuda",            43.55, 132.70,  9, "seaward_garrison"),
    ("Primore",            43.30, 132.10,  9, "seaward_garrison"),
    ("Slavyan-Torg",       42.88, 131.42,  9, "seaward_garrison"),
    ("Olympika",           43.20, 132.55,  9, "seaward_garrison"),
    ("Belomore",           43.60, 131.50,  9, "seaward_garrison"),
    ("Sosnovka",           43.85, 131.80,  9, "seaward_garrison"),
    ("Brookston",          43.00, 132.30,  9, "seaward_garrison"),
    # Stratton founding core → Stratton profile (UTC+10)
    ("Stratton-Sloboda",   48.85, 132.98, 10, "heartland_stratton"),
    ("Cumzion",            49.05, 131.45, 10, "heartland_stratton"),
    ("Phildelya",          48.55, 132.10, 10, "heartland_stratton"),
    ("Goodman",            48.70, 132.50, 10, "heartland_stratton"),
    ("Novy-Heard",         48.40, 133.20, 10, "heartland_stratton"),
    ("Amur-Zaimka",        48.95, 133.40, 10, "heartland_stratton"),
    # Lower / Upper Amur heartland → Amargrad profile (+10 Lower, +9 Upper)
    ("Amargrad-Pristan",   48.52, 135.12, 10, "heartland_amargrad"),
    ("Nizhne-Komsa",       50.45, 137.10, 10, "heartland_amargrad"),
    ("Lesozma",            50.10, 136.50, 10, "heartland_amargrad"),
    ("Verkhne-Klyuch",     49.80, 135.80, 10, "heartland_amargrad"),
    ("Ryboreche",          53.05, 140.60, 10, "heartland_amargrad"),
    ("Kitay-Torg",         50.25, 127.60,  9, "heartland_amargrad"),
    ("Zeyagrad",           51.10, 128.80,  9, "heartland_amargrad"),
    ("Svobodny-Yar",       51.40, 128.20,  9, "heartland_amargrad"),
    ("Ust-Tynda",          55.20, 124.80,  9, "heartland_amargrad"),
    ("Bureya",             49.80, 129.85,  9, "heartland_amargrad"),
    # Zolotoy gold north → Magadan profile (UTC+11)
    ("Zolotoy-Prival",     59.70, 150.50, 11, "zolotoy_magadan"),
    ("Amargol",            60.20, 151.40, 11, "zolotoy_magadan"),
    ("Goldzma",            61.50, 149.20, 11, "zolotoy_magadan"),
    ("Susuman-Ridge",      62.85, 148.30, 11, "zolotoy_magadan"),
    ("Olskaya",            59.62, 151.35, 11, "zolotoy_magadan"),
    ("Yagodnoye",          62.55, 149.62, 11, "zolotoy_magadan"),
    # Sakhalin oil island → Neftezma profile (UTC+10)
    ("Nyefta",             47.05, 142.80, 10, "sakhalin_neftezma"),
    ("Pilvo",              53.20, 142.50, 10, "sakhalin_neftezma"),
    ("Tymsk",              51.20, 143.10, 10, "sakhalin_neftezma"),
    ("Korsa-Gavan",        46.60, 142.82, 10, "sakhalin_neftezma"),
    ("Aleksandrovsk",      50.90, 142.16, 10, "sakhalin_neftezma"),
    # Kamchatka volcanic → Vulkangrad profile (UTC+12)
    ("Vulkangrad-Verkhne", 53.10, 158.70, 12, "kamchatka_vulkangrad"),
    ("Klyuch-Geyzer",      56.20, 160.70, 12, "kamchatka_vulkangrad"),
    ("Palanka",            59.10, 159.95, 12, "kamchatka_vulkangrad"),
    ("Tigilsk",            57.80, 158.65, 12, "kamchatka_vulkangrad"),
    ("Itelka",             54.50, 161.20, 12, "kamchatka_vulkangrad"),
    ("Ust-Kamchatsk",      56.25, 162.50, 12, "kamchatka_vulkangrad"),
    # Chukotka Arctic frontier → Anadyr profile (UTC+12 / +13; some WEST lon)
    ("Chaungrad",          68.80, 170.60, 12, "chukotka_anadyr"),
    ("Anakar",             64.70, 177.60, 12, "chukotka_anadyr"),
    ("Ledport",            69.70, 170.30, 12, "chukotka_anadyr"),
    ("Egvekinot",          66.34, 179.13, 12, "chukotka_anadyr"),
    ("Uelka",              66.16, -169.80, 13, "chukotka_anadyr"),
    ("Provideniya-Reid",   64.42, -173.25, 13, "chukotka_anadyr"),
    # --- Expansion: fur-trapping outposts, mining settlements, fishing redoubts ---
    # Seaward (UTC+9)
    ("Zarya-Vanguard",     43.25, 131.90,  9, "seaward_garrison"),
    ("Possyet-Gate",       42.67, 130.81,  9, "seaward_garrison"),
    ("Millstone-Creek",    43.65, 132.12,  9, "seaward_garrison"),
    ("Shkotova",           43.32, 132.35,  9, "seaward_garrison"),
    # Stratton founding core (UTC+10, except the Upper-Amur bluff at +9)
    ("Stratton-North",     48.92, 132.88, 10, "heartland_stratton"),
    ("Bidzhan-Trail",      48.22, 131.98, 10, "heartland_stratton"),
    ("Poyarkovo",          49.42, 129.43,  9, "heartland_stratton"),
    # Lower Amur (UTC+10)
    ("Komsomol-Vostok",    50.60, 136.95, 10, "heartland_amargrad"),
    ("Gorin-Siding",       50.75, 136.65, 10, "heartland_amargrad"),
    ("Bogorodskoye",       52.37, 140.43, 10, "heartland_amargrad"),
    # Upper Amur (UTC+9)
    ("Zeya-Dam",           53.75, 127.25,  9, "heartland_amargrad"),
    ("Skovorodino",        53.98, 123.93,  9, "heartland_amargrad"),
    ("Albazin-Redoubt",    53.38, 124.08,  9, "heartland_amargrad"),
    # Zolotoy gold north (UTC+11)
    ("Magadan-Vostochny",  59.55, 150.92, 11, "zolotoy_magadan"),
    ("Talaya-Spas",        61.13, 152.39, 11, "zolotoy_magadan"),
    ("Atka-Pass",          60.83, 151.78, 11, "zolotoy_magadan"),
    # Sakhalin oil island (UTC+10)
    ("Kholmsk-Strait",     47.05, 142.04, 10, "sakhalin_neftezma"),
    ("Smirnykh-Line",      49.75, 142.84, 10, "sakhalin_neftezma"),
    ("Poronaysk",          49.23, 143.11, 10, "sakhalin_neftezma"),
    # Kamchatka volcanic (UTC+12)
    ("Avacha-Sloboda",     53.05, 158.55, 12, "kamchatka_vulkangrad"),
    ("Milkovo-Plain",      54.68, 158.62, 12, "kamchatka_vulkangrad"),
    ("Ossora",             59.25, 163.07, 12, "kamchatka_vulkangrad"),
    # Chukotka Arctic frontier (UTC+12 / +13; Lavrentiya is WEST longitude)
    ("Pevek-Anchorage",    69.70, 170.31, 12, "chukotka_anadyr"),
    ("Lavrentiya",         65.58, -171.00, 13, "chukotka_anadyr"),
    ("Bilibino-Atom",      68.05, 166.45, 12, "chukotka_anadyr"),
]

# Fold the minor towns into the lookup tables. setdefault keeps the
# hand-authored primary entries authoritative (e.g. Nogliki, which also
# appears as a tertiary fallback in the source roster).
for _zn, _zlat, _zlon, _zoff, _zprof in _ZARYAN_MINOR_TOWNS:
    ZARYAN_CITY_COORDS.setdefault(_zn, (_zlat, _zlon))
    ZARYAN_CITY_UTC_OFFSET.setdefault(_zn, _zoff)
    ZARYAN_CITY_TO_PROFILE.setdefault(_zn, _zprof)


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


def coords(city: str) -> tuple[float, float] | None:
    """(lat, lon) for a Zaryan `city`, or None if unknown."""
    return ZARYAN_CITY_COORDS.get(city)


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
