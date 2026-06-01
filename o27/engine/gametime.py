"""
Game start time — local first-pitch clock stamped on every game.

Two concerns, deliberately split:

  LOCATION decides time zone and daylight. The home park's longitude
  fixes a whole-hour UTC offset (15° per hour; Zaryan cities carry
  explicit offsets because their geography spans five zones). The
  park's latitude plus the calendar date fix sunset via the standard
  sunrise equation, which in turn decides the `low_light` flag — the
  fading-light "harder to see the ball" penalty that used to ride on
  the retired `dusk` cloud tier (see o27/engine/weather.py).

  CONVENTION decides what o'clock games tend to begin. Three real-world
  baseball cultures, scrambled per game:

    MLB  — mostly night (~7:05 PM), weekend day games (1:05 / 4:05).
    NPB  — weekday 6:00 PM, weekend 1:00–2:00 PM (summer drifts night).
    KBO  — weekday 6:30 PM, weekend 2:00 / 5:00 PM.

Stamped at SCHEDULE time alongside weather, so /schedule can show first
pitch before a game runs. Pure: feed the same RNG state twice and you
get the same time.
"""
from __future__ import annotations
import datetime
import math
import random
from dataclasses import dataclass


# (hour, minute) first-pitch slots per convention, split day vs night.
# Minutes echo the real leagues' habit of starting a few past the hour.
_CONVENTIONS: dict[str, dict] = {
    "MLB": {
        "day":   [(13, 5), (13, 10), (13, 35), (14, 10), (16, 5)],
        "night": [(18, 40), (19, 5), (19, 10), (19, 15), (19, 35), (20, 10)],
        # p(day game) by weekday, Mon=0 .. Sun=6. Weekdays skew night;
        # Thursday is a getaway-day bump; weekends carry the day games.
        "p_day": (0.10, 0.10, 0.12, 0.25, 0.10, 0.50, 0.70),
    },
    "NPB": {
        "day":   [(13, 0), (13, 30), (14, 0)],
        "night": [(17, 45), (18, 0), (18, 0)],
        "p_day": (0.05, 0.05, 0.05, 0.05, 0.05, 0.45, 0.55),
    },
    "KBO": {
        "day":   [(14, 0), (17, 0)],
        "night": [(18, 30)],
        "p_day": (0.02, 0.02, 0.02, 0.02, 0.02, 0.50, 0.60),
    },
}
_CONVENTION_NAMES = tuple(_CONVENTIONS)

# A game starting this many minutes before sunset (or later) finishes in
# deepening dusk — a nine-inning game runs ~3 h, so an hour-before-sunset
# start spends most of itself under fading light. Trips the low-light flag.
_LOW_LIGHT_LEAD_MIN = 90

# Whole-hour UTC offset → local time-zone abbreviation. North America keeps
# the generic (no S/D) labels; the Russian Far East offsets the Zaryan league
# spans (+9..+13) use their real zone names; the rest fill in so a game never
# has to fall back to a bare "UTC+N".
_TZ_ABBREV = {
    -11: "SST",  -10: "HT",  -9: "AKT",  -8: "PT",  -7: "MT",  -6: "CT",
     -5: "ET",   -4: "AT",   -3: "ART",  -2: "GST", -1: "CVT",  0: "GMT",
      1: "CET",   2: "EET",   3: "MSK",   4: "SAMT", 5: "YEKT",  6: "OMST",
      7: "KRAT",  8: "IRKT",  9: "YAKT", 10: "VLAT", 11: "MAGT", 12: "PETT",
     13: "ANAT",
}


@dataclass(frozen=True)
class GameTime:
    start_minute: int           # minutes after local midnight (first pitch)
    utc_offset:   int | None    # home-park UTC offset, whole hours (None if unknown)
    low_light:    bool          # game runs into fading light → see-ball penalty
    convention:   str = "MLB"   # MLB / NPB / KBO — flavor, not persisted


def _weekday(game_date: str) -> int:
    """0 = Monday … 6 = Sunday."""
    return datetime.date.fromisoformat(game_date).weekday()


def _day_of_year(game_date: str) -> int:
    return datetime.date.fromisoformat(game_date).timetuple().tm_yday


def sunset_minute(lat: float, game_date: str) -> int:
    """Approximate local-solar-time sunset, minutes after midnight.

    Sunrise equation: solar declination from day-of-year, hour angle
    from latitude. Ignores the within-zone longitude correction and the
    equation of time — good to ~15 min, plenty for an "is it getting
    dark" flag. Clamped for polar day (never sets → 24:00) and polar
    night (never rises → noon).
    """
    doy = _day_of_year(game_date)
    decl = math.radians(23.44 * math.sin(2 * math.pi / 365.0 * (doy - 81)))
    latr = math.radians(lat)
    cos_h = -math.tan(latr) * math.tan(decl)
    cos_h = max(-1.0, min(1.0, cos_h))
    hour_angle = math.degrees(math.acos(cos_h))  # 0 .. 180 degrees
    return int(round((12.0 + hour_angle / 15.0) * 60))


def utc_offset_for(city: str, lon: float | None) -> int | None:
    """Whole-hour UTC offset for the home park, or None when it can't be
    determined (caller then omits the zone label rather than guessing).

    Zaryan cities carry explicit offsets (their five zones don't follow a
    clean longitude rule). Otherwise the offset comes from longitude
    (15° per hour) — and when the team row has no coordinates, we recover
    them from the weather gazetteer by city name before giving up.
    """
    if city:
        from o27.engine import zaryan_climate as _zc
        off = _zc.utc_offset(city)
        if off is not None:
            return off
    if lon is None and city:
        from o27.engine import weather as _wx
        c = _wx.coords_for_city(city)
        if c is not None:
            lon = c[1]
    if lon is None:
        return None
    return int(round(lon / 15.0))


def draw_game_time(rng: random.Random, game_date: str,
                   lat: float | None = None, lon: float | None = None,
                   city: str = "") -> GameTime:
    """Stamp a first-pitch time for a game on `game_date` (YYYY-MM-DD).

    Convention (MLB / NPB / KBO) is scrambled per game; the clock time
    is read in the home park's LOCAL time. Low-light is decided against
    the park's actual sunset for that date and latitude.
    """
    name = rng.choice(_CONVENTION_NAMES)
    spec = _CONVENTIONS[name]
    wd = _weekday(game_date)
    slots = spec["day"] if rng.random() < spec["p_day"][wd] else spec["night"]
    h, m = rng.choice(slots)
    start_minute = h * 60 + m

    if lat is None:
        low = start_minute >= 18 * 60          # no geography → clock fallback
    else:
        low = start_minute >= sunset_minute(lat, game_date) - _LOW_LIGHT_LEAD_MIN
    return GameTime(
        start_minute=start_minute,
        utc_offset=utc_offset_for(city, lon),
        low_light=bool(low),
        convention=name,
    )


def tz_label(utc_offset: int | None) -> str:
    """Local time-zone abbreviation (e.g. 'ET', 'VLAT'). Only an
    out-of-range offset would fall back to 'UTC+N'."""
    if utc_offset is None:
        return ""
    return _TZ_ABBREV.get(utc_offset, f"UTC{utc_offset:+d}")


def format_start(start_minute: int | None, utc_offset: int | None = None) -> str:
    """'7:05 PM ET' first-pitch string. Empty when start is unknown."""
    if start_minute is None:
        return ""
    h, m = divmod(int(start_minute), 60)
    ampm = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    label = tz_label(utc_offset)
    return f"{h12}:{m:02d} {ampm}" + (f" {label}" if label else "")
