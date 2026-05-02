"""
30-team league definition and player generation for O27v2.

Each team has 12 players:
  - 9 position players (slot 9 = pitcher)
  - 3 jokers

Skill attributes are generated with a seeded RNG so the league is
reproducible. Each team has a slightly different skill profile to give
personality (offense-heavy, pitching-heavy, speed team, etc.).
"""
from __future__ import annotations
import random
from typing import Any

TEAMS: list[dict] = [
    # AL East
    {"name": "Harbor Hawks",   "abbrev": "HHK", "city": "Harbor City",  "division": "AL East",    "league": "AL"},
    {"name": "Bayport Bears",  "abbrev": "BBR", "city": "Bayport",      "division": "AL East",    "league": "AL"},
    {"name": "Eastside Eagles","abbrev": "EEG", "city": "Eastside",     "division": "AL East",    "league": "AL"},
    {"name": "Northgate Norse","abbrev": "NNR", "city": "Northgate",    "division": "AL East",    "league": "AL"},
    {"name": "Riverside Rams", "abbrev": "RRM", "city": "Riverside",    "division": "AL East",    "league": "AL"},
    # AL Central
    {"name": "Prairie Wolves", "abbrev": "PWO", "city": "Prairie",      "division": "AL Central", "league": "AL"},
    {"name": "Lakeview Lions", "abbrev": "LLI", "city": "Lakeview",     "division": "AL Central", "league": "AL"},
    {"name": "Midfield Mustangs","abbrev":"MMU","city": "Midfield",     "division": "AL Central", "league": "AL"},
    {"name": "Ironbridge Iron","abbrev": "IBR", "city": "Ironbridge",   "division": "AL Central", "league": "AL"},
    {"name": "Sundown Sox",    "abbrev": "SSX", "city": "Sundown",      "division": "AL Central", "league": "AL"},
    # AL West
    {"name": "Crestview Condors","abbrev":"CVC","city": "Crestview",    "division": "AL West",    "league": "AL"},
    {"name": "Dune Devils",    "abbrev": "DDV", "city": "Dune City",    "division": "AL West",    "league": "AL"},
    {"name": "Mesa Monarchs",  "abbrev": "MMO", "city": "Mesa",         "division": "AL West",    "league": "AL"},
    {"name": "Pacific Pines",  "abbrev": "PPI", "city": "Pinecrest",    "division": "AL West",    "league": "AL"},
    {"name": "Canyon Crows",   "abbrev": "CCR", "city": "Canyon",       "division": "AL West",    "league": "AL"},
    # NL East
    {"name": "Capital Capitals","abbrev":"CAP", "city": "Capital City", "division": "NL East",    "league": "NL"},
    {"name": "Harborview Herons","abbrev":"HVH","city":"Harborview",    "division": "NL East",    "league": "NL"},
    {"name": "Bayside Bulldogs","abbrev":"BBD", "city": "Bayside",      "division": "NL East",    "league": "NL"},
    {"name": "Stonegate Stags","abbrev": "SGS", "city": "Stonegate",    "division": "NL East",    "league": "NL"},
    {"name": "Redwood Rockets","abbrev": "RRK", "city": "Redwood",      "division": "NL East",    "league": "NL"},
    # NL Central
    {"name": "Millbrook Foxes","abbrev": "MFX", "city": "Millbrook",    "division": "NL Central", "league": "NL"},
    {"name": "Crossroads Cubs","abbrev": "CCC", "city": "Crossroads",   "division": "NL Central", "league": "NL"},
    {"name": "Flatlands Flash","abbrev": "FLF", "city": "Flatlands",    "division": "NL Central", "league": "NL"},
    {"name": "Inland Inferno", "abbrev": "INF", "city": "Inland",       "division": "NL Central", "league": "NL"},
    {"name": "Timber Timberwolves","abbrev":"TTW","city":"Timberland",  "division": "NL Central", "league": "NL"},
    # NL West
    {"name": "Goldcoast Gulls","abbrev": "GCG", "city": "Goldcoast",   "division": "NL West",    "league": "NL"},
    {"name": "Summit Stallions","abbrev":"SUM", "city": "Summit",       "division": "NL West",    "league": "NL"},
    {"name": "Shoreline Sharks","abbrev":"SHK", "city": "Shoreline",    "division": "NL West",    "league": "NL"},
    {"name": "Valleyview Vipers","abbrev":"VVV","city": "Valleyview",   "division": "NL West",    "league": "NL"},
    {"name": "Brushfire Bears","abbrev": "BFB", "city": "Brushfire",   "division": "NL West",    "league": "NL"},
]

POSITIONS = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C", "P"]

_FIRST_NAMES = [
    "Alex","Blake","Casey","Drew","Evan","Flynn","Grant","Hayes","Ivan","Jared",
    "Kyle","Lance","Mason","Nolan","Owen","Perry","Quinn","Ryder","Scott","Tyler",
    "Upton","Vance","Wade","Xavier","York","Zane","Aaron","Brett","Cole","Derek",
    "Ellis","Frank","Gary","Hank","Isaac","Joel","Kent","Lloyd","Marco","Nash",
    "Omar","Pablo","Randy","Stan","Todd","Ulric","Virgil","Walt","Xander","Yuri",
]

_LAST_NAMES = [
    "Adams","Baker","Clark","Davis","Evans","Ford","Green","Harris","Irving","Jones",
    "King","Lewis","Moore","Nelson","Owen","Park","Quinn","Reed","Smith","Taylor",
    "Upton","Vance","Walker","Xiong","Young","Zhang","Allen","Brown","Cole","Drake",
    "Ellis","Flynn","Grant","Hayes","Irwin","James","Knox","Lane","Myers","Nash",
    "Ortiz","Price","Ruiz","Stone","Torres","Urwin","Vega","Wood","Xu","York",
]

_JOKER_NAMES = [
    "The Ace","The Blaze","The Clutch","The Dart","The Edge",
    "The Flame","The Ghost","The Hawk","The Ice","The Joker",
    "The King","The Legend","The Maverick","The Nail","The Oracle",
    "The Phantom","The Quick","The Rock","The Storm","The Titan",
    "The Ultra","The Viper","The Wild","The X-Factor","The Yankee",
    "The Zenith","The Arrow","The Baron","The Cobra","The Dagger",
]


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def generate_players(team_idx: int, rng: random.Random) -> list[dict]:
    """
    Generate 12 players for a team (9 position + 3 jokers).
    team_idx influences the skill distribution to give each team personality.
    """
    used_names: set[str] = set()

    def _name() -> str:
        for _ in range(100):
            n = f"{rng.choice(_FIRST_NAMES)} {rng.choice(_LAST_NAMES)}"
            if n not in used_names:
                used_names.add(n)
                return n
        return f"Player {rng.randint(100, 999)}"

    # Each team has a "profile" that biases skill attributes.
    profile = team_idx % 5
    skill_base   = [0.52, 0.50, 0.54, 0.48, 0.51][profile]
    speed_base   = [0.52, 0.60, 0.48, 0.55, 0.50][profile]
    pitcher_base = [0.54, 0.48, 0.52, 0.56, 0.50][profile]

    players = []
    for i, pos in enumerate(POSITIONS):
        is_p = (pos == "P")
        skill  = _clamp(rng.gauss(skill_base,   0.10))
        speed  = _clamp(rng.gauss(speed_base,   0.12))
        pskill = _clamp(rng.gauss(pitcher_base, 0.12)) if is_p else _clamp(rng.gauss(0.35, 0.08))
        stay_a = _clamp(rng.gauss(0.40, 0.12))
        cqt    = _clamp(rng.gauss(0.45, 0.08))
        players.append({
            "name": _name(),
            "position": pos,
            "is_pitcher": int(is_p),
            "is_joker": 0,
            "skill": round(skill, 3),
            "speed": round(speed, 3),
            "pitcher_skill": round(pskill, 3),
            "stay_aggressiveness": round(stay_a, 3),
            "contact_quality_threshold": round(cqt, 3),
        })

    # 3 jokers
    joker_names_used: set[str] = set()
    for j in range(3):
        jname = rng.choice(_JOKER_NAMES)
        while jname in joker_names_used:
            jname = rng.choice(_JOKER_NAMES)
        joker_names_used.add(jname)
        skill  = _clamp(rng.gauss(0.62, 0.08))   # jokers skew higher
        speed  = _clamp(rng.gauss(0.60, 0.10))
        pskill = _clamp(rng.gauss(0.40, 0.10))
        stay_a = _clamp(rng.gauss(0.50, 0.12))
        cqt    = _clamp(rng.gauss(0.40, 0.08))
        players.append({
            "name": jname,
            "position": "JKR",
            "is_pitcher": 0,
            "is_joker": 1,
            "skill": round(skill, 3),
            "speed": round(speed, 3),
            "pitcher_skill": round(pskill, 3),
            "stay_aggressiveness": round(stay_a, 3),
            "contact_quality_threshold": round(cqt, 3),
        })
    return players


def seed_league(rng_seed: int = 42) -> None:
    """
    Insert 30 teams and their players into the database.
    Safe to call only once (checks for existing data first).
    """
    from o27v2 import db
    existing = db.fetchone("SELECT COUNT(*) as n FROM teams")
    if existing and existing["n"] > 0:
        return

    rng = random.Random(rng_seed)
    for idx, t in enumerate(TEAMS):
        team_id = db.execute(
            "INSERT INTO teams (name, abbrev, city, division, league) VALUES (?,?,?,?,?)",
            (t["name"], t["abbrev"], t["city"], t["division"], t["league"]),
        )
        players = generate_players(idx, rng)
        db.executemany(
            """INSERT INTO players
               (team_id, name, position, is_pitcher, is_joker, skill, speed,
                pitcher_skill, stay_aggressiveness, contact_quality_threshold)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [(team_id, p["name"], p["position"], p["is_pitcher"], p["is_joker"],
              p["skill"], p["speed"], p["pitcher_skill"],
              p["stay_aggressiveness"], p["contact_quality_threshold"])
             for p in players],
        )
