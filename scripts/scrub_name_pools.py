"""
Scrub scraped junk out of the name pools.

The name buckets in ``o27v2/data/names/{male_first,female_first,surnames}.json``
were seeded from scraped sports rosters/fixtures, which dragged in three kinds
of non-personal tokens:

  1. Club mascots / sponsor words  — "Antlers", "Bluewings", "Dragons",
     "Steelers", "Motors", "VfB", "Reysol", "Knights" ...
  2. City / place names            — "Dortmund", "Frankfurt", "Hiroshima",
     "Busan", "Jakarta", "Zagreb" ...
  3. Misfiled name *parts*         — Korean *given* names dumped into the
     Korean SURNAME bucket ("Hyun-soo", "Heung-min"); Chinese given-name
     romanisations and foreign-player surnames in the Chinese SURNAME bucket.

That is why the UI produced names like "Hyun-soo Knights" and "Red Young-pyo".

This script removes the junk and rewrites the three JSON files in place. It is
idempotent — running it twice is a no-op — and prints a full per-bucket report
of what it removed. The committed JSON is the authoritative source; re-run only
to re-clean after a pool refresh.

Strategy (deliberately conservative to avoid nuking real names):

  * SURNAME pools      — drop single-token city names (from hometowns.json),
    minus a whitelist of city words that are also legit surnames
    (Mendoza, Santiago, Cruz, Khan ...). City-as-surname is rare; the
    whitelist covers the real ones.
  * FIRST-NAME pools   — place-as-given-name is a real naming convention in
    the West (Paris, Victoria, Dallas, Milan, David), so we DON'T apply the
    broad city sweep here. We only strip an explicit list of foreign cities
    that are never given names (Busan, Beijing, Yokohama ...).
  * ALL pools          — drop mascot/club/sponsor junk (explicit list +
    team_naming.json mascot words).
  * CJK SURNAMES       — the Korean and Chinese surname buckets are so
    polluted with misfiled given names + foreign-player names that a
    blocklist can't save them. Instead we keep only tokens that are valid
    surnames per a canonical allowlist (Hundred-Family-Surnames for Chinese,
    the standard romanised Korean surnames). Hyphenated tokens (always given
    names in Korean) are dropped as part of this.

Run from repo root:
    python scripts/scrub_name_pools.py            # write changes
    python scripts/scrub_name_pools.py --dry-run  # report only
"""
from __future__ import annotations

import json
import os
import sys

_NAMES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "o27v2", "data", "names",
)


def _load(fname: str):
    with open(os.path.join(_NAMES_DIR, fname), encoding="utf-8") as fh:
        return json.load(fh)


def _save(fname: str, data) -> None:
    with open(os.path.join(_NAMES_DIR, fname), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# ---------------------------------------------------------------------------
# Mascot / club / sponsor junk — applies to every pool.
# ---------------------------------------------------------------------------
def _mascot_words() -> set[str]:
    """Single-token mascot words from team_naming.json's mascot pool."""
    tn = _load("team_naming.json")
    words: set[str] = set()

    def walk(node):
        if isinstance(node, str):
            for w in node.split():
                words.add(w)
        elif isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            for x in node.values():
                walk(x)

    walk(tn.get("category_3_traditional_mascots", {}).get("mascot_pool", {}))
    return words


# Real-world club / sponsor / generic-noun junk that scraped in but is not in
# the in-game mascot pool. These are never personal names.
CLUB_JUNK = {
    # East-Asian football clubs / sponsor words
    "Antlers", "Bluewings", "Blueminx", "Aces", "Citizen", "Corps",
    "Bellmare", "Cerezo", "Gamba", "Leonessa", "IPark", "Reysol", "Sakers",
    "Tosu", "Trinita", "Sanfrecce", "Shonan", "Vissel", "Pohang", "Jeonbuk",
    "Kashima", "Kashiwa", "Oita", "Sagan", "Holstein", "Hamburger", "Hamburg",
    "VfB", "VfL", "Motors", "University", "Steelers", "Reds",
    # generic nouns / non-names that slipped in
    "Birds", "Deer", "Ducks", "Flame", "Kylin", "Liberty", "Sky",
    "Sturgeons", "Timberwolves", "Wall", "States", "Coast", "City", "Corp",
    "Klinsmann",          # coach name scraped into KR pool
    # nation / direction words seen in scraped first/last name slots
    "Korea", "Zhongguo", "South", "North", "Red", "Aviv",
}

# Foreign cities that turned up in FIRST-NAME pools and are never given names.
# (We intentionally do NOT sweep all cities from first names — Paris, Victoria,
# Dallas, Milan, David, Carolina etc. are legitimate given names.)
FIRST_NAME_CITY_JUNK = {
    "Busan", "Changwon", "Daejeon", "Seoul", "Suwon", "Incheon", "Pohang",
    "Beijing", "Guangzhou", "Shanghai", "Shenzhen", "Nanjing",
    "Chiba", "Kawasaki", "Nagoya", "Yokohama", "Taichung",
    "Riyadh", "Adana", "Konya",
    "Aarhus", "Antwerp", "Malmö", "Odense", "Vejle", "Helsinki",
    "Kampala", "Kano",
}

# ---------------------------------------------------------------------------
# Surname city sweep — city words that ARE legit surnames stay.
# ---------------------------------------------------------------------------
SURNAME_CITY_KEEP = {
    # Hispanic place-surnames (genuinely common family names)
    "Mendoza", "Santiago", "Colón", "Córdoba", "Ponce", "Cruz", "Soto",
    "Medina", "Vega", "Rios", "Valencia", "Bilbao", "Granada", "Salvador",
    "Marino", "Carolina", "Veracruz",
    # English / European surnames that are also place names
    "Hamilton", "Houston", "London", "Hull", "Leeds", "Hastings", "Goodman",
    "Garrison", "Warwick", "Scarborough", "Stratton", "Lyon", "Florence",
    "Hall", "Marsh", "George", "Crane", "Barber", "Cummings", "Bosch",
    "Colombo", "Nice", "Bonn", "Linden",
    # South / East Asian & African surnames that are also place names
    "Khan", "Shah", "Dar", "Alam", "David", "Antonio", "Fernando", "Paulo",
    "Pedro", "Louis", "Long", "Kong", "Mai", "San", "Tin", "Pak", "Mun",
    "Hong", "Tong", "Ba", "Bani", "Nicolaas", "Samara",
}


def _surname_cities() -> set[str]:
    """Single-token city names from hometowns.json (distinctive place names;
    single-token avoids splitting 'Santa Cruz' -> 'Cruz')."""
    ht = _load("hometowns.json")
    blob: list[str] = []

    def walk(node):
        if isinstance(node, str):
            blob.append(node)
        elif isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            for x in node.values():
                walk(x)

    walk(ht)
    cities = set()
    for c in blob:
        c = str(c).strip()
        if c and " " not in c and "," not in c and c[0].isupper():
            cities.add(c)
    return cities


# ---------------------------------------------------------------------------
# Canonical CJK surname allowlists — the Korean & Chinese surname buckets are
# rebuilt by intersection (keep only tokens that are real surnames).
# ---------------------------------------------------------------------------
KOREAN_SURNAMES = {
    "Kim", "Lee", "Yi", "Park", "Pak", "Choi", "Choe", "Jung", "Jeong",
    "Chung", "Kang", "Cho", "Jo", "Yoon", "Yun", "Jang", "Chang", "Lim",
    "Im", "Han", "Oh", "Seo", "Suh", "Shin", "Sin", "Kwon", "Gwon",
    "Hwang", "Ahn", "An", "Song", "Yoo", "Yu", "Hong", "Jeon", "Jun",
    "Ko", "Go", "Moon", "Mun", "Yang", "Bae", "Pae", "Baek", "Paek",
    "Heo", "Hur", "Huh", "Nam", "Sim", "Shim", "Noh", "Roh", "Ha", "Jin",
    "Ryu", "Yoo", "Min", "Chu", "Joo", "Ju", "Na", "Ra", "Do", "Sun",
    "Won", "Ban", "Ban", "Gil", "Kil", "Wang", "Pyo", "Ki", "Gi", "Chae",
    "Cha", "Ku", "Koo", "Gu", "Byun", "Byeon", "Eom", "Um", "Ok", "Tak",
    "Seol", "Sol", "Kwak", "Gwak", "Yeom", "Yom", "Bang", "Pang", "Yeo",
    "Yang", "Geum", "Seok", "Sung", "Seong",
}

CHINESE_SURNAMES = {
    "Wang", "Li", "Zhang", "Liu", "Chen", "Yang", "Zhao", "Huang", "Zhou",
    "Wu", "Xu", "Sun", "Hu", "Zhu", "Gao", "Lin", "He", "Guo", "Ma", "Luo",
    "Liang", "Song", "Zheng", "Xie", "Han", "Tang", "Feng", "Yu", "Dong",
    "Xiao", "Cheng", "Cao", "Yuan", "Deng", "Xu", "Fu", "Shen", "Zeng",
    "Peng", "Lyu", "Su", "Lu", "Jiang", "Cai", "Jia", "Ding", "Wei", "Xue",
    "Ye", "Yan", "Pan", "Du", "Dai", "Xia", "Zhong", "Wang", "Tian", "Ren",
    "Jiang", "Fan", "Fang", "Shi", "Yao", "Tan", "Liao", "Zou", "Xiong",
    "Jin", "Lu", "Hao", "Kong", "Bai", "Cui", "Kang", "Mao", "Qiu", "Qin",
    "Jiang", "Shi", "Gu", "Hou", "Shao", "Meng", "Long", "Wan", "Duan",
    "Lei", "Qian", "Tang", "Yin", "Li", "Yi", "Chang", "Wu", "Qiao", "He",
    "Lai", "Gong", "Wen", "Pang", "Fan", "Lan", "Ke", "Qi", "Pu", "Qu",
    "Ru", "Tao", "Zhi",
    # common Cantonese / overseas romanisations
    "Chan", "Chen", "Cheung", "Chow", "Chu", "Fong", "Ho", "Hui", "Kwan",
    "Kwok", "Lam", "Lau", "Leung", "Lo", "Ma", "Mak", "Ng", "Tse", "Tsui",
    "Wong", "Wu", "Yeung", "Yip", "Yuen", "Tsun", "Wai", "Wing", "Hin",
    "Hoi", "Lok", "Fai", "Him", "Kwan",
}


def scrub(dry_run: bool = False) -> dict:
    male = _load("male_first.json")
    female = _load("female_first.json")
    surnames = _load("surnames.json")

    mascots = _mascot_words() | CLUB_JUNK
    surname_city_junk = (_surname_cities() | mascots) - SURNAME_CITY_KEEP

    report: dict[str, dict[str, list[str]]] = {
        "male_first": {}, "female_first": {}, "surnames": {},
    }

    def clean_bucket(pool_name, key, values, blocklist):
        removed = [v for v in values if v in blocklist]
        if removed:
            report[pool_name][key] = sorted(set(removed))
        kept = [v for v in values if v not in blocklist]
        # de-dupe preserving order
        seen, out = set(), []
        for v in kept:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    # First names: mascots + explicit foreign-city junk only.
    first_block = mascots | FIRST_NAME_CITY_JUNK
    for pool_name, pool in (("male_first", male), ("female_first", female)):
        for key, values in pool.items():
            if isinstance(values, list):
                pool[key] = clean_bucket(pool_name, key, values, first_block)

    # Surnames: mascots + city sweep; CJK buckets get canonical allowlist.
    for key, values in surnames.items():
        if not isinstance(values, list):
            continue
        if key == "korean":
            removed = sorted({v for v in values if v not in KOREAN_SURNAMES})
            if removed:
                report["surnames"][key] = removed
            surnames[key] = sorted(KOREAN_SURNAMES & set(values))
        elif key == "chinese":
            removed = sorted({v for v in values if v not in CHINESE_SURNAMES})
            if removed:
                report["surnames"][key] = removed
            surnames[key] = sorted(CHINESE_SURNAMES & set(values))
        else:
            surnames[key] = clean_bucket("surnames", key, values, surname_city_junk)

    if not dry_run:
        _save("male_first.json", male)
        _save("female_first.json", female)
        _save("surnames.json", surnames)

    return report


def main():
    dry = "--dry-run" in sys.argv
    report = scrub(dry_run=dry)
    total = 0
    for pool_name in ("surnames", "male_first", "female_first"):
        buckets = report[pool_name]
        if not buckets:
            continue
        print(f"\n=== {pool_name} ===")
        for key in sorted(buckets):
            removed = buckets[key]
            total += len(removed)
            print(f"  {key} (-{len(removed)}): {removed}")
    print(f"\n{'[DRY RUN] would remove' if dry else 'Removed'} {total} junk tokens.")


if __name__ == "__main__":
    main()
