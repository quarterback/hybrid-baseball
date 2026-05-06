"""
Game simulation for O27v2.

simulate_game(game_id) runs a complete O27 game for the given DB game_id,
stores results back to the database, and returns a result summary dict.

Phase 9 additions:
  - Active roster filtering: injured players are excluded from the lineup.
  - Post-game injury draws fire after each game.
  - Trade deadline and in-season trade checks fire after each game.
  - Waiver claims fire when bullpen drops below threshold.
  - All roster moves are logged to the transactions table.
"""
from __future__ import annotations
import random
import sys
import os

_workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _workspace not in sys.path:
    sys.path.insert(0, _workspace)

from o27.engine.state import GameState, Team, Player
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.render.render import Renderer

from o27v2 import db
import o27v2.config as v2cfg
from o27v2 import scout as _scout


# ---------------------------------------------------------------------------
# Defensive positional values (Bill James style, scaled to O27).
# These weights determine BOTH:
#   - the team-defense-rating aggregation (high-value positions count more)
#   - the per-player DRS positional adjustment (a +SS is worth more than +1B)
# Centralised here so app.py and sim.py read the same numbers.
# ---------------------------------------------------------------------------

POSITIONAL_VALUE: dict[str, float] = {
    "C":  +1.5,
    "SS": +1.0,
    "CF": +0.5,
    "2B": +0.5,
    "3B": +0.3,
    "LF": -0.3,
    "RF": -0.3,
    "1B": -0.7,
    "DH": -1.5,
    "UT":  0.0,
    "P":  -2.0,   # pitchers rarely field; their "defense" mostly reflects PFP
}


_INFIELD_POSITIONS  = frozenset(("1B", "2B", "3B", "SS"))
_OUTFIELD_POSITIONS = frozenset(("LF", "CF", "RF"))


_SPEED_RANGE_POSITIONS = {
    # Position → fraction of the rating that's driven by foot speed
    # (closing range, first-step quickness, taking the extra base on
    # cutoffs). Speed = 0.5 is neutral (zero contribution); deviations
    # from neutral push the position-defense rating up or down.
    "CF":  0.30, "LF":  0.22, "RF":  0.22,
    "SS":  0.18, "2B":  0.18,
    "3B":  0.08, "1B":  0.04,
    "C":   0.00,
}


def _position_defense_rating(player, pos: str) -> float:
    """Return the player's effective defense at the given position.

    Blends general `defense` with the position-group sub-rating so a
    specialist gets a real boost at their primary group and a real
    penalty out of group. 60% sub-group, 40% general. A speed adjustment
    is then layered on top for positions where foot speed translates to
    range — heavily weighted for CF, moderate for corner OF and middle
    IF, and basically nil at 1B / C. Identity preserved at speed = 0.5.
    """
    general = float(getattr(player, "defense", 0.5) or 0.5)
    if pos == "C":
        sub = float(getattr(player, "defense_catcher", 0.5) or 0.5)
    elif pos in _INFIELD_POSITIONS:
        sub = float(getattr(player, "defense_infield", 0.5) or 0.5)
    elif pos in _OUTFIELD_POSITIONS:
        sub = float(getattr(player, "defense_outfield", 0.5) or 0.5)
    else:
        sub = general
    base = 0.6 * sub + 0.4 * general
    speed = float(getattr(player, "speed", 0.5) or 0.5)
    speed_w = _SPEED_RANGE_POSITIONS.get(pos, 0.0)
    return base + speed_w * (speed - 0.5)


def _team_defense_rating(lineup: list, roster: list[dict]) -> float:
    """Compute a single 0..1 team defense rating as a positional-value-
    weighted mean of fielders' position-aware defense ratings.

    `lineup` is the engine-side Player list (8 fielders + SP + 3 DH).
    `roster` is the original DB-side player rows so we can look up the
    canonical position string by player_id (engine Players don't carry
    position).

    Identity: at all defaults (defense = 0.5 for everyone) → returns 0.5.
    """
    pos_by_id: dict[str, str] = {
        str(r["id"]): str(r.get("position") or "") for r in roster
    }
    weighted_sum = 0.0
    weight_sum   = 0.0
    for player in lineup:
        pos = pos_by_id.get(player.player_id, "")
        if pos in ("DH", "P"):
            continue   # DH and starting pitchers don't contribute to fielding
        # Weight = max(0.5, 1.5 + positional_value) so even -bias positions
        # contribute, but valuable positions count more.
        w = max(0.5, 1.5 + POSITIONAL_VALUE.get(pos, 0.0))
        weighted_sum += w * _position_defense_rating(player, pos)
        weight_sum   += w
    return (weighted_sum / weight_sum) if weight_sum > 0 else 0.5


# ---------------------------------------------------------------------------
# DB ↔ engine type converters
# ---------------------------------------------------------------------------

def _bat_score(p) -> float:
    """Composite hitting talent score used for lineup ordering and joker
    pool selection."""
    return (
        float(p.skill)        * 0.55
        + float(p.power)      * 0.15
        + float(p.contact)    * 0.20
        + float(p.eye)        * 0.10
    )


def _ordered_lineup(
    starting_fielders: list,
    todays_sp: list,
) -> list:
    """Order the 9-batter base lineup by hitting talent.

    Base lineup = 8 fielders + SP. DHs are NOT in the base lineup — they
    live in the joker pool (jokers_available) and are inserted tactically
    by the manager AI per PA, subject to once-per-cycle.

    Pitchers almost always hit 9th. Exception: a pitcher whose hitting
    `skill` clears 0.50 (top ~5-10% of arms in a fresh seed) slots in by
    talent like everyone else.
    """
    non_pitchers = list(starting_fielders)
    non_pitchers.sort(key=_bat_score, reverse=True)

    sp = todays_sp[0] if todays_sp else None
    if sp is None:
        return non_pitchers

    # Pitchers ≥ this hitting `skill` slot in by talent like a non-pitcher.
    # Default 0.50 catches the top ~5-10% of pitchers per the tier ladder.
    if float(sp.skill) >= 0.50:
        combined = non_pitchers + [sp]
        combined.sort(key=_bat_score, reverse=True)
        return combined
    return non_pitchers + [sp]   # default: SP bats 9th


def _pick_jokers(non_starter_bats: list, n: int = 3) -> list:
    """Pick the top-n bats from the non-starter pool (DH + UT bench)
    as today's joker pool. Sorted by composite hitting talent.

    These are the manager's tactical pinch-hitters — three pinch-hits-
    with-no-cost, each available once per cycle. They are NOT in the
    base lineup; they enter via per-PA insertion when leverage warrants.
    """
    sorted_bats = sorted(non_starter_bats, key=_bat_score, reverse=True)
    return sorted_bats[:n]


def _position_player_workload(
    team_id: int, game_date: str, lookback_days: int = 12
) -> dict[int, dict]:
    """Consecutive-starts state for each position player on the team.

    Walks backward through the team's actual played dates (skipping off-days
    so the All-Star break doesn't reset the streak) and counts how many of
    the most-recent-N games the player started (PA > 0 in regulation).

    Returns: { db_player_id: {"consecutive_starts": int, "last_start": str} }
    Players who haven't started in the lookback window are absent from the
    dict — callers should treat them as fully rested.
    """
    team_date_rows = db.fetchall(
        """SELECT DISTINCT g.game_date
             FROM games g
            WHERE g.played = 1
              AND (g.home_team_id = ? OR g.away_team_id = ?)
              AND g.game_date >= date(?, ?)
              AND g.game_date <  ?
            ORDER BY g.game_date DESC""",
        (team_id, team_id, game_date, f"-{lookback_days} days", game_date),
    )
    team_dates = [r["game_date"] for r in team_date_rows]
    if not team_dates:
        return {}

    placeholders = ",".join("?" * len(team_dates))
    starts_rows = db.fetchall(
        f"""SELECT g.game_date, bs.player_id
              FROM game_batter_stats bs
              JOIN games g ON g.id = bs.game_id
             WHERE bs.team_id = ?
               AND g.played = 1
               AND g.game_date IN ({placeholders})
               AND bs.pa > 0
               AND bs.phase = 0""",
        (team_id, *team_dates),
    )
    player_starts: dict[int, set[str]] = {}
    for r in starts_rows:
        player_starts.setdefault(r["player_id"], set()).add(r["game_date"])

    out: dict[int, dict] = {}
    for pid, started_dates in player_starts.items():
        consecutive = 0
        for td in team_dates:
            if td in started_dates:
                consecutive += 1
            else:
                break
        out[pid] = {
            "consecutive_starts": consecutive,
            "last_start": max(started_dates),
        }
    return out


def _db_team_to_engine(
    team_row: dict,
    players: list[dict],
    team_role: str,
    rotation_index: int = 0,
    recently_used_pitcher_ids: set[int] | None = None,
    workload: dict[int, dict[str, int]] | None = None,
    position_workload: dict[int, dict] | None = None,
    game_date: str | None = None,
) -> Team:
    """
    Convert a DB team row + player rows into an O27 engine Team object.

    Task #65 changes:
      - roster:  ALL active healthy players (34: 12 fielders + 3 DH + 19 P).
      - lineup:  8 starting fielders + today's SP + 3 DH = 12 batters.
      - Today's SP is the highest-Stamina active arm that did NOT pitch in
        the last few sim days (`recently_used_pitcher_ids`). No rotation
        index is used — role is derived live from current attributes so
        aging arms naturally drift out of the rotation.

    `recently_used_pitcher_ids` is a set of DB player_ids who appeared in
    the previous ~4 sim days; supply via the caller. None / empty is
    treated as "everyone is rested".

    `rotation_index` is accepted for back-compat with older callers but
    is no longer consulted.
    """
    _ = rotation_index  # unused — kept for back-compat
    rest_excluded = recently_used_pitcher_ids or set()
    workload = workload or {}

    engine_players: list[Player] = []
    fielders: list[Player] = []
    pitchers: list[Player] = []
    dhs:      list[Player] = []
    jokers:   list[Player] = []

    # Map engine player_id (str) → original DB id (int) so we can apply
    # the rest filter.
    engine_to_db_id: dict[str, int] = {}

    for p in players:
        home_bonus = (
            v2cfg.HOME_ADVANTAGE_SKILL
            if team_role == "home"
            else 0.0
        )
        stamina_grade = p.get("stamina")
        if stamina_grade is None or stamina_grade == 0:
            stamina_grade = p.get("pitcher_skill", 50)
        player = Player(
            player_id=str(p["id"]),
            name=p["name"],
            is_pitcher=bool(p["is_pitcher"]),
            skill=_scout.to_unit(p["skill"]) + home_bonus,
            speed=_scout.to_unit(p["speed"]),
            pitcher_skill=_scout.to_unit(p["pitcher_skill"]),
            stamina=_scout.to_unit(stamina_grade),
            stay_aggressiveness=float(p["stay_aggressiveness"]),
            contact_quality_threshold=float(p["contact_quality_threshold"]),
            archetype=str(p.get("archetype") or ""),
            pitcher_role=str(p.get("pitcher_role") or ""),
            hard_contact_delta=float(p.get("hard_contact_delta") or 0.0),
            hr_weight_bonus=float(p.get("hr_weight_bonus") or 0.0),
            # Realism layer — defaults of 50 / 'R' produce engine-identical
            # behavior on legacy DB rows that predate these columns.
            contact=_scout.to_unit(p.get("contact") or 50),
            power=_scout.to_unit(p.get("power") or 50),
            eye=_scout.to_unit(p.get("eye") or 50),
            command=_scout.to_unit(p.get("command") or 50),
            movement=_scout.to_unit(p.get("movement") or 50),
            # Legacy DB rows pre-realism return '' / 'R' from the column
            # default; only treat seeded 'L'/'R'/'S' as platoon-applicable.
            bats=str(p.get("bats") or ""),
            throws=str(p.get("throws") or ""),
            defense=_scout.to_unit(p.get("defense") or 50),
            arm=_scout.to_unit(p.get("arm") or 50),
            defense_infield=_scout.to_unit(p.get("defense_infield") or 50),
            defense_outfield=_scout.to_unit(p.get("defense_outfield") or 50),
            defense_catcher=_scout.to_unit(p.get("defense_catcher") or 50),
            baserunning=_scout.to_unit(p.get("baserunning") or 50),
            run_aggressiveness=_scout.to_unit(p.get("run_aggressiveness") or 50),
            position=str(p.get("position") or ""),
        )
        # Stamp workload state on every Player so the manager AI and the
        # engine's tired-multiplier can read it without extra plumbing.
        wl = workload.get(int(p["id"]), {}) if p.get("is_pitcher") else {}
        player.days_rest  = int(wl.get("days_rest", 99))
        player.pitch_debt = int(wl.get("pitch_debt", 0))
        engine_players.append(player)
        engine_to_db_id[player.player_id] = int(p["id"])
        if bool(p.get("is_joker")):
            jokers.append(player)
        elif player.is_pitcher:
            pitchers.append(player)
        elif p.get("position") == "DH":
            dhs.append(player)
        else:
            fielders.append(player)

    # ---- Pick today's SP via rest-tiered, stamina-weighted selection ----
    # Real rotations want 4 days rest between starts. We tier candidates by
    # rest level and pick the highest-Stamina arm in the best non-empty tier.
    # Critical: never just fall back to "anyone" — that produces a workhorse
    # who throws every single day. If no arm has the ideal rest, we still
    # pick the MOST-RESTED arm so the rotation keeps cycling.
    todays_sp: list[Player] = []
    if pitchers:
        # Pre-rank: highest stamina first, with debt as a tiebreaker (less
        # debt = fresher arm). Done once so each tier filter just slices it.
        ranked = sorted(
            pitchers,
            key=lambda pl: (-pl.stamina, pl.pitch_debt),
        )
        # Tier the candidates by minimum days rest. Once a tier has any
        # qualifying arm, take the top-stamina one in that tier.
        for min_rest in (4, 3, 2, 1):
            tier = [p for p in ranked if p.days_rest >= min_rest]
            if tier:
                todays_sp = [tier[0]]
                break
        # Last-resort: every arm pitched yesterday or today. Pick the one
        # with the most rest (most rested) and lowest debt — emergency only.
        if not todays_sp:
            ranked.sort(key=lambda pl: (-pl.days_rest, pl.pitch_debt))
            todays_sp = [ranked[0]]

    # Cap fielders at the canonical 8 starting positions; remaining ones
    # are bench depth that lives in the roster but does not bat. Cap DHs
    # at 3 batting slots so the lineup stays at 12 (8 fielders + SP + 3 DH)
    # for engine compatibility.
    starting_fielders = list(fielders[:8])
    bench_fielders = list(fielders[8:])

    # Rest-day pass: rotate UT bench bats in for regulars based on the
    # manager's bench_usage tendency, age, position (catchers rest more),
    # and consecutive starts (compounds after 5 days). Capped at 2 rests
    # per game so we don't get a half-bench lineup. RNG is seeded from
    # game_date + team_id so the decision is reproducible game-to-game
    # and doesn't depend on the sim-time clock.
    if game_date and bench_fielders and position_workload is not None:
        import random as _r
        seed_key = hash((game_date, str(team_role), team_row.get("id", 0))) & 0x7FFFFFFF
        rest_rng = _r.Random(seed_key)
        bench_usage = float(team_row.get("mgr_bench_usage") or 0.5)

        # Score each starter's rest probability and roll independently.
        rest_rolls: list[tuple] = []
        for sf in starting_fielders:
            db_id = engine_to_db_id.get(sf.player_id)
            wl = position_workload.get(db_id, {}) if db_id is not None else {}
            consecutive = int(wl.get("consecutive_starts", 0))
            # Pull the underlying DB row to read age + position. We have
            # them indirectly via the original players list.
            db_row = next((p for p in players if int(p["id"]) == db_id), None) if db_id else None
            age = int((db_row or {}).get("age") or 27)
            pos = (db_row or {}).get("position") or ""

            # Base rate is heavily damped by manager bench_usage so an
            # old-school skipper effectively never rests anyone.
            rest_p = 0.06 * (0.20 + 1.40 * bench_usage)
            if age >= 30:
                rest_p += (age - 30) * 0.005
            if pos == "C":
                rest_p += 0.04
                if consecutive >= 4:
                    rest_p += 0.10
            if consecutive >= 5:
                rest_p += min(0.15, (consecutive - 4) * 0.04)
            rest_p = max(0.0, min(0.40, rest_p))

            rest_rolls.append((sf, rest_p))

        # Roll, cap at 2 rests per game (resort by highest rest_p first
        # so the most-deserving rests get applied if more than 2 fire).
        will_rest: list = []
        for sf, p in sorted(rest_rolls, key=lambda x: -x[1]):
            if len(will_rest) >= min(2, len(bench_fielders)):
                break
            if rest_rng.random() < p:
                will_rest.append(sf)

        if will_rest:
            # Pull bench bats by skill (highest first); position-specific
            # backup is a follow-up — for now we trust the UT pool to be
            # generally usable across the board.
            bench_sorted = sorted(
                bench_fielders,
                key=lambda pl: -float(getattr(pl, "skill", 0.5) or 0.5),
            )
            for i, rested in enumerate(will_rest):
                if i >= len(bench_sorted):
                    break
                idx = starting_fielders.index(rested)
                starting_fielders[idx] = bench_sorted[i]

    # Build the 9-batter base lineup: 8 fielders + SP, ordered by talent.
    lineup = _ordered_lineup(starting_fielders, todays_sp)

    # Pick today's 3 jokers from the non-starter bat pool (all DHs + any
    # bench fielders not in the starting 8). These are tactical pinch-
    # hitters — manager AI inserts them per PA based on leverage, each
    # at most once per cycle through the order.
    bench_pool = list(dhs) + list(fielders[8:])
    jokers     = _pick_jokers(bench_pool, n=3)

    # Reorder the roster so today's SP is the first pitcher. The engine's
    # `_set_fielding_pitcher` picks the first is_pitcher in roster order,
    # so without this swap the SP rotation would be cosmetic — game.py
    # would pick whatever pitcher happened to come first in the DB query.
    if todays_sp:
        sp = todays_sp[0]
        if sp in engine_players:
            engine_players = [sp] + [p for p in engine_players if p is not sp]

    team = Team(
        team_id=team_role,
        name=team_row["name"],
        roster=engine_players,
        lineup=lineup,
        jokers_available=list(jokers),
        park_hr=float(team_row.get("park_hr") or 1.0),
        park_hits=float(team_row.get("park_hits") or 1.0),
    )
    # Compute aggregate defense rating from the lineup's fielding 8.
    team.defense_rating = _team_defense_rating(lineup, players)
    # Stamp manager persona — bias hook/joker/PH/run-game decisions.
    team.manager_archetype        = str(team_row.get("manager_archetype") or "")
    team.mgr_quick_hook           = float(team_row.get("mgr_quick_hook") or 0.5)
    team.mgr_bullpen_aggression   = float(team_row.get("mgr_bullpen_aggression") or 0.5)
    team.mgr_leverage_aware       = float(team_row.get("mgr_leverage_aware") or 0.5)
    team.mgr_joker_aggression     = float(team_row.get("mgr_joker_aggression") or 0.5)
    team.mgr_pinch_hit_aggression = float(team_row.get("mgr_pinch_hit_aggression") or 0.5)
    team.mgr_platoon_aggression   = float(team_row.get("mgr_platoon_aggression") or 0.5)
    team.mgr_run_game             = float(team_row.get("mgr_run_game") or 0.5)
    team.mgr_bench_usage          = float(team_row.get("mgr_bench_usage") or 0.5)
    # Stamp the catcher's arm rating on the Team for SB-success scaling.
    pos_by_id = {str(r["id"]): str(r.get("position") or "") for r in players}
    catcher_arm = 0.5
    for player in lineup:
        if pos_by_id.get(player.player_id, "") == "C":
            catcher_arm = float(getattr(player, "arm", 0.5) or 0.5)
            break
    team.catcher_arm = catcher_arm
    return team


# ---------------------------------------------------------------------------
# Stat extraction from Renderer
# ---------------------------------------------------------------------------

def _extract_batter_stats(renderer: Renderer, team_id: int, players: list[dict]) -> list[dict]:
    """Extract per-phase batter stats from the Renderer's per-phase snapshots.

    Task #58: yields one row per (player, phase) tuple, where phase 0 is
    regulation and phase N >= 1 is super-inning round N. Players with no
    activity in a phase are omitted.
    """
    team_player_ids: set[int] = {p["id"] for p in players}
    rows: list[dict] = []
    phases = renderer.phases_seen()
    if not phases:
        # Engine never called end_phase (legacy code path) — fall back to
        # writing the cumulative stats as a single phase-0 row so older
        # tests / callers still get something.
        phases = [0]
        per_phase = {0: dict(renderer._batter_stats)}
    else:
        per_phase = {p: renderer.batter_stats_for_phase(p) for p in phases}

    for phase in phases:
        for engine_pid, bstat in per_phase.get(phase, {}).items():
            try:
                db_pid = int(engine_pid)
            except (ValueError, TypeError):
                continue
            if db_pid not in team_player_ids:
                continue
            rows.append({
                "team_id": team_id,
                "player_id": db_pid,
                "phase": phase,
                "pa": bstat.pa,
                "ab": bstat.ab,
                "runs": bstat.runs,
                "hits": bstat.hits,
                "doubles": bstat.doubles,
                "triples": bstat.triples,
                "hr": bstat.hr,
                "rbi": bstat.rbi,
                "bb": bstat.bb,
                "k": bstat.k,
                "stays": bstat.sty,
                "outs_recorded": bstat.outs_recorded,
                "hbp": getattr(bstat, "hbp", 0),
                "sb": getattr(bstat, "sb", 0),
                "cs": getattr(bstat, "cs", 0),
                "fo": getattr(bstat, "fo", 0),
                "multi_hit_abs": getattr(bstat, "multi_hit_abs", 0),
                "stay_rbi": getattr(bstat, "stay_rbi", 0),
                "stay_hits": getattr(bstat, "stay_hits", 0),
                "roe": getattr(bstat, "roe", 0),
                "po": getattr(bstat, "po", 0),
                "e":  getattr(bstat, "e",  0),
            })
    return rows


def _extract_pitcher_stats(state: GameState, team_id: int, players: list[dict]) -> list[dict]:
    """Extract per-phase pitcher stats from state.spell_log for DB insertion.

    Task #58: each SpellRecord already carries super_inning_number, so a
    pitcher who pitched in regulation AND in SI round 1 produces TWO
    rows (phase=0 and phase=1) instead of one combined row.
    """
    from o27.stats.pitcher import PitcherStats
    team_player_ids: set[int] = {p["id"] for p in players}

    # Group spells by (pitcher_id, phase).
    by_phase_pid: dict[tuple[str, int], list] = {}
    for rec in state.spell_log:
        phase = int(getattr(rec, "super_inning_number", 0) or 0)
        by_phase_pid.setdefault((rec.pitcher_id, phase), []).append(rec)

    rows: list[dict] = []
    for (pid_str, phase), spells in by_phase_pid.items():
        try:
            db_pid = int(pid_str)
        except (ValueError, TypeError):
            continue
        if db_pid not in team_player_ids:
            continue
        player = (state.visitors.get_player(pid_str) or
                  state.home.get_player(pid_str))
        if player is None:
            continue
        ps = PitcherStats.from_spell_log(spells, pid_str, player.name)
        sb_allowed = sum(getattr(rec, "sb_allowed", 0) for rec in spells)
        cs_caught  = sum(getattr(rec, "cs_caught",  0) for rec in spells)
        fo_induced = sum(getattr(rec, "fo_induced", 0) for rec in spells)

        # Sum arc-bucketed counters across spells (a pitcher may pitch
        # discontiguous spells if the manager pulls and re-uses them).
        def _sum_arc(attr: str) -> tuple[int, int, int]:
            a1 = a2 = a3 = 0
            for rec in spells:
                arc = getattr(rec, attr, [0, 0, 0]) or [0, 0, 0]
                a1 += arc[0] if len(arc) > 0 else 0
                a2 += arc[1] if len(arc) > 1 else 0
                a3 += arc[2] if len(arc) > 2 else 0
            return a1, a2, a3

        er_a1, er_a2, er_a3 = _sum_arc("er_arc")
        k_a1,  k_a2,  k_a3  = _sum_arc("k_arc")
        fo_a1, fo_a2, fo_a3 = _sum_arc("fo_arc")
        bf_a1, bf_a2, bf_a3 = _sum_arc("bf_arc")

        # is_starter: this pitcher began the game on the mound for this
        # team. Detect via the first spell's start_batter_num == 1 AND
        # phase == 0 (regulation half).
        is_starter = 0
        if phase == 0 and spells:
            first = min(spells, key=lambda r: getattr(r, "start_batter_num", 0))
            if getattr(first, "start_batter_num", 0) == 1:
                is_starter = 1

        rows.append({
            "team_id": team_id,
            "player_id": db_pid,
            "phase": phase,
            "batters_faced": ps.batters_faced,
            "outs_recorded": ps.outs_recorded,
            "hits_allowed": ps.hits_allowed,
            "runs_allowed": ps.runs_allowed,
            # Task #48: ER = runs_allowed - passed-ball-charged unearned runs.
            "er": max(0, ps.runs_allowed - getattr(ps, "unearned_runs", 0)),
            "bb": ps.bb,
            "k": ps.k,
            "hr_allowed": ps.hr_allowed,
            "pitches": ps.pitches_thrown,
            "hbp_allowed":   getattr(ps, "hbp", 0),
            "unearned_runs": getattr(ps, "unearned_runs", 0),
            "sb_allowed":    sb_allowed,
            "cs_caught":     cs_caught,
            "fo_induced":    fo_induced,
            "er_arc1": er_a1, "er_arc2": er_a2, "er_arc3": er_a3,
            "k_arc1":  k_a1,  "k_arc2":  k_a2,  "k_arc3":  k_a3,
            "fo_arc1": fo_a1, "fo_arc2": fo_a2, "fo_arc3": fo_a3,
            "bf_arc1": bf_a1, "bf_arc2": bf_a2, "bf_arc3": bf_a3,
            "is_starter": is_starter,
        })
    return rows


def _compute_team_phase_outs(
    away_bstats: list[dict],
    home_bstats: list[dict],
    away_pstats: list[dict],
    home_pstats: list[dict],
    home_team_id: int,
    away_team_id: int,
) -> list[dict]:
    """Per (team, phase): unattributed outs = team_outs - sum(batter_outs).

    team_outs come from the OPPOSING side's pitcher rows (a pitcher's
    outs_recorded counts the outs the batting team made against him).
    The batter side's outs_recorded reflects only outs the engine could
    charge to a specific batter (CS / FC / pickoff handled). The
    difference is logged so the box-score Game Notes section can show
    "X outs unattributed" instead of silently dropping rows or padding
    a fake "[Caught Stealing/FC]" patch row.
    """
    def _per_phase_outs(rows: list[dict], key: str = "outs_recorded") -> dict[int, int]:
        out: dict[int, int] = {}
        for r in rows:
            out[r["phase"]] = out.get(r["phase"], 0) + (r[key] or 0)
        return out

    away_batter_outs  = _per_phase_outs(away_bstats)
    home_batter_outs  = _per_phase_outs(home_bstats)
    away_pitcher_outs = _per_phase_outs(away_pstats)  # outs vs HOME
    home_pitcher_outs = _per_phase_outs(home_pstats)  # outs vs AWAY

    rows: list[dict] = []
    all_phases = set(away_batter_outs) | set(home_batter_outs) | \
                 set(away_pitcher_outs) | set(home_pitcher_outs)
    for phase in sorted(all_phases):
        # Outs by the AWAY team's batters = total outs HOME's pitchers recorded.
        away_team_total_outs = home_pitcher_outs.get(phase, 0)
        home_team_total_outs = away_pitcher_outs.get(phase, 0)
        away_unattr = max(0, away_team_total_outs - away_batter_outs.get(phase, 0))
        home_unattr = max(0, home_team_total_outs - home_batter_outs.get(phase, 0))
        if away_unattr:
            rows.append({"team_id": away_team_id, "phase": phase,
                         "unattributed_outs": away_unattr})
        if home_unattr:
            rows.append({"team_id": home_team_id, "phase": phase,
                         "unattributed_outs": home_unattr})
    return rows


# ---------------------------------------------------------------------------
# Active roster helpers (Phase 9)
# ---------------------------------------------------------------------------

def _get_active_players(team_id: int, game_date: str) -> list[dict]:
    """Return today's playable roster: healthy is_active=1 players, topped
    up from the reserve pool (is_active=0) when injuries thin out a slot.

    Reserve promotion is ephemeral — the DB flags are not flipped, the
    reserves are only added to today's lineup pool.
    """
    from o27v2.injuries import get_active_players
    return get_active_players(team_id, game_date)


def _recently_used_pitcher_ids(
    team_id: int, game_date: str, days_back: int = 4
) -> set[int]:
    """DB ids of pitchers who appeared for `team_id` in the last
    `days_back` sim days (used to keep today's SP rested)."""
    rows = db.fetchall(
        """SELECT DISTINCT ps.player_id
             FROM game_pitcher_stats ps
             JOIN games g ON g.id = ps.game_id
            WHERE ps.team_id = ?
              AND g.played = 1
              AND g.game_date >= date(?, ?)
              AND g.game_date <  ?""",
        (team_id, game_date, f"-{days_back} days", game_date),
    )
    return {int(r["player_id"]) for r in rows}


def _pitcher_workload_state(
    team_id: int, game_date: str, lookback_days: int = 5
) -> dict[int, dict[str, int]]:
    """For every pitcher who's appeared for the team in the last
    `lookback_days` sim days, return their workload state as of `game_date`.

    Returns: { db_player_id: {
        "days_rest":  int,      # days since most recent appearance
        "pitch_debt": int,      # decayed sum of pitches over lookback window
        "p_yesterday": int,     # raw pitches thrown yesterday
        "p_5d":       int,      # raw pitches thrown over the 5-day window
        "appearances_5d": int,
    }}
    Pitchers who haven't appeared inside the window are absent from the dict
    — callers should treat them as fully rested (days_rest=99, debt=0).

    Pitch-debt decays linearly over the window so an old appearance counts
    less than yesterday's; this keeps a 4-days-ago start from looking as
    fresh as a real workhorse would feel.
    """
    rows = db.fetchall(
        """SELECT ps.player_id,
                  g.game_date AS gdate,
                  COALESCE(ps.pitches, 0) AS pitches,
                  COALESCE(ps.outs_recorded, 0) AS outs
             FROM game_pitcher_stats ps
             JOIN games g ON g.id = ps.game_id
            WHERE ps.team_id = ?
              AND g.played = 1
              AND g.game_date >= date(?, ?)
              AND g.game_date <  ?
            ORDER BY g.game_date DESC""",
        (team_id, game_date, f"-{lookback_days} days", game_date),
    )

    if not rows:
        return {}

    from datetime import date
    today = date.fromisoformat(game_date)

    state: dict[int, dict[str, int]] = {}
    for r in rows:
        pid = int(r["player_id"])
        try:
            days_ago = (today - date.fromisoformat(r["gdate"])).days
        except ValueError:
            days_ago = lookback_days
        # Linear decay: an appearance N days ago contributes
        # pitches * (lookback_days - N) / lookback_days to the debt score.
        # Yesterday (N=1) → pitches * 4/5 = 80%; 4 days ago → 20%.
        decay = max(0.0, (lookback_days - days_ago) / lookback_days)

        st = state.setdefault(pid, {
            "days_rest": 99,
            "pitch_debt": 0,
            "p_yesterday": 0,
            "p_5d": 0,
            "appearances_5d": 0,
        })
        if days_ago < st["days_rest"]:
            st["days_rest"] = days_ago
        if days_ago == 1:
            st["p_yesterday"] += int(r["pitches"])
        st["p_5d"] += int(r["pitches"])
        st["appearances_5d"] += 1
        st["pitch_debt"] += int(round(int(r["pitches"]) * decay))

    return state


def _promote_pitcher_role(players: list[dict]) -> list[dict]:
    """Task #65: roles are derived live (no `pitcher_role` is read), so
    this helper only needs to guarantee at least one `is_pitcher` arm
    exists. If the active staff is somehow empty (legacy/edge case),
    promote the highest-pitcher_skill non-joker as an emergency starter.
    """
    has_pitcher = any(p.get("is_pitcher") for p in players)
    if has_pitcher:
        return players
    pool = [p for p in players if not p.get("is_joker")]
    if not pool:
        return players
    best = max(pool, key=lambda p: float(p.get("pitcher_skill", 0.0)))
    return [dict(p, is_pitcher=1) if p["id"] == best["id"] else p
            for p in players]


# ---------------------------------------------------------------------------
# Main simulation entry point
# ---------------------------------------------------------------------------

def simulate_game(game_id: int, seed: int | None = None) -> dict:
    """
    Run an O27 game for the given DB game_id.

    - Loads active (healthy) players only (Phase 9).
    - Runs the O27 probabilistic engine.
    - Stores score, winner, and per-player stats back to DB.
    - Fires post-game injury draws, deadline trade checks, and waiver claims.
    - Returns a summary dict.
    """
    game = db.fetchone("SELECT * FROM games WHERE id = ?", (game_id,))
    if game is None:
        raise ValueError(f"Game {game_id} not found")
    if game["played"]:
        raise ValueError(f"Game {game_id} has already been played")

    game_date    = game["game_date"]
    home_team_id = game["home_team_id"]
    away_team_id = game["away_team_id"]

    home_row  = db.fetchone("SELECT * FROM teams WHERE id = ?", (home_team_id,))
    away_row  = db.fetchone("SELECT * FROM teams WHERE id = ?", (away_team_id,))

    # Phase 9: use active (non-injured) roster
    home_players = _promote_pitcher_role(_get_active_players(home_team_id, game_date))
    away_players = _promote_pitcher_role(_get_active_players(away_team_id, game_date))

    if seed is None:
        seed = random.randint(0, 999_999)

    rng = random.Random(seed)

    # Phase 10: rotate starting pitcher per game. Use game_id so the
    # rotation is deterministic and each SP gets a near-equal share of
    # the season's starts (162 / 4 ≈ 40 starts per SP per team).
    # Task #65: today's SP is picked live by Stamina + rest, so the only
    # rotation state we need is the set of arms that pitched recently.
    home_rest = _recently_used_pitcher_ids(home_team_id, game_date)
    away_rest = _recently_used_pitcher_ids(away_team_id, game_date)
    # Workload-model state: per-pitcher rolling pitch debt + days rest.
    # Drives both SP selection and the manager's relief picks (via fields
    # stamped on Player.days_rest / pitch_debt inside _db_team_to_engine).
    home_workload = _pitcher_workload_state(home_team_id, game_date)
    away_workload = _pitcher_workload_state(away_team_id, game_date)
    # Position-player workload (consecutive starts) — drives the
    # manager's rest-day decision in _db_team_to_engine.
    home_pos_wl = _position_player_workload(home_team_id, game_date)
    away_pos_wl = _position_player_workload(away_team_id, game_date)

    # Stamp the team_row with id so the rest-day RNG seed is stable.
    away_row_id = dict(away_row); away_row_id["id"] = away_team_id
    home_row_id = dict(home_row); home_row_id["id"] = home_team_id

    visitors_team = _db_team_to_engine(
        away_row_id, away_players, "visitors",
        recently_used_pitcher_ids=away_rest,
        workload=away_workload,
        position_workload=away_pos_wl,
        game_date=game_date,
    )
    home_team = _db_team_to_engine(
        home_row_id, home_players, "home",
        recently_used_pitcher_ids=home_rest,
        workload=home_workload,
        position_workload=home_pos_wl,
        game_date=game_date,
    )

    state = GameState(visitors=visitors_team, home=home_team)
    state.current_pitcher_id = _find_pitcher_id(home_team)
    # Stamp the per-game weather context (drawn at schedule time). prob.py
    # reads this; everything else passes it through.
    from o27.engine.weather import Weather
    state.weather = Weather.from_row(game)

    renderer = Renderer()
    provider = ProbabilisticProvider(rng)

    final_state, _log = run_game(state, provider, renderer)

    away_score = final_state.score["visitors"]
    home_score = final_state.score["home"]
    winner_team_id: int | None = None
    if final_state.winner == "visitors":
        winner_team_id = away_team_id
    elif final_state.winner == "home":
        winner_team_id = home_team_id

    # ----------------------------------------------------------------
    # Phase 10: extract stats BEFORE marking the game played, so a
    # mid-flow exception leaves the game retryable instead of orphaning
    # it as played-with-no-stats. This was the root cause of the ~1108
    # missing-stats games observed in the previous full sim.
    # ----------------------------------------------------------------
    all_home_players = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? ORDER BY id", (home_team_id,)
    )
    all_away_players = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? ORDER BY id", (away_team_id,)
    )
    away_bstats = _extract_batter_stats(renderer, away_team_id, all_away_players)
    home_bstats = _extract_batter_stats(renderer, home_team_id, all_home_players)
    away_pstats = _extract_pitcher_stats(final_state, away_team_id, all_away_players)
    home_pstats = _extract_pitcher_stats(final_state, home_team_id, all_home_players)
    team_phase_outs = _compute_team_phase_outs(
        away_bstats, home_bstats, away_pstats, home_pstats,
        home_team_id, away_team_id,
    )

    # Atomic write: game row + team W/L + per-player stats in one txn.
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE games SET home_score=?, away_score=?, winner_id=?,
               super_inning=?, played=1, seed=? WHERE id=?""",
            (home_score, away_score, winner_team_id,
             final_state.super_inning_number, seed, game_id),
        )
        if winner_team_id is not None:
            loser_id = away_team_id if winner_team_id == home_team_id else home_team_id
            conn.execute("UPDATE teams SET wins = wins + 1 WHERE id = ?", (winner_team_id,))
            conn.execute("UPDATE teams SET losses = losses + 1 WHERE id = ?", (loser_id,))
        # Inline inserts inside the same connection so it's all one txn.
        # Task #58: writes per-phase rows; outs_recorded is now included
        # for batters (the prior inline INSERT silently dropped it, which
        # is why every historical batter row has OR=0).
        for r in away_bstats + home_bstats:
            conn.execute(
                """INSERT INTO game_batter_stats
                   (game_id, team_id, player_id, phase, pa, ab, runs, hits,
                    doubles, triples, hr, rbi, bb, k, stays, outs_recorded,
                    hbp, sb, cs, fo, multi_hit_abs, stay_rbi, stay_hits, roe, po, e)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (game_id, r["team_id"], r["player_id"], r["phase"],
                 r["pa"], r["ab"], r["runs"], r["hits"], r["doubles"],
                 r["triples"], r["hr"], r["rbi"], r["bb"], r["k"],
                 r["stays"], r.get("outs_recorded", 0),
                 r.get("hbp", 0), r.get("sb", 0), r.get("cs", 0),
                 r.get("fo", 0), r.get("multi_hit_abs", 0),
                 r.get("stay_rbi", 0), r.get("stay_hits", 0),
                 r.get("roe", 0),
                 r.get("po", 0), r.get("e", 0)),
            )
        # Phase 11D — per-PA event log (ball_in_play events only).
        # Engine team_ids are role-strings ("home"/"away"); map to DB IDs.
        pa_log = getattr(renderer, "_pa_log", []) or []
        if pa_log:
            role_to_db = {"home": home_team_id, "away": away_team_id}
            conn.executemany(
                """INSERT INTO game_pa_log
                   (game_id, team_id, batter_id, pitcher_id, ab_seq, swing_idx,
                    choice, quality, hit_type, was_stay, stay_credited,
                    runs_scored, rbi_credited)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(game_id, role_to_db.get(e["team_id"], None),
                  int(e["batter_id"]) if e["batter_id"] is not None else None,
                  int(e["pitcher_id"]) if e["pitcher_id"] is not None else None,
                  e["ab_seq"], e["swing_idx"],
                  e["choice"], e.get("quality"), e.get("hit_type"),
                  e["was_stay"], e["stay_credited"],
                  e["runs_scored"], e["rbi_credited"])
                 for e in pa_log
                 if e["team_id"] in role_to_db],
            )
        for r in away_pstats + home_pstats:
            conn.execute(
                """INSERT INTO game_pitcher_stats
                   (game_id, team_id, player_id, phase, batters_faced,
                    outs_recorded, hits_allowed, runs_allowed, er, bb, k,
                    hr_allowed, pitches, hbp_allowed, unearned_runs,
                    sb_allowed, cs_caught, fo_induced,
                    er_arc1, er_arc2, er_arc3,
                    k_arc1,  k_arc2,  k_arc3,
                    fo_arc1, fo_arc2, fo_arc3,
                    bf_arc1, bf_arc2, bf_arc3,
                    is_starter)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (game_id, r["team_id"], r["player_id"], r["phase"],
                 r["batters_faced"], r["outs_recorded"], r["hits_allowed"],
                 r["runs_allowed"], r.get("er", r["runs_allowed"]),
                 r["bb"], r["k"],
                 r.get("hr_allowed", 0), r.get("pitches", 0),
                 r.get("hbp_allowed", 0), r.get("unearned_runs", 0),
                 r.get("sb_allowed", 0), r.get("cs_caught", 0),
                 r.get("fo_induced", 0),
                 r.get("er_arc1", 0), r.get("er_arc2", 0), r.get("er_arc3", 0),
                 r.get("k_arc1",  0), r.get("k_arc2",  0), r.get("k_arc3",  0),
                 r.get("fo_arc1", 0), r.get("fo_arc2", 0), r.get("fo_arc3", 0),
                 r.get("bf_arc1", 0), r.get("bf_arc2", 0), r.get("bf_arc3", 0),
                 r.get("is_starter", 0)),
            )
        for r in team_phase_outs:
            conn.execute(
                """INSERT OR REPLACE INTO team_phase_outs
                   (game_id, team_id, phase, unattributed_outs)
                   VALUES (?,?,?,?)""",
                (game_id, r["team_id"], r["phase"], r["unattributed_outs"]),
            )
        conn.commit()

    # -----------------------------------------------------------------------
    # Phase 9: Post-game injury draws + transaction logging
    # -----------------------------------------------------------------------
    _post_game_roster_processing(game_id, game_date, home_team_id, away_team_id, rng, seed)

    return {
        "game_id": game_id,
        "away_team": away_row["name"],
        "home_team": home_row["name"],
        "away_score": away_score,
        "home_score": home_score,
        "winner": final_state.winner,
        "super_inning": final_state.super_inning_number,
        "seed": seed,
    }


def _post_game_roster_processing(
    game_id: int,
    game_date: str,
    home_team_id: int,
    away_team_id: int,
    rng: random.Random,
    seed: int,
) -> None:
    """
    Run all Phase 9 post-game roster events:
      1. Process player returns (expired injuries).
      2. Draw new injuries for players in this game.
      3. Check for waiver claims (depleted bullpen).
      4. Check deadline / in-season trade triggers — DEFERRED until the
         last game of the calendar date so a player traded between games
         can't appear on two teams' box scores in the same day.
    All events are logged to the transactions table.
    """
    from o27v2.injuries import process_returns, process_post_game_injuries, check_waiver_claims
    from o27v2.trades import check_deadline_and_trades
    from o27v2.transactions import log_many

    games_played = db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")
    n_played = games_played["n"] if games_played else 0
    season = 1

    all_events: list[dict] = []

    # Player returns
    all_events.extend(process_returns(game_date))

    # Injury draws
    inj_rng = random.Random(seed + game_id * 31337)
    all_events.extend(
        process_post_game_injuries(game_id, game_date, home_team_id, away_team_id, inj_rng)
    )

    # Waiver claims
    all_events.extend(check_waiver_claims(game_date))

    # Trades — only fire once per calendar date, after the last game on
    # that date is in the books. Otherwise a player traded mid-day ends
    # up batting for two teams on the same date.
    remaining = db.fetchone(
        "SELECT COUNT(*) as n FROM games WHERE played = 0 AND game_date = ?",
        (game_date,),
    )
    if (remaining["n"] if remaining else 0) == 0:
        all_events.extend(check_deadline_and_trades(game_date, n_played))

    log_many(season, game_date, all_events)


def _find_pitcher_id(team: Team) -> str | None:
    """Phase 10: return the player_id of today's starter (in the lineup)."""
    # Today's SP is the lone pitcher in the batting lineup (slot 9).
    for p in team.lineup:
        if p.is_pitcher and p.pitcher_role in ("starter", "workhorse"):
            return p.player_id
    for p in team.lineup:
        if p.is_pitcher:
            return p.player_id
    # Fallback to roster (should not happen with Phase 10 setup)
    for p in team.roster:
        if p.is_pitcher:
            return p.player_id
    return team.roster[0].player_id if team.roster else None


def _insert_batter_stats(game_id: int, rows: list[dict]) -> None:
    if not rows:
        return
    db.executemany(
        """INSERT INTO game_batter_stats
           (game_id, team_id, player_id, pa, ab, runs, hits, doubles, triples,
            hr, rbi, bb, k, stays, outs_recorded,
            hbp, sb, cs, fo, multi_hit_abs, stay_rbi, stay_hits, roe, po, e)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(game_id, r["team_id"], r["player_id"], r["pa"], r["ab"], r["runs"],
          r["hits"], r["doubles"], r["triples"], r["hr"], r["rbi"],
          r["bb"], r["k"], r["stays"], r.get("outs_recorded", 0),
          r.get("hbp", 0), r.get("sb", 0), r.get("cs", 0),
          r.get("fo", 0), r.get("multi_hit_abs", 0),
          r.get("stay_rbi", 0), r.get("stay_hits", 0),
          r.get("roe", 0),
          r.get("po", 0), r.get("e", 0))
         for r in rows],
    )


def _insert_pitcher_stats(game_id: int, rows: list[dict]) -> None:
    if not rows:
        return
    db.executemany(
        """INSERT INTO game_pitcher_stats
           (game_id, team_id, player_id, batters_faced, outs_recorded,
            hits_allowed, runs_allowed, er, bb, k, hr_allowed, pitches,
            hbp_allowed, unearned_runs, sb_allowed, cs_caught, fo_induced,
            er_arc1, er_arc2, er_arc3,
            k_arc1,  k_arc2,  k_arc3,
            fo_arc1, fo_arc2, fo_arc3,
            bf_arc1, bf_arc2, bf_arc3,
            is_starter)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(game_id, r["team_id"], r["player_id"], r["batters_faced"],
          r["outs_recorded"], r["hits_allowed"], r["runs_allowed"],
          r.get("er", r["runs_allowed"]),
          r["bb"], r["k"], r.get("hr_allowed", 0), r.get("pitches", 0),
          r.get("hbp_allowed", 0), r.get("unearned_runs", 0),
          r.get("sb_allowed", 0), r.get("cs_caught", 0),
          r.get("fo_induced", 0),
          r.get("er_arc1", 0), r.get("er_arc2", 0), r.get("er_arc3", 0),
          r.get("k_arc1",  0), r.get("k_arc2",  0), r.get("k_arc3",  0),
          r.get("fo_arc1", 0), r.get("fo_arc2", 0), r.get("fo_arc3", 0),
          r.get("bf_arc1", 0), r.get("bf_arc2", 0), r.get("bf_arc3", 0),
          r.get("is_starter", 0))
         for r in rows],
    )


# ---------------------------------------------------------------------------
# Batch simulation helper
# ---------------------------------------------------------------------------

def simulate_next_n(n: int = 10, seed_base: int | None = None) -> list[dict]:
    """
    Simulate the next N unplayed games in schedule order.
    Returns list of result dicts.
    """
    games = db.fetchall(
        "SELECT id FROM games WHERE played = 0 ORDER BY game_date, id LIMIT ?", (n,)
    )
    results = []
    for i, g in enumerate(games):
        seed = None if seed_base is None else seed_base + i
        try:
            r = simulate_game(g["id"], seed=seed)
            results.append(r)
        except Exception as e:
            results.append({"game_id": g["id"], "error": str(e)})
    return results


# ---------------------------------------------------------------------------
# Date-based simulation helpers
# ---------------------------------------------------------------------------

import datetime as _dt


def get_first_scheduled_date() -> str | None:
    row = db.fetchone("SELECT MIN(game_date) as d FROM games")
    return row["d"] if row and row["d"] else None


def get_last_scheduled_date() -> str | None:
    row = db.fetchone("SELECT MAX(game_date) as d FROM games")
    return row["d"] if row and row["d"] else None


def get_earliest_unplayed_date() -> str | None:
    row = db.fetchone("SELECT MIN(game_date) as d FROM games WHERE played = 0")
    return row["d"] if row and row["d"] else None


def get_current_sim_date() -> str | None:
    """The simulator's calendar clock. Persists in sim_meta so the user can step through
    off-days via Sim Today, while staying anchored to the next unplayed game by default."""
    row = db.fetchone("SELECT value FROM sim_meta WHERE key = 'sim_date'")
    stored = row["value"] if row and row["value"] else None
    if stored is None:
        # Lazy-init: prefer the next unplayed date so existing leagues with progress
        # show the right date. If the league is already fully played, seed to
        # last_scheduled_date + 1 so is_season_complete() returns true. Final fallback
        # is the schedule's first day for a brand-new (unplayed) schedule.
        earliest = get_earliest_unplayed_date()
        if earliest is not None:
            seed = earliest
        else:
            last = get_last_scheduled_date()
            if last is not None:
                seed = (_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat()
            else:
                first = get_first_scheduled_date()
                if first is None:
                    return None
                seed = first
        db.execute(
            "INSERT OR REPLACE INTO sim_meta (key, value) VALUES ('sim_date', ?)",
            (seed,),
        )
        return seed
    return stored


def resync_sim_clock() -> str | None:
    """Bump the clock forward to the earliest unplayed date if it has fallen behind
    (e.g. games were simulated via legacy /api/sim or single-game endpoints).
    Never moves the clock backward, and never moves it past last_scheduled_date+1."""
    current = get_current_sim_date()
    earliest = get_earliest_unplayed_date()
    if earliest is None:
        # Season complete — push clock past the last game so is_season_complete() is true.
        last = get_last_scheduled_date()
        if last is not None:
            advance_sim_clock((_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat())
        return get_current_sim_date()
    if current is None or earliest > current:
        set_sim_date(earliest)
    return get_current_sim_date()


def set_sim_date(date: str | None) -> None:
    if date is None:
        db.execute("DELETE FROM sim_meta WHERE key = 'sim_date'")
    else:
        db.execute(
            "INSERT OR REPLACE INTO sim_meta (key, value) VALUES ('sim_date', ?)",
            (date,),
        )


def is_season_complete() -> bool:
    # Authoritative: no unplayed games left.
    if get_earliest_unplayed_date() is None:
        return True
    current = get_current_sim_date()
    last = get_last_scheduled_date()
    if current is None or last is None:
        return True
    return current > last


def get_all_star_date() -> str | None:
    """The day games resume *after* the All-Star break — i.e. the simulator's
    target when the user clicks "Sim to All-Star Break".

    The schedule generator carves out a 4-day no-games gap mid-season (see
    o27v2/schedule.py). We detect that gap by scanning consecutive game
    dates: the largest gap inside the season is the ASB. The returned date
    is the last day BEFORE the gap — sim_through(this) plays everything up
    to the break, and the next-day clock advance lands on the resume day.

    Falls back to the calendar midpoint if no gap is detected (e.g. legacy
    schedules that predate the series-aware generator)."""
    first = get_first_scheduled_date()
    last = get_last_scheduled_date()
    if not first or not last:
        return None

    # Distinct game dates in order. A gap > 1 day means the schedule
    # carved out a break; the largest such gap is the ASB.
    rows = db.fetchall(
        "SELECT DISTINCT game_date FROM games ORDER BY game_date"
    )
    dates = [_dt.date.fromisoformat(r["game_date"]) for r in rows]
    largest_gap = 0
    pre_break: _dt.date | None = None
    for i in range(1, len(dates)):
        delta = (dates[i] - dates[i - 1]).days
        if delta > largest_gap:
            largest_gap = delta
            pre_break = dates[i - 1]
    if pre_break is not None and largest_gap >= 3:
        return pre_break.isoformat()

    f = _dt.date.fromisoformat(first)
    l = _dt.date.fromisoformat(last)
    return (f + _dt.timedelta(days=(l - f).days // 2)).isoformat()


SIM_PER_REQUEST_GAME_CAP = 3000


def simulate_date(date: str, seed_base: int | None = None, max_games: int = SIM_PER_REQUEST_GAME_CAP) -> list[dict]:
    """Simulate every unplayed game whose game_date == `date`. Does NOT touch the clock."""
    games = db.fetchall(
        "SELECT id FROM games WHERE played = 0 AND game_date = ? ORDER BY id LIMIT ?",
        (date, max_games),
    )
    results = []
    for i, g in enumerate(games):
        seed = None if seed_base is None else seed_base + i
        try:
            results.append(simulate_game(g["id"], seed=seed))
        except Exception as e:
            results.append({"game_id": g["id"], "error": str(e)})
    return results


def simulate_through(target_date: str, seed_base: int | None = None, max_games: int = SIM_PER_REQUEST_GAME_CAP) -> list[dict]:
    """Simulate every unplayed game with game_date <= `target_date`. Does NOT touch the clock."""
    games = db.fetchall(
        "SELECT id FROM games WHERE played = 0 AND game_date <= ? ORDER BY game_date, id LIMIT ?",
        (target_date, max_games),
    )
    results = []
    for i, g in enumerate(games):
        seed = None if seed_base is None else seed_base + i
        try:
            results.append(simulate_game(g["id"], seed=seed))
        except Exception as e:
            results.append({"game_id": g["id"], "error": str(e)})
    return results


def advance_sim_clock(new_date: str) -> None:
    """Move the sim clock forward to `new_date` (never backward). Caller computes target."""
    current = get_current_sim_date()
    if current is None or new_date > current:
        set_sim_date(new_date)
