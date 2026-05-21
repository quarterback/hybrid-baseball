"""
Weather model — lightweight categorical game conditions.

Five categorical variables stamped on every game at SCHEDULE time:

  temperature  cold / mild / warm / hot
  wind         out / neutral / in / cross
  humidity     dry / normal / humid
  precipitation none / light / heavy   (heavy is flagged but still plays)
  cloud        clear / overcast / dusk

Drawn from a city -> climatological archetype lookup, with a per-archetype
per-month tier distribution. Twelve archetypes cover the catalogue:
desert, coastal_cool, coastal_warm, continental_cold, continental_warm,
tropical, mountain, subarctic, mediterranean, tropical_monsoon,
subtropical_humid, arid_steppe.

City lookup chain: exact city → city with trailing 2/3-letter country
code stripped → country-code default (e.g. "FIN" → subarctic) →
continental_warm. So a custom team in "Helsinki FIN" gets subarctic
weather without anyone having to add Helsinki to the per-city table.

Engine touch points (bounded — DON'T sprinkle reads beyond these):

  prob.py    — HR weight, contact-quality balance, K rate, error rate,
               fatigue threshold (stamina decay)
  state.py   — Weather lives on GameState as `weather`

Everything else passes Weather as context, never reads from it.

Magnitude budget: every individual multiplier sits in [0.85, 1.20].
Weather is flavor, not outcome determination.
"""
from __future__ import annotations
import datetime
import random
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Tier vocabularies
# ---------------------------------------------------------------------------

TEMPERATURE_TIERS = ("cold", "mild", "warm", "hot")
WIND_TIERS        = ("out", "neutral", "in", "cross")
HUMIDITY_TIERS    = ("dry", "normal", "humid")
PRECIP_TIERS      = ("none", "light", "heavy")
CLOUD_TIERS       = ("clear", "overcast", "dusk")


@dataclass(frozen=True)
class Weather:
    temperature: str = "mild"
    wind:        str = "neutral"
    humidity:    str = "normal"
    precip:      str = "none"
    cloud:       str = "clear"

    def to_row(self) -> dict:
        return {
            "temperature_tier": self.temperature,
            "wind_tier":        self.wind,
            "humidity_tier":    self.humidity,
            "precip_tier":      self.precip,
            "cloud_tier":       self.cloud,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Weather":
        if not row:
            return NEUTRAL
        return cls(
            temperature=str(row.get("temperature_tier") or "mild"),
            wind=str(row.get("wind_tier") or "neutral"),
            humidity=str(row.get("humidity_tier") or "normal"),
            precip=str(row.get("precip_tier") or "none"),
            cloud=str(row.get("cloud_tier") or "clear"),
        )

    def short_label(self) -> str:
        """Compact one-line string for the box-score weather strip."""
        temp = {"cold": "Cold", "mild": "Mild", "warm": "Warm", "hot": "Hot"}[self.temperature]
        wind = {
            "out":     "Wind out",
            "neutral": "Wind calm",
            "in":      "Wind in",
            "cross":   "Wind cross",
        }[self.wind]
        cloud = {"clear": "Clear", "overcast": "Overcast", "dusk": "Dusk"}[self.cloud]
        bits = [temp, wind, cloud]
        if self.precip == "light":
            bits.append("Light rain")
        elif self.precip == "heavy":
            bits.append("Heavy rain")
        if self.humidity == "humid":
            bits.append("Humid")
        elif self.humidity == "dry":
            bits.append("Dry")
        return " · ".join(bits)

    # Representative °F for each tier — midpoint of the rough real-world
    # range we associate with the categorical bucket. Used by the
    # newspaper-style box-score footer.
    _F_BY_TEMP = {"cold": 52, "mild": 66, "warm": 78, "hot": 90}

    def fahrenheit(self) -> int:
        return self._F_BY_TEMP.get(self.temperature, 70)

    def box_score_line(self) -> str:
        """Newspaper-footer phrase: '78°F, wind out, clear, humid.'

        Suppresses the humidity descriptor when precipitation is reported
        — saying "heavy rain, dry" is incoherent on the page even if both
        tiers were drawn independently.
        """
        wind = {
            "out":     "wind out",
            "neutral": "calm",
            "in":      "wind in",
            "cross":   "wind cross",
        }[self.wind]
        cloud = {"clear": "clear", "overcast": "overcast", "dusk": "dusk"}[self.cloud]
        bits = [f"{self.fahrenheit()}°F", wind, cloud]
        raining = self.precip in ("light", "heavy")
        if self.precip == "light":
            bits.append("light rain")
        elif self.precip == "heavy":
            bits.append("heavy rain")
        if not raining:
            if self.humidity == "humid":
                bits.append("humid")
            elif self.humidity == "dry":
                bits.append("dry")
        return ", ".join(bits) + "."


NEUTRAL = Weather()


# ---------------------------------------------------------------------------
# City -> archetype mapping
# ---------------------------------------------------------------------------

# Hand-authored from the team catalogue in o27v2/data/teams_database.json.
# Unknown cities fall back to "continental_warm" (the broadest archetype).

_CITY_ARCHETYPES = {
    # desert: high heat, low humidity, light wind, very little rain
    "Albuquerque":      "desert",
    "Arizona":          "desert",
    "Las Vegas":        "desert",
    "Midland":          "desert",
    "El Paso":          "desert",

    # coastal_cool: mild, frequent overcast, breezy on/off shore
    "San Francisco":    "coastal_cool",
    "Oakland":          "coastal_cool",
    "Seattle":          "coastal_cool",
    "Portland":         "coastal_cool",
    "Tacoma":           "coastal_cool",
    "Salem":            "coastal_cool",

    # coastal_warm: warm, humid, sea breeze, summer thunderstorms
    "Los Angeles":      "coastal_warm",
    "San Diego":        "coastal_warm",
    "Sacramento":       "coastal_warm",  # inland but hot/dry-warm; close enough at v1
    "Tampa":            "coastal_warm",
    "Tampa Bay":        "coastal_warm",
    "Clearwater":       "coastal_warm",
    "Daytona":          "coastal_warm",
    "Jacksonville":     "coastal_warm",
    "Savannah":         "coastal_warm",
    "Charlotte":        "coastal_warm",
    "Carolina":         "coastal_warm",
    "Greenville":       "coastal_warm",
    "Greensboro":       "coastal_warm",
    "Wilmington":       "coastal_warm",
    "Lynchburg":        "coastal_warm",
    "Myrtle Beach":     "coastal_warm",
    "Asheville":        "coastal_warm",
    "Durham":           "coastal_warm",
    "Richmond":         "coastal_warm",
    "Norfolk":          "coastal_warm",
    "Zebulon":          "coastal_warm",

    # continental_cold: cool springs, hot summers, big swings, often windy
    "Chicago":          "continental_cold",
    "Cleveland":        "continental_cold",
    "Detroit":          "continental_cold",
    "Milwaukee":        "continental_cold",
    "Minnesota":        "continental_cold",
    "Pittsburgh":       "continental_cold",
    "Buffalo":          "continental_cold",
    "Toronto":          "continental_cold",
    "Montreal":         "continental_cold",
    "Boston":           "continental_cold",
    "New York":         "continental_cold",
    "Pawtucket":        "continental_cold",
    "Hartford":         "continental_cold",
    "Trenton":          "continental_cold",
    "Lehigh Valley":    "continental_cold",
    "Binghamton":       "continental_cold",
    "Lansing":          "continental_cold",
    "Cedar Rapids":     "continental_cold",
    "Columbus":         "continental_cold",
    "Toledo":           "continental_cold",
    "Indianapolis":     "continental_cold",
    "Omaha":            "continental_cold",
    "Harrisburg":       "continental_cold",

    # continental_warm: hot humid summers, mild winters, thunderstorms
    "Atlanta":          "continental_warm",
    "Baltimore":        "continental_warm",
    "Cincinnati":       "continental_warm",
    "Houston":          "continental_warm",
    "Kansas City":      "continental_warm",
    "Nashville":        "continental_warm",
    "Philadelphia":     "continental_warm",
    "St. Louis":        "continental_warm",
    "Texas":            "continental_warm",
    "Washington":       "continental_warm",
    "Arkansas":         "continental_warm",
    "Birmingham":       "continental_warm",
    "Chattanooga":      "continental_warm",
    "Corpus Christi":   "continental_warm",
    "Frisco":           "continental_warm",
    "Jackson":          "continental_warm",
    "Lakewood":         "continental_warm",
    "Montgomery":       "continental_warm",
    "Peoria":           "continental_warm",
    "Round Rock":       "continental_warm",
    "San Antonio":      "continental_warm",

    # tropical: warm, very humid, frequent showers
    "Miami":            "tropical",
    "Biloxi":           "tropical",

    # mountain: cool, dry, thin air, swirling wind
    "Colorado":         "mountain",
    "Colorado Springs": "mountain",
    "Salt Lake City":   "mountain",

    # ---------- INTERNATIONAL ---------------------------------------------
    # Listed where the country fallback is wrong for a specific city — e.g.
    # Australia falls back to subtropical_humid which fits Sydney/Brisbane
    # but not Perth (mediterranean) or Alice Springs (desert).

    # Nordic — Finland / Sweden / Norway / Iceland: subarctic everywhere
    "Helsinki":         "subarctic",
    "Tampere":          "subarctic",
    "Turku":            "subarctic",
    "Espoo":            "subarctic",
    "Vantaa":           "subarctic",
    "Oulu":             "subarctic",
    "Lahti":            "subarctic",
    "Kuopio":           "subarctic",
    "Jyväskylä":        "subarctic",
    "Vaasa":            "subarctic",
    "Joensuu":          "subarctic",
    "Pori":             "subarctic",
    "Lappeenranta":     "subarctic",
    "Hämeenlinna":      "subarctic",
    "Rovaniemi":        "subarctic",
    "Mikkeli":          "subarctic",
    "Kotka":            "subarctic",
    "Salo":             "subarctic",
    "Porvoo":           "subarctic",
    "Kouvola":          "subarctic",
    "Stockholm":        "subarctic",
    "Gothenburg":       "subarctic",
    "Malmö":            "subarctic",
    "Uppsala":          "subarctic",
    "Oslo":             "subarctic",
    "Bergen":           "coastal_cool",
    "Trondheim":        "subarctic",
    "Reykjavik":        "subarctic",
    "Copenhagen":       "continental_cold",

    # Baltics
    "Tallinn":          "subarctic",
    "Riga":             "subarctic",
    "Vilnius":          "continental_cold",

    # UK / Ireland
    "London":           "coastal_cool",
    "Manchester":       "coastal_cool",
    "Birmingham":       "coastal_cool",
    "Liverpool":        "coastal_cool",
    "Leeds":            "coastal_cool",
    "Edinburgh":        "coastal_cool",
    "Glasgow":          "coastal_cool",
    "Belfast":          "coastal_cool",
    "Dublin":           "coastal_cool",
    "Cardiff":          "coastal_cool",

    # Western Europe
    "Paris":            "continental_warm",
    "Lyon":             "continental_warm",
    "Marseille":        "mediterranean",
    "Nice":             "mediterranean",
    "Toulouse":         "continental_warm",
    "Bordeaux":         "continental_warm",
    "Berlin":           "continental_warm",
    "Munich":           "continental_warm",
    "Hamburg":          "continental_warm",
    "Frankfurt":        "continental_warm",
    "Cologne":          "continental_warm",
    "Vienna":           "continental_warm",
    "Zürich":           "continental_warm",
    "Amsterdam":        "coastal_cool",
    "Rotterdam":        "coastal_cool",
    "Brussels":         "continental_warm",

    # Mediterranean Europe
    "Madrid":           "mediterranean",
    "Barcelona":        "mediterranean",
    "Valencia":         "mediterranean",
    "Seville":          "mediterranean",
    "Bilbao":           "coastal_cool",  # Atlantic-influenced
    "Lisbon":           "mediterranean",
    "Porto":            "mediterranean",
    "Rome":             "mediterranean",
    "Milan":            "continental_warm",
    "Naples":           "mediterranean",
    "Turin":            "continental_warm",
    "Bologna":          "continental_warm",
    "Florence":         "mediterranean",
    "Venice":           "mediterranean",
    "Athens":           "mediterranean",
    "Thessaloniki":     "mediterranean",

    # Eastern Europe
    "Warsaw":           "continental_cold",
    "Krakow":           "continental_cold",
    "Prague":           "continental_warm",
    "Budapest":         "continental_warm",
    "Bucharest":        "continental_warm",
    "Belgrade":         "continental_warm",
    "Sofia":            "continental_warm",
    "Zagreb":           "continental_warm",
    "Moscow":           "continental_cold",
    "Saint Petersburg": "subarctic",
    "Kyiv":             "continental_cold",
    "Istanbul":         "mediterranean",
    "Ankara":           "arid_steppe",

    # East Asia
    "Tokyo":            "subtropical_humid",
    "Osaka":            "subtropical_humid",
    "Yokohama":         "subtropical_humid",
    "Nagoya":           "subtropical_humid",
    "Sapporo":          "continental_cold",
    "Fukuoka":          "subtropical_humid",
    "Hiroshima":        "subtropical_humid",
    "Sendai":           "continental_warm",
    "Kobe":             "subtropical_humid",
    "Kyoto":            "subtropical_humid",
    "Seoul":            "subtropical_humid",
    "Busan":            "subtropical_humid",
    "Incheon":          "subtropical_humid",
    "Daegu":            "subtropical_humid",
    "Beijing":          "continental_warm",
    "Shanghai":         "subtropical_humid",
    "Guangzhou":        "tropical_monsoon",
    "Shenzhen":         "tropical_monsoon",
    "Chengdu":          "subtropical_humid",
    "Wuhan":            "subtropical_humid",
    "Hong Kong":        "tropical_monsoon",
    "Taipei":           "subtropical_humid",
    "Kaohsiung":        "tropical_monsoon",
    "Ulaanbaatar":      "continental_cold",

    # Southeast Asia
    "Bangkok":          "tropical_monsoon",
    "Chiang Mai":       "tropical_monsoon",
    "Phuket":           "tropical_monsoon",
    "Ho Chi Minh City": "tropical_monsoon",
    "Saigon":           "tropical_monsoon",
    "Hanoi":            "tropical_monsoon",
    "Manila":           "tropical_monsoon",
    "Cebu":             "tropical_monsoon",
    "Jakarta":          "tropical_monsoon",
    "Surabaya":         "tropical_monsoon",
    "Bali":             "tropical_monsoon",
    "Kuala Lumpur":     "tropical_monsoon",
    "Penang":           "tropical_monsoon",
    "Singapore":        "tropical_monsoon",
    "Phnom Penh":       "tropical_monsoon",
    "Yangon":           "tropical_monsoon",
    "Vientiane":        "tropical_monsoon",

    # South Asia
    "Mumbai":           "tropical_monsoon",
    "Delhi":            "subtropical_humid",
    "New Delhi":        "subtropical_humid",
    "Bangalore":        "tropical_monsoon",
    "Bengaluru":        "tropical_monsoon",
    "Chennai":          "tropical_monsoon",
    "Kolkata":          "tropical_monsoon",
    "Hyderabad":        "tropical_monsoon",
    "Pune":             "tropical_monsoon",
    "Karachi":          "arid_steppe",
    "Lahore":           "arid_steppe",
    "Islamabad":        "subtropical_humid",
    "Dhaka":            "tropical_monsoon",
    "Colombo":          "tropical",
    "Kathmandu":        "continental_warm",

    # Middle East
    "Dubai":            "desert",
    "Abu Dhabi":        "desert",
    "Doha":             "desert",
    "Riyadh":           "desert",
    "Jeddah":           "desert",
    "Mecca":            "desert",
    "Tehran":           "arid_steppe",
    "Baghdad":          "desert",
    "Tel Aviv":         "mediterranean",
    "Jerusalem":        "mediterranean",
    "Beirut":           "mediterranean",
    "Amman":            "arid_steppe",
    "Cairo":            "desert",
    "Alexandria":       "mediterranean",

    # Africa
    "Lagos":            "tropical",
    "Abuja":            "tropical",
    "Nairobi":          "tropical",
    "Mombasa":          "tropical",
    "Addis Ababa":      "continental_warm",
    "Dar es Salaam":    "tropical",
    "Kampala":          "tropical",
    "Kigali":           "tropical",
    "Accra":            "tropical",
    "Dakar":            "tropical",
    "Casablanca":       "mediterranean",
    "Marrakech":        "arid_steppe",
    "Tunis":            "mediterranean",
    "Algiers":          "mediterranean",
    "Johannesburg":     "subtropical_humid",
    "Cape Town":        "mediterranean",
    "Durban":           "subtropical_humid",
    "Pretoria":         "subtropical_humid",
    "Luanda":           "tropical",
    "Antananarivo":     "tropical",

    # Australia & NZ & Pacific
    "Sydney":           "subtropical_humid",
    "Melbourne":        "subtropical_humid",
    "Brisbane":         "subtropical_humid",
    "Perth":            "mediterranean",
    "Adelaide":         "mediterranean",
    "Canberra":         "continental_warm",
    "Hobart":           "coastal_cool",
    "Darwin":           "tropical_monsoon",
    "Alice Springs":    "desert",
    "Auckland":         "coastal_cool",
    "Wellington":       "coastal_cool",
    "Christchurch":     "coastal_cool",
    "Suva":             "tropical",
    "Port Moresby":     "tropical_monsoon",

    # Latin America & Caribbean (cities not already in the US list)
    "Mexico City":      "subtropical_humid",
    "Guadalajara":      "subtropical_humid",
    "Monterrey":        "subtropical_humid",
    "Tijuana":          "mediterranean",
    "Cancun":           "tropical",
    "Havana":           "tropical",
    "Santo Domingo":    "tropical",
    "San Juan":         "tropical",
    "Kingston":         "tropical",
    "San José":         "tropical",
    "Panama City":      "tropical",
    "Caracas":          "tropical",
    "Bogotá":           "tropical",
    "Medellín":         "tropical",
    "Cali":             "tropical",
    "Quito":            "tropical",
    "Guayaquil":        "tropical",
    "Lima":             "tropical",
    "La Paz":           "mountain",
    "Santa Cruz":       "tropical",
    "Asunción":         "subtropical_humid",
    "Buenos Aires":     "subtropical_humid",
    "Córdoba":          "continental_warm",
    "Rosario":          "subtropical_humid",
    "Mendoza":          "arid_steppe",
    "Santiago":         "mediterranean",
    "Valparaíso":       "mediterranean",
    "Montevideo":       "subtropical_humid",
    "Rio de Janeiro":   "tropical",
    "São Paulo":        "subtropical_humid",
    "Brasília":         "tropical",
    "Salvador":         "tropical",
    "Recife":           "tropical",
    "Fortaleza":        "tropical",
    "Manaus":           "tropical_monsoon",
    "Porto Alegre":     "subtropical_humid",
    "Curitiba":         "subtropical_humid",
    "Belo Horizonte":   "subtropical_humid",

    # Canada (extends the existing handful)
    "Vancouver":        "coastal_cool",
    "Victoria":         "coastal_cool",
    "Calgary":          "continental_cold",
    "Edmonton":         "continental_cold",
    "Winnipeg":         "continental_cold",
    "Halifax":          "coastal_cool",
    "Quebec City":      "continental_cold",
    "Ottawa":           "continental_cold",
}


# ---------------------------------------------------------------------------
# Country-code fallbacks
# ---------------------------------------------------------------------------
#
# Cities often arrive as "Helsinki FIN" / "Tokyo JPN" — the viperball
# cities.json shape. When a city isn't in `_CITY_ARCHETYPES`, strip the
# trailing 2/3-letter country code and look up a per-country default.
# Keeps custom international teams sensible without forcing per-city
# authoring for every city in the world.
#
# Picked to roughly match Köppen climate zones, with a baseball-shaped
# bias: warmer-leaning for shoulder-season nuance.

_COUNTRY_ARCHETYPES: dict[str, str] = {
    # Nordic / subarctic
    "FIN": "subarctic", "SWE": "subarctic", "NOR": "subarctic",
    "ISL": "subarctic", "DNK": "continental_cold",

    # Western Europe (mostly mild oceanic / continental_warm)
    "GBR": "coastal_cool", "IRL": "coastal_cool",
    "FRA": "continental_warm", "DEU": "continental_warm",
    "NLD": "coastal_cool",     "BEL": "continental_warm",
    "CHE": "continental_warm", "AUT": "continental_warm",
    "LUX": "continental_warm",

    # Mediterranean rim
    "ESP": "mediterranean", "ITA": "mediterranean",
    "PRT": "mediterranean", "GRC": "mediterranean",
    "TUR": "mediterranean", "ISR": "mediterranean",
    "MAR": "mediterranean", "TUN": "mediterranean",
    "CYP": "mediterranean",

    # Eastern Europe / Russia
    "POL": "continental_cold", "CZE": "continental_warm",
    "SVK": "continental_warm", "HUN": "continental_warm",
    "ROU": "continental_warm", "BGR": "continental_warm",
    "SRB": "continental_warm", "HRV": "mediterranean",
    "RUS": "continental_cold", "UKR": "continental_warm",
    "BLR": "continental_cold", "EST": "subarctic",
    "LVA": "subarctic",        "LTU": "continental_cold",

    # East Asia
    "JPN": "subtropical_humid", "KOR": "subtropical_humid",
    "PRK": "continental_cold",  "CHN": "subtropical_humid",
    "TWN": "subtropical_humid", "HKG": "subtropical_humid",
    "MNG": "continental_cold",

    # Southeast Asia / monsoon belt
    "THA": "tropical_monsoon", "VNM": "tropical_monsoon",
    "PHL": "tropical_monsoon", "MYS": "tropical_monsoon",
    "IDN": "tropical_monsoon", "SGP": "tropical_monsoon",
    "KHM": "tropical_monsoon", "MMR": "tropical_monsoon",
    "LAO": "tropical_monsoon", "BRN": "tropical_monsoon",
    "TLS": "tropical_monsoon",

    # South Asia
    "IND": "tropical_monsoon", "PAK": "arid_steppe",
    "BGD": "tropical_monsoon", "LKA": "tropical",
    "NPL": "continental_warm", "BTN": "continental_warm",
    "AFG": "arid_steppe",      "MDV": "tropical",

    # Central / West Asia
    "KAZ": "arid_steppe",      "UZB": "arid_steppe",
    "TKM": "arid_steppe",      "KGZ": "continental_cold",
    "TJK": "continental_cold", "IRN": "arid_steppe",
    "IRQ": "desert",           "SYR": "arid_steppe",
    "JOR": "arid_steppe",      "LBN": "mediterranean",
    "ARE": "desert",           "SAU": "desert",
    "OMN": "desert",           "QAT": "desert",
    "KWT": "desert",           "BHR": "desert",
    "YEM": "desert",           "AZE": "arid_steppe",
    "ARM": "continental_warm", "GEO": "continental_warm",

    # Sub-Saharan Africa
    "ZAF": "subtropical_humid", "EGY": "desert",
    "DZA": "mediterranean",    "LBY": "desert",
    "SDN": "desert",           "ETH": "continental_warm",
    "KEN": "tropical",         "TZA": "tropical",
    "UGA": "tropical",         "RWA": "tropical",
    "NGA": "tropical",         "GHA": "tropical",
    "CIV": "tropical",         "SEN": "tropical",
    "CMR": "tropical",         "AGO": "tropical",
    "MOZ": "tropical",         "ZMB": "tropical",
    "ZWE": "tropical",         "BWA": "arid_steppe",
    "NAM": "desert",           "MDG": "tropical",
    "MUS": "tropical",         "ERI": "arid_steppe",
    "SOM": "arid_steppe",

    # Oceania
    "AUS": "subtropical_humid",  # eastern coast bias; arid interior covered by city overrides
    "NZL": "coastal_cool",
    "FJI": "tropical",         "PNG": "tropical",
    "WSM": "tropical",         "TON": "tropical",
    "NCL": "tropical",         "PYF": "tropical",
    "VUT": "tropical",         "SLB": "tropical",

    # Latin America & Caribbean
    "MEX": "subtropical_humid",
    "BRA": "tropical",         "ARG": "continental_warm",
    "CHL": "mediterranean",    "URY": "subtropical_humid",
    "PRY": "subtropical_humid", "BOL": "mountain",
    "PER": "tropical",         "ECU": "tropical",
    "COL": "tropical",         "VEN": "tropical",
    "GUY": "tropical",         "SUR": "tropical",
    "DOM": "tropical",         "CUB": "tropical",
    "PRI": "tropical",         "JAM": "tropical",
    "HTI": "tropical",         "BHS": "tropical",
    "TTO": "tropical",         "BRB": "tropical",
    "CRI": "tropical",         "PAN": "tropical",
    "NIC": "tropical",         "HND": "tropical",
    "GTM": "tropical",         "SLV": "tropical",
    "BLZ": "tropical",

    # North America (city overrides take precedence; these are wide nets)
    "USA": "continental_warm",
    "CAN": "continental_cold",
}


def _strip_country_code(city: str) -> tuple[str, str | None]:
    """Split 'Helsinki FIN' -> ('Helsinki', 'FIN'). Accepts 2- or
    3-letter codes after the last whitespace. Returns (city, None) if
    no trailing code is present."""
    s = city.strip()
    if not s:
        return s, None
    parts = s.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isupper() and 2 <= len(parts[1]) <= 3 and parts[1].isalpha():
        return parts[0].strip(), parts[1]
    return s, None


def archetype_for_city(city: str) -> str:
    """Return the climatological archetype for `city`. Lookup chain:
    exact city -> city minus trailing country code -> country fallback ->
    continental_warm.
    """
    if not city:
        return "continental_warm"
    s = city.strip()
    if s in _CITY_ARCHETYPES:
        return _CITY_ARCHETYPES[s]
    bare, country = _strip_country_code(s)
    if bare in _CITY_ARCHETYPES:
        return _CITY_ARCHETYPES[bare]
    if country and country in _COUNTRY_ARCHETYPES:
        return _COUNTRY_ARCHETYPES[country]
    return "continental_warm"


# ---------------------------------------------------------------------------
# City coordinates + nearest-city lookup
# ---------------------------------------------------------------------------
#
# A coordinate gazetteer so weather can be drawn from a team's lat/lon by
# finding the nearest anchor city, instead of needing an exact name match.
# "Pick the closest known city" — approximate is fine, so the distance is
# a cheap equirectangular metric, not a true great-circle haversine.
#
# `_BASE_COORDS` carries coordinates for cities already in
# `_CITY_ARCHETYPES`. `_EXTRA_CITIES` is the expansion pack — new cities
# that get BOTH an archetype and coordinates in one place (folded into the
# tables below). Heavy on Finnish towns and US metro / micropolitan
# markets so a custom league can sprawl far past the preset catalogue.

_BASE_COORDS: dict[str, tuple[float, float]] = {
    # US / North America anchors
    "Albuquerque": (35.08, -106.65), "Arizona": (33.45, -112.07),
    "Las Vegas": (36.17, -115.14), "El Paso": (31.76, -106.49),
    "Midland": (31.99, -102.08),
    "San Francisco": (37.77, -122.42), "Oakland": (37.80, -122.27),
    "Seattle": (47.61, -122.33), "Portland": (45.52, -122.68),
    "Tacoma": (47.25, -122.44), "Salem": (44.94, -123.04),
    "Los Angeles": (34.05, -118.24), "San Diego": (32.72, -117.16),
    "Sacramento": (38.58, -121.49), "Tampa": (27.95, -82.46),
    "Tampa Bay": (27.77, -82.64), "Clearwater": (27.97, -82.80),
    "Daytona": (29.21, -81.02), "Jacksonville": (30.33, -81.66),
    "Savannah": (32.08, -81.09), "Charlotte": (35.23, -80.84),
    "Greenville": (34.85, -82.39), "Greensboro": (36.07, -79.79),
    "Wilmington": (34.23, -77.94), "Lynchburg": (37.41, -79.14),
    "Myrtle Beach": (33.69, -78.89), "Asheville": (35.60, -82.55),
    "Durham": (35.99, -78.90), "Richmond": (37.54, -77.44),
    "Norfolk": (36.85, -76.29), "Zebulon": (35.83, -78.31),
    "Chicago": (41.85, -87.65), "Cleveland": (41.50, -81.69),
    "Detroit": (42.33, -83.05), "Milwaukee": (43.04, -87.91),
    "Minnesota": (44.98, -93.27), "Pittsburgh": (40.44, -80.00),
    "Buffalo": (42.89, -78.88), "Toronto": (43.65, -79.38),
    "Montreal": (45.50, -73.57), "Boston": (42.36, -71.06),
    "New York": (40.71, -74.01), "Pawtucket": (41.88, -71.38),
    "Hartford": (41.76, -72.69), "Trenton": (40.22, -74.76),
    "Lehigh Valley": (40.65, -75.43), "Binghamton": (42.10, -75.91),
    "Lansing": (42.73, -84.56), "Cedar Rapids": (41.98, -91.67),
    "Columbus": (39.96, -83.00), "Toledo": (41.66, -83.56),
    "Indianapolis": (39.77, -86.16), "Omaha": (41.26, -95.94),
    "Harrisburg": (40.27, -76.88),
    "Atlanta": (33.75, -84.39), "Baltimore": (39.29, -76.61),
    "Cincinnati": (39.10, -84.51), "Houston": (29.76, -95.37),
    "Kansas City": (39.10, -94.58), "Nashville": (36.16, -86.78),
    "Philadelphia": (39.95, -75.17), "St. Louis": (38.63, -90.20),
    "Texas": (32.75, -97.08), "Washington": (38.91, -77.04),
    "Arkansas": (34.75, -92.29), "Chattanooga": (35.05, -85.31),
    "Corpus Christi": (27.80, -97.40), "Frisco": (33.15, -96.82),
    "Jackson": (32.30, -90.18), "Lakewood": (40.10, -74.22),
    "Montgomery": (32.37, -86.30), "Peoria": (40.69, -89.59),
    "Round Rock": (30.51, -97.68), "San Antonio": (29.42, -98.49),
    "Miami": (25.76, -80.19), "Biloxi": (30.40, -88.89),
    "Colorado": (39.74, -104.99), "Colorado Springs": (38.83, -104.82),
    "Salt Lake City": (40.76, -111.89),
    "Vancouver": (49.28, -123.12), "Victoria": (48.43, -123.37),
    "Calgary": (51.05, -114.07), "Edmonton": (53.55, -113.49),
    "Winnipeg": (49.90, -97.14), "Halifax": (44.65, -63.58),
    "Quebec City": (46.81, -71.21), "Ottawa": (45.42, -75.70),
    "New Jersey": (40.74, -74.17),
    # Nordic / Baltic
    "Helsinki": (60.17, 24.94), "Tampere": (61.50, 23.79),
    "Turku": (60.45, 22.27), "Espoo": (60.21, 24.66),
    "Vantaa": (60.29, 25.04), "Oulu": (65.01, 25.47),
    "Lahti": (60.98, 25.66), "Kuopio": (62.89, 27.68),
    "Jyväskylä": (62.24, 25.75), "Vaasa": (63.10, 21.62),
    "Joensuu": (62.60, 29.76), "Pori": (61.49, 21.80),
    "Lappeenranta": (61.06, 28.19), "Hämeenlinna": (60.99, 24.46),
    "Rovaniemi": (66.50, 25.73), "Mikkeli": (61.69, 27.27),
    "Kotka": (60.47, 26.95), "Salo": (60.38, 23.13),
    "Porvoo": (60.39, 25.66), "Kouvola": (60.87, 26.70),
    "Stockholm": (59.33, 18.07), "Gothenburg": (57.71, 11.97),
    "Malmö": (55.60, 13.00), "Uppsala": (59.86, 17.64),
    "Oslo": (59.91, 10.75), "Bergen": (60.39, 5.32),
    "Trondheim": (63.43, 10.39), "Reykjavik": (64.15, -21.94),
    "Copenhagen": (55.68, 12.57), "Tallinn": (59.44, 24.75),
    "Riga": (56.95, 24.11), "Vilnius": (54.69, 25.28),
    "Saint Petersburg": (59.93, 30.34),
    # UK / Ireland / W Europe
    "London": (51.51, -0.13), "Manchester": (53.48, -2.24),
    "Liverpool": (53.41, -2.99), "Leeds": (53.80, -1.55),
    "Edinburgh": (55.95, -3.19), "Glasgow": (55.86, -4.25),
    "Belfast": (54.60, -5.93), "Dublin": (53.35, -6.26),
    "Cardiff": (51.48, -3.18), "Paris": (48.86, 2.35),
    "Lyon": (45.76, 4.84), "Marseille": (43.30, 5.37),
    "Nice": (43.70, 7.27), "Toulouse": (43.60, 1.44),
    "Bordeaux": (44.84, -0.58), "Berlin": (52.52, 13.40),
    "Munich": (48.14, 11.58), "Hamburg": (53.55, 9.99),
    "Frankfurt": (50.11, 8.68), "Cologne": (50.94, 6.96),
    "Vienna": (48.21, 16.37), "Zürich": (47.37, 8.54),
    "Amsterdam": (52.37, 4.90), "Rotterdam": (51.92, 4.48),
    "Brussels": (50.85, 4.35), "Madrid": (40.42, -3.70),
    "Barcelona": (41.39, 2.17), "Valencia": (39.47, -0.38),
    "Seville": (37.39, -5.99), "Bilbao": (43.26, -2.93),
    "Lisbon": (38.72, -9.14), "Porto": (41.16, -8.62),
    "Rome": (41.90, 12.50), "Milan": (45.46, 9.19),
    "Naples": (40.85, 14.27), "Athens": (37.98, 23.73),
    "Warsaw": (52.23, 21.01), "Prague": (50.08, 14.44),
    "Budapest": (47.50, 19.04), "Moscow": (55.76, 37.62),
    "Kyiv": (50.45, 30.52), "Istanbul": (41.01, 28.98),
    # Asia / Oceania / Africa / LatAm anchors
    "Tokyo": (35.68, 139.69), "Osaka": (34.69, 135.50),
    "Sapporo": (43.06, 141.35), "Seoul": (37.57, 126.98),
    "Beijing": (39.90, 116.41), "Shanghai": (31.23, 121.47),
    "Hong Kong": (22.32, 114.17), "Taipei": (25.03, 121.57),
    "Bangkok": (13.76, 100.50), "Manila": (14.60, 120.98),
    "Jakarta": (-6.21, 106.85), "Singapore": (1.35, 103.82),
    "Kuala Lumpur": (3.14, 101.69), "Mumbai": (19.08, 72.88),
    "Delhi": (28.61, 77.21), "Chennai": (13.08, 80.27),
    "Dubai": (25.20, 55.27), "Riyadh": (24.71, 46.68),
    "Tel Aviv": (32.08, 34.78), "Cairo": (30.04, 31.24),
    "Lagos": (6.52, 3.38), "Nairobi": (-1.29, 36.82),
    "Cape Town": (-33.92, 18.42), "Johannesburg": (-26.20, 28.05),
    "Sydney": (-33.87, 151.21), "Melbourne": (-37.81, 144.96),
    "Brisbane": (-27.47, 153.03), "Perth": (-31.95, 115.86),
    "Alice Springs": (-23.70, 133.88), "Auckland": (-36.85, 174.76),
    "Wellington": (-41.29, 174.78), "Mexico City": (19.43, -99.13),
    "Monterrey": (25.69, -100.32), "Havana": (23.11, -82.37),
    "Santo Domingo": (18.49, -69.93), "San Juan": (18.47, -66.11),
    "Buenos Aires": (-34.60, -58.38), "Santiago": (-33.45, -70.67),
    "Rio de Janeiro": (-22.91, -43.17), "São Paulo": (-23.55, -46.63),
    "Lima": (-12.05, -77.04), "Bogotá": (4.71, -74.07),
}

# Expansion pack — (lat, lon, archetype). Folded into both tables below.
_EXTRA_CITIES: dict[str, tuple[float, float, str]] = {
    # ----- Finland: far more towns, mostly subarctic -----
    "Seinäjoki": (62.79, 22.84, "subarctic"),
    "Kokkola": (63.84, 23.13, "subarctic"),
    "Kajaani": (64.23, 27.73, "subarctic"),
    "Kemi": (65.74, 24.56, "subarctic"),
    "Tornio": (65.85, 24.15, "subarctic"),
    "Iisalmi": (63.56, 27.19, "subarctic"),
    "Savonlinna": (61.87, 28.88, "subarctic"),
    "Raahe": (64.68, 24.48, "subarctic"),
    "Imatra": (61.19, 28.77, "subarctic"),
    "Hyvinkää": (60.63, 24.86, "subarctic"),
    "Järvenpää": (60.47, 25.10, "subarctic"),
    "Lohja": (60.25, 24.07, "subarctic"),
    "Rauma": (61.13, 21.51, "subarctic"),
    "Kuusamo": (65.96, 29.19, "subarctic"),
    "Sodankylä": (67.42, 26.59, "subarctic"),
    "Inari": (68.66, 27.55, "subarctic"),
    "Hanko": (59.83, 22.97, "subarctic"),
    "Mariehamn": (60.10, 19.94, "subarctic"),
    "Nokia": (61.48, 23.51, "subarctic"),
    "Ylöjärvi": (61.55, 23.60, "subarctic"),
    "Kerava": (60.40, 25.10, "subarctic"),
    "Riihimäki": (60.74, 24.78, "subarctic"),
    "Valkeakoski": (61.27, 24.03, "subarctic"),
    "Heinola": (61.20, 26.04, "subarctic"),
    "Varkaus": (62.31, 27.87, "subarctic"),
    "Pieksämäki": (62.30, 27.13, "subarctic"),
    "Ylivieska": (64.08, 24.55, "subarctic"),
    "Kuhmo": (64.13, 29.52, "subarctic"),
    "Sotkamo": (64.13, 28.40, "subarctic"),
    "Pietarsaari": (63.68, 22.70, "subarctic"),
    "Uusikaupunki": (60.80, 21.41, "subarctic"),
    "Naantali": (60.47, 22.03, "subarctic"),
    "Kaarina": (60.41, 22.37, "subarctic"),
    "Forssa": (60.81, 23.62, "subarctic"),
    "Kangasala": (61.46, 24.07, "subarctic"),
    "Tuusula": (60.40, 25.03, "subarctic"),
    "Nurmijärvi": (60.47, 24.81, "subarctic"),
    "Kirkkonummi": (60.12, 24.44, "subarctic"),
    "Kemijärvi": (66.71, 27.43, "subarctic"),
    "Tornio Haparanda": (65.84, 24.14, "subarctic"),
    # ----- US Northeast / New England (continental_cold) -----
    "Albany": (42.65, -73.76, "continental_cold"),
    "Syracuse": (43.05, -76.15, "continental_cold"),
    "Rochester": (43.16, -77.61, "continental_cold"),
    "Worcester": (42.26, -71.80, "continental_cold"),
    "Providence": (41.82, -71.41, "continental_cold"),
    "Manchester NH": (42.99, -71.46, "continental_cold"),
    "Portland ME": (43.66, -70.26, "continental_cold"),
    "Burlington VT": (44.48, -73.21, "continental_cold"),
    "Scranton": (41.41, -75.66, "continental_cold"),
    "Allentown": (40.60, -75.48, "continental_cold"),
    "Erie": (42.13, -80.09, "continental_cold"),
    "Springfield MA": (42.10, -72.59, "continental_cold"),
    "New Haven": (41.31, -72.93, "continental_cold"),
    "Bridgeport": (41.19, -73.20, "continental_cold"),
    "Bangor": (44.80, -68.77, "continental_cold"),
    "Utica": (43.10, -75.23, "continental_cold"),
    # ----- US Midwest / Great Lakes (continental_cold) -----
    "Grand Rapids": (42.96, -85.67, "continental_cold"),
    "Fort Wayne": (41.08, -85.14, "continental_cold"),
    "Dayton": (39.76, -84.19, "continental_cold"),
    "Akron": (41.08, -81.52, "continental_cold"),
    "Youngstown": (41.10, -80.65, "continental_cold"),
    "Madison": (43.07, -89.40, "continental_cold"),
    "Green Bay": (44.51, -88.02, "continental_cold"),
    "Des Moines": (41.59, -93.62, "continental_cold"),
    "Sioux Falls": (43.55, -96.70, "continental_cold"),
    "Fargo": (46.88, -96.79, "continental_cold"),
    "Duluth": (46.79, -92.10, "continental_cold"),
    "Rockford": (42.27, -89.09, "continental_cold"),
    "South Bend": (41.68, -86.25, "continental_cold"),
    "Kalamazoo": (42.29, -85.59, "continental_cold"),
    "Flint": (43.01, -83.69, "continental_cold"),
    "Saginaw": (43.42, -83.95, "continental_cold"),
    "Quad Cities": (41.52, -90.58, "continental_cold"),
    "Springfield IL": (39.78, -89.65, "continental_cold"),
    "Wichita": (37.69, -97.34, "continental_warm"),
    "Lincoln": (40.81, -96.70, "continental_cold"),
    "Bismarck": (46.81, -100.78, "continental_cold"),
    "Rapid City": (44.08, -103.23, "arid_steppe"),
    # ----- US South / Southeast (continental_warm) -----
    "Memphis": (35.15, -90.05, "continental_warm"),
    "Knoxville": (35.96, -83.92, "continental_warm"),
    "Huntsville": (34.73, -86.59, "continental_warm"),
    "Mobile": (30.69, -88.04, "continental_warm"),
    "Shreveport": (32.53, -93.75, "continental_warm"),
    "Baton Rouge": (30.45, -91.19, "continental_warm"),
    "Little Rock": (34.75, -92.29, "continental_warm"),
    "Tulsa": (36.15, -95.99, "continental_warm"),
    "Oklahoma City": (35.47, -97.52, "continental_warm"),
    "Columbia SC": (34.00, -81.03, "continental_warm"),
    "Augusta": (33.47, -81.97, "continental_warm"),
    "Macon": (32.84, -83.63, "continental_warm"),
    "Tallahassee": (30.44, -84.28, "continental_warm"),
    "Pensacola": (30.42, -87.22, "coastal_warm"),
    "Lexington": (38.04, -84.50, "continental_warm"),
    "Louisville": (38.25, -85.76, "continental_warm"),
    "Roanoke": (37.27, -79.94, "continental_warm"),
    "Fayetteville NC": (35.05, -78.88, "coastal_warm"),
    "Columbus GA": (32.46, -84.99, "continental_warm"),
    "Tuscaloosa": (33.21, -87.57, "continental_warm"),
    "New Orleans": (29.95, -90.07, "continental_warm"),
    # ----- Texas (continental_warm / arid) -----
    "Austin": (30.27, -97.74, "continental_warm"),
    "Waco": (31.55, -97.15, "continental_warm"),
    "Lubbock": (33.58, -101.86, "arid_steppe"),
    "Amarillo": (35.22, -101.83, "arid_steppe"),
    "Abilene": (32.45, -99.73, "arid_steppe"),
    "Laredo": (27.51, -99.51, "desert"),
    "Brownsville": (25.90, -97.50, "coastal_warm"),
    "McAllen": (26.20, -98.23, "coastal_warm"),
    "Tyler": (32.35, -95.30, "continental_warm"),
    "Beaumont": (30.08, -94.10, "continental_warm"),
    # ----- Florida (coastal_warm / tropical) -----
    "Orlando": (28.54, -81.38, "coastal_warm"),
    "Fort Myers": (26.64, -81.87, "tropical"),
    "Sarasota": (27.34, -82.53, "coastal_warm"),
    "Gainesville": (29.65, -82.32, "coastal_warm"),
    "Lakeland": (28.04, -81.95, "coastal_warm"),
    "Fort Lauderdale": (26.12, -80.14, "tropical"),
    "West Palm Beach": (26.71, -80.05, "tropical"),
    "Key West": (24.56, -81.78, "tropical"),
    "Naples FL": (26.14, -81.79, "tropical"),
    "Ocala": (29.19, -82.13, "coastal_warm"),
    "Pensacola FL": (30.42, -87.22, "coastal_warm"),
    # ----- US Southwest desert -----
    "Phoenix": (33.45, -112.07, "desert"),
    "Tucson": (32.22, -110.97, "desert"),
    "Yuma": (32.69, -114.63, "desert"),
    "Mesa": (33.42, -111.83, "desert"),
    "Palm Springs": (33.83, -116.55, "desert"),
    "St. George": (37.10, -113.58, "desert"),
    # ----- Mountain / Intermountain West -----
    "Boise": (43.62, -116.21, "arid_steppe"),
    "Spokane": (47.66, -117.43, "arid_steppe"),
    "Reno": (39.53, -119.81, "arid_steppe"),
    "Flagstaff": (35.20, -111.65, "mountain"),
    "Missoula": (46.87, -113.99, "mountain"),
    "Bozeman": (45.68, -111.04, "mountain"),
    "Billings": (45.78, -108.50, "arid_steppe"),
    "Cheyenne": (41.14, -104.82, "arid_steppe"),
    "Casper": (42.85, -106.32, "arid_steppe"),
    "Pocatello": (42.87, -112.45, "arid_steppe"),
    "Provo": (40.23, -111.66, "mountain"),
    "Ogden": (41.22, -111.97, "mountain"),
    "Boulder": (40.01, -105.27, "mountain"),
    # ----- Pacific Northwest (coastal_cool) -----
    "Eugene": (44.05, -123.09, "coastal_cool"),
    "Bellingham": (48.75, -122.48, "coastal_cool"),
    "Olympia": (47.04, -122.90, "coastal_cool"),
    "Vancouver WA": (45.63, -122.66, "coastal_cool"),
    # ----- California -----
    "Fresno": (36.74, -119.77, "mediterranean"),
    "Bakersfield": (35.37, -119.02, "arid_steppe"),
    "Stockton": (37.96, -121.29, "mediterranean"),
    "Modesto": (37.64, -120.997, "mediterranean"),
    "San Jose": (37.34, -121.89, "coastal_warm"),
    "Santa Barbara": (34.42, -119.70, "mediterranean"),
    "Long Beach": (33.77, -118.19, "coastal_warm"),
    "Anaheim": (33.84, -117.91, "coastal_warm"),
    "Riverside": (33.95, -117.40, "mediterranean"),
    "San Bernardino": (34.11, -117.29, "mediterranean"),
    "Chico": (39.73, -121.84, "mediterranean"),
    "Redding": (40.59, -122.39, "mediterranean"),
    "Santa Rosa": (38.44, -122.71, "mediterranean"),
    "Monterey": (36.60, -121.89, "coastal_cool"),
    "Ventura": (34.27, -119.29, "coastal_warm"),
}

# Fold the expansion pack into the name->archetype table (don't clobber an
# existing hand-authored entry) and build the combined name->coords map.
for _xname, (_xlat, _xlon, _xarch) in _EXTRA_CITIES.items():
    _CITY_ARCHETYPES.setdefault(_xname, _xarch)

_CITY_COORDS: dict[str, tuple[float, float]] = dict(_BASE_COORDS)
for _xname, (_xlat, _xlon, _xarch) in _EXTRA_CITIES.items():
    _CITY_COORDS[_xname] = (_xlat, _xlon)

# Coordinate anchors for nearest-city lookup: every city we know both a
# location AND an archetype for. (name, lat, lon, archetype).
_CLIMATE_ANCHORS: list[tuple[str, float, float, str]] = [
    (name, lat, lon, _CITY_ARCHETYPES[name])
    for name, (lat, lon) in _CITY_COORDS.items()
    if name in _CITY_ARCHETYPES
]


def _coord_dist2(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Squared equirectangular distance — monotone in true distance, so
    fine for nearest-neighbour ranking and far cheaper than haversine."""
    import math
    mid = math.radians((lat1 + lat2) * 0.5)
    x = math.radians(lon2 - lon1) * math.cos(mid)
    y = math.radians(lat2 - lat1)
    return x * x + y * y


def nearest_city(lat: float, lon: float) -> tuple[str, str] | None:
    """Return (city_name, archetype) of the nearest anchor to (lat, lon),
    or None if no coordinate is usable."""
    if lat is None or lon is None:
        return None
    try:
        lat = float(lat); lon = float(lon)
    except (TypeError, ValueError):
        return None
    best: tuple[str, str] | None = None
    best_d = float("inf")
    for name, alat, alon, arch in _CLIMATE_ANCHORS:
        d = _coord_dist2(lat, lon, alat, alon)
        if d < best_d:
            best_d, best = d, (name, arch)
    return best


def archetype_for_coords(lat: float, lon: float) -> str:
    """Climatological archetype for a lat/lon — the archetype of the
    nearest anchor city. Falls back to continental_warm if coords are
    unusable."""
    hit = nearest_city(lat, lon)
    return hit[1] if hit else "continental_warm"


def city_gazetteer() -> list[dict]:
    """Sorted list of known cities with coords + archetype, for UI
    pickers (the team-location datalist)."""
    out = [
        {"name": name, "lat": lat, "lon": lon, "archetype": _CITY_ARCHETYPES[name]}
        for name, (lat, lon) in _CITY_COORDS.items()
        if name in _CITY_ARCHETYPES
    ]
    out.sort(key=lambda c: c["name"])
    return out


# ---------------------------------------------------------------------------
# Archetype-month tier distributions
# ---------------------------------------------------------------------------
#
# Months are bucketed into 6 windows aligned with the baseball calendar:
#   apr  early-spring         (Apr)
#   may  late-spring          (May)
#   jun  early-summer         (Jun)
#   jul  midsummer            (Jul)
#   aug  late-summer          (Aug)
#   sep  early-fall           (Sep + Oct fallback)
#
# Each entry is a dict mapping tier -> weight. Weights need not sum to 1.0;
# `_choose` normalises. Missing tiers are treated as 0.

_MonthKey = str  # 'apr', 'may', 'jun', 'jul', 'aug', 'sep'

_MONTH_BUCKETS: dict[int, _MonthKey] = {
    3: "apr", 4: "apr",        # spring training stragglers fold into Apr
    5: "may",
    6: "jun",
    7: "jul",
    8: "aug",
    9: "sep", 10: "sep", 11: "sep",
}


def _month_bucket(date_iso: str) -> _MonthKey:
    try:
        d = datetime.date.fromisoformat(date_iso)
    except (ValueError, TypeError):
        return "jun"
    return _MONTH_BUCKETS.get(d.month, "jun")


# Tier weights per (archetype, month). Compact tables — every variable's
# distribution is independent within a (archetype, month) cell.

_DESERT = {
    "apr": {"temperature": {"mild": 4, "warm": 4, "hot": 2},
            "humidity":    {"dry": 7, "normal": 2, "humid": 0},
            "precip":      {"none": 19, "light": 1, "heavy": 0}},
    "may": {"temperature": {"warm": 4, "hot": 5, "mild": 1},
            "humidity":    {"dry": 8, "normal": 2, "humid": 0},
            "precip":      {"none": 19, "light": 1, "heavy": 0}},
    "jun": {"temperature": {"hot": 8, "warm": 2},
            "humidity":    {"dry": 8, "normal": 2, "humid": 0},
            "precip":      {"none": 20, "light": 0, "heavy": 0}},
    "jul": {"temperature": {"hot": 9, "warm": 1},
            "humidity":    {"dry": 7, "normal": 3, "humid": 0},  # monsoon nudges
            "precip":      {"none": 17, "light": 2, "heavy": 1}},
    "aug": {"temperature": {"hot": 8, "warm": 2},
            "humidity":    {"dry": 7, "normal": 3, "humid": 0},
            "precip":      {"none": 17, "light": 2, "heavy": 1}},
    "sep": {"temperature": {"warm": 5, "hot": 3, "mild": 2},
            "humidity":    {"dry": 7, "normal": 3, "humid": 0},
            "precip":      {"none": 19, "light": 1, "heavy": 0}},
}

_COASTAL_COOL = {
    "apr": {"temperature": {"cold": 3, "mild": 6, "warm": 1},
            "humidity":    {"dry": 1, "normal": 6, "humid": 3},
            "precip":      {"none": 14, "light": 5, "heavy": 1}},
    "may": {"temperature": {"mild": 7, "warm": 2, "cold": 1},
            "humidity":    {"dry": 1, "normal": 6, "humid": 3},
            "precip":      {"none": 16, "light": 3, "heavy": 1}},
    "jun": {"temperature": {"mild": 6, "warm": 4},
            "humidity":    {"dry": 1, "normal": 6, "humid": 3},
            "precip":      {"none": 18, "light": 2, "heavy": 0}},
    "jul": {"temperature": {"mild": 5, "warm": 5},
            "humidity":    {"dry": 1, "normal": 6, "humid": 3},
            "precip":      {"none": 19, "light": 1, "heavy": 0}},
    "aug": {"temperature": {"mild": 5, "warm": 5},
            "humidity":    {"dry": 1, "normal": 6, "humid": 3},
            "precip":      {"none": 19, "light": 1, "heavy": 0}},
    "sep": {"temperature": {"mild": 7, "warm": 3},
            "humidity":    {"dry": 1, "normal": 6, "humid": 3},
            "precip":      {"none": 17, "light": 2, "heavy": 1}},
}

_COASTAL_WARM = {
    "apr": {"temperature": {"mild": 5, "warm": 4, "hot": 1},
            "humidity":    {"normal": 5, "humid": 5, "dry": 0},
            "precip":      {"none": 15, "light": 4, "heavy": 1}},
    "may": {"temperature": {"warm": 6, "hot": 3, "mild": 1},
            "humidity":    {"normal": 4, "humid": 6},
            "precip":      {"none": 15, "light": 4, "heavy": 1}},
    "jun": {"temperature": {"warm": 5, "hot": 5},
            "humidity":    {"normal": 3, "humid": 7},
            "precip":      {"none": 12, "light": 6, "heavy": 2}},
    "jul": {"temperature": {"hot": 7, "warm": 3},
            "humidity":    {"normal": 2, "humid": 8},
            "precip":      {"none": 12, "light": 5, "heavy": 3}},
    "aug": {"temperature": {"hot": 7, "warm": 3},
            "humidity":    {"normal": 2, "humid": 8},
            "precip":      {"none": 12, "light": 5, "heavy": 3}},
    "sep": {"temperature": {"warm": 6, "hot": 3, "mild": 1},
            "humidity":    {"normal": 4, "humid": 6},
            "precip":      {"none": 14, "light": 5, "heavy": 1}},
}

_CONTINENTAL_COLD = {
    "apr": {"temperature": {"cold": 5, "mild": 4, "warm": 1},
            "humidity":    {"dry": 2, "normal": 6, "humid": 2},
            "precip":      {"none": 14, "light": 5, "heavy": 1}},
    "may": {"temperature": {"mild": 5, "warm": 4, "cold": 1},
            "humidity":    {"dry": 2, "normal": 6, "humid": 2},
            "precip":      {"none": 15, "light": 4, "heavy": 1}},
    "jun": {"temperature": {"warm": 6, "mild": 3, "hot": 1},
            "humidity":    {"dry": 1, "normal": 6, "humid": 3},
            "precip":      {"none": 15, "light": 4, "heavy": 1}},
    "jul": {"temperature": {"warm": 5, "hot": 4, "mild": 1},
            "humidity":    {"dry": 1, "normal": 5, "humid": 4},
            "precip":      {"none": 15, "light": 4, "heavy": 1}},
    "aug": {"temperature": {"warm": 5, "hot": 4, "mild": 1},
            "humidity":    {"dry": 1, "normal": 5, "humid": 4},
            "precip":      {"none": 16, "light": 3, "heavy": 1}},
    "sep": {"temperature": {"mild": 5, "warm": 4, "cold": 1},
            "humidity":    {"dry": 2, "normal": 6, "humid": 2},
            "precip":      {"none": 16, "light": 3, "heavy": 1}},
}

_CONTINENTAL_WARM = {
    "apr": {"temperature": {"mild": 5, "warm": 4, "cold": 1},
            "humidity":    {"dry": 2, "normal": 5, "humid": 3},
            "precip":      {"none": 15, "light": 4, "heavy": 1}},
    "may": {"temperature": {"warm": 6, "hot": 2, "mild": 2},
            "humidity":    {"dry": 1, "normal": 5, "humid": 4},
            "precip":      {"none": 14, "light": 5, "heavy": 1}},
    "jun": {"temperature": {"warm": 5, "hot": 4, "mild": 1},
            "humidity":    {"dry": 1, "normal": 4, "humid": 5},
            "precip":      {"none": 13, "light": 5, "heavy": 2}},
    "jul": {"temperature": {"hot": 7, "warm": 3},
            "humidity":    {"dry": 0, "normal": 3, "humid": 7},
            "precip":      {"none": 13, "light": 5, "heavy": 2}},
    "aug": {"temperature": {"hot": 6, "warm": 4},
            "humidity":    {"dry": 0, "normal": 3, "humid": 7},
            "precip":      {"none": 14, "light": 4, "heavy": 2}},
    "sep": {"temperature": {"warm": 6, "hot": 2, "mild": 2},
            "humidity":    {"dry": 1, "normal": 5, "humid": 4},
            "precip":      {"none": 15, "light": 4, "heavy": 1}},
}

_TROPICAL = {
    "apr": {"temperature": {"warm": 6, "hot": 4},
            "humidity":    {"normal": 3, "humid": 7},
            "precip":      {"none": 12, "light": 6, "heavy": 2}},
    "may": {"temperature": {"warm": 5, "hot": 5},
            "humidity":    {"normal": 2, "humid": 8},
            "precip":      {"none": 11, "light": 6, "heavy": 3}},
    "jun": {"temperature": {"hot": 7, "warm": 3},
            "humidity":    {"humid": 9, "normal": 1},
            "precip":      {"none": 9, "light": 7, "heavy": 4}},
    "jul": {"temperature": {"hot": 8, "warm": 2},
            "humidity":    {"humid": 9, "normal": 1},
            "precip":      {"none": 9, "light": 7, "heavy": 4}},
    "aug": {"temperature": {"hot": 8, "warm": 2},
            "humidity":    {"humid": 9, "normal": 1},
            "precip":      {"none": 9, "light": 7, "heavy": 4}},
    "sep": {"temperature": {"hot": 6, "warm": 4},
            "humidity":    {"humid": 8, "normal": 2},
            "precip":      {"none": 10, "light": 6, "heavy": 4}},
}

_MOUNTAIN = {
    "apr": {"temperature": {"cold": 4, "mild": 5, "warm": 1},
            "humidity":    {"dry": 6, "normal": 3, "humid": 1},
            "precip":      {"none": 16, "light": 3, "heavy": 1}},
    "may": {"temperature": {"mild": 5, "warm": 4, "cold": 1},
            "humidity":    {"dry": 5, "normal": 4, "humid": 1},
            "precip":      {"none": 16, "light": 3, "heavy": 1}},
    "jun": {"temperature": {"warm": 6, "hot": 2, "mild": 2},
            "humidity":    {"dry": 6, "normal": 3, "humid": 1},
            "precip":      {"none": 17, "light": 2, "heavy": 1}},
    "jul": {"temperature": {"warm": 5, "hot": 4, "mild": 1},
            "humidity":    {"dry": 5, "normal": 4, "humid": 1},
            "precip":      {"none": 15, "light": 4, "heavy": 1}},
    "aug": {"temperature": {"warm": 6, "hot": 3, "mild": 1},
            "humidity":    {"dry": 5, "normal": 4, "humid": 1},
            "precip":      {"none": 16, "light": 3, "heavy": 1}},
    "sep": {"temperature": {"mild": 5, "warm": 4, "cold": 1},
            "humidity":    {"dry": 6, "normal": 3, "humid": 1},
            "precip":      {"none": 17, "light": 2, "heavy": 1}},
}

_SUBARCTIC = {
    # Helsinki / Stockholm / Reykjavik shape: cold-dominated season with
    # a brief mild-warm window in midsummer. Skies trend overcast, light
    # precip is the norm, hot is essentially absent. Pesäpallo home turf.
    "apr": {"temperature": {"cold": 8, "mild": 2},
            "humidity":    {"normal": 6, "humid": 3, "dry": 1},
            "precip":      {"none": 13, "light": 6, "heavy": 1}},
    "may": {"temperature": {"cold": 4, "mild": 6},
            "humidity":    {"normal": 6, "humid": 3, "dry": 1},
            "precip":      {"none": 14, "light": 5, "heavy": 1}},
    "jun": {"temperature": {"mild": 7, "warm": 3},
            "humidity":    {"normal": 6, "humid": 3, "dry": 1},
            "precip":      {"none": 14, "light": 5, "heavy": 1}},
    "jul": {"temperature": {"mild": 5, "warm": 5},
            "humidity":    {"normal": 5, "humid": 4, "dry": 1},
            "precip":      {"none": 13, "light": 6, "heavy": 1}},
    "aug": {"temperature": {"mild": 6, "warm": 4},
            "humidity":    {"normal": 6, "humid": 3, "dry": 1},
            "precip":      {"none": 13, "light": 6, "heavy": 1}},
    "sep": {"temperature": {"cold": 5, "mild": 5},
            "humidity":    {"normal": 6, "humid": 3, "dry": 1},
            "precip":      {"none": 12, "light": 6, "heavy": 2}},
}

_MEDITERRANEAN = {
    # Madrid / Rome / Athens / Marseille / Lisbon: hot dry summers, mild
    # wet shoulders. Heavy precip is rare in midsummer; spring/fall get
    # the rain. Humidity tilts dry except by the coast in shoulder months.
    "apr": {"temperature": {"mild": 6, "warm": 3, "cold": 1},
            "humidity":    {"dry": 3, "normal": 5, "humid": 2},
            "precip":      {"none": 14, "light": 5, "heavy": 1}},
    "may": {"temperature": {"warm": 6, "mild": 3, "hot": 1},
            "humidity":    {"dry": 4, "normal": 5, "humid": 1},
            "precip":      {"none": 16, "light": 3, "heavy": 1}},
    "jun": {"temperature": {"warm": 4, "hot": 6},
            "humidity":    {"dry": 7, "normal": 3, "humid": 0},
            "precip":      {"none": 19, "light": 1, "heavy": 0}},
    "jul": {"temperature": {"hot": 8, "warm": 2},
            "humidity":    {"dry": 8, "normal": 2, "humid": 0},
            "precip":      {"none": 20, "light": 0, "heavy": 0}},
    "aug": {"temperature": {"hot": 8, "warm": 2},
            "humidity":    {"dry": 8, "normal": 2, "humid": 0},
            "precip":      {"none": 20, "light": 0, "heavy": 0}},
    "sep": {"temperature": {"warm": 6, "hot": 2, "mild": 2},
            "humidity":    {"dry": 5, "normal": 4, "humid": 1},
            "precip":      {"none": 16, "light": 3, "heavy": 1}},
}

_TROPICAL_MONSOON = {
    # Bangkok / Manila / Mumbai / Jakarta: hot and humid year-round with
    # a distinct wet season that dominates the heart of the calendar.
    # Heavy precip is genuinely common Jul-Aug; light rain is the norm.
    "apr": {"temperature": {"warm": 4, "hot": 6},
            "humidity":    {"normal": 3, "humid": 7, "dry": 0},
            "precip":      {"none": 13, "light": 5, "heavy": 2}},
    "may": {"temperature": {"warm": 3, "hot": 7},
            "humidity":    {"normal": 2, "humid": 8, "dry": 0},
            "precip":      {"none": 9,  "light": 7, "heavy": 4}},
    "jun": {"temperature": {"warm": 4, "hot": 6},
            "humidity":    {"normal": 1, "humid": 9, "dry": 0},
            "precip":      {"none": 6,  "light": 8, "heavy": 6}},
    "jul": {"temperature": {"warm": 5, "hot": 5},
            "humidity":    {"normal": 1, "humid": 9, "dry": 0},
            "precip":      {"none": 5,  "light": 8, "heavy": 7}},
    "aug": {"temperature": {"warm": 5, "hot": 5},
            "humidity":    {"normal": 1, "humid": 9, "dry": 0},
            "precip":      {"none": 5,  "light": 8, "heavy": 7}},
    "sep": {"temperature": {"warm": 4, "hot": 6},
            "humidity":    {"normal": 2, "humid": 8, "dry": 0},
            "precip":      {"none": 8,  "light": 8, "heavy": 4}},
}

_SUBTROPICAL_HUMID = {
    # Tokyo / Shanghai / Seoul / Houston / Buenos Aires shoulder. Hot
    # humid midsummer (with the East-Asia rainy season in jun) but
    # less extreme than tropical_monsoon — hottest tier doesn't dominate
    # the way it does at lower latitudes.
    "apr": {"temperature": {"mild": 5, "warm": 4, "cold": 1},
            "humidity":    {"normal": 5, "humid": 4, "dry": 1},
            "precip":      {"none": 14, "light": 5, "heavy": 1}},
    "may": {"temperature": {"warm": 6, "mild": 3, "hot": 1},
            "humidity":    {"normal": 4, "humid": 5, "dry": 1},
            "precip":      {"none": 12, "light": 6, "heavy": 2}},
    "jun": {"temperature": {"warm": 5, "hot": 4, "mild": 1},
            "humidity":    {"normal": 2, "humid": 8, "dry": 0},
            "precip":      {"none": 10, "light": 7, "heavy": 3}},
    "jul": {"temperature": {"hot": 7, "warm": 3},
            "humidity":    {"normal": 1, "humid": 9, "dry": 0},
            "precip":      {"none": 11, "light": 6, "heavy": 3}},
    "aug": {"temperature": {"hot": 7, "warm": 3},
            "humidity":    {"normal": 1, "humid": 9, "dry": 0},
            "precip":      {"none": 11, "light": 6, "heavy": 3}},
    "sep": {"temperature": {"warm": 6, "hot": 2, "mild": 2},
            "humidity":    {"normal": 3, "humid": 7, "dry": 0},
            "precip":      {"none": 12, "light": 6, "heavy": 2}},
}

_ARID_STEPPE = {
    # Astana / Tashkent / interior West-Asia: hot dry summers, cool
    # springs/falls, very low humidity, occasional thunderstorm. Less
    # extreme than desert (more spring rain, cooler shoulders).
    "apr": {"temperature": {"cold": 3, "mild": 6, "warm": 1},
            "humidity":    {"dry": 6, "normal": 3, "humid": 1},
            "precip":      {"none": 16, "light": 3, "heavy": 1}},
    "may": {"temperature": {"mild": 4, "warm": 5, "hot": 1},
            "humidity":    {"dry": 6, "normal": 3, "humid": 1},
            "precip":      {"none": 17, "light": 2, "heavy": 1}},
    "jun": {"temperature": {"warm": 5, "hot": 4, "mild": 1},
            "humidity":    {"dry": 7, "normal": 3, "humid": 0},
            "precip":      {"none": 19, "light": 1, "heavy": 0}},
    "jul": {"temperature": {"hot": 7, "warm": 3},
            "humidity":    {"dry": 8, "normal": 2, "humid": 0},
            "precip":      {"none": 19, "light": 1, "heavy": 0}},
    "aug": {"temperature": {"hot": 6, "warm": 4},
            "humidity":    {"dry": 8, "normal": 2, "humid": 0},
            "precip":      {"none": 19, "light": 1, "heavy": 0}},
    "sep": {"temperature": {"mild": 4, "warm": 5, "cold": 1},
            "humidity":    {"dry": 6, "normal": 3, "humid": 1},
            "precip":      {"none": 18, "light": 2, "heavy": 0}},
}


_TABLES: dict[str, dict] = {
    "desert":            _DESERT,
    "coastal_cool":      _COASTAL_COOL,
    "coastal_warm":      _COASTAL_WARM,
    "continental_cold":  _CONTINENTAL_COLD,
    "continental_warm":  _CONTINENTAL_WARM,
    "tropical":          _TROPICAL,
    "mountain":          _MOUNTAIN,
    "subarctic":         _SUBARCTIC,
    "mediterranean":     _MEDITERRANEAN,
    "tropical_monsoon":  _TROPICAL_MONSOON,
    "subtropical_humid": _SUBTROPICAL_HUMID,
    "arid_steppe":       _ARID_STEPPE,
}

# Wind and cloud cover are archetype-agnostic at v1 — they vary too much
# day-to-day for a small table to capture meaningfully, and the engine
# effects are already small. One global distribution per variable.
_WIND_DIST  = {"neutral": 6, "out": 2, "in": 1, "cross": 2}
_CLOUD_DIST = {"clear": 6, "overcast": 3, "dusk": 1}


def _choose(rng: random.Random, dist: dict[str, float]) -> str:
    total = sum(max(0.0, w) for w in dist.values())
    if total <= 0:
        return next(iter(dist))
    r = rng.random() * total
    cum = 0.0
    for tier, w in dist.items():
        cum += max(0.0, w)
        if r < cum:
            return tier
    return tier


def draw_weather(rng: random.Random, city: str, game_date: str,
                 lat: float | None = None, lon: float | None = None) -> Weather:
    """Draw a Weather sample for `city` on `game_date` (YYYY-MM-DD).

    When `lat`/`lon` are supplied, the archetype is taken from the
    nearest known anchor city ("closest city" geography) rather than an
    exact name match — so any custom location resolves to sensible
    weather. Falls back to the name lookup when coordinates are absent.

    Pure: feed the same RNG state twice and you get the same sample.
    """
    if lat is not None and lon is not None:
        archetype = archetype_for_coords(lat, lon)
    else:
        archetype = archetype_for_city(city)
    month_key = _month_bucket(game_date)
    table = _TABLES.get(archetype, _CONTINENTAL_WARM).get(month_key, {})

    return Weather(
        temperature=_choose(rng, table.get("temperature", {"mild": 1})),
        humidity=_choose(rng, table.get("humidity", {"normal": 1})),
        precip=_choose(rng, table.get("precip", {"none": 1})),
        wind=_choose(rng, _WIND_DIST),
        cloud=_choose(rng, _CLOUD_DIST),
    )


# ---------------------------------------------------------------------------
# Engine modifiers
# ---------------------------------------------------------------------------
#
# All multipliers collapse to 1.0 for NEUTRAL weather. Bounds: every
# individual factor is in [0.85, 1.20]. They combine MULTIPLICATIVELY so
# the worst-case stack stays within ~25% of neutral on any single rate.

# Magnitudes are deliberately small so an extreme-every-game stack stays
# inside ~10% of the neutral-baseline league rates (calibration target).
# Individual factors are well within the [0.85, 1.20] envelope.

# HR: hot+wind-out = launch conditions; cold+wind-in = dead ball.
_HR_TEMP = {"cold": 0.97, "mild": 1.00, "warm": 1.02, "hot": 1.04}
_HR_WIND = {"out":  1.05, "neutral": 1.00, "in": 0.95, "cross": 0.99}
_HR_HUM  = {"dry":  1.02, "normal":  1.00, "humid": 0.99}
_HR_PRE  = {"none": 1.00, "light":   0.98, "heavy": 0.96}

# Hard contact share (extra-base juice). Same direction as HR but milder.
_HC_TEMP = {"cold": 0.98, "mild": 1.00, "warm": 1.01, "hot": 1.02}
_HC_PRE  = {"none": 1.00, "light": 0.98, "heavy": 0.96}

# Strikeout rate. Dusk ball is harder to see; small hot-K reduction.
_K_TEMP  = {"cold": 1.01, "mild": 1.00, "warm": 0.995, "hot": 0.98}
_K_CLOUD = {"clear": 1.00, "overcast": 1.005, "dusk": 1.03}

# Error rate. Wet ball + low light = fumbles. Errors are rare so larger
# percentage swings here barely move league offense.
_E_PRE   = {"none": 1.00, "light": 1.08, "heavy": 1.15}
_E_CLOUD = {"clear": 1.00, "overcast": 1.02, "dusk": 1.05}
_E_TEMP  = {"cold": 1.04, "mild": 1.00, "warm": 1.00, "hot": 1.01}

# Stamina decay (fatigue ramp). Hot+humid wears pitchers down faster;
# cool/dry weather extends them slightly. Returns a multiplier on the
# fatigue ramp magnitude — >1.0 means fatigue accumulates faster.
_STAM_TEMP = {"cold": 0.95, "mild": 1.00, "warm": 1.03, "hot": 1.07}
_STAM_HUM  = {"dry":  0.98, "normal": 1.00, "humid":  1.04}


def _mult(*factors: float) -> float:
    out = 1.0
    for f in factors:
        out *= f
    return out


def hr_multiplier(w: Weather | None) -> float:
    if w is None:
        return 1.0
    return _mult(_HR_TEMP[w.temperature], _HR_WIND[w.wind],
                 _HR_HUM[w.humidity], _HR_PRE[w.precip])


def hard_contact_multiplier(w: Weather | None) -> float:
    """Multiplier on the hard-contact share (vs weak/medium)."""
    if w is None:
        return 1.0
    return _mult(_HC_TEMP[w.temperature], _HC_PRE[w.precip])


def k_multiplier(w: Weather | None) -> float:
    if w is None:
        return 1.0
    return _mult(_K_TEMP[w.temperature], _K_CLOUD[w.cloud])


def error_multiplier(w: Weather | None) -> float:
    if w is None:
        return 1.0
    return _mult(_E_PRE[w.precip], _E_CLOUD[w.cloud], _E_TEMP[w.temperature])


def stamina_decay_multiplier(w: Weather | None) -> float:
    """Scale on the fatigue-ramp magnitude. >1.0 = pitchers tire faster."""
    if w is None:
        return 1.0
    return _mult(_STAM_TEMP[w.temperature], _STAM_HUM[w.humidity])
