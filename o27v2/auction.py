"""
IPL-style auction with keepers — off-season redistribution mechanic
for tiered league configs.

Run between seasons after promotion/relegation. Produces a fully
shuffled roster ecosystem: a fixed number of keepers per team carry
over (config-driven, default 3), the rest of every team's players
land in the auction pool, then each player is sold to the highest-
bidding team via a Vickrey draw — winner pays the second-highest bid.

Design notes:
  * No human bidding. Each team has an internal valuation model based
    on player overall + a roster-need bonus. The valuation drives a
    private-value bid; ties broken by team id.
  * Vickrey (second-price) auction: encourages teams to bid their true
    value rather than try to game order, and produces interpretable
    "the player went for X because team B was willing to pay X-1"
    narratives in the log.
  * Players who go unsold (no team had budget left) drop into the pro
    free-agent pool with team_id NULL.
  * After the auction, every team is re-rostered to ROSTER_TARGET
    active players. Reserves are assigned from leftover purchased
    players (the lowest-overall ones each team won).

Public API:
  apply_auction(config, *, rng_seed) -> dict
"""
from __future__ import annotations

import random
from typing import Any

from o27v2 import db


# Roster shape after the auction. Active = 34 (matches the snake-draft
# default in league.py). Anything beyond gets is_active=0.
ROSTER_TARGET = 34


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_KEEPERS = """
CREATE TABLE IF NOT EXISTS auction_keepers (
    season   INTEGER NOT NULL,
    team_id  INTEGER NOT NULL REFERENCES teams(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    PRIMARY KEY (season, player_id)
);
"""

_SCHEMA_RESULTS = """
CREATE TABLE IF NOT EXISTS auction_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season      INTEGER NOT NULL,
    lot_order   INTEGER,                       -- 1-based sequence at the auction
    player_id   INTEGER NOT NULL REFERENCES players(id),
    player_overall INTEGER,                    -- snapshot at auction time
    winner_team_id INTEGER REFERENCES teams(id),  -- NULL = unsold
    winning_bid INTEGER,                       -- the winner's max bid
    second_bid  INTEGER,                       -- the runner-up's bid (Vickrey reference)
    price       INTEGER,                       -- the realised clearing price (Vickrey)
    bid_round   INTEGER NOT NULL DEFAULT 1
);
"""

# Per-lot bid sheet (top 8 bidders) so the live-auction UI can animate
# the bidding up to the final price. Storing all 56 teams × 2500 lots
# would balloon to ~140k rows; capping at the top 8 per lot still gives
# the UI everything it needs to render bid drama.
_SCHEMA_LOT_BIDS = """
CREATE TABLE IF NOT EXISTS auction_lot_bids (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    season    INTEGER NOT NULL,
    lot_order INTEGER NOT NULL,
    team_id   INTEGER NOT NULL REFERENCES teams(id),
    bid       INTEGER NOT NULL,
    rank      INTEGER NOT NULL                 -- 1 = winner, 2 = runner-up, ...
);
"""


def init_auction_schema() -> None:
    db.execute(_SCHEMA_KEEPERS)
    db.execute(_SCHEMA_RESULTS)
    db.execute(_SCHEMA_LOT_BIDS)
    # Idempotent migrations for older auction_results rows that pre-date
    # the live-auction columns.
    for col_def in (
        "lot_order INTEGER",
        "player_overall INTEGER",
        "price INTEGER",
    ):
        try:
            db.execute(f"ALTER TABLE auction_results ADD COLUMN {col_def}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Player overall (mirrors league.py._player_overall but lives here so
# auction.py doesn't import league internals)
# ---------------------------------------------------------------------------

def _player_overall(p: dict) -> int:
    if p.get("is_pitcher"):
        return (int(p.get("pitcher_skill", 50))
                + int(p.get("command", 50))
                + int(p.get("movement", 50))) // 3
    return (int(p.get("skill", 50))
            + int(p.get("contact", 50))
            + int(p.get("power", 50))
            + int(p.get("eye", 50))) // 4


# ---------------------------------------------------------------------------
# Keeper selection
# ---------------------------------------------------------------------------

def _select_keepers(team_id: int, n_keepers: int) -> list[dict]:
    """Pick the top-N players on this team's active roster by overall.
    Reserves (is_active=0) are eligible too — high-grade reserves can
    be kept over middling actives."""
    if n_keepers <= 0:
        return []
    rows = db.fetchall(
        "SELECT * FROM players WHERE team_id = ?",
        (team_id,),
    )
    ranked = sorted(rows, key=lambda p: _player_overall(dict(p)), reverse=True)
    return [dict(p) for p in ranked[:n_keepers]]


# ---------------------------------------------------------------------------
# Bid valuation
# ---------------------------------------------------------------------------

def _team_position_need(team_id: int, position: str,
                        is_pitcher: bool) -> int:
    """Return the count of slots a team needs at this position. A team
    that already has 19 active pitchers won't outbid a team that needs
    arms. Naive but produces sensible auction shape."""
    if is_pitcher:
        row = db.fetchone(
            "SELECT COUNT(*) AS n FROM players "
            "WHERE team_id = ? AND is_pitcher = 1",
            (team_id,),
        )
        have = (row or {}).get("n", 0)
        # Target: 19 pitchers per active roster + 5 reserves.
        return max(0, 24 - have)
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM players "
        "WHERE team_id = ? AND is_pitcher = 0 AND position = ?",
        (team_id, position),
    )
    have = (row or {}).get("n", 0)
    # Most positions: target 2 (a starter + backup). 4 for high-rotation.
    target = 4 if position in ("CF", "SS", "2B", "C") else 2
    return max(0, target - have)


# Bid base in guilders per overall point. With a 78-overall mega-star
# this lands at ƒ39 cr × need_mult (≤1.5) × noise (≤1.15) ≈ ƒ67 cr max
# bid; Vickrey collapses that to a clearing price around ƒ45-55 cr —
# the "ƒ50-crore signing" headline tier on a ƒ100-cr team purse.
BID_BASE_PER_OVERALL: int = 50 * 100_000  # 50 lakh = ƒ50,00,000


def _team_bid(player: dict, team_id: int, purse_remaining: int,
              n_keepers: int, rng: random.Random,
              *, min_bid: int) -> int:
    """Compute a single team's private valuation for a player, in
    guilders.

    Formula:
      base_value      = overall × BID_BASE_PER_OVERALL
      need_multiplier = 1.0 + 0.15 × position_need (capped at 1.5)
      noise           = uniform(0.85, 1.15)
      max_bid         = floor(base × need_mult × noise)

    The team won't overspend relative to remaining slot needs — it
    reserves at least `min_bid` per remaining slot so the rest of the
    roster can clear without bricking. Produces the IPL-shaped
    distribution where the top players spike high and the depth lots
    clear at floor.
    """
    overall = _player_overall(player)
    base = overall * BID_BASE_PER_OVERALL

    is_pitcher = bool(player.get("is_pitcher"))
    position = player.get("position", "P" if is_pitcher else "DH")
    need = _team_position_need(team_id, position, is_pitcher)
    need_mult = min(1.5, 1.0 + 0.15 * need)

    noise = rng.uniform(0.85, 1.15)
    max_bid = int(base * need_mult * noise)

    remaining_slots = max(1, ROSTER_TARGET - n_keepers)
    cap = max(min_bid, purse_remaining - min_bid * (remaining_slots - 1))
    return max(0, min(max_bid, cap))


# ---------------------------------------------------------------------------
# Auction loop
# ---------------------------------------------------------------------------

def apply_auction(
    config: dict,
    *,
    rng_seed: int = 0,
    season: int | None = None,
) -> dict[str, Any]:
    """Run the full off-season auction. Mutates the database.

    Pipeline:
      1. Pick keepers per team (config knob, default 3). Insert into
         auction_keepers.
      2. Set non-keepers' team_id = NULL (they're now in the pool).
         Snapshot their player_id list.
      3. Sort the pool by overall desc (with small jitter). For each
         player:
           a. Each team computes its private valuation.
           b. Highest valuer wins; pays max(second_highest + 1, min_bid).
           c. If no team would pay min_bid, the player goes unsold and
              stays a free agent.
      4. Promote the winning team's purchased players to is_active=1
         until ROSTER_TARGET; rest go to reserves (is_active=0).

    Returns a structured report.
    """
    init_auction_schema()
    cfg_a = dict(config.get("auction") or {})
    enabled = bool(cfg_a.get("enabled", True))
    if not enabled:
        return {"ok": False, "reason": "auction disabled in config"}

    n_keepers = int(cfg_a.get("keepers_per_team", 3))
    # Defaults are guilder-era: 100 cr per team, 50 lakh min-bid.
    # See `BID_BASE_PER_OVERALL` for the per-grade scaling that ties
    # the bid distribution to this purse.
    purse_init = int(cfg_a.get("team_purse", 100 * 1_00_00_000))   # 100 cr
    min_bid    = int(cfg_a.get("min_bid",       50 * 1_00_000))    # 50 lakh

    if season is None:
        row = db.fetchone(
            "SELECT value FROM sim_meta WHERE key = 'season_number'"
        )
        try:
            season = int((row or {}).get("value") or 1)
        except (TypeError, ValueError):
            season = 1

    # Wipe any previous auction record for this season so the rerun
    # doesn't merge with stale data.
    db.execute("DELETE FROM auction_keepers WHERE season = ?", (season,))
    db.execute("DELETE FROM auction_results WHERE season = ?", (season,))
    db.execute("DELETE FROM auction_lot_bids WHERE season = ?", (season,))

    rng = random.Random((rng_seed or 0) ^ 0xA17_C7)

    teams = db.fetchall("SELECT * FROM teams ORDER BY id")
    team_ids = [t["id"] for t in teams]
    if not teams:
        return {"ok": False, "reason": "no teams"}

    # Step 1: keepers
    keepers_by_team: dict[int, list[dict]] = {}
    keeper_player_ids: set[int] = set()
    for t in teams:
        keeps = _select_keepers(t["id"], n_keepers)
        keepers_by_team[t["id"]] = keeps
        for k in keeps:
            keeper_player_ids.add(k["id"])
            db.execute(
                "INSERT OR IGNORE INTO auction_keepers "
                "(season, team_id, player_id) VALUES (?, ?, ?)",
                (season, t["id"], k["id"]),
            )

    # Step 2: pool (everyone NOT a keeper, NOT already unaffiliated FA)
    pool_rows = db.fetchall(
        "SELECT * FROM players WHERE team_id IS NOT NULL"
    )
    pool: list[dict] = []
    for r in pool_rows:
        if r["id"] in keeper_player_ids:
            continue
        pool.append(dict(r))

    # Cut all non-keeper players from their teams.
    if pool:
        pool_ids = [p["id"] for p in pool]
        # SQLite parameter limit safety: chunk if huge.
        CHUNK = 500
        for i in range(0, len(pool_ids), CHUNK):
            chunk = pool_ids[i:i + CHUNK]
            qs = ",".join("?" for _ in chunk)
            db.execute(
                f"UPDATE players SET team_id = NULL, is_active = 0 "
                f"WHERE id IN ({qs})", tuple(chunk)
            )

    # Sort the pool: best players go to auction first. Adds small
    # jitter so identical-grade players aren't strictly id-ordered.
    pool.sort(key=lambda p: _player_overall(p) + rng.uniform(-2, 2),
              reverse=True)

    # Step 3: per-team purse, slot tracking, auction loop.
    purse = {tid: purse_init for tid in team_ids}
    won_by_team: dict[int, list[dict]] = {tid: [] for tid in team_ids}
    log: list[dict] = []
    unsold = 0

    lot_order = 0
    for player in pool:
        lot_order += 1
        player_overall = _player_overall(player)

        # Each team makes one private bid.
        bids: list[tuple[int, int]] = []  # (bid_amount, team_id)
        for tid in team_ids:
            keepers_on_team = len(keepers_by_team.get(tid, []))
            won_so_far = len(won_by_team[tid])
            slots_filled = keepers_on_team + won_so_far
            if slots_filled >= ROSTER_TARGET + 13:  # active + reserve cap
                continue
            if purse[tid] < min_bid:
                continue
            bid = _team_bid(player, tid, purse[tid],
                            n_keepers + won_so_far, rng,
                            min_bid=min_bid)
            if bid >= min_bid:
                bids.append((bid, tid))

        # Persist top 8 bids per lot for the live-auction UI. Done
        # here even on unsold lots (so the live page can render an
        # explicit "no bidders" stage card without a special-case).
        bids_sorted_desc = sorted(bids, reverse=True)
        for rank, (bid_amt, bid_tid) in enumerate(bids_sorted_desc[:8], start=1):
            db.execute(
                "INSERT INTO auction_lot_bids "
                "(season, lot_order, team_id, bid, rank) "
                "VALUES (?, ?, ?, ?, ?)",
                (season, lot_order, bid_tid, bid_amt, rank),
            )

        if not bids_sorted_desc:
            unsold += 1
            db.execute(
                "INSERT INTO auction_results "
                "(season, lot_order, player_id, player_overall, "
                " winner_team_id, winning_bid, second_bid, price) "
                "VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL)",
                (season, lot_order, player["id"], player_overall),
            )
            log.append({
                "player_id":  player["id"],
                "player_name": player["name"],
                "position":    player["position"],
                "overall":     player_overall,
                "lot_order":   lot_order,
                "winner_team_id": None,
                "winner_abbrev":  None,
                "winning_bid":    None,
                "second_bid":     None,
                "result":         "unsold",
            })
            continue

        # Vickrey: highest bid wins, pays second-highest + 1 (clamped to min_bid).
        winner_bid, winner_tid = bids_sorted_desc[0]
        second = bids_sorted_desc[1][0] if len(bids_sorted_desc) >= 2 else min_bid
        price = max(min_bid, second + 1)
        price = min(price, winner_bid, purse[winner_tid])

        purse[winner_tid] -= price
        won_by_team[winner_tid].append(player)

        winner_team = next((t for t in teams if t["id"] == winner_tid), None)
        winner_abbrev = winner_team["abbrev"] if winner_team else ""

        db.execute(
            "INSERT INTO auction_results "
            "(season, lot_order, player_id, player_overall, "
            " winner_team_id, winning_bid, second_bid, price) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (season, lot_order, player["id"], player_overall,
             winner_tid, winner_bid, second, price),
        )
        log.append({
            "player_id":     player["id"],
            "player_name":   player["name"],
            "position":      player["position"],
            "overall":       player_overall,
            "lot_order":     lot_order,
            "winner_team_id": winner_tid,
            "winner_abbrev":  winner_abbrev,
            "winning_bid":    winner_bid,
            "second_bid":     second,
            "price":          price,
            "result":         "sold",
        })

    # Step 4: assign winners to teams + active/reserve flags.
    for tid in team_ids:
        won = won_by_team[tid]
        # Sort by overall desc; first ROSTER_TARGET-keepers slots → active.
        won.sort(key=lambda p: _player_overall(p), reverse=True)
        active_target = max(0, ROSTER_TARGET - len(keepers_by_team.get(tid, [])))
        for idx, p in enumerate(won):
            is_active = 1 if idx < active_target else 0
            db.execute(
                "UPDATE players SET team_id = ?, is_active = ? "
                "WHERE id = ?",
                (tid, is_active, p["id"]),
            )
        # Re-flag keepers as active.
        for k in keepers_by_team.get(tid, []):
            db.execute(
                "UPDATE players SET is_active = 1 WHERE id = ?",
                (k["id"],),
            )

    # Per-team summary for the UI.
    summary = []
    for t in teams:
        won = won_by_team[t["id"]]
        spent = purse_init - purse[t["id"]]
        summary.append({
            "team_id":     t["id"],
            "abbrev":      t["abbrev"],
            "name":        t["name"],
            "league":      t["league"],
            "keepers":     len(keepers_by_team.get(t["id"], [])),
            "purchased":   len(won),
            "spent":       spent,
            "remaining":   purse[t["id"]],
        })

    return {
        "ok":         True,
        "season":     season,
        "n_keepers":  sum(len(v) for v in keepers_by_team.values()),
        "n_pool":     len(pool),
        "n_sold":     sum(1 for r in log if r["result"] == "sold"),
        "n_unsold":   unsold,
        "log":        log,
        "summary":    summary,
        "config_used": {
            "keepers_per_team": n_keepers,
            "team_purse":       purse_init,
            "min_bid":          min_bid,
        },
    }


# ---------------------------------------------------------------------------
# Live-auction feed (top-50 stage + montage of the rest)
# ---------------------------------------------------------------------------

# Stage tier — first N lots get the full animated card. Everything
# after rolls past in the montage. Tuned for IPL TV pacing: ~50 lots
# at 3s each = ~2.5 min of stage, then ~30s of montage.
LIVE_STAGE_LOTS = 50


def _player_stars(overall: int | None) -> int:
    """Map a composite overall to a 1-5 star tier — the same scale the
    youth league uses, so the live UI's drama lines up with the
    recruiting-style language. Pure presentation, not stored."""
    if overall is None:
        return 1
    if overall >= 68: return 5
    if overall >= 58: return 4
    if overall >= 48: return 3
    if overall >= 36: return 2
    return 1


def get_live_auction(season: int | None = None) -> dict | None:
    """Return the JSON payload the /auction/live page consumes:

      {
        "season":        int,
        "stage_lots":    [ {lot_order, player, overall, stars, position,
                            winner_abbrev, winner_name, winning_bid,
                            second_bid, price, bids: [{abbrev, name, bid, rank}]} ],
        "montage_lots":  [ same shape, for lots 51..end ],
        "team_purses":   {team_id: {abbrev, spent, won}},
        "config":        {keepers_per_team, team_purse, min_bid},
        "summary":       {n_lots, n_sold, n_unsold},
      }
    """
    init_auction_schema()
    if season is None:
        row = db.fetchone(
            "SELECT value FROM sim_meta WHERE key = 'season_number'"
        )
        try:
            season = int((row or {}).get("value") or 1)
        except (TypeError, ValueError):
            season = 1

    results = db.fetchall(
        "SELECT r.*, "
        "       p.name AS player_name, p.position, p.is_pitcher, "
        "       wt.abbrev AS winner_abbrev, wt.name AS winner_name "
        "FROM auction_results r "
        "JOIN players p ON p.id = r.player_id "
        "LEFT JOIN teams wt ON wt.id = r.winner_team_id "
        "WHERE r.season = ? AND r.lot_order IS NOT NULL "
        "ORDER BY r.lot_order ASC",
        (season,),
    )
    if not results:
        return None

    bid_rows = db.fetchall(
        "SELECT b.lot_order, b.bid, b.rank, "
        "       t.id AS team_id, t.abbrev, t.name AS team_name "
        "FROM auction_lot_bids b "
        "JOIN teams t ON t.id = b.team_id "
        "WHERE b.season = ? "
        "ORDER BY b.lot_order ASC, b.rank ASC",
        (season,),
    )
    bids_by_lot: dict[int, list[dict]] = {}
    for r in bid_rows:
        bids_by_lot.setdefault(r["lot_order"], []).append({
            "team_id": r["team_id"],
            "abbrev":  r["abbrev"],
            "name":    r["team_name"],
            "bid":     r["bid"],
            "rank":    r["rank"],
        })

    # Stage / montage split.
    def _shape(r: dict) -> dict:
        winning = r["winning_bid"]
        second  = r["second_bid"]
        # Prefer the persisted clearing price; fall back to the Vickrey
        # reconstruction for older auction_results rows that pre-date
        # the `price` column. The fallback uses winning as the floor
        # (rather than a hardcoded magic number) since `winning >=
        # min_bid` is invariant of the auction loop.
        price = r["price"] if r["price"] is not None else (
            None if winning is None else
            (winning if second is None else min(winning, second + 1))
        )
        return {
            "lot_order":      r["lot_order"],
            "player_id":      r["player_id"],
            "player_name":    r["player_name"],
            "position":       r["position"],
            "is_pitcher":     bool(r["is_pitcher"]),
            "overall":        r["player_overall"],
            "stars":          _player_stars(r["player_overall"]),
            "winner_team_id": r["winner_team_id"],
            "winner_abbrev":  r["winner_abbrev"] or "",
            "winner_name":    r["winner_name"] or "",
            "winning_bid":    winning,
            "second_bid":     second,
            "price":          price,
            "result":         "sold" if r["winner_team_id"] else "unsold",
            "bids":           bids_by_lot.get(r["lot_order"], []),
        }

    shaped = [_shape(r) for r in results]
    stage_lots   = shaped[:LIVE_STAGE_LOTS]
    montage_lots = shaped[LIVE_STAGE_LOTS:]

    # Team purse / spend tracker.
    team_rows = db.fetchall("SELECT id, abbrev, name FROM teams ORDER BY id")
    spent_by_team: dict[int, int] = {t["id"]: 0 for t in team_rows}
    won_by_team:   dict[int, int] = {t["id"]: 0 for t in team_rows}
    for s in shaped:
        if s["winner_team_id"] and s["price"]:
            spent_by_team[s["winner_team_id"]] = (
                spent_by_team.get(s["winner_team_id"], 0) + (s["price"] or 0)
            )
            won_by_team[s["winner_team_id"]] = (
                won_by_team.get(s["winner_team_id"], 0) + 1
            )
    team_purses = []
    for t in team_rows:
        team_purses.append({
            "team_id": t["id"],
            "abbrev":  t["abbrev"],
            "name":    t["name"],
            "spent":   spent_by_team.get(t["id"], 0),
            "won":     won_by_team.get(t["id"], 0),
        })
    team_purses.sort(key=lambda r: r["spent"], reverse=True)

    n_sold   = sum(1 for s in shaped if s["winner_team_id"])
    n_unsold = len(shaped) - n_sold

    return {
        "season":       season,
        "stage_lots":   stage_lots,
        "montage_lots": montage_lots,
        "stage_count":  len(stage_lots),
        "team_purses":  team_purses,
        "summary": {
            "n_lots":   len(shaped),
            "n_sold":   n_sold,
            "n_unsold": n_unsold,
        },
    }


def get_auction(season: int | None = None) -> dict | None:
    """Read-only fetch of a stored auction result for the UI."""
    init_auction_schema()
    if season is None:
        row = db.fetchone(
            "SELECT value FROM sim_meta WHERE key = 'season_number'"
        )
        try:
            season = int((row or {}).get("value") or 1)
        except (TypeError, ValueError):
            season = 1

    keepers = db.fetchall(
        "SELECT k.season, k.team_id, k.player_id, "
        "       t.abbrev AS team_abbrev, t.name AS team_name, "
        "       p.name AS player_name, p.position, p.is_pitcher "
        "FROM auction_keepers k "
        "JOIN teams t ON t.id = k.team_id "
        "JOIN players p ON p.id = k.player_id "
        "WHERE k.season = ? "
        "ORDER BY t.league, t.id",
        (season,),
    )

    results = db.fetchall(
        "SELECT r.season, r.player_id, r.winner_team_id, r.winning_bid, "
        "       r.second_bid, p.name AS player_name, p.position, p.is_pitcher, "
        "       t.abbrev AS winner_abbrev, t.name AS winner_name "
        "FROM auction_results r "
        "JOIN players p ON p.id = r.player_id "
        "LEFT JOIN teams t ON t.id = r.winner_team_id "
        "WHERE r.season = ? "
        "ORDER BY r.winning_bid DESC NULLS LAST, r.id",
        (season,),
    )

    if not keepers and not results:
        return None
    return {
        "season":  season,
        "keepers": [dict(r) for r in keepers],
        "results": [dict(r) for r in results],
    }
