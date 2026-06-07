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


# ---------------------------------------------------------------------------
# Scraped sports junk — league-wide sweep.
#
# The CJK/Japanese cleanup above was thorough for its pools, but the
# EUROPEAN / AFRICAN / LATIN-AMERICAN buckets were left riddled with the same
# class of scrape garbage: football & basketball CLUB names, league /
# federation / sponsor words, sports terms in many languages (Calcio, Basket,
# Bàsquet, Fotball...), and cross-culture place names sitting in the wrong
# bucket (e.g. "Wielkopolski" in a Baltic pool, "Calcio" as a given name).
# That produced roster names like "Nevena Calcio", "Elena Basket" and
# "United Wielkopolski". The garbage was stripped from the data in a
# league-wide pass (see docs/aar-orphan-nations-and-name-pollution.md); this
# set bakes that pass into the scrubber so a re-seed can't quietly bring it
# back, and so tests/test_name_pool_clean.py actually guards against it.
#
# Every token here is ALREADY absent from the committed pools, so applying it
# removes nothing today — it is pure forward protection and cannot regress a
# real name currently in the data. Genuine ethnic names (Slavic -ić/-ović,
# Finnish -nen, Mārtiņš, Markkanen, Þorsteinn, ...) were deliberately excluded
# from this list. Applied to ALL non-CJK pools (first + surname) via `mascots`.
# ---------------------------------------------------------------------------
SCRAPED_SPORTS_JUNK = {
    'AaB', 'Aalen', 'Aalesunds', 'Abidjan', 'Academy', 'African',
    'AfroBasket', 'Agadir', 'Agency', 'Agglomération', 'Ahlen', 'Aix',
    'Ajaccio', 'Al-Quwa', 'Al-Wehdat', 'Alavés', 'Albirex', 'Alcoyano',
    'Alger', 'Alianza', 'Alkmaar', 'Almelo', 'Almería', 'Alsace', 'Alvark',
    'Americans', 'Amman', 'Andorra', 'Ankaragücü', 'Annis', 'Antigua',
    'Aquila', 'Arabia', 'Araski', 'Arcus', 'Ardennes', 'Arena', 'Argentinos',
    'Argonauts', 'Arminia', 'Ashdod', 'Asociación', 'Association',
    'Associação', 'Atalanta', 'Atenas', 'Athletic', 'Atlético', 'Atomerőmű',
    'Audax', 'Australian', 'Auvergne', 'Auvray', 'Auxerre', 'Avaldsnes',
    'Avell', 'Avenida', 'Avranches', 'Azur', 'B-Corsairs', 'Bagatskis',
    'Baggott', 'Ballarat', 'Baltimore', 'Bamenda', 'Banchi', 'Barangay',
    'Bashundhara', 'Basket', 'Basket-Ball', 'Basketbol', 'Baskonia', 'Bateau',
    'Battery', 'Bauermann', 'Bayno', 'Beermen', 'Belediyespor', 'Belgrade',
    'Bendigo', 'Benevento', 'Bernhardt', 'Betis', 'Białystok', 'Bielefeld',
    'Bielsko-Biała', 'Bigorre', 'Bizkaia', 'Blazers', 'Blues', 'BoIS', 'Boca',
    'Bochum', 'Boldklub', 'Boleslav', 'Bolts', 'Bond-Flasza', 'Bonn',
    'Borkelmans', 'Borussia', 'Botaş', 'Botev', 'Boulazac', 'Boulogne',
    'Boys', 'Bragantino', 'Brann', 'Brasil', 'Bratanovic', 'Braunschweig',
    'Brauzman', 'Breakers', 'Breda', 'Bremen', 'Bremerhaven', 'Breogán',
    'Brescia', 'Bresciano', 'Brex', 'Brommapojkarna', 'Brøndby', 'Buccaneers',
    'Bucks', 'Budivelnyk', 'Budućnost', 'Bullets', 'Bursaspor', 'Buscaglia',
    'Bàsquet', 'Básquet', 'Cacereño', 'Cadamuro', 'Caen', 'Cagliari',
    'Calcio', 'Caledonia', 'California', 'Calvados', 'Canadian', 'Canaria',
    'Canarias', 'Cannes', 'Canterbury', 'Cantillana', 'Cape', 'Casa',
    'Castelletto', 'Castelló', 'Cavaliers', 'Cavigal', 'Cel', 'Celtics',
    'Center', 'Central', 'Cercle', 'Cerro', 'Chalon', 'Champagne',
    'Championships', 'Chapelle', 'Charleville-Mézières', 'Chemidor',
    'Chicago', 'Chinese', 'Chivas', 'Chlef', 'Cholet', 'Chorale',
    'Châteauroux', 'Ciak', 'Cibona', 'Cittadella', 'Clippers', 'College',
    'Coloma', 'Colombe', 'Colorado', 'Columbus', 'Concarneau', 'Concorde',
    'Confesor', 'Connecticut', 'Constantine', 'Cools', 'Coquimbo', 'Coruña',
    'Courage', 'Cracovia', 'Crailsheim', 'Craiova', 'Cremonese', 'Crew',
    'Crown', 'Créteil-Lusitanos', 'Curicó', 'Current', 'Częstochowa',
    'Círculo', 'Cúper', 'Da Rosa', 'Darüşşafaka', 'De Marco', 'Defensor',
    'Degerfors', 'Demirspor', 'Denver', 'Deportes', 'Deportivo', 'Derthona',
    'Detroit', 'Dettmann', 'Diamonds', 'Dikeoulakos', 'Dinamo', 'Djorkaeff',
    'Djurgårdens', 'Dolonc', 'Dominican', 'Donetsk', 'Doradas', 'Dordogne',
    'Douala', 'Dragonflies', 'Dream', 'Dräger', 'Duisburg', 'Durbin',
    'Dushanbe', 'Dux', 'Dynamo', 'Dziewa', 'Düsseldorf', 'Earthquakes',
    'Eggesvik', 'Egurrola', 'Eibar', 'Eintracht', 'Eisbären', 'Ekaterinburg',
    'Ekrem', 'Eldstål', 'Elfsborg', 'Erciyesspor', 'Eskhata', 'Eskilsminne',
    'Eskilstuna', 'Espanyol', 'Essen', 'Estudiantes', 'EuroBasket',
    'Excellence', 'Fabril', 'Fagiano', 'Fajr', 'Falkesgaard', 'Farkhor',
    'Fazer', 'Fehérvár', 'Feldeine', 'Femenino', 'Ferencvárosi', 'Fever',
    'Fighters', 'Filipino', 'Fire', 'Firebonds', 'Fjellerup', 'Flamengo',
    'Fodbold', 'Força', 'Fotball', 'Fotboll', 'Francs', 'Frankston',
    'Fratello', 'Frenderup', 'Fuenlabrada', 'Fundación', 'Féminas', 'Féminin',
    'Fürth', 'Galaxy', 'Geas', 'Gefle', 'Geraldton', 'Gernika', 'Gevitz',
    'Giant', 'Giants', 'Gijón', 'Ginzburg', 'Gipuzkoa', 'GiroLive', 'Girona',
    'Gjergja', 'Glenfield', 'Gliwice', 'Gloriosas', 'Goodes', 'Goree',
    'Gorica', 'Gourevitch', 'Graafschap', 'Grameni', 'Grampus', 'Granada',
    'Grandison', 'Gravelines-Dunkerque', 'Grecia', 'Gretter', 'Grouses',
    'Grêmio', 'Guillou', 'Guingamp', 'Gwathmey', 'Gymnastikforening',
    'Górnik', 'Göteborg', 'Göttingen', 'Hakkarigücü', 'Halcones', 'Halmstads',
    'Hammarby', 'Hang-seo', 'Hapoel', 'Hassania', 'Heading', 'Heat',
    'Helsingborgs', 'Helsingin', 'Heracles', 'Hermine', 'Heroes', 'Heroum',
    'Hettsheimeir', 'Hilal', 'Hillal', 'Hiroshima', 'Hjørring', 'Hobro',
    'Hokkaido', 'Hollingshed', 'Hollis-Jefferson', 'Hoopers', 'Hornets',
    'Horoya', 'Hubner', 'Huesca', 'Ibiza', 'Independiente', 'Insa', 'Inter',
    'Istaravshan', 'Italiano', 'Jablonec', 'JackJumpers', 'Jagiellonia',
    'Jalkapalloklubi', 'Jaro', 'Jazz', 'Jean-Aimé', 'Jenner', 'Jeonnam',
    'Jerv', 'Jeter', 'Jiangsu', 'Jonava', 'Juniors', 'Júbilo', 'Kabylie',
    'Kalovelonis', 'Kangoeroes', 'Kansas', 'Kardemir', 'Karlslunds',
    'Kashuba', 'Katanec', 'Kauhajoki', 'Kayseri', 'Kecskeméti', 'Keltern',
    'Kennesaw', 'Kerkhof', 'Khartoum', 'Khimik', 'Khimki', 'Khujand',
    'Kilsyth', 'Kindermann', 'Kingdom', 'Klok', 'Knattspyrnufélag',
    'Knattspyrnufélagið', 'Knicks', 'Koblenz', 'Kocian', 'Kolbotn', 'Korona',
    'Kortrijk', 'Krejčíková–Siniaková', 'Krestinin', 'Krimets',
    'Kristianstads', 'Kuban', 'Kulob', 'Kuopion', 'Kursk', 'Körmend',
    'La Calera', 'Lakers', 'Lakes', 'Landskrona', 'Langreo', 'Lanxess',
    'Larkas', 'Larroquette', 'Las', 'Lazio', 'Le Portel', 'Lebanon', 'Lecce',
    'Lech', 'Lechia', 'Lega', 'Leganés', 'Legia', 'Legnica', 'Leipzig',
    'Levanga', 'Levante', 'Lezkano', 'Lietkabelis', 'Limassol', 'Limburg',
    'Limeira', 'Lionesses', 'Liège', 'Lobos', 'Logroñés', 'Longhorns',
    'Loria', 'Lorient', 'Los', 'Louves', 'Lublin', 'Lux', 'Lynx', 'Lyonnais',
    'Maccabi', 'Machín', 'Magic', 'Magnano', 'Mahram', 'Maine', 'Major',
    'Malatyaspor', 'Mallorca', 'Manresa', 'Markaz', 'Marrakech', 'Matyash',
    'Maxhuni', 'Mažeikiai', 'McCowan', 'Meindl', 'Melbourne', 'Melipilla',
    'Meralco', 'Mercury', 'Merk', 'Merlins', 'Mezőkövesdi', 'Miami',
    'Mickelson', 'Miedź', 'Milano', 'Milwaukee', 'Mimosas', 'Minnesota',
    'Minproff', 'Minsk', 'Mishchenko', 'Mitteldeutscher', 'Montella', 'Monza',
    'Mudir', 'Mulders', 'Mustaki', 'Mystics', 'Métropole', 'Nacional',
    'Nairobi', 'Naismith', 'Name', 'Namibian', 'Namur-Capitale',
    'Navalcarnero', 'Needham', 'Neftçi', 'Nesterov', 'Nets', 'Nevėžis', 'New',
    'Nice', 'Nicosia', 'Nijmegen', 'Nkamhoua', 'Norambuena', 'Norrby',
    'Norrköping', 'NorthPort', 'Nouakchott', 'Novgorod', 'Novo', 'Nuggets',
    'Numancia', 'Nîmes', 'Obradoiro', 'Obras', 'Okayama', 'Oklahoma',
    'Olimpia', 'Olimpija', 'Olomoucko', 'Olympiakos', 'Olympic', 'Olympique',
    'Olímpico', 'Omonia', 'Once', 'Onehunga', 'Oriente', 'Orléans', 'Osasuna',
    'Osnabrück', 'Oud-Heverlee', 'Pabellón', 'Pacers', 'Palace', 'Palayesh',
    'Palencia', 'Pallacanestro', 'Palloilijat', 'Palloseura', 'Palmas',
    'Palmeiras', 'Palmi', 'Pan-gon', 'Panjshanbe', 'Panom', 'Parisien',
    'Partizan', 'Patriots', 'Pauw', 'Pelicans', 'Persebaya', 'Persib',
    'Persija', 'Persis', 'Petkim', 'Petrochimi', 'Petrolero', 'Piast', 'Pier',
    'Piešťanské', 'Pistons', 'Piteå', 'Pizzi', 'Plovdiv', 'Podbeskidzie',
    'Podgorica', 'Pogoń', 'Ponferradina', 'Porta', 'Portland', 'Potassa',
    'Premier', 'Primorye', 'Prizren', 'Prometey', 'Puerto', 'Purdue',
    'Pétange', 'Płock', 'Qarabağ', 'Queens', 'Raiders', 'Raków', 'Ramsay',
    'Ranheim', 'Rapids', 'Raptors', 'Ratanakosin', 'Ratiopharm', 'Real',
    'Records', 'Regar-TadAZ', 'Regatas', 'Reggiana', 'Regirl', 'Rehhagel',
    'Reichelt', 'Republic', 'Revolution', 'Reyer', 'Reykjavíkur', 'Reynald',
    'Riga', 'Rivadavia', 'Rizespor', 'Rockets', 'Roddar', 'Rodez', 'Roma',
    'Rosenborg', 'Rouen', 'Rovers', 'Rowdies', 'Royale', 'Rutronik',
    'Ružomberok', 'Rytas', 'Rīga', 'Saarlouis', 'Sabres', 'Sachs',
    'Sacramento', 'Safar', 'Safari', 'Sagnol', 'Saint Petersburg',
    'Saint-Chamond', 'Saint-Gilloise', 'Saint-Malo', 'Saint-Priest',
    'Saint-Quentin', 'Saints', 'Sampdoria', 'San Felipe', 'Sandefjord',
    'Sandringham', 'Sanfrisco', 'Sangalhos', 'Sanitarias', 'Sanon', 'Santa',
    'Saphir', 'Sarajevo', 'Saski', 'Sassari', 'Saudi', 'Saville', 'Schilb',
    'Schio', 'Seagulls', 'Seahawks', 'Seattle', 'Sežana', 'Sfaxien', 'Sheva',
    'Shiga', 'Shkëndija', 'Sichuan', 'Sidorenko', 'Signeul', 'Sikh',
    'Silkeborg', 'Sitak', 'Sittard', 'Skyliners', 'Skövde', 'Sociedad',
    'Sociedade', 'Sopot', 'Sopron', 'Southeastern', 'SpVgg', 'Sparks',
    'Sparta', 'Spellman', 'Spezia', 'Spirit', 'Spirou', 'Splitter', 'Spor',
    'Sport', 'Sportif', 'Sporting', 'Sportive', 'Sportivo', 'Sports', 'Spurs',
    'Stabæk', 'Stade', 'Stajcic', 'Stal', 'Stalbekov', 'Standard', 'Stange',
    'Strathmore', 'Strauß', 'Strongest', 'Struick', 'Strømsgodset',
    'Stubblefield', 'Styles', 'Subotica', 'Sud', 'Sudan', 'Sundhage', 'Suns',
    'Suwannaphum', 'Szekszárd', 'Séance', 'Södertälje', 'Tae-yong', 'Taipans',
    'Tamburrini', 'Tampa', 'Tango', 'Tanton', 'Tapiolan', 'Tarbes',
    'Tarragona', 'Tasmania', 'Telekom', 'Texas', 'The', 'Three', 'Timbers',
    'Tirana', 'Toaster', 'Tobey', 'Tokyo', 'Toronto', 'Torreforta', 'Toruń',
    'Tours', 'Tovuz', 'Towers', 'Townsville', 'Toyama', 'Trefl', 'Trench',
    'Trento', 'Trieste', 'Trophy', 'Troussier', 'TuS', 'Turbo', 'Turkey',
    'Twarde', 'Tychy', 'Udinese', 'Ulinzi', 'Ulm', 'Uni', 'Unido', 'Union',
    'United', 'Universo', 'Unión', 'Urawa', 'Utah', 'Utsiktens', 'Utsunomiya',
    'Uşak', 'Vallecano', 'Van Zanten', 'Vannes', 'Vast', 'Venezia', 'Verdy',
    'Verona', 'VfR', 'Viking', 'Villarrobledo', 'Vipers', 'Virtus', 'Vital',
    'Vitesse', 'Vittsjö', 'Vojvodina', 'Vyškov', 'Växjö', 'Vålerenga',
    'Værløse', 'Víkingur', 'Waalwijk', 'Waikato', 'Walkup', 'Wanderers',
    'Warriors', 'Warta', 'Waverley', 'Welcome', 'Whales', 'Wielkopolski',
    'Wien', 'Wiesbaden', 'Wilbekin', 'Wildcats', 'Windi', 'Windy', 'Wings',
    'Wisła', 'Wizards', 'Wolfsberger', 'Wolfsburg', 'Women', 'Woodland',
    'World', 'Wydad', 'Włocławek', 'Xerez', 'Yalovaspor', 'Yambol', 'Zabrze',
    'Zaccheroni', 'Zagłębie', 'Zamarat', 'Zealand', 'Zob', 'Zwolle', 'Åland',
    'Çaykur', 'Çukurova', 'Épinal', 'Étoile', 'Örebro', 'Östersunds',
    'İstanbul', 'İzmit', 'ŠK', 'Šegrt', 'ŽKD', 'ŽKK', 'Žalgiris',
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
# Canonical CJK surname pools — the Korean & Chinese surname buckets are
# rebuilt FROM these sets (the full list is seeded, not just the intersection
# with whatever the scrape happened to contain), so the buckets carry the real
# spread of surnames rather than a thin scraped subset. One dominant
# romanisation per surname to avoid odd "Lee vs Yi" doubling.
# ---------------------------------------------------------------------------
KOREAN_SURNAMES = {
    "Kim", "Lee", "Park", "Choi", "Jung", "Jeong", "Kang", "Cho", "Yoon",
    "Jang", "Lim", "Han", "Oh", "Seo", "Shin", "Kwon", "Hwang", "Ahn",
    "Song", "Yoo", "Hong", "Jeon", "Ko", "Moon", "Yang", "Bae", "Baek",
    "Heo", "Nam", "Noh", "Ha", "Joo", "Koo", "Shim", "Min", "Chae", "Cha",
    "Byun", "Eom", "Won", "Ok", "Sun", "Tak", "Seol", "Kwak", "Yeom",
    "Bang", "Yeo", "Wang", "Pyo", "Ki", "Geum", "Do", "Ryu", "Na", "Ban",
    "Gil", "Jin", "Sung", "Chu", "Ma", "Gong", "Hyun", "Sunwoo", "Ryang",
    "Pi", "Gu", "Ju", "Yook", "Jegal", "Seon",
}

# Mandarin-pinyin surnames for the mainland `chinese` bucket (the Cantonese
# romanisations that the scrape dragged in belong to HK/overseas pools, not
# this one). Roughly the Hundred-Family-Surnames head plus the common tail.
CHINESE_SURNAMES = {
    "Wang", "Li", "Zhang", "Liu", "Chen", "Yang", "Huang", "Zhao", "Wu",
    "Zhou", "Xu", "Sun", "Ma", "Zhu", "Hu", "Guo", "He", "Gao", "Lin",
    "Luo", "Zheng", "Liang", "Xie", "Song", "Tang", "Han", "Feng", "Deng",
    "Cao", "Peng", "Zeng", "Xiao", "Tian", "Dong", "Pan", "Yuan", "Cai",
    "Jiang", "Yu", "Du", "Ye", "Cheng", "Su", "Wei", "Lyu", "Ding", "Ren",
    "Shen", "Yao", "Lu", "Jin", "Fu", "Zhong", "Cui", "Tan", "Liao", "Fan",
    "Shi", "Jia", "Xia", "Fang", "Zou", "Xiong", "Bai", "Meng", "Qin",
    "Yan", "Xue", "Hou", "Lei", "Long", "Duan", "Kong", "Mao", "Shao",
    "Wan", "Qian", "Qiu", "Wen", "Niu", "Pang", "Yin", "Gu", "Kang", "Qi",
    "Tao", "Hao", "Lai", "Qiao", "Chang", "Ke", "Pu", "Zhi", "Lan", "Xiang",
}

# ---------------------------------------------------------------------------
# Korean & Chinese FIRST-name pools — the same scrape dumped *surnames* into
# the given-name slot (Kim/Cho/Choi as "first" names; Wang/Chen/Zhang too),
# plus provinces (Fujian, Guangdong) and foreign players (Michael, Aleksandar).
# A blocklist alone leaves a thin/odd pool, so these buckets are rebuilt:
#   * Korean — given names are reliably hyphenated, so we keep existing
#     hyphenated tokens and add a canonical curated set; single-token
#     surnames/junk drop out.
#   * Chinese — single-syllable surnames and given names overlap heavily, so
#     we strip every surname/place/foreign token and re-seed from a canonical
#     given-name list (which re-introduces the syllables that are legit given
#     names, e.g. Wei/Tao/Hao).
# ---------------------------------------------------------------------------

# Foreign / western / corporate / place tokens seen in the CJK first slots.
CJK_FIRST_JUNK = {
    "Jürgen", "Paulo", "Ricardo", "Aleksandar", "Everton", "Goran", "Jørn",
    "Marcello", "Matt", "Michael", "Minnesota", "Nico", "Oliver", "Scott",
    "Sean", "Stefan", "Vas", "Yannis", "Shinichi", "Lisa", "Casey", "Helen",
    "Las", "Back", "Samsung", "United", "New", "Chicago", "Cheongju", "Uiduk",
}

# Chinese provinces / regions that scraped into the given-name slot.
CHINESE_PLACES = {
    "Fujian", "Guangdong", "Liaoning", "Xinjiang", "Zhejiang", "Heilongjiang",
    "Henan", "Jiangsu", "Shandong", "Shanxi", "Bayi",
}

# A handful of legit single-token Korean given names (no hyphen).
KOREAN_FIRST_KEEP = {"Bora", "Bitna", "Saem", "Areum", "Haneul", "Nari"}

KOREAN_MALE_GIVEN = {
    "Min-jun", "Seo-jun", "Do-yun", "Si-woo", "Ji-ho", "Ha-jun", "Ye-jun",
    "Yu-jun", "Geon-woo", "Woo-jin", "Hyun-woo", "Jun-seo", "Min-jae",
    "Dong-hyun", "Sung-min", "Jin-woo", "Tae-hyun", "Hyun-jin", "Hyun-soo",
    "Joon-ho", "Jong-ho", "Sang-hyun", "Seung-min", "Seung-hwan", "Kwang-hyun",
    "Byung-ho", "Chan-ho", "Dae-ho", "Yong-soo", "Kang-min", "Min-ho",
    "Min-soo", "Tae-young", "Joon-young", "Jun-seok", "Jin-soo", "Ji-hwan",
    "Ji-min", "Yoo-jin", "Young-min", "Sung-woo", "Jae-won", "Dong-won",
    "Sang-woo", "Seok-jin", "Nam-joon", "Ho-seok", "Sung-ho", "Jae-hyun",
    "Do-hyun", "Eun-woo", "Tae-woo", "Won-jun", "Kyung-ho", "Jin-hyuk",
    # bolster
    "Joon-woo", "Seung-ho", "Hyun-ki", "Sang-min", "Jae-ho", "Young-ho",
    "Dong-min", "Min-gyu", "Seong-min", "Jun-ho", "Jae-yong", "Ki-tae",
    "Sung-jin", "Woo-sung", "Hyung-jun", "Jin-young", "Tae-jun", "Seung-woo",
    "Ji-sung", "Chang-min", "Dong-gun", "Hyun-bin", "Kwang-soo", "Sang-ho",
    "Yong-jin", "Jae-suk", "Byung-hoon", "Seok-woo", "Min-seok", "Tae-yang",
    "Hyeon-woo", "Jae-min", "Seung-gi", "Woo-hyun", "Kyung-min", "Dae-sung",
    "Jong-su", "Han-gyul", "Yoon-ho", "Sang-yeop",
}

KOREAN_FEMALE_GIVEN = {
    "Seo-yeon", "Seo-young", "Ji-woo", "Ha-eun", "Ha-na", "Soo-jin", "Soo-min",
    "Min-ji", "Min-seo", "Ji-eun", "Ji-min", "Ji-yeon", "Hye-jin", "Hye-rin",
    "Hyun-ah", "Hyun-jung", "Da-eun", "Da-hyun", "Eun-ji", "Eun-jung", "Bo-ra",
    "Yu-ri", "Yoon-ah", "Yeon-woo", "Jin-ah", "Joo-eun", "Mi-na", "Sun-hee",
    "Sung-hee", "Na-yeon", "Ye-jin", "Su-bin", "Chae-won", "Ga-eun", "Yu-jin",
    "Seo-hyun", "Hae-won", "So-yeon", "Soo-ah", "Hyo-jin", "Mi-young",
    "Eun-young", "Jung-eun", "Da-som", "Ha-rin", "Ye-won",
    # bolster
    "Seo-ah", "Ha-yoon", "Ye-eun", "Yu-na", "Ji-a", "Chae-yeong", "Da-yeon",
    "Ye-rin", "Na-eun", "Seo-jin", "Yeon-seo", "So-eun", "Min-young",
    "Hye-won", "Su-jin", "Eun-bi", "Ga-yeon", "Hae-rin", "Ji-hye", "Hyo-rin",
    "Bo-mi", "Eun-chae", "Da-bin", "Ji-su", "Yeon-ji", "Sae-rom", "A-ra",
    "Eun-seo", "Yu-jeong", "Da-hye", "Soo-young", "Hye-soo", "Mi-rae",
    "Na-rae", "Ye-na",
}

CHINESE_MALE_GIVEN = {
    "Wei", "Jun", "Hao", "Lei", "Bin", "Bo", "Tao", "Peng", "Gang", "Jian",
    "Jie", "Ming", "Yong", "Hui", "Qiang", "Sheng", "Kai", "Chao", "Xiang",
    "Hua", "Hong", "Zhi", "Hai", "Xin", "Yun", "Jing", "Qing", "Wen", "Rui",
    "Xuan", "Yi", "Yu", "Liang", "Feng", "Guang", "Dawei", "Jianhua",
    "Weidong", "Zhihao", "Haoran", "Zihao", "Yuxuan", "Junjie", "Minghao",
    "Tianyu", "Zixuan", "Haoyu", "Yichen", "Jiahao", "Zhiyuan", "Wenbo",
    "Yifan", "Shengjie", "Guoqiang", "Jianguo", "Yuhang", "Chenyu", "Donghai",
    "Xiaoming", "Zhiqiang", "Yuhan",
    # bolster
    "Hanyu", "Zihang", "Yibo", "Ziyang", "Haowen", "Junhao", "Kaiwen",
    "Boyang", "Jiawei", "Zhihang", "Yuchen", "Hongyu", "Mingyu", "Shihan",
    "Zhibin", "Yujie", "Wenhao", "Xiaolong", "Guoliang", "Zhigang", "Jinhai",
    "Yongan", "Shaoqing", "Dehua", "Liwei", "Yanjun", "Zhanpeng", "Chenxi",
    "Hongbo", "Jiaming", "Zhihua", "Guodong", "Xiaobo", "Yongqiang",
    "Jianfeng", "Weiguo", "Chengen", "Ruihan", "Zhengyu", "Haoyang",
}

CHINESE_FEMALE_GIVEN = {
    "Mei", "Jing", "Hua", "Fang", "Yan", "Lan", "Xin", "Ying", "Ling", "Min",
    "Na", "Ting", "Juan", "Hong", "Qing", "Xia", "Dan", "Yu", "Xue", "Shan",
    "Hui", "Rou", "Bing", "Ai", "Yun", "Wen", "Jiao", "Nan", "Qian", "Li",
    "Chunhua", "Dandan", "Xiaohong", "Xiaoli", "Yanyan", "Lijuan", "Xiumei",
    "Yuhan", "Xinyi", "Yaqi", "Mengyao", "Jiaqi", "Siyu", "Yuxin", "Ruoxi",
    "Jiayi", "Xinyu", "Yiran", "Zihan", "Yuting", "Mengqi", "Shuhua",
    "Xiaoyan", "Lili",
    # bolster
    "Mengjia", "Xinran", "Yuewen", "Shujuan", "Lihua", "Xiulan", "Guiying",
    "Yumei", "Cuihua", "Fenfen", "Jingjing", "Lingling", "Nana", "Tingting",
    "Wenwen", "Xiaomei", "Xiaolan", "Huifang", "Yanan", "Sijia", "Yuqi",
    "Wanru", "Jiani", "Xiaoyu", "Yunxi", "Hanwen", "Shiyu", "Qianqian",
    "Xiaoxiao", "Yueyue", "Ruohan", "Zhiqing", "Meiling", "Xiuli", "Yaru",
}


# ---------------------------------------------------------------------------
# Japan — the `japanese` pools are large and mostly real, but the same scrape
# left European/J-league club names, sponsor companies, and foreign-player
# given names behind. Cleaned by blocklist (not allowlist — Japanese surnames
# are too numerous to enumerate) and topped up with common missing surnames.
# ---------------------------------------------------------------------------
JAPANESE_SURNAME_JUNK = {
    # European football clubs / places
    "Alkmaar", "Alsace", "Betis", "Bremen", "Bochum", "Düsseldorf", "Eibar",
    "Leganés", "Liège", "Mönchengladbach", "Saint-Gilloise", "Sociedad",
    "Wolfsburg",
    # J-/B-league club nicknames & sponsor words
    "Antelopes", "B-Corsairs", "Brex", "Diamonds", "Dolphins", "Frontale",
    "Grampus", "Grizzlies", "Jets", "Koalas", "Ladies", "Lakes", "Lamas",
    "Legends", "Rabbits", "S-Pulse", "Suns", "Thunders", "V-Magic", "Warriors",
    "Wave", "Wizards", "Iris",
    # foreign (non-Japanese) surnames scraped from rosters
    "Daniel", "Fazekas", "Friend", "Gaines", "Hawkinson", "Hovasse",
    "Malhotra", "Mawuli", "McLachlan", "Okoye", "Sade", "Santillan",
    "Schafer", "Smith", "Trotter", "Zaccheroni",
}

# Common Japanese surnames absent from the scrape — genuine bolster.
JAPANESE_SURNAME_ADD = {
    "Yamashita", "Goto", "Aoki", "Nishimura", "Ono", "Tamura", "Wada", "Kudo",
    "Miyamoto", "Maruyama", "Imai", "Fujimoto", "Takeda", "Murata", "Ueno",
    "Hirano", "Kojima", "Iwasaki", "Sakurai", "Matsuo", "Kikuchi", "Adachi",
    "Sugimoto", "Hattori", "Komatsu", "Mizuno", "Nishida", "Ishihara",
    "Hirata", "Nakata", "Ohashi", "Fukushima", "Ogura", "Sugiura", "Kuroda",
    "Hamada", "Ishibashi", "Hosokawa", "Nakanishi", "Yamauchi",
}

# Foreign given names / club / sponsor / place tokens in the Japanese first
# slots. (Short legit names like Go/Gen/Jun/Kei/Sho/Yu/Rio/Reo/Rui/Mao/Maya
# are NOT listed — they're real Japanese given names.)
JAPANESE_FIRST_JUNK = {
    # clubs / sponsors / places
    "Albirex", "Alvark", "Borussia", "Cercle", "Eintracht", "Fortuna",
    "Inter", "Júbilo", "Real", "Royale", "SeaHorses", "Sporting", "Standard",
    "Vegalta", "Urawa", "Utsunomiya", "Shimizu", "Shiga", "Shinshu",
    "Memphis", "Texas", "Washington", "United", "Chanson", "Denso", "Fujitsu",
    "Mitsubishi", "Toyota",
    # foreign given names
    "Alberto", "Avi", "Ben", "Corey", "Geoffrey", "James", "Jay", "Josh",
    "Julio", "Marcus", "Nick", "Thomas", "Tom", "Zion", "Anastasia", "Evelyn",
    "Lily", "Monica", "Stephanie", "Futoshi", "Sun",
}

# Modest given-name bolster for parity with the KR/CN work.
JAPANESE_MALE_GIVEN_ADD = {
    "Sora", "Yamato", "Asahi", "Haru", "Yuma", "Ren", "Sosuke", "Itsuki",
    "Kaito", "Riku", "Yusei", "Kanata", "Minato", "Hinata", "Towa", "Aoto",
    "Rikuto", "Souta", "Yuto", "Ryusei",
}
JAPANESE_FEMALE_GIVEN_ADD = {
    "Yua", "Hina", "Mei", "Tsumugi", "Akari", "Sara", "Yuna", "Riko", "Mio",
    "Ichika", "Koharu", "Tsubaki", "Sakura", "Hinata", "Kaede", "Rin", "Yuzuki",
    "Honoka", "Mao", "Saki",
}

# ---------------------------------------------------------------------------
# Taiwan — the `chinese_taiwanese` bucket is clean Wade-Giles but thin, so it
# is bolstered (union) rather than rebuilt. Keeps the Wade-Giles convention
# (Hsieh/Tsai/Chang surnames, hyphenated given names) distinct from the
# mainland pinyin `chinese` pool.
# ---------------------------------------------------------------------------
TAIWAN_SURNAME_ADD = {
    "Wang", "Lee", "Chou", "Yeh", "Kao", "Chien", "Chuang", "Tang", "Weng",
    "Chao", "Tu", "Shih", "Ko", "Chiang", "Hsiung", "Fang", "Ho", "Tai",
    "Shen", "Wei", "Chan", "Hsia", "Chung", "Hsu", "Tsai", "Hung", "Chu",
    "Tseng", "Lai", "Chiu",
}
TAIWAN_MALE_GIVEN_ADD = {
    "Chih-wei", "Chun-hsiang", "Cheng-wei", "Chia-hsien", "Hung-wen",
    "Kuan-ting", "Ming-che", "Po-wei", "Sheng-an", "Tsung-han", "Wei-lun",
    "Yi-chieh", "Yu-cheng", "Chien-hao", "Hsiao-tung", "Jui-chi", "Kai-wei",
    "Tzu-chiang", "Wen-pin", "Yung-chi",
}
TAIWAN_FEMALE_GIVEN_ADD = {
    "Chia-ling", "Hsin-ru", "Pei-yu", "Shu-hua", "Tzu-ching", "Wan-ju",
    "Yi-chun", "Ya-ting", "Hui-chen", "Mei-chen", "Pei-ru", "Shu-ting",
    "Tzu-han", "Wan-ting", "Ya-hsuan", "Yu-chen", "Ching-wen", "Hsiao-yu",
    "Li-hua", "Shih-yu",
}


def _rebuild_korean_first(values, curated, report_slot):
    """Keep hyphenated given names + a small keep-list, add curated; drop the
    rest (surnames, foreign, junk). Reports removed = original - final."""
    final = sorted(
        {v for v in values if "-" in v or v in KOREAN_FIRST_KEEP} | curated
    )
    removed = sorted(set(values) - set(final))
    if removed:
        report_slot[:] = removed
    return final


def _rebuild_chinese_first(values, curated, report_slot):
    """Strip every surname/place/foreign token, re-seed from the curated
    given-name list. Reports removed = original - final."""
    drop = CHINESE_SURNAMES | CHINESE_PLACES | CJK_FIRST_JUNK
    final = sorted({v for v in values if v not in drop} | curated)
    removed = sorted(set(values) - set(final))
    if removed:
        report_slot[:] = removed
    return final


def scrub(dry_run: bool = False) -> dict:
    male = _load("male_first.json")
    female = _load("female_first.json")
    surnames = _load("surnames.json")

    mascots = _mascot_words() | CLUB_JUNK | SCRAPED_SPORTS_JUNK
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
    # Korean & Chinese first-name buckets are rebuilt separately (below).
    first_block = mascots | FIRST_NAME_CITY_JUNK
    handled_first = ("korean", "chinese", "japanese", "chinese_taiwanese")
    for pool_name, pool in (("male_first", male), ("female_first", female)):
        for key, values in pool.items():
            if key in handled_first or not isinstance(values, list):
                continue
            pool[key] = clean_bucket(pool_name, key, values, first_block)

    # Korean / Chinese first names — rebuild from curated given-name pools.
    cjk_first = {
        ("male_first", "korean"):   (male, KOREAN_MALE_GIVEN, _rebuild_korean_first),
        ("female_first", "korean"): (female, KOREAN_FEMALE_GIVEN, _rebuild_korean_first),
        ("male_first", "chinese"):  (male, CHINESE_MALE_GIVEN, _rebuild_chinese_first),
        ("female_first", "chinese"): (female, CHINESE_FEMALE_GIVEN, _rebuild_chinese_first),
    }
    for (pool_name, key), (pool, curated, rebuild) in cjk_first.items():
        values = pool.get(key)
        if not isinstance(values, list):
            continue
        slot: list[str] = []
        pool[key] = rebuild(values, curated, slot)
        if slot:
            report[pool_name][key] = slot

    def bolster(pool_name, key, pool, drop, add):
        """Remove `drop`, union `add`; report removed = original - final."""
        values = pool.get(key)
        if not isinstance(values, list):
            return
        final = sorted((set(values) - drop) | add)
        removed = sorted(set(values) - set(final))
        if removed:
            report[pool_name][key] = removed
        pool[key] = final

    # Japanese first names — clean foreign/club junk, bolster given names.
    jp_first_drop = first_block | JAPANESE_FIRST_JUNK
    bolster("male_first", "japanese", male, jp_first_drop, JAPANESE_MALE_GIVEN_ADD)
    bolster("female_first", "japanese", female, jp_first_drop, JAPANESE_FEMALE_GIVEN_ADD)
    # Taiwanese first names — Wade-Giles bolster (clean any stray mascot/city).
    bolster("male_first", "chinese_taiwanese", male, first_block, TAIWAN_MALE_GIVEN_ADD)
    bolster("female_first", "chinese_taiwanese", female, first_block, TAIWAN_FEMALE_GIVEN_ADD)

    # Surnames: mascots + city sweep; CJK buckets get canonical allowlist.
    for key, values in surnames.items():
        if not isinstance(values, list):
            continue
        if key == "korean":
            removed = sorted(set(values) - KOREAN_SURNAMES)
            if removed:
                report["surnames"][key] = removed
            surnames[key] = sorted(KOREAN_SURNAMES)
        elif key == "chinese":
            removed = sorted(set(values) - CHINESE_SURNAMES)
            if removed:
                report["surnames"][key] = removed
            surnames[key] = sorted(CHINESE_SURNAMES)
        elif key == "japanese":
            final = sorted(
                (set(values) - surname_city_junk - JAPANESE_SURNAME_JUNK)
                | JAPANESE_SURNAME_ADD
            )
            removed = sorted(set(values) - set(final))
            if removed:
                report["surnames"][key] = removed
            surnames[key] = final
        elif key == "chinese_taiwanese":
            final = sorted((set(values) - surname_city_junk) | TAIWAN_SURNAME_ADD)
            removed = sorted(set(values) - set(final))
            if removed:
                report["surnames"][key] = removed
            surnames[key] = final
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
