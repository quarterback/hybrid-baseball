"""
Expand the O27 name pool for the global-leagues rollout.

Companion to expand_o27_names.py. That script seeded the cricket-pipeline
demographics; this one fills the gaps that surfaced while prepping global
leagues:

  * Philippines  — promoted from a single Spanish-colonial bucket to a full
                   set of culturally-distinct pools (Tagalog/Luzon, Visayan,
                   Ilocano, Chinese-Filipino, Spanish-mestizo, Muslim
                   Mindanao). The base `filipino` first-name pools also grow
                   to cover modern Tagalog given names + the distinctive
                   Filipino nickname culture, not just Jose/Carlos.
  * Southeast Asia — Myanmar (burmese), Laos (lao), Singapore Chinese.
                   Brunei reuses the existing `malay` pool.
  * South Asia    — Nepal (nepali), an emerging cricket nation.
  * Israel        — israeli (real WBC baseball nation).
  * Europe        — greek, croatian, slovenian (emerging-baseball nations).
  * Pacific/US    — chamorro (Guam) and hawaiian (Hawaii / Polynesian-
                   American). American Samoa reuses the existing `samoan`
                   pool.

The bucket keys here are wired into regions.json. As with the sibling
script this ADDS / OVERWRITES only the buckets it manages and leaves every
other bucket untouched.

Run from repo root:
    python scripts/expand_global_leagues.py

Idempotent — re-running overwrites the buckets it manages with the
canonical pool below and leaves everything else alone.
"""
from __future__ import annotations
import json
import os
import sys

_NAMES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "o27v2", "data", "names",
)


# ===========================================================================
# MALE FIRST NAMES
# ===========================================================================
MALE_FIRST_ADDITIONS: dict[str, list[str]] = {

    # -------- Philippines: base pool (Tagalog/Luzon, used by most subregions) --------
    # Spanish-Catholic given names + modern Tagalog + the distinctive Filipino
    # nickname / portmanteau culture (Bongbong, Jejomar, Junjun).
    "filipino": [
        "Alberto", "Adrian", "Alexander", "Andres", "Angelo", "Arnel",
        "Benjamin", "Carlos", "Cesar", "Diego", "Eduardo", "Emilio",
        "Enrique", "Ernesto", "Fernando", "Gabriel", "Hermes", "Jose",
        "Juan", "Lorenzo", "Manuel", "Marco", "Mariano", "Miguel",
        "Pablo", "Pedro", "Rafael", "Ramon", "Ricardo", "Roberto",
        "Rodel", "Romeo", "Salvador", "Santiago", "Tomas", "Vicente",
        # modern Tagalog / common given names
        "Aljon", "Arvin", "Ariel", "Bryan", "Carlo", "Cris", "Dante",
        "Darwin", "Dennis", "Edgar", "Edwin", "Efren", "Eljay", "Emmanuel",
        "Eric", "Ferdinand", "Gerald", "Gilbert", "Glenn", "Harold",
        "Isagani", "Jaime", "James", "Jasper", "Jay", "Jayson", "Jaymar",
        "Jericho", "Jerome", "Joel", "John", "Jomar", "Jonas", "Jonathan",
        "Joshua", "Jovit", "Justin", "Kevin", "Larry", "Leandro", "Lito",
        "Marlon", "Mark", "Melvin", "Michael", "Nestor", "Nilo", "Noel",
        "Paolo", "Philip", "Renato", "Rene", "Reymart", "Rico", "Rodrigo",
        "Rogelio", "Rolando", "Ronald", "Ronaldo", "Ryan", "Sonny",
        "Teodoro", "Vincent", "Virgilio", "Wilfredo", "Willie",
        # nickname / portmanteau culture
        "Bong", "Bongbong", "Boy", "Dindo", "Jejomar", "Jhun", "Jun",
        "Junjun", "Onyok", "Pepe", "Toto",
    ],
    # -------- Chinese-Filipino (Chinoy): mostly Western given names --------
    "filipino_chinese": [
        "Alfred", "Andrew", "Anthony", "Brian", "Bryan", "Edgar", "Eric",
        "Frederick", "George", "Henry", "Jack", "Jackson", "John", "Kevin",
        "Lance", "Lucio", "Michael", "Ramon", "Robert", "Stanley",
        "Vincent", "William", "Wilson", "Cheng", "Jun", "Wei", "Sheldon",
        "Brandon", "Marvin", "Patrick", "Justin", "Charles",
    ],
    # -------- Muslim Mindanao (Maranao / Tausug / Maguindanao) --------
    "filipino_muslim": [
        "Abdul", "Abdullah", "Ahmad", "Akmad", "Amir", "Anwar", "Cosain",
        "Datu", "Disomimba", "Faisal", "Guiamel", "Hassan", "Hussein",
        "Ibrahim", "Ismael", "Jamal", "Kahar", "Macapaar", "Maguid",
        "Mohammad", "Nasser", "Norodin", "Omar", "Pangandaman", "Rashid",
        "Rasul", "Salipada", "Sukarno", "Tahir", "Usman", "Yusuf",
    ],

    # -------- Myanmar (Burmese: mononymic, full name = two pool draws) --------
    "burmese": [
        "Aung", "Bo", "Chit", "Hla", "Htin", "Htun", "Kyaw", "Kyi", "Lwin",
        "Maung", "Min", "Moe", "Myint", "Naing", "Nay", "Nyan", "Nyein",
        "Oo", "Phyo", "Pyae", "San", "Sein", "Sithu", "Soe", "Than",
        "Thant", "Thaw", "Thein", "Thet", "Thura", "Tin", "Tun", "Wai",
        "Win", "Wunna", "Ye", "Zaw", "Zeya", "Zin",
    ],
    # -------- Laos --------
    "lao": [
        "Anousone", "Bounlap", "Bounmy", "Bounnhang", "Bounthavy",
        "Choummaly", "Daophet", "Khamla", "Khamphan", "Khamtai", "Kongkeo",
        "Manivanh", "Outhai", "Phankham", "Phout", "Sengdao", "Sengphet",
        "Sisavath", "Somchai", "Somphone", "Somsak", "Souvanna", "Thip",
        "Thongchai", "Vilaysack", "Vongphachanh",
    ],
    # -------- Singapore (Chinese): English + romanized Mandarin given names --------
    "singaporean_chinese": [
        "Aloysius", "Boon Keng", "Bryan", "Chee Wee", "Darren", "Eng Hwa",
        "Glen", "Jia Hao", "Jun Hao", "Jun Jie", "Kai", "Kok Wai", "Marcus",
        "Nicholas", "Shawn", "Terrence", "Wei Jie", "Wei Ming", "Wen",
        "Wesley", "Yi Xuan", "Yong", "Ze Rui", "Zhi Hao",
    ],

    # -------- Nepal --------
    "nepali": [
        "Aakash", "Aarav", "Anil", "Anish", "Bibek", "Bikash", "Bikram",
        "Binod", "Deepak", "Dilip", "Dinesh", "Dipendra", "Gagan", "Gautam",
        "Hari", "Kamal", "Karan", "Kishor", "Krishna", "Kushal", "Madan",
        "Mahesh", "Manoj", "Nabin", "Niraj", "Paras", "Prabin", "Pradeep",
        "Prakash", "Pratik", "Rabindra", "Raju", "Rajesh", "Ramesh", "Rohit",
        "Sagar", "Sandeep", "Sanjay", "Santosh", "Saurav", "Shyam", "Sompal",
        "Suman", "Sunil", "Suraj", "Suresh", "Ujwal",
    ],

    # -------- Israel (Hebrew) --------
    "israeli": [
        "Amit", "Ariel", "Assaf", "Aviv", "Avraham", "Barak", "Daniel",
        "David", "Dor", "Eitan", "Elad", "Eli", "Eyal", "Gal", "Gilad",
        "Guy", "Idan", "Itai", "Itamar", "Lior", "Maor", "Moshe", "Nadav",
        "Nir", "Noam", "Ofer", "Omer", "Oren", "Ron", "Roni", "Shai",
        "Shimon", "Tal", "Tom", "Tomer", "Uri", "Yair", "Yarden", "Yehuda",
        "Yonatan", "Yosef", "Yuval", "Ziv",
    ],

    # -------- Greece --------
    "greek": [
        "Achilleas", "Alexandros", "Andreas", "Antonis", "Apostolos",
        "Aristotelis", "Charalampos", "Christos", "Dimitris", "Dimitrios",
        "Evangelos", "Fotis", "Georgios", "Giannis", "Grigoris", "Ioannis",
        "Konstantinos", "Kostas", "Lefteris", "Manolis", "Michalis",
        "Nikolaos", "Nikos", "Panagiotis", "Pavlos", "Petros", "Sotiris",
        "Spiros", "Stavros", "Stefanos", "Thanasis", "Theodoros",
        "Vangelis", "Vasilis", "Vassilios",
    ],
    # -------- Croatia --------
    "croatian": [
        "Andrej", "Ante", "Borna", "Bruno", "Davor", "Dejan", "Dino",
        "Domagoj", "Filip", "Goran", "Hrvoje", "Ivan", "Ivica", "Josip",
        "Karlo", "Krešimir", "Luka", "Marin", "Mario", "Marko", "Mate",
        "Matej", "Mislav", "Nikola", "Petar", "Roko", "Stjepan", "Tomislav",
        "Toni", "Vedran", "Zoran",
    ],
    # -------- Slovenia --------
    "slovenian": [
        "Aljaž", "Andraž", "Anže", "Blaž", "Bojan", "Domen", "Gašper",
        "Gregor", "Jaka", "Jan", "Janez", "Jure", "Klemen", "Luka", "Marko",
        "Matej", "Matic", "Miha", "Nejc", "Primož", "Rok", "Sebastijan",
        "Tilen", "Tim", "Urban", "Žan", "Žiga",
    ],

    # -------- Guam (Chamorro): Spanish-Catholic + American + Chamorro --------
    "chamorro": [
        "Antonio", "Ben", "Carlos", "Eddie", "Felix", "Francisco", "Frank",
        "Greg", "Hurao", "Ignacio", "Jesse", "Joaquin", "Joe", "Jose",
        "Juan", "Kobe", "Kurt", "Kyle", "Manny", "Matapang", "Pablo",
        "Pedro", "Ricky", "Roman", "Ryan", "Tomas", "Tony", "Travis",
        "Vicente",
    ],
    # -------- Hawaii (Native Hawaiian + Polynesian-American) --------
    "hawaiian": [
        "Bronson", "Hoku", "Ikaika", "Kai", "Kainoa", "Kaipo", "Kaleo",
        "Kanoa", "Kawika", "Keanu", "Kekoa", "Keola", "Keoni", "Koa",
        "Kolten", "Kurt", "Lani", "Lono", "Makana", "Makoa", "Mano", "Manu",
        "Micah", "Nainoa", "Pono", "Shane", "Sione", "Tama",
    ],
}


# ===========================================================================
# FEMALE FIRST NAMES (smaller; league default is male)
# ===========================================================================
FEMALE_FIRST_ADDITIONS: dict[str, list[str]] = {
    # -------- Puerto Rico (fills a pre-existing male-only gap so female/mixed
    # leagues don't fall back to "Player N" for PR draws) --------
    "puerto_rican": [
        "Adriana", "Alejandra", "Ana", "Andrea", "Carmen", "Daniela",
        "Eva", "Gabriela", "Glorimar", "Isabel", "Ivelisse", "Jocelyn",
        "Juana", "Karla", "Lourdes", "Luz", "María", "Mariana", "Maritza",
        "Marta", "Mayra", "Mónica", "Nancy", "Natalia", "Nilda", "Norma",
        "Paola", "Rosa", "Sandra", "Sofía", "Vanessa", "Verónica", "Wanda",
        "Yadira", "Yesenia", "Zoraida",
    ],
    "filipino": [
        "Andrea", "Angel", "Angelica", "Ana", "Bea", "Carmela", "Charmaine",
        "Cherry", "Christine", "Claire", "Corazon", "Cristina", "Daisy",
        "Diana", "Divina", "Dolores", "Donna", "Elaine", "Elena", "Ellen",
        "Erlinda", "Evangeline", "Fe", "Flor", "Gemma", "Geraldine", "Gina",
        "Gloria", "Grace", "Hazel", "Imelda", "Imee", "Irene", "Isabel",
        "Jasmine", "Jennifer", "Jessa", "Joana", "Jocelyn", "Josefina",
        "Joy", "Kristine", "Lea", "Liza", "Lorna", "Lourdes", "Maria",
        "Maricel", "Marisol", "Marites", "Marivic", "Mary", "Mayumi",
        "Melinda", "Mercedes", "Michelle", "Mutya", "Nimfa", "Norma",
        "Perla", "Pilar", "Princess", "Regina", "Reyna", "Rhea", "Rosa",
        "Rosario", "Rowena", "Sharon", "Sofia", "Teresa", "Trinidad",
        "Veronica", "Vilma", "Yolanda", "Zenaida",
    ],
    "filipino_chinese": [
        "Angel", "Anne", "Charlene", "Charmaine", "Cherry", "Cheryl",
        "Christine", "Cynthia", "Diana", "Grace", "Janet", "Jasmine",
        "Jennifer", "Joyce", "Karen", "Mei", "Michelle", "Sharon",
        "Stephanie", "Vivian",
    ],
    "filipino_muslim": [
        "Amina", "Bai", "Farida", "Hanan", "Jamila", "Norhata", "Norjana",
        "Potre", "Raisa", "Sahara", "Salma", "Saripa", "Sittie", "Yasmin",
        "Zainab",
    ],
    "burmese": [
        "Aye", "Cho", "Ei", "Hla", "Hnin", "Htay", "Kay", "Khin", "Mar",
        "May", "Mi", "Moe", "Mya", "Nilar", "Nu", "Nwe", "Ohnmar", "Phyu",
        "San", "Sandar", "Su", "Thandar", "Thazin", "Thida", "Thinzar",
        "Wai", "Yadana", "Yin", "Zar",
    ],
    "lao": [
        "Bouakham", "Channary", "Chanthala", "Daophet", "Dara", "Keo",
        "Khamphet", "Lamphone", "Malychan", "Maly", "Manivanh", "Nilandone",
        "Noy", "Phaivanh", "Phetdala", "Phonsavanh", "Saysamone", "Souvanny",
        "Vandara", "Vilayphone",
    ],
    "singaporean_chinese": [
        "Charmaine", "Cheryl", "Germaine", "Hui Ling", "Jia Min", "Joanne",
        "Li Ying", "Mei Ling", "Rachel", "Serene", "Shu Hui", "Valerie",
        "Wan Ting", "Wendy", "Xin Yi", "Yi Ling", "Zhi Ying",
    ],
    "nepali": [
        "Aarati", "Anita", "Anjali", "Bandana", "Bhawana", "Binita", "Deepa",
        "Gita", "Kabita", "Kalpana", "Manisha", "Nirmala", "Pratima", "Puja",
        "Rabina", "Rachana", "Rashmi", "Reshma", "Sabina", "Samjhana",
        "Sangita", "Saraswati", "Sarita", "Shanti", "Sita", "Smriti",
        "Sneha", "Sushila", "Usha", "Yashoda",
    ],
    "israeli": [
        "Adi", "Anat", "Avigail", "Bar", "Dana", "Eden", "Gal", "Hila",
        "Hodaya", "Inbar", "Liat", "Lior", "Maya", "Meital", "Michal",
        "Moran", "Naama", "Noa", "Noga", "Ofir", "Ortal", "Reut", "Roni",
        "Shani", "Shira", "Sivan", "Tal", "Tamar", "Yael", "Yarden",
    ],
    "greek": [
        "Anastasia", "Angeliki", "Anna", "Athina", "Christina", "Chrysanthi",
        "Despina", "Dimitra", "Eirini", "Eleni", "Eva", "Fotini", "Georgia",
        "Ioanna", "Kalliopi", "Katerina", "Konstantina", "Maria", "Nikoletta",
        "Panagiota", "Paraskevi", "Sofia", "Stavroula", "Stella", "Theodora",
        "Vasiliki", "Zoi",
    ],
    "croatian": [
        "Ana", "Antonia", "Dora", "Ema", "Iva", "Ivana", "Karla", "Klara",
        "Lana", "Lucija", "Maja", "Marija", "Marina", "Martina", "Mia",
        "Nika", "Petra", "Sara", "Tena", "Valentina",
    ],
    "slovenian": [
        "Ana", "Eva", "Ema", "Katja", "Klara", "Lara", "Maja", "Manca",
        "Mojca", "Neža", "Nina", "Petra", "Pia", "Sara", "Špela", "Tina",
        "Tjaša", "Urška", "Zala", "Živa",
    ],
    "chamorro": [
        "Ana", "Bernadette", "Carmen", "Dolores", "Frances", "Jasmine",
        "Josefa", "Jovita", "Joleen", "Kayla", "Lourdes", "Maria",
        "Michelle", "Nina", "Rita", "Rosa", "Sandra", "Tasi", "Teresita",
        "Vivian",
    ],
    "hawaiian": [
        "Alana", "Healani", "Iolana", "Kaila", "Kailani", "Kalena", "Kanani",
        "Keala", "Kehau", "Kiana", "Lani", "Leila", "Leilani", "Lokelani",
        "Mahina", "Maile", "Malia", "Moana", "Nalani", "Noelani", "Pua",
        "Puanani", "Ululani", "Waiola",
    ],
}


# ===========================================================================
# SURNAMES
# ===========================================================================
SURNAME_ADDITIONS: dict[str, list[str]] = {

    # -------- Philippines: base pool (Spanish-derived + native Tagalog) --------
    "filipino": [
        "Abad", "Aguilar", "Alonzo", "Alvarez", "Andrada", "Angeles",
        "Aquino", "Bautista", "Bernardo", "Bonifacio", "Cabrera", "Castillo",
        "Castro", "Concepcion", "Corpuz", "Cruz", "David", "De Guzman",
        "De Leon", "Del Rosario", "Dela Cruz", "Diaz", "Domingo", "Espiritu",
        "Estrada", "Fajardo", "Fernandez", "Flores", "Galang", "Garcia",
        "Geronimo", "Gonzales", "Guevarra", "Hernandez", "Ignacio", "Lopez",
        "Macapagal", "Magsaysay", "Manalo", "Marasigan", "Marcos", "Martinez",
        "Mendoza", "Mercado", "Natividad", "Navarro", "Ocampo", "Padilla",
        "Pangilinan", "Pascual", "Perez", "Pineda", "Ramirez", "Ramos",
        "Reyes", "Rivera", "Rodriguez", "Roxas", "Salonga", "Sanchez",
        "Santiago", "Santos", "Soriano", "Tolentino", "Torres", "Valdez",
        "Velasco", "Villanueva", "Villar",
    ],
    # -------- Visayan (Cebuano / Ilonggo) surnames --------
    "filipino_visayan": [
        "Abellana", "Abellanosa", "Alcantara", "Almendras", "Bacus", "Booc",
        "Cabahug", "Cabaero", "Cañete", "Cuenco", "Durano", "Gabuya",
        "Gentallan", "Gimena", "Gullas", "Inocian", "Jakosalem", "Labella",
        "Maglasang", "Osmeña", "Paras", "Pepito", "Pesquera", "Quijano",
        "Rama", "Sotto", "Tabal", "Yap",
    ],
    # -------- Ilocano (North Luzon) surnames --------
    "filipino_ilocano": [
        "Ablan", "Acosta", "Agbayani", "Aglipay", "Bautista", "Bersamin",
        "Corpuz", "Crisologo", "Duldulao", "Fariñas", "Foronda", "Gaerlan",
        "Galima", "Lazaro", "Macanas", "Marcos", "Nalupta", "Pacis",
        "Padaca", "Peralta", "Pidlaoan", "Rabanal", "Singson", "Tabios",
        "Tugade", "Valdez", "Ver", "Verzosa",
    ],
    # -------- Chinese-Filipino (Chinoy / Chinese-mestizo) surnames --------
    "filipino_chinese": [
        "Ang", "Chan", "Chua", "Co", "Cojuangco", "Cua", "Dy", "Go",
        "Gokongwei", "Gotianun", "Lao", "Lim", "Ng", "Ong", "Que", "Sia",
        "Sy", "Sycip", "Tan", "Tee", "Tiongson", "Tiu", "Ty", "Uy", "Yang",
        "Yao", "Yap", "Yu",
    ],
    # -------- Muslim Mindanao surnames --------
    "filipino_muslim": [
        "Adiong", "Alonto", "Ampatuan", "Balindong", "Balt", "Dimakuta",
        "Dimaporo", "Dimaukom", "Disomimba", "Guiani", "Hataman", "Loong",
        "Lucman", "Macapaar", "Macarambon", "Mangudadatu", "Mastura",
        "Matalam", "Misuari", "Pangandaman", "Pendatun", "Romato", "Salapuddin",
        "Sangki", "Sema", "Sinarimbo", "Sinsuat", "Tamano", "Ututalum",
    ],

    # -------- Myanmar (mononymic — same component pool as first names) --------
    "burmese": [
        "Aung", "Bo", "Chit", "Hla", "Htin", "Htun", "Kyaw", "Kyi", "Lwin",
        "Maung", "Min", "Moe", "Myint", "Naing", "Nay", "Nyan", "Nyein",
        "Oo", "Phyo", "Pyae", "San", "Sein", "Sithu", "Soe", "Than",
        "Thant", "Thaw", "Thein", "Thet", "Thura", "Tin", "Tun", "Wai",
        "Win", "Wunna", "Ye", "Zaw", "Zeya", "Zin",
    ],
    # -------- Laos --------
    "lao": [
        "Chanthavong", "Douangchak", "Inthachack", "Inthavong", "Keomany",
        "Luangaphay", "Luangrath", "Manivong", "Phetsarath", "Phimmasone",
        "Phomma", "Phomvihane", "Rajavong", "Rasphone", "Sananikone",
        "Sengdara", "Sengsavang", "Sisoulith", "Souvannavong", "Vongphakdy",
        "Vongsa", "Vorachit", "Xayasith", "Xayasith",
    ],

    # -------- Nepal --------
    "nepali": [
        "Acharya", "Adhikari", "Airee", "Bhandari", "Bhattarai", "Bhurtel",
        "Chaudhary", "Dahal", "Gurung", "Joshi", "Kami", "Karki", "Khadka",
        "Koirala", "Lama", "Lamichhane", "Magar", "Maharjan", "Malla",
        "Pandey", "Paudel", "Pokharel", "Pradhan", "Rai", "Rana", "Regmi",
        "Sah", "Sapkota", "Shah", "Shahi", "Sharma", "Shrestha", "Subedi",
        "Tamang", "Thapa",
    ],

    # -------- Israel --------
    "israeli": [
        "Aharon", "Amar", "Avraham", "Azoulay", "Bar", "Barak", "Ben-David",
        "Biton", "Cohen", "Dahan", "Edri", "Eliyahu", "Friedman", "Gabai",
        "Goldberg", "Hadad", "Halevi", "Katz", "Klein", "Levi", "Malka",
        "Mizrahi", "Moyal", "Ohayon", "Peretz", "Rosenberg", "Segal",
        "Segev", "Shapira", "Stein", "Tal", "Tzur", "Weiss", "Yosef",
    ],

    # -------- Greece --------
    "greek": [
        "Alexopoulos", "Angelopoulos", "Antoniou", "Christodoulou",
        "Demetriou", "Dimitriou", "Dragatakis", "Fotopoulos", "Georgiou",
        "Gerasimou", "Ioannidis", "Karagiannis", "Katsaros", "Konstantinidis",
        "Lambrou", "Makris", "Manolopoulos", "Nikolaidis", "Oikonomou",
        "Papadakis", "Papadopoulos", "Pappas", "Pavlidis", "Petrou", "Samaras",
        "Sotiriou", "Spanos", "Stavrou", "Theodorou", "Triantafyllou",
        "Tsiartas", "Vasileiou", "Vlachos", "Zografos",
    ],
    # -------- Croatia --------
    "croatian": [
        "Babić", "Blažević", "Božić", "Brozović", "Grgić", "Horvat", "Jurić",
        "Knežević", "Kovač", "Kovačević", "Kramarić", "Lovren", "Mandžukić",
        "Marić", "Marković", "Matić", "Modrić", "Novak", "Pavić", "Perić",
        "Perišić", "Petrović", "Radić", "Rakitić", "Šimić", "Tomić",
        "Vlašić", "Vuković",
    ],
    # -------- Slovenia --------
    "slovenian": [
        "Bizjak", "Doncic", "Golob", "Horvat", "Hribar", "Kogoj", "Korošec",
        "Kos", "Kovač", "Kovačič", "Krajnc", "Kralj", "Mlakar", "Novak",
        "Oblak", "Pavlin", "Pirc", "Potočnik", "Rozman", "Sever", "Turk",
        "Vidmar", "Žagar", "Zupan", "Zupančič",
    ],

    # -------- Guam (Chamorro: heavily Spanish + indigenous) --------
    "chamorro": [
        "Aguon", "Atoigue", "Babauta", "Blas", "Borja", "Camacho", "Castro",
        "Charfauros", "Cruz", "Duenas", "Flores", "Guerrero", "Leon Guerrero",
        "Mafnas", "Mendiola", "Naputi", "Pangelinan", "Perez", "Quichocho",
        "Quinata", "Quintanilla", "Reyes", "Sablan", "Salas", "San Agustin",
        "San Nicolas", "Santos", "Taijeron", "Taitano", "Tenorio", "Torres",
        "Untalan",
    ],
    # -------- Hawaii (Native Hawaiian + Portuguese / Asian-American island mix) --------
    "hawaiian": [
        "Akana", "Akau", "Aki", "Akiona", "Apana", "Cabral", "Fernandez",
        "Ho", "Kahale", "Kahanu", "Kahaulelio", "Kalama", "Kalani", "Kaluna",
        "Kamai", "Kamaka", "Kamealoha", "Kanahele", "Kealoha", "Lee",
        "Mahoe", "Makaiau", "Medeiros", "Naeole", "Naki", "Nakoa", "Pang",
        "Pico", "Souza", "Vierra", "Wong",
    ],
}


# ===========================================================================
# RUNNER  (identical merge semantics to expand_o27_names.py)
# ===========================================================================

def _patch(path: str, additions: dict[str, list[str]]) -> tuple[int, int]:
    """Merge `additions` into the JSON file at `path`. Managed keys are
    overwritten with the (de-duped, order-preserving) lists below; all other
    existing keys are left untouched. Returns (n_keys_total, n_keys_changed)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    n_changed = 0
    for k, v in additions.items():
        seen: set[str] = set()
        cleaned: list[str] = []
        for n in v:
            if n not in seen:
                seen.add(n)
                cleaned.append(n)
        if data.get(k) != cleaned:
            n_changed += 1
        data[k] = cleaned
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return len(data), n_changed


def main() -> None:
    targets = [
        ("male_first.json",   MALE_FIRST_ADDITIONS),
        ("female_first.json", FEMALE_FIRST_ADDITIONS),
        ("surnames.json",     SURNAME_ADDITIONS),
    ]
    for fname, additions in targets:
        path = os.path.join(_NAMES_DIR, fname)
        total, changed = _patch(path, additions)
        print(f"{fname}: {changed} keys added/updated, {total} total buckets")


if __name__ == "__main__":
    sys.exit(main())
