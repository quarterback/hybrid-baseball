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

Performance shape:
  - One `SELECT * FROM players` to load every team roster + the FA
    pool into memory at sweep start.
  - All round / pick / swap logic is pure-Python on dicts — zero
    SQL during the round loop.
  - At the end: three executemany calls (team_id move, is_active
    reflag, transaction log) regardless of how many claims fired.
  - Total SQL per sweep: ~5 statements. Previous version did
    O(teams × FAs × claims) per-row queries (~10K+ per Sunday) and
    blocked the sim around the first Sunday.
"""
from __future__ import annotations

import datetime as _dt

from o27v2 import db


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


def _bucket_for_player(p: dict) -> str:
    """Which sweep-bucket does this player belong to?"""
    return _PITCHER_BUCKET if p.get("is_pitcher") else (p.get("position") or "")


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

    See module docstring for mechanics. Implementation is one bulk
    load + in-memory rounds + bulk writeback for performance.
    """
    team_ids = _team_order_worst_first()
    if not team_ids:
        return {"date": game_date, "rounds_run": 0, "claims": [],
                "fa_before": 0, "fa_after": 0}

    # ---- Bulk load ---------------------------------------------------------
    all_players = db.fetchall("SELECT * FROM players")
    fa_pool: list[dict] = []
    team_buckets: dict[int, dict[str, list[dict]]] = {tid: {} for tid in team_ids}

    for row in all_players:
        p = dict(row)
        tid = p.get("team_id")
        if tid is None:
            fa_pool.append(p)
        elif tid in team_buckets:
            bucket = _bucket_for_player(p)
            team_buckets[tid].setdefault(bucket, []).append(p)
        # Players with team_id pointing at a missing team (shouldn't happen
        # post-Phase-1, but defensive) are skipped — they won't bias the
        # claim math and won't get reassigned.

    fa_before = len(fa_pool)

    # ---- Round loop (pure in-memory) ---------------------------------------
    picks_available: dict[int, int] = {tid: 0 for tid in team_ids}
    claims: list[dict] = []
    moved_players: dict[int, int | None] = {}   # player_id -> new team_id (None for FA)
    dirty_buckets: set[tuple[int, str]] = set() # (team_id, bucket) pairs needing is_active reflag

    rounds_run = 0
    for round_idx in range(max_rounds):
        rounds_run = round_idx + 1
        # Each team gains one fresh pick at the start of the round.
        for tid in team_ids:
            picks_available[tid] += 1

        round_claims = 0
        for tid in team_ids:
            while picks_available[tid] > 0:
                # In-memory scan for the best positive-delta upgrade.
                best: tuple[dict, dict, int] | None = None
                for fa in fa_pool:
                    bucket = _bucket_for_player(fa)
                    bucket_roster = team_buckets[tid].get(bucket)
                    if not bucket_roster:
                        continue
                    worst = min(bucket_roster, key=_player_overall)
                    delta = _player_overall(fa) - _player_overall(worst)
                    if delta <= 0:
                        continue
                    if best is None or delta > best[2]:
                        best = (fa, worst, delta)

                if best is None:
                    break  # no upgrade — bank remaining picks for later rounds

                fa, displaced, delta = best
                bucket = _bucket_for_player(fa)

                # In-memory swap.
                team_buckets[tid][bucket].remove(displaced)
                team_buckets[tid][bucket].append(fa)
                fa_pool.remove(fa)
                fa_pool.append(displaced)

                # Track for the writeback pass.
                moved_players[fa["id"]] = tid
                moved_players[displaced["id"]] = None
                dirty_buckets.add((tid, bucket))

                claims.append({
                    "round": rounds_run,
                    "team_id": tid,
                    "claimed_id":   fa["id"],
                    "claimed_name": fa["name"],
                    "claimed_pos":  bucket,
                    "claimed_ovr":  _player_overall(fa),
                    "released_id":   displaced["id"],
                    "released_name": displaced["name"],
                    "released_ovr":  _player_overall(displaced),
                    "delta": delta,
                })
                picks_available[tid] -= 1
                round_claims += 1

        if round_claims == 0:
            # Nobody found an improvement this round — sweep is done.
            break

    # ---- Writeback (bulk) --------------------------------------------------
    if moved_players:
        # New team_id for each moved player (None ⇒ FA pool).
        db.executemany(
            "UPDATE players SET team_id = ? WHERE id = ?",
            [(new_tid, pid) for pid, new_tid in moved_players.items()],
        )
        # Players cut to FA: also clear is_active.
        fa_demote = [(pid,) for pid, new_tid in moved_players.items() if new_tid is None]
        if fa_demote:
            db.executemany(
                "UPDATE players SET is_active = 0 WHERE id = ?",
                fa_demote,
            )

    # Reflag is_active for every bucket that saw a swap. Top-N (per
    # `_BUCKET_ACTIVE_SLOTS`) stay active; the rest become reserve.
    if dirty_buckets:
        active_updates: list[tuple[int, int]] = []
        for tid, bucket in dirty_buckets:
            active_n = _BUCKET_ACTIVE_SLOTS.get(bucket, 1)
            roster = sorted(team_buckets[tid].get(bucket, []),
                            key=_player_overall, reverse=True)
            for idx, p in enumerate(roster):
                active_updates.append((1 if idx < active_n else 0, p["id"]))
        if active_updates:
            db.executemany(
                "UPDATE players SET is_active = ? WHERE id = ?",
                active_updates,
            )

    # Bulk-log the transactions for the whole sweep.
    if claims:
        from o27v2.transactions import log_many
        events: list[dict] = []
        for c in claims:
            events.append({
                "event_type": "waiver_claim",
                "team_id":   c["team_id"],
                "player_id": c["claimed_id"],
                "detail": (
                    f"R{c['round']} claim — {c['claimed_name']} "
                    f"({c['claimed_pos']}, ovr {c['claimed_ovr']}); "
                    f"replaces {c['released_name']} (ovr {c['released_ovr']})"
                ),
            })
            events.append({
                "event_type": "waiver_release",
                "team_id":   c["team_id"],
                "player_id": c["released_id"],
                "detail": (
                    f"R{c['round']} release — bumped by {c['claimed_name']} "
                    f"(ovr {c['claimed_ovr']})"
                ),
            })
        log_many(season, game_date, events)

    _set_last_sweep_date(game_date)

    return {
        "date": game_date,
        "rounds_run": rounds_run,
        "claims": claims,
        "fa_before": fa_before,
        "fa_after": len(fa_pool),
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
