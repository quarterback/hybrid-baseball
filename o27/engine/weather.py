"""
Weather model — lightweight categorical game conditions.

Five categorical variables stamped on every game at SCHEDULE time:

  temperature  cold / mild / warm / hot
  wind         out / neutral / in / cross
  humidity     dry / normal / humid
  precipitation none / light / heavy   (heavy is flagged but still plays)
  cloud        clear / overcast / dusk

Drawn from a city -> climatological archetype lookup, with a per-archetype
per-month tier distribution. Seven archetypes cover the catalogue:
desert, coastal_cool, coastal_warm, continental_cold, continental_warm,
tropical, mountain.

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
}


def archetype_for_city(city: str) -> str:
    """Return the climatological archetype for `city`. Unknown -> continental_warm."""
    if not city:
        return "continental_warm"
    return _CITY_ARCHETYPES.get(city.strip(), "continental_warm")


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

_TABLES: dict[str, dict] = {
    "desert":            _DESERT,
    "coastal_cool":      _COASTAL_COOL,
    "coastal_warm":      _COASTAL_WARM,
    "continental_cold":  _CONTINENTAL_COLD,
    "continental_warm":  _CONTINENTAL_WARM,
    "tropical":          _TROPICAL,
    "mountain":          _MOUNTAIN,
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


def draw_weather(rng: random.Random, city: str, game_date: str) -> Weather:
    """Draw a Weather sample for `city` on `game_date` (YYYY-MM-DD).

    Pure: feed the same RNG state twice and you get the same sample.
    """
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
