"""
Schedule generation for O27v2 — MLB-style series scheduling.

The previous algorithm packed every team into every calendar day with no
breaks (162 games / 162 days = each team played every day for 162 days
straight). This version models a real MLB schedule:

  • Series of 2/3/4 consecutive games against the same opponent at one venue.
  • Off-days that fall out naturally between road trips and homestands.
  • A real All-Star break (4 days, mid-July) when nobody plays.
  • Calendar starts April 1 and stretches through ~late September / early
    October (depending on team count and games_per_team).

Pipeline:
  1. Build directed pairings (intra-/inter-division weighted) — same as
     the old generator, just refactored.
  2. Group the pairings into per-pair series. For each unordered pair of
     teams (A, B), partition the games between them into series with one
     home team per series and lengths in {2, 3, 4}. Home/away is balanced.
  3. Assign series to calendar days using a greedy slot scheduler:
       - Walk days from `season_start`, skipping the All-Star break window.
       - Each day, advance any series already in progress (same opponents
         the next day).
       - For each free team, try to start a new series from its queue,
         pairing with another free team that also has the matchup queued.
       - Off-days happen naturally when a free team has no compatible
         partner (or the matching round can't fill all teams).

Reseed-on-different-seed:
  `seed_schedule` consults the active league meta seed. If the meta seed
  differs from the requested rng_seed, the games table (and the cached
  sim_date) is wiped and regenerated. Same-seed calls are still no-ops.
"""
from __future__ import annotations
import random
import datetime
from collections import defaultdict, deque


# Default season anchors. These can be overridden via league config keys
# `season_year`, `season_start_month`, `season_start_day`,
# `all_star_break_month`, `all_star_break_day`, `all_star_break_days`.
_DEFAULT_SEASON_YEAR        = 2026
_DEFAULT_SEASON_START_MONTH = 4
_DEFAULT_SEASON_START_DAY   = 1
# All-Star break: Mon–Thu of the week containing MLB's mid-July All-Star
# game. For 2026, the break starts Monday July 13 and runs through Thursday
# July 16 (4 days, no games).
_DEFAULT_ASB_MONTH = 7
_DEFAULT_ASB_DAY   = 13
_DEFAULT_ASB_DAYS  = 4

# Soft cap on how far past `season_days` we'll let the calendar run before
# erroring out. Series scheduling is greedy and occasionally needs an extra
# week or two of slack (especially for tight schedules with many series).
_CALENDAR_SLACK_DAYS = 30


# ---------------------------------------------------------------------------
# Pair generation (intra-/inter-division weighted)
# ---------------------------------------------------------------------------

def _generate_pairings(
    team_ids: list[int],
    games_per_team: int,
    div_map: dict[int, str],
    intra_w: float,
    inter_w: float,
    rng: random.Random,
) -> list[tuple[int, int]]:
    """Return a list of directed (home, away) games with each team appearing
    exactly `games_per_team` times. Honours intra/inter division weighting
    when divisions are present."""
    n = len(team_ids)
    has_div = bool(div_map) and all(v for v in div_map.values())
    use_div_weights = has_div and intra_w > 0.0 and inter_w > 0.0

    directed: list[tuple[int, int]] = []

    if use_div_weights:
        intra_pairs: list[tuple[int, int]] = []
        inter_pairs: list[tuple[int, int]] = []
        for i in range(n):
            for j in range(i + 1, n):
                a, b = team_ids[i], team_ids[j]
                if div_map[a] == div_map[b]:
                    intra_pairs.append((a, b))
                else:
                    inter_pairs.append((a, b))

        # Per-team opponent counts in each bucket.
        div_sizes = defaultdict(int)
        for tid in team_ids:
            div_sizes[div_map[tid]] += 1
        # Equal-sized divisions assumed (config-validated).
        n_d = next(iter(div_sizes.values())) if div_sizes else n
        n_intra_per_team = max(1, n_d - 1)
        n_inter_per_team = max(1, n - n_d)

        intra_target = round(games_per_team * intra_w)
        inter_target = games_per_team - intra_target

        intra_full  = intra_target // n_intra_per_team
        intra_extra = intra_target %  n_intra_per_team
        inter_full  = inter_target // n_inter_per_team
        inter_extra = inter_target %  n_inter_per_team

        for _ in range(intra_full):
            for a, b in intra_pairs:
                directed.append((a, b) if rng.random() < 0.5 else (b, a))
        for _ in range(inter_full):
            for a, b in inter_pairs:
                directed.append((a, b) if rng.random() < 0.5 else (b, a))

        for rnd in range(intra_extra + inter_extra):
            if rnd < intra_extra:
                matching = _intra_div_matching(list(team_ids), div_map, rng)
            else:
                matching = _inter_div_matching(list(team_ids), div_map, rng)
            for a, b in matching:
                directed.append((a, b) if rng.random() < 0.5 else (b, a))
    else:
        full_rounds = games_per_team // (n - 1)
        for _ in range(full_rounds):
            for i in range(n):
                for j in range(i + 1, n):
                    directed.append(
                        (team_ids[i], team_ids[j])
                        if rng.random() < 0.5
                        else (team_ids[j], team_ids[i])
                    )
        extra = games_per_team % (n - 1)
        for _ in range(extra):
            for a, b in _perfect_matching(list(team_ids), rng):
                directed.append((a, b))

    counts: dict[int, int] = {tid: 0 for tid in team_ids}
    for home, away in directed:
        counts[home] += 1
        counts[away] += 1
    bad = [tid for tid, c in counts.items() if c != games_per_team]
    if bad:
        raise RuntimeError(
            f"Schedule imbalance after generation for team IDs: {bad} "
            f"(expected {games_per_team} each)"
        )
    return directed


def _perfect_matching(ids: list[int], rng: random.Random) -> list[tuple[int, int]]:
    shuffled = list(ids)
    rng.shuffle(shuffled)
    return [(shuffled[i], shuffled[i + 1]) for i in range(0, len(shuffled), 2)]


def _intra_div_matching(
    team_ids: list[int],
    div_map: dict[int, str],
    rng: random.Random,
) -> list[tuple[int, int]]:
    remaining = list(team_ids)
    rng.shuffle(remaining)
    pairs: list[tuple[int, int]] = []
    while len(remaining) >= 2:
        a = remaining.pop(0)
        for idx, b in enumerate(remaining):
            if div_map.get(a) == div_map.get(b):
                pairs.append((a, b))
                remaining.pop(idx)
                break
        else:
            pairs.append((a, remaining.pop(0)))
    return pairs


def _inter_div_matching(
    team_ids: list[int],
    div_map: dict[int, str],
    rng: random.Random,
) -> list[tuple[int, int]]:
    remaining = list(team_ids)
    rng.shuffle(remaining)
    pairs: list[tuple[int, int]] = []
    while len(remaining) >= 2:
        a = remaining.pop(0)
        for idx, b in enumerate(remaining):
            if div_map.get(a) != div_map.get(b):
                pairs.append((a, b))
                remaining.pop(idx)
                break
        else:
            pairs.append((a, remaining.pop(0)))
    return pairs


# ---------------------------------------------------------------------------
# Pair → series partitioning
# ---------------------------------------------------------------------------

def _chunks_for_count(rem: int, rng: random.Random) -> list[int]:
    """Partition `rem` games into series lengths in {2, 3, 4}. Falls back
    to a 1-game series only if `rem == 1` and no merge is possible."""
    out: list[int] = []
    while rem >= 5:
        size = 4 if rng.random() < 0.45 else 3
        out.append(size)
        rem -= size
    if rem == 4:
        out.append(4)
    elif rem == 3:
        out.append(3)
    elif rem == 2:
        out.append(2)
    elif rem == 1:
        # Steal a game from a previous chunk if it's >2, else make a
        # 1-game series (edge case for very thin matchups).
        for i in range(len(out)):
            if out[i] > 2:
                out[i] -= 1
                out.append(2)
                rem = 0
                break
        else:
            out.append(1)
    return out


def _partition_pair_into_series(
    a: int,
    b: int,
    games: list[tuple[int, int]],
    rng: random.Random,
) -> list[tuple[int, int, int]]:
    """Given the directed games between teams a and b, return a list of
    series (home, away, length). Series lengths are 2-4 (with rare 1).
    Home/away counts respect the original directed counts."""
    a_home = sum(1 for h, w in games if h == a)
    b_home = len(games) - a_home

    a_chunks = _chunks_for_count(a_home, rng)
    b_chunks = _chunks_for_count(b_home, rng)

    series: list[tuple[int, int, int]] = []
    series.extend((a, b, c) for c in a_chunks)
    series.extend((b, a, c) for c in b_chunks)
    rng.shuffle(series)
    return series


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------

def _resolve_calendar(config: dict | None) -> tuple[datetime.date, set[str], set[int]]:
    """Return (season_start_date, set_of_asb_iso_dates, set_of_weekly_off_dows)
    honouring config overrides. Defaults: April 1, 2026 start; July 13–16 ASB;
    no weekly league-wide off-days.

    `weekly_off_dows` is a set of Python weekday integers (0=Mon ... 6=Sun);
    every date matching one of those weekdays is treated like an ASB day —
    the scheduler skips it entirely, so it falls out as a league-wide off-day.
    A real-MLB-feel cadence is `[0]` (no Monday games) or `[0, 3]` (no Mon/Thu).
    """
    cfg = config or {}
    year  = int(cfg.get("season_year", _DEFAULT_SEASON_YEAR))
    smon  = int(cfg.get("season_start_month", _DEFAULT_SEASON_START_MONTH))
    sday  = int(cfg.get("season_start_day",   _DEFAULT_SEASON_START_DAY))
    amon  = int(cfg.get("all_star_break_month", _DEFAULT_ASB_MONTH))
    aday  = int(cfg.get("all_star_break_day",   _DEFAULT_ASB_DAY))
    alen  = int(cfg.get("all_star_break_days",  _DEFAULT_ASB_DAYS))
    start = datetime.date(year, smon, sday)
    asb_start = datetime.date(year, amon, aday)
    asb_dates = {
        (asb_start + datetime.timedelta(days=i)).isoformat()
        for i in range(alen)
    }
    raw_dows = cfg.get("weekly_off_dows") or []
    weekly_off: set[int] = set()
    for d in raw_dows:
        try:
            di = int(d)
        except (TypeError, ValueError):
            continue
        if 0 <= di <= 6:
            weekly_off.add(di)
    return start, asb_dates, weekly_off


# ---------------------------------------------------------------------------
# Series → calendar scheduling
# ---------------------------------------------------------------------------

class _SeriesState:
    """One in-progress series, shared between both teams' active slots."""
    __slots__ = ("home", "away", "length", "days_done")

    def __init__(self, home: int, away: int, length: int):
        self.home = home
        self.away = away
        self.length = length
        self.days_done = 0


def _schedule_series(
    series_list: list[tuple[int, int, int]],
    team_ids: list[int],
    season_start: datetime.date,
    asb_dates: set[str],
    season_days: int,
    rng: random.Random,
    weekly_off_dows: set[int] | None = None,
) -> list[dict]:
    """Greedy series-aware scheduler. Walks the calendar one day at a time,
    advancing in-progress series and starting new ones for free teams.

    Off-days fall out for any team whose queue has no opponent free today.
    All teams are off during ASB days, and on any weekday in
    `weekly_off_dows` (0=Mon ... 6=Sun) — that's the league-wide off-day
    cadence. Active series pause across these gaps and resume when the
    calendar reopens, so a 4-game series can span Sun–Tue–Wed–Thu if Mon
    is off."""
    weekly_off = weekly_off_dows or set()

    # Per-team queue of pending series (each series appears in BOTH teams'
    # queues; first team to schedule it wins and removes from both).
    team_q: dict[int, deque] = {t: deque() for t in team_ids}
    rng.shuffle(series_list)
    # Tag each series with a unique id so we can remove it from both queues.
    tagged = [(i, s) for i, s in enumerate(series_list)]
    for sid, (home, away, length) in tagged:
        team_q[home].append((sid, home, away, length))
        team_q[away].append((sid, home, away, length))
    scheduled: set[int] = set()

    active: dict[int, _SeriesState | None] = {t: None for t in team_ids}

    games: list[dict] = []
    day = 0
    max_day = season_days + _CALENDAR_SLACK_DAYS
    pending_series = len(series_list)

    while pending_series > 0 or any(active[t] is not None for t in team_ids):
        if day > max_day:
            remaining = sum(len(q) for q in team_q.values()) // 2
            raise RuntimeError(
                f"Schedule could not fit into {max_day} days "
                f"(~{remaining} series remain unscheduled). "
                f"Bump season_days or shorten games_per_team."
            )

        date = season_start + datetime.timedelta(days=day)
        date_iso = date.isoformat()

        if date_iso in asb_dates or date.weekday() in weekly_off:
            day += 1
            continue

        used_today: set[int] = set()

        # --- Step 1: advance any in-progress series ---
        # Iterate by unique series object so we emit exactly one game per
        # active series per day.
        seen: set[int] = set()
        for t in team_ids:
            ss = active[t]
            if ss is None or id(ss) in seen:
                continue
            seen.add(id(ss))
            games.append({
                "season":       1,
                "game_date":    date_iso,
                "home_team_id": ss.home,
                "away_team_id": ss.away,
            })
            used_today.add(ss.home)
            used_today.add(ss.away)
            ss.days_done += 1
            if ss.days_done >= ss.length:
                active[ss.home] = None
                active[ss.away] = None

        # --- Step 2: start new series for free teams ---
        # Shuffle to avoid biasing toward low team_ids when partners compete.
        free = [t for t in team_ids if active[t] is None and t not in used_today]
        rng.shuffle(free)
        for t in free:
            if t in used_today or active[t] is not None:
                continue
            q = team_q[t]
            chosen: tuple | None = None
            for entry in q:
                sid, home, away, length = entry
                if sid in scheduled:
                    continue
                opp = away if t == home else home
                if opp in used_today or active[opp] is not None:
                    continue
                chosen = entry
                break
            if chosen is None:
                continue  # team takes an implicit off-day today

            sid, home, away, length = chosen
            ss = _SeriesState(home, away, length)
            ss.days_done = 1
            games.append({
                "season":       1,
                "game_date":    date_iso,
                "home_team_id": home,
                "away_team_id": away,
            })
            used_today.add(home)
            used_today.add(away)
            active[home] = ss
            active[away] = ss
            scheduled.add(sid)
            pending_series -= 1
            if length == 1:
                # 1-game series: closes immediately.
                active[home] = None
                active[away] = None
            # Lazy-clean both queues: drop the chosen entry. Other already-
            # scheduled entries at the head get popped on next visit.
            try:
                team_q[home].remove(chosen)
            except ValueError:
                pass
            try:
                team_q[away].remove(chosen)
            except ValueError:
                pass

        day += 1

    # Verify integrity: no team plays twice on the same date.
    by_date: dict[str, list[int]] = defaultdict(list)
    for g in games:
        by_date[g["game_date"]].extend([g["home_team_id"], g["away_team_id"]])
    for date_iso, ids in by_date.items():
        if len(ids) != len(set(ids)):
            from collections import Counter as _C
            dup = [tid for tid, c in _C(ids).items() if c > 1]
            raise RuntimeError(f"Date {date_iso} double-booked teams: {dup}")
    return games


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_schedule(
    teams: list[dict],
    games_per_team: int = 30,
    season_days: int = 186,
    rng_seed: int = 42,
    config: dict | None = None,
) -> list[dict]:
    """Generate an MLB-style schedule.

    Args:
        teams:          List of team dicts; must have 'id'; optionally 'division'.
        games_per_team: Target games per team.
        season_days:    Calendar spread budget. The series scheduler may use
                        up to `season_days + 30` days of slack.
        rng_seed:       RNG seed for reproducibility.
        config:         League config dict — may carry intra/inter division
                        weights and calendar overrides (season_year, ASB date).

    Returns:
        List of game dicts: {season, game_date, home_team_id, away_team_id}
        with games organised into series (one home team per consecutive run
        of dates) and an All-Star break window respected.
    """
    rng = random.Random(rng_seed)
    team_ids = [t["id"] for t in teams]
    n = len(team_ids)
    if n < 2:
        raise ValueError("Need at least 2 teams")
    if n % 2 != 0:
        raise ValueError("Number of teams must be even for balanced scheduling")

    div_map = {t["id"]: t.get("division") for t in teams}
    intra_w = float((config or {}).get("intra_division_weight", 0.0))
    inter_w = float((config or {}).get("inter_division_weight", 0.0))

    # Step 1: directed pair generation.
    directed = _generate_pairings(
        team_ids, games_per_team, div_map, intra_w, inter_w, rng
    )

    # Step 2: group into per-pair series. Iterate unordered pairs to keep
    # each pair's series lengths internally consistent.
    by_pair: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for home, away in directed:
        key = (home, away) if home < away else (away, home)
        by_pair[key].append((home, away))

    series_list: list[tuple[int, int, int]] = []
    for (a, b), games_for_pair in by_pair.items():
        series_list.extend(_partition_pair_into_series(a, b, games_for_pair, rng))

    # Step 3: lay series onto the calendar.
    season_start, asb_dates, weekly_off = _resolve_calendar(config)
    games = _schedule_series(
        series_list, team_ids, season_start, asb_dates, season_days, rng,
        weekly_off_dows=weekly_off,
    )
    return games


# ---------------------------------------------------------------------------
# DB seeding
# ---------------------------------------------------------------------------

def verify_balance(games: list[dict], team_ids: list[int]) -> dict[int, int]:
    """Return a per-team game count dict for verification."""
    counts: dict[int, int] = {tid: 0 for tid in team_ids}
    for g in games:
        counts[g["home_team_id"]] += 1
        counts[g["away_team_id"]] += 1
    return counts


def seed_schedule(
    games_per_team: int | None = None,
    season_days: int | None = None,
    config_id: str = "30teams",
    rng_seed: int = 42,
    config: dict | None = None,
) -> int:
    """Insert the schedule into the database.

    Pass either `config_id` (loads `data/league_configs/<id>.json`) or
    a fully-built `config` dict (used by the parametric / "build your own
    league" form). When both are provided, the dict wins.

    Reseed-on-different-seed: if the games table already has rows but the
    active league meta seed differs from `rng_seed` (or no meta seed is
    recorded), the games table and cached sim_date are wiped and the
    schedule is regenerated under the requested seed. Same-seed calls
    are still no-ops (returning 0).

    Returns the number of games inserted (0 if nothing was written).
    """
    from o27v2 import db
    from o27v2.league import get_config
    from o27v2.season_archive import get_active_league_meta

    cfg = config if config is not None else get_config(config_id)
    if games_per_team is None:
        games_per_team = cfg["games_per_team"]
    if season_days is None:
        season_days = cfg["season_days"]

    existing = db.fetchone("SELECT COUNT(*) as n FROM games")
    has_games = bool(existing and existing["n"])
    if has_games:
        meta_seed, _meta_cfg = get_active_league_meta()
        if meta_seed == rng_seed:
            # Same seed → schedule already reflects this run; nothing to do.
            return 0
        # Different seed (or unrecorded) → refresh the schedule. Played
        # results would be invalidated by a different schedule, so wipe
        # the supporting tables too.
        db.execute("DELETE FROM games")
        db.execute("DELETE FROM game_batter_stats")
        db.execute("DELETE FROM game_pitcher_stats")
        db.execute("DELETE FROM team_phase_outs")
        db.execute("DELETE FROM sim_meta WHERE key = 'sim_date'")

    teams = db.fetchall("SELECT id, division, city FROM teams ORDER BY id")
    if not teams:
        raise RuntimeError("seed_league() must be called before seed_schedule()")

    games = generate_schedule(
        teams,
        games_per_team=games_per_team,
        season_days=season_days,
        rng_seed=rng_seed,
        config=cfg,
    )

    # Weather draw — stamped at schedule time so /schedule shows conditions
    # before the game is played. RNG forks off the schedule seed so reseeds
    # land deterministically.
    from o27.engine.weather import draw_weather
    city_by_id = {t["id"]: (t.get("city") or "") for t in teams}
    weather_rng = random.Random((rng_seed or 0) ^ 0xBA5EBA11)

    rows = []
    for g in games:
        home_city = city_by_id.get(g["home_team_id"], "")
        w = draw_weather(weather_rng, home_city, g["game_date"])
        rows.append((
            g["season"], g["game_date"], g["home_team_id"], g["away_team_id"],
            w.temperature, w.wind, w.humidity, w.precip, w.cloud,
        ))

    db.executemany(
        "INSERT INTO games (season, game_date, home_team_id, away_team_id, "
        "temperature_tier, wind_tier, humidity_tier, precip_tier, cloud_tier) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(games)
