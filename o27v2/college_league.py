"""
College league infrastructure — programs, conferences, schedule, sim,
postseason, annual rollover.

Layers on top of `o27v2.college` (which owns the player data model and
engine adapter). This module owns:

  Programs / conferences  — 64 programs across 8 conferences,
                            regional+conference identity for postseason
  Schedule                — 55-game weekend-series season (Fri/Sat/Sun
                            cadence; ~70% conference / ~30% non-conf)
  Game sim                — runs a college game through the engine and
                            writes the box to the DB
  Postseason              — Regional (16 four-team double-elim) →
                            Super-Regional (8 best-of-3) → CWS (8-team
                            double-elim, the WCWS analog)
  Annual rollover         — seniors graduate (flagged for the pro draft),
                            other classes age up, fresh freshmen roll in

Tables (see init_college_schema):

  college_programs            id, name, short_name, conference, region,
                              home_city, lat, lon, season
  college_players             id, program_id, name, position, college_year,
                              + all the engine grades, + the hidden
                              potential / access / interest / fog fields
  college_games               id, season, game_date, home/away programs,
                              scores, played, phase, bracket_meta
  college_batter_stats        per-game per-player line
  college_pitcher_stats       per-game per-player line
  college_scouting_reports    season, player_id, source ('service' or
                              'team:<n>'), per-attribute grades
  college_meta                season, phase
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta

from o27v2 import db
from o27v2 import college as _cg


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_PROGRAMS = """
CREATE TABLE IF NOT EXISTS college_programs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    short_name  TEXT,
    conference  TEXT,
    region      TEXT,
    home_city   TEXT,
    lat         REAL,
    lon         REAL,
    season      INTEGER,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

_SCHEMA_PLAYERS = """
CREATE TABLE IF NOT EXISTS college_players (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id  INTEGER NOT NULL REFERENCES college_programs(id),
    name        TEXT NOT NULL,
    position    TEXT,
    country     TEXT,
    hometown    TEXT,
    bats        TEXT DEFAULT 'R',
    throws      TEXT DEFAULT 'R',
    is_pitcher  INTEGER DEFAULT 0,
    is_joker    INTEGER DEFAULT 0,
    roster_slot TEXT DEFAULT '',
    is_active   INTEGER DEFAULT 1,
    college_year INTEGER DEFAULT 1,
    -- Displayed (engine sees these)
    skill INTEGER, contact INTEGER, power INTEGER, eye INTEGER, speed INTEGER,
    pitcher_skill INTEGER, command INTEGER, movement INTEGER, stamina INTEGER,
    defense INTEGER, arm INTEGER, baserunning INTEGER, run_aggressiveness INTEGER,
    defense_infield INTEGER, defense_outfield INTEGER, defense_catcher INTEGER,
    archetype TEXT DEFAULT '',
    hard_contact_delta REAL DEFAULT 0.0,
    hr_weight_bonus REAL DEFAULT 0.0,
    stay_aggressiveness REAL DEFAULT 0.30,
    contact_quality_threshold REAL DEFAULT 0.50,
    -- Hidden (the lens, fog, interest, true potential)
    potential_skill REAL, potential_contact REAL, potential_power REAL,
    potential_eye REAL, potential_speed REAL,
    potential_pitcher_skill REAL, potential_command REAL,
    potential_movement REAL, potential_stamina REAL,
    access_skill REAL, access_contact REAL, access_power REAL,
    access_eye REAL, access_speed REAL,
    access_pitcher_skill REAL, access_command REAL,
    access_movement REAL, access_stamina REAL,
    interest_rate_percent INTEGER,
    fog_magnitude INTEGER,
    -- Career
    graduated INTEGER DEFAULT 0,
    signed_pro_player_id INTEGER DEFAULT NULL
)
"""

_SCHEMA_GAMES = """
CREATE TABLE IF NOT EXISTS college_games (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    season          INTEGER NOT NULL,
    game_date       TEXT NOT NULL,
    home_program_id INTEGER NOT NULL REFERENCES college_programs(id),
    away_program_id INTEGER NOT NULL REFERENCES college_programs(id),
    home_score      INTEGER DEFAULT 0,
    away_score      INTEGER DEFAULT 0,
    played          INTEGER DEFAULT 0,
    phase           TEXT DEFAULT 'regular',
    bracket_meta    TEXT DEFAULT ''
)
"""

_SCHEMA_BATTER_STATS = """
CREATE TABLE IF NOT EXISTS college_batter_stats (
    game_id    INTEGER NOT NULL REFERENCES college_games(id),
    program_id INTEGER NOT NULL,
    player_id  INTEGER NOT NULL REFERENCES college_players(id),
    pa INTEGER, ab INTEGER, h INTEGER, doubles INTEGER, triples INTEGER, hr INTEGER,
    rbi INTEGER, r INTEGER, bb INTEGER, k INTEGER, sb INTEGER, cs INTEGER,
    PRIMARY KEY (game_id, player_id)
)
"""

_SCHEMA_PITCHER_STATS = """
CREATE TABLE IF NOT EXISTS college_pitcher_stats (
    game_id    INTEGER NOT NULL REFERENCES college_games(id),
    program_id INTEGER NOT NULL,
    player_id  INTEGER NOT NULL REFERENCES college_players(id),
    outs INTEGER, h INTEGER, r INTEGER, er INTEGER, bb INTEGER, k INTEGER, hr INTEGER,
    bf INTEGER,
    PRIMARY KEY (game_id, player_id)
)
"""

_SCHEMA_REPORTS = """
CREATE TABLE IF NOT EXISTS college_scouting_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season      INTEGER NOT NULL,
    player_id   INTEGER NOT NULL REFERENCES college_players(id),
    source      TEXT NOT NULL,
    grade_skill INTEGER, grade_contact INTEGER, grade_power INTEGER,
    grade_eye INTEGER, grade_speed INTEGER,
    grade_pitcher_skill INTEGER, grade_command INTEGER,
    grade_movement INTEGER, grade_stamina INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(season, player_id, source)
)
"""

_SCHEMA_META = """
CREATE TABLE IF NOT EXISTS college_meta (
    season     INTEGER PRIMARY KEY,
    phase      TEXT NOT NULL DEFAULT 'regular',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


def init_college_schema() -> None:
    for sql in (_SCHEMA_PROGRAMS, _SCHEMA_PLAYERS, _SCHEMA_GAMES,
                _SCHEMA_BATTER_STATS, _SCHEMA_PITCHER_STATS,
                _SCHEMA_REPORTS, _SCHEMA_META):
        db.execute(sql)


# ---------------------------------------------------------------------------
# Conference + program catalogue
# ---------------------------------------------------------------------------
#
# 64 programs across 8 conferences × 8 teams each. Conferences are
# regional analogs to real NCAA softball powers. Each conference also
# carries a `region` tag used to seed postseason regionals (so postseason
# brackets cluster geographically the way the actual tournament does).

# (conference_name, region_tag, [(program_name, short, home_city, lat, lon), ...])
# Real NCAA conferences with real schools, embracing the 2024+ realignment
# (SEC adds Texas + Oklahoma; Big Ten adds UCLA/USC/Oregon/Washington;
# Big 12 adds BYU/UCF/Houston/Cincinnati + the four ex-Pac-12 schools;
# "new Pac-12" rebuilt around WSU/OSU). Includes academic D3 powers
# (UAA, Centennial w/ Johns Hopkins, NESCAC) and schools that have
# dropped baseball but are real conference members (Wyoming in MW, etc).
# Not every school in this catalog actually plays D1 baseball today;
# inclusion is by real conference membership, not by sport.
_CONFERENCES: list[tuple[str, str, list[tuple[str, str, str, float, float]]]] = [
    ("SEC", "South", [
        ("Florida Gators",                 "UF",  "Gainesville FL",     29.65,  -82.34),
        ("Alabama Crimson Tide",           "BAMA","Tuscaloosa AL",      33.21,  -87.55),
        ("LSU Tigers",                     "LSU", "Baton Rouge LA",     30.45,  -91.18),
        ("Tennessee Volunteers",           "TEN", "Knoxville TN",       35.95,  -83.92),
        ("Georgia Bulldogs",               "UGA", "Athens GA",          33.96,  -83.38),
        ("Auburn Tigers",                  "AUB", "Auburn AL",          32.61,  -85.48),
        ("Ole Miss Rebels",                "MISS","Oxford MS",          34.37,  -89.52),
        ("Mississippi State Bulldogs",     "MSST","Starkville MS",      33.46,  -88.79),
        ("Texas A&M Aggies",               "TAM", "College Station TX", 30.62,  -96.34),
        ("Arkansas Razorbacks",            "ARK", "Fayetteville AR",    36.07,  -94.17),
        ("South Carolina Gamecocks",       "SC",  "Columbia SC",        34.00,  -81.03),
        ("Kentucky Wildcats",              "UK",  "Lexington KY",       38.04,  -84.50),
        ("Missouri Tigers",                "MIZ", "Columbia MO",        38.95,  -92.33),
        ("Vanderbilt Commodores",          "VAN", "Nashville TN",       36.14,  -86.80),
        ("Texas Longhorns",                "TEX", "Austin TX",          30.27,  -97.74),
        ("Oklahoma Sooners",               "OU",  "Norman OK",          35.22,  -97.45),
    ]),
    ("ACC", "Mid-Atlantic", [
        ("Florida State Seminoles",        "FSU", "Tallahassee FL",     30.43,  -84.28),
        ("Virginia Tech Hokies",           "VT",  "Blacksburg VA",      37.23,  -80.42),
        ("North Carolina Tar Heels",       "UNC", "Chapel Hill NC",     35.91,  -79.05),
        ("NC State Wolfpack",              "NCST","Raleigh NC",         35.78,  -78.67),
        ("Duke Blue Devils",               "DUKE","Durham NC",          36.00,  -78.94),
        ("Wake Forest Demon Deacons",      "WAKE","Winston-Salem NC",   36.13,  -80.27),
        ("Clemson Tigers",                 "CLEM","Clemson SC",         34.68,  -82.84),
        ("Notre Dame Fighting Irish",      "ND",  "South Bend IN",      41.70,  -86.24),
        ("Boston College Eagles",          "BC",  "Chestnut Hill MA",   42.34,  -71.17),
        ("Pittsburgh Panthers",            "PITT","Pittsburgh PA",      40.44,  -79.96),
        ("Louisville Cardinals",           "LOU", "Louisville KY",      38.21,  -85.76),
        ("Virginia Cavaliers",             "UVA", "Charlottesville VA", 38.03,  -78.51),
        ("Georgia Tech Yellow Jackets",    "GT",  "Atlanta GA",         33.78,  -84.40),
        ("Miami Hurricanes",               "MIA", "Coral Gables FL",    25.72,  -80.28),
        ("Syracuse Orange",                "CUSE","Syracuse NY",        43.04,  -76.13),
        ("Stanford Cardinal",              "STAN","Stanford CA",        37.43, -122.17),
        ("California Golden Bears",        "CAL", "Berkeley CA",        37.87, -122.26),
        ("SMU Mustangs",                   "SMU", "Dallas TX",          32.84,  -96.78),
    ]),
    ("Big 12", "Plains", [
        ("Oklahoma State Cowboys",         "OKST","Stillwater OK",      36.12,  -97.07),
        ("Baylor Bears",                   "BAY", "Waco TX",            31.55,  -97.12),
        ("Iowa State Cyclones",            "ISU", "Ames IA",            42.03,  -93.61),
        ("Kansas Jayhawks",                "KU",  "Lawrence KS",        38.97,  -95.24),
        ("Kansas State Wildcats",          "KSU", "Manhattan KS",       39.19,  -96.58),
        ("Texas Tech Red Raiders",         "TTU", "Lubbock TX",         33.58, -101.88),
        ("TCU Horned Frogs",               "TCU", "Fort Worth TX",      32.71,  -97.36),
        ("West Virginia Mountaineers",     "WVU", "Morgantown WV",      39.65,  -79.96),
        ("Cincinnati Bearcats",            "CIN", "Cincinnati OH",      39.13,  -84.52),
        ("BYU Cougars",                    "BYU", "Provo UT",           40.25, -111.65),
        ("UCF Knights",                    "UCF", "Orlando FL",         28.60,  -81.20),
        ("Houston Cougars",                "HOU", "Houston TX",         29.72,  -95.34),
        ("Arizona Wildcats",               "ARIZ","Tucson AZ",          32.23, -110.96),
        ("Arizona State Sun Devils",       "ASU", "Tempe AZ",           33.42, -111.93),
        ("Utah Utes",                      "UTAH","Salt Lake City UT",  40.76, -111.84),
        ("Colorado Buffaloes",             "COLO","Boulder CO",         40.01, -105.27),
    ]),
    ("Big Ten", "Midwest", [
        ("Michigan Wolverines",            "MICH","Ann Arbor MI",       42.27,  -83.74),
        ("Michigan State Spartans",        "MSU", "East Lansing MI",    42.72,  -84.48),
        ("Ohio State Buckeyes",            "OSU", "Columbus OH",        40.00,  -83.01),
        ("Northwestern Wildcats",          "NW",  "Evanston IL",        42.05,  -87.69),
        ("Wisconsin Badgers",              "WIS", "Madison WI",         43.07,  -89.40),
        ("Minnesota Golden Gophers",       "MINN","Minneapolis MN",     44.97,  -93.23),
        ("Nebraska Cornhuskers",           "NEB", "Lincoln NE",         40.81,  -96.70),
        ("Iowa Hawkeyes",                  "IOWA","Iowa City IA",       41.66,  -91.54),
        ("Indiana Hoosiers",               "IND", "Bloomington IN",     39.17,  -86.52),
        ("Illinois Fighting Illini",       "ILL", "Champaign IL",       40.10,  -88.23),
        ("Purdue Boilermakers",            "PUR", "West Lafayette IN",  40.42,  -86.92),
        ("Penn State Nittany Lions",       "PSU", "University Park PA", 40.80,  -77.86),
        ("Maryland Terrapins",             "MD",  "College Park MD",    38.99,  -76.94),
        ("Rutgers Scarlet Knights",        "RU",  "Piscataway NJ",      40.50,  -74.45),
        ("UCLA Bruins",                    "UCLA","Los Angeles CA",     34.07, -118.45),
        ("USC Trojans",                    "USC", "Los Angeles CA",     34.02, -118.29),
        ("Oregon Ducks",                   "ORE", "Eugene OR",          44.05, -123.07),
        ("Washington Huskies",             "WASH","Seattle WA",         47.66, -122.31),
    ]),
    ("Pac-12 (new)", "Pacific", [
        # The reconstituted Pac-12 after 10 schools left in 2024.
        ("Washington State Cougars",       "WSU", "Pullman WA",         46.73, -117.18),
        ("Oregon State Beavers",           "OSUB","Corvallis OR",       44.57, -123.28),
        ("Boise State Broncos",            "BSU", "Boise ID",           43.62, -116.20),
        ("Colorado State Rams",            "CSU", "Fort Collins CO",    40.57, -105.08),
        ("Fresno State Bulldogs",          "FRES","Fresno CA",          36.81, -119.74),
        ("San Diego State Aztecs",         "SDSU","San Diego CA",       32.77, -117.07),
        ("Utah State Aggies",              "USU", "Logan UT",           41.74, -111.81),
        ("Gonzaga Bulldogs",               "GONZ","Spokane WA",         47.67, -117.40),
    ]),
    ("Mountain West", "Mountain", [
        # Post-Pac-12 raid: smaller, more inland focus. Includes Wyoming —
        # dropped baseball in 1996 but remains a real MW member.
        ("UNLV Rebels",                    "UNLV","Las Vegas NV",       36.11, -115.14),
        ("Wyoming Cowboys",                "WYO", "Laramie WY",         41.31, -105.59),
        ("Air Force Falcons",              "AF",  "Colorado Springs CO",38.99, -104.86),
        ("Nevada Wolf Pack",               "NEV", "Reno NV",            39.54, -119.81),
        ("New Mexico Lobos",               "UNM", "Albuquerque NM",     35.08, -106.62),
        ("San José State Spartans",        "SJSU","San Jose CA",        37.34, -121.88),
        ("Northern Illinois Huskies",      "NIU", "DeKalb IL",          41.93,  -88.78),
        ("Hawaii Rainbow Warriors",        "HAW", "Honolulu HI",        21.31, -157.82),
    ]),
    ("Big West", "Pacific", [
        ("Long Beach State Beach",         "LBS", "Long Beach CA",      33.78, -118.11),
        ("Cal State Fullerton Titans",     "CSF", "Fullerton CA",       33.88, -117.88),
        ("UC Santa Barbara Gauchos",       "UCSB","Santa Barbara CA",   34.41, -119.85),
        ("UC Davis Aggies",                "UCD", "Davis CA",           38.54, -121.74),
        ("UC Irvine Anteaters",            "UCI", "Irvine CA",          33.65, -117.84),
        ("Cal Poly Mustangs",              "CP",  "San Luis Obispo CA", 35.30, -120.66),
        ("UC Riverside Highlanders",       "UCR", "Riverside CA",       33.97, -117.33),
        ("UC San Diego Tritons",           "UCSD","La Jolla CA",        32.88, -117.23),
    ]),
    ("American", "Southeast", [
        ("South Florida Bulls",            "USF", "Tampa FL",           28.06,  -82.41),
        ("Wichita State Shockers",         "WSU2","Wichita KS",         37.72,  -97.29),
        ("Memphis Tigers",                 "MEM", "Memphis TN",         35.12,  -90.00),
        ("Tulane Green Wave",              "TULN","New Orleans LA",     29.94,  -90.12),
        ("Tulsa Golden Hurricane",         "TUL", "Tulsa OK",           36.15,  -95.95),
        ("East Carolina Pirates",          "ECU", "Greenville NC",      35.61,  -77.37),
        ("Temple Owls",                    "TEMP","Philadelphia PA",    39.98,  -75.16),
        ("FAU Owls",                       "FAU", "Boca Raton FL",      26.37,  -80.10),
        ("Charlotte 49ers",                "CLT", "Charlotte NC",       35.31,  -80.74),
        ("North Texas Mean Green",         "UNT", "Denton TX",          33.20,  -97.13),
        ("Rice Owls",                      "RICE","Houston TX",         29.72,  -95.40),
        ("UAB Blazers",                    "UAB", "Birmingham AL",      33.50,  -86.81),
    ]),
    ("Sun Belt", "Gulf", [
        ("Coastal Carolina Chanticleers",  "CCU", "Conway SC",          33.79,  -79.01),
        ("Louisiana Ragin' Cajuns",        "ULL", "Lafayette LA",       30.21,  -92.02),
        ("South Alabama Jaguars",          "USA", "Mobile AL",          30.69,  -88.18),
        ("Texas State Bobcats",            "TXST","San Marcos TX",      29.88,  -97.94),
        ("Troy Trojans",                   "TROY","Troy AL",            31.81,  -85.97),
        ("Arkansas State Red Wolves",      "ARST","Jonesboro AR",       35.84,  -90.70),
        ("Appalachian State Mountaineers", "APP", "Boone NC",           36.21,  -81.68),
        ("Georgia State Panthers",         "GSU", "Atlanta GA",         33.75,  -84.39),
        ("Georgia Southern Eagles",        "GASO","Statesboro GA",      32.42,  -81.78),
        ("Marshall Thundering Herd",       "MRSH","Huntington WV",      38.42,  -82.43),
        ("James Madison Dukes",            "JMU", "Harrisonburg VA",    38.43,  -78.87),
        ("Old Dominion Monarchs",          "ODU", "Norfolk VA",         36.89,  -76.30),
    ]),
    ("Conference USA", "South", [
        ("Liberty Flames",                 "LIB", "Lynchburg VA",       37.35,  -79.17),
        ("FIU Panthers",                   "FIU", "Miami FL",           25.76,  -80.37),
        ("Jacksonville State Gamecocks",   "JVST","Jacksonville AL",    33.82,  -85.77),
        ("Kennesaw State Owls",            "KSU2","Kennesaw GA",        34.04,  -84.58),
        ("Middle Tennessee Blue Raiders",  "MT",  "Murfreesboro TN",    35.85,  -86.39),
        ("New Mexico State Aggies",        "NMST","Las Cruces NM",      32.28, -106.74),
        ("Sam Houston Bearkats",           "SHSU","Huntsville TX",      30.71,  -95.55),
        ("UTEP Miners",                    "UTEP","El Paso TX",         31.77, -106.50),
        ("Western Kentucky Hilltoppers",   "WKU", "Bowling Green KY",   36.99,  -86.46),
    ]),
    ("Atlantic 10", "Northeast", [
        ("Davidson Wildcats",              "DAV", "Davidson NC",        35.50,  -80.84),
        ("Dayton Flyers",                  "DAY", "Dayton OH",          39.74,  -84.18),
        ("Fordham Rams",                   "FOR", "Bronx NY",           40.86,  -73.88),
        ("George Mason Patriots",          "GMU", "Fairfax VA",         38.83,  -77.31),
        ("George Washington Revolutionaries","GW","Washington DC",      38.90,  -77.05),
        ("UMass Minutemen",                "UMS", "Amherst MA",         42.39,  -72.53),
        ("Rhode Island Rams",              "URI", "Kingston RI",        41.48,  -71.53),
        ("Richmond Spiders",               "RICH","Richmond VA",        37.58,  -77.54),
        ("Saint Joseph's Hawks",           "SJU", "Philadelphia PA",    40.03,  -75.24),
        ("Saint Louis Billikens",          "SLU", "St. Louis MO",       38.64,  -90.23),
        ("VCU Rams",                       "VCU", "Richmond VA",        37.55,  -77.45),
        ("Loyola Chicago Ramblers",        "LUC", "Chicago IL",         41.99,  -87.66),
    ]),
    ("Missouri Valley", "Midwest", [
        ("Bradley Braves",                 "BRAD","Peoria IL",          40.70,  -89.62),
        ("Drake Bulldogs",                 "DRK", "Des Moines IA",      41.60,  -93.65),
        ("Evansville Aces",                "EVA", "Evansville IN",      37.97,  -87.67),
        ("Illinois State Redbirds",        "ILST","Normal IL",          40.51,  -88.99),
        ("Indiana State Sycamores",        "INST","Terre Haute IN",     39.47,  -87.41),
        ("Missouri State Bears",           "MOST","Springfield MO",     37.21,  -93.29),
        ("Murray State Racers",            "MUR", "Murray KY",          36.62,  -88.31),
        ("Northern Iowa Panthers",         "UNI", "Cedar Falls IA",     42.51,  -92.46),
        ("Southern Illinois Salukis",      "SIU", "Carbondale IL",      37.71,  -89.22),
        ("Valparaiso Beacons",             "VAL", "Valparaiso IN",      41.46,  -87.05),
    ]),
    ("Big East", "Northeast", [
        ("Butler Bulldogs",                "BUT", "Indianapolis IN",    39.84,  -86.17),
        ("Creighton Bluejays",             "CREI","Omaha NE",           41.27,  -95.94),
        ("Georgetown Hoyas",               "GU",  "Washington DC",      38.91,  -77.07),
        ("St. John's Red Storm",           "STJ", "Queens NY",          40.72,  -73.79),
        ("Seton Hall Pirates",             "SHU", "South Orange NJ",    40.74,  -74.24),
        ("Villanova Wildcats",             "NOVA","Villanova PA",       40.04,  -75.34),
        ("Xavier Musketeers",              "XAV", "Cincinnati OH",      39.15,  -84.47),
        ("Providence Friars",              "PROV","Providence RI",      41.84,  -71.43),
    ]),
    ("Ivy League", "Northeast", [
        ("Harvard Crimson",                "HARV","Cambridge MA",       42.37,  -71.12),
        ("Yale Bulldogs",                  "YALE","New Haven CT",       41.31,  -72.93),
        ("Princeton Tigers",               "PRIN","Princeton NJ",       40.35,  -74.66),
        ("Columbia Lions",                 "COLU","New York NY",        40.81,  -73.96),
        ("Cornell Big Red",                "COR", "Ithaca NY",          42.45,  -76.48),
        ("Penn Quakers",                   "PENN","Philadelphia PA",    39.95,  -75.19),
        ("Dartmouth Big Green",            "DART","Hanover NH",         43.70,  -72.29),
        ("Brown Bears",                    "BRO", "Providence RI",      41.83,  -71.40),
    ]),
    ("Patriot League", "Mid-Atlantic", [
        ("Army Black Knights",             "ARMY","West Point NY",      41.39,  -73.96),
        ("Navy Midshipmen",                "NAVY","Annapolis MD",       38.98,  -76.49),
        ("Boston University Terriers",     "BU",  "Boston MA",          42.35,  -71.10),
        ("Bucknell Bison",                 "BUCK","Lewisburg PA",       40.95,  -76.88),
        ("Holy Cross Crusaders",           "HC",  "Worcester MA",       42.29,  -71.81),
        ("Lafayette Leopards",             "LAF", "Easton PA",          40.69,  -75.21),
        ("Lehigh Mountain Hawks",          "LEH", "Bethlehem PA",       40.61,  -75.38),
        ("Loyola (MD) Greyhounds",         "LOYM","Baltimore MD",       39.35,  -76.62),
    ]),
    ("UAA (D3)", "Academic", [
        # University Athletic Association — D3 academic powerhouses.
        ("Brandeis Judges",                "BRAN","Waltham MA",         42.37,  -71.26),
        ("Carnegie Mellon Tartans",        "CMU", "Pittsburgh PA",      40.44,  -79.94),
        ("Case Western Spartans",          "CWRU","Cleveland OH",       41.50,  -81.61),
        ("University of Chicago Maroons",  "UCHI","Chicago IL",         41.79,  -87.60),
        ("Emory Eagles",                   "EMO", "Atlanta GA",         33.79,  -84.32),
        ("NYU Violets",                    "NYU", "New York NY",        40.73,  -73.99),
        ("Rochester Yellowjackets",        "ROC", "Rochester NY",       43.13,  -77.63),
        ("Washington University Bears",    "WUSL","St. Louis MO",       38.65,  -90.30),
    ]),
    ("Centennial (D3)", "Academic", [
        # Centennial Conference — Johns Hopkins headlines this academic
        # D3 group; many of these have D1 lacrosse/wrestling but D3 ball.
        ("Johns Hopkins Blue Jays",        "JHU", "Baltimore MD",       39.33,  -76.62),
        ("Swarthmore Garnet",              "SWAR","Swarthmore PA",      39.90,  -75.35),
        ("Haverford Black Squirrels",      "HAV", "Haverford PA",       40.01,  -75.30),
        ("Franklin & Marshall Diplomats",  "F&M", "Lancaster PA",       40.05,  -76.32),
        ("Gettysburg Bullets",             "GBG", "Gettysburg PA",      39.83,  -77.23),
        ("Dickinson Red Devils",           "DICK","Carlisle PA",        40.20,  -77.20),
        ("Muhlenberg Mules",               "MUH", "Allentown PA",       40.59,  -75.51),
        ("Ursinus Bears",                  "URS", "Collegeville PA",    40.19,  -75.46),
    ]),
    ("NESCAC (D3)", "Northeast Academic", [
        # New England small-college academic conference.
        ("Amherst Mammoths",               "AMH", "Amherst MA",         42.37,  -72.51),
        ("Williams Ephs",                  "WIL", "Williamstown MA",    42.71,  -73.20),
        ("Wesleyan Cardinals",             "WES", "Middletown CT",      41.55,  -72.66),
        ("Tufts Jumbos",                   "TUF", "Medford MA",         42.41,  -71.12),
        ("Middlebury Panthers",            "MID", "Middlebury VT",      44.01,  -73.18),
        ("Bowdoin Polar Bears",            "BOW", "Brunswick ME",       43.91,  -69.96),
        ("Bates Bobcats",                  "BATE","Lewiston ME",        44.10,  -70.20),
        ("Colby Mules",                    "COL2","Waterville ME",      44.56,  -69.65),
    ]),
]


def _all_programs_spec() -> list[dict]:
    """Flatten the conference catalogue into 64 program dicts."""
    out = []
    for conf, region, programs in _CONFERENCES:
        for name, short, city, lat, lon in programs:
            out.append({"name": name, "short_name": short, "conference": conf,
                        "region": region, "home_city": city,
                        "lat": lat, "lon": lon})
    return out


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def seed_college_league(season: int, rng_seed: int = 0) -> dict:
    """Idempotent: creates 64 programs and full freshman rosters for
    `season` if not already seeded. Returns a summary dict."""
    init_college_schema()
    existing = db.fetchone(
        "SELECT COUNT(*) AS n FROM college_programs WHERE season = ?", (season,)
    )
    if existing and existing["n"] > 0:
        return {"created_programs": 0, "created_players": 0, "season": season}

    rng = random.Random((rng_seed or 0) ^ 0xC011E6E5)

    n_programs = n_players = 0
    for spec in _all_programs_spec():
        program_id = db.execute(
            "INSERT INTO college_programs (name, short_name, conference, region, "
            "home_city, lat, lon, season) VALUES (?,?,?,?,?,?,?,?)",
            (spec["name"], spec["short_name"], spec["conference"], spec["region"],
             spec["home_city"], spec["lat"], spec["lon"], season),
        )
        n_programs += 1

        roster = _cg.generate_college_roster(rng, spec["short_name"])
        # Distribute college_year across the roster so the first season
        # isn't all freshmen (no one to graduate). Mix: 25% each year.
        for i, p in enumerate(roster):
            target_year = (i % 4) + 1
            # `advance_one_year` increments college_year itself, so just
            # advance target_year-1 times and let it land at target_year.
            for _ in range(target_year - 1):
                _cg.advance_one_year(p)
            _insert_player(program_id, p)
            n_players += 1

    db.execute(
        "INSERT OR REPLACE INTO college_meta (season, phase) VALUES (?, 'regular')",
        (season,),
    )
    return {"created_programs": n_programs,
            "created_players":  n_players,
            "season":           season}


_PLAYER_COLS = (
    "program_id", "name", "position", "country", "hometown", "bats", "throws",
    "is_pitcher", "is_joker", "roster_slot", "is_active", "college_year",
    "skill", "contact", "power", "eye", "speed",
    "pitcher_skill", "command", "movement", "stamina",
    "defense", "arm", "baserunning", "run_aggressiveness",
    "defense_infield", "defense_outfield", "defense_catcher",
    "archetype", "hard_contact_delta", "hr_weight_bonus",
    "stay_aggressiveness", "contact_quality_threshold",
    "potential_skill", "potential_contact", "potential_power",
    "potential_eye", "potential_speed",
    "potential_pitcher_skill", "potential_command",
    "potential_movement", "potential_stamina",
    "access_skill", "access_contact", "access_power",
    "access_eye", "access_speed",
    "access_pitcher_skill", "access_command",
    "access_movement", "access_stamina",
    "interest_rate_percent", "fog_magnitude",
)


def _insert_player(program_id: int, p: dict) -> int:
    """Insert one college player; returns the new id."""
    values = [program_id] + [p.get(c) if c != "program_id" else program_id
                              for c in _PLAYER_COLS[1:]]
    placeholders = ",".join("?" * len(_PLAYER_COLS))
    return db.execute(
        f"INSERT INTO college_players ({','.join(_PLAYER_COLS)}) VALUES ({placeholders})",
        tuple(values),
    )


# ---------------------------------------------------------------------------
# Schedule generation — 55-game weekend series
# ---------------------------------------------------------------------------
#
# Each conference team plays:
#   * One weekend series (Fri/Sat/Sun = 3 games) vs each other conference
#     opponent → 7 opponents × 3 = 21 conference games.
#   * Mid-week single games vs non-conference opponents → fills the rest
#     of the 55-game target. Roughly 50/50 home/away balance.

def generate_schedule(season: int, start_date: str = None) -> int:
    """Generate the season's games and insert them as `played=0` rows.
    Idempotent — early-returns if games already exist for the season."""
    init_college_schema()
    existing = db.fetchone(
        "SELECT COUNT(*) AS n FROM college_games WHERE season = ?", (season,)
    )
    if existing and existing["n"] > 0:
        return 0

    progs = db.fetchall(
        "SELECT id, name, conference FROM college_programs "
        "WHERE season = ? ORDER BY id", (season,)
    )
    by_conf: dict[str, list] = {}
    for p in progs:
        by_conf.setdefault(p["conference"], []).append(p["id"])

    # Default start: April 1 of `season` year. The college season runs
    # ~12 weekends. Weekend series Fri/Sat/Sun + mid-week single games.
    if start_date:
        d0 = date.fromisoformat(start_date)
    else:
        d0 = date(season, 4, 1)
    # Find the first Friday ≥ d0
    while d0.weekday() != 4:   # 4 = Friday
        d0 += timedelta(days=1)

    rng = random.Random(season ^ 0x5CED0001)
    games: list[tuple] = []

    # --- Conference weekend series ---
    # Round-robin within each conference: each pair plays one 3-game
    # series. 8 teams = 7 opponents per team = 7 weekends used for
    # conference play.
    weekend_idx = 0
    for conf, ids in by_conf.items():
        ids = list(ids)
        pairs = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.append((ids[i], ids[j]))
        rng.shuffle(pairs)
        # Each conference uses its own weekend cadence; teams play one
        # series per weekend. With 8 teams we have 7 round-robin rounds.
        # Distribute pairs across the conference weekends.
        rounds: list[list[tuple]] = _round_robin_rounds(ids)
        for rnd_idx, rnd in enumerate(rounds):
            friday = d0 + timedelta(days=7 * rnd_idx)
            for (a, b) in rnd:
                home, away = (a, b) if rng.random() < 0.5 else (b, a)
                for offset in (0, 1, 2):  # Fri/Sat/Sun
                    g_date = (friday + timedelta(days=offset)).isoformat()
                    games.append((season, g_date, home, away, "regular"))

    # --- Non-conference mid-week games ---
    # Tue/Wed singles for the first ~12 weeks. Random opponents from a
    # different conference, ~5-7 of these per team to hit 55 games.
    all_prog_ids = [p["id"] for p in progs]
    prog_to_conf = {p["id"]: p["conference"] for p in progs}
    # Each team needs 55 - 21 = 34 more games. With 2 mid-week games × 12 weeks
    # = 24 mid-week slots, plus some weekend non-conf series early in the
    # season. We do mid-week games for 12 weeks and add some non-conf weekend
    # series at the end of the slate to bring totals close to 55.
    target_extra_per_team = 34
    extras: dict[int, int] = {pid: 0 for pid in all_prog_ids}

    week = 0
    while min(extras.values()) < target_extra_per_team and week < 24:
        # Tuesday + Wednesday of `week`
        for dow_offset in (-3, -2):  # Tue, Wed before the Friday
            g_date = (d0 + timedelta(days=7 * week + dow_offset)).isoformat()
            # Pair up programs that still need games, cross-conference preferred
            need = sorted([pid for pid, n in extras.items()
                           if n < target_extra_per_team],
                          key=lambda pid: extras[pid])
            rng.shuffle(need)
            used: set[int] = set()
            for pid in need:
                if pid in used: continue
                # Find an opponent in a different conference still needing games
                opp = None
                for oid in need:
                    if oid == pid or oid in used: continue
                    if prog_to_conf[oid] != prog_to_conf[pid]:
                        opp = oid; break
                if opp is None:
                    for oid in need:
                        if oid == pid or oid in used: continue
                        opp = oid; break
                if opp is None:
                    continue
                home, away = (pid, opp) if rng.random() < 0.5 else (opp, pid)
                games.append((season, g_date, home, away, "regular"))
                extras[pid] += 1
                extras[opp] += 1
                used.add(pid); used.add(opp)
        week += 1

    # Bulk insert
    db.executemany(
        "INSERT INTO college_games (season, game_date, home_program_id, "
        "away_program_id, phase) VALUES (?,?,?,?,?)",
        games,
    )
    return len(games)


def _round_robin_rounds(ids: list[int]) -> list[list[tuple[int, int]]]:
    """Standard circle round-robin — returns list of rounds, each a list
    of (home, away) pairs. For n=8: 7 rounds × 4 pairs each."""
    n = len(ids)
    if n % 2 == 1:
        ids = ids + [-1]   # bye marker
        n += 1
    rounds = []
    arr = list(ids)
    for r in range(n - 1):
        rnd = []
        for i in range(n // 2):
            a, b = arr[i], arr[n - 1 - i]
            if a != -1 and b != -1:
                rnd.append((a, b))
        rounds.append(rnd)
        # Rotate (keep first fixed)
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]
    return rounds


# ---------------------------------------------------------------------------
# Game sim
# ---------------------------------------------------------------------------

def _roster_for_program(program_id: int) -> list[dict]:
    rows = db.fetchall(
        "SELECT * FROM college_players WHERE program_id = ? AND is_active = 1",
        (program_id,),
    )
    return [dict(r) for r in rows]


def sim_game(game_id: int, rng_seed: int = 0) -> dict:
    """Sim one scheduled college game; write score + per-player box to DB."""
    game = db.fetchone("SELECT * FROM college_games WHERE id = ?", (game_id,))
    if game is None:
        raise ValueError(f"college game {game_id} not found")
    if game["played"]:
        return {"already_played": True, "game_id": game_id}

    home_prog = db.fetchone("SELECT * FROM college_programs WHERE id = ?",
                            (game["home_program_id"],))
    away_prog = db.fetchone("SELECT * FROM college_programs WHERE id = ?",
                            (game["away_program_id"],))
    home_roster = _roster_for_program(game["home_program_id"])
    away_roster = _roster_for_program(game["away_program_id"])
    if not home_roster or not away_roster:
        raise ValueError(f"missing roster for game {game_id}")

    rng = random.Random(rng_seed ^ game_id)
    final, renderer = _cg.sim_college_game(
        home_prog["name"], home_roster,
        away_prog["name"], away_roster,
        rng=rng, return_renderer=True,
    )
    home_score = final.score["home"]
    away_score = final.score["visitors"]

    db.execute(
        "UPDATE college_games SET home_score = ?, away_score = ?, played = 1 "
        "WHERE id = ?",
        (home_score, away_score, game_id),
    )

    # Per-player box rows — engine player_id is the str() of college_player.id
    home_pids = {int(p["id"]) for p in home_roster}
    away_pids = {int(p["id"]) for p in away_roster}
    _persist_batter_rows(game_id, game["home_program_id"], home_pids, renderer)
    _persist_batter_rows(game_id, game["away_program_id"], away_pids, renderer)
    _persist_pitcher_rows(game_id, game["home_program_id"], home_pids, final)
    _persist_pitcher_rows(game_id, game["away_program_id"], away_pids, final)

    return {"game_id": game_id, "home_score": home_score, "away_score": away_score}


def _persist_batter_rows(game_id: int, program_id: int,
                         player_ids: set[int], renderer) -> None:
    """Extract this side's batter stats from the renderer and write rows."""
    phases = renderer.phases_seen() or [0]
    agg: dict[int, dict] = {}
    for phase in phases:
        try:
            phase_stats = (renderer.batter_stats_for_phase(phase)
                            if renderer.phases_seen()
                            else dict(renderer._batter_stats))
        except Exception:
            phase_stats = dict(renderer._batter_stats)
        for engine_pid, bstat in phase_stats.items():
            try:
                pid = int(engine_pid)
            except (TypeError, ValueError):
                continue
            if pid not in player_ids:
                continue
            row = agg.setdefault(pid, {
                "pa": 0, "ab": 0, "h": 0, "doubles": 0, "triples": 0, "hr": 0,
                "rbi": 0, "r": 0, "bb": 0, "k": 0, "sb": 0, "cs": 0,
            })
            row["pa"]      += getattr(bstat, "pa",       0) or 0
            row["ab"]      += getattr(bstat, "ab",       0) or 0
            row["h"]       += getattr(bstat, "hits",     0) or 0
            row["doubles"] += getattr(bstat, "doubles",  0) or 0
            row["triples"] += getattr(bstat, "triples",  0) or 0
            row["hr"]      += getattr(bstat, "hr",       0) or 0
            row["rbi"]     += getattr(bstat, "rbi",      0) or 0
            row["r"]       += getattr(bstat, "runs",     0) or 0
            row["bb"]      += getattr(bstat, "bb",       0) or 0
            row["k"]       += getattr(bstat, "k",        0) or 0
            row["sb"]      += getattr(bstat, "sb",       0) or 0
            row["cs"]      += getattr(bstat, "cs",       0) or 0
    for pid, row in agg.items():
        db.execute(
            "INSERT OR REPLACE INTO college_batter_stats "
            "(game_id, program_id, player_id, pa, ab, h, doubles, triples, "
            " hr, rbi, r, bb, k, sb, cs) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (game_id, program_id, pid,
             row["pa"], row["ab"], row["h"], row["doubles"], row["triples"],
             row["hr"], row["rbi"], row["r"], row["bb"], row["k"],
             row["sb"], row["cs"]),
        )


def _persist_pitcher_rows(game_id: int, program_id: int,
                          player_ids: set[int], final_state) -> None:
    """Aggregate this side's pitcher records from the spell log; write rows."""
    by_pid: dict[int, list] = {}
    for rec in getattr(final_state, "spell_log", []) or []:
        try:
            pid = int(rec.pitcher_id)
        except (TypeError, ValueError):
            continue
        if pid not in player_ids:
            continue
        by_pid.setdefault(pid, []).append(rec)
    if not by_pid:
        return
    from o27.stats.pitcher import PitcherStats
    for pid, spells in by_pid.items():
        ps = PitcherStats.from_spell_log(spells, str(pid), "")
        runs = getattr(ps, "runs_allowed", 0) or 0
        unearned = getattr(ps, "unearned_runs", 0) or 0
        db.execute(
            "INSERT OR REPLACE INTO college_pitcher_stats "
            "(game_id, program_id, player_id, outs, h, r, er, bb, k, hr, bf) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (game_id, program_id, pid,
             getattr(ps, "outs_recorded", 0) or 0,
             getattr(ps, "hits_allowed", 0) or 0,
             runs, max(0, runs - unearned),
             getattr(ps, "bb", 0) or 0,
             getattr(ps, "k",  0) or 0,
             getattr(ps, "hr_allowed", 0) or 0,
             getattr(ps, "batters_faced", 0) or 0),
        )


def sim_all_unplayed(season: int, rng_seed: int = 0) -> dict:
    """Sim every unplayed regular-season game for `season`."""
    rows = db.fetchall(
        "SELECT id FROM college_games WHERE season = ? AND phase = 'regular' "
        "AND played = 0 ORDER BY game_date, id",
        (season,),
    )
    n = 0
    for r in rows:
        sim_game(r["id"], rng_seed=rng_seed)
        n += 1
    return {"games_played": n, "season": season}


# ---------------------------------------------------------------------------
# Standings + leaders
# ---------------------------------------------------------------------------

def standings(season: int) -> list[dict]:
    """Per-program W-L for the season, sorted within conference by win-pct."""
    rows = db.fetchall(
        """
        SELECT p.id, p.name, p.short_name, p.conference, p.region,
               SUM(CASE WHEN g.played=1 AND g.home_program_id = p.id AND g.home_score > g.away_score THEN 1
                        WHEN g.played=1 AND g.away_program_id = p.id AND g.away_score > g.home_score THEN 1
                        ELSE 0 END) AS wins,
               SUM(CASE WHEN g.played=1 AND g.home_program_id = p.id AND g.home_score < g.away_score THEN 1
                        WHEN g.played=1 AND g.away_program_id = p.id AND g.away_score < g.home_score THEN 1
                        ELSE 0 END) AS losses
          FROM college_programs p
          LEFT JOIN college_games g
            ON (g.home_program_id = p.id OR g.away_program_id = p.id)
           AND g.season = p.season
         WHERE p.season = ?
         GROUP BY p.id
         ORDER BY p.conference, wins DESC, losses ASC, p.short_name
        """,
        (season,),
    )
    out = []
    for r in rows:
        d = dict(r)
        gp = d["wins"] + d["losses"]
        d["games_played"] = gp
        d["pct"] = (d["wins"] / gp) if gp else 0.0
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Stat leaders + per-player season totals
# ---------------------------------------------------------------------------

def batter_leaders(season: int, *, sort: str = "avg",
                   min_pa: int = 50, limit: int = 50) -> list[dict]:
    """Top batters in `season` sorted by `sort` (avg | hr | rbi | h | r)."""
    rows = db.fetchall(
        """SELECT pl.id AS player_id, pl.name, pl.position, pl.college_year,
                  pl.is_pitcher, prg.id AS program_id,
                  prg.short_name AS program_short, prg.name AS program_name,
                  SUM(s.pa)      AS pa,
                  SUM(s.ab)      AS ab,
                  SUM(s.h)       AS h,
                  SUM(s.doubles) AS d,
                  SUM(s.triples) AS t,
                  SUM(s.hr)      AS hr,
                  SUM(s.rbi)     AS rbi,
                  SUM(s.r)       AS r,
                  SUM(s.bb)      AS bb,
                  SUM(s.k)       AS k,
                  SUM(s.sb)      AS sb
             FROM college_batter_stats s
             JOIN college_players  pl  ON pl.id = s.player_id
             JOIN college_programs prg ON prg.id = s.program_id
             JOIN college_games    g   ON g.id  = s.game_id
            WHERE g.season = ? AND pl.is_pitcher = 0
            GROUP BY pl.id
           HAVING pa >= ?""",
        (season, min_pa),
    )
    out = []
    for r in rows:
        d = dict(r)
        ab = d["ab"] or 0
        h  = d["h"]  or 0
        bb = d["bb"] or 0
        d["avg"] = (h / ab) if ab else 0.0
        d["obp"] = ((h + bb) / (ab + bb)) if (ab + bb) else 0.0
        d["slg"] = (((h - (d["d"] or 0) - (d["t"] or 0) - (d["hr"] or 0))
                     + 2 * (d["d"] or 0) + 3 * (d["t"] or 0) + 4 * (d["hr"] or 0))
                     / ab) if ab else 0.0
        d["ops"] = d["obp"] + d["slg"]
        out.append(d)
    key = {"avg": "avg", "hr": "hr", "rbi": "rbi", "h": "h", "r": "r",
           "ops": "ops", "obp": "obp", "slg": "slg"}.get(sort, "avg")
    out.sort(key=lambda x: (x.get(key) or 0), reverse=True)
    return out[:limit]


def pitcher_leaders(season: int, *, sort: str = "era",
                    min_outs: int = 60, limit: int = 50) -> list[dict]:
    """Top pitchers; sort by era (asc) / k (desc) / ip (desc) / w (desc)."""
    rows = db.fetchall(
        """SELECT pl.id AS player_id, pl.name, pl.position, pl.college_year,
                  prg.id AS program_id,
                  prg.short_name AS program_short, prg.name AS program_name,
                  SUM(s.outs) AS outs,
                  SUM(s.h)    AS h,
                  SUM(s.r)    AS r,
                  SUM(s.er)   AS er,
                  SUM(s.bb)   AS bb,
                  SUM(s.k)    AS k,
                  SUM(s.hr)   AS hr,
                  SUM(s.bf)   AS bf
             FROM college_pitcher_stats s
             JOIN college_players  pl  ON pl.id = s.player_id
             JOIN college_programs prg ON prg.id = s.program_id
             JOIN college_games    g   ON g.id  = s.game_id
            WHERE g.season = ? AND pl.is_pitcher = 1
            GROUP BY pl.id
           HAVING outs >= ?""",
        (season, min_outs),
    )
    out = []
    for r in rows:
        d = dict(r)
        outs = d["outs"] or 0
        ip = outs / 3.0
        er = d["er"] or 0
        d["ip"]  = ip
        d["era"] = (er * 9.0 / ip) if ip else 0.0
        d["whip"] = ((d["bb"] or 0) + (d["h"] or 0)) / ip if ip else 0.0
        d["k9"]  = ((d["k"] or 0) * 9.0 / ip) if ip else 0.0
        out.append(d)
    if sort == "era":
        out.sort(key=lambda x: x["era"] or 999)
    elif sort == "whip":
        out.sort(key=lambda x: x["whip"] or 999)
    else:
        key = {"k": "k", "ip": "ip", "k9": "k9"}.get(sort, "k")
        out.sort(key=lambda x: (x.get(key) or 0), reverse=True)
    return out[:limit]


def game_box(game_id: int) -> dict | None:
    """Return all the per-player rows + program metadata for one game."""
    game = db.fetchone(
        """SELECT g.*, ph.name AS home_name, ph.short_name AS home_short,
                  pa.name AS away_name, pa.short_name AS away_short
             FROM college_games g
             JOIN college_programs ph ON ph.id = g.home_program_id
             JOIN college_programs pa ON pa.id = g.away_program_id
            WHERE g.id = ?""",
        (game_id,),
    )
    if not game:
        return None
    batters = db.fetchall(
        """SELECT s.*, pl.name AS player_name, pl.position
             FROM college_batter_stats s
             JOIN college_players pl ON pl.id = s.player_id
            WHERE s.game_id = ?
            ORDER BY s.program_id, s.pa DESC""", (game_id,)
    )
    pitchers = db.fetchall(
        """SELECT s.*, pl.name AS player_name
             FROM college_pitcher_stats s
             JOIN college_players pl ON pl.id = s.player_id
            WHERE s.game_id = ?
            ORDER BY s.program_id, s.outs DESC""", (game_id,)
    )
    return {"game": dict(game),
            "batters": [dict(b) for b in batters],
            "pitchers": [dict(p) for p in pitchers]}


def player_season_totals(player_id: int) -> dict:
    """Aggregate batter + pitcher career totals for one player across
    all college seasons (used by the player page + the FA sign-card)."""
    return _career_stats(player_id)


def draft_class(season: int) -> list[dict]:
    """Graduated seniors for a season — the available signing pool —
    with both scouting reports zipped in side-by-side for triangulation."""
    rows = db.fetchall(
        """SELECT pl.*, prg.short_name AS program_short, prg.name AS program_name
             FROM college_players pl
             JOIN college_programs prg ON prg.id = pl.program_id
            WHERE pl.graduated = 1 AND pl.signed_pro_player_id IS NULL
            ORDER BY pl.is_pitcher, pl.name""",
    )
    out = []
    for r in rows:
        p = dict(r)
        # Reports come from generate_scouting_reports. Two-key zip:
        # {'service': {...}, 'team:42': {...}, ...}
        reports = db.fetchall(
            "SELECT * FROM college_scouting_reports WHERE player_id = ? "
            "ORDER BY source", (p["id"],),
        )
        p["reports"] = [dict(r2) for r2 in reports]
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Postseason — Regionals → Super-Regionals → CWS
# ---------------------------------------------------------------------------
#
# 64 programs → top 64 (all) seeded into 16 four-team regionals.
# Regional: 4-team double-elim, 1 advances.
# Super-Regional: 16 → 8, best-of-3.
# CWS: 8-team double-elim.

def _top_seeds(season: int, n: int = 64) -> list[dict]:
    """Top n programs by win-pct."""
    s = standings(season)
    return sorted(s, key=lambda r: (-r["pct"], -r["wins"]))[:n]


def run_postseason(season: int, rng_seed: int = 0) -> dict:
    """Run the full postseason: regionals + super-regionals + CWS.
    Idempotent at the meta level — won't double-run if phase complete."""
    meta = db.fetchone("SELECT * FROM college_meta WHERE season = ?", (season,))
    if meta and meta["phase"] == "complete":
        return {"already_complete": True}

    rng = random.Random(rng_seed ^ season ^ 0x705751DE)
    seeds = _top_seeds(season, n=64)
    if len(seeds) < 16:
        return {"error": "not enough programs to run postseason",
                "have": len(seeds)}

    # --- Regionals (16 four-team double-elim) ---
    regional_winners: list[int] = []
    for r_idx in range(16):
        bucket = [seeds[r_idx + 16 * i] for i in range(4)]
        winner = _sim_double_elim_bracket(season, "regional", r_idx,
                                           [b["id"] for b in bucket], rng)
        regional_winners.append(winner)

    # --- Super-Regionals (best-of-3, 16 → 8) ---
    sr_winners: list[int] = []
    for sr_idx in range(8):
        a, b = regional_winners[sr_idx * 2], regional_winners[sr_idx * 2 + 1]
        winner = _sim_best_of(season, "super_regional", sr_idx, [a, b], 3, rng)
        sr_winners.append(winner)

    # --- CWS (8-team double-elim) ---
    cws_winner = _sim_double_elim_bracket(season, "cws", 0, sr_winners, rng)

    db.execute(
        "INSERT OR REPLACE INTO college_meta (season, phase) VALUES (?, 'complete')",
        (season,),
    )
    return {"champion_program_id": cws_winner,
            "regional_winners": regional_winners,
            "super_regional_winners": sr_winners}


def _add_game_to_db(season: int, phase: str, bracket_meta: dict,
                    home_id: int, away_id: int) -> int:
    return db.execute(
        "INSERT INTO college_games (season, game_date, home_program_id, "
        "away_program_id, phase, bracket_meta) VALUES (?,?,?,?,?,?)",
        (season, "postseason", home_id, away_id, phase, json.dumps(bracket_meta)),
    )


def _sim_double_elim_bracket(season: int, phase: str, bracket_idx: int,
                             team_ids: list[int], rng) -> int:
    """Run a double-elimination bracket; returns the winner's program_id.

    Simplified to a workable round-robin-on-bracket-bracket structure
    rather than a literal NCAA bracket: each team starts 0-0; we play
    until one team is the last unbeaten, then the loser bracket survivor
    plays them in a final."""
    losses: dict[int, int] = {t: 0 for t in team_ids}
    games_played = 0
    while sum(1 for v in losses.values() if v < 2) > 1 and games_played < 30:
        alive = [t for t, l in losses.items() if l < 2]
        rng.shuffle(alive)
        a, b = alive[0], alive[1]
        home_id, away_id = (a, b) if rng.random() < 0.5 else (b, a)
        meta = {"bracket": bracket_idx, "game_no": games_played + 1}
        gid = _add_game_to_db(season, phase, meta, home_id, away_id)
        result = sim_game(gid, rng_seed=rng.randint(0, 10**9))
        loser = home_id if result["home_score"] < result["away_score"] else away_id
        losses[loser] += 1
        games_played += 1
    survivors = [t for t, l in losses.items() if l < 2]
    return survivors[0] if survivors else team_ids[0]


def _sim_best_of(season: int, phase: str, bracket_idx: int,
                 team_ids: list[int], n: int, rng) -> int:
    """Best-of-n series between two teams; returns the winner."""
    a, b = team_ids
    wins = {a: 0, b: 0}
    need = (n // 2) + 1
    game_no = 0
    while wins[a] < need and wins[b] < need and game_no < n:
        home_id, away_id = (a, b) if (game_no % 2 == 0) else (b, a)
        meta = {"bracket": bracket_idx, "game_no": game_no + 1, "of": n}
        gid = _add_game_to_db(season, phase, meta, home_id, away_id)
        result = sim_game(gid, rng_seed=rng.randint(0, 10**9))
        winner = home_id if result["home_score"] > result["away_score"] else away_id
        wins[winner] += 1
        game_no += 1
    return a if wins[a] > wins[b] else b


# ---------------------------------------------------------------------------
# Scouting reports
# ---------------------------------------------------------------------------

def generate_scouting_reports(season: int, rng_seed: int = 0) -> int:
    """Generate the season's shared scouting service report for every
    senior + the per-team draft reports for each pro-league team."""
    init_college_schema()
    # Skip if already generated
    existing = db.fetchone(
        "SELECT COUNT(*) AS n FROM college_scouting_reports WHERE season = ?",
        (season,)
    )
    if existing and existing["n"] > 0:
        return 0

    seniors = db.fetchall(
        "SELECT * FROM college_players WHERE college_year = 4 AND is_active = 1"
    )
    pro_teams = db.fetchall("SELECT id FROM teams")   # pro-league teams
    rng = random.Random(rng_seed ^ season ^ 0x5C0F75DE)

    n = 0
    for sp in seniors:
        p = dict(sp)
        # Shared service report
        r = _cg.make_scouting_report(p, rng, source="service")
        _save_report(season, p["id"], "service", r)
        n += 1
        # Per-team independent draws
        for t in pro_teams:
            r = _cg.make_scouting_report(p, rng, source=f"team:{t['id']}")
            _save_report(season, p["id"], f"team:{t['id']}", r)
            n += 1
    return n


def _save_report(season: int, player_id: int, source: str, report: dict) -> None:
    db.execute(
        "INSERT OR REPLACE INTO college_scouting_reports "
        "(season, player_id, source, "
        " grade_skill, grade_contact, grade_power, grade_eye, grade_speed, "
        " grade_pitcher_skill, grade_command, grade_movement, grade_stamina) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (season, player_id, source,
         report.get("skill"), report.get("contact"), report.get("power"),
         report.get("eye"),   report.get("speed"),
         report.get("pitcher_skill"), report.get("command"),
         report.get("movement"),       report.get("stamina")),
    )


# ---------------------------------------------------------------------------
# Annual rollover
# ---------------------------------------------------------------------------

def annual_rollover(season: int, next_season: int, rng_seed: int = 0) -> dict:
    """End-of-year cycle: graduate seniors, age everyone else, generate
    fresh freshman class for every program for `next_season`."""
    init_college_schema()
    rng = random.Random(rng_seed ^ next_season ^ 0x701102EF)

    # 1) Mark all current seniors graduated (they become signable for pro).
    db.execute(
        "UPDATE college_players SET graduated = 1, is_active = 0 "
        "WHERE college_year >= 4 AND is_active = 1"
    )

    # 2) Advance non-graduates one year. Pull each, apply growth, write back.
    aging = db.fetchall(
        "SELECT * FROM college_players WHERE is_active = 1 AND graduated = 0"
    )
    for row in aging:
        p = dict(row)
        _cg.advance_one_year(p)
        # Write the grown grades + new college_year back.
        db.execute(
            "UPDATE college_players SET "
            "college_year = ?, "
            "potential_skill=?, potential_contact=?, potential_power=?, "
            "potential_eye=?, potential_speed=?, "
            "potential_pitcher_skill=?, potential_command=?, "
            "potential_movement=?, potential_stamina=?, "
            "skill=?, contact=?, power=?, eye=?, speed=?, "
            "pitcher_skill=?, command=?, movement=?, stamina=? "
            "WHERE id = ?",
            (p["college_year"],
             p.get("potential_skill"), p.get("potential_contact"), p.get("potential_power"),
             p.get("potential_eye"),   p.get("potential_speed"),
             p.get("potential_pitcher_skill"), p.get("potential_command"),
             p.get("potential_movement"), p.get("potential_stamina"),
             p.get("skill"), p.get("contact"), p.get("power"),
             p.get("eye"), p.get("speed"),
             p.get("pitcher_skill"), p.get("command"),
             p.get("movement"), p.get("stamina"),
             p["id"]),
        )

    # 3) For the new season, generate a fresh freshman class per program
    # to replace the graduated seniors. ~6 fresh freshmen per program
    # (mirrors the original seeding distribution where 25% of the 23-man
    # roster was seniors).
    progs = db.fetchall("SELECT id, short_name FROM college_programs "
                        "WHERE season = ?", (season,))
    # Carry the same programs forward to next_season — duplicate the rows
    # with the new season number.
    for prog in progs:
        new_prog_id = db.execute(
            "INSERT INTO college_programs (name, short_name, conference, "
            "region, home_city, lat, lon, season) "
            "SELECT name, short_name, conference, region, home_city, "
            "       lat, lon, ? FROM college_programs WHERE id = ?",
            (next_season, prog["id"]),
        )
        # Move active non-grad players from old program → new program.
        db.execute(
            "UPDATE college_players SET program_id = ? "
            "WHERE program_id = ? AND is_active = 1 AND graduated = 0",
            (new_prog_id, prog["id"]),
        )
        # Generate ~6 freshmen
        for _ in range(6):
            p = _cg.generate_college_player(rng,
                                            is_pitcher=(rng.random() < 0.35))
            _insert_player(new_prog_id, p)

    db.execute(
        "INSERT OR REPLACE INTO college_meta (season, phase) VALUES (?, 'regular')",
        (next_season,),
    )
    return {"next_season": next_season,
            "programs_carried": len(progs)}


# ---------------------------------------------------------------------------
# Pro signing — pull a graduated senior into the pro free-agent pool
# ---------------------------------------------------------------------------

def sign_graduate_to_pro(college_player_id: int) -> int:
    """Move a graduated college player into the pro `players` table as a
    free agent. Returns the new pro player id. The college row stays
    intact (graduated=1, signed_pro_player_id stamped) so the pro player
    card can backlink to their college career."""
    row = db.fetchone("SELECT * FROM college_players WHERE id = ?",
                      (college_player_id,))
    if row is None:
        raise ValueError(f"college player {college_player_id} not found")
    p = dict(row)
    if not p.get("graduated"):
        raise ValueError("only graduated players can be signed")

    pro_player = _cg.sign_to_pro(p, college_career_stats=_career_stats(college_player_id))

    # Insert into pro players table as a free agent (team_id = NULL).
    # Pitchers default to position "P"; hitters carry their college position
    # but fall back to "RF" if blank (jokers / DH-only types).
    if not pro_player.get("position"):
        pro_player["position"] = "P" if pro_player.get("is_pitcher") else "RF"
    pro_cols = ("name", "position", "country", "hometown", "bats", "throws",
                "is_pitcher", "is_joker", "roster_slot", "is_active",
                "skill", "contact", "power", "eye", "speed",
                "pitcher_skill", "command", "movement", "stamina",
                "defense", "arm", "baserunning", "run_aggressiveness",
                "defense_infield", "defense_outfield", "defense_catcher",
                "archetype", "hard_contact_delta", "hr_weight_bonus",
                "stay_aggressiveness", "contact_quality_threshold")
    values = tuple(pro_player.get(c) for c in pro_cols)
    placeholders = ",".join("?" * len(pro_cols))
    pro_id = db.execute(
        f"INSERT INTO players ({','.join(pro_cols)}, team_id) "
        f"VALUES ({placeholders}, NULL)",
        values,
    )
    db.execute(
        "UPDATE college_players SET signed_pro_player_id = ? WHERE id = ?",
        (pro_id, college_player_id),
    )
    return pro_id


def _career_stats(college_player_id: int) -> dict:
    """Aggregate a college player's career batting + pitching lines."""
    bat = db.fetchone(
        "SELECT SUM(pa) AS pa, SUM(ab) AS ab, SUM(h) AS h, "
        "       SUM(hr) AS hr, SUM(rbi) AS rbi, SUM(r) AS r, "
        "       SUM(bb) AS bb, SUM(k) AS k, SUM(sb) AS sb "
        "FROM college_batter_stats WHERE player_id = ?", (college_player_id,)
    )
    pit = db.fetchone(
        "SELECT SUM(outs) AS outs, SUM(h) AS h, SUM(er) AS er, "
        "       SUM(bb) AS bb, SUM(k) AS k, SUM(hr) AS hr "
        "FROM college_pitcher_stats WHERE player_id = ?", (college_player_id,)
    )
    out: dict = {}
    if bat and (bat["pa"] or 0) > 0:
        ab = bat["ab"] or 0
        h  = bat["h"]  or 0
        out["batting"] = {
            "pa": bat["pa"] or 0, "ab": ab, "h": h,
            "avg": (h / ab) if ab else 0.0,
            "hr": bat["hr"] or 0, "rbi": bat["rbi"] or 0,
            "r":  bat["r"]  or 0, "bb": bat["bb"] or 0,
            "k":  bat["k"]  or 0, "sb": bat["sb"] or 0,
        }
    if pit and (pit["outs"] or 0) > 0:
        outs = pit["outs"] or 0
        ip = outs / 3.0
        er = pit["er"] or 0
        out["pitching"] = {
            "ip":  ip, "er": er,
            "era": (er * 9.0 / ip) if ip else 0.0,
            "h":   pit["h"]  or 0, "bb": pit["bb"] or 0,
            "k":   pit["k"]  or 0, "hr": pit["hr"] or 0,
        }
    return out
