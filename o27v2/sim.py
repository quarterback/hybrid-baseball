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
import json
import random
import sys
import os
import threading
import time

_workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _workspace not in sys.path:
    sys.path.insert(0, _workspace)

from o27.engine.state import GameState, Team, Player, PitchEntry
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.render.render import Renderer

from o27v2 import db
import o27v2.config as v2cfg
from o27v2 import scout as _scout


# Process-wide serialisation for sim execution. Flask's dev server is
# threaded by default, so without this two concurrent sim requests
# (e.g. a bulk "Sim to All-Star Break" click while a single-game Sim
# button is mid-flight, two browser tabs, or a multi-season run racing
# a manual sim) both pass the per-game `played=0` check before either
# commits, then one wins the UPDATE and the other raises
# "Game N has already been played" — corrupting stats along the way.
# RLock lets bulk sim drivers re-enter simulate_game on the same thread.
_SIM_LOCK = threading.RLock()


class GameAlreadyPlayedError(ValueError):
    """Raised by simulate_game when its game already has played=1.
    Subclass of ValueError so existing `except ValueError` handlers
    (e.g. /api/sim/<id>) still see it; bulk drivers catch this type
    specifically and silently skip — the work is already done."""


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

# Canonical 8 fielding positions (excluding pitcher). Every starting
# fielder must land on exactly one of these.
_CANONICAL_FIELDING_8 = ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF")


def _assign_game_positions(starters: list, sp: list, dhs: list) -> None:
    """Stamp `game_position` on every player in today's batting lineup.

    - 8 starting fielders → one of {C, 1B, 2B, 3B, SS, LF, CF, RF}.
      Players whose static `position` already matches one of these slots
      keep it. Utility players (or excess at a single position) are
      placed in remaining slots based on glove-rating fit:
        infield slots → defense_infield
        outfield slots → defense_outfield
        catcher slot → defense_catcher
    - SP → "P".
    - Jokers (DH-pool) → "J" — every team carries exactly 3 jokers; they
      bat as tactical pinch-hitters but don't field. (Jokers MAY enter
      the field as defensive subs mid-game; that move shows up via a
      runtime `game_position` mutation, not at lineup build time.)
    """
    assigned: dict[str, object] = {}   # position → player
    unassigned: list = []
    for p in starters:
        pos = (p.position or "").upper()
        if pos in _CANONICAL_FIELDING_8 and pos not in assigned:
            assigned[pos] = p
            p.game_position = pos
        else:
            unassigned.append(p)

    open_slots = [s for s in _CANONICAL_FIELDING_8 if s not in assigned]

    # Greedy best-fit: at each step pick the (player, slot) pair with the
    # highest fit score. 8×8 search is tiny.
    while unassigned and open_slots:
        best_score = -1.0
        best_pair = None
        for p in unassigned:
            for slot in open_slots:
                if slot in _INFIELD_POSITIONS:
                    score = float(getattr(p, "defense_infield", 0.5) or 0.5)
                elif slot in _OUTFIELD_POSITIONS:
                    score = float(getattr(p, "defense_outfield", 0.5) or 0.5)
                else:   # C
                    score = float(getattr(p, "defense_catcher", 0.5) or 0.5)
                if score > best_score:
                    best_score = score
                    best_pair = (p, slot)
        if best_pair is None:
            break
        p, slot = best_pair
        p.game_position = slot
        unassigned.remove(p)
        open_slots.remove(slot)

    # Anything that didn't get a slot (shouldn't happen with 8 fielders +
    # 8 slots, but guard) falls back to the static position so the box
    # score still has SOMETHING to display.
    for p in unassigned:
        p.game_position = p.position or "UT"

    for p in sp:
        p.game_position = "P"
    for p in dhs:
        # Jokers bat from the DH pool. Every team carries exactly 3; they
        # remain "J" on the box score until/unless one is moved to the
        # field as a defensive sub.
        p.game_position = "J"


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
        # Phase 5e — work-ethic / work-habits stamp. Defaults of 50 / 0.5
        # produce engine-identical behavior on legacy DB rows that
        # predate these columns. Used by _roll_today_condition to shift
        # the per-game wellness draw and (post-game) by the cup updater.
        player.work_ethic  = int(p.get("work_ethic")  or 50)
        player.work_habits = int(p.get("work_habits") or 50)
        player.habit_cup   = float(p.get("habit_cup") if p.get("habit_cup") is not None else 0.5)
        # Pitch-type activation: load repertoire JSON onto Player so the
        # engine's _select_pitch() can sample from it. Legacy rows
        # (NULL repertoire) leave Player.repertoire = [] which the
        # engine treats as "no typed pitches" and falls back to the
        # aggregate Stuff/Command/Movement path.
        if p.get("is_pitcher"):
            player.release_angle  = float(p.get("release_angle")  if p.get("release_angle")  is not None else 0.5)
            player.pitch_variance = float(p.get("pitch_variance") if p.get("pitch_variance") is not None else 0.0)
            player.grit           = float(p.get("grit")           if p.get("grit")           is not None else 0.5)
            rep_json = p.get("repertoire")
            if rep_json:
                try:
                    raw = json.loads(rep_json) if isinstance(rep_json, str) else rep_json
                    player.repertoire = [
                        PitchEntry(
                            pitch_type=str(e["pitch_type"]),
                            quality=float(e.get("quality", 0.5)),
                            usage_weight=float(e.get("usage_weight", 1.0)),
                        )
                        for e in raw if e.get("pitch_type")
                    ]
                except (ValueError, TypeError, KeyError):
                    player.repertoire = []
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

    # Phase 5e/5f: habit-bench pass. Fires BEFORE the rest-day pass so
    # a cold-cup starter can be swapped for a hot-cup bench fielder of
    # comparable skill before the manager's rest-day logic considers
    # the new lineup. Capped at one swap per game.
    #
    # Threshold sensitivity scales with `mgr_bench_usage`:
    #   usage = 0.0 (old-school skipper) — only fires on extreme
    #     slumps (cup ≤ 0.15), and the bench fielder must be a clear
    #     upgrade (cup at least +0.40 above the starter's).
    #   usage = 0.5 (default) — fires on cup ≤ 0.30, requires a
    #     +0.30 cup gap. Same as the original Phase 5f thresholds.
    #   usage = 1.0 (analytics-forward) — fires on cup ≤ 0.45,
    #     requires only a +0.20 cup gap.
    #
    # Skill tolerance stays flat at 6 _bat_score grade-points across
    # all managers — every skipper agrees not to swap a stud for a
    # scrub. The "how easily I bench" question is the right knob.
    bench_usage = float(team_row.get("mgr_bench_usage") or 0.5)
    _HABIT_BENCH_CUP_THRESHOLD = 0.15 + bench_usage * 0.30   # 0.15 .. 0.45
    _HABIT_BENCH_CUP_DELTA     = 0.40 - bench_usage * 0.20   # 0.40 .. 0.20
    _HABIT_BENCH_SKILL_TOLERANCE = 6.0   # _bat_score grade points

    def _try_habit_bench():
        if not bench_fielders:
            return
        # Walk starters slump-first so the worst cup gets the swap if
        # only one is available.
        starters_by_cup = sorted(
            list(enumerate(starting_fielders)),
            key=lambda iv: float(getattr(iv[1], "habit_cup", 0.5)),
        )
        for idx, starter in starters_by_cup:
            starter_cup = float(getattr(starter, "habit_cup", 0.5))
            if starter_cup >= _HABIT_BENCH_CUP_THRESHOLD:
                return  # remaining starters all have healthy cups
            starter_score = _bat_score(starter)
            candidates = [
                pl for pl in bench_fielders
                if abs(_bat_score(pl) - starter_score) <= _HABIT_BENCH_SKILL_TOLERANCE
                and float(getattr(pl, "habit_cup", 0.5)) >= starter_cup + _HABIT_BENCH_CUP_DELTA
            ]
            if not candidates:
                continue
            # Hottest cup among comparable bench guys.
            replacement = max(candidates,
                              key=lambda pl: float(getattr(pl, "habit_cup", 0.5)))
            starting_fielders[idx] = replacement
            bench_fielders.remove(replacement)
            bench_fielders.append(starter)
            return  # one swap per game
    _try_habit_bench()

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

    # Stamp per-game fielding positions BEFORE building the lineup so the
    # ordering pass already sees concrete positions on every player. Jokers
    # picked from the DH pool below get their "J" tag here too.
    _assign_game_positions(starting_fielders, todays_sp, dhs)

    # Build the 9-batter base lineup: 8 fielders + SP, ordered by talent.
    lineup = _ordered_lineup(starting_fielders, todays_sp)

    # Pick today's 3 jokers from the non-starter bat pool (all DHs + any
    # bench fielders not in the starting 8). These are tactical pinch-
    # hitters — manager AI inserts them per PA based on leverage, each
    # at most once per cycle through the order.
    bench_pool = list(dhs) + list(fielders[8:])
    jokers     = _pick_jokers(bench_pool, n=3)
    # Force-stamp every joker with game_position="J" — the per-bench-
    # source picker can pull a non-DH (e.g. utility infielder) so we
    # can't rely on `_assign_game_positions(... dhs)` having tagged them.
    for j in jokers:
        j.game_position = "J"

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

def _extract_batter_stats(renderer: Renderer, team_id: int, players: list[dict],
                          engine_team=None) -> list[dict]:
    """Extract per-phase batter stats from the Renderer's per-phase snapshots.

    Task #58: yields one row per (player, phase) tuple, where phase 0 is
    regulation and phase N >= 1 is super-inning round N. Players with no
    activity in a phase are omitted.
    """
    team_player_ids: set[int] = {p["id"] for p in players}
    # Build engine-player lookup so we can pull `game_position` (set on
    # the Player object at lineup-build time by `_assign_game_positions`).
    engine_players_by_id: dict[str, object] = {}
    if engine_team is not None:
        for ep in (getattr(engine_team, "roster", None) or []):
            engine_players_by_id[str(ep.player_id)] = ep
        for ep in (getattr(engine_team, "lineup", None) or []):
            engine_players_by_id.setdefault(str(ep.player_id), ep)
        for ep in (getattr(engine_team, "jokers_available", None) or []):
            engine_players_by_id.setdefault(str(ep.player_id), ep)
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
                "c2_op_1b":  getattr(bstat, "c2_op_1b", 0),
                "c2_adv_1b": getattr(bstat, "c2_adv_1b", 0),
                "c2_op_2b":  getattr(bstat, "c2_op_2b", 0),
                "c2_adv_2b": getattr(bstat, "c2_adv_2b", 0),
                "c2_op_3b":  getattr(bstat, "c2_op_3b", 0),
                "c2_adv_3b": getattr(bstat, "c2_adv_3b", 0),
                "game_position": str(getattr(
                    engine_players_by_id.get(str(engine_pid)),
                    "game_position", "") or ""),
                "entry_type": str(getattr(bstat, "entry_type", "") or "starter"),
                "replaced_player_id": (
                    int(getattr(bstat, "replaced_player_id", "") or 0) or None
                ),
                "gidp": getattr(bstat, "gidp", 0),
                "gitp": getattr(bstat, "gitp", 0),
                "roe": getattr(bstat, "roe", 0),
                "po": getattr(bstat, "po", 0),
                "a":  getattr(bstat, "a",  0),
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


def _decorate_pitcher_pitch_mix(renderer, pstats: list[dict]) -> None:
    """Compute per-pitcher hit-type breakdown + pitch-mix from the renderer
    PA log and stamp the new columns on each row of `pstats` in place.

    Fields added:
      singles_allowed / doubles_allowed / triples_allowed (xRA v3 inputs)
      fastball_pct / breaking_pct / offspeed_pct + primary_pitch

    PA-log rows have pitcher_id (engine string), phase, pitch_type, hit_type.
    Pitchers without a typed repertoire emit pitch_type=NULL — those rows
    contribute to hit-type tallies but produce 0.0 mix percentages.
    """
    pa_log = getattr(renderer, "_pa_log", []) or []
    if not pa_log:
        return

    # Pitch-type → bucket. Matches the buckets exposed on the player page.
    fb_keys      = {"four_seam", "sinker", "cutter"}
    breaking_keys = {"slider", "sisko_slider", "walking_slider",
                     "curveball", "curve_10_to_2", "screwball",
                     "gyroball", "spitter"}
    offspeed_keys = {"changeup", "vulcan_changeup", "splitter",
                     "palmball", "knuckleball", "eephus"}

    # Aggregate by (pitcher_id_int, phase).
    agg: dict[tuple[int, int], dict] = {}
    for entry in pa_log:
        pid_raw = entry.get("pitcher_id")
        if pid_raw is None:
            continue
        try:
            pid = int(pid_raw)
        except (ValueError, TypeError):
            continue
        phase = int(entry.get("phase", 0) or 0)
        bucket = agg.setdefault((pid, phase), {
            "singles": 0, "doubles": 0, "triples": 0,
            "fb": 0, "br": 0, "off": 0, "any_pitch": 0,
            "pitch_counts": {},
        })
        ht = entry.get("hit_type") or ""
        # Only true safety hits count toward the H-shape. infield_single
        # is treated as a single.
        if ht in ("single", "infield_single"):
            bucket["singles"] += 1
        elif ht == "double":
            bucket["doubles"] += 1
        elif ht == "triple":
            bucket["triples"] += 1
        pt = entry.get("pitch_type")
        if pt:
            bucket["any_pitch"] += 1
            bucket["pitch_counts"][pt] = bucket["pitch_counts"].get(pt, 0) + 1
            if pt in fb_keys:
                bucket["fb"] += 1
            elif pt in breaking_keys:
                bucket["br"] += 1
            elif pt in offspeed_keys:
                bucket["off"] += 1

    for row in pstats:
        key = (int(row["player_id"]), int(row.get("phase", 0)))
        b = agg.get(key)
        if not b:
            row.setdefault("singles_allowed", 0)
            row.setdefault("doubles_allowed", 0)
            row.setdefault("triples_allowed", 0)
            row.setdefault("fastball_pct", 0.0)
            row.setdefault("breaking_pct", 0.0)
            row.setdefault("offspeed_pct", 0.0)
            row.setdefault("primary_pitch", "")
            continue
        row["singles_allowed"] = b["singles"]
        row["doubles_allowed"] = b["doubles"]
        row["triples_allowed"] = b["triples"]
        total_typed = max(1, b["any_pitch"])
        row["fastball_pct"] = round(b["fb"] / total_typed, 3)
        row["breaking_pct"] = round(b["br"] / total_typed, 3)
        row["offspeed_pct"] = round(b["off"] / total_typed, 3)
        pc = b["pitch_counts"]
        row["primary_pitch"] = max(pc.items(), key=lambda kv: kv[1])[0] if pc else ""


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
# Phase 3 — per-game per-player condition roll
# ---------------------------------------------------------------------------

# Tuning knobs for the daily-condition multiplier. Picked to produce a
# noticeable "bad day" tail without flattening talent: σ=0.07 ⇒ ~16% of
# players land below 0.93 on any given game (a real off-day rate),
# clamped to [0.85, 1.15] so condition can't fully erase or double a
# player's effective talent.
_CONDITION_SIGMA      = 0.07
_CONDITION_MIN        = 0.85
_CONDITION_MAX        = 1.15

# Weather penalties on the μ of the condition roll. Bad weather doesn't
# uniformly degrade everyone (the engine's existing per-PA weather hooks
# already do that) — instead it shifts the daily-condition distribution
# down so bad-weather days have MORE bad performances. Heat / cold /
# heavy rain compounds when stacked.
_HEAT_PENALTY     = -0.025   # `temp == "hot"`
_COLD_PENALTY     = -0.020   # `temp == "cold"`
_HEAVY_RAIN_PEN   = -0.030   # `precipitation == "heavy"`
_LIGHT_RAIN_PEN   = -0.010   # `precipitation == "light"`

# Phase 5e — work-ethic / work-habits scale on the daily μ. Both are
# 20-80 grades; we centre at 50 and divide by 500 so the per-grade
# contribution is small but the extreme ends matter:
#   work_ethic = 80  → μ += +0.06   (≈ "always shows up ready")
#   work_ethic = 20  → μ += -0.06   (≈ "phones it in")
#   work_habits = 80 with full cup → μ += +0.06; with empty cup → -0.06
# So a great-ethic / hot-cup player on a mild day has μ ~ 1.12 (capped
# by [0.85, 1.15] floor on the gauss draw); a bad-ethic / cold-cup
# player on a hot rainy day has μ ~ 0.83 → frequent off games.
_ETHIC_SCALE   = 1.0 / 500.0
_HABITS_SCALE  = 1.0 / 500.0


def _condition_mu_for_weather(weather) -> float:
    """Compute the μ of the condition roll for the day. 1.0 in mild
    weather; subtracts small penalties in extreme conditions."""
    mu = 1.0
    if weather is None:
        return mu
    temp   = getattr(weather, "temp", None)
    precip = getattr(weather, "precipitation", None)
    if temp == "hot":
        mu += _HEAT_PENALTY
    elif temp == "cold":
        mu += _COLD_PENALTY
    if precip == "heavy":
        mu += _HEAVY_RAIN_PEN
    elif precip == "light":
        mu += _LIGHT_RAIN_PEN
    return mu


# Phase 5e — habit-cup deltas per game. The cup is in [0, 1]; we move
# it ±_HABIT_CUP_STEP based on the game's performance, then clamp.
# Step is small (≈ 12 games to swing the full range) so streaks build
# gradually instead of flipping every game.
_HABIT_CUP_STEP = 0.04
_HABIT_CUP_MIN  = 0.0
_HABIT_CUP_MAX  = 1.0

# Phase 5g — motivator-archetype cup-fill. Some manager personas can
# nudge a player's cup upward independently of the player's own
# game line — the leadership / morale layer. The trigger is a
# per-player dice roll each game, gated by:
#   - grit   (≈ stamina_unit) — gritty players respond to motivators
#   - talent (player's primary skill) — stars get the leader bump
#   - team last-10-game form — winning teams have momentum to share
# Three archetypes qualify (the morale-coded skippers in
# managers.py): `players_manager`, `iron_manager`, `fiery`. Other
# archetypes don't fill cups via this path — those teams rely on
# the standard performance-driven cup updates only.
_MOTIVATOR_ARCHETYPES = frozenset({"players_manager", "iron_manager", "fiery"})
_MOTIVATOR_BASE_P     = 0.02   # 2% floor — even on a losing team a low-grit
                                # bench scrub has a sliver of upside
_MOTIVATOR_GRIT_W     = 0.08   # +8 pp at full grit (stamina=95)
_MOTIVATOR_TALENT_W   = 0.08   # +8 pp at grade-80 talent
_MOTIVATOR_FORM_W     = 0.05   # +5 pp at full hot streak (10-0 last 10)
_MOTIVATOR_CUP_FILL   = 0.02   # half of _HABIT_CUP_STEP — "small amount"


def _team_last10_winpct(team_id: int) -> float:
    """Winning percentage over the team's last 10 played regular-season
    games. Returns 0.5 (neutral) when the team has played < 1 game."""
    rows = db.fetchall(
        "SELECT winner_id FROM games "
        "WHERE played = 1 AND COALESCE(is_playoff, 0) = 0 "
        "  AND (home_team_id = ? OR away_team_id = ?) "
        "ORDER BY game_date DESC, id DESC LIMIT 10",
        (team_id, team_id),
    )
    if not rows:
        return 0.5
    wins = sum(1 for r in rows if r["winner_id"] == team_id)
    return wins / len(rows)


def _motivator_cup_fill(team_id: int, stat_rows: list[dict],
                         rng: random.Random) -> None:
    """For motivator-coded managers, roll a small cup boost for every
    player who appeared in the game. Probability scales with player
    grit + talent + team's last-10 form."""
    team = db.fetchone(
        "SELECT manager_archetype FROM teams WHERE id = ?", (team_id,)
    )
    if not team or team["manager_archetype"] not in _MOTIVATOR_ARCHETYPES:
        return

    # Only roll for players who actually appeared (any PA or any out).
    pids = [
        r["player_id"] for r in stat_rows
        if r.get("player_id")
        and ((r.get("pa") or 0) > 0 or (r.get("outs_recorded") or 0) > 0)
    ]
    if not pids:
        return

    last10 = _team_last10_winpct(team_id)
    team_form = max(-1.0, min(1.0, (last10 - 0.5) * 2.0))
    form_bonus = max(0.0, team_form) * _MOTIVATOR_FORM_W

    placeholders = ",".join(["?"] * len(pids))
    players = db.fetchall(
        f"SELECT id, skill, pitcher_skill, stamina, is_pitcher "
        f"FROM players WHERE id IN ({placeholders})",
        tuple(pids),
    )

    fills: list[int] = []
    for p in players:
        grit_unit = max(0.0, min(1.0, ((p.get("stamina") or 50) - 20) / 60.0))
        talent_grade = (p.get("pitcher_skill") if p.get("is_pitcher")
                        else p.get("skill"))
        talent_unit = max(0.0, min(1.0, ((talent_grade or 50) - 20) / 60.0))
        chance = (_MOTIVATOR_BASE_P
                  + _MOTIVATOR_GRIT_W   * grit_unit
                  + _MOTIVATOR_TALENT_W * talent_unit
                  + form_bonus)
        if rng.random() < chance:
            fills.append(p["id"])

    if fills:
        with db.get_conn() as conn:
            for pid in fills:
                conn.execute(
                    "UPDATE players SET habit_cup = "
                    "MIN(?, MAX(?, habit_cup + ?)) WHERE id = ?",
                    (_HABIT_CUP_MAX, _HABIT_CUP_MIN, _MOTIVATOR_CUP_FILL, pid),
                )
            conn.commit()


def _update_habit_cups(batter_rows: list[dict], pitcher_rows: list[dict]) -> None:
    """Push each player's `habit_cup` toward 1.0 on a good game and
    toward 0.0 on a bad one. "Good" thresholds are deliberately mild
    so the cup actually moves over a 30-game season:
      Hitter good day: 1+ hit AND on-base ≥ 0.333 (1-for-3 with a walk
                       counts; 0-for-3 with a walk doesn't).
      Hitter bad day:  0-for-3+ AND no walk / HBP.
      Pitcher good day: 9+ outs AND ≤2 ER (3 IP, 6.00 ERA-equivalent).
      Pitcher bad day:  6+ outs AND ≥4 ER, OR ≤3 outs AND ≥3 ER.
    Idle bench days don't move the cup either way."""
    deltas: dict[int, float] = {}

    for r in batter_rows:
        pid = r.get("player_id")
        if pid is None:
            continue
        pa = r.get("pa") or 0
        ab = r.get("ab") or 0
        h  = r.get("hits") or 0
        bb = r.get("bb") or 0
        hbp = r.get("hbp") or 0
        if pa < 2:
            continue   # idle / pinch-only — no cup movement
        on_base = h + bb + hbp
        obp = on_base / pa if pa else 0.0
        if h >= 1 and obp >= 0.333:
            deltas[pid] = deltas.get(pid, 0.0) + _HABIT_CUP_STEP
        elif ab >= 3 and h == 0 and bb == 0 and hbp == 0:
            deltas[pid] = deltas.get(pid, 0.0) - _HABIT_CUP_STEP

    for r in pitcher_rows:
        pid = r.get("player_id")
        if pid is None:
            continue
        outs = r.get("outs_recorded") or 0
        er   = r.get("er", r.get("runs_allowed", 0)) or 0
        if outs < 1:
            continue
        if outs >= 9 and er <= 2:
            deltas[pid] = deltas.get(pid, 0.0) + _HABIT_CUP_STEP
        elif (outs >= 6 and er >= 4) or (outs <= 3 and er >= 3):
            deltas[pid] = deltas.get(pid, 0.0) - _HABIT_CUP_STEP

    if not deltas:
        return
    # Apply via individual UPDATE statements with clamp inline, in one
    # connection so it's a single transaction.
    with db.get_conn() as conn:
        for pid, d in deltas.items():
            conn.execute(
                "UPDATE players SET habit_cup = "
                "MIN(?, MAX(?, habit_cup + ?)) WHERE id = ?",
                (_HABIT_CUP_MAX, _HABIT_CUP_MIN, d, pid),
            )
        conn.commit()


def _roll_today_condition(visitors: Team, home: Team, weather, rng: random.Random) -> None:
    """Roll a `today_condition` multiplier for every player on both
    teams. Stamped directly on the Player dataclass field so prob.py's
    existing `getattr(player, "today_condition", 1.0)` reads pick it up.

    Per-player μ folds in:
      - weather penalties (heat / cold / rain)
      - work_ethic shift  (constant for the season; locks at age 30)
      - work_habits shift (modulated by `habit_cup`; cup fills with
        good performance and drains with bad — so a high-habits
        player on a hot streak gets the full +shift, on a cold streak
        the full -shift)
      - per-team "hot factor" — a coordinated game-level offense tilt
        rolled once per team per game. Lineups have hot days and cold
        days that move TOGETHER (not 9 independent dice), which is the
        mechanism that widens game-to-game run distribution without
        flattening individual talent signals. Bounded so it can't drag
        any player past the broader [0.70, 1.30] condition cap.
    """
    weather_mu = _condition_mu_for_weather(weather)
    # One hot factor per team per game. Gaussian(1.0, 0.10), clipped to
    # ±25%. Identity at 1.0 — pre-widening sims reproduce.
    hot_factors = {
        id(visitors): max(0.78, min(1.22, rng.gauss(1.0, 0.10))),
        id(home):     max(0.78, min(1.22, rng.gauss(1.0, 0.10))),
    }
    for team in (visitors, home):
        hot = hot_factors[id(team)]
        for p in team.roster:
            ethic_shift  = (getattr(p, "work_ethic",  50) - 50) * _ETHIC_SCALE
            habits_raw   = (getattr(p, "work_habits", 50) - 50) * _HABITS_SCALE
            cup          = float(getattr(p, "habit_cup", 0.5))
            # cup=0.5 ⇒ neutral; cup=1.0 ⇒ +1× habits; cup=0.0 ⇒ -1× habits.
            cup_factor   = (cup - 0.5) * 2.0
            habits_shift = habits_raw * cup_factor
            mu = weather_mu + ethic_shift + habits_shift
            cond = rng.gauss(mu, _CONDITION_SIGMA)
            # Apply the team-wide hot factor only to non-pitchers — the
            # pitcher's own form variance handles their day-to-day swing.
            if not getattr(p, "is_pitcher", False):
                cond *= hot
            p.today_condition = max(0.70, min(1.30, cond))
        # Stash the hot factor on the Team so post-game inspectors can
        # see why a slugfest or a duel happened.
        team.today_hot_factor = hot


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
    with _SIM_LOCK:
        return _simulate_game_locked(game_id, seed=seed)


def _simulate_game_locked(game_id: int, seed: int | None = None) -> dict:
    game = db.fetchone("SELECT * FROM games WHERE id = ?", (game_id,))
    if game is None:
        raise ValueError(f"Game {game_id} not found")
    if game["played"]:
        raise GameAlreadyPlayedError(f"Game {game_id} has already been played")

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
    # Stamp the home park's dimensions (lf/lcf/cf/rcf/rf/wall_h). Read
    # by o27.engine.park_effects.apply_park_effects() after the engine's
    # categorical hit_type is decided, to reshape outcomes against
    # actual fence geometry.
    try:
        _home_park_dims_raw = home_row.get("park_dimensions") if home_row else None
        if _home_park_dims_raw:
            state.park_dimensions = json.loads(_home_park_dims_raw)
    except (ValueError, TypeError):
        state.park_dimensions = None

    # Phase 3: roll today_condition once per player per game so any player
    # — ace or replacement bat — can have an off day. The roll is centred
    # at 1.0 with σ=0.07, weather-modulated: extreme heat / cold / rain
    # shifts μ down so bad-weather days produce more bad performances
    # without uniformly degrading every player. Read in prob.py's
    # _pitch_probs and contact_quality alongside the existing today_form.
    _roll_today_condition(visitors_team, home_team, state.weather, rng)

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
    away_bstats = _extract_batter_stats(renderer, away_team_id, all_away_players,
                                        engine_team=visitors_team)
    home_bstats = _extract_batter_stats(renderer, home_team_id, all_home_players,
                                        engine_team=home_team)
    away_pstats = _extract_pitcher_stats(final_state, away_team_id, all_away_players)
    home_pstats = _extract_pitcher_stats(final_state, home_team_id, all_home_players)
    # Per-pitcher hit-type + pitch-type breakdown, computed from the PA log.
    # Decorates each row in away_pstats/home_pstats in place.
    _decorate_pitcher_pitch_mix(renderer, away_pstats + home_pstats)
    team_phase_outs = _compute_team_phase_outs(
        away_bstats, home_bstats, away_pstats, home_pstats,
        home_team_id, away_team_id,
    )

    # Atomic write: game row + team W/L + per-player stats in one txn.
    with db.get_conn() as conn:
        # Clear any stat rows left over from a prior interrupted attempt
        # at this game (played=0 but stats already inserted). Without this,
        # the retry hits UNIQUE(player_id, game_id, phase) on the very
        # first batter row and the day's whole sweep fails. Same-txn dup
        # rows from a real bug would still collide on the inserts below.
        conn.execute("DELETE FROM game_batter_stats  WHERE game_id = ?", (game_id,))
        conn.execute("DELETE FROM game_pitcher_stats WHERE game_id = ?", (game_id,))
        conn.execute("DELETE FROM game_pa_log        WHERE game_id = ?", (game_id,))
        conn.execute("DELETE FROM team_phase_outs    WHERE game_id = ?", (game_id,))
        conn.execute(
            """UPDATE games SET home_score=?, away_score=?, winner_id=?,
               super_inning=?, played=1, seed=? WHERE id=?""",
            (home_score, away_score, winner_team_id,
             final_state.super_inning_number, seed, game_id),
        )
        if winner_team_id is not None and not game.get("is_playoff"):
            # Regular-season W-L only — playoff results are tracked on
            # playoff_series rows, not on teams.wins/losses.
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
                    hbp, sb, cs, fo, multi_hit_abs, stay_rbi, stay_hits,
                    c2_op_1b, c2_adv_1b, c2_op_2b, c2_adv_2b, c2_op_3b, c2_adv_3b,
                    game_position, entry_type, replaced_player_id,
                    gidp, gitp,
                    roe, po, a, e)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (game_id, r["team_id"], r["player_id"], r["phase"],
                 r["pa"], r["ab"], r["runs"], r["hits"], r["doubles"],
                 r["triples"], r["hr"], r["rbi"], r["bb"], r["k"],
                 r["stays"], r.get("outs_recorded", 0),
                 r.get("hbp", 0), r.get("sb", 0), r.get("cs", 0),
                 r.get("fo", 0), r.get("multi_hit_abs", 0),
                 r.get("stay_rbi", 0), r.get("stay_hits", 0),
                 r.get("c2_op_1b", 0), r.get("c2_adv_1b", 0),
                 r.get("c2_op_2b", 0), r.get("c2_adv_2b", 0),
                 r.get("c2_op_3b", 0), r.get("c2_adv_3b", 0),
                 r.get("game_position", ""),
                 r.get("entry_type", "starter"),
                 r.get("replaced_player_id"),
                 r.get("gidp", 0), r.get("gitp", 0),
                 r.get("roe", 0),
                 r.get("po", 0), r.get("a", 0), r.get("e", 0)),
            )
        # Phase 11D — per-PA event log (ball_in_play events only).
        # Engine team_ids are role-strings ("home"/"visitors") — see
        # o27/engine/state.py:Team.team_id. The legacy mapping used "away"
        # which silently dropped every visitor's PA event from the log.
        pa_log = getattr(renderer, "_pa_log", []) or []
        if pa_log:
            role_to_db = {
                "home":     home_team_id,
                "visitors": away_team_id,
                "away":     away_team_id,  # legacy — kept for backward compat
            }
            conn.executemany(
                """INSERT INTO game_pa_log
                   (game_id, team_id, batter_id, pitcher_id, phase, ab_seq, swing_idx,
                    choice, quality, hit_type, pitch_type,
                    exit_velocity, launch_angle, spray_angle,
                    was_stay, stay_credited,
                    runs_scored, rbi_credited,
                    outs_before, bases_before, score_diff_before,
                    outs_after,  bases_after,  score_diff_after)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(game_id, role_to_db.get(e["team_id"], None),
                  int(e["batter_id"]) if e["batter_id"] is not None else None,
                  int(e["pitcher_id"]) if e["pitcher_id"] is not None else None,
                  e.get("phase", 0),
                  e["ab_seq"], e["swing_idx"],
                  e["choice"], e.get("quality"), e.get("hit_type"),
                  e.get("pitch_type"),
                  e.get("exit_velocity"), e.get("launch_angle"), e.get("spray_angle"),
                  e["was_stay"], e["stay_credited"],
                  e["runs_scored"], e["rbi_credited"],
                  e.get("outs_before"), e.get("bases_before"), e.get("score_diff_before"),
                  e.get("outs_after"),  e.get("bases_after"),  e.get("score_diff_after"))
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
                    is_starter,
                    singles_allowed, doubles_allowed, triples_allowed,
                    fastball_pct, breaking_pct, offspeed_pct, primary_pitch)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                 r.get("is_starter", 0),
                 r.get("singles_allowed", 0), r.get("doubles_allowed", 0),
                 r.get("triples_allowed", 0),
                 r.get("fastball_pct", 0.0), r.get("breaking_pct", 0.0),
                 r.get("offspeed_pct", 0.0), r.get("primary_pitch", "")),
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

    # Phase 5e: post-game habit-cup update. Each player's cup drifts
    # toward 1.0 on a good game and toward 0.0 on a bad one. Only fires
    # for regular-season games (playoffs aren't part of the season-arc
    # cup mechanic) and does nothing on legacy DB rows where the column
    # is missing — the helper itself is best-effort.
    if not game.get("is_playoff"):
        try:
            _update_habit_cups(away_bstats + home_bstats,
                               away_pstats + home_pstats)
        except Exception:
            pass

    # Phase 5g: motivator-archetype cup-fill. Independent of the
    # performance-driven cup update above — runs an extra dice roll
    # per appearing player on teams whose manager is morale-coded
    # (players_manager / iron_manager / fiery). Probability scales
    # with grit + talent + last-10 team form.
    if not game.get("is_playoff"):
        try:
            _motivator_cup_fill(away_team_id,
                                away_bstats + away_pstats, rng)
            _motivator_cup_fill(home_team_id,
                                home_bstats + home_pstats, rng)
        except Exception:
            pass

    # Phase 4: post-game playoff hook — if this game was part of a
    # playoff series, update the series and (if decided) advance the
    # bracket to the next round. winner_team_id is the same one written
    # to the games row (engine emits "visitors"/"home", not "away").
    if game.get("is_playoff"):
        try:
            from o27v2.playoffs import post_playoff_game
            post_playoff_game({
                "id":         game_id,
                "series_id":  game.get("series_id"),
                "winner_id":  winner_team_id,
                "game_date":  game_date,
            }, rng_seed=seed)
        except Exception as e:
            # Don't let a bracket bookkeeping bug crash the sim.
            try:
                from o27v2.web.app import app as _app
                _app.logger.exception("post_playoff_game failed: %s", e)
            except Exception:
                pass

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
    # Task #65 cleared all stored pitcher_role values, so the prior
    # role-tagged fast path never fired — collapsed into the single loop.
    for p in team.lineup:
        if p.is_pitcher:
            return p.player_id
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
            hbp, sb, cs, fo, multi_hit_abs, stay_rbi, stay_hits,
            c2_op_1b, c2_adv_1b, c2_op_2b, c2_adv_2b, c2_op_3b, c2_adv_3b,
            roe, po, e)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(game_id, r["team_id"], r["player_id"], r["pa"], r["ab"], r["runs"],
          r["hits"], r["doubles"], r["triples"], r["hr"], r["rbi"],
          r["bb"], r["k"], r["stays"], r.get("outs_recorded", 0),
          r.get("hbp", 0), r.get("sb", 0), r.get("cs", 0),
          r.get("fo", 0), r.get("multi_hit_abs", 0),
          r.get("stay_rbi", 0), r.get("stay_hits", 0),
          r.get("c2_op_1b", 0), r.get("c2_adv_1b", 0),
          r.get("c2_op_2b", 0), r.get("c2_adv_2b", 0),
          r.get("c2_op_3b", 0), r.get("c2_adv_3b", 0),
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

    Triggers the weekly Sunday match-day waiver sweep before any games
    on a Sunday date (idempotent — never runs twice for the same
    Sunday). After the last regular-season game completes, initiates
    the playoff bracket (idempotent).
    """
    from o27v2.waivers import maybe_run_sweep
    from o27v2.playoffs import maybe_initiate as _maybe_init_playoffs
    games = db.fetchall(
        "SELECT id, game_date FROM games WHERE played = 0 ORDER BY game_date, id LIMIT ?", (n,)
    )
    results = []
    seen_sunday: set[str] = set()
    for i, g in enumerate(games):
        if g["game_date"] not in seen_sunday:
            seen_sunday.add(g["game_date"])
            try:
                maybe_run_sweep(g["game_date"])
            except Exception as e:
                results.append({"sweep_error": str(e), "date": g["game_date"]})
        seed = None if seed_base is None else seed_base + i
        try:
            r = simulate_game(g["id"], seed=seed)
            results.append(r)
        except GameAlreadyPlayedError:
            continue
        except Exception as e:
            results.append({"game_id": g["id"], "error": str(e)})
    try:
        _maybe_init_playoffs(rng_seed=seed_base)
    except Exception as e:
        results.append({"playoff_init_error": str(e)})
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
    """Simulate every unplayed game whose game_date == `date`. Does NOT touch the clock.

    Runs the weekly Sunday match-day sweep first if `date` is a Sunday
    (idempotent — see o27v2/waivers.py).
    """
    from o27v2.waivers import maybe_run_sweep
    try:
        maybe_run_sweep(date)
    except Exception as e:
        # Don't let a sweep failure block the day's simulation.
        results: list[dict] = [{"sweep_error": str(e), "date": date}]
    else:
        results = []
    games = db.fetchall(
        "SELECT id FROM games WHERE played = 0 AND game_date = ? ORDER BY id LIMIT ?",
        (date, max_games),
    )
    for i, g in enumerate(games):
        seed = None if seed_base is None else seed_base + i
        try:
            results.append(simulate_game(g["id"], seed=seed))
        except GameAlreadyPlayedError:
            continue
        except Exception as e:
            results.append({"game_id": g["id"], "error": str(e)})
    return results


def simulate_through(
    target_date: str,
    seed_base: int | None = None,
    max_games: int = SIM_PER_REQUEST_GAME_CAP,
    max_seconds: float | None = None,
) -> list[dict]:
    """Simulate every unplayed game with game_date <= `target_date`. Does NOT touch the clock.

    Runs the weekly Sunday match-day sweep at every distinct Sunday
    encountered in the date range (idempotent — see o27v2/waivers.py).
    Initiates the playoff bracket once the regular season is complete,
    and re-queries games so newly-scheduled playoff games inside the
    target window get simulated in the same call.

    `max_seconds` (when set) bounds wall-clock time spent in this call so
    the bulk-sim HTTP endpoints can return promptly enough to dodge mobile
    Safari / Fly proxy fetch timeouts. Caller loops until target reached.
    """
    from o27v2.waivers import maybe_run_sweep
    from o27v2.playoffs import maybe_initiate as _maybe_init_playoffs

    deadline = None if max_seconds is None else (time.monotonic() + max_seconds)
    results: list[dict] = []
    seen_sunday: set[str] = set()
    seen_game_ids: set[int] = set()
    iterations_remaining = 50  # safety bound on the schedule-then-sim loop

    while iterations_remaining > 0:
        iterations_remaining -= 1
        if deadline is not None and time.monotonic() >= deadline:
            break
        games = db.fetchall(
            "SELECT id, game_date FROM games WHERE played = 0 AND game_date <= ? "
            "AND id NOT IN ({}) ORDER BY game_date, id LIMIT ?".format(
                ",".join(str(i) for i in seen_game_ids) if seen_game_ids else "0"
            ),
            (target_date, max_games),
        )
        if not games:
            break
        for i, g in enumerate(games):
            if deadline is not None and time.monotonic() >= deadline:
                return results
            seen_game_ids.add(g["id"])
            if g["game_date"] not in seen_sunday:
                seen_sunday.add(g["game_date"])
                try:
                    maybe_run_sweep(g["game_date"])
                except Exception as e:
                    results.append({"sweep_error": str(e), "date": g["game_date"]})
            seed = None if seed_base is None else seed_base + len(seen_game_ids)
            try:
                results.append(simulate_game(g["id"], seed=seed))
            except GameAlreadyPlayedError:
                continue
            except Exception as e:
                results.append({"game_id": g["id"], "error": str(e)})
        try:
            init_summary = _maybe_init_playoffs(rng_seed=seed_base)
            if init_summary is None:
                # No new playoff games to drain — only re-loop if a
                # series-advance might have scheduled a game inside the
                # target window. The post-game hook already handles that
                # by inserting the next game into `games`, so we re-query.
                pass
        except Exception as e:
            results.append({"playoff_init_error": str(e)})
        # Loop again to pick up any newly-scheduled playoff games whose
        # date falls within target_date. Safe because `seen_game_ids`
        # prevents re-simulating already-played games.
    return results


def advance_sim_clock(new_date: str) -> None:
    """Move the sim clock forward to `new_date` (never backward). Caller computes target."""
    current = get_current_sim_date()
    if current is None or new_date > current:
        set_sim_date(new_date)
