"""
Phase E — Post-auction trade reconciliation.

After the auction + FA signing rounds settle the roster ecosystem,
plenty of teams end up with depth-chart pathologies the bidding
engine couldn't unwind:

  * A talented backup buried behind a star at the same position
    (would start somewhere else, can't crack the lineup here).
  * Two teams whose starting lineups would improve if they swapped
    a star apiece — same OVR magnitude, different positions, both
    teams need what the other has.
  * A surplus team with multiple over-target depth pieces who could
    cash one in for a need-fill at a different position.
  * Three-team cycles where A's blocked talent fits B, B's fits C,
    C's fits A — none of those swaps work as a 2-team deal, but the
    cycle closes when all three move.

This module runs a one-shot reconciliation pass that detects these
patterns and fires the trades. It's distinct from `o27v2.trades`,
which handles in-season trade-deadline motivations — this is a
post-auction settling pass with a different objective (depth-chart
sanity, not winning-now/rebuilding).

Trade classes (highest score wins each iteration):
  blockbuster      — both teams have blocked talents that swap; both
                     post-trade depth charts unlock a starter.
  star_for_star    — both teams' starters swap (no blocking needed);
                     each team's noise-free valuation says the inbound
                     starter strictly beats the outbound at that pos.
  3_cycle          — A→B→C→A blocked-talent cycle across 3 distinct
                     positions. All three teams unlock a depth slot.
  surplus          — A has need ≤ -2 at P; trades 2nd-best chip there
                     for B's best player at A's most-needed position.
  arbitrage        — A's player is valued markedly higher by B than A
                     (noise-free), and B has a return asset A values
                     similarly. Catches everything the structured
                     classes miss.

Each iteration: enumerate every candidate of every class, score, fire
the top one (subject to per-team cap + talent-floor gates), then
re-build the depth-chart + edge index from CURRENT roster state and
re-enumerate. This prevents double-dipping a player and prevents
draining a position to thinness via a stale-state-driven misfire.

Talent floors (configurable but defaulted at module scope):
  STARTER_FLOOR    65   — "would start on some other team"
  BLOCKBUSTER_TAG  75   — both inbound players ≥ this → "blockbuster"
  MARQUEE_TAG      80   — both ≥ this → "marquee" subtag

Stops when no candidate clears the both-sides-improve threshold or
all eligible teams have hit their per-team cap.
"""
from __future__ import annotations

from typing import Any, Iterable
import random
from collections import defaultdict

from o27v2 import db
from o27v2 import auction as _au


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

STARTER_FLOOR:   int = 65   # OVR threshold for "would start elsewhere"
BLOCKBUSTER_TAG: int = 75   # both inbound ≥ this → blockbuster
MARQUEE_TAG:     int = 80   # both ≥ this → marquee subtag

# Talent-symmetry tolerance: how close in OVR the two (or three) moving
# players must be. 2-team cycles are tighter than 3-team (3-team is
# rarer + more valuable, worth more slack).
SYM_2TEAM: int = 5
SYM_3TEAM: int = 7

# Per-team cap on trades per reconciliation pass. Higher → more churn,
# lower → conservative. 2 lets a team do one depth-chart fix + one
# marquee move without spiraling.
PER_TEAM_CAP: int = 2

# Safety: hard upper bound on iterations so a misconfigured threshold
# can't infinite-loop. Each iteration fires at most one trade, so this
# also caps total trades.
MAX_ITERATIONS: int = 200

# 3-cycle score bonus — three-way deals are organically rarer and
# worth surfacing more aggressively.
CYCLE_3_BONUS: float = 1.25


# ---------------------------------------------------------------------------
# Roster snapshot helpers
# ---------------------------------------------------------------------------

def _all_teams() -> list[dict]:
    rows = db.fetchall(
        "SELECT id, name, abbrev, org_strength, "
        "       mgr_quick_hook, mgr_bullpen_aggression, mgr_joker_aggression "
        "FROM teams ORDER BY id"
    )
    return [dict(r) for r in rows]


def _team_active_roster(team_id: int) -> list[dict]:
    rows = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? AND is_active = 1",
        (team_id,),
    )
    return [dict(r) for r in rows]


def _position_target(position: str, is_pitcher: bool) -> int:
    """Matches the convention in `auction._team_position_need` so the
    surplus/blocked logic aligns with the bidding damper.
    """
    if is_pitcher:
        return 24
    return 4 if position in ("CF", "SS", "2B", "C") else 2


def _starter_slots(position: str, is_pitcher: bool) -> int:
    """How many lineup slots a position can fill. Hitters slot one
    starter at their nominal position; pitchers don't have a single
    "starter" since rotation + bullpen share the role, so the blocked
    detector skips pitchers entirely (their depth-chart unlock isn't
    really a thing).
    """
    if is_pitcher:
        return 24   # effectively never "blocked" — skip
    return 1


def _blocked_at_position(roster: list[dict]) -> dict[str, list[dict]]:
    """Group hitters by position, sort by OVR desc, return ONLY the
    players past the starter-slot count whose own OVR clears
    STARTER_FLOOR. These are the "blocked talents" — players good
    enough to start somewhere but stuck behind a teammate here.
    """
    by_pos: dict[str, list[dict]] = defaultdict(list)
    for p in roster:
        if p.get("is_pitcher"):
            continue
        pos = p.get("position") or "DH"
        by_pos[pos].append(p)
    blocked: dict[str, list[dict]] = {}
    for pos, players in by_pos.items():
        players.sort(key=lambda x: _au._player_overall(x), reverse=True)
        starter_slots = _starter_slots(pos, False)
        chain = []
        for p in players[starter_slots:]:
            if _au._player_overall(p) >= STARTER_FLOOR:
                chain.append(p)
        if chain:
            blocked[pos] = chain
    return blocked


def _starter_at_position(roster: list[dict]) -> dict[str, dict | None]:
    """The team's #1 (highest-OVR hitter) at each position. None if the
    team has nobody at that slot.
    """
    by_pos: dict[str, list[dict]] = defaultdict(list)
    for p in roster:
        if p.get("is_pitcher"):
            continue
        pos = p.get("position") or "DH"
        by_pos[pos].append(p)
    starters: dict[str, dict | None] = {}
    for pos, players in by_pos.items():
        players.sort(key=lambda x: _au._player_overall(x), reverse=True)
        starters[pos] = players[0] if players else None
    return starters


def _thin_at_position(roster: list[dict], position: str) -> bool:
    """A position is 'thin' if the team's #1 there is below STARTER_FLOOR
    (or absent entirely). Receiving a talent at a thin position is a
    real unlock; receiving at a deep position isn't.
    """
    candidates = [p for p in roster
                  if not p.get("is_pitcher")
                  and (p.get("position") or "DH") == position]
    if not candidates:
        return True
    best = max(_au._player_overall(p) for p in candidates)
    return best < STARTER_FLOOR


def _team_position_need(team_id: int, position: str) -> int:
    """Thin wrapper so this module doesn't reach into auction._ internals
    from every callsite. Hitters only — we don't blockbuster pitchers
    in v1."""
    return _au._team_position_need(team_id, position, is_pitcher=False)


# ---------------------------------------------------------------------------
# State snapshot
# ---------------------------------------------------------------------------

def _snapshot_all_teams() -> dict[int, dict]:
    """Build a per-team snapshot of everything the candidate enumerator
    needs. Rebuilt on every iteration so post-trade state is reflected.

    Returns: team_id → {
        "row":      team_row (id, abbrev, org_strength, ...),
        "profile":  auction profile (aggression, star_bias, ...),
        "roster":   list[player dict],
        "blocked":  {position → [blocked players, sorted by OVR desc]},
        "starter":  {position → starter player or None},
        "needs":    {position → signed need (target - have)},
    }
    """
    teams = _all_teams()
    snap: dict[int, dict] = {}
    for t in teams:
        roster = _team_active_roster(t["id"])
        blocked = _blocked_at_position(roster)
        starter = _starter_at_position(roster)
        needs: dict[str, int] = {}
        for pos in ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"):
            needs[pos] = _team_position_need(t["id"], pos)
        snap[t["id"]] = {
            "row":     t,
            "profile": _au._team_auction_profile(t),
            "roster":  roster,
            "blocked": blocked,
            "starter": starter,
            "needs":   needs,
        }
    return snap


# ---------------------------------------------------------------------------
# Candidate enumeration — yields dicts shaped:
#   {
#     "class":     str — blockbuster / star_for_star / 3_cycle / surplus / arbitrage
#     "moves":     list[(player, from_team_id, to_team_id)]
#     "teams":     list[int]
#     "tag":       optional sub-tag (e.g. "marquee")
#     "score":     float — populated by _score_candidate
#   }
# ---------------------------------------------------------------------------

def _starter_ovr(roster: list[dict], position: str,
                 exclude_player_id: int | None = None) -> int:
    """Highest OVR at `position` on this roster, optionally treating a
    given player as already gone (used to check 'what happens to the
    depth chart AFTER we ship them out')."""
    best = 0
    for p in roster:
        if p.get("is_pitcher"):
            continue
        if (p.get("position") or "DH") != position:
            continue
        if exclude_player_id is not None and p["id"] == exclude_player_id:
            continue
        ovr = _au._player_overall(p)
        if ovr > best:
            best = ovr
    return best


def _both_sides_improve(snap: dict, t_a: int, t_b: int,
                        player_a: dict, player_b: dict) -> bool:
    """Lineup-unlock gate: after the swap, each team's STARTER at the
    inbound position must be at least as good as before AND at least
    one side must strictly improve. Captures the real blockbuster /
    star-for-star intuition (lineup OVR rises) without leaning on the
    coarse position-need multiplier, which clips identical-need
    candidates to a tie.

    For each team we compare:
      before_a = max OVR at player_b's position currently on A
      after_a  = max(before_a, player_b's OVR)   # inbound joins
      → improvement_a = after_a - before_a       # ≥ 0 always
    And the mirror for team B. Symmetric ties (both 0) don't fire —
    we require strict total improvement to avoid no-op shuffles.
    """
    pos_a = player_a.get("position") or "DH"
    pos_b = player_b.get("position") or "DH"

    # A loses player_a (at pos_a), gains player_b (at pos_b).
    before_a_at_pos_b = _starter_ovr(snap[t_a]["roster"], pos_b)
    after_a_at_pos_b  = max(before_a_at_pos_b, _au._player_overall(player_b))
    # A's depth at pos_a after shipping player_a out — drops to next-best.
    after_a_at_pos_a  = _starter_ovr(snap[t_a]["roster"], pos_a,
                                     exclude_player_id=player_a["id"])
    before_a_at_pos_a = _starter_ovr(snap[t_a]["roster"], pos_a)

    improvement_a = (after_a_at_pos_b - before_a_at_pos_b
                     + after_a_at_pos_a - before_a_at_pos_a)

    before_b_at_pos_a = _starter_ovr(snap[t_b]["roster"], pos_a)
    after_b_at_pos_a  = max(before_b_at_pos_a, _au._player_overall(player_a))
    after_b_at_pos_b  = _starter_ovr(snap[t_b]["roster"], pos_b,
                                     exclude_player_id=player_b["id"])
    before_b_at_pos_b = _starter_ovr(snap[t_b]["roster"], pos_b)

    improvement_b = (after_b_at_pos_a - before_b_at_pos_a
                     + after_b_at_pos_b - before_b_at_pos_b)

    # Both sides at least break even; total improvement strictly positive.
    return (improvement_a >= 0 and improvement_b >= 0
            and improvement_a + improvement_b > 0)


def _enum_blockbusters(snap: dict) -> Iterable[dict]:
    """A blocked talent at A's P_a fits B (B is thin at P_a) AND B
    has a blocked talent at P_b that fits A (A is thin at P_b). Talent
    symmetric (|ΔOVR| ≤ SYM_2TEAM).
    """
    team_ids = list(snap.keys())
    for i, t_a in enumerate(team_ids):
        for t_b in team_ids[i + 1:]:
            for p_a, chain_a in snap[t_a]["blocked"].items():
                for p_b, chain_b in snap[t_b]["blocked"].items():
                    if p_a == p_b:
                        continue   # same position — different class (surplus)
                    if not _thin_at_position(snap[t_b]["roster"], p_a):
                        continue
                    if not _thin_at_position(snap[t_a]["roster"], p_b):
                        continue
                    # Pick the top blocked talent each side; if more than
                    # one, the next iteration after this trade will pick
                    # the next one.
                    pl_a, pl_b = chain_a[0], chain_b[0]
                    ovr_a, ovr_b = (_au._player_overall(pl_a),
                                    _au._player_overall(pl_b))
                    if abs(ovr_a - ovr_b) > SYM_2TEAM:
                        continue
                    if not _both_sides_improve(snap, t_a, t_b, pl_a, pl_b):
                        continue
                    tag = None
                    if min(ovr_a, ovr_b) >= MARQUEE_TAG:
                        tag = "marquee"
                    elif min(ovr_a, ovr_b) >= BLOCKBUSTER_TAG:
                        tag = "blockbuster"
                    yield {
                        "class":  "blockbuster",
                        "tag":    tag,
                        "teams":  [t_a, t_b],
                        "moves":  [(pl_a, t_a, t_b), (pl_b, t_b, t_a)],
                        "positions": [p_a, p_b],
                        "ovrs":   [ovr_a, ovr_b],
                    }


def _enum_star_for_star(snap: dict) -> Iterable[dict]:
    """Both teams' starters swap (no blocking needed). Fires when each
    team's noise-free val of the inbound starter beats its val of the
    outbound starter — typically because of position-need asymmetry.
    """
    team_ids = list(snap.keys())
    for i, t_a in enumerate(team_ids):
        for t_b in team_ids[i + 1:]:
            for p_a, st_a in snap[t_a]["starter"].items():
                if st_a is None:
                    continue
                if _au._player_overall(st_a) < BLOCKBUSTER_TAG:
                    continue   # only fire on real stars
                for p_b, st_b in snap[t_b]["starter"].items():
                    if p_a == p_b:
                        continue
                    if st_b is None:
                        continue
                    if _au._player_overall(st_b) < BLOCKBUSTER_TAG:
                        continue
                    ovr_a, ovr_b = (_au._player_overall(st_a),
                                    _au._player_overall(st_b))
                    if abs(ovr_a - ovr_b) > SYM_2TEAM:
                        continue
                    if not _both_sides_improve(snap, t_a, t_b, st_a, st_b):
                        continue
                    tag = "marquee" if min(ovr_a, ovr_b) >= MARQUEE_TAG else None
                    yield {
                        "class":  "star_for_star",
                        "tag":    tag,
                        "teams":  [t_a, t_b],
                        "moves":  [(st_a, t_a, t_b), (st_b, t_b, t_a)],
                        "positions": [p_a, p_b],
                        "ovrs":   [ovr_a, ovr_b],
                    }


def _enum_3_cycles(snap: dict) -> Iterable[dict]:
    """A→B→C→A blocked-talent cycle across 3 distinct positions.

    Edge `(A → B at P_a)` exists when A has a blocked talent at P_a
    AND B is thin at P_a. We build the edge list per source, then
    iterate triples that close back to A.
    """
    edges: dict[int, list[tuple[int, str, dict]]] = defaultdict(list)
    for t_src, data in snap.items():
        for pos, chain in data["blocked"].items():
            top = chain[0]
            for t_dst in snap.keys():
                if t_dst == t_src:
                    continue
                if _thin_at_position(snap[t_dst]["roster"], pos):
                    edges[t_src].append((t_dst, pos, top))

    seen_cycles: set[frozenset] = set()
    for t_a, outs_a in edges.items():
        for (t_b, p_a, pl_a) in outs_a:
            for (t_c, p_b, pl_b) in edges.get(t_b, []):
                if t_c == t_a:
                    continue
                if p_b == p_a:
                    continue
                for (t_close, p_c, pl_c) in edges.get(t_c, []):
                    if t_close != t_a:
                        continue
                    if p_c in (p_a, p_b):
                        continue
                    # De-dupe rotation-equivalent cycles (A→B→C and
                    # B→C→A would otherwise both fire).
                    key = frozenset({t_a, t_b, t_c})
                    if key in seen_cycles:
                        continue
                    ovrs = [_au._player_overall(pl) for pl in (pl_a, pl_b, pl_c)]
                    if max(ovrs) - min(ovrs) > SYM_3TEAM:
                        continue
                    # Depth-chart unlock check, mirroring the 2-team
                    # gate but rotated across the cycle: each team's
                    # inbound either matches or exceeds their existing
                    # best at the inbound pos, AND at least one strict
                    # improvement somewhere in the cycle.
                    def unlock(player_in, player_out, dest_tid):
                        pos_in = player_in.get("position") or "DH"
                        pos_out = player_out.get("position") or "DH"
                        before_in = _starter_ovr(snap[dest_tid]["roster"], pos_in)
                        after_in = max(before_in, _au._player_overall(player_in))
                        before_out = _starter_ovr(snap[dest_tid]["roster"], pos_out)
                        after_out = _starter_ovr(snap[dest_tid]["roster"], pos_out,
                                                 exclude_player_id=player_out["id"])
                        return (after_in - before_in) + (after_out - before_out)
                    # In the cycle, each team SENDS one player and RECEIVES
                    # the next team's player:
                    # A sends pl_a, receives pl_c. B sends pl_b, receives pl_a. C sends pl_c, receives pl_b.
                    imp_a = unlock(pl_c, pl_a, t_a)
                    imp_b = unlock(pl_a, pl_b, t_b)
                    imp_c = unlock(pl_b, pl_c, t_c)
                    if min(imp_a, imp_b, imp_c) < 0:
                        continue
                    if imp_a + imp_b + imp_c <= 0:
                        continue
                    seen_cycles.add(key)
                    tag = None
                    if min(ovrs) >= MARQUEE_TAG:
                        tag = "marquee"
                    elif min(ovrs) >= BLOCKBUSTER_TAG:
                        tag = "blockbuster"
                    # Each player moves to the next team in the cycle:
                    # pl_a (from A) → B, pl_b (from B) → C, pl_c (from C) → A.
                    yield {
                        "class":  "3_cycle",
                        "tag":    tag,
                        "teams":  [t_a, t_b, t_c],
                        "moves":  [(pl_a, t_a, t_b),
                                   (pl_b, t_b, t_c),
                                   (pl_c, t_c, t_a)],
                        "positions": [p_a, p_b, p_c],
                        "ovrs":   ovrs,
                    }


def _enum_surplus(snap: dict) -> Iterable[dict]:
    """A has need ≤ -2 at P; trades 2nd-best chip there for B's best
    player at A's most-needed (positive-need) position.
    """
    team_ids = list(snap.keys())
    for t_a in team_ids:
        # Find A's over-stocked positions
        surplus_pos = [p for p, n in snap[t_a]["needs"].items() if n <= -2]
        if not surplus_pos:
            continue
        # A's most-needed position
        need_pos_a = max(snap[t_a]["needs"].items(), key=lambda kv: kv[1])
        if need_pos_a[1] < 1:
            continue
        need_pos_a = need_pos_a[0]

        for p_a in surplus_pos:
            chain_a = sorted(
                [p for p in snap[t_a]["roster"]
                 if not p.get("is_pitcher")
                 and (p.get("position") or "DH") == p_a],
                key=lambda x: _au._player_overall(x), reverse=True,
            )
            if len(chain_a) < 2:
                continue
            chip_a = chain_a[1]   # 2nd-best — never trade the starter
            for t_b in team_ids:
                if t_b == t_a:
                    continue
                if snap[t_b]["needs"].get(p_a, 0) < 1:
                    continue
                # B's best player at A's most-needed position
                cands_b = sorted(
                    [p for p in snap[t_b]["roster"]
                     if not p.get("is_pitcher")
                     and (p.get("position") or "DH") == need_pos_a],
                    key=lambda x: _au._player_overall(x), reverse=True,
                )
                if not cands_b:
                    continue
                return_b = cands_b[0]
                ovr_a, ovr_b = (_au._player_overall(chip_a),
                                _au._player_overall(return_b))
                if abs(ovr_a - ovr_b) > SYM_2TEAM:
                    continue
                if not _both_sides_improve(snap, t_a, t_b, chip_a, return_b):
                    continue
                yield {
                    "class":  "surplus",
                    "tag":    None,
                    "teams":  [t_a, t_b],
                    "moves":  [(chip_a, t_a, t_b), (return_b, t_b, t_a)],
                    "positions": [p_a, need_pos_a],
                    "ovrs":   [ovr_a, ovr_b],
                }


def _enum_arbitrage(snap: dict) -> Iterable[dict]:
    """Catch-all valuation mismatch: A's player is valued markedly higher
    by B than by A, AND B has a return asset that A values markedly
    higher than B. Talent-symmetric, both-sides-improve, no other
    pattern required. Fires last — used as a cleanup sweep after the
    structured classes have run.

    Threshold: each side's val of inbound must exceed val of outbound by
    ≥ 15%. Tighter than the structured classes (which use a strict >);
    arbitrage needs the surplus to justify the cleanup tag.
    """
    THRESH = 0.15
    team_ids = list(snap.keys())
    for i, t_a in enumerate(team_ids):
        for t_b in team_ids[i + 1:]:
            for pl_a in snap[t_a]["roster"]:
                if pl_a.get("is_pitcher"):
                    continue
                v_a_out = _au._team_valuation_noisefree(
                    pl_a, t_a, snap[t_a]["profile"])
                v_a_in_b = _au._team_valuation_noisefree(
                    pl_a, t_b, snap[t_b]["profile"])
                if v_a_in_b < v_a_out * (1.0 + THRESH):
                    continue
                # B values pl_a notably higher than A does. Now find a
                # return asset on B that A would value at least 15%
                # higher than B does.
                for pl_b in snap[t_b]["roster"]:
                    if pl_b.get("is_pitcher"):
                        continue
                    if pl_b["id"] == pl_a["id"]:
                        continue
                    ovr_a, ovr_b = (_au._player_overall(pl_a),
                                    _au._player_overall(pl_b))
                    if abs(ovr_a - ovr_b) > SYM_2TEAM:
                        continue
                    v_b_out = _au._team_valuation_noisefree(
                        pl_b, t_b, snap[t_b]["profile"])
                    v_b_in_a = _au._team_valuation_noisefree(
                        pl_b, t_a, snap[t_a]["profile"])
                    if v_b_in_a < v_b_out * (1.0 + THRESH):
                        continue
                    yield {
                        "class":  "arbitrage",
                        "tag":    None,
                        "teams":  [t_a, t_b],
                        "moves":  [(pl_a, t_a, t_b), (pl_b, t_b, t_a)],
                        "positions": [pl_a.get("position"),
                                      pl_b.get("position")],
                        "ovrs":   [ovr_a, ovr_b],
                    }


# ---------------------------------------------------------------------------
# Scoring + firing
# ---------------------------------------------------------------------------

def _score_candidate(cand: dict, snap: dict) -> float:
    """Unified score across all candidate classes.

    Components:
      unlock_mag  — sum of post-trade depth-chart improvements (best
                    inbound OVR minus current best at that pos)
      talent_mag  — sum of moving-player OVRs (rewards marquee deals)
      symmetry    — penalty on OVR spread (closer is better)
      class_bonus — 1.25× for 3-cycles, 1.10× for blockbusters/star,
                    1.00× otherwise
    """
    moves = cand["moves"]
    ovrs = cand["ovrs"]
    talent_mag = sum(ovrs)
    spread = max(ovrs) - min(ovrs)
    sym_pen = max(0.0, 1.0 - 0.05 * spread)

    unlock_mag = 0
    for (player, _from_tid, to_tid) in moves:
        pos = player.get("position") or "DH"
        cur_best_row = max(
            (_au._player_overall(p) for p in snap[to_tid]["roster"]
             if not p.get("is_pitcher")
             and (p.get("position") or "DH") == pos),
            default=0,
        )
        inbound_ovr = _au._player_overall(player)
        unlock_mag += max(0, inbound_ovr - cur_best_row)

    class_bonus = {
        "3_cycle":       CYCLE_3_BONUS,
        "blockbuster":   1.10,
        "star_for_star": 1.10,
        "surplus":       1.00,
        "arbitrage":     0.90,
    }.get(cand["class"], 1.00)

    return (unlock_mag * 4 + talent_mag) * sym_pen * class_bonus


def _detail_string(cand: dict, snap: dict) -> str:
    parts = [f"Trade · {cand['class'].replace('_', ' ')}"]
    if cand.get("tag"):
        parts[-1] += f" ({cand['tag']})"
    move_strs = []
    for (player, from_tid, to_tid) in cand["moves"]:
        from_ab = snap[from_tid]["row"]["abbrev"]
        to_ab = snap[to_tid]["row"]["abbrev"]
        ovr = _au._player_overall(player)
        move_strs.append(
            f"{player['name']} ({player.get('position', '?')}, OVR {ovr}) "
            f"{from_ab}→{to_ab}"
        )
    parts.append(" · ".join(move_strs))
    return " — ".join(parts)


def _fire_trade(cand: dict, season: int, snap: dict) -> dict:
    """Apply the moves to the DB and emit transaction events. Returns
    the candidate enriched with `detail` and `db_ok` flags.
    """
    detail = _detail_string(cand, snap)
    cand["detail"] = detail

    for (player, from_tid, to_tid) in cand["moves"]:
        db.execute(
            "UPDATE players SET team_id = ? WHERE id = ?",
            (to_tid, player["id"]),
        )

    # Transaction events: one per player moved, tagged to the destination
    # team so the player-card transactions tab shows the inbound move.
    try:
        from o27v2.transactions import log_many, current_season
        from datetime import date as _date
        events = []
        for (player, from_tid, to_tid) in cand["moves"]:
            events.append({
                "event_type": "trade",
                "team_id":    to_tid,
                "player_id":  player["id"],
                "detail":     detail,
            })
        log_many(season or current_season(), _date.today().isoformat(), events)
    except Exception:
        # Transactions are best-effort — the trade itself isn't reverted
        # if logging fails (db consistency comes first).
        pass

    cand["db_ok"] = True
    return cand


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_post_auction_trades(*, season: int | None = None,
                            rng_seed: int = 0,
                            per_team_cap: int = PER_TEAM_CAP,
                            max_trades: int = MAX_ITERATIONS,
                            include_arbitrage: bool = True) -> dict[str, Any]:
    """Run the full post-auction trade reconciliation pass.

    Iterative: each round enumerates all candidate trades from the
    current depth-chart state, scores them, fires the top one, then
    re-snapshots. Stops when no candidate clears the gates or all
    eligible teams have hit per_team_cap.

    Returns:
      {
        "trades":      list[{class, tag, teams, moves, positions, ovrs,
                             score, detail}],
        "total_trades": N,
        "iterations":  K,
      }
    """
    rng = random.Random(rng_seed ^ 0xDEA1C0DE)
    if season is None:
        try:
            from o27v2.transactions import current_season
            season = current_season()
        except Exception:
            season = 1

    fired: list[dict] = []
    trades_by_team: dict[int, int] = defaultdict(int)

    for it in range(max_trades):
        snap = _snapshot_all_teams()
        # Filter teams that have hit cap
        eligible = {tid for tid, n in trades_by_team.items()
                    if n >= per_team_cap}

        candidates: list[dict] = []
        # Priority order: 3-cycle → blockbuster → star-for-star →
        # surplus → arbitrage. Each enumerator yields its set; we
        # score them all together and fire the top regardless of class.
        candidates.extend(_enum_3_cycles(snap))
        candidates.extend(_enum_blockbusters(snap))
        candidates.extend(_enum_star_for_star(snap))
        candidates.extend(_enum_surplus(snap))
        if include_arbitrage:
            candidates.extend(_enum_arbitrage(snap))

        # Drop candidates that would push any participating team over cap
        viable = [c for c in candidates
                  if not any(tid in eligible for tid in c["teams"])]
        if not viable:
            break

        for c in viable:
            c["score"] = _score_candidate(c, snap)
        # Tie-breaker jitter so identical-score candidates don't always
        # pick the same team-id ordering.
        viable.sort(key=lambda c: (c["score"], rng.random()), reverse=True)

        top = viable[0]
        _fire_trade(top, season, snap)
        fired.append(top)
        for tid in top["teams"]:
            trades_by_team[tid] += 1
    else:
        it = max_trades

    # Strip ORM dicts out of the moves field for JSON-friendly return;
    # caller can re-query if they need full player rows.
    serial = []
    for c in fired:
        serial.append({
            "class":     c["class"],
            "tag":       c.get("tag"),
            "teams":     c["teams"],
            "positions": c["positions"],
            "ovrs":      c["ovrs"],
            "score":     c["score"],
            "detail":    c["detail"],
            "moves": [
                {"player_id": p["id"], "player_name": p["name"],
                 "from_team_id": f, "to_team_id": t,
                 "position": p.get("position"),
                 "overall": _au._player_overall(p)}
                for (p, f, t) in c["moves"]
            ],
        })

    return {
        "trades":       serial,
        "total_trades": len(serial),
        "iterations":   it + 1 if fired else 0,
    }
