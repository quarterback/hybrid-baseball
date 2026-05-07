"""
Phase 2 — Match-Day Waiver Sweep.

Runs every Sunday during the season. Each team can claim from the
free-agent pool to fill positional gaps. The auto-policy mirrors a
"medical-school match day": every useful player is placed on the team
that needs them most, with no money or contracts involved.

Sweep mechanics:
  - Up to 5 rounds per Sunday.
  - Order each round: worst record first.
  - Each team accrues 1 pick allowance per round, banked between
    rounds (a team that defers in rounds 1-3 can use the banked picks
    in rounds 4-5).
  - During a team's turn in a round, it uses its current allowance
    (round_idx - claims_made_so_far + 1) to claim players while a
    positive-improvement FA is available. Unused allowance banks for
    later rounds.
  - When a team claims an FA at position P, the team's worst player
    in the position-P bucket is cut to the FA pool. The bucket is
    re-sorted and is_active flags reassigned (top-N stay active, rest
    become reserve depth).

Trigger: `simulate_date` and `simulate_through` call `maybe_run_sweep`
before any games on a date that's a Sunday and hasn't already had a
sweep run for it. The last-sweep date is persisted in `sim_meta` so
a sweep never runs twice for the same Sunday.
"""
from __future__ import annotations

import datetime as _dt

from o27v2 import db
from o27v2.transactions import log_transaction


# Position buckets a team carries. Mirrors `_DRAFT_SLOTS` in league.py.
# A claim at position P always swaps with the worst player on the team
# whose `position` column equals P.
_HITTER_BUCKETS = ("CF", "SS", "2B", "3B", "RF", "LF", "1B", "C", "UT", "DH")
_PITCHER_BUCKET = "P"

# How many players per team are "active" in each bucket. Anything beyond
# is reserve. Mirrors generate_players()'s slot composition.
_BUCKET_ACTIVE_SLOTS: dict[str, int] = {
    "CF": 1, "SS": 1, "2B": 1, "3B": 1, "RF": 1, "LF": 1, "1B": 1, "C": 1,
    "UT": 4,    # 4 active bench, 8 reserve
    "DH": 3,    # all 3 active
    "P":  19,   # 19 active staff, 5 reserve arms
}

_MAX_ROUNDS = 5

_SWEEP_DATE_KEY = "last_match_day"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _player_overall(p: dict) -> int:
    """Composite rating used to rank players. Mirrors the snake-draft
    helper in league.py."""
    if p.get("is_pitcher"):
        return (int(p.get("pitcher_skill", 50))
              + int(p.get("command", 50))
              + int(p.get("movement", 50))) // 3
    return (int(p.get("skill", 50))
          + int(p.get("contact", 50))
          + int(p.get("power", 50))
          + int(p.get("eye", 50))) // 4


def _is_sunday(date_str: str) -> bool:
    return _dt.date.fromisoformat(date_str).weekday() == 6  # Mon=0 .. Sun=6


def _last_sweep_date() -> str | None:
    row = db.fetchone(f"SELECT value FROM sim_meta WHERE key = '{_SWEEP_DATE_KEY}'")
    return row["value"] if row and row["value"] else None


def _set_last_sweep_date(date_str: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO sim_meta (key, value) VALUES (?, ?)",
        (_SWEEP_DATE_KEY, date_str),
    )


def get_free_agents() -> list[dict]:
    """Return all players currently in the FA pool (team_id IS NULL)."""
    return db.fetchall("SELECT * FROM players WHERE team_id IS NULL")


def _team_bucket(team_id: int, bucket: str) -> list[dict]:
    """Players on this team whose `position` equals `bucket`. For 'P'
    we bucket by `is_pitcher = 1` instead, since pitcher positions are
    all 'P' but include both active and reserve."""
    if bucket == _PITCHER_BUCKET:
        return db.fetchall(
            "SELECT * FROM players WHERE team_id = ? AND is_pitcher = 1",
            (team_id,),
        )
    return db.fetchall(
        "SELECT * FROM players WHERE team_id = ? AND position = ? AND is_pitcher = 0",
        (team_id, bucket),
    )


def _team_worst_in_bucket(team_id: int, bucket: str) -> dict | None:
    """Return the lowest-rated player in the team's bucket, or None
    if the team has nobody at that position."""
    roster = _team_bucket(team_id, bucket)
    if not roster:
        return None
    return min(roster, key=_player_overall)


def _bucket_for_player(p: dict) -> str:
    """Which sweep-bucket does this player belong to?"""
    return _PITCHER_BUCKET if p.get("is_pitcher") else p["position"]


def _pick_best_claim(
    team_id: int,
    fa_pool: list[dict],
) -> tuple[dict, dict, int] | None:
    """For one team, find the FA whose addition produces the largest
    positive improvement vs the team's worst player at that FA's
    position. Returns (fa, displaced_player, delta) or None if no
    positive-improvement claim exists.

    "Positive improvement" means strictly greater than the worst
    player's overall — no-op swaps are skipped so the sweep doesn't
    churn the league's player IDs without changing roster strength.
    """
    best: tuple[dict, dict, int] | None = None
    for fa in fa_pool:
        bucket = _bucket_for_player(fa)
        worst = _team_worst_in_bucket(team_id, bucket)
        if worst is None:
            # Team has no slot at this position — shouldn't happen
            # post-draft, but skip defensively.
            continue
        delta = _player_overall(fa) - _player_overall(worst)
        if delta <= 0:
            continue
        if best is None or delta > best[2]:
            best = (fa, worst, delta)
    return best


def _reassign_active_flags(team_id: int, bucket: str) -> None:
    """After a swap, re-sort the bucket by overall rating and set the
    top-N rows to is_active=1, the rest to is_active=0. N is read
    from `_BUCKET_ACTIVE_SLOTS`. Single-active-slot buckets (CF, SS,
    etc.) are no-ops since those buckets only have one player anyway.
    """
    active_n = _BUCKET_ACTIVE_SLOTS.get(bucket, 1)
    roster = _team_bucket(team_id, bucket)
    if len(roster) <= active_n:
        # Everyone in the bucket is already active.
        for p in roster:
            db.execute("UPDATE players SET is_active = 1 WHERE id = ?", (p["id"],))
        return
    roster.sort(key=_player_overall, reverse=True)
    for idx, p in enumerate(roster):
        db.execute(
            "UPDATE players SET is_active = ? WHERE id = ?",
            (1 if idx < active_n else 0, p["id"]),
        )


def _apply_claim(
    season: int,
    game_date: str,
    team_id: int,
    fa: dict,
    displaced: dict,
) -> None:
    """Move `fa` onto `team_id`, cut `displaced` to the FA pool, and
    re-balance active/reserve flags in the affected position bucket.
    Logs both halves of the swap to the transactions table."""
    bucket = _bucket_for_player(fa)
    # Promote the FA — temporarily mark active=1; the rebalance below
    # will demote them to reserve if their bucket is over the active
    # cap and they're not in the top N.
    db.execute(
        "UPDATE players SET team_id = ?, is_active = 1 WHERE id = ?",
        (team_id, fa["id"]),
    )
    # Cut the displaced player to the FA pool.
    db.execute(
        "UPDATE players SET team_id = NULL, is_active = 0 WHERE id = ?",
        (displaced["id"],),
    )
    _reassign_active_flags(team_id, bucket)
    log_transaction(
        season, game_date, "waiver_claim", team_id, fa["id"],
        f"Claimed {fa['name']} ({bucket}, ovr {_player_overall(fa)}) "
        f"from waivers — replaces {displaced['name']} "
        f"(ovr {_player_overall(displaced)})",
    )
    log_transaction(
        season, game_date, "waiver_release", team_id, displaced["id"],
        f"Released {displaced['name']} to waivers — bumped by {fa['name']}",
    )


def _team_order_worst_first() -> list[int]:
    """Worst record first; ties broken by team_id ascending so the
    order is deterministic across runs."""
    rows = db.fetchall(
        "SELECT id, wins, losses FROM teams ORDER BY (wins * 1.0 / "
        "MAX(1, wins + losses)) ASC, id ASC"
    )
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------

def run_match_day(
    season: int,
    game_date: str,
    max_rounds: int = _MAX_ROUNDS,
) -> dict:
    """Execute one match-day sweep on `game_date`. Returns a summary:

        {
          "date": str,
          "rounds_run": int,
          "claims": list[dict],   # one entry per claim made
          "fa_before": int,
          "fa_after": int,
        }

    Mechanics (see module docstring):
      - Up to `max_rounds` rounds.
      - Each round, every team accrues 1 pick allowance.
      - Teams claim worst-record-first. During a team's turn it
        consumes its current allowance, claiming while a positive-
        improvement FA is available; unused allowance banks for
        later rounds.
      - The sweep ends early if a full round passes with zero claims
        (no team can find an improvement).
    """
    fa_before = len(get_free_agents())
    team_ids  = _team_order_worst_first()
    picks_available: dict[int, int] = {tid: 0 for tid in team_ids}
    claims: list[dict] = []

    rounds_run = 0
    for round_idx in range(max_rounds):
        rounds_run = round_idx + 1
        # Each team gains 1 fresh pick at the start of the round.
        for tid in team_ids:
            picks_available[tid] += 1

        # Refresh FA pool once per round (in-round claims update the
        # in-memory pool directly so we don't re-query mid-round).
        fa_pool = get_free_agents()
        round_claims = 0

        for tid in team_ids:
            while picks_available[tid] > 0:
                pick = _pick_best_claim(tid, fa_pool)
                if pick is None:
                    break  # no improvement available — bank remaining
                fa, displaced, delta = pick
                _apply_claim(season, game_date, tid, fa, displaced)
                # Update in-memory pool: remove claimed FA, add the
                # displaced player so other teams can pick them up
                # later this round.
                fa_pool = [p for p in fa_pool if p["id"] != fa["id"]]
                # Reload displaced from DB since we just updated it.
                displaced_row = db.fetchone(
                    "SELECT * FROM players WHERE id = ?", (displaced["id"],)
                )
                if displaced_row is not None:
                    fa_pool.append(displaced_row)
                picks_available[tid] -= 1
                claims.append({
                    "round": rounds_run,
                    "team_id": tid,
                    "claimed_id": fa["id"],
                    "claimed_name": fa["name"],
                    "claimed_pos": _bucket_for_player(fa),
                    "claimed_ovr": _player_overall(fa),
                    "released_id": displaced["id"],
                    "released_name": displaced["name"],
                    "released_ovr": _player_overall(displaced),
                    "delta": delta,
                })
                round_claims += 1

        if round_claims == 0:
            # Nobody found an improvement this round — sweep is done.
            break

    _set_last_sweep_date(game_date)

    return {
        "date": game_date,
        "rounds_run": rounds_run,
        "claims": claims,
        "fa_before": fa_before,
        "fa_after": len(get_free_agents()),
    }


def maybe_run_sweep(game_date: str, season: int = 1) -> dict | None:
    """Idempotent sweep trigger. Runs `run_match_day(season, game_date)`
    if `game_date` is a Sunday AND no sweep has already run for it (or
    a later date). Returns the sweep summary, or None if skipped.

    Called from the sim driver (`simulate_date`, `simulate_through`,
    `simulate_next_n`) before games for a Sunday are simulated.
    """
    if not _is_sunday(game_date):
        return None
    last = _last_sweep_date()
    if last is not None and last >= game_date:
        return None
    return run_match_day(season, game_date)
