"""
One-time name pool seeder using pybaseball.

Scrapes real MLB player names from pybaseball's batting/pitching stats and
categorises them into four regional pools:

  usa          — primarily English/European-American names
  latin        — Spanish/Portuguese-origin names (Caribbean, South America)
  japan_korea  — Japanese and Korean names
  other        — European, African, South Asian, and other international names

Usage:
    python scripts/seed_names.py

Output files (committed to repo; pybaseball is NOT a runtime dependency):
    o27v2/data/names/usa.json
    o27v2/data/names/latin.json
    o27v2/data/names/japan_korea.json
    o27v2/data/names/other.json

NOTE: The committed JSON files are the authoritative source.  Re-run this
script only to refresh pools from a newer pybaseball snapshot.
"""
from __future__ import annotations
import json
import os
import sys
import re
import unicodedata

# ---- Paths ------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR   = os.path.dirname(SCRIPT_DIR)
NAMES_DIR  = os.path.join(ROOT_DIR, "o27v2", "data", "names")
os.makedirs(NAMES_DIR, exist_ok=True)

# ---- Name sets for classification -------------------------------------------

LATIN_SURNAMES = {
    "rodriguez", "gonzalez", "martinez", "hernandez", "garcia", "lopez",
    "diaz", "perez", "sanchez", "ramirez", "torres", "flores", "alvarez",
    "morales", "reyes", "jimenez", "ruiz", "suarez", "gutierrez", "castro",
    "romero", "ortiz", "vargas", "cabrera", "molina", "nunez", "beltran",
    "cespedes", "abreu", "acosta", "acevedo", "aguilar", "alcantara",
    "alfonzo", "almonte", "alonso", "altuve", "cordero", "correa", "cruz",
    "encarnacion", "escobar", "familia", "feliz", "fernandez", "figueroa",
    "franco", "gurriel", "guzman", "infante", "liriano", "machado",
    "mejia", "mercado", "mesa", "montero", "mora", "moreno", "munoz",
    "odor", "olivares", "osuna", "pagan", "paredes", "pena", "peralta",
    "polanco", "robles", "rodney", "rojas", "rosario", "santana", "soto",
    "tapia", "taveras", "tejada", "urias", "uribe", "valera", "vasquez",
    "velasquez", "velazquez", "villar", "vizcaino", "volquez", "acuna",
    "baez", "bautista", "berrios", "betances", "blanco", "bogaerts",
    "contreras", "de la rosa", "dominguez", "guerrero", "herrera", "ynoa",
}

EAST_ASIAN_SURNAMES = {
    "ohtani", "yamamoto", "sasaki", "tanaka", "maeda", "darvish", "kikuchi",
    "uehara", "nomo", "matsui", "suzuki", "iwakuma", "kawakami", "kuroda",
    "saito", "nakamura", "yoshida", "fujikawa", "aoki", "arihara", "imanaga",
    "ishikawa", "kaneko", "kimura", "kobayashi", "matsuda", "matsumoto",
    "shimizu", "sugano", "takahashi", "watanabe", "yamada", "yamaguchi",
    "yanagita", "oh", "kim", "lee", "park", "choi", "jung", "kang", "cho",
    "lim", "han", "shin", "yang", "jang", "song", "hong", "kwon", "ryu",
    "ahn", "yoon", "bae", "yoo", "nam", "moon", "ko", "hwang", "seo",
}

# Surnames clearly from non-English, non-Latin, non-East-Asian origins
OTHER_SURNAME_PATTERNS = [
    r"^(van|de|von|di|le|la|du|mc|mac|o')",            # European particles
    r"(berg|stein|mann|man|burg|feld|bach|auer|huber|meier|maier|ner|haar|baar|baer)$",  # German
    r"(sen|ssen|dahl|lund|strom|quist|gren|boe|vik|vold|dal|holm)$",   # Nordic
    r"(escu|ache|oiu|anu)$",                            # Romanian
    r"(ic|ich|ovic|ski|sky|czyk|wicz|lic|nik|vac)$",   # Slavic
    r"(obi|ade|ola|olu|una|ebe|chi|eke|uzo|nna)$",     # West African
    r"(pur|kar|rao|nair|iyer|appa|swamy|reddy|kumar)$", # South Asian
]

# Japanese-style surname endings — checked for japan_korea BEFORE other patterns
_JAPANESE_SURNAME_RE = re.compile(
    r"(ga|gi|no|ka|ko|ta|shi|mi|ra|na|ma|ya|ro|wa|ha|ne|mo|to|ki|su|ri|ku|fu|yu|tsu)$",
    re.IGNORECASE,
)

_OTHER_RE = [re.compile(p, re.IGNORECASE) for p in OTHER_SURNAME_PATTERNS]


def _normalise(s: str) -> str:
    """Lowercase, strip accents."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def classify_name(first: str, last: str) -> str:
    """
    Return one of: usa | latin | japan_korea | other.

    Classification order:
      1. East Asian surname lookup
      2. Korean/Japanese given-name patterns (hyphenated or syllable clusters)
      3. Latin surname lookup
      4. Latin phonetic endings
      5. Explicit non-English surname regex patterns → other
      6. Default: usa
    """
    nl = _normalise(last)
    nf = _normalise(first)

    # East Asian surnames
    if nl in EAST_ASIAN_SURNAMES:
        return "japan_korea"

    # Korean-style hyphenated given names (e.g., "Ha-Seong")
    if "-" in first:
        return "japan_korea"

    # Japanese/Korean given-name syllable patterns
    if re.search(r"\b(ha|ji|jae|sung|hyun|woo|seong|seung|hee|soo|yoon|jun|jin|min)\b",
                 nf, re.IGNORECASE):
        return "japan_korea"

    # Latin surnames (exact match)
    if nl in LATIN_SURNAMES:
        return "latin"

    # Latin phonetic endings
    if re.search(r"(ez|illo|illo|aldo|ardo|endo|ero|ino|ario|ano|uela)$", nl):
        return "latin"

    # Japanese-style surname endings (before other European patterns)
    if _JAPANESE_SURNAME_RE.search(nl):
        return "japan_korea"

    # Explicit "other" patterns (non-English European, African, South Asian)
    for pattern in _OTHER_RE:
        if pattern.search(nl):
            return "other"

    # Non-ASCII characters in the surname suggest non-English origin
    if nl != _normalise(last).encode("ascii", "ignore").decode():
        return "other"

    # Default to USA
    return "usa"


# ---- Main -------------------------------------------------------------------

def scrape_and_save() -> None:
    try:
        import pybaseball
    except ImportError:
        print("pybaseball not installed.  Run:  pip install pybaseball")
        sys.exit(1)

    print("Fetching batting stats 2018-2023…")
    batting = pybaseball.batting_stats_range("2018-01-01", "2023-12-31")
    print("Fetching pitching stats 2018-2023…")
    pitching = pybaseball.pitching_stats_range("2018-01-01", "2023-12-31")

    pools: dict[str, dict[str, set]] = {
        "usa":         {"first_names": set(), "last_names": set()},
        "latin":       {"first_names": set(), "last_names": set()},
        "japan_korea": {"first_names": set(), "last_names": set()},
        "other":       {"first_names": set(), "last_names": set()},
    }

    for df in [batting, pitching]:
        for _, row in df.iterrows():
            name = str(row.get("Name", "") or "").strip()
            if not name or " " not in name:
                continue
            parts = name.split()
            first, last = parts[0], parts[-1]
            region = classify_name(first, last)
            pools[region]["first_names"].add(first)
            pools[region]["last_names"].add(last)

    for region, data in pools.items():
        out = {
            "first_names": sorted(data["first_names"]),
            "last_names":  sorted(data["last_names"]),
        }
        path = os.path.join(NAMES_DIR, f"{region}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, ensure_ascii=False)
        print(f"  Wrote {path} — "
              f"{len(out['first_names'])} first names, "
              f"{len(out['last_names'])} last names")

    print("Done — name pools refreshed.")


# ---- Dry-run classification check -------------------------------------------

def classify_samples() -> None:
    """Quick sanity-check of the classifier without pybaseball."""
    samples = [
        ("Aaron",    "Judge",      "usa"),
        ("Jose",     "Ramirez",    "latin"),
        ("Shohei",   "Ohtani",     "japan_korea"),
        ("Ha-Seong", "Kim",        "japan_korea"),
        ("Felix",    "Hernandez",  "latin"),
        ("Lars",     "Nootbaar",   "other"),
        ("Robbie",   "Grossman",   "other"),
        ("Matt",     "Olson",      "usa"),
        ("Yordan",   "Alvarez",    "latin"),
        ("Kodai",    "Senga",      "japan_korea"),
        ("Nico",     "Hoerner",    "other"),
        ("Adolis",   "Garcia",     "latin"),
        ("Jo",       "Adell",      "usa"),
        ("Hyun-Jin", "Ryu",        "japan_korea"),
    ]
    print("=== classify_name() sanity check ===")
    for first, last, expected in samples:
        got = classify_name(first, last)
        status = "OK" if got == expected else f"WRONG (expected {expected})"
        print(f"  {first} {last:<20} → {got:<15} {status}")


if __name__ == "__main__":
    if "--check" in sys.argv:
        classify_samples()
    else:
        scrape_and_save()
