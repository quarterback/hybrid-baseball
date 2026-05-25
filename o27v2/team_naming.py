"""
Locale-aware team-identity generator for peer-universe leagues.

Standard configs draw club identities from `teams_database.json` (real
MLB/MiLB franchises). That's wrong for the O27 global universe, where an
"African Federation" club should not be the "Dodgers". This module builds
region-appropriate identities from the hand-authored naming data in
`data/names/` and is wired into `seed_league` for configs that set
`"team_naming": "generated"`.

Five naming categories, mirroring how clubs are really named around the
world (see the data files for the full rationale + sources):

  1. corporate      — "{Corp} {Mascot}"  (NPB/KBO: Samsung Lions)
  2. small_business — "{City} {Modifier?} {Business}{Suffix?}"  (most common)
  3. traditional    — "{City} {Mascot}"   (North-American holdout)
  4. authority      — "{City} {Authority}" (Indian Railways, Mumbai Police)
  5. baseball_club  — "{City} {locale-spelled Baseball Club}" (European civic)

The African Federation is special-cased to its real-club roster
(`african_federation_teams.json`).

Per-league category targets come straight from each data file's
`regional_distribution` block; the corporate count is the residual after
the four explicit categories. Everything is driven by a seeded RNG so the
same (rng_seed, config) reproduces the same universe.
"""
from __future__ import annotations

import json
import os
import random
import zlib

_NAMES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "names")

_cache: dict[str, dict] = {}


def _load(fname: str) -> dict:
    if fname not in _cache:
        with open(os.path.join(_NAMES_DIR, fname), encoding="utf-8") as fh:
            _cache[fname] = json.load(fh)
    return _cache[fname]


# League display name -> (region_key used by the data files, league_key used
# by the regional_distribution blocks). Leagues absent from this map fall
# back to the default (teams_database) identities.
_LEAGUE_MAP: dict[str, tuple[str, str]] = {
    "Americas Premier League":             ("americas",     "americas_premier"),
    "Rising Sun League":                   ("east_asia",    "rising_sun"),
    "Europa League":                       ("europe",       "europa"),
    "Pacifica League":                     ("pacifica",     "pacifica"),
    "Indo-Malay League":                   ("indo_malay",   "indo_malay"),
    "Subcontinental & Middle East League": ("subcontinent", "subcontinent"),
    "African Federation":                  ("africa",       "african_federation"),
}

# region_key -> the city_to_locale sub-entry prefixes that belong to it.
_REGION_CITY_PREFIXES: dict[str, tuple[str, ...]] = {
    "americas":     ("americas_",),
    "east_asia":    ("east_asia_",),
    "europe":       ("europa_",),
    "pacifica":     ("pacifica_",),
    "indo_malay":   ("indo_malay_",),
    "subcontinent": ("subcontinent_", "middle_east_"),
    "africa":       ("africa_",),
}

# A custom universe league carries a `locale` (a region/preset id from
# data/names/regions.json) instead of one of the seven canonical league
# names. Map those locale ids onto the team_naming region_keys above so any
# user-named league still gets region-appropriate generated identities rather
# than falling back to real MLB/MiLB franchises. Unmapped locales (and the
# blended presets like "global") resolve to None -> a worldwide city pool.
_LOCALE_TO_REGION: dict[str, str] = {
    # Americas
    "us": "americas", "canada": "americas", "latin_america": "americas",
    "south_america": "americas", "caribbean_dutch": "americas",
    "caribbean_cricket": "americas", "haiti": "americas", "curacao": "americas",
    "aruba": "americas", "suriname": "americas", "guyana": "americas",
    "americas_pro": "americas", "us_only": "americas",
    # East Asia
    "east_asia": "east_asia", "asian_pro": "east_asia",
    # Europe
    "europe_western": "europe", "europe_eastern": "europe",
    "europe_southeast": "europe", "british_isles": "europe",
    "netherlands": "europe", "italy": "europe", "israel": "europe",
    "nordic": "europe", "finland": "europe", "sweden": "europe",
    "norway": "europe", "denmark": "europe", "european": "europe",
    # Pacifica
    "anzac": "pacifica", "pacific_islands": "pacifica", "guam": "pacifica",
    # Indo-Malay / Southeast Asia
    "malaysia": "indo_malay", "southeast_asia": "indo_malay",
    "philippines": "indo_malay", "indonesia": "indo_malay", "thailand": "indo_malay",
    # Subcontinent & Middle East
    "south_asia": "subcontinent", "afghan_central_asia": "subcontinent",
    "central_west_asia": "subcontinent", "gulf_cricket": "subcontinent",
    # Africa
    "africa": "africa", "africa_cricket": "africa",
}

# locale (from city_to_locale) -> the language prefix used by the
# regional_flavor "<lang>_suffix_options" keys in business_names.json.
_LOCALE_TO_SUFFIX_LANG = {
    "english": "english", "spanish": "spanish", "portuguese": "portuguese",
    "french": "french", "german": "german", "dutch": "dutch",
    "italian": "italian", "nordic": "nordic",
}


def supports(league_name: str) -> bool:
    """True if this league has a generated-identity locale mapping."""
    return league_name in _LEAGUE_MAP


# ---------------------------------------------------------------------------
# City pool
# ---------------------------------------------------------------------------

def _city_pool(region_key: str | None) -> list[tuple[str, str]]:
    """All (city, locale) pairs available to a region, from team_naming's
    city_to_locale exemplar lists. A None/"global" region_key draws from
    every region (used for blended-preset or locale-less custom leagues)."""
    c2l = _load("team_naming.json")["category_5_baseball_club"]["city_to_locale"]
    if region_key in (None, "global"):
        prefixes = tuple(p for ps in _REGION_CITY_PREFIXES.values() for p in ps)
    else:
        prefixes = _REGION_CITY_PREFIXES.get(region_key, ())
    out: list[tuple[str, str]] = []
    for key, entry in c2l.items():
        if key.startswith("_"):
            continue
        if not key.startswith(prefixes):
            continue
        locale = entry.get("locale", "english")
        for city in entry.get("cities_example", []):
            out.append((city, locale))
    return out


# ---------------------------------------------------------------------------
# Per-category name builders. Each returns a club name string.
# ---------------------------------------------------------------------------

def _mascot(region_key: str, rng: random.Random) -> str:
    pool = _load("team_naming.json")["category_3_traditional_mascots"]["mascot_pool"]
    buckets: list[str] = []
    buckets += pool["regional_animals"].get(region_key, [])
    buckets += pool["predators"]
    buckets += pool["weather_and_natural"]
    buckets += pool["mythical_and_heritage"]
    buckets += pool["prey_and_hardy"]
    buckets += pool["industrial_civic"]
    return rng.choice(buckets)


def _name_traditional(city: str, locale: str, region_key: str, rng: random.Random) -> str:
    return f"{city} {_mascot(region_key, rng)}"


def _name_baseball_club(city: str, locale: str, region_key: str, rng: random.Random) -> str:
    spellings = _load("team_naming.json")["category_5_baseball_club"]["locale_spellings"]
    opts = spellings.get(locale) or spellings["english"]
    # Skip pure-script glyph spellings (last entries) most of the time so the
    # roster stays readable; keep the romanised ones.
    romanised = [s for s in opts if s.isascii()] or opts
    return f"{city} {rng.choice(romanised)}"


def _name_authority(city: str, locale: str, region_key: str, rng: random.Random) -> str:
    types = _load("public_authorities.json")["authority_types"]
    eligible: list[str] = []
    for group in types.values():
        if not isinstance(group, list):
            continue
        for ent in group:
            if region_key in ent.get("regions", []):
                eligible.append(ent["stem"])
    stem = rng.choice(eligible) if eligible else "Police"
    return f"{city} {stem}"


def _name_corporate(city: str, locale: str, region_key: str, rng: random.Random) -> str:
    corp = _load("corporations.json").get(region_key, {})
    pool = [c["name"] for c in corp.get("extant_majors", [])]
    pool += [c["name"] for c in corp.get("defunct", [])]
    if not pool:
        return _name_traditional(city, locale, region_key, rng)
    return f"{rng.choice(pool)} {_mascot(region_key, rng)}"


def _name_business(city: str, locale: str, region_key: str, rng: random.Random) -> str:
    biz = _load("business_names.json")
    flavor = biz["regional_flavor"].get(region_key, {})
    parts = [city]
    # ~40% prepend a modifier.
    if rng.random() < 0.40:
        parts.append(rng.choice(biz["modifiers_optional"]))
    # 70% global business type, 30% the region's preferred extras.
    extras = flavor.get("preferred_business_types_extra") or []
    if extras and rng.random() < 0.30:
        btype = rng.choice(extras)
    else:
        btype = rng.choice(biz["business_types_global"])
    name = " ".join(parts) + " " + btype
    # ~50% append a locale-appropriate suffix.
    if rng.random() < 0.50:
        lang = _LOCALE_TO_SUFFIX_LANG.get(locale, "english")
        suffixes = flavor.get(f"{lang}_suffix_options")
        if not suffixes:
            # Any available suffix list for the region, else none.
            for k, v in flavor.items():
                if k.endswith("_suffix_options") and v:
                    suffixes = v
                    break
        if suffixes:
            name += rng.choice(suffixes)
    return name.strip()


_CATEGORY_BUILDERS = {
    "corporate":      _name_corporate,
    "small_business": _name_business,
    "traditional":    _name_traditional,
    "authority":      _name_authority,
    "baseball_club":  _name_baseball_club,
}


# ---------------------------------------------------------------------------
# Category allocation
# ---------------------------------------------------------------------------

# The data files zero out traditional / baseball-club / authority counts for
# the African Federation because they were authored around a real-club roster.
# Now that the league is generated like the others, give it a spread that fits
# African club-naming culture (evocative/animal civic identities) rather than
# defaulting almost entirely to corporate. Corporate remains the residual.
_AFRICAN_FED_TARGETS = {
    "target_traditional":    3,   # Lions, Leopards, Cheetahs — evocative animals
    "target_category_5":     3,   # civic "Baseball Club" (Sundowns-style)
    "target_small_business": 2,
    "target_authorities":    1,
}


def _default_targets(n_teams: int) -> list[str]:
    """A balanced category spread for custom leagues that have no authored
    regional_distribution block. Mirrors the global mix of club-naming
    cultures so a user-named league gets variety instead of all-corporate."""
    trad = round(n_teams * 0.30)
    club = round(n_teams * 0.25)
    biz  = round(n_teams * 0.25)
    auth = round(n_teams * 0.10)
    cats = (["traditional"] * trad + ["baseball_club"] * club
            + ["small_business"] * biz + ["authority"] * auth)
    cats = cats[:n_teams]
    cats += ["corporate"] * (n_teams - len(cats))  # corporate is the residual
    return cats


def _targets(league_key: str | None, n_teams: int) -> list[str]:
    """Return a per-team category list of length n_teams, honoring each data
    file's regional_distribution and filling the remainder with corporate."""
    if league_key is None:
        return _default_targets(n_teams)
    if league_key == "african_federation":
        trad = _AFRICAN_FED_TARGETS["target_traditional"]
        club = _AFRICAN_FED_TARGETS["target_category_5"]
        biz = _AFRICAN_FED_TARGETS["target_small_business"]
        auth = _AFRICAN_FED_TARGETS["target_authorities"]
        cats = (["baseball_club"] * club + ["small_business"] * biz
                + ["traditional"] * trad + ["authority"] * auth)
        cats = cats[:n_teams]
        cats += ["corporate"] * (n_teams - len(cats))
        return cats

    tn = _load("team_naming.json")
    trad = (tn["category_3_traditional_mascots"]["regional_distribution"]
            .get(league_key, {}).get("target_traditional", 0))
    club = (tn["category_5_baseball_club"]["regional_distribution"]
            .get(league_key, {}).get("target_category_5", 0))
    biz = (_load("business_names.json")["regional_distribution"]
           .get(league_key, {}).get("target_small_business", 0))
    auth = (_load("public_authorities.json")["regional_distribution"]
            .get(league_key, {}).get("target_authorities", 0))

    cats: list[str] = (["baseball_club"] * club + ["small_business"] * biz
                       + ["traditional"] * trad + ["authority"] * auth)
    cats = cats[:n_teams]                       # never exceed the roster
    cats += ["corporate"] * (n_teams - len(cats))  # corporate is the residual
    return cats


# ---------------------------------------------------------------------------
# Abbreviations
# ---------------------------------------------------------------------------

def _make_abbrev(name: str, used: set[str]) -> str:
    words = [w for w in name.replace("&", " ").replace(".", " ").split() if w]
    base = "".join(w[0] for w in words[:3]).upper()
    if len(base) < 2 and words:
        base = words[0][:3].upper()
    base = (base or "TBD")[:4]
    cand = base
    i = 1
    while cand in used or len(cand) < 2:
        suffix = str(i)
        cand = (base[: max(1, 3 - len(suffix))] + suffix).upper()
        i += 1
    used.add(cand)
    return cand


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_league_teams(league_name: str, n_teams: int, rng_seed: int,
                          used_abbrev: set[str] | None = None,
                          locale: str | None = None) -> list[dict]:
    """Return n_teams identity dicts ({name, city, abbrev}) for a league.

    Deterministic for a given (league_name, locale, rng_seed). The region is
    resolved from the canonical league name first, then from `locale` (a
    region/preset id from data/names/regions.json) for custom universes. A
    locale-less, non-canonical league resolves to a worldwide pool so it still
    gets generated identities rather than falling back to MLB/MiLB franchises.
    Returns [] only when there is no locale data at all to draw from.
    Pass a shared `used_abbrev` set to keep abbreviations unique across an
    entire universe of leagues.
    """
    if league_name in _LEAGUE_MAP:
        region_key, league_key = _LEAGUE_MAP[league_name]
    else:
        region_key = _LOCALE_TO_REGION.get((locale or "").strip())
        league_key = None  # no authored distribution -> balanced default

    seed = (rng_seed ^ zlib.crc32(league_name.encode())
            ^ zlib.crc32((locale or "").encode())) & 0x7FFFFFFF
    rng = random.Random(seed)
    if used_abbrev is None:
        used_abbrev = set()

    cities = _city_pool(region_key)
    rng.shuffle(cities)
    cats = _targets(league_key, n_teams)
    rng.shuffle(cats)

    used_names: set[str] = set()
    out = []
    ci = 0
    for cat in cats:
        # Each team gets a unique city (for the city field + weather), even
        # categories whose name doesn't surface it (corporate).
        city, locale = cities[ci % len(cities)] if cities else ("", "english")
        ci += 1
        builder = _CATEGORY_BUILDERS[cat]
        name = builder(city, locale, region_key, rng)
        # Guard against an accidental duplicate club name within the league.
        tries = 0
        while name in used_names and tries < 8:
            city, locale = cities[ci % len(cities)] if cities else ("", "english")
            ci += 1
            name = builder(city, locale, region_key, rng)
            tries += 1
        used_names.add(name)
        out.append({
            "name": name,
            "city": city,
            "abbrev": _make_abbrev(name, used_abbrev),
        })
    return out
