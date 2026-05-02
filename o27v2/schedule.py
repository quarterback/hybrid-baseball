"""
Schedule generation for O27v2.

Generates a perfectly balanced schedule for any even team count, with optional
division-weighted scheduling when team division info and a league config are provided.

Division-weighted algorithm:
  1. Compute intra-division games per pair from config's intra_division_weight.
  2. Compute inter-division games per pair from the remaining budget.
  3. Add full round-robins for intra pairs (all same-division pairs).
  4. Add full round-robins for inter pairs (all cross-division pairs).
  5. Distribute leftover games (from integer rounding) via perfect-matching rounds
     that prefer intra-division pairs first, then inter-division pairs.

Fallback (no division info):
  Uses uniform all-pairs round-robins + perfect-matching extras (original algorithm).

Config/team-count balance table:
  8  teams /  56 gpt → 8 uniform round-robins
  12 teams /  66 gpt → 6 uniform round-robins
  16 teams /  90 gpt → 6 uniform round-robins
  24 teams / 138 gpt → 6 uniform round-robins
  30 teams / 162 gpt → 5 RR + 17 extra matching rounds (with division weighting)
  36 teams / 162 gpt → 4 RR + 22 extra matching rounds (with division weighting)
"""
from __future__ import annotations
import random
import datetime
from collections import defaultdict


_SEASON_START = datetime.date(2026, 4, 1)


def _perfect_matching(ids: list[int], rng: random.Random) -> list[tuple[int, int]]:
    """Return a random perfect matching of `ids` (must have even length)."""
    shuffled = list(ids)
    rng.shuffle(shuffled)
    return [(shuffled[i], shuffled[i + 1]) for i in range(0, len(shuffled), 2)]


def _intra_div_matching(
    team_ids: list[int],
    div_map: dict[int, str],
    rng: random.Random,
) -> list[tuple[int, int]]:
    """
    Perfect matching that maximises intra-division pairs.
    Every team appears exactly once. Falls back to any available opponent
    when no same-division partner is available.
    """
    remaining = list(team_ids)
    rng.shuffle(remaining)
    pairs: list[tuple[int, int]] = []

    while len(remaining) >= 2:
        a = remaining.pop(0)
        # Prefer a same-division partner
        for idx, b in enumerate(remaining):
            if div_map.get(a) == div_map.get(b):
                pairs.append((a, b))
                remaining.pop(idx)
                break
        else:
            # No intra-div partner found; take first available
            pairs.append((a, remaining.pop(0)))

    return pairs


def _inter_div_matching(
    team_ids: list[int],
    div_map: dict[int, str],
    rng: random.Random,
) -> list[tuple[int, int]]:
    """
    Perfect matching that maximises inter-division pairs.
    Every team appears exactly once. Falls back to any available opponent
    when no cross-division partner is available.
    """
    remaining = list(team_ids)
    rng.shuffle(remaining)
    pairs: list[tuple[int, int]] = []

    while len(remaining) >= 2:
        a = remaining.pop(0)
        # Prefer a cross-division partner
        for idx, b in enumerate(remaining):
            if div_map.get(a) != div_map.get(b):
                pairs.append((a, b))
                remaining.pop(idx)
                break
        else:
            # No inter-div partner found; take first available
            pairs.append((a, remaining.pop(0)))

    return pairs


def generate_schedule(
    teams: list[dict],
    games_per_team: int = 30,
    season_days: int = 162,
    rng_seed: int = 42,
    config: dict | None = None,
) -> list[dict]:
    """
    Generate a balanced schedule where every team plays exactly `games_per_team` games.

    Args:
        teams:          List of team dicts; must have 'id'; optionally 'division'.
        games_per_team: Target games per team.
        season_days:    Calendar spread of the season in days.
        rng_seed:       RNG seed for reproducibility.
        config:         League config dict (may contain intra/inter division weights).

    Returns:
        List of game dicts: {season, game_date, home_team_id, away_team_id}
    """
    rng = random.Random(rng_seed)
    team_ids = [t["id"] for t in teams]
    n = len(team_ids)

    if n < 2:
        raise ValueError("Need at least 2 teams")
    if n % 2 != 0:
        raise ValueError("Number of teams must be even for balanced scheduling")

    # Check whether division-weighted scheduling is possible
    div_map = {t["id"]: t.get("division") for t in teams}
    has_div = all(v for v in div_map.values())
    intra_w = float((config or {}).get("intra_division_weight", 0.0)) if has_div else 0.0
    inter_w = float((config or {}).get("inter_division_weight", 0.0)) if has_div else 0.0
    use_div_weights = has_div and intra_w > 0.0 and inter_w > 0.0

    directed: list[tuple[int, int]] = []

    if use_div_weights:
        # ----------------------------------------------------------------
        # Division-weighted scheduling
        # ----------------------------------------------------------------
        div_teams: dict[str, list[int]] = defaultdict(list)
        for t in teams:
            div_teams[t["division"]].append(t["id"])

        n_divs = len(div_teams)
        n_d    = n // n_divs   # teams per division (assumes equal-sized divisions)

        # Partition pairs into intra / inter
        intra_pairs: list[tuple[int, int]] = []
        inter_pairs: list[tuple[int, int]] = []
        for i in range(n):
            for j in range(i + 1, n):
                a, b = team_ids[i], team_ids[j]
                if div_map[a] == div_map[b]:
                    intra_pairs.append((a, b))
                else:
                    inter_pairs.append((a, b))

        n_intra_per_team = n_d - 1        # intra-div opponents per team
        n_inter_per_team = n - n_d        # inter-div opponents per team

        # Desired games per team from each type (rounding to integers)
        intra_target = round(games_per_team * intra_w)
        inter_target = games_per_team - intra_target

        # Full rounds for each pair class
        intra_full  = intra_target // n_intra_per_team if n_intra_per_team > 0 else 0
        intra_extra = intra_target % n_intra_per_team  if n_intra_per_team > 0 else 0

        inter_full  = inter_target // n_inter_per_team if n_inter_per_team > 0 else 0
        inter_extra = inter_target % n_inter_per_team  if n_inter_per_team > 0 else 0

        # Add full intra round-robins
        for _ in range(intra_full):
            for a, b in intra_pairs:
                directed.append((a, b) if rng.random() < 0.5 else (b, a))

        # Add full inter round-robins
        for _ in range(inter_full):
            for a, b in inter_pairs:
                directed.append((a, b) if rng.random() < 0.5 else (b, a))

        # Extra games via biased perfect matchings.
        # Each matching gives every team exactly 1 more game.
        # We want intra_extra of those rounds to prefer intra-division pairs
        # and inter_extra rounds to prefer inter-division pairs.
        for rnd in range(intra_extra + inter_extra):
            if rnd < intra_extra:
                matching = _intra_div_matching(list(team_ids), div_map, rng)
            else:
                matching = _inter_div_matching(list(team_ids), div_map, rng)
            for a, b in matching:
                directed.append((a, b) if rng.random() < 0.5 else (b, a))

    else:
        # ----------------------------------------------------------------
        # Uniform scheduling (no division info or no weights configured)
        # ----------------------------------------------------------------
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

    # ----------------------------------------------------------------
    # Verify balance
    # ----------------------------------------------------------------
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

    # Shuffle and spread across season calendar
    rng.shuffle(directed)
    total = len(directed)
    games: list[dict] = []
    for idx, (home, away) in enumerate(directed):
        day_offset = int(idx * season_days / max(total, 1))
        game_date  = (_SEASON_START + datetime.timedelta(days=day_offset)).isoformat()
        games.append({
            "season": 1,
            "game_date": game_date,
            "home_team_id": home,
            "away_team_id": away,
        })
    return games


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
) -> int:
    """
    Insert schedule into the database.
    Reads games_per_team / season_days / division weights from the league config.
    Teams are fetched with their division info to enable division-weighted scheduling.
    Returns number of games inserted (0 if already seeded).
    """
    from o27v2 import db
    from o27v2.league import get_config

    existing = db.fetchone("SELECT COUNT(*) as n FROM games")
    if existing and existing["n"] > 0:
        return 0

    # Fetch teams WITH division info for division-weighted scheduling
    teams = db.fetchall("SELECT id, division FROM teams ORDER BY id")
    if not teams:
        raise RuntimeError("seed_league() must be called before seed_schedule()")

    cfg = get_config(config_id)
    if games_per_team is None:
        games_per_team = cfg["games_per_team"]
    if season_days is None:
        season_days = cfg["season_days"]

    games = generate_schedule(
        teams,
        games_per_team=games_per_team,
        season_days=season_days,
        rng_seed=rng_seed,
        config=cfg,
    )

    db.executemany(
        "INSERT INTO games (season, game_date, home_team_id, away_team_id) VALUES (?,?,?,?)",
        [(g["season"], g["game_date"], g["home_team_id"], g["away_team_id"]) for g in games],
    )
    return len(games)
