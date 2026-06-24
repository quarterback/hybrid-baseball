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

# Reserve-slot cap on top of the active roster. Final regular-season
# total roster size = ROSTER_TARGET + RESERVE_CAP = 47.
RESERVE_CAP = 13
ROSTER_FINAL_CAP = ROSTER_TARGET + RESERVE_CAP   # 47

# During the auction itself (and the trade window that immediately
# follows) teams can carry up to ROSTER_DRAFT_CAP players. The extra
# slack vs. the 47 regular-season cap is deliberate — it gives teams
# room to make trades or roster moves before the league-wide cut day
# enforces 47. Mirrors NFL preseason: 90 → 53 at cut day.
ROSTER_DRAFT_CAP = 50

# Minimum-roster guarantee. No team may exit the auction below a viable roster:
# a light-spending club that won few lots is topped up from the free-agent pool
# so it can always field a full lineup + staff. Without this, a low-bid team
# leaves the auction with a skeleton roster (the 9-batter / 5-pitcher bug).
AUCTION_MIN_ROSTER   = ROSTER_TARGET           # total players a team must exit with
AUCTION_MIN_PITCHERS = 11                       # staff floor among those
_CANONICAL_POSITIONS = ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF")


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
    winner_team_id INTEGER REFERENCES teams(id),  -- NULL = unsold; the original Vickrey winner
    winning_bid INTEGER,                       -- the winner's max bid
    second_bid  INTEGER,                       -- the runner-up's bid (Vickrey reference)
    price       INTEGER,                       -- the realised clearing price (Vickrey)
    traded_to_team_id INTEGER REFERENCES teams(id),  -- post-clear sellback target; NULL = no trade
    trade_price INTEGER,                       -- guilders the buyer paid the seller
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
    # the live-auction + sellback columns.
    for col_def in (
        "lot_order INTEGER",
        "player_overall INTEGER",
        "price INTEGER",
        "traded_to_team_id INTEGER",
        "trade_price INTEGER",
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
    """Return SIGNED need at this position: positive = under-stocked
    (team wants this player), negative = over-stocked (team would
    rather skip). Drives the Assistant-GM-style valuation multiplier
    that prevents one team from stacking the same position.

    Targets:
      * pitchers      24 (19 active + 5 reserve)
      * high-rotation  4 (CF/SS/2B/C)
      * other positions 2 (1B/3B/LF/RF/DH-style)
    """
    if is_pitcher:
        row = db.fetchone(
            "SELECT COUNT(*) AS n FROM players "
            "WHERE team_id = ? AND is_pitcher = 1",
            (team_id,),
        )
        have = (row or {}).get("n", 0)
        return 24 - have
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM players "
        "WHERE team_id = ? AND is_pitcher = 0 AND position = ?",
        (team_id, position),
    )
    have = (row or {}).get("n", 0)
    target = 4 if position in ("CF", "SS", "2B", "C") else 2
    return target - have


def _need_multiplier(need: int) -> float:
    """Roster-gap-aware valuation multiplier — the Assistant GM piece.

    Pushes bids UP when the team has a real gap at this position;
    DAMPERS bids hard when the team is already stuffed there. The
    over-stuffed damper is the antidote to the old behavior where
    one team would win 12 of 13 lots because nothing checked whether
    they'd already filled the slot.
    """
    if need >= 2:  return 1.30   # multiple open slots — push hard
    if need == 1:  return 1.15   # one open slot — push
    if need == 0:  return 0.90   # target met — mild damper
    if need == -1: return 0.50   # one over — strong damper
    return 0.15                  # 2+ over — essentially won't bid


# Bid base curve. A linear `overall × const` produces too-tight Vickrey
# clustering at the top. The cure is a non-linear curve that pulls the
# elite tier well above the mid-tier *before* noise and need-multipliers
# compress them. Pivot at OVR 25 (well below the auction-pool floor
# of ~30) so even fringe players land at a meaningful base — and the
# gap between fringe and elite stretches across the full quadratic.
#
# Quadratic: base = max(0, overall - 25) ** 2 × BID_QUAD_SCALE
#
# Scale × purse calibration: raw bases stay an order of magnitude under
# the per-team cap (≈ ƒ185 cr on a ƒ200-cr purse). Personality
# multipliers stack to ~4.5× before clipping, which is enough headroom
# for the top star's max-bid to land in the ƒ80-100 cr range — real
# Vickrey price discovery rather than every team hitting the ceiling.
#
# Sample raw bases (before need × aggression × noise × star_warp):
#   OVR 30 →   25 × 1L                = ƒ25 lakh
#   OVR 40 →  225 × 1L                = ƒ2.25 cr
#   OVR 50 →  625 × 1L                = ƒ6.25 cr
#   OVR 55 →  900 × 1L                = ƒ9    cr
#   OVR 60 → 1225 × 1L  +    0 × 10L  = ƒ12.25 cr   (elite bonus pivot)
#   OVR 65 → 1600 × 1L  +   25 × 10L  = ƒ18.5 cr
#   OVR 67 → 1764 × 1L  +   49 × 10L  = ƒ22.5 cr
#   OVR 68 → 1849 × 1L  +   64 × 10L  = ƒ24.9 cr
#   OVR 70 → 2025 × 1L  +  100 × 10L  = ƒ30.25 cr
#   OVR 78 → 2809 × 1L  +  324 × 10L  = ƒ60.5 cr
#
# The elite bonus carves out a real step-up between the top ~25 in
# any seeded pool and everyone else: the OVR 60 → 68 stretch goes
# from a 1.5× ratio under pure quadratic to ~2× under quadratic +
# elite bonus. First 3 picks read as marquee, not "slightly better
# than the next picks."
BID_QUAD_PIVOT: int = 25                  # talent floor — base is 0 here
BID_QUAD_SCALE: int = 1_00_000            # ƒ1 lakh × pivot²
BID_ELITE_PIVOT: int = 60                 # elite bonus kicks in here
BID_ELITE_SCALE: int = 10 * 1_00_000      # ƒ10 lakh × elite_pivot²


def _bid_base(overall: int) -> int:
    """Quadratic curve from overall → raw bid base (in guilders) with
    an elite-tier bonus stacked on top. Pure function so the test
    suite (and future calibration sweeps) can pin its shape."""
    o = int(overall or 0)
    pivot = max(0, o - BID_QUAD_PIVOT)
    base = pivot * pivot * BID_QUAD_SCALE
    elite = max(0, o - BID_ELITE_PIVOT)
    base += elite * elite * BID_ELITE_SCALE
    return base


def _team_auction_profile(team_row: dict) -> dict[str, float]:
    """Derive a per-team auction strategy from existing manager
    personality + organizational strength columns. The same profile
    applies across every lot the team bids on, so each team's bidding
    reads as a consistent strategy instead of freewheeling per-lot
    randomness.

    Inputs (already persisted on `teams` from league seed-time):
      * `org_strength`           20-95, league-mean 50.
      * `mgr_quick_hook`         0..1, sabermetric vs traditional.
      * `mgr_bullpen_aggression` 0..1, modern vs old-school.
      * `mgr_joker_aggression`   0..1, gambler vs patient.

    Outputs:
      * `discipline`   0..1 — high-org teams have tighter per-lot noise.
                       A 90-org team identifies true value; a 25-org
                       team disagrees with the market a lot.
      * `star_bias`    -0.5..+0.5 — positive = traditional-leaning,
                       overpays for marquee names; negative =
                       sabermetric-leaning, hunts arbitrage in the
                       depth tier.
      * `aggression`   0.85..1.35 — gambler/joker managers go big on
                       lots they want; patient managers pull back.
    """
    org = int(team_row.get("org_strength") or 50)
    org01 = max(0.0, min(1.0, (org - 20) / 75.0))   # 0..1 over the 20-95 range

    qh = float(team_row.get("mgr_quick_hook") or 0.5)
    ba = float(team_row.get("mgr_bullpen_aggression") or 0.5)
    ja = float(team_row.get("mgr_joker_aggression") or 0.5)

    # qh + ba both run 0=traditional → 1=sabermetric. Average them so
    # the "saber" axis is robust to per-axis noise.
    saber = (qh + ba) / 2.0
    star_bias = 0.5 - saber                         # +0.5..-0.5

    aggression = 0.85 + ja * 0.50                   # 0.85..1.35

    return {
        "discipline": org01,
        "star_bias":  star_bias,
        "aggression": aggression,
    }


# Per-lot cap as a fraction of remaining purse. Tranche cutoffs match
# the live-auction page's stage / montage split (50 stage lots first,
# then everything else) so the audience-facing pacing and the engine's
# pacing line up. Cutoff in `lot_order` (1-based, the same column the
# live page reads).
TRANCHE_CAPS: tuple[tuple[int | None, float], ...] = (
    ( 50, 0.50),   # marquee — up to 50% of remaining purse on one lot
    (200, 0.20),   # core    — up to 20%
    (500, 0.10),   # depth   — up to 10%
    (None, 0.04),  # roster-fill — 4%
)


def _per_lot_cap(purse_remaining: int, lot_order: int,
                 remaining_slots: int, min_bid: int) -> int:
    """Per-lot maximum bid. Soft cap is the tranche fraction of the
    team's remaining purse; hard cap leaves enough behind for the
    rest of the roster to clear at floor. Returns the binding (lower)
    of the two."""
    pct = TRANCHE_CAPS[-1][1]
    for cutoff, p in TRANCHE_CAPS:
        if cutoff is None or lot_order <= cutoff:
            pct = p
            break
    soft = int(purse_remaining * pct)
    floor_reserve = min_bid * max(0, remaining_slots - 1)
    hard = max(min_bid, purse_remaining - floor_reserve)
    return max(min_bid, min(soft, hard))


def _team_valuation_noisefree(player: dict, team_id: int,
                              profile: dict[str, float],
                              big_bid_pct: float = 0.0) -> int:
    """Deterministic willingness-to-pay for a player — same shape as
    `_team_bid` but with the per-lot noise term collapsed to 1.0 and
    no cap. Used by the post-clear trade phase: the question is "would
    team X pay more than the cleared price for this player on average,"
    not "did team X happen to roll a high noise this lot."

    `big_bid_pct` (default 0.0) is the team's largest single winning
    bid so far as a fraction of their purse; see `_apron_damper`. FA
    signing or post-clear trade callers can leave this at 0.0 since
    there's no in-auction history outside the auction loop.
    """
    overall = _player_overall(player)
    base = _bid_base(overall)

    over_50 = max(-25, overall - 50)
    star_warp = max(0.4, 1.0 + profile["star_bias"] * (over_50 / 17.0))
    base = int(base * star_warp)

    is_pitcher = bool(player.get("is_pitcher"))
    position = player.get("position", "P" if is_pitcher else "DH")
    need = _team_position_need(team_id, position, is_pitcher)
    need_mult = _need_multiplier(need)
    apron_mult = _apron_damper(big_bid_pct)

    return int(base * need_mult * apron_mult * profile["aggression"])


# Sellback / post-clear trade thresholds. The buyer's noise-free
# valuation must exceed the cleared price by at least
# (1 + TRADE_THRESHOLD) for the trade to fire — this keeps the engine
# from churning on every lot, only triggering when the would-be buyer
# clearly values the player higher than the noisy original auction
# allocated. Trade clears at the midpoint, so both sides realise
# surplus.
#
# Two hard caps prevent runaway churning that leaves teams full at
# the roster cap with surplus cash forfeit:
#   * Trades only fire in the first TRADE_LOT_LIMIT lots — past the
#     marquee tier, the auction settles and rosters can't be flipped.
#   * Each team can sell at most TRADE_SALES_PER_TEAM player(s)
#     across the whole auction. One sale per team is the natural
#     "draft trade-back" rhythm — pick someone for another team and
#     get cash, then commit to your remaining picks.
TRADE_THRESHOLD: float = 0.05    # buyer must value at ≥ 5% above winning bid
TRADE_LOT_LIMIT: int   = 50      # trades only allowed in lots 1..50
TRADE_SALES_PER_TEAM: int = 1    # max sellbacks per team per auction


def _apron_damper(big_bid_pct: float) -> float:
    """NBA-apron-style escalating penalty for whale-bid behavior.

    The signal is the team's LARGEST single winning bid so far in the
    auction, as a fraction of their initial purse. Cumulative spend
    isn't the right axis here: a team eating on lots of low-tier
    talent isn't the same problem as a team dropping huge bids to
    sweep up the stars. The position-need damper already handles the
    "13 cheap lots at one position" stacking failure; the apron is
    aimed specifically at the whale who keeps outbidding on marquee
    lots.

    Once a team has made a notably big bid, subsequent bids get
    damped progressively — so the whale gets one star, then has to
    sit out future star bidding while less-spent teams take the rest.

        peak ≤ 5%:   1.00   (no marquee bid yet — no premium)
        peak ≤ 15%:  0.85
        peak ≤ 30%:  0.65
        peak ≤ 50%:  0.45
        peak ≤ 70%:  0.30
        peak >  70%: 0.20   (clear whale — effectively out of the running)
    """
    if big_bid_pct <= 0.05: return 1.00
    if big_bid_pct <= 0.15: return 0.85
    if big_bid_pct <= 0.30: return 0.65
    if big_bid_pct <= 0.50: return 0.45
    if big_bid_pct <= 0.70: return 0.30
    return 0.20


def _team_pressure(purse_remaining: int, lot_order: int,
                   auction_lot_limit: int, purse_init: int) -> float:
    """How much surplus does a team have vs expected linear depletion?

    Auction budget doesn't roll over to the regular season, so a team
    sitting on a pile of cash with the auction running out has every
    incentive to overpay rather than let the money disappear. Returns
    a multiplier on aggression: 1.0 = on the linear track, > 1.0 =
    sitting on cash and should bid hotter, capped at 2.5× so the
    pressure doesn't go infinite at the very end of the auction.

    Surplus only ever comes from sellbacks (a team that flipped a player
    for more than they paid). The pressure factor is what closes the
    "no rollover ⇒ overpay later" loop the brief described.
    """
    if auction_lot_limit <= 0 or purse_init <= 0:
        return 1.0
    fraction_done = min(1.0, lot_order / auction_lot_limit)
    # Expected purse decays linearly from purse_init to 5% over the
    # auction phase — never to 0, so the ratio doesn't blow up.
    expected = purse_init * max(0.05, 1.0 - fraction_done)
    return max(1.0, min(2.5, purse_remaining / expected))


def _team_bid(player: dict, team_id: int, purse_remaining: int,
              n_keepers: int, rng: random.Random,
              *, min_bid: int, lot_order: int,
              profile: dict[str, float],
              pressure: float = 1.0,
              big_bid_pct: float = 0.0) -> int:
    """Compute a single team's private valuation for a player, in
    guilders. The team's `profile` (from `_team_auction_profile`) is
    fixed for the whole auction — the only per-lot randomness is the
    final noise term.

    Formula:
      raw_base    = _bid_base(overall)
      star_warp   = 1.0 + profile.star_bias × (overall - 50) / 17.0
                    (positive → curve steepens at the top; negative →
                    flattens, value-hunters pay up on depth instead)
      need_mult   = 1.0 + 0.15 × position_need (capped at 1.5)
      noise_band  = uniform(0.45, 1.50) for a 0-discipline team;
                    tightens to ~uniform(0.85, 1.10) for a 1-discipline
                    team. High-org teams converge on true value.
      max_bid     = floor(raw_base × star_warp × need_mult ×
                          aggression × noise)

    The team won't overspend relative to remaining slot needs — it
    reserves at least `min_bid` per remaining slot so the rest of the
    roster can clear without bricking.
    """
    overall = _player_overall(player)
    base = _bid_base(overall)

    over_50 = max(-25, overall - 50)
    star_warp = max(0.4, 1.0 + profile["star_bias"] * (over_50 / 17.0))
    base = int(base * star_warp)

    is_pitcher = bool(player.get("is_pitcher"))
    position = player.get("position", "P" if is_pitcher else "DH")
    need = _team_position_need(team_id, position, is_pitcher)
    need_mult = _need_multiplier(need)
    apron_mult = _apron_damper(big_bid_pct)

    # Noise band tightens with discipline. The full ±55%/+50% band
    # lives at the bottom-end org-strength; well-run franchises (org
    # ≈ 90) shrink to roughly ±15%, mirroring the existing in-game
    # manager `noise` attribute on Archetype but applied to the
    # auction's valuation step.
    half_lo = 0.55 - 0.40 * profile["discipline"]   # 0.55 → 0.15
    half_hi = 0.50 - 0.40 * profile["discipline"]   # 0.50 → 0.10
    noise = rng.uniform(1.0 - half_lo, 1.0 + half_hi)

    effective_aggression = profile["aggression"] * pressure
    max_bid = int(base * need_mult * apron_mult * effective_aggression * noise)

    remaining_slots = max(1, ROSTER_TARGET - n_keepers)
    cap = _per_lot_cap(purse_remaining, lot_order, remaining_slots, min_bid)
    return max(0, min(max_bid, cap))


def _guarantee_min_roster(team_ids: list[int]) -> list[dict]:
    """Top up any team the auction left short from the free-agent pool.

    Guarantees each team exits with: every canonical hitter position covered,
    >= AUCTION_MIN_PITCHERS arms, and >= AUCTION_MIN_ROSTER total players.
    Signs best-available FAs (team_id IS NULL) per need, marking them active.
    Returns a list of {team_id, player_id, detail} signing events. Pitching
    roles are re-derived for any team that gained an arm."""
    from o27v2 import rotation as _rotation
    events: list[dict] = []
    touched_pitching: set[int] = set()

    for tid in team_ids:
        roster = [dict(r) for r in db.fetchall(
            "SELECT id, position, is_pitcher, COALESCE(is_joker,0) AS is_joker "
            "FROM players WHERE team_id = ?", (tid,))]
        n_total = len(roster)
        n_pit = sum(1 for p in roster if p["is_pitcher"])
        have_pos = {p["position"] for p in roster
                    if not p["is_pitcher"] and not p["is_joker"]}
        need_pos = [pos for pos in _CANONICAL_POSITIONS if pos not in have_pos]
        need_pit = max(0, AUCTION_MIN_PITCHERS - n_pit)
        if not need_pos and need_pit == 0 and n_total >= AUCTION_MIN_ROSTER:
            continue

        # Free-agent pool, bucketed.
        fa = [dict(r) for r in db.fetchall(
            "SELECT * FROM players WHERE team_id IS NULL")]
        fa_pit = sorted((p for p in fa if p["is_pitcher"]),
                        key=_player_overall, reverse=True)
        fa_pos_by: dict[str, list[dict]] = {}
        for p in fa:
            if not p["is_pitcher"] and not (p.get("is_joker") or 0):
                fa_pos_by.setdefault(p["position"], []).append(p)
        for lst in fa_pos_by.values():
            lst.sort(key=_player_overall, reverse=True)

        signed: set[int] = set()
        to_sign: list[dict] = []
        # 1) Cover any missing canonical position.
        for pos in need_pos:
            pick = next((p for p in fa_pos_by.get(pos, [])
                         if p["id"] not in signed), None)
            if pick:
                to_sign.append(pick); signed.add(pick["id"])
        # 2) Reach the pitcher floor.
        for p in fa_pit:
            if need_pit <= 0:
                break
            if p["id"] in signed:
                continue
            to_sign.append(p); signed.add(p["id"]); need_pit -= 1
        # 3) Fill to the total floor with best available.
        remaining = AUCTION_MIN_ROSTER - (n_total + len(to_sign))
        if remaining > 0:
            rest = sorted((p for p in fa if p["id"] not in signed),
                          key=_player_overall, reverse=True)
            for p in rest[:remaining]:
                to_sign.append(p); signed.add(p["id"])

        for p in to_sign:
            db.execute("UPDATE players SET team_id = ?, is_active = 1 WHERE id = ?",
                       (tid, p["id"]))
            if p["is_pitcher"]:
                touched_pitching.add(tid)
            events.append({
                "team_id":   tid,
                "player_id": p["id"],
                "detail":    f"Auction floor signing: {p.get('name', '?')} "
                             f"({p.get('position', '?')}) — roster top-up from FA pool",
            })

    for tid in touched_pitching:
        _rotation.assign_roles_for_team(tid)
    return events


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
    # Defaults are guilder-era: 200 cr per team, 50 lakh min-bid.
    # See `BID_QUAD_SCALE` and `TRANCHE_CAPS` for the per-lot scaling
    # and tranche-based cap that together produce the IPL-shape: top
    # stars in the ƒ70-100 cr range, mid-tier at ƒ15-30 cr, depth at
    # ƒ2-5 cr, replacement-tier unsold or at floor.
    purse_init = int(cfg_a.get("team_purse", 200 * 1_00_00_000))   # 200 cr
    min_bid    = int(cfg_a.get("min_bid",       50 * 1_00_000))    # 50 lakh
    # Top N lots (sorted by composite) go through the bidding loop;
    # the rest fill rosters via snake draft at min_bid. Splits the
    # auction into "real price discovery" (top tier) and "depth
    # roster fill" (everyone else) — the floor-tier doesn't need
    # bidding theatre, just an order.
    auction_lot_limit = int(cfg_a.get("auction_lot_limit", 500))

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

    # Sample each team's auction profile once. Stable across every
    # lot — the auction reads as a consistent personality per team
    # rather than freewheeling per-lot randomness.
    profiles = {t["id"]: _team_auction_profile(t) for t in teams}

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

    # Split: top N go through bidding, the rest go through snake-draft
    # at min_bid. Players further down the list will only fill open
    # roster slots — anything left unallocated becomes a free agent.
    auction_pool = pool[:auction_lot_limit]
    draft_pool   = pool[auction_lot_limit:]

    # Step 3: per-team purse, slot tracking, auction loop.
    # If economy.team_budgets is initialized for this season, each team's
    # starting purse is THEIR specific remaining budget (fantasy-style
    # per-team caps). Otherwise everyone shares `purse_init` from the
    # auction config (legacy behavior).
    initial_purses = _initial_purses_from_budgets(season, team_ids, purse_init)
    purse = dict(initial_purses)
    # Apron tracker: each team's biggest single winning bid so far, in
    # guilders. Used to compute `big_bid_pct` = peak / purse_init, fed
    # into `_apron_damper` so the whale who outbids on stars gets
    # damped on subsequent lots.
    biggest_win: dict[int, int] = {tid: 0 for tid in team_ids}
    won_by_team: dict[int, list[dict]] = {tid: [] for tid in team_ids}
    # Per-team sales count for the sellback mechanic. Capped at 1 so
    # no team can churn dozens of player → cash flips and exit the
    # auction with massive surplus. One sale per team across the
    # whole auction; the team picks the timing implicitly via "first
    # qualifying offer wins."
    sales_count: dict[int, int] = {tid: 0 for tid in team_ids}
    log: list[dict] = []
    unsold = 0

    lot_order = 0
    for player in auction_pool:
        lot_order += 1
        player_overall = _player_overall(player)

        # Each team makes one private bid.
        bids: list[tuple[int, int]] = []  # (bid_amount, team_id)
        for tid in team_ids:
            keepers_on_team = len(keepers_by_team.get(tid, []))
            won_so_far = len(won_by_team[tid])
            slots_filled = keepers_on_team + won_so_far
            if slots_filled >= ROSTER_DRAFT_CAP:  # draft-day cap (cut later)
                continue
            if purse[tid] < min_bid:
                continue
            tp = _team_pressure(purse[tid], lot_order,
                                auction_lot_limit, purse_init)
            # Apron damper key: biggest single winning bid this team has
            # made so far, as fraction of their starting purse. A team
            # that's been winning cheap lots has a low peak and pays no
            # premium; a whale who already dropped a marquee bid is
            # damped on subsequent lots.
            init_p = max(1, initial_purses.get(tid, purse_init))
            big_bid_pct = biggest_win[tid] / init_p
            bid = _team_bid(player, tid, purse[tid],
                            n_keepers + won_so_far, rng,
                            min_bid=min_bid,
                            lot_order=lot_order,
                            profile=profiles[tid],
                            pressure=tp,
                            big_bid_pct=big_bid_pct)
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
        # Apron bookkeeping: bump the team's peak winning bid so the
        # damper kicks in on subsequent lots for whales.
        if price > biggest_win[winner_tid]:
            biggest_win[winner_tid] = price

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

        # Sellback / post-clear trade: any other team whose noise-free
        # valuation exceeds the cleared price by ≥ TRADE_THRESHOLD can
        # buy the player off the original winner at the midpoint of
        # (winning_bid, buyer_valuation). Cash flows to the seller, who
        # uses it (or doesn't — no rollover) on later lots.
        #
        # Two hard caps gate the trade: the lot must be inside the
        # marquee window (lot_order ≤ TRADE_LOT_LIMIT), and the seller
        # mustn't already be at their per-team sale cap. Without these
        # the auction churns — teams flip players endlessly, end up
        # full at the roster cap, and forfeit surplus cash they
        # couldn't deploy.
        traded_to_tid: int | None = None
        traded_to_abbrev = ""
        trade_price: int | None = None

        trade_eligible = (lot_order <= TRADE_LOT_LIMIT
                          and sales_count[winner_tid] < TRADE_SALES_PER_TEAM)

        # The trade fires when another team's noise-free valuation
        # exceeds the auction winner's noise-free valuation — i.e.,
        # the wrong team won the lot because the winner's noise rolled
        # high, not because they actually wanted the player most.
        # Trade clears at the midpoint of (winner_val, buyer_val), so
        # both sides extract surplus relative to their reservation
        # price. The original winning_bid is sunk cost for the seller —
        # what matters is whether the cash they'd receive exceeds
        # their own valuation of the player.
        w_init_p = max(1, initial_purses.get(winner_tid, purse_init))
        winner_val = _team_valuation_noisefree(
            player, winner_tid, profiles[winner_tid],
            big_bid_pct=biggest_win[winner_tid] / w_init_p,
        )
        val_threshold = int(winner_val * (1.0 + TRADE_THRESHOLD))

        candidates: list[tuple[int, int]] = []
        if trade_eligible:
            for tid in team_ids:
                if tid == winner_tid:
                    continue
                slots_filled = (len(keepers_by_team.get(tid, []))
                                + len(won_by_team[tid]))
                if slots_filled >= ROSTER_DRAFT_CAP:
                    continue
                buyer_pressure = _team_pressure(
                    purse[tid], lot_order, auction_lot_limit, purse_init,
                )
                b_init_p = max(1, initial_purses.get(tid, purse_init))
                val = int(_team_valuation_noisefree(
                              player, tid, profiles[tid],
                              big_bid_pct=biggest_win[tid] / b_init_p)
                          * buyer_pressure)
                if val >= val_threshold:
                    candidates.append((val, tid))

        if candidates:
            candidates.sort(reverse=True)
            buyer_val, buyer_tid = candidates[0]
            mid = (winner_val + buyer_val) // 2
            t_price = min(mid, purse[buyer_tid])
            # Seller's reservation: their own noise-free valuation. They
            # only sell if the cash exceeds what they value the player at.
            if t_price > winner_val:
                purse[buyer_tid] -= t_price
                purse[winner_tid] += t_price
                # Move the player off the original winner's roster.
                won_by_team[winner_tid] = [
                    p for p in won_by_team[winner_tid]
                    if p["id"] != player["id"]
                ]
                won_by_team[buyer_tid].append(player)
                sales_count[winner_tid] += 1
                # Apron bookkeeping: the buyer just committed `t_price`
                # — that's a real big-bid signal, bump their peak.
                if t_price > biggest_win[buyer_tid]:
                    biggest_win[buyer_tid] = t_price
                traded_to_tid = buyer_tid
                trade_price = t_price
                buyer_team = next(
                    (t for t in teams if t["id"] == buyer_tid), None
                )
                traded_to_abbrev = buyer_team["abbrev"] if buyer_team else ""
                db.execute(
                    "UPDATE auction_results "
                    "SET traded_to_team_id = ?, trade_price = ? "
                    "WHERE season = ? AND lot_order = ?",
                    (buyer_tid, t_price, season, lot_order),
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
            "traded_to_team_id": traded_to_tid,
            "traded_to_abbrev":  traded_to_abbrev,
            "trade_price":       trade_price,
            "result":         "traded" if traded_to_tid else "sold",
        })

    # Step 3b: snake-draft phase. Roster-fill players (those past the
    # auction-lot-limit) get assigned in snake order, no bidding —
    # everyone pays min_bid. Order is by team_id; round 1 forward,
    # round 2 reverse, etc. Teams already at the active+reserve cap
    # are skipped. Anything that can't be placed becomes a free agent.
    n_drafted = 0
    if draft_pool:
        snake_idx = 0
        snake_dir = 1
        team_count = len(team_ids)

        def _team_has_slot(tid: int) -> bool:
            slots_filled = (len(keepers_by_team.get(tid, []))
                            + len(won_by_team[tid]))
            return slots_filled < ROSTER_DRAFT_CAP

        def _advance() -> bool:
            """Move snake_idx to the next pickable team. Returns False
            if every team is full."""
            nonlocal snake_idx, snake_dir
            for _ in range(team_count * 2):
                next_idx = snake_idx + snake_dir
                if next_idx >= team_count:
                    snake_dir = -1            # bounce; same team picks again
                    next_idx = team_count - 1
                    if snake_idx == next_idx:  # already there → step inward
                        next_idx = team_count - 2 if team_count > 1 else 0
                elif next_idx < 0:
                    snake_dir = 1
                    next_idx = 0
                    if snake_idx == next_idx:
                        next_idx = 1 if team_count > 1 else 0
                snake_idx = next_idx
                if _team_has_slot(team_ids[snake_idx]):
                    return True
            return False

        # Make sure the starting team has a slot. If not, advance.
        if not _team_has_slot(team_ids[snake_idx]) and not _advance():
            draft_pool = []  # everyone full

        for player in draft_pool:
            lot_order += 1
            player_overall = _player_overall(player)
            tid = team_ids[snake_idx]

            # Variable-cost snake fill: spread any leftover auction
            # purse evenly across the team's remaining open slots so
            # surplus cash gets sopped up by overpaying for floor-tier
            # players rather than forfeit. Floor: min_bid. A team that
            # ran its purse close to zero in the auction phase pays
            # the floor on every snake pick; a team sitting on ƒ50 cr
            # of un-deployed cash with 30 open slots pays ƒ1.7 cr per
            # pick — match the brief's "no money should leave the
            # auction unspent."
            slots_filled = (len(keepers_by_team.get(tid, []))
                            + len(won_by_team[tid]))
            open_slots = max(1, ROSTER_DRAFT_CAP - slots_filled)
            price = max(min_bid, purse[tid] // open_slots)
            price = min(price, purse[tid])  # never spend more than we have
            purse[tid] = max(0, purse[tid] - price)
            won_by_team[tid].append(player)
            n_drafted += 1

            winner_team = next((t for t in teams if t["id"] == tid), None)
            winner_abbrev = winner_team["abbrev"] if winner_team else ""

            db.execute(
                "INSERT INTO auction_results "
                "(season, lot_order, player_id, player_overall, "
                " winner_team_id, winning_bid, second_bid, price) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                (season, lot_order, player["id"], player_overall,
                 tid, price, price),
            )
            # Persist a single-bid sheet so the live-auction UI can
            # render the pick without hitting a special-case.
            db.execute(
                "INSERT INTO auction_lot_bids "
                "(season, lot_order, team_id, bid, rank) "
                "VALUES (?, ?, ?, ?, 1)",
                (season, lot_order, tid, price),
            )
            log.append({
                "player_id":     player["id"],
                "player_name":   player["name"],
                "position":      player["position"],
                "overall":       player_overall,
                "lot_order":     lot_order,
                "winner_team_id": tid,
                "winner_abbrev":  winner_abbrev,
                "winning_bid":    price,
                "second_bid":     None,
                "price":          price,
                "result":         "drafted",
            })

            # Advance the snake. If we're now out of teams with slots,
            # remaining draft_pool players become unsold.
            if not _advance():
                # Mark every leftover player as unsold and stop.
                idx_at_break = draft_pool.index(player) + 1
                for leftover in draft_pool[idx_at_break:]:
                    lot_order += 1
                    db.execute(
                        "INSERT INTO auction_results "
                        "(season, lot_order, player_id, player_overall, "
                        " winner_team_id, winning_bid, second_bid, price) "
                        "VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL)",
                        (season, lot_order, leftover["id"],
                         _player_overall(leftover)),
                    )
                    unsold += 1
                break

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

    # Step 5: minimum-roster guarantee. Top up any team the auction left short
    # from the free-agent pool so no club exits with a skeleton roster.
    floor_signings = _guarantee_min_roster(team_ids)
    if floor_signings:
        from o27v2.transactions import log_many
        from datetime import date as _date
        log_many(season, _date.today().isoformat(),
                 [{"event_type": "auction_floor_signing",
                   "team_id": s["team_id"], "player_id": s["player_id"],
                   "detail": s["detail"]} for s in floor_signings])

    # Per-team summary for the UI. Spent is the per-team delta against
    # THAT team's initial purse (which may be its own team_budget remaining
    # when economy is configured, not the global purse_init).
    summary = []
    for t in teams:
        won = won_by_team[t["id"]]
        spent = initial_purses.get(t["id"], purse_init) - purse[t["id"]]
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

    # Emit auction_sign transaction rows for every winner — feeds the
    # /transactions page and the player-card history tab.
    _emit_auction_signs(season, log)

    # Persist each team's net auction spend into team_budgets so the
    # league economy stays in sync with the auction outcome.
    _persist_auction_spend(season, initial_purses, purse)

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


def _college_map(player_ids: list[int]) -> dict[int, dict]:
    """Batch lookup: pro_player_id → {short, name} of the college
    program they were signed from. Returns {} for any pro player
    without a college backlink, and {} entirely if the college tier
    isn't present in this save."""
    if not player_ids:
        return {}
    has_college = db.fetchone(
        "SELECT 1 AS x FROM sqlite_master "
        "WHERE type='table' AND name='college_players'"
    )
    if not has_college:
        return {}
    out: dict[int, dict] = {}
    CHUNK = 500
    for i in range(0, len(player_ids), CHUNK):
        chunk = player_ids[i:i + CHUNK]
        qs = ",".join("?" for _ in chunk)
        rows = db.fetchall(
            f"SELECT cp.signed_pro_player_id AS pid, "
            f"       prg.short_name AS short, prg.name AS name "
            f"FROM college_players cp "
            f"JOIN college_programs prg ON prg.id = cp.program_id "
            f"WHERE cp.signed_pro_player_id IN ({qs})",
            tuple(chunk),
        )
        for r in rows:
            out[int(r["pid"])] = {"short": r["short"], "name": r["name"]}
    return out


def preview_auction(n_keepers: int = 3) -> dict[str, Any]:
    """Dry-run the keeper-selection step + snapshot the FA pool so the
    UI can show, BEFORE running the auction, exactly who's eligible.

    Returns:
      {
        "keepers": [{"team_id", "team_abbrev", "team_name",
                     "players": [{id,name,position,overall,is_pitcher}]}],
        "pool":    same shape as keepers — non-keeper rostered players,
                   grouped by current team, who'll get auctioned.
        "free_agents": flat list of players with team_id IS NULL.
        "n_keepers", "n_pool", "n_free_agents": counts.
      }
    """
    teams = db.fetchall(
        "SELECT id, abbrev, name FROM teams ORDER BY id"
    )

    keepers_out: list[dict] = []
    pool_out: list[dict] = []
    n_keepers_total = 0
    n_pool_total = 0
    # Pre-fetch every roster row so we can do a single college-lookup
    # for everyone (including FAs below) rather than per-team queries.
    all_rostered = db.fetchall(
        "SELECT * FROM players WHERE team_id IS NOT NULL"
    )
    by_team: dict[int, list[dict]] = {}
    for r in all_rostered:
        by_team.setdefault(r["team_id"], []).append(dict(r))
    all_ids = [r["id"] for r in all_rostered]
    fa_rows_raw = db.fetchall("SELECT * FROM players WHERE team_id IS NULL")
    all_ids.extend(int(r["id"]) for r in fa_rows_raw)
    college = _college_map(all_ids)

    def _shape(plist):
        return [{
            "id":       p["id"],
            "name":     p["name"],
            "position": p.get("position"),
            "is_pitcher": bool(p.get("is_pitcher")),
            "is_active":  bool(p.get("is_active")),
            "overall":  _player_overall(p),
            "college":  college.get(int(p["id"])),
        } for p in plist]

    for t in teams:
        roster = by_team.get(t["id"], [])
        ranked = sorted(roster, key=lambda p: -_player_overall(p))
        keep = ranked[:n_keepers]
        rest = ranked[n_keepers:]
        keepers_out.append({
            "team_id":     t["id"],
            "team_abbrev": t["abbrev"],
            "team_name":   t["name"],
            "players":     _shape(keep),
        })
        pool_out.append({
            "team_id":     t["id"],
            "team_abbrev": t["abbrev"],
            "team_name":   t["name"],
            "players":     _shape(rest),
        })
        n_keepers_total += len(keep)
        n_pool_total += len(rest)

    free_agents = sorted(
        ({
            "id":         r["id"],
            "name":       r["name"],
            "position":   r["position"] if "position" in r.keys() else None,
            "is_pitcher": bool(r["is_pitcher"]),
            "overall":    _player_overall(dict(r)),
            "college":    college.get(int(r["id"])),
        } for r in fa_rows_raw),
        key=lambda p: -p["overall"],
    )

    return {
        "keepers":       keepers_out,
        "pool":          pool_out,
        "free_agents":   free_agents,
        "n_keepers":     n_keepers_total,
        "n_pool":        n_pool_total,
        "n_free_agents": len(free_agents),
    }


def apply_roster_cut(season: int | None = None) -> dict[str, Any]:
    """Roster cut day. Trim every team back to ROSTER_FINAL_CAP = 47.

    During the auction (and the trade window that follows it) teams
    can carry up to ROSTER_DRAFT_CAP = 50 so they have slack to make
    trades and shuffle roster spots. This step enforces the regular-
    season cap by releasing the lowest-overall reserves to the FA pool.
    Active players (is_active=1) are never cut — only the deep bench
    is exposed.

    Idempotent: re-running on an already-trimmed league is a no-op.
    """
    if season is None:
        row = db.fetchone(
            "SELECT value FROM sim_meta WHERE key = 'season_number'"
        )
        try:
            season = int((row or {}).get("value") or 1)
        except (TypeError, ValueError):
            season = 1

    teams = db.fetchall("SELECT id, abbrev, name FROM teams ORDER BY id")
    cuts: list[dict] = []
    for t in teams:
        roster = db.fetchall(
            "SELECT * FROM players WHERE team_id = ?", (t["id"],)
        )
        if len(roster) <= ROSTER_FINAL_CAP:
            continue
        rl = [dict(r) for r in roster]
        # Sort: actives first (never cut), then reserves by overall desc.
        # Tail past ROSTER_FINAL_CAP is what gets released.
        rl.sort(key=lambda p: (-(p.get("is_active") or 0),
                               -_player_overall(p)))
        keep = rl[:ROSTER_FINAL_CAP]
        release = rl[ROSTER_FINAL_CAP:]
        for p in release:
            db.execute(
                "UPDATE players SET team_id = NULL, is_active = 0 "
                "WHERE id = ?", (p["id"],),
            )
            cuts.append({
                "team_id":     t["id"],
                "team_abbrev": t["abbrev"],
                "team_name":   t["name"],
                "player_id":   p["id"],
                "player_name": p["name"],
                "position":    p.get("position"),
                "overall":     _player_overall(p),
            })

    if cuts:
        from o27v2.transactions import log_many
        from datetime import date as _date
        events = [{
            "event_type": "roster_cut",
            "team_id":    c["team_id"],
            "player_id":  c["player_id"],
            "detail":     "Roster cut day — released to FA pool",
        } for c in cuts]
        log_many(season, _date.today().isoformat(), events)

    return {
        "ok":     True,
        "season": season,
        "cap":    ROSTER_FINAL_CAP,
        "draft_cap": ROSTER_DRAFT_CAP,
        "cuts":   cuts,
        "n_cut":  len(cuts),
    }


def _emit_auction_signs(season: int, log: list[dict]) -> None:
    """Log one `auction_sign` transaction per winning lot."""
    from o27v2.transactions import log_many
    from datetime import date as _date
    today = _date.today().isoformat()
    events = []
    for entry in log:
        if entry.get("result") != "sold":
            continue
        tid = entry.get("winner_team_id")
        pid = entry.get("player_id")
        price = entry.get("price")
        if tid is None or pid is None:
            continue
        events.append({
            "event_type": "auction_sign",
            "team_id":    tid,
            "player_id":  pid,
            "detail":     f"Won at auction for ƒ{price}" if price else "Won at auction",
        })
    if events:
        log_many(season, today, events)


def _initial_purses_from_budgets(season: int, team_ids: list[int],
                                  fallback_purse: int) -> dict[int, int]:
    """If league_economy / team_budgets are configured, return each
    team's REMAINING budget as their auction starting purse. Otherwise
    fall back to `fallback_purse` for every team (legacy behavior)."""
    try:
        from o27v2 import economy as _econ
        # Has the user actually saved any economy config for this season?
        from o27v2 import db as _db
        has_econ = _db.fetchone(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_budgets'"
        )
        if not has_econ:
            return {tid: fallback_purse for tid in team_ids}
        _econ.init_budgets(season)
        return {tid: _econ.budget_for_team(tid, season)["remaining"]
                for tid in team_ids}
    except Exception:
        return {tid: fallback_purse for tid in team_ids}


def _persist_auction_spend(season: int, initial_purses: dict[int, int],
                            final_purses: dict[int, int]) -> None:
    """Walk each team's spend (initial - remaining) and deduct from
    team_budgets so the per-season ledger stays accurate. Silent no-op
    when economy isn't configured."""
    try:
        from o27v2 import economy as _econ
        from o27v2 import db as _db
        has_econ = _db.fetchone(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_budgets'"
        )
        if not has_econ:
            return
        for tid, initial in initial_purses.items():
            remaining = final_purses.get(tid, initial)
            spent = max(0, initial - remaining)
            if spent > 0:
                _econ.deduct(tid, season, spent, allow_overdraft=True)
    except Exception:
        pass


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
        "       p.country AS player_country, "
        "       wt.abbrev AS winner_abbrev, wt.name AS winner_name, "
        "       tt.abbrev AS traded_to_abbrev, tt.name AS traded_to_name "
        "FROM auction_results r "
        "JOIN players p ON p.id = r.player_id "
        "LEFT JOIN teams wt ON wt.id = r.winner_team_id "
        "LEFT JOIN teams tt ON tt.id = r.traded_to_team_id "
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
        traded_to_tid = r["traded_to_team_id"]
        trade_price   = r["trade_price"]
        if r["winner_team_id"]:
            result_kind = "traded" if traded_to_tid else "sold"
        else:
            result_kind = "unsold"
        # Final owner = traded-to (if a trade occurred) else original winner.
        final_owner_tid    = traded_to_tid or r["winner_team_id"]
        final_owner_abbrev = (r["traded_to_abbrev"]
                              if traded_to_tid else r["winner_abbrev"]) or ""
        return {
            "lot_order":      r["lot_order"],
            "player_id":      r["player_id"],
            "player_name":    r["player_name"],
            "player_country": (r["player_country"] or "").upper(),
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
            "traded_to_team_id":  traded_to_tid,
            "traded_to_abbrev":   r["traded_to_abbrev"] or "",
            "traded_to_name":     r["traded_to_name"] or "",
            "trade_price":        trade_price,
            "final_owner_team_id":  final_owner_tid,
            "final_owner_abbrev":   final_owner_abbrev,
            "result":         result_kind,
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
        # Spend ledger is the *net* cash out per team. Original winner
        # paid `price`; if they later sold the player on, refund that
        # team and bill the buyer the trade price. Won-count goes to
        # the final roster owner so the purse-rollup line reads as
        # "what teams ended up with."
        if s["winner_team_id"] and s["price"]:
            spent_by_team[s["winner_team_id"]] += s["price"]
        if s["traded_to_team_id"] and s["trade_price"]:
            spent_by_team[s["winner_team_id"]] -= s["trade_price"]
            spent_by_team[s["traded_to_team_id"]] += s["trade_price"]
        owner = s["final_owner_team_id"]
        if owner:
            won_by_team[owner] = won_by_team.get(owner, 0) + 1
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
        "       r.second_bid, r.price, "
        "       r.traded_to_team_id, r.trade_price, "
        "       p.name AS player_name, p.position, p.is_pitcher, "
        "       t.abbrev AS winner_abbrev, t.name AS winner_name, "
        "       tt.abbrev AS traded_to_abbrev, tt.name AS traded_to_name "
        "FROM auction_results r "
        "JOIN players p ON p.id = r.player_id "
        "LEFT JOIN teams t  ON t.id  = r.winner_team_id "
        "LEFT JOIN teams tt ON tt.id = r.traded_to_team_id "
        "WHERE r.season = ? "
        "ORDER BY r.winning_bid DESC NULLS LAST, r.id",
        (season,),
    )

    if not keepers and not results:
        return None

    # Tag any player that came from the college pipeline with their
    # program so the UI can show e.g. "← NSH" alongside the player name.
    all_ids = [int(r["player_id"]) for r in keepers] + [int(r["player_id"]) for r in results]
    college = _college_map(list(set(all_ids)))
    keepers_out = []
    for r in keepers:
        d = dict(r)
        d["college"] = college.get(int(r["player_id"]))
        keepers_out.append(d)
    results_out = []
    for r in results:
        d = dict(r)
        d["college"] = college.get(int(r["player_id"]))
        results_out.append(d)
    return {
        "season":  season,
        "keepers": keepers_out,
        "results": results_out,
    }
