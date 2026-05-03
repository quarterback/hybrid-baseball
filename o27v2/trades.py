"""
Phase 9: Trade engine for O27v2.

Heuristic deadline trade engine:
  - Fires once at 2/3 of the season by calendar date.
  - Contenders (win_pct >= 0.55) buy; sellers (win_pct <= 0.40) sell.
  - Trade value scored by skill, age, role, and archetype.
  - v1: 1-for-1 or 2-for-2 trades only.
  - Target: 3-8 deadline trades per league per season.
  - Plus 0-2 minor in-season trades (random probability after game 30/team avg).

Waiver claims: handled in injuries.py (bullpen depth trigger).
"""
from __future__ import annotations
import random
import datetime

from o27v2 import db

# ---------------------------------------------------------------------------
# Trade value model
# ---------------------------------------------------------------------------

_AGE_PEAK_LO = 26
_AGE_PEAK_HI = 31


def trade_value(player: dict) -> float:
    """
    Score a player's trade value in [0, 1].
    Higher = more valuable to a contender.
    """
    age  = int(player.get("age", 27))
    role = player.get("pitcher_role", "")
    arch = player.get("archetype", "")

    # Skill component (DB stores 20-80 grades; convert to 0-1 units).
    from o27v2 import scout as _scout
    batting  = _scout.to_unit(player.get("skill", 50))
    pitching = _scout.to_unit(player.get("pitcher_skill", 50))
    speed    = _scout.to_unit(player.get("speed", 50))

    if role == "workhorse":
        skill_score = pitching * 0.65 + batting * 0.20 + speed * 0.15
    elif role == "committee":
        skill_score = pitching * 0.50 + batting * 0.30 + speed * 0.20
    else:
        skill_score = batting * 0.60 + speed * 0.25 + pitching * 0.15

    # Archetype bonus (impact players command higher value)
    arch_bonus = {"power": 0.06, "speed": 0.04, "contact": 0.05}.get(arch, 0.0)

    # Age factor: peak [26-31], gentle decline outside
    if _AGE_PEAK_LO <= age <= _AGE_PEAK_HI:
        age_factor = 0.0
    elif age < _AGE_PEAK_LO:
        age_factor = -0.010 * (_AGE_PEAK_LO - age)
    else:
        age_factor = -0.014 * (age - _AGE_PEAK_HI)

    value = skill_score + arch_bonus + age_factor
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Deadline detection
# ---------------------------------------------------------------------------

def _get_deadline_date() -> str:
    """Compute the trade deadline as 2/3 through the scheduled calendar."""
    row = db.fetchone("SELECT MIN(game_date) as mn, MAX(game_date) as mx FROM games")
    if not row or not row["mn"]:
        return "2099-01-01"
    mn = datetime.date.fromisoformat(row["mn"])
    mx = datetime.date.fromisoformat(row["mx"])
    total_days = (mx - mn).days
    dl = mn + datetime.timedelta(days=int(total_days * 2 / 3))
    return dl.isoformat()


def _deadline_trades_fired() -> bool:
    """Return True if deadline trades have already been logged this season."""
    row = db.fetchone(
        "SELECT COUNT(*) as n FROM transactions WHERE event_type = 'deadline_trade'"
    )
    return bool(row and row["n"] > 0)


def _inseason_trade_count() -> int:
    row = db.fetchone(
        "SELECT COUNT(*) as n FROM transactions WHERE event_type = 'inseason_trade'"
    )
    return row["n"] if row else 0


# ---------------------------------------------------------------------------
# Team evaluation
# ---------------------------------------------------------------------------

def _get_team_standings() -> list[dict]:
    teams = db.fetchall(
        "SELECT t.*, "
        "CAST(t.wins AS REAL) / MAX(t.wins + t.losses, 1) AS win_pct "
        "FROM teams t ORDER BY win_pct DESC"
    )
    return teams


def _classify_teams(standings: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Identify contenders and sellers using relative standings.
    Contenders = top 30% (minimum 2), sellers = bottom 30% (minimum 2).
    Also apply absolute win-pct floor/ceiling to exclude mediocre contenders.
    """
    n = len(standings)
    top_n    = max(2, n * 30 // 100)
    bottom_n = max(2, n * 30 // 100)

    # Sort descending by win_pct (already sorted)
    contenders = standings[:top_n]
    sellers    = standings[n - bottom_n:]

    # Filter: contenders must have played enough and be above .500
    total_games = contenders[0]["wins"] + contenders[0]["losses"] if contenders else 0
    if total_games < 20:
        return [], []   # too early to trade

    contenders = [t for t in contenders if t["win_pct"] >= 0.50]
    sellers    = [t for t in sellers    if t["win_pct"] <= 0.50]

    return contenders, sellers


def _get_tradeable_players(team_id: int, game_date: str) -> list[dict]:
    """Return healthy, non-joker players sorted by trade value descending."""
    players = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? "
        "AND (injured_until IS NULL OR injured_until <= ?)",
        (team_id, game_date),
    )
    return sorted(players, key=trade_value, reverse=True)


# ---------------------------------------------------------------------------
# Roster validation helpers
# ---------------------------------------------------------------------------

def _has_workhorse(team_id: int, game_date: str) -> bool:
    row = db.fetchone(
        "SELECT COUNT(*) as n FROM players "
        "WHERE team_id = ? AND pitcher_role = 'workhorse' "
        "AND (injured_until IS NULL OR injured_until <= ?)",
        (team_id, game_date),
    )
    return bool(row and row["n"] > 0)


def _committee_count(team_id: int, game_date: str) -> int:
    row = db.fetchone(
        "SELECT COUNT(*) as n FROM players "
        "WHERE team_id = ? AND pitcher_role = 'committee' "
        "AND (injured_until IS NULL OR injured_until <= ?)",
        (team_id, game_date),
    )
    return row["n"] if row else 0


def _can_trade_player(player: dict, from_team_id: int, game_date: str) -> bool:
    """
    Guard rails: don't allow a trade if it would strip a team of their
    only workhorse pitcher or leave fewer than 2 committee pitchers.
    """
    role = player.get("pitcher_role", "")
    if role == "workhorse" and not _has_alternative_workhorse(
        from_team_id, player["id"], game_date
    ):
        return False
    if role == "committee" and _committee_count(from_team_id, game_date) <= 2:
        return False
    return True


def _has_alternative_workhorse(team_id: int, exclude_player_id: int, game_date: str) -> bool:
    row = db.fetchone(
        "SELECT COUNT(*) as n FROM players "
        "WHERE team_id = ? AND pitcher_role IN ('workhorse','committee') "
        "AND id != ? AND (injured_until IS NULL OR injured_until <= ?)",
        (team_id, exclude_player_id, game_date),
    )
    return bool(row and row["n"] >= 2)


# ---------------------------------------------------------------------------
# Execute a trade
# ---------------------------------------------------------------------------

def _do_trade(
    send_players: list[dict],
    recv_players: list[dict],
    from_team: dict,
    to_team: dict,
    game_date: str,
    event_type: str,
) -> list[dict]:
    """
    Move players between teams and return transaction entries.
    send_players go from from_team → to_team.
    recv_players go from to_team → from_team.
    """
    events: list[dict] = []
    with db.get_conn() as conn:
        for p in send_players:
            conn.execute("UPDATE players SET team_id = ? WHERE id = ?",
                         (to_team["id"], p["id"]))
            events.append({
                "event_type": event_type,
                "team_id": from_team["id"],
                "player_id": p["id"],
                "detail": (
                    f"TRADE: {from_team['name']} sends {p['name']} ({p['position']}) "
                    f"to {to_team['name']}"
                ),
            })
        for p in recv_players:
            conn.execute("UPDATE players SET team_id = ? WHERE id = ?",
                         (from_team["id"], p["id"]))
            events.append({
                "event_type": event_type,
                "team_id": to_team["id"],
                "player_id": p["id"],
                "detail": (
                    f"TRADE: {to_team['name']} sends {p['name']} ({p['position']}) "
                    f"to {from_team['name']}"
                ),
            })
        conn.commit()
    return events


# ---------------------------------------------------------------------------
# Deadline trade engine
# ---------------------------------------------------------------------------

def run_deadline_trades(game_date: str, rng_seed: int = 42) -> list[dict]:
    """
    Fire the trade deadline: contenders buy from sellers.
    Returns list of transaction event dicts.
    Target: 3-8 trades per league per season.
    """
    if _deadline_trades_fired():
        return []

    rng = random.Random(rng_seed + hash(game_date) & 0x7FFFFFFF)
    standings = _get_team_standings()
    contenders, sellers = _classify_teams(standings)

    if not contenders or not sellers:
        return []

    # Randomise matching order
    rng.shuffle(contenders)
    rng.shuffle(sellers)

    # Target 3-8 trades; scale with number of contender/seller pairs
    max_pairs   = min(len(contenders), len(sellers))
    trade_count = rng.randint(3, min(8, max(3, max_pairs)))
    events: list[dict] = []

    used_sellers: set[int] = set()

    for contender in contenders:
        if len([e for e in events if e["event_type"] == "deadline_trade"]) >= trade_count:
            break

        # Find a seller not yet involved in a trade
        seller = None
        for s in sellers:
            if s["id"] not in used_sellers and s["id"] != contender["id"]:
                seller = s
                break
        if not seller:
            break

        # Decide 1-for-1 or 2-for-2
        trade_size = 2 if rng.random() < 0.30 else 1

        sell_pool = [
            p for p in _get_tradeable_players(seller["id"], game_date)
            if _can_trade_player(p, seller["id"], game_date)
        ]
        buy_pool = [
            p for p in _get_tradeable_players(contender["id"], game_date)
            if _can_trade_player(p, contender["id"], game_date)
        ]

        if len(sell_pool) < trade_size or len(buy_pool) < trade_size:
            continue   # not enough eligible players

        # Seller sends top-N; contender sends mid-tier players back
        send = sell_pool[:trade_size]
        # Contender gives back players ranked around the 40th percentile (fair return)
        mid = max(0, len(buy_pool) // 2 - 1)
        recv = buy_pool[mid: mid + trade_size]
        if len(recv) < trade_size:
            recv = buy_pool[-trade_size:]
        if len(recv) < trade_size:
            continue

        new_evts = _do_trade(send, recv, seller, contender, game_date, "deadline_trade")
        events.extend(new_evts)
        used_sellers.add(seller["id"])

    return events


# ---------------------------------------------------------------------------
# In-season minor trade engine
# ---------------------------------------------------------------------------

def maybe_inseason_trade(game_date: str, games_played: int, rng_seed: int = 0) -> list[dict]:
    """
    Fire a random in-season trade with small probability.
    At most 2 in-season trades total per season.
    Only fires if deadline hasn't passed.
    """
    deadline = _get_deadline_date()
    if game_date >= deadline:
        return []
    if _inseason_trade_count() >= 2:
        return []
    if games_played < 20:       # need some games on the board first
        return []

    rng = random.Random(rng_seed + hash(game_date) & 0x7FFFFFFF)
    if rng.random() > 0.004:    # ~0.4% per game → ~0-2 per 162-game season
        return []

    standings = _get_team_standings()
    contenders, sellers = _classify_teams(standings)

    if not contenders or not sellers:
        return []

    mild_sellers    = sellers
    mild_contenders = contenders

    rng.shuffle(mild_sellers)
    rng.shuffle(mild_contenders)
    seller    = mild_sellers[0]
    contender = mild_contenders[0]
    if seller["id"] == contender["id"]:
        return []

    sell_pool = [
        p for p in _get_tradeable_players(seller["id"], game_date)
        if _can_trade_player(p, seller["id"], game_date)
    ]
    buy_pool = [
        p for p in _get_tradeable_players(contender["id"], game_date)
        if _can_trade_player(p, contender["id"], game_date)
    ]
    if not sell_pool or not buy_pool:
        return []

    send = [sell_pool[0]]
    mid  = max(0, len(buy_pool) // 2)
    recv = [buy_pool[mid]]

    return _do_trade(send, recv, seller, contender, game_date, "inseason_trade")


# ---------------------------------------------------------------------------
# Combined post-game check
# ---------------------------------------------------------------------------

def check_deadline_and_trades(game_date: str, games_played: int) -> list[dict]:
    """
    Called after every game. Fires deadline trades when the date threshold is
    crossed and tries occasional in-season trades before the deadline.
    """
    deadline = _get_deadline_date()
    events: list[dict] = []

    if game_date >= deadline:
        events.extend(run_deadline_trades(game_date, rng_seed=games_played))
    else:
        events.extend(maybe_inseason_trade(game_date, games_played, rng_seed=games_played))

    return events
