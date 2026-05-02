"""
Schedule generation for O27v2.

Generates a perfectly balanced 30-game regular season per team.

Algorithm guarantees exactly `games_per_team` games for every team:
  - With 30 teams and 30 games each, total games = 450.
  - All 435 unique pairs play once (29 games each).
  - A random perfect-matching of 15 pairs is selected to play a second game
    (home/away swapped), bringing every team to exactly 30.
  - Home/away assignment is randomised for single-play pairs.
"""
from __future__ import annotations
import random
import datetime


_SEASON_START = datetime.date(2026, 4, 1)


def _perfect_matching(ids: list[int], rng: random.Random) -> list[tuple[int, int]]:
    """Return a random perfect matching of `ids` (must have even length)."""
    shuffled = list(ids)
    rng.shuffle(shuffled)
    return [(shuffled[i], shuffled[i + 1]) for i in range(0, len(shuffled), 2)]


def generate_schedule(teams: list[dict], games_per_team: int = 30, rng_seed: int = 42) -> list[dict]:
    """
    Generate a balanced schedule where every team plays exactly `games_per_team` games.

    With 30 teams and games_per_team=30:
      - 435 unique pairs each play once  → 29 games/team
      - 15 pairs from a perfect matching play a second time → +1 each → 30 games/team
      - Total: 450 directed games (each pair appears as home once, away once)

    Returns a list of game dicts:
      {home_team_id, away_team_id, game_date, season}
    """
    rng = random.Random(rng_seed)
    team_ids = [t["id"] for t in teams]
    n = len(team_ids)

    if n % 2 != 0:
        raise ValueError("Number of teams must be even for balanced scheduling")

    # Step 1: All unique pairs play once (home/away randomly assigned)
    directed: list[tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.5:
                directed.append((team_ids[i], team_ids[j]))
            else:
                directed.append((team_ids[j], team_ids[i]))

    # Step 2: Perfect matching of teams provides 15 pairs that play a second game
    #         with home/away reversed so each side gets one home and one away.
    matching = _perfect_matching(list(team_ids), rng)
    for a, b in matching:
        # Find existing direction for (a,b) or (b,a) and add the reverse.
        if (a, b) in directed or (b, a) in directed:
            # Use the one that keeps home/away balanced
            directed.append((b, a))
        else:
            directed.append((a, b))

    # Shuffle for random game order
    rng.shuffle(directed)

    total = len(directed)
    games: list[dict] = []
    for idx, (home, away) in enumerate(directed):
        day_offset = int(idx * 162 / max(total, 1))
        game_date = (_SEASON_START + datetime.timedelta(days=day_offset)).isoformat()
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


def seed_schedule(rng_seed: int = 42) -> int:
    """
    Insert schedule into the database.
    Returns number of games inserted (0 if already seeded).
    """
    from o27v2 import db
    existing = db.fetchone("SELECT COUNT(*) as n FROM games")
    if existing and existing["n"] > 0:
        return 0

    teams = db.fetchall("SELECT id FROM teams ORDER BY id")
    if not teams:
        raise RuntimeError("seed_league() must be called before seed_schedule()")

    games = generate_schedule(teams, rng_seed=rng_seed)

    # Verify balance before inserting
    counts = verify_balance(games, [t["id"] for t in teams])
    bad = [tid for tid, c in counts.items() if c != 30]
    if bad:
        raise RuntimeError(f"Schedule imbalance detected for team IDs: {bad}")

    db.executemany(
        "INSERT INTO games (season, game_date, home_team_id, away_team_id) VALUES (?,?,?,?)",
        [(g["season"], g["game_date"], g["home_team_id"], g["away_team_id"]) for g in games],
    )
    return len(games)
