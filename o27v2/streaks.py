"""
Performance streaks — multi-week hot/cold runs on player (and team) attributes.

The design the user asked for:

  "The improvement during a hot streak should be exponential over ratings — a
   hot streak can add as many as +18 [grade] per week to attributes, and each
   week that can go up +5; a cold streak can do the same, like a flu it starts
   slow and ramps up."

So a streak is a *ramp*, not a step:
  - Week 1 of a hot streak adds STREAK_WEEK1 grade points to the player's
    offensive ratings.
  - Each subsequent week adds STREAK_WEEK_STEP MORE on top (accelerating).
  - The total swing is capped at STREAK_CAP grade points so a long run peaks
    "elite-ish," not superhuman.
  - A cold streak is the mirror (negative), same accelerating shape.

Streaks are PERFORMANCE-IGNITED and REVERTING: hot/cold play fills a rolling
"heat" signal; cross a threshold and a streak of that polarity starts; keep
playing that way and it ramps week over week; play to the contrary (or just
cool off) and it breaks, returning the player to his true baseline. The streak
is a temporary OVERLAY applied at game-load time — it never mutates the stored
rating, so when it ends the player is exactly who he was.

State lives in the DB (players/teams: streak_state, streak_weeks, streak_games,
streak_heat) so the ramp survives across the season and the live deployment.

Two layers:
  - per-player streaks (each guy heats up / slumps on his own)
  - a lighter per-team streak overlaid on the whole lineup (a club catching
    fire together)

The engine works in [0,1] unit floats; the streak magnitude is expressed in
grade points (the 20-95 scout scale the user spoke in) and converted to a unit
delta via scout.to_unit's slope (~0.70 unit per 60 grade) before being added to
the offensive attributes.
"""
from __future__ import annotations

import random
from typing import Optional

from o27v2 import db
from o27v2 import config as _v2cfg


# ---------------------------------------------------------------------------
# Tunables (grade-point scale unless noted)
# ---------------------------------------------------------------------------

# Ramp shape. Week 1 of a streak is worth WEEK1 grade points; each completed
# week adds WEEK_STEP more (so week N magnitude = WEEK1 + (N-1)*WEEK_STEP,
# summed implicitly by tracking weeks). Capped at CAP.
STREAK_WEEK1: float       = 18.0   # grade points added in the first hot week
STREAK_WEEK_STEP: float   = 5.0    # extra grade points each subsequent week
STREAK_CAP: float         = 30.0   # max total swing (|delta|), elite-ish peak

# A "week" is this many of the player's own games. The ramp ticks one week
# every time the player accumulates this many appearances while the streak
# holds. ~6 keeps a week roughly a real calendar week at the league's cadence.
STREAK_GAMES_PER_WEEK: int = 6

# Heat: a rolling [-1, 1] performance signal. Each game nudges it by the
# game grade (good day +, bad day -), decaying toward 0 so a streak needs
# *sustained* play, not one game. Crossing +IGNITE starts a hot streak;
# -IGNITE a cold one. Falling back inside BREAK (toward 0, or flipping sign)
# ends the current streak and reverts the player.
# Neutral-game baselines for the per-game heat grade. Set to the O27 league
# norms so a league-average game line scores ~0 heat (hot and cold ignite at
# symmetric rates). These are run-environment dependent; re-center if the
# offensive environment shifts materially.
STREAK_OBP_BASELINE: float = 0.55
STREAK_SLG_BASELINE: float = 0.85

STREAK_HEAT_GOOD: float    = 0.34   # heat added on a clearly good game
STREAK_HEAT_BAD: float     = 0.34   # heat subtracted on a clearly bad game
STREAK_HEAT_DECAY: float   = 0.12   # pull toward 0 each game (cool-off)
STREAK_IGNITE: float       = 0.55   # |heat| to start a streak
STREAK_BREAK: float        = 0.20   # |heat| below which an active streak ends

# Team streak: same machinery, lighter magnitude, ignited by team W/L heat.
STREAK_TEAM_SCALE: float   = 0.45   # team swing = this * the player ramp
STREAK_TEAM_HEAT_WIN: float = 0.30
STREAK_TEAM_HEAT_DECAY: float = 0.14
STREAK_TEAM_IGNITE: float  = 0.55
STREAK_TEAM_BREAK: float   = 0.20

# Offensive attributes a hitter's streak rides on. (Pitchers are left to the
# existing condition/form systems — this layer is the bat-streak the user
# described.) Defense/speed deliberately excluded so a hot bat doesn't turn a
# statue into a gold glover.
STREAK_HITTER_ATTRS = ("skill", "contact", "power", "eye")

# Grade->unit slope (matches scout.to_unit: 0.70 unit over 60 grade points,
# 20..80). Used to turn a grade-point streak delta into a unit-float delta.
_GRADE_TO_UNIT = 0.70 / 60.0


# ---------------------------------------------------------------------------
# Ramp magnitude
# ---------------------------------------------------------------------------

def streak_grade_delta(state: int, weeks: int) -> float:
    """Signed grade-point delta for a streak in `state` (-1/0/+1) that has
    ramped `weeks` complete weeks (the current, in-progress week counts as the
    first rung). Accelerating: week 1 = WEEK1, each further week adds WEEK_STEP.
    Capped at +/-STREAK_CAP.

    weeks is 0-based completed weeks; the streak is "in" week (weeks+1).
    """
    if state == 0:
        return 0.0
    rungs = weeks + 1                      # the week currently being served
    # Sum of an arithmetic-ish ramp: week1 = WEEK1, then +STEP per later week.
    mag = STREAK_WEEK1 + STREAK_WEEK_STEP * (rungs - 1)
    mag = min(mag, STREAK_CAP)
    return mag * (1 if state > 0 else -1)


def streak_unit_delta(state: int, weeks: int, scale: float = 1.0) -> float:
    """The streak magnitude as a [0,1]-scale unit delta (what the engine adds
    to an attribute), optionally scaled (team streaks pass STREAK_TEAM_SCALE)."""
    return streak_grade_delta(state, weeks) * _GRADE_TO_UNIT * scale


# ---------------------------------------------------------------------------
# Overlay — apply a player's streak to the engine Player at game-load time
# ---------------------------------------------------------------------------

def apply_player_streak(player, row: dict, team_delta: float = 0.0) -> None:
    """Add the player's (and the team's) streak overlay to an engine Player's
    offensive attributes in place. `row` is the player's DB row (carrying
    streak_state / streak_weeks); `team_delta` is the team streak's unit delta.

    Non-hitters get only the team overlay (their pitching is governed by the
    existing condition systems). The overlay is clamped to [0,1] and never
    written back — it evaporates when the next game reloads from the DB.
    """
    pdelta = 0.0
    if not getattr(player, "is_pitcher", False):
        pdelta = streak_unit_delta(
            int(row.get("streak_state") or 0),
            int(row.get("streak_weeks") or 0),
        )
    total = pdelta + team_delta
    if total == 0.0:
        return
    for attr in STREAK_HITTER_ATTRS:
        cur = float(getattr(player, attr, 0.5) or 0.5)
        setattr(player, attr, max(0.0, min(1.0, cur + total)))


def team_streak_unit_delta(team_row: Optional[dict]) -> float:
    """Unit delta contributed by a team's streak (applied to every hitter on
    that team's lineup). 0.0 when the team has no active streak."""
    if not team_row:
        return 0.0
    return streak_unit_delta(
        int(team_row.get("streak_state") or 0),
        int(team_row.get("streak_weeks") or 0),
        scale=STREAK_TEAM_SCALE,
    )


# ---------------------------------------------------------------------------
# Heat / ramp updates — run post-game, persisted to the DB
# ---------------------------------------------------------------------------

def _batter_game_grade(r: dict) -> Optional[float]:
    """Classify a batter's game line into a heat nudge in roughly [-1, 1], or
    None for an idle/pinch-only line that shouldn't move the streak.

    Built from on-base + slug productivity vs a neutral day. Mild thresholds
    so a streak builds over a handful of games, not off one swing."""
    pa = r.get("pa") or 0
    if pa < 2:
        return None
    ab = r.get("ab") or 0
    h  = r.get("hits") or 0
    bb = (r.get("bb") or 0) + (r.get("hbp") or 0)
    xb = (r.get("doubles") or 0) + 2 * (r.get("triples") or 0) + 3 * (r.get("hr") or 0)
    on_base = h + bb
    obp = on_base / pa if pa else 0.0
    # Total-bases per AB as a power signal (singles=1.. hr=4).
    tb = h + xb
    slg = tb / ab if ab else 0.0
    # Center on the O27 league norm (high run environment — OBP ~.67, SLG ~1.0
    # with the stay mechanic), so a typical game reads ~neutral and hot/cold
    # ignite at roughly symmetric rates. Tuned against an 8-team season's
    # per-game line distribution (see test_streaks / the streaks AAR).
    grade = (obp - STREAK_OBP_BASELINE) * 1.7 + (slg - STREAK_SLG_BASELINE) * 0.9
    return max(-1.0, min(1.0, grade))


def _advance_streak(state: int, weeks: int, games: int, heat: float,
                    ignite: float, brk: float) -> tuple[int, int, int]:
    """Pure state transition for one streak after its heat has been updated.

    Returns the new (state, weeks, games). Rules:
      - No streak: if |heat| crosses `ignite`, start one (state=sign, week 0).
      - Active streak: if heat has fallen back inside `brk` (or flipped against
        the streak), it BREAKS — reset to neutral (the player reverts).
        Otherwise the streak holds: tick a game; every GAMES_PER_WEEK games
        completes a week and the ramp grows.
    """
    if state == 0:
        if heat >= ignite:
            return 1, 0, 0
        if heat <= -ignite:
            return -1, 0, 0
        return 0, 0, 0
    # Active streak — does it still hold?
    aligned = heat * state            # >0 means heat still favors the streak
    if aligned < brk:
        return 0, 0, 0                # break → revert to baseline
    games += 1
    if games >= STREAK_GAMES_PER_WEEK:
        games = 0
        weeks += 1
    return state, weeks, games


def update_player_streaks(batter_rows: list[dict]) -> None:
    """Post-game: fold each batter's game line into his heat, then advance his
    streak state. Persists to the players table. Mirrors _update_habit_cups."""
    updates: list[tuple] = []
    pids = [r.get("player_id") for r in batter_rows if r.get("player_id")]
    if not pids:
        return
    placeholders = ",".join(["?"] * len(pids))
    cur = {
        row["id"]: row
        for row in db.fetchall(
            f"SELECT id, streak_state, streak_weeks, streak_games, streak_heat "
            f"FROM players WHERE id IN ({placeholders})",
            tuple(pids),
        )
    }
    for r in batter_rows:
        pid = r.get("player_id")
        if pid is None or pid not in cur:
            continue
        grade = _batter_game_grade(r)
        if grade is None:
            continue                  # idle line — leave streak untouched
        prev = cur[pid]
        state = int(prev.get("streak_state") or 0)
        weeks = int(prev.get("streak_weeks") or 0)
        games = int(prev.get("streak_games") or 0)
        heat  = float(prev.get("streak_heat") or 0.0)
        # Decay toward 0, then add this game's signal.
        heat += -heat * STREAK_HEAT_DECAY
        heat += grade * (STREAK_HEAT_GOOD if grade > 0 else STREAK_HEAT_BAD)
        heat = max(-1.5, min(1.5, heat))
        state, weeks, games = _advance_streak(
            state, weeks, games, heat, STREAK_IGNITE, STREAK_BREAK
        )
        updates.append((state, weeks, games, heat, pid))
    if not updates:
        return
    with db.get_conn() as conn:
        conn.executemany(
            "UPDATE players SET streak_state=?, streak_weeks=?, "
            "streak_games=?, streak_heat=? WHERE id=?",
            updates,
        )
        conn.commit()


def update_team_streak(team_id: int, won: bool) -> None:
    """Post-game: fold a W/L into the team's heat and advance its streak.
    A win heats the club up, a loss cools it — same ramp machinery, lighter."""
    row = db.fetchone(
        "SELECT streak_state, streak_weeks, streak_games, streak_heat "
        "FROM teams WHERE id = ?", (team_id,)
    )
    if not row:
        return
    state = int(row.get("streak_state") or 0)
    weeks = int(row.get("streak_weeks") or 0)
    games = int(row.get("streak_games") or 0)
    heat  = float(row.get("streak_heat") or 0.0)
    heat += -heat * STREAK_TEAM_HEAT_DECAY
    heat += (STREAK_TEAM_HEAT_WIN if won else -STREAK_TEAM_HEAT_WIN)
    heat = max(-1.5, min(1.5, heat))
    state, weeks, games = _advance_streak(
        state, weeks, games, heat, STREAK_TEAM_IGNITE, STREAK_TEAM_BREAK
    )
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE teams SET streak_state=?, streak_weeks=?, "
            "streak_games=?, streak_heat=? WHERE id=?",
            (state, weeks, games, heat, team_id),
        )
        conn.commit()
