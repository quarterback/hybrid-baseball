"""
Zaryan name converter.

Implements the Zaryanification pipeline: takes an English first + last name
drawn from the standard American/Black-American pools and runs it through
a Russian-English creole transformation. Output is a full Zaryan name with
patronymic — e.g.

    "Marcus Brooks"          ->  "Maks Elijahovich Brooks"
    "Paul Smith"             ->  "Pavel Genrievich Kuznetsov"
    "Karen Harrison" (older) ->  "Kira Marcusovna Garisonova"

The pipeline (per the design spec) is:

  1. Phonology — H->G, TH->T, W->V (applied to patronymics; surnames default
     to the "modern register" and keep original spelling).
  2. Surname path — A (Russified -ov ending), B (translated-Slavic via a
     meaning dictionary), or C (bare English).
  3. Patronymic — built from a separately-drawn father's first name with
     -ovich (m) or -ovna (f), after phonology.
  4. First name — Russianized via the convert dictionary, or kept as a
     heritage/Biblical name.
  5. Feminization — Path B always feminizes for women; Path A/C only for
     "older/rural" register characters.

Hooked into o27v2.league.make_name_picker via the "zaryanovia" region id.
"""
from __future__ import annotations

import os
import re
import json
import random


# ---------------------------------------------------------------------------
# Step 1: phonology
# ---------------------------------------------------------------------------

# Match H only when NOT preceded by another consonant — so the rule fires
# on word-initial H (Harrison → Garrison) and intervocalic H (Ohio → Ogio)
# but leaves the Sh/Ch/Ph/Th/Gh/Kh/Wh/Rh digraphs alone (Shaq stays Shaq,
# Richey stays Richey, Prakash stays Prakash).
_H_NOT_DIGRAPH = re.compile(r"(?<![BbCcDdFfGgJjKkLlMmNnPpQqRrSsTtVvWwXxZz])H",
                            flags=re.IGNORECASE)
_TH = re.compile(r"Th", flags=re.IGNORECASE)
# W → V, except when followed by h (Wh digraph: Whistler stays Whistler).
_W_NOT_WH = re.compile(r"W(?!h)", flags=re.IGNORECASE)


def _apply_phonology(s: str) -> str:
    """Russian sound system reshapes English roots. Order matters: TH before H."""
    s = _TH.sub(lambda m: "T" if m.group(0)[0].isupper() else "t", s)
    s = _H_NOT_DIGRAPH.sub(lambda m: "G" if m.group(0).isupper() else "g", s)
    s = _W_NOT_WH.sub(lambda m: "V" if m.group(0).isupper() else "v", s)
    return s


# ---------------------------------------------------------------------------
# Step 2: surname conversion
# ---------------------------------------------------------------------------

# Path B — meaning-based translations. Keys are lowercase English surnames;
# values are the Slavic stem (masculine form; -a is appended for women).
# Authored from the spec's worked examples + natural extensions.
_PATH_B_SURNAMES: dict[str, str] = {
    "wood":     "Lesov",
    "woods":    "Lesov",
    "smith":    "Kuznetsov",
    "black":    "Chernov",
    "stone":    "Kamnev",
    "stones":   "Kamnev",
    "rivers":   "Rechnov",
    "river":    "Rechnov",
    "bright":   "Svetlov",
    "hill":     "Gornov",
    "hills":    "Gornov",
    "young":    "Molodov",
    "king":     "Korolyov",
    "bell":     "Kolokolov",
    "bells":    "Kolokolov",
    "cross":    "Krestov",
    "field":    "Polev",
    "fields":   "Polev",
    "snow":     "Snegov",
    "bird":     "Ptitsyn",
    "fox":      "Lisin",
    "wolf":     "Volkov",
    "wolfe":    "Volkov",
    "wolves":   "Volkov",
    "reed":     "Trostnikov",
    "reeds":    "Trostnikov",
    "day":      "Dnyov",
    "winter":   "Zimin",
    "winters":  "Zimin",
    "summer":   "Letov",
    "summers":  "Letov",
    "freeman":  "Volnov",
    "freemen":  "Volnov",
    "white":    "Belov",
    "brown":    "Buryev",
    "green":    "Zelyonov",
    "rose":     "Rozin",
    "knight":   "Rytsarev",
    "ford":     "Brodov",
    "lake":     "Ozerov",
    "lakes":    "Ozerov",
    "wells":    "Kolodtsev",
    "well":     "Kolodtsev",
    "moore":    "Bolotov",
    "moor":     "Bolotov",
    "stark":    "Surovov",
    "swift":    "Bystrov",
    "fisher":   "Rybakov",
    "shepherd": "Pastukhov",
    "shepard":  "Pastukhov",
    "miller":   "Melnikov",
    "baker":    "Pekarev",
    "carpenter":"Plotnikov",
    "porter":   "Nosilov",
    "potter":   "Goncharov",
    "weaver":   "Tkachev",
    "mason":    "Kamenshchikov",
    "cook":     "Povarov",
    "hunter":   "Okhotnikov",
    "gardener": "Sadovnikov",
    "knox":     "Skalov",
    "stout":    "Krepkov",
    "strong":   "Sil'nov",
}


def _russify_surname(eng: str) -> str:
    """Path A: tack a Slavic ending onto the English surname.

    Rules (from the spec):
      * names ending in -er/-or keep the suffix then add -ov  -> Turner -> Turnerov
      * names ending in vowel cluster: drop the cluster, add -in -> Riley -> Rilin
      * otherwise add -ov                                     -> Brooks -> Brooksov
    """
    if not eng or len(eng.strip()) < 2:
        return eng
    eng = eng.strip()
    low = eng.lower()
    if (low.endswith("ov") or low.endswith("ev") or low.endswith("in")
            or low.endswith("sky") or low.endswith("skiy")
            or low.endswith("tsky") or low.endswith("ich")):
        return eng   # already looks Russian; don't double up
    # Drop trailing vowel cluster (Riley -> Ril, Carey -> Car, Singletary -> Singletar)
    i = len(eng)
    while i > 1 and eng[i - 1].lower() in "aeiouy":
        i -= 1
    if i < len(eng):
        return eng[:i] + "in"
    return eng + "ov"


def _feminize(stem: str) -> str:
    """Feminize a Slavic-style surname. Adds -a to -ov/-ev/-in/-yn etc.
    Surnames ending in any other consonant get -a too (Volkov -> Volkova,
    Ptitsyn -> Ptitsyna, Lisin -> Lisina)."""
    if not stem:
        return stem
    low = stem.lower()
    if low.endswith("a") or low.endswith("aya"):
        return stem
    if low.endswith("ij") or low.endswith("y"):
        return stem[:-1] + "aya"   # -y -> -aya
    return stem + "a"


def _convert_surname(
    eng: str,
    gender: str,
    rng: random.Random,
    *,
    register_weights: tuple[float, float, float] = (0.45, 0.35, 0.20),
    feminize_path_ac: float = 0.15,
) -> str:
    """Apply Step 2 (surname path) + Step 5 (feminization).

    `register_weights` = (Path A, Path B, Path C) probabilities. Path B is
    only available when the English surname has a meaning entry; if not,
    its weight folds into Path A (Russified) as the natural fallback.
    """
    low = (eng or "").strip().lower()
    has_meaning = low in _PATH_B_SURNAMES

    wa, wb, wc = register_weights
    if not has_meaning:
        wa += wb
        wb = 0.0
    total = wa + wb + wc
    r = rng.random() * total

    if r < wa:
        path = "A"
        out = _russify_surname(eng)
    elif r < wa + wb:
        path = "B"
        out = _PATH_B_SURNAMES[low]
    else:
        path = "C"
        out = eng

    if gender == "female":
        if path == "B":
            out = _feminize(out)
        elif rng.random() < feminize_path_ac:
            out = _feminize(out)
    return out


# ---------------------------------------------------------------------------
# Step 4: first name conversion (English -> Russian)
# ---------------------------------------------------------------------------

_FIRST_NAME_M: dict[str, str] = {
    "marcus":    "Maks",
    "mark":      "Mark",
    "michael":   "Mikhail",
    "mike":      "Misha",
    "paul":      "Pavel",
    "eugene":    "Evgeny",
    "cyrus":     "Kirill",
    "daniel":    "Daniil",
    "dan":       "Danya",
    "roman":     "Roman",
    "anthony":   "Antoniy",
    "tony":      "Tosha",
    "john":      "Ivan",
    "james":     "Yakov",
    "jim":       "Yasha",
    "george":    "Georgy",
    "samuel":    "Samuil",
    "sam":       "Sasha",
    "alexander": "Aleksandr",
    "alex":      "Sasha",
    "andrew":    "Andrey",
    "peter":     "Pyotr",
    "pete":      "Petya",
    "stephen":   "Stepan",
    "steven":    "Stepan",
    "stefan":    "Stepan",
    "thomas":    "Foma",
    "tom":       "Foma",
    "edward":    "Eduard",
    "ed":        "Eduard",
    "robert":    "Rostislav",
    "rob":       "Rostya",
    "richard":   "Rikhard",
    "henry":     "Genri",
    "william":   "Vilyam",
    "max":       "Maks",
    "maxwell":   "Maks",
    "philip":    "Filipp",
    "frank":     "Frants",
    "francis":   "Frants",
    "joseph":    "Iosif",
    "joe":       "Yosha",
    "matthew":   "Matvey",
    "matt":      "Matvey",
    "nicholas":  "Nikolay",
    "nick":      "Kolya",
    "david":     "David",
    "dave":      "Davyd",
    "isaiah":    "Isayev",
    "elijah":    "Iliya",
    "solomon":   "Solomon",
    "booker":    "Booker",
    "moses":     "Moisey",
    "abraham":   "Avraam",
    "zachariah": "Zakhary",
    "zach":      "Zakhar",
    "noah":      "Noy",
    "jacob":     "Yakov",
    "jake":      "Yasha",
    "luke":      "Luka",
    "mark":      "Mark",
    "simon":     "Semyon",
    "tobias":    "Tovy",
    "ethan":     "Iyetan",
    "leon":      "Lev",
    "leo":       "Lev",
    "vincent":   "Vikenty",
    "victor":    "Viktor",
    "felix":     "Feliks",
    "harold":    "Garold",
    "howard":    "Govard",
    "harrison":  "Garison",
    "harvey":    "Garvey",
    "sterling":  "Sterling",
    "cody":      "Kodya",
}

_FIRST_NAME_F: dict[str, str] = {
    "mary":      "Maria",
    "maria":     "Maria",
    "julia":     "Yulia",
    "julie":     "Yulia",
    "karen":     "Kira",
    "victoria":  "Vika",
    "vicki":     "Vika",
    "catherine": "Yekaterina",
    "katherine": "Yekaterina",
    "kate":      "Katya",
    "kathy":     "Katya",
    "cathy":     "Katya",
    "katie":     "Katya",
    "valerie":   "Valeriya",
    "val":       "Ria",
    "nancy":     "Nadya",
    "helen":     "Elena",
    "elaine":    "Elena",
    "ellen":     "Elena",
    "anne":      "Anna",
    "ann":       "Anna",
    "anna":      "Anna",
    "anita":     "Anya",
    "elizabeth": "Yelizaveta",
    "liz":       "Liza",
    "beth":      "Liza",
    "olivia":    "Oksana",
    "sarah":     "Zara",
    "sara":      "Zara",
    "rebecca":   "Rivka",
    "ruth":      "Ruf",
    "esther":    "Estera",
    "naomi":     "Naomi",
    "rachel":    "Rakhil",
    "leah":      "Liya",
    "deborah":   "Devora",
    "miriam":    "Miriam",
    "hannah":    "Anna",
    "abigail":   "Avigail",
    "claire":    "Klara",
    "claudia":   "Klaudiya",
    "diana":     "Diana",
    "irene":     "Irina",
    "joan":      "Yanna",
    "joanne":    "Yanna",
    "judith":    "Yudif",
    "linda":     "Liliya",
    "lisa":      "Liza",
    "michelle":  "Mila",
    "monica":    "Monika",
    "patricia":  "Praskovya",
    "pat":       "Pasha",
    "rose":      "Roza",
    "stephanie": "Stefaniya",
    "susan":     "Susanna",
    "sue":       "Sonya",
    "teresa":    "Taisiya",
    "therese":   "Taisiya",
    "veronica":  "Veronika",
    "wendy":     "Vanda",
    "yvonne":    "Evvonna",
    "coretta":   "Koretta",
    "ada":       "Ada",
}


def _convert_first_name(eng: str, gender: str, rng: random.Random,
                        *, russianize_p: float = 0.65) -> str:
    """Step 4: swap English first name for a Russian one (most common),
    or keep it as a Biblical/heritage name (the Stratton AME Zion stream).
    """
    if not eng:
        return eng
    low = eng.strip().lower()
    table = _FIRST_NAME_F if gender == "female" else _FIRST_NAME_M

    keep_biblical_set = {
        "elijah", "isaiah", "solomon", "booker", "marcus", "coretta", "ada",
        "moses", "abraham", "zachariah", "noah", "jacob", "ethan", "simon",
        "luke", "tobias", "leon", "leo", "felix", "miriam", "esther", "ruth",
        "naomi", "rachel", "leah", "deborah", "rebecca", "hannah", "abigail",
    }
    if low in keep_biblical_set and rng.random() < 0.55:
        return eng.capitalize()
    if rng.random() < russianize_p and low in table:
        return table[low]
    # Fall through: keep original English, capitalized
    return eng.capitalize()


# ---------------------------------------------------------------------------
# Step 3: patronymic
# ---------------------------------------------------------------------------

def _patronymic(father_eng: str, gender: str) -> str:
    """Build a patronymic from a father's English first name. Apply phonology
    (H->G, W->V), then attach the gendered suffix.

    Suffix rules (derived from the spec's worked examples):
      * end in consonant         -> +ovich / +ovna   (Govard -> Govardovich)
      * end in -i / -y           -> +evich / +evna   (Genri  -> Genrievich,
                                                       Mikey  -> Mikeyevich)
      * end in -a / -e / -o / -u -> drop the vowel, +evich / +evna
                                    (Kodya -> Kody+evich = Kodyevich;
                                     Tyrone -> Tyron+evna = Tyronevna)
    """
    if not father_eng or len(father_eng.strip()) < 2:
        return ""
    base = _apply_phonology(father_eng).strip()
    if len(base) < 2:
        return ""
    is_female = (gender == "female")
    cons_suffix = "ovna" if is_female else "ovich"
    soft_suffix = "evna" if is_female else "evich"
    low = base.lower()
    last = low[-1]
    if last in "iy":
        return base + soft_suffix
    if last in "aeou":
        return base[:-1] + soft_suffix
    return base + cons_suffix


# ---------------------------------------------------------------------------
# Public draw
# ---------------------------------------------------------------------------

_NAMES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "names")
_cache: dict[str, list[str]] = {}


def _load_pool(fname: str, key: str) -> list[str]:
    cache_key = f"{fname}::{key}"
    if cache_key in _cache:
        return _cache[cache_key]
    with open(os.path.join(_NAMES_DIR, fname), encoding="utf-8") as f:
        d = json.load(f)
    pool = d.get(key, [])
    _cache[cache_key] = pool
    return pool


# English first-name sub-pools the creole majority sources from. The AME
# Zion / Black-American settler stream is the founding majority; American
# regional pools fill out the rest. African-origin Zaryans exist (post-WWII
# colonial-army-veteran migration + the Ethiopian thread) but they KEEP
# their origin-culture names rather than getting run through the creole
# Zaryanification filter — they're routed via the "african" expat stream
# below, not folded in here.
_ENG_FIRST_KEYS_M = ["black_american", "american_south", "american_northeast",
                     "american_midwest", "american_west"]
_ENG_FIRST_KEYS_F = ["black_american", "american_south", "american_northeast",
                     "american_midwest", "american_west"]
_ENG_FIRST_WEIGHTS = [0.40, 0.20, 0.15, 0.15, 0.10]

_ENG_SURNAME_KEYS    = ["black_american", "american_general"]
_ENG_SURNAME_WEIGHTS = [0.35, 0.65]


def _weighted_choice(rng: random.Random, options: list[str],
                     weights: list[float]) -> str:
    total = sum(weights)
    r = rng.random() * total
    acc = 0.0
    for opt, w in zip(options, weights):
        acc += w
        if r < acc:
            return opt
    return options[-1]


def _draw_english(rng: random.Random, pool_kind: str) -> str:
    """Draw a random name from the weighted English sub-pools."""
    if pool_kind == "male_first":
        fname = "male_first.json"
        keys, weights = _ENG_FIRST_KEYS_M, _ENG_FIRST_WEIGHTS
    elif pool_kind == "female_first":
        fname = "female_first.json"
        keys, weights = _ENG_FIRST_KEYS_F, _ENG_FIRST_WEIGHTS
    else:
        fname = "surnames.json"
        keys, weights = _ENG_SURNAME_KEYS, _ENG_SURNAME_WEIGHTS
    # try in weighted order, fall through if a pool is empty
    tries = list(zip(keys, weights))
    while tries:
        idx = 0
        # weighted draw among remaining
        total = sum(w for _, w in tries)
        r = rng.random() * total
        acc = 0.0
        for i, (_, w) in enumerate(tries):
            acc += w
            if r < acc:
                idx = i
                break
        key = tries[idx][0]
        pool = _load_pool(fname, key)
        # Drop names too short to phonologize cleanly (e.g. "KK", initials).
        pool = [p for p in pool if isinstance(p, str) and len(p.strip()) >= 3]
        if pool:
            return rng.choice(pool)
        tries.pop(idx)
    return ""


def zaryanify_draw(rng: random.Random, gender: str) -> tuple[str, str]:
    """Return (full_name, country_code='ZR') for a CREOLE Zaryan name —
    the 130-year-deep Black-American + African + mixed majority stream
    run through the full Zaryanification pipeline.

    The full name is "First Patronymic Last" — three components, separated
    by single spaces, matching how other players in the league surface.
    """
    is_female = (gender or "male").lower() == "female"
    pool_kind = "female_first" if is_female else "male_first"

    eng_first   = _draw_english(rng, pool_kind)
    eng_last    = _draw_english(rng, "surname")
    father_eng  = _draw_english(rng, "male_first")   # patronymic is always paternal

    first = _convert_first_name(eng_first, "female" if is_female else "male", rng)
    last  = _convert_surname(eng_last, "female" if is_female else "male", rng)
    patro = _patronymic(father_eng, "female" if is_female else "male")

    if patro:
        full = f"{first} {patro} {last}"
    else:
        full = f"{first} {last}"
    return full, "ZR"


# ---------------------------------------------------------------------------
# Diversity buckets — stratified pick over the four Zaryan streams
# ---------------------------------------------------------------------------
#
# Most Zaryan names go through the Zaryanification pipeline (the 130-year-
# deep creole). The rest pick one of three named buckets — Russian
# minority, East Asian (Korean/Japanese/Chinese), or a diverse "expat"
# pool (the third-culture-kid passport mix the master wiki calls out,
# including modern Americans and African Zaryans who keep origin-country
# names). Each bucket's share is fixed; the expat pool is flat across
# many cultures within it.
#
# No special-case patronymic logic — each culture's pool already produces
# the name shape that culture wears in this game.

ZARYAN_STREAM_WEIGHTS: dict[str, float] = {
    "creole":    0.67,   # Zaryanification pipeline (creole majority)
    "russian":   0.13,   # ethnic Russian minority — "marked normal" group
    "east_asia": 0.13,   # Korean / Japanese / Chinese, combined
    "ukrainian": 0.03,   # Zelyony Klyn "Green Wedge" minority — real
                         # late-19C/early-20C Ukrainian-majority area in
                         # the Russian Far East; historically grounded
                         # Slavic minority distinct from ethnic Russian
    "expat":     0.04,   # third-culture-kid pool incl. modern Americans + Africans
}

# Within the East Asian bucket, distribute the weight across the three
# streams. These sum to 1.0 (proportions inside the 7% slice).
_EAST_ASIA_WEIGHTS: list[tuple[str, str, float]] = [
    ("korean",   "korean",   0.45),
    ("japanese", "japanese", 0.35),
    ("chinese",  "chinese",  0.20),
]

# The expat pool — flat list of cultures, weighted within itself.
# Modern Americans (the "foreign but also not" wave) + African Zaryans
# (origin-country names kept) + a broad third-culture-kid bevy.
_EXPAT_CULTURES: list[tuple[str, str, float]] = [
    # Modern American expats
    ("american_midwest",   "american_general",   0.10),
    ("american_northeast", "american_general",   0.08),
    ("black_american",     "black_american",     0.06),
    # African Zaryans — keep origin-country names, not Russified
    ("african",             "african",            0.07),
    ("ethiopian",           "ethiopian",          0.04),
    ("yoruba",              "yoruba",             0.03),
    ("east_african",        "east_african",       0.03),
    # Third-culture-kid passport mix. The surname-key may differ from
    # first-key when the codebase splits a culture across multiple
    # pools — Brazilian first names + Brazilian-Portuguese surnames,
    # Mexican first names sourced from latin_american pools, Lebanese
    # surnames from arabic since there's no direct Lebanese surname pool.
    ("indian",              "indian",                 0.10),
    ("filipino",            "filipino",               0.05),
    ("latin_american",      "latin_american",         0.08),
    ("latin_american",      "latin_american",         0.05),   # extra weight (was "mexican")
    ("brazilian",           "brazilian_portuguese",   0.04),
    ("italian",             "italian",                0.04),
    ("german",              "german",                 0.03),
    ("french",              "french",                 0.03),
    ("iranian",             "iranian",                0.03),
    ("arabic",              "arabic",                 0.03),   # was "lebanese"
    ("turkish",             "turkish",                0.03),
    ("vietnamese",          "vietnamese",             0.03),
    ("polish",              "polish",                 0.02),
    ("greek",               "greek",                  0.01),
    ("lithuanian",          "lithuanian",             0.01),
]


def _draw_named(rng: random.Random, gender: str,
                first_key: str, surname_key: str) -> tuple[str, str] | None:
    """Native first + native surname from the given culture pools.
    No phonology, no Path A/B/C, no patronymic — the name comes out the
    way that culture normally appears in the rest of the game."""
    pool_first_fname = "female_first.json" if gender == "female" else "male_first.json"
    first_pool   = _load_pool(pool_first_fname, first_key)
    surname_pool = _load_pool("surnames.json",  surname_key)
    if not first_pool or not surname_pool:
        return None
    first   = rng.choice(first_pool)
    surname = rng.choice(surname_pool)
    return f"{first} {surname}", "ZR"


def _pick_from_weighted(rng: random.Random,
                        choices: list[tuple[str, str, float]]
                        ) -> tuple[str, str]:
    """Weighted pick among (first_key, surname_key, weight) tuples."""
    active = [c for c in choices if c[2] > 0]
    total = sum(w for _, _, w in active)
    r = rng.random() * total
    acc = 0.0
    for f, s, w in active:
        acc += w
        if r < acc:
            return f, s
    return active[-1][0], active[-1][1]


def draw_zaryan_name(rng: random.Random, gender: str) -> tuple[str, str]:
    """Top-level Zaryan name draw — stratified over five streams:

      creole    (67%):  through the Zaryanification pipeline
      russian   (13%):  bare Russian first + Russian surname
      east_asia (13%):  one of Korean / Japanese / Chinese
      ukrainian  (3%):  Zelyony Klyn "Green Wedge" Slavic minority
      expat      (4%):  one of the broad third-culture-kid culture pool
                        (modern Americans, Africans, Latin Americans,
                        Indians, Europeans, Middle Easterners…)

    Non-creole picks that come up empty (missing pool) fall through to
    the creole pipeline so a name always succeeds.
    """
    g = "female" if (gender or "male").lower() == "female" else "male"
    stream = _weighted_choice(
        rng,
        list(ZARYAN_STREAM_WEIGHTS.keys()),
        list(ZARYAN_STREAM_WEIGHTS.values()),
    )
    if stream == "creole":
        return zaryanify_draw(rng, g)
    if stream == "russian":
        out = _draw_named(rng, g, "russian", "russian")
    elif stream == "ukrainian":
        out = _draw_named(rng, g, "ukrainian", "ukrainian")
    elif stream == "east_asia":
        f_key, s_key = _pick_from_weighted(rng, _EAST_ASIA_WEIGHTS)
        out = _draw_named(rng, g, f_key, s_key)
    else:  # expat
        f_key, s_key = _pick_from_weighted(rng, _EXPAT_CULTURES)
        out = _draw_named(rng, g, f_key, s_key)
    return out if out else zaryanify_draw(rng, g)
