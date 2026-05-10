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

        # If a bucket is impossible (single division → no inter pairs;
        # divisions of size 1 → no intra pairs), redirect its share to
        # the other bucket so we still hit games_per_team.
        if not inter_pairs:
            intra_target = games_per_team
            inter_target = 0
        elif not intra_pairs:
            intra_target = 0
            inter_target = games_per_team
        else:
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


# ---------------------------------------------------------------------------
# Tiered-mode pairings (4-tier promotion/relegation league)
# ---------------------------------------------------------------------------
#
# Used when `config["schedule_mode"] == "tiered"`. Tiers are top-down ordered
# in `config["tier_order"]`; each team's tier is its `league` value. Pair-
# wise game counts are read from the config:
#
#   tier_in_league_games_per_pair    — round-robin within a tier
#   tier_schedule_pairs              — list of [tier_a, tier_b] pairs that
#                                       play cross-tier games this season
#   tier_cross_games_per_pair        — games per opponent in a paired tier
#
# Tier pairing is what actually balances 80 games for every team in the
# 56-team config: G↔P and N↔A are the only cross-tier edges. P-N has no
# games even though they're adjacent for promotion/relegation purposes;
# the standings ladder is what moves teams between Premier and National,
# not the schedule.

def _generate_tiered_pairings(
    team_ids: list[int],
    tier_map: dict[int, str],
    config: dict,
    rng: random.Random,
) -> list[tuple[int, int]]:
    """Tiered schedule: round-robin per tier + paired-tier cross games.

    Returns a directed (home, away) list. Home/away is balanced per pair
    by alternating the assignment so a 2-game pair gets 1 home + 1 away
    and a 4-game pair gets 2 home + 2 away.
    """
    in_tier_per_pair = int(config.get("tier_in_league_games_per_pair", 4))
    cross_per_pair   = int(config.get("tier_cross_games_per_pair", 2))
    pairs_cfg        = config.get("tier_schedule_pairs") or []

    # Bucket teams by tier.
    tier_teams: dict[str, list[int]] = defaultdict(list)
    for tid in team_ids:
        tier = tier_map.get(tid)
        if tier:
            tier_teams[tier].append(tid)

    directed: list[tuple[int, int]] = []

    def _emit_pair(a: int, b: int, n_games: int) -> None:
        # Alternate home so each pair lands at floor/ceil home counts.
        # First game's host is randomised so successive seasons don't
        # always favour the same team.
        first_home_a = rng.random() < 0.5
        for k in range(n_games):
            host_a = first_home_a if (k % 2 == 0) else not first_home_a
            if host_a:
                directed.append((a, b))
            else:
                directed.append((b, a))

    # In-tier round-robin.
    for tier_name, ids in tier_teams.items():
        ids = list(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                _emit_pair(ids[i], ids[j], in_tier_per_pair)

    # Cross-tier games for each scheduled tier-pair.
    for entry in pairs_cfg:
        if not entry or len(entry) != 2:
            continue
        a_tier, b_tier = entry[0], entry[1]
        a_ids = tier_teams.get(a_tier, [])
        b_ids = tier_teams.get(b_tier, [])
        if not a_ids or not b_ids:
            continue
        for a in a_ids:
            for b in b_ids:
                _emit_pair(a, b, cross_per_pair)

    return directed


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
    """Partition `rem` games into series lengths in {2, 3, 4}, tuned to
    the game count.

    Real-MLB shape: most series are 3 games, with 4-game sets common
    when the calendar permits and 2-game sets used to balance the math.
    Pair-game counts that don't decompose cleanly (e.g. exactly 1) get
    handled by stealing a game from a longer chunk.

    The tuning depends on `rem`:
      - Thin pairs (≤4 games): single series of that length, no chopping.
      - Mid pairs (5-9): mix of 2 / 3 / 4, weighted toward 3.
      - Thick pairs (≥10): closer to MLB's pattern — mostly 3-game with
        a few 4-game; 2-game series only as residue.
    """
    out: list[int] = []
    if rem <= 0:
        return out
    if rem <= 4:
        # 1- to 4-game pair: keep the entire matchup as one series. A
        # 1-game leg is rare (only fires when directed-pair math gives
        # one team a single home game in the entire season) but legal.
        return [rem]

    # For thicker pairs, MLB-style: prefer 3-game, sprinkle in 4-game,
    # use 2-game only when needed to land on an integer.
    if rem >= 10:
        prob_4 = 0.30  # ~30% of long pairs are 4-game sets
    else:
        prob_4 = 0.40  # mid pairs slightly more 4s to absorb the slack

    while rem >= 5:
        size = 4 if rng.random() < prob_4 else 3
        out.append(size)
        rem -= size

    if rem == 4 or rem == 3 or rem == 2:
        out.append(rem)
    elif rem == 1:
        # Steal a game from a previous chunk if it's >2 (turn a 4 into
        # a 3 + 2, or a 3 into a 2 + 2). Falls back to a 1-game series
        # only when every prior chunk is already 2 — extremely rare.
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
    max_consec_days: int = 14,
    target_stand_length: int = 3,
    soft_consec_days: int = 6,
    games_per_team: int | None = None,
) -> list[dict]:
    """Greedy series-aware scheduler. Walks the calendar one day at a time,
    advancing in-progress series and starting new ones for free teams.

    Off-days fall out for any team whose queue has no opponent free today.
    All teams are off during ASB days, and on any weekday in
    `weekly_off_dows` (0=Mon ... 6=Sun) — that's the league-wide off-day
    cadence. Active series pause across these gaps and resume when the
    calendar reopens, so a 4-game series can span Sun-Tue-Wed-Thu if Mon
    is off.

    Three layered constraints govern off-days:

    * **Hard streak cap**: any team that has played `max_consec_days`
      calendar days in a row is forced off today. MLB CBA caps consec
      game-days at 20, but real schedules rarely push past 14, so the
      default is 14. Off-days reset the streak.

    * **Soft per-team off-day pacing** (NEW): once a team passes
      `soft_consec_days` games in a row AND has played at least its
      pro-rated share of the season (per `games_per_team` / `season_days`),
      it takes an off-day with high probability instead of starting a new
      series. Without this, a free team always grabs a partner if one is
      available — producing 20-game streaks and "all 36 teams play" days.
      Real MLB schedules have an off-day every ~7-10 game-days per team;
      this rule reproduces that pattern instead of cramming off-days into
      league-wide pauses. `games_per_team` must be set for it to fire.

    * **Road-trip / homestand grouping**: when a free team has multiple
      compatible series in its queue, pick the one that keeps the team
      in its current stand (home or away) until `target_stand_length`
      series, then switch. This produces real-MLB-shaped trips
      (3-4 series at home, 3-4 on the road) instead of single-series
      flips. Default target is 3 series per stand.
    """
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
    # Stand state: which mode each team last played in, and how many
    # consecutive series they've been in that mode. Off-days don't reset
    # the stand (a team coming off a 1-day break still continues their
    # homestand) but do reset the consecutive-game-day streak.
    last_stand: dict[int, str | None] = {t: None for t in team_ids}
    stand_len:  dict[int, int]        = {t: 0    for t in team_ids}
    consec_days: dict[int, int]       = {t: 0    for t in team_ids}
    # Total game-days each team has logged so far. Drives the soft off-day
    # pacing rule: a team that's at-or-ahead of its pro-rated games target
    # is biased toward an off-day once it's past `soft_consec_days`.
    games_played: dict[int, int]      = {t: 0    for t in team_ids}
    pace_per_day = (games_per_team / season_days) if games_per_team else None

    def _stand_score(t: int, home: int) -> int:
        """Higher = better fit for team `t` if this series gets scheduled.
        Continues the current stand until `target_stand_length`, then
        prefers flipping. Teams with no current stand take anything."""
        new_stand = "home" if t == home else "away"
        cur = last_stand[t]
        if cur is None:
            return 1
        if cur == new_stand:
            # Continue: prefer until we hit the target length.
            return 2 if stand_len[t] < target_stand_length else 0
        else:
            # Flip: prefer once we've completed a stand of decent length.
            return 2 if stand_len[t] >= target_stand_length else 0

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
            # League-wide off day: every team's consec-day streak resets.
            for t in team_ids:
                consec_days[t] = 0
            day += 1
            continue

        used_today: set[int] = set()
        played_today: set[int] = set()

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
            played_today.add(ss.home)
            played_today.add(ss.away)
            ss.days_done += 1
            if ss.days_done >= ss.length:
                active[ss.home] = None
                active[ss.away] = None

        # --- Step 2a: pre-compute teams that should rest today ---
        # Soft pacing: a free team that's at-or-ahead of its pro-rated
        # games target AND has been playing for `soft_consec_days` in a
        # row takes an off-day with high probability instead of grabbing
        # the next compatible series. Without this rule the greedy picker
        # produces 20-game streaks because there's almost always a
        # compatible partner.
        rest_today: set[int] = set()
        if pace_per_day is not None:
            expected_games = pace_per_day * (day + 1)
            for t in team_ids:
                if active[t] is not None or consec_days[t] < soft_consec_days:
                    continue
                if games_played[t] < expected_games - 0.5:
                    # Behind pace — keep playing.
                    continue
                # Ramp the rest probability with consec_days: at the soft
                # cap a 60% chance, scaling to 95% as we approach the
                # hard cap. Series continuations are unaffected; only the
                # decision to *start* a new series is biased.
                span = max(1, max_consec_days - soft_consec_days)
                ratio = (consec_days[t] - soft_consec_days) / span
                p = 0.6 + 0.35 * min(1.0, max(0.0, ratio))
                if rng.random() < p:
                    rest_today.add(t)

        # --- Step 2b: start new series for free teams ---
        # Shuffle to avoid biasing toward low team_ids when partners compete.
        free = [t for t in team_ids
                if active[t] is None
                and t not in used_today
                and t not in rest_today]
        rng.shuffle(free)
        for t in free:
            if t in used_today or active[t] is not None:
                continue
            # Force off-day if this team would exceed the consecutive-day cap.
            if consec_days[t] >= max_consec_days:
                continue
            q = team_q[t]
            best: tuple | None = None
            best_score = -1
            for entry in q:
                sid, home, away, length = entry
                if sid in scheduled:
                    continue
                opp = away if t == home else home
                if opp in used_today or active[opp] is not None:
                    continue
                if consec_days[opp] >= max_consec_days:
                    continue
                if opp in rest_today:
                    continue
                # Score: own stand-fit + opponent's stand-fit (so the
                # picker doesn't break the opponent's homestand pattern).
                score = _stand_score(t, home) + _stand_score(opp, home)
                if score > best_score:
                    best_score = score
                    best = entry
                    # Score-2-for-self / score-2-for-opp = 4 is the max;
                    # short-circuit when achievable.
                    if best_score >= 4:
                        break
            chosen = best
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
            played_today.add(home)
            played_today.add(away)
            active[home] = ss
            active[away] = ss
            scheduled.add(sid)
            pending_series -= 1
            # Update stand state.
            for tid, is_home in ((home, True), (away, False)):
                stand = "home" if is_home else "away"
                if last_stand[tid] == stand:
                    stand_len[tid] += 1
                else:
                    last_stand[tid] = stand
                    stand_len[tid] = 1
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

        # --- Step 3: update consecutive-game-day streaks + games_played ---
        for t in team_ids:
            if t in played_today:
                consec_days[t] += 1
                games_played[t] += 1
            else:
                consec_days[t] = 0

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
    if (config or {}).get("schedule_mode") == "tiered":
        tier_map = {t["id"]: t.get("league") for t in teams}
        directed = _generate_tiered_pairings(team_ids, tier_map, config or {}, rng)
        # Verify the generated pairings hit games_per_team for every team.
        counts: dict[int, int] = {tid: 0 for tid in team_ids}
        for home, away in directed:
            counts[home] += 1
            counts[away] += 1
        bad = [tid for tid, c in counts.items() if c != games_per_team]
        if bad:
            raise RuntimeError(
                f"Tiered schedule imbalance for team IDs: {bad} "
                f"(expected {games_per_team} each). Check tier_order, "
                f"tier_schedule_pairs, and per-pair counts in config."
            )
    else:
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
    cfg = config or {}
    max_consec       = int(cfg.get("max_consecutive_game_days", 14))
    target_stand     = int(cfg.get("target_stand_length", 3))
    soft_consec      = int(cfg.get("soft_consecutive_game_days", 6))
    games = _schedule_series(
        series_list, team_ids, season_start, asb_dates, season_days, rng,
        weekly_off_dows=weekly_off,
        max_consec_days=max_consec,
        target_stand_length=target_stand,
        soft_consec_days=soft_consec,
        games_per_team=games_per_team,
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


def verify_opponent_balance(
    games: list[dict],
    teams: list[dict],
    config: dict | None = None,
) -> dict:
    """Inspect opponent distribution and return a structured report:

        {
          "ok":              bool,             # True if no warnings
          "intra_avg":       float,            # avg games per intra-div opponent
          "inter_avg":       float,            # avg games per inter-div opponent
          "intra_spread":    int,              # max - min intra count, per pair
          "inter_spread":    int,              # max - min inter count, per pair
          "off_day_min":     int,              # min off-days any team got
          "off_day_max":     int,              # max off-days
          "off_day_avg":     float,
          "warnings":        list[str],        # human-readable issues
        }

    Doesn't raise; the caller (e.g. seed_schedule) decides whether to log
    or surface in the UI. The form-handler displays it on the new-league
    flash banner so the user sees what they're getting before clicking
    "Sim Today" 50 times and noticing imbalance.
    """
    cfg = config or {}
    div_map = {t["id"]: t.get("division") for t in teams}

    pair_counts: dict[tuple[int, int], int] = defaultdict(int)
    for g in games:
        a, b = g["home_team_id"], g["away_team_id"]
        key = (a, b) if a < b else (b, a)
        pair_counts[key] += 1

    intra_pair_counts: list[int] = []
    inter_pair_counts: list[int] = []
    for (a, b), n in pair_counts.items():
        same_div = (div_map.get(a) == div_map.get(b)) and div_map.get(a)
        if same_div:
            intra_pair_counts.append(n)
        else:
            inter_pair_counts.append(n)

    def _avg(xs: list[int]) -> float:
        return (sum(xs) / len(xs)) if xs else 0.0
    def _spread(xs: list[int]) -> int:
        return (max(xs) - min(xs)) if xs else 0

    # Per-team off-day count. An off-day is any calendar date in the
    # span of the schedule on which the team didn't play. We don't have
    # the calendar bounds here directly, so approximate using the played
    # game-date span.
    if games:
        all_dates = sorted({g["game_date"] for g in games})
        first, last = all_dates[0], all_dates[-1]
        first_d = datetime.date.fromisoformat(first)
        last_d  = datetime.date.fromisoformat(last)
        total_days = (last_d - first_d).days + 1
    else:
        total_days = 0

    games_played: dict[int, set[str]] = defaultdict(set)
    for g in games:
        games_played[g["home_team_id"]].add(g["game_date"])
        games_played[g["away_team_id"]].add(g["game_date"])
    off_days = [total_days - len(d) for d in games_played.values()]

    warnings: list[str] = []
    intra_spread = _spread(intra_pair_counts)
    inter_spread = _spread(inter_pair_counts)
    if intra_spread > 4:
        warnings.append(
            f"Intra-division opponent counts vary by {intra_spread} games "
            f"(some pairs play many more than others)."
        )
    if inter_spread > 4:
        warnings.append(
            f"Inter-division opponent counts vary by {inter_spread} games."
        )
    if off_days and (max(off_days) - min(off_days)) > 10:
        warnings.append(
            f"Off-day distribution is uneven: min {min(off_days)}, "
            f"max {max(off_days)} per team."
        )

    return {
        "ok":           not warnings,
        "intra_avg":    _avg(intra_pair_counts),
        "inter_avg":    _avg(inter_pair_counts),
        "intra_spread": intra_spread,
        "inter_spread": inter_spread,
        "off_day_min":  min(off_days) if off_days else 0,
        "off_day_max":  max(off_days) if off_days else 0,
        "off_day_avg":  _avg(off_days),
        "warnings":     warnings,
    }


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

    teams = db.fetchall("SELECT id, division, league, city FROM teams ORDER BY id")
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
