"""
Motivation-driven trade engine for O27v2.

Trades are the league's main player-distribution mechanic. Every post-game
date, every team rolls against an initiation probability gated by season
phase and its front-office (FO) aggression. Teams that initiate then score
nine motivations and pick one to act on:

  block_breaking      A reserve is on par with the active starter at his
                      position; trade the reserve away for value.
  injury_backfill     A long-IL hole at a critical position; chase a
                      healthy player from a team with surplus depth.
  deadline_buyer      Contender buying at the 2/3-season mark.
  deadline_seller     Sub-.500 team selling at the 2/3-season mark.
  salary_dump         High-payroll team offloads expensive talent.
  rebuild_fire_sale   Rebuilders trade vets for younger players regardless
                      of immediate skill match.
  win_now_overpay     Win-now teams pay over market for stars.
  gm_noise            Pure irrationality — small flat baseline for every
                      team that occasionally produces clearly lopsided
                      deals. Relaxed acceptance threshold.
  star_demand         After long losing streaks, a team's best player
                      forces a move to a contender.

Per-team FO persona (o27v2/front_office.py) modulates motivation weights
and acceptance thresholds. Volume target: ~20-40 trades per team per
162-game season; ~60% of volume concentrated in the late-season window
before the deadline.

PUBLIC API (preserved for back-compat — see valuation.py, sim.py):
  trade_value(player: dict) -> float
  check_deadline_and_trades(game_date, games_played) -> list[dict]
"""
from __future__ import annotations

import datetime
import random
import statistics
from typing import Optional

from o27v2 import db


# ---------------------------------------------------------------------------
# Trade value model
#
# PRESERVED BYTE-FOR-BYTE from the prior implementation. valuation.py:_BANDS
# is calibrated to its exact 0..1 output range; any drift here re-tiers
# every salary in the league.
# ---------------------------------------------------------------------------

_AGE_PEAK_LO = 26
_AGE_PEAK_HI = 31


def trade_value(player: dict) -> float:
    """Score a player's trade value in [0, 1]. Higher = more valuable."""
    age  = int(player.get("age", 27))
    role = player.get("pitcher_role", "")
    arch = player.get("archetype", "")

    from o27v2 import scout as _scout
    from o27v2 import rotation as _rotation
    batting  = _scout.to_unit(player.get("skill", 50))
    pitching = _scout.to_unit(player.get("pitcher_skill", 50))
    speed    = _scout.to_unit(player.get("speed", 50))

    # A steering arm (the Helms tier — or the legacy "workhorse") is valued
    # as a front-line pitcher; the rest of the crew (or a legacy "committee"
    # arm) as a swing/relief pitcher; everyone else as a bat. Weights are
    # unchanged from the pre-crew model so valuation._BANDS stays calibrated.
    if role == "workhorse" or _rotation.is_steer_role(role):
        skill_score = pitching * 0.65 + batting * 0.20 + speed * 0.15
    elif role == "committee" or (role and role in _rotation.RELIEF_ROLES):
        skill_score = pitching * 0.50 + batting * 0.30 + speed * 0.20
    else:
        skill_score = batting * 0.60 + speed * 0.25 + pitching * 0.15

    arch_bonus = {"power": 0.06, "speed": 0.04, "contact": 0.05}.get(arch, 0.0)

    if _AGE_PEAK_LO <= age <= _AGE_PEAK_HI:
        age_factor = 0.0
    elif age < _AGE_PEAK_LO:
        age_factor = -0.010 * (_AGE_PEAK_LO - age)
    else:
        age_factor = -0.014 * (age - _AGE_PEAK_HI)

    value = skill_score + arch_bonus + age_factor
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MOTIVATIONS = (
    "block_breaking",
    "injury_backfill",
    "deadline_buyer",
    "deadline_seller",
    "salary_dump",
    "rebuild_fire_sale",
    "win_now_overpay",
    "gm_noise",
    "star_demand",
)

# Per-phase per-team initiation probability. ~55% of the season's trade
# volume concentrates in the 'late' window (~last ~2 weeks before deadline).
# Tuned for high-activity target: ~20-40 trades per team per 162-game season.
BASE_INITIATE_PROB: dict[str, float] = {
    "early":         0.06,
    "middle":        0.10,
    "late":          0.28,
    "post_deadline": 0.01,
}

# Strategy x motivation multipliers. 1.0 = neutral; >1 amplifies; <1 suppresses.
# Designed so each strategy has a distinct "voice".
STRATEGY_MULT: dict[str, dict[str, float]] = {
    "win_now": {
        "win_now_overpay":   2.5,
        "deadline_buyer":    1.6,
        "injury_backfill":   1.4,
        "block_breaking":    0.6,
        "rebuild_fire_sale": 0.0,
        "deadline_seller":   0.2,
        "salary_dump":       0.3,
        "gm_noise":          1.0,
        "star_demand":       0.6,
    },
    "contend": {
        "deadline_buyer":    1.8,
        "injury_backfill":   1.3,
        "block_breaking":    1.0,
        "win_now_overpay":   0.8,
        "rebuild_fire_sale": 0.0,
        "deadline_seller":   0.2,
        "salary_dump":       0.6,
        "gm_noise":          1.0,
        "star_demand":       0.6,
    },
    "balanced": {
        "block_breaking":    1.2,
        "injury_backfill":   1.0,
        "deadline_buyer":    0.9,
        "deadline_seller":   0.9,
        "salary_dump":       1.0,
        "rebuild_fire_sale": 0.3,
        "win_now_overpay":   0.5,
        "gm_noise":          1.0,
        "star_demand":       1.0,
    },
    "develop": {
        "block_breaking":    1.5,
        "deadline_seller":   1.2,
        "salary_dump":       1.1,
        "rebuild_fire_sale": 0.5,
        "win_now_overpay":   0.2,
        "deadline_buyer":    0.3,
        "injury_backfill":   0.7,
        "gm_noise":          1.0,
        "star_demand":       1.2,
    },
    "rebuild": {
        "rebuild_fire_sale": 2.5,
        "deadline_seller":   1.8,
        "salary_dump":       1.6,
        "block_breaking":    1.0,
        "deadline_buyer":    0.0,
        "win_now_overpay":   0.0,
        "injury_backfill":   0.4,
        "gm_noise":          1.0,
        "star_demand":       1.4,
    },
}

# Acceptance threshold per partner FO strategy. Higher = pickier.
ACCEPTANCE_THRESHOLD: dict[str, float] = {
    "rebuild":  0.85,
    "win_now":  0.80,
    "develop":  0.90,
    "contend":  0.92,
    "balanced": 0.95,
}

MOTIVATION_FLOOR = 0.10

# Canonical position groups for floor checks.
_CANONICAL_HITTER_POSITIONS = ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF")
_MIN_HEALTHY_PITCHERS = 5


# ---------------------------------------------------------------------------
# Season phase + helpers
# ---------------------------------------------------------------------------

def _get_deadline_date() -> str:
    """2/3 of the scheduled calendar — matches the legacy deadline."""
    row = db.fetchone("SELECT MIN(game_date) AS mn, MAX(game_date) AS mx FROM games")
    if not row or not row["mn"]:
        return "2099-01-01"
    mn = datetime.date.fromisoformat(row["mn"])
    mx = datetime.date.fromisoformat(row["mx"])
    total_days = (mx - mn).days
    dl = mn + datetime.timedelta(days=int(total_days * 2 / 3))
    return dl.isoformat()


def _season_window() -> tuple[str, str, str]:
    """Return (season_start, deadline, season_end) as ISO date strings."""
    row = db.fetchone("SELECT MIN(game_date) AS mn, MAX(game_date) AS mx FROM games")
    if not row or not row["mn"]:
        return ("2099-01-01", "2099-01-01", "2099-01-01")
    return (row["mn"], _get_deadline_date(), row["mx"])


def _season_phase(game_date: str) -> str:
    start, deadline, end = _season_window()
    try:
        gd = datetime.date.fromisoformat(game_date)
        s  = datetime.date.fromisoformat(start)
        dl = datetime.date.fromisoformat(deadline)
        e  = datetime.date.fromisoformat(end)
    except ValueError:
        return "early"
    if gd > dl:
        return "post_deadline"
    total = max(1, (dl - s).days)
    elapsed = (gd - s).days
    frac = elapsed / total
    # 'late' = final ~25% of pre-deadline window (where deadline volume lives).
    if frac >= 0.75:
        return "late"
    if frac >= 0.40:
        return "middle"
    return "early"


def _load_teams_with_fo() -> list[dict]:
    """All teams with FO + record fields, plus a computed losing streak."""
    teams = db.fetchall(
        "SELECT id, name, abbrev, league, wins, losses, "
        "       COALESCE(fo_strategy, 'balanced')        AS fo_strategy, "
        "       COALESCE(fo_aggression, 0.5)             AS fo_aggression, "
        "       COALESCE(fo_archetype_bias, '')          AS fo_archetype_bias, "
        "       COALESCE(fo_last_trade_date, '')         AS fo_last_trade_date "
        "FROM teams"
    )
    for t in teams:
        t["wins"]    = t["wins"]    or 0
        t["losses"]  = t["losses"]  or 0
        g = t["wins"] + t["losses"]
        t["win_pct"] = (t["wins"] / g) if g else 0.5
        t["losing_streak"] = _losing_streak(t["id"])
    return teams


def _losing_streak(team_id: int) -> int:
    """Consecutive losses at the head of the played-games list for this team."""
    rows = db.fetchall(
        "SELECT winner_id, home_team_id, away_team_id "
        "FROM games WHERE played = 1 AND (home_team_id = ? OR away_team_id = ?) "
        "ORDER BY game_date DESC, id DESC LIMIT 30",
        (team_id, team_id),
    )
    streak = 0
    for r in rows:
        if r["winner_id"] == team_id:
            break
        streak += 1
    return streak


# ---------------------------------------------------------------------------
# Tradeable player pool
# ---------------------------------------------------------------------------

def _get_tradeable_players(team_id: int, game_date: str) -> list[dict]:
    """Healthy, non-joker players on this team, sorted by trade_value desc."""
    players = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? "
        "AND COALESCE(is_joker, 0) = 0 "
        "AND (injured_until IS NULL OR injured_until <= ?)",
        (team_id, game_date),
    )
    return sorted(players, key=trade_value, reverse=True)


def _healthy_active_at_position(team_id: int, position: str, game_date: str) -> list[dict]:
    return db.fetchall(
        "SELECT * FROM players WHERE team_id = ? AND position = ? "
        "AND COALESCE(is_active, 1) = 1 "
        "AND COALESCE(is_joker, 0) = 0 "
        "AND (injured_until IS NULL OR injured_until <= ?)",
        (team_id, position, game_date),
    )


def _healthy_reserves_at_position(team_id: int, position: str, game_date: str) -> list[dict]:
    return db.fetchall(
        "SELECT * FROM players WHERE team_id = ? AND position = ? "
        "AND COALESCE(is_active, 1) = 0 "
        "AND COALESCE(is_joker, 0) = 0 "
        "AND (injured_until IS NULL OR injured_until <= ?)",
        (team_id, position, game_date),
    )


# ---------------------------------------------------------------------------
# Motivation scoring
# ---------------------------------------------------------------------------

def _score_block_breaking(team: dict, roster: list[dict], game_date: str) -> tuple[float, dict]:
    """High-value reserve sitting behind a same-position active starter."""
    best_score = 0.0
    ctx: dict = {}
    by_pos: dict[str, list[dict]] = {}
    for p in roster:
        by_pos.setdefault(p["position"], []).append(p)
    for pos, players in by_pos.items():
        actives  = [p for p in players if p.get("is_active", 1) == 1]
        reserves = [p for p in players if p.get("is_active", 1) == 0]
        if not actives or not reserves:
            continue
        starter = max(actives, key=trade_value)
        reserve = max(reserves, key=trade_value)
        diff = trade_value(reserve) - trade_value(starter)
        # Reserve within 0.10 of starter (or above) is "blocked".
        if diff >= -0.10:
            # Score scales with proximity + absolute value of the reserve.
            score = (0.10 + diff) * 2.0 + trade_value(reserve) * 0.5
            if score > best_score:
                best_score = score
                ctx = {"blocked_reserve_id": reserve["id"],
                       "blocking_starter_id": starter["id"],
                       "position": pos}
    return min(1.0, best_score), ctx


def _score_injury_backfill(team: dict, roster: list[dict], game_date: str) -> tuple[float, dict]:
    """Long-IL hole at C/SS/CF/P (critical positions)."""
    rows = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? "
        "AND il_tier IN ('short', 'long') "
        "AND injured_until > ? "
        "AND COALESCE(is_joker, 0) = 0",
        (team["id"], game_date),
    )
    critical = {"C", "SS", "CF", "P"}
    score = 0.0
    target_pos: Optional[str] = None
    target_player_id: Optional[int] = None
    for r in rows:
        try:
            days_out = (datetime.date.fromisoformat(r["injured_until"])
                        - datetime.date.fromisoformat(game_date)).days
        except (TypeError, ValueError):
            continue
        if days_out < 14:
            continue
        urgency = min(1.0, days_out / 60.0)
        is_crit = 1.0 if r["position"] in critical else 0.5
        # Worse hole if no healthy active at the same position.
        backups = _healthy_active_at_position(team["id"], r["position"], game_date)
        backup_factor = 1.0 if not backups else 0.5
        s = urgency * is_crit * backup_factor
        if s > score:
            score = s
            target_pos = r["position"]
            target_player_id = r["id"]
    return min(1.0, score * 1.4), {"target_position": target_pos,
                                    "injured_player_id": target_player_id}


def _score_deadline_buyer(team: dict, phase: str) -> tuple[float, dict]:
    if phase not in ("middle", "late"):
        return 0.0, {}
    if team["win_pct"] < 0.50:
        return 0.0, {}
    # Score = how far above .500, plus phase boost.
    excess = team["win_pct"] - 0.50
    phase_boost = {"middle": 0.3, "late": 1.0}[phase]
    return min(1.0, (excess * 3.0 + 0.2) * phase_boost), {}


def _score_deadline_seller(team: dict, phase: str) -> tuple[float, dict]:
    if phase not in ("middle", "late"):
        return 0.0, {}
    if team["win_pct"] >= 0.50:
        return 0.0, {}
    deficit = 0.50 - team["win_pct"]
    phase_boost = {"middle": 0.3, "late": 1.0}[phase]
    return min(1.0, (deficit * 3.0 + 0.2) * phase_boost), {}


def _score_salary_dump(team: dict, payrolls: dict[int, int], median_pay: int) -> tuple[float, dict]:
    if median_pay <= 0:
        return 0.0, {}
    p = payrolls.get(team["id"], 0)
    ratio = p / median_pay
    if ratio < 1.15:
        return 0.0, {}
    return min(1.0, (ratio - 1.15) * 1.5), {}


def _score_rebuild_fire_sale(team: dict, roster: list[dict]) -> tuple[float, dict]:
    if team["fo_strategy"] != "rebuild":
        return 0.0, {}
    if not roster:
        return 0.0, {}
    vets = [p for p in roster if (p.get("age") or 27) >= 30]
    frac = len(vets) / len(roster)
    if frac < 0.30:
        return 0.0, {}
    return min(1.0, (frac - 0.30) * 2.0 + 0.3), {}


def _score_win_now_overpay(team: dict) -> tuple[float, dict]:
    if team["fo_strategy"] != "win_now":
        return 0.0, {}
    if team["fo_aggression"] < 0.5:
        return 0.0, {}
    return 0.4 + (team["fo_aggression"] - 0.5) * 1.0, {}


def _score_gm_noise(team: dict) -> tuple[float, dict]:
    # Flat baseline keeps a long tail of irrational moves — just above the
    # motivation floor so a team with no other compelling motivation can
    # still occasionally produce a clearly lopsided deal.
    return 0.11, {}


def _score_star_demand(team: dict, roster: list[dict], games_played: int) -> tuple[float, dict]:
    streak = team.get("losing_streak", 0)
    wp_bad = (games_played > 60 and team["win_pct"] < 0.30)
    if streak < 7 and not wp_bad:
        return 0.0, {}
    if not roster:
        return 0.0, {}
    star = max(roster, key=trade_value)
    if trade_value(star) < 0.55:
        return 0.0, {}
    base = 0.4 if streak >= 7 else 0.3
    return min(1.0, base + 0.05 * max(0, streak - 7)), {"star_id": star["id"]}


# ---------------------------------------------------------------------------
# Counterparty matching
# ---------------------------------------------------------------------------

def _candidate_partners(
    initiator: dict,
    motivation: str,
    ctx: dict,
    all_teams: list[dict],
    payrolls: dict[int, int],
    median_pay: int,
    game_date: str,
) -> list[dict]:
    others = [t for t in all_teams if t["id"] != initiator["id"]]
    if motivation == "block_breaking":
        pos = ctx.get("position")
        if not pos:
            return []
        # Partners with weaker starter at that position.
        results = []
        for t in others:
            starters = _healthy_active_at_position(t["id"], pos, game_date)
            if not starters:
                results.append(t)
                continue
            best_starter = max(starters, key=trade_value)
            # Initiator's reserve must be >= this starter's value by some margin.
            results.append((t, trade_value(best_starter)))
        return [r if isinstance(r, dict) else r[0] for r in results]

    if motivation == "injury_backfill":
        pos = ctx.get("target_position")
        if not pos:
            return []
        results = []
        for t in others:
            depth = _healthy_active_at_position(t["id"], pos, game_date)
            depth += _healthy_reserves_at_position(t["id"], pos, game_date)
            if len(depth) >= 2:
                results.append(t)
        # Prefer rebuild/develop counterparties (more willing to move regulars).
        results.sort(key=lambda t: 0 if t["fo_strategy"] in ("rebuild", "develop") else 1)
        return results

    if motivation == "deadline_buyer":
        return [t for t in others if t["win_pct"] < 0.50]

    if motivation == "deadline_seller":
        return [t for t in others if t["win_pct"] >= 0.50]

    if motivation == "salary_dump":
        if median_pay <= 0:
            return []
        return [t for t in others
                if payrolls.get(t["id"], 0) < median_pay * 0.85
                and t["fo_aggression"] > 0.5]

    if motivation == "rebuild_fire_sale":
        return [t for t in others if t["fo_strategy"] in ("contend", "win_now")]

    if motivation == "win_now_overpay":
        return [t for t in others if t["fo_strategy"] in ("rebuild", "develop")]

    if motivation == "gm_noise":
        return others

    if motivation == "star_demand":
        ranked = sorted(others, key=lambda t: t["win_pct"], reverse=True)
        return ranked[:3]

    return []


# ---------------------------------------------------------------------------
# Offer construction
# ---------------------------------------------------------------------------

def _build_offer(
    initiator: dict,
    partner: dict,
    motivation: str,
    ctx: dict,
    game_date: str,
    rng: random.Random,
) -> tuple[list[dict], list[dict]]:
    """Return (send_from_initiator, recv_from_partner)."""
    init_pool    = _get_tradeable_players(initiator["id"], game_date)
    partner_pool = _get_tradeable_players(partner["id"], game_date)
    if not init_pool or not partner_pool:
        return [], []

    if motivation == "block_breaking":
        reserve_id = ctx.get("blocked_reserve_id")
        send = [p for p in init_pool if p["id"] == reserve_id][:1]
        if not send:
            return [], []
        # Recv a comparable mid-tier from partner.
        send_v = trade_value(send[0])
        recv = _pick_by_value(partner_pool, send_v, rng, tolerance=0.20)
        return send, recv

    if motivation == "injury_backfill":
        pos = ctx.get("target_position")
        # Partner sends a healthy starter at pos; we send mid-tier value back.
        partner_at_pos = [p for p in partner_pool if p["position"] == pos]
        if not partner_at_pos:
            return [], []
        # Pick the lowest-value starter the partner is willing to part with.
        target = partner_at_pos[len(partner_at_pos) // 2]
        recv = [target]
        send = _pick_by_value(init_pool, trade_value(target) * 0.95, rng, tolerance=0.20)
        return send, recv

    if motivation == "deadline_buyer":
        # Initiator sends 2 mid-tier; receives 1 top-tier from partner.
        mid = len(init_pool) // 2
        send = init_pool[mid: mid + 2]
        recv = partner_pool[:1]
        return send, recv

    if motivation == "deadline_seller":
        # Initiator sends 1 top-tier; receives 2 mid-tier from partner.
        send = init_pool[:1]
        mid = len(partner_pool) // 2
        recv = partner_pool[mid: mid + 2]
        return send, recv

    if motivation == "salary_dump":
        # Send a high-salary player; receive a cheaper one of any tier.
        top_paid = max(init_pool, key=lambda p: int(p.get("salary") or 0))
        send = [top_paid]
        # Recv: low-value cheap from partner.
        partner_cheap = sorted(partner_pool, key=lambda p: int(p.get("salary") or 0))
        recv = partner_cheap[:1]
        return send, recv

    if motivation == "rebuild_fire_sale":
        vets = [p for p in init_pool if (p.get("age") or 27) >= 30]
        if not vets:
            return [], []
        vets.sort(key=trade_value, reverse=True)
        send = vets[:1]
        young = [p for p in partner_pool if (p.get("age") or 27) <= 24]
        young.sort(key=trade_value, reverse=True)
        n_recv = rng.choice([2, 2, 3])
        recv = young[:n_recv]
        if not recv:
            return [], []
        return send, recv

    if motivation == "win_now_overpay":
        # Initiator sends 3 youngish; receives 1 star.
        recv = partner_pool[:1]
        if not recv:
            return [], []
        young = [p for p in init_pool if (p.get("age") or 27) <= 26]
        young.sort(key=trade_value, reverse=True)
        send = young[:3] if len(young) >= 3 else init_pool[:3]
        return send, recv

    if motivation == "gm_noise":
        # Random pick, ignoring fair-value math.
        send = [rng.choice(init_pool)]
        recv = [rng.choice(partner_pool)]
        if send[0]["id"] == recv[0]["id"]:
            return [], []
        return send, recv

    if motivation == "star_demand":
        star_id = ctx.get("star_id")
        send = [p for p in init_pool if p["id"] == star_id][:1]
        if not send:
            return [], []
        mid = len(partner_pool) // 2
        recv = partner_pool[mid: mid + 2]
        return send, recv

    return [], []


def _pick_by_value(
    pool: list[dict],
    target_value: float,
    rng: random.Random,
    tolerance: float = 0.15,
) -> list[dict]:
    """Pick one player from `pool` whose trade_value is within ±tolerance of target."""
    candidates = [p for p in pool if abs(trade_value(p) - target_value) <= tolerance]
    if not candidates:
        # Closest match within the pool.
        if not pool:
            return []
        candidates = [min(pool, key=lambda p: abs(trade_value(p) - target_value))]
    return [rng.choice(candidates)]


# ---------------------------------------------------------------------------
# Acceptance + validation
# ---------------------------------------------------------------------------

def _evaluate_offer(
    partner: dict,
    send: list[dict],
    recv: list[dict],
    motivation: str,
    rng: random.Random,
) -> bool:
    """Partner perspective: does partner accept getting `send` in exchange for
    giving up `recv`? Returns True on accept."""
    if not send or not recv:
        return False

    incoming = sum(trade_value(p) for p in send)
    outgoing = sum(trade_value(p) for p in recv)

    # Archetype bias bonus on incoming.
    bias = partner.get("fo_archetype_bias", "")
    if bias and incoming > 0:
        bias_hits = sum(1 for p in send if p.get("archetype") == bias)
        if bias_hits:
            incoming *= 1.0 + 0.15 * (bias_hits / max(1, len(send)))

    # Rebuilder age-discount: prefers youth in incoming relative to outgoing.
    strategy = partner["fo_strategy"]
    if strategy == "rebuild":
        avg_in  = _avg_age(send)
        avg_out = _avg_age(recv)
        delta = (avg_out - avg_in) / 10.0   # positive when getting younger
        incoming *= 1.0 + max(-0.30, min(0.30, 0.30 * delta))
    if strategy == "win_now":
        max_in  = max((trade_value(p) for p in send), default=0.0)
        max_out = max((trade_value(p) for p in recv), default=0.0)
        if max_in > max_out:
            incoming *= 1.0 + 0.20 * (max_in - max_out)

    threshold = ACCEPTANCE_THRESHOLD[strategy]
    if motivation == "gm_noise":
        threshold *= 0.5
    elif motivation == "win_now_overpay":
        threshold *= 0.7
    elif motivation == "star_demand":
        threshold *= 0.85
    elif motivation == "injury_backfill":
        # Backfilling team is desperate; partners get a relaxed bar too
        # because the position-surplus they're shedding is genuinely surplus.
        threshold *= 0.85

    return incoming >= threshold * outgoing


def _avg_age(players: list[dict]) -> float:
    if not players:
        return 27.0
    return statistics.mean(int(p.get("age") or 27) for p in players)


def _validate_offer(
    send: list[dict],
    recv: list[dict],
    from_team: dict,
    to_team: dict,
    game_date: str,
) -> bool:
    """Reject offers that would strip a roster below safe operating depth."""
    if not send or not recv:
        return False
    # No duplicate players within a side.
    if len({p["id"] for p in send}) != len(send):
        return False
    if len({p["id"] for p in recv}) != len(recv):
        return False
    # Can't trade a player to their own team (sanity).
    if from_team["id"] == to_team["id"]:
        return False
    # From-team floor checks (post-trade roster).
    if not _roster_floor_ok_after(from_team["id"], send, recv, game_date):
        return False
    # To-team floor checks (mirror).
    if not _roster_floor_ok_after(to_team["id"], recv, send, game_date):
        return False
    return True


def _roster_floor_ok_after(
    team_id: int,
    removed: list[dict],
    added: list[dict],
    game_date: str,
) -> bool:
    """After removing `removed` and adding `added` to team_id, every canonical
    hitter position must have ≥1 healthy player and the team must have
    ≥_MIN_HEALTHY_PITCHERS healthy pitchers."""
    healthy = db.fetchall(
        "SELECT id, position, is_pitcher, COALESCE(is_joker,0) AS is_joker "
        "FROM players WHERE team_id = ? "
        "AND (injured_until IS NULL OR injured_until <= ?)",
        (team_id, game_date),
    )
    removed_ids = {p["id"] for p in removed}
    after = [p for p in healthy if p["id"] not in removed_ids]
    # Added players land here; injuries are checked at add-time (callers only
    # pass healthy players in `added`).
    for a in added:
        after.append({
            "id": a["id"],
            "position": a["position"],
            "is_pitcher": a.get("is_pitcher", 0),
            "is_joker":   a.get("is_joker", 0),
        })

    pitcher_count = sum(1 for p in after if p["is_pitcher"] and not p["is_joker"])
    if pitcher_count < _MIN_HEALTHY_PITCHERS:
        return False
    for pos in _CANONICAL_HITTER_POSITIONS:
        if not any(p["position"] == pos and not p["is_joker"] for p in after):
            return False
    return True


# ---------------------------------------------------------------------------
# Execute trade + emit events
# ---------------------------------------------------------------------------

def _do_trade(
    send: list[dict],
    recv: list[dict],
    from_team: dict,
    to_team: dict,
    game_date: str,
    motivation: str,
) -> list[dict]:
    event_type = f"trade_{motivation}"
    events: list[dict] = []
    tag = _motivation_tag(motivation)
    with db.get_conn() as conn:
        for p in send:
            conn.execute("UPDATE players SET team_id = ?, is_active = 1 WHERE id = ?",
                         (to_team["id"], p["id"]))
            events.append({
                "event_type": event_type,
                "team_id":    from_team["id"],
                "player_id":  p["id"],
                "detail": (
                    f"{tag}: {from_team['name']} sends {p['name']} "
                    f"({p['position']}) to {to_team['name']}"
                ),
            })
        for p in recv:
            conn.execute("UPDATE players SET team_id = ?, is_active = 1 WHERE id = ?",
                         (from_team["id"], p["id"]))
            events.append({
                "event_type": event_type,
                "team_id":    to_team["id"],
                "player_id":  p["id"],
                "detail": (
                    f"{tag}: {to_team['name']} sends {p['name']} "
                    f"({p['position']}) to {from_team['name']}"
                ),
            })
        # Stamp last-trade-date so callers can throttle if needed.
        conn.execute("UPDATE teams SET fo_last_trade_date = ? WHERE id IN (?, ?)",
                     (game_date, from_team["id"], to_team["id"]))
        conn.commit()
    # A trade reshapes both staffs — re-derive each crew so an arm slots into
    # his new club relative to the company he now keeps (a Helms on his old
    # team may be a Skidder on a deeper one, and vice versa). (o27v2/rotation.py)
    if any(p for p in send if p.get("is_pitcher")) or \
       any(p for p in recv if p.get("is_pitcher")):
        from o27v2 import rotation as _rotation
        _rotation.assign_roles_for_team(from_team["id"])
        _rotation.assign_roles_for_team(to_team["id"])
    return events


def _motivation_tag(motivation: str) -> str:
    return {
        "block_breaking":     "BLOCK-BREAK",
        "injury_backfill":    "INJURY-BACKFILL",
        "deadline_buyer":     "DEADLINE-BUY",
        "deadline_seller":    "DEADLINE-SELL",
        "salary_dump":        "SALARY-DUMP",
        "rebuild_fire_sale":  "FIRE-SALE",
        "win_now_overpay":    "WIN-NOW",
        "gm_noise":           "GM-NOISE",
        "star_demand":        "STAR-DEMAND",
    }.get(motivation, "TRADE")


# ---------------------------------------------------------------------------
# Per-team tick + main pass
# ---------------------------------------------------------------------------

def _pick_motivation(
    team: dict,
    scored: list[tuple[float, str, dict]],
    rng: random.Random,
) -> Optional[tuple[str, dict]]:
    above_floor = [(s, m, ctx) for (s, m, ctx) in scored if s >= MOTIVATION_FLOOR]
    if not above_floor:
        return None
    above_floor.sort(reverse=True, key=lambda t: t[0])
    top = above_floor[:3]
    weights = [s for (s, _m, _c) in top]
    pick = rng.choices(top, weights=weights, k=1)[0]
    return pick[1], pick[2]


def _run_team_iteration(
    team: dict,
    all_teams: list[dict],
    payrolls: dict[int, int],
    median_pay: int,
    game_date: str,
    games_played: int,
    phase: str,
    rng: random.Random,
) -> list[dict]:
    """One team's chance to initiate a single trade. Returns the event list."""
    # Activity gate.
    p_initiate = BASE_INITIATE_PROB.get(phase, 0.02) * (0.5 + team["fo_aggression"])
    if rng.random() > p_initiate:
        return []
    # Throttle: at most one trade per team per date.
    if team["fo_last_trade_date"] == game_date:
        return []

    roster = _get_tradeable_players(team["id"], game_date)
    if not roster:
        return []

    # Score every motivation; multiply by strategy weight.
    mult = STRATEGY_MULT.get(team["fo_strategy"], STRATEGY_MULT["balanced"])
    scored: list[tuple[float, str, dict]] = []

    raw: dict[str, tuple[float, dict]] = {
        "block_breaking":    _score_block_breaking(team, roster, game_date),
        "injury_backfill":   _score_injury_backfill(team, roster, game_date),
        "deadline_buyer":    _score_deadline_buyer(team, phase),
        "deadline_seller":   _score_deadline_seller(team, phase),
        "salary_dump":       _score_salary_dump(team, payrolls, median_pay),
        "rebuild_fire_sale": _score_rebuild_fire_sale(team, roster),
        "win_now_overpay":   _score_win_now_overpay(team),
        "gm_noise":          _score_gm_noise(team),
        "star_demand":       _score_star_demand(team, roster, games_played),
    }

    for motivation in MOTIVATIONS:
        score, ctx = raw[motivation]
        weight = mult.get(motivation, 1.0)
        scored.append((score * weight, motivation, ctx))

    picked = _pick_motivation(team, scored, rng)
    if not picked:
        return []
    motivation, ctx = picked

    partners = _candidate_partners(
        team, motivation, ctx, all_teams, payrolls, median_pay, game_date,
    )
    if not partners:
        return []

    # Try up to 3 partners (filter out anyone who already traded today).
    rng.shuffle(partners)
    for partner in partners[:3]:
        if partner["fo_last_trade_date"] == game_date:
            continue
        send, recv = _build_offer(team, partner, motivation, ctx, game_date, rng)
        if not _validate_offer(send, recv, team, partner, game_date):
            continue
        if not _evaluate_offer(partner, send, recv, motivation, rng):
            continue
        events = _do_trade(send, recv, team, partner, game_date, motivation)
        # Locally reflect the date stamp so subsequent iterations skip.
        team["fo_last_trade_date"]    = game_date
        partner["fo_last_trade_date"] = game_date
        return events
    return []


def run_motivation_pass(
    game_date: str,
    games_played: int,
    rng_seed: int = 0,
) -> list[dict]:
    """One league-wide motivation pass — every team gets a chance to initiate."""
    rng = random.Random((rng_seed ^ hash(game_date)) & 0x7FFFFFFF)
    phase = _season_phase(game_date)
    teams = _load_teams_with_fo()
    if not teams:
        return []
    payrolls = _compute_payrolls()
    median_pay = int(statistics.median(payrolls.values())) if payrolls else 0

    events: list[dict] = []
    order = teams[:]
    rng.shuffle(order)
    for team in order:
        # Per-team RNG keeps the pass deterministic under team reordering.
        team_rng = random.Random(
            (rng_seed ^ hash((game_date, team["id"]))) & 0x7FFFFFFF
        )
        events.extend(
            _run_team_iteration(
                team, teams, payrolls, median_pay,
                game_date, games_played, phase, team_rng,
            )
        )
    return events


def _compute_payrolls() -> dict[int, int]:
    """Sum of salaries per team_id. Empty/0 salaries treated as 0."""
    rows = db.fetchall(
        "SELECT team_id, COALESCE(SUM(salary), 0) AS total "
        "FROM players WHERE team_id IS NOT NULL GROUP BY team_id"
    )
    return {r["team_id"]: int(r["total"]) for r in rows}


# ---------------------------------------------------------------------------
# Public entry point (preserved signature; called from sim.py)
# ---------------------------------------------------------------------------

def check_deadline_and_trades(game_date: str, games_played: int) -> list[dict]:
    """Post-game trade hook. Called once per calendar date from sim.py."""
    return run_motivation_pass(game_date, games_played, rng_seed=games_played)
