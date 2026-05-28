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
# data/names/regions.json) instead of one of the seven canonical league names.
# Map each locale onto the PRECISE city_to_locale sub-pool keys it should draw
# from — finer than region_key so a Latin-American league never gets US/Canada
# cities, a Nordic league only gets Nordic cities, etc. The coarse region_key
# (which drives corporate sponsors, mascots and civic authorities, all of which
# only have region-level data) is derived from these via _region_for_city_keys.
# Unmapped locales (and blended presets like "global") fall through to a
# worldwide pool so any user-named league still gets generated identities
# instead of real MLB/MiLB franchises.
_LOCALE_TO_CITY_KEYS: dict[str, tuple[str, ...]] = {
    # --- Americas ---
    "us":                ("americas_anglophone",),
    "us_only":           ("americas_anglophone",),
    "canada":            ("americas_anglophone", "americas_canada_french"),
    "latin_america":     ("americas_spanish", "americas_mexican", "americas_cuban",
                          "americas_dominican", "americas_venezuelan",
                          "americas_portuguese"),
    "south_america":     ("americas_spanish", "americas_venezuelan",
                          "americas_portuguese"),
    "brazil":            ("americas_portuguese",),
    "mexico":            ("americas_mexican",),
    "cuba":              ("americas_cuban",),
    "dominican":         ("americas_dominican",),
    "venezuela":         ("americas_venezuelan",),
    "bermuda":           ("americas_bermuda",),
    "caribbean_dutch":   ("americas_spanish",),
    "caribbean_cricket": ("americas_spanish",),
    "haiti":             ("americas_spanish",),
    "curacao":           ("americas_spanish",),
    "aruba":             ("americas_spanish",),
    "suriname":          ("americas_portuguese",),
    "guyana":            ("americas_anglophone",),
    "barbados":          ("americas_barbados",),
    "bahamas":           ("americas_bahamas",),
    "americas_pro":      ("americas_anglophone", "americas_spanish",
                          "americas_mexican", "americas_cuban",
                          "americas_dominican", "americas_venezuelan",
                          "americas_portuguese", "americas_canada_french"),
    # --- East Asia ---
    "east_asia":         ("east_asia_japan", "east_asia_korea", "east_asia_china",
                          "east_asia_hongkong"),
    "asian_pro":         ("east_asia_japan", "east_asia_korea", "east_asia_china"),
    "hong_kong":         ("east_asia_hongkong",),
    # --- Europe ---
    "british_isles":     ("europa_english", "europa_scottish"),
    "scotland":          ("europa_scottish",),
    "europe_western":    ("europa_english", "europa_german", "europa_dutch",
                          "europa_french", "europa_italian", "europa_spanish"),
    "germany":           ("europa_german",),
    "austria":           ("europa_austrian",),
    "europe_eastern":    ("europa_polish", "europa_slavic", "europa_czech",
                          "europa_slovak", "europa_hungarian",
                          "europa_russian", "europa_ukrainian"),
    "czechia":           ("europa_czech",),
    "slovakia":          ("europa_slovak",),
    "hungary":           ("europa_hungarian",),
    "europe_southeast":  ("europa_slavic", "europa_italian", "europa_greek",
                          "europa_croatian", "europa_slovenian"),
    "greece":            ("europa_greek",),
    "croatia":           ("europa_croatian",),
    "slovenia":          ("europa_slovenian",),
    "san_marino":        ("europa_sanmarino",),
    "switzerland":       ("europa_swiss_de", "europa_swiss_fr", "europa_swiss_it"),
    "lithuania":         ("europa_lithuanian",),
    "russia":            ("europa_russian",),
    "ukraine":           ("europa_ukrainian",),
    "netherlands":       ("europa_dutch",),
    "italy":             ("europa_italian",),
    "israel":            ("europa_english",),   # no Hebrew city pool -> civic English
    "nordic":            ("europa_nordic",),
    "finland":           ("europa_nordic",),
    "sweden":            ("europa_nordic",),
    "norway":            ("europa_nordic",),
    "denmark":           ("europa_nordic",),
    "european":          ("europa_english", "europa_german", "europa_austrian",
                          "europa_dutch", "europa_french", "europa_italian",
                          "europa_spanish", "europa_nordic", "europa_polish",
                          "europa_slavic", "europa_croatian", "europa_slovenian",
                          "europa_slovak", "europa_hungarian", "europa_czech",
                          "europa_swiss_de", "europa_swiss_fr", "europa_swiss_it",
                          "europa_lithuanian"),
    # --- Pacifica ---
    "anzac":             ("pacifica_australia",),
    "pacific_islands":   ("pacifica_pacific",),
    "guam":              ("pacifica_pacific",),
    "philippines":       ("pacifica_philippines",),
    # --- Indo-Malay / Southeast Asia ---
    "malaysia":          ("indo_malay_malay",),
    "indonesia":         ("indo_malay_indonesian",),
    "thailand":          ("indo_malay_other",),
    "southeast_asia":    ("indo_malay_malay", "indo_malay_indonesian", "indo_malay_other"),
    # --- Subcontinent & Middle East ---
    "south_asia":        ("subcontinent_india", "subcontinent_pakistan",
                          "subcontinent_other"),
    "afghan_central_asia": ("subcontinent_pakistan", "middle_east_persian"),
    "central_west_asia": ("middle_east_turkish", "middle_east_persian",
                          "central_asia_kazakh"),
    "turkey":            ("middle_east_turkish",),
    "iran":              ("middle_east_persian",),
    "kazakhstan":        ("central_asia_kazakh",),
    "gulf_cricket":      ("middle_east_arabic",),
    # --- Africa ---
    "africa":            ("africa_english", "africa_swahili", "africa_afrikaans",
                          "africa_french", "africa_amharic", "africa_namibia"),
    "africa_cricket":    ("africa_english", "africa_namibia"),
    "namibia":           ("africa_namibia",),
    "cape_verde":        ("africa_capeverde",),
    "mauritius":         ("africa_mauritius",),
}


def _region_for_city_keys(city_keys: tuple[str, ...]) -> str | None:
    """The coarse region_key that owns the given city sub-pool keys (used for
    corporate sponsors / mascots / authorities). Derived from the city-key
    prefix; all keys in a locale map to one region by construction."""
    for ck in city_keys:
        for region, prefixes in _REGION_CITY_PREFIXES.items():
            if ck.startswith(prefixes):
                return region
    return None


def _resolve_locale_city_keys(locale) -> tuple[tuple[str, ...] | None, str | None]:
    """Resolve a `locale` to (city_keys, dominant region_key) for generated
    team identities. `locale` is either a region/preset id string OR a weighted
    {region_id: weight} blend dict. A blend draws cities from the UNION of all
    its regions' sub-pools (so a mixed-origin league gets mixed-but-regional
    cities, and therefore regional weather), with the highest-weight region
    driving the coarse naming flavor. Returns (None, None) when nothing
    resolves, so the caller falls back to the worldwide pool."""
    if isinstance(locale, dict):
        keys: list[str] = []
        for rid in sorted(locale, key=lambda k: -float(locale.get(k) or 0.0)):
            keys.extend(_LOCALE_TO_CITY_KEYS.get(rid, ()))
        if not keys:
            return None, None
        seen: set[str] = set()
        ck = tuple(k for k in keys if not (k in seen or seen.add(k)))
        return ck, _region_for_city_keys(ck)
    ck = _LOCALE_TO_CITY_KEYS.get((locale or "").strip())
    return ck, (_region_for_city_keys(ck) if ck else None)

# locale (from city_to_locale) -> the language prefix used by the
# regional_flavor "<lang>_suffix_options" keys in business_names.json.
_LOCALE_TO_SUFFIX_LANG = {
    "english": "english", "spanish": "spanish", "portuguese": "portuguese",
    "french": "french", "german": "german", "dutch": "dutch",
    "italian": "italian", "nordic": "nordic",
    "japanese": "japanese", "korean": "korean", "chinese": "chinese",
    "tagalog": "tagalog", "malay": "malay", "indonesian": "indonesian",
    "hindi": "hindi", "urdu": "urdu", "arabic": "arabic", "turkish": "turkish",
    "persian": "persian", "swahili": "swahili", "afrikaans": "afrikaans",
    "amharic": "amharic",
}


def supports(league_name: str) -> bool:
    """True if this league has a generated-identity locale mapping."""
    return league_name in _LEAGUE_MAP


# ---------------------------------------------------------------------------
# City pool
# ---------------------------------------------------------------------------

def _city_pool(region_key: str | None,
               city_keys: tuple[str, ...] | None = None) -> list[tuple[str, str]]:
    """All (city, locale) pairs to draw from, out of team_naming's
    city_to_locale exemplar lists.

      * city_keys given  -> EXACTLY those sub-pools (precise locale tightening).
      * region_key set   -> every sub-pool whose key matches the region prefix.
      * None/"global"    -> every region (blended-preset or locale-less leagues).
    """
    c2l = _load("team_naming.json")["category_5_baseball_club"]["city_to_locale"]
    out: list[tuple[str, str]] = []
    for key, entry in c2l.items():
        if key.startswith("_"):
            continue
        if city_keys is not None:
            if key not in city_keys:
                continue
        elif region_key in (None, "global"):
            pass
        elif not key.startswith(_REGION_CITY_PREFIXES.get(region_key, ())):
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
    lang = _LOCALE_TO_SUFFIX_LANG.get(locale, "english")
    parts = [city]
    # ~40% prepend a modifier.
    if rng.random() < 0.40:
        parts.append(rng.choice(biz["modifiers_optional"]))
    # 70% global business type, 30% the region's flavor extras, keyed to the
    # city's own language so the type never crosses locales (a Portuguese city
    # draws no Spanish "Heladería", a Korean city no Japanese "Ramen House").
    # A locale with no list uses the global types only. preferred_* is a
    # legacy region-wide fallback, retained for safety but unused by shipped data.
    extras = (flavor.get(f"{lang}_business_types_extra")
              or flavor.get("preferred_business_types_extra") or [])
    if extras and rng.random() < 0.30:
        btype = rng.choice(extras)
    else:
        btype = rng.choice(biz["business_types_global"])
    name = " ".join(parts) + " " + btype
    # ~50% append a locale-appropriate suffix.
    if rng.random() < 0.50:
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
        city_keys = None  # whole-region pool for canonical leagues
    else:
        league_key = None  # no authored distribution -> balanced default
        city_keys, region_key = _resolve_locale_city_keys(locale)

    _locale_key = json.dumps(locale, sort_keys=True) if isinstance(locale, dict) else (locale or "")
    seed = (rng_seed ^ zlib.crc32(league_name.encode())
            ^ zlib.crc32(_locale_key.encode())) & 0x7FFFFFFF
    rng = random.Random(seed)
    if used_abbrev is None:
        used_abbrev = set()

    cities = _city_pool(region_key, city_keys)
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
