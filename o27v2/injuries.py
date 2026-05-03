"""
Phase 9: Injury model for O27v2.

Per-game injury draws for all non-joker position players.

Rates:
  - Base rate: 1.5%/player/game (spec-mandated floor for all position players)
  - Catcher: +0.5% (total ~2.0%)
  - Workhorse pitcher: +0.8% (total ~2.3%)
  - Age 33-35: +0.2%/yr over 32
  - Age 36+: +0.4%/yr over 35
  - High recent BF (pitcher): +0.3% if bf_this_season > 600

IL Tiers (when injury fires):
  - DTD (day-to-day):  P=0.50, 1-3 games missed
  - Short-term IL:     P=0.35, 10-25 games missed
  - Long-term IL:      P=0.15, 60-100 games missed

Minimum active roster: 7 non-jokers (max 2 simultaneous injuries per team).
Injury draws stop once this floor is reached.  The 8-15 per-team per-season
target counts IL stints only (short + long tier, P=0.50 of all draws), which
is the baseball convention for "going on the injured list."  At 1.5% base
rate the effective IL stint total is ~10-11 per team per 162 games (✓ 8-15).
Day-to-day incidents (DTD tier) fire on top of this and clear within 1-3 games.

Depth-chart promotion and position-shortage performance penalties are logged
as transactions immediately after each injury event.
"""
from __future__ import annotations
import datetime
import random

from o27v2 import db
from o27v2 import scout as _scout

INJURY_BASE_RATE     = 0.015   # 1.5%/player/game — spec-mandated base for all players
CATCHER_BONUS        = 0.005   # catcher total ~2.0%
PITCHER_BONUS        = 0.008   # workhorse pitcher total ~2.3%
AGE_BONUS_PER_YR_33  = 0.002   # +0.2%/yr over age 32 (mild age risk)
AGE_BONUS_PER_YR_36  = 0.004   # +0.4%/yr over age 35 (sharper risk after 35)

MIN_ROSTER_THRESHOLD = 7       # always keep at least this many healthy non-jokers (max 2 simultaneous injuries)


# ---------------------------------------------------------------------------
# Probability helpers
# ---------------------------------------------------------------------------

def _injury_probability(player: dict, bf_this_season: int = 0) -> float:
    rate = INJURY_BASE_RATE
    pos  = player.get("position", "")
    age  = int(player.get("age", 27))

    if pos == "C":
        rate += CATCHER_BONUS
    if player.get("pitcher_role") == "workhorse" or player.get("is_pitcher"):
        rate += PITCHER_BONUS
        if bf_this_season > 600:
            rate += 0.003

    if 33 <= age <= 35:
        rate += AGE_BONUS_PER_YR_33 * (age - 32)
    elif age >= 36:
        rate += AGE_BONUS_PER_YR_33 * 3 + AGE_BONUS_PER_YR_36 * (age - 35)

    return min(rate, 0.15)


def _draw_tier(rng: random.Random) -> str:
    r = rng.random()
    if r < 0.50:
        return "dtd"
    if r < 0.85:
        return "short"
    return "long"


def _tier_duration(tier: str, rng: random.Random) -> int:
    if tier == "dtd":
        return rng.randint(1, 3)
    if tier == "short":
        return rng.randint(10, 25)
    return rng.randint(60, 100)


# ---------------------------------------------------------------------------
# Depth chart promotion & performance penalty
# ---------------------------------------------------------------------------

def _depth_chart_events(
    injured_player: dict,
    team_id: int,
    game_date: str,
) -> list[dict]:
    """
    After an injury fires, identify the depth chart replacement and log:
      - A 'promotion' event naming the fill-in player.
      - A 'penalty' event when the fill-in is at a skill disadvantage or when
        the lineup drops below safe depth.

    These events are informational (they don't alter sim math) but appear in
    the transaction log so roster decisions are auditable.
    """
    events: list[dict] = []

    team_row = db.fetchone("SELECT name FROM teams WHERE id = ?", (team_id,))
    team_name = team_row["name"] if team_row else "Team"

    # Current healthy roster after this injury has been recorded
    healthy = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? "
        "AND (injured_until IS NULL OR injured_until <= ?) ORDER BY id",
        (team_id, game_date),
    )

    role = injured_player.get("pitcher_role", "")
    pos  = injured_player.get("position", "")
    name = injured_player.get("name", "?")

    # -- Pitcher position --
    # Phase 10: pitcher depth = remaining healthy SPs first, then RPs.
    if role in ("workhorse", "starter", "reliever") or pos == "P":
        backups = [
            p for p in healthy
            if p.get("is_pitcher") and p["id"] != injured_player["id"]
        ]
        # Prefer fellow starters as fill-in, then relievers, then anyone else.
        backups.sort(
            key=lambda p: (
                p.get("pitcher_role") in ("starter", "workhorse"),
                p.get("pitcher_role") == "reliever",
                float(p.get("pitcher_skill", 0.0)),
            ),
            reverse=True,
        )
        if backups:
            best = backups[0]
            events.append({
                "event_type": "promotion",
                "team_id": team_id,
                "player_id": best["id"],
                "detail": (
                    f"{team_name}: {best['name']} ({best['position']}) promoted to "
                    f"cover pitching role while {name} is on IL"
                ),
            })
            # Performance penalty if best replacement is below starter threshold.
            # Phase 10 stores skills as raw floats, so no scout conversion needed.
            if float(best.get("pitcher_skill", 0.0)) < 0.48:
                events.append({
                    "event_type": "penalty",
                    "team_id": team_id,
                    "player_id": best["id"],
                    "detail": (
                        f"COVERAGE PENALTY: {team_name} pitcher {name} on IL; "
                        f"replacement {best['name']} pitcher_skill={best['pitcher_skill']:.3f} "
                        f"(below starter threshold 0.48)"
                    ),
                })
        else:
            events.append({
                "event_type": "penalty",
                "team_id": team_id,
                "player_id": None,
                "detail": (
                    f"CRITICAL SHORTAGE: {team_name} has no available pitchers after "
                    f"{name} placed on IL"
                ),
            })

    # -- Catcher position --
    elif pos == "C":
        fillin = next(
            (p for p in healthy if p.get("position") not in ("P", "JKR") and p["id"] != injured_player["id"]),
            None,
        )
        if fillin:
            events.append({
                "event_type": "promotion",
                "team_id": team_id,
                "player_id": fillin["id"],
                "detail": (
                    f"{team_name}: {fillin['name']} ({fillin['position']}) covering catcher "
                    f"while {name} is on IL"
                ),
            })
            events.append({
                "event_type": "penalty",
                "team_id": team_id,
                "player_id": fillin["id"],
                "detail": (
                    f"COVERAGE PENALTY: {team_name} catcher {name} on IL; "
                    f"non-specialist {fillin['name']} filling in (reduced game-calling quality)"
                ),
            })
        else:
            events.append({
                "event_type": "penalty",
                "team_id": team_id,
                "player_id": None,
                "detail": (
                    f"CRITICAL SHORTAGE: {team_name} has no catcher cover after "
                    f"{name} placed on IL"
                ),
            })

    # -- General position (outfield / infield) --
    else:
        # Any remaining healthy position player is the fill-in
        candidates = [p for p in healthy if p["id"] != injured_player["id"]]
        fillin = max(candidates, key=lambda p: float(p.get("skill", 0.0))) if candidates else None
        if fillin:
            events.append({
                "event_type": "promotion",
                "team_id": team_id,
                "player_id": fillin["id"],
                "detail": (
                    f"{team_name}: {fillin['name']} ({fillin['position']}) filling lineup "
                    f"gap at {pos} while {name} is on IL"
                ),
            })

    # -- Lineup depth shortage penalty --
    non_joker_count = len(healthy)
    if non_joker_count < 7:
        events.append({
            "event_type": "penalty",
            "team_id": team_id,
            "player_id": None,
            "detail": (
                f"LINEUP SHORTAGE PENALTY: {team_name} has only {non_joker_count} healthy "
                f"position players; jokers will supplement the batting lineup"
            ),
        })

    return events


# ---------------------------------------------------------------------------
# Player return processing
# ---------------------------------------------------------------------------

def process_returns(game_date: str) -> list[dict]:
    """
    Clear expired injuries and return transaction dicts for each returning player.
    A player is available again when injured_until <= game_date.
    """
    returning = db.fetchall(
        "SELECT * FROM players WHERE injured_until IS NOT NULL AND injured_until <= ?",
        (game_date,),
    )
    if not returning:
        return []

    with db.get_conn() as conn:
        conn.execute(
            "UPDATE players SET injured_until = NULL, il_tier = NULL "
            "WHERE injured_until IS NOT NULL AND injured_until <= ?",
            (game_date,),
        )
        conn.commit()

    tier_labels = {
        "dtd":   "Day-to-Day",
        "short": "Short-Term IL",
        "long":  "Long-Term IL",
    }
    return [
        {
            "event_type": "return",
            "team_id": p["team_id"],
            "player_id": p["id"],
            "detail": (
                f"{p['name']} returns from {tier_labels.get(p['il_tier'] or '', 'IL')} "
                f"({p['position']})"
            ),
        }
        for p in returning
    ]


# ---------------------------------------------------------------------------
# Post-game injury draws
# ---------------------------------------------------------------------------

def process_post_game_injuries(
    game_id: int,
    game_date: str,
    home_team_id: int,
    away_team_id: int,
    rng: random.Random,
) -> list[dict]:
    """
    Draw injury events for all healthy non-joker players on both teams.
    Stops injuring when healthy non-joker count reaches MIN_ROSTER_THRESHOLD.
    Returns list of transaction dicts for all events (injuries + promotions + penalties).
    """
    # Season pitcher BF totals for high-workload modifier
    pitcher_bf: dict[int, int] = {}
    for row in db.fetchall(
        "SELECT player_id, SUM(batters_faced) as total_bf "
        "FROM game_pitcher_stats gps "
        "JOIN games g ON g.id = gps.game_id "
        "WHERE g.played = 1 "
        "GROUP BY player_id"
    ):
        pitcher_bf[row["player_id"]] = row["total_bf"]

    events: list[dict] = []

    for team_id in [home_team_id, away_team_id]:
        players = db.fetchall(
            "SELECT * FROM players WHERE team_id = ? "
            "AND (injured_until IS NULL OR injured_until <= ?) ORDER BY id",
            (team_id, game_date),
        )
        healthy_count = len(players)

        for p in players:
            if rng.random() > _injury_probability(p, pitcher_bf.get(p["id"], 0)):
                continue

            # Stop injuring if at floor
            if healthy_count <= MIN_ROSTER_THRESHOLD:
                break

            tier     = _draw_tier(rng)
            duration = _tier_duration(tier, rng)
            base     = datetime.date.fromisoformat(game_date)
            return_date = (base + datetime.timedelta(days=duration)).isoformat()

            db.execute(
                "UPDATE players SET injured_until = ?, il_tier = ? WHERE id = ?",
                (return_date, tier, p["id"]),
            )
            healthy_count -= 1

            tier_label = {
                "dtd":   "Day-to-Day",
                "short": "Short-Term IL",
                "long":  "Long-Term IL",
            }[tier]
            events.append({
                "event_type": "injury",
                "team_id": team_id,
                "player_id": p["id"],
                "detail": (
                    f"{p['name']} ({p['position']}) placed on {tier_label}, "
                    f"out until {return_date} (~{duration} games)"
                ),
            })

            # Depth chart promotion + shortage penalty
            events.extend(_depth_chart_events(p, team_id, game_date))

    return events


# ---------------------------------------------------------------------------
# Active roster for simulation
# ---------------------------------------------------------------------------

def get_active_players(team_id: int, game_date: str) -> list[dict]:
    """
    Return today's playable roster for a team.

    Task #65 model:
      - Pull the active roster (`is_active = 1`) minus anyone on the IL.
      - One-for-one IL replacement: top the active pitcher count back up
        to TARGET_ACTIVE_PITCHERS by ephemerally promoting the highest-
        Stamina healthy reserve pitchers, and top position players back
        to TARGET_ACTIVE_POSITION by promoting the highest-skill healthy
        reserve hitters (preferring the same position when available).
      - Reserve promotion is in-memory only: the DB `is_active` flags are
        never flipped, so when the injured player returns the reserve
        falls back to the bench naturally.
    """
    from o27v2.league import ACTIVE_PITCHERS, ACTIVE_POSITION_TOTAL
    TARGET_ACTIVE_PITCHERS = ACTIVE_PITCHERS
    TARGET_ACTIVE_POSITION = ACTIVE_POSITION_TOTAL

    active = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? "
        "AND COALESCE(is_active, 1) = 1 "
        "AND (injured_until IS NULL OR injured_until <= ?) ORDER BY id",
        (team_id, game_date),
    )

    n_pitchers = sum(1 for p in active if p.get("is_pitcher"))
    n_position = sum(1 for p in active
                     if not p.get("is_pitcher") and not p.get("is_joker"))

    need_pitchers = max(0, TARGET_ACTIVE_PITCHERS - n_pitchers)
    need_position = max(0, TARGET_ACTIVE_POSITION - n_position)
    if need_pitchers == 0 and need_position == 0:
        return active

    reserves = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? "
        "AND COALESCE(is_active, 1) = 0 "
        "AND (injured_until IS NULL OR injured_until <= ?)",
        (team_id, game_date),
    )

    if need_pitchers:
        rp = [r for r in reserves if r.get("is_pitcher")]
        rp.sort(key=lambda p: float(p.get("stamina") or p.get("pitcher_skill") or 0),
                reverse=True)
        active.extend(rp[:need_pitchers])

    if need_position:
        rh = [r for r in reserves
              if not r.get("is_pitcher") and not r.get("is_joker")]
        # Prefer reserves that can cover the same position(s) currently
        # short on the active roster — same-position fillers come first,
        # then highest-skill bats fill any remaining holes.
        injured_positions = {
            r["position"] for r in db.fetchall(
                "SELECT position FROM players WHERE team_id = ? "
                "AND COALESCE(is_active, 1) = 1 AND is_pitcher = 0 "
                "AND injured_until IS NOT NULL AND injured_until > ?",
                (team_id, game_date),
            )
        }
        def _fill_rank(p: dict) -> tuple[int, float]:
            same_pos = 1 if p.get("position") in injured_positions else 0
            return (same_pos, float(p.get("skill") or 0))
        rh.sort(key=_fill_rank, reverse=True)
        active.extend(rh[:need_position])

    return active


# ---------------------------------------------------------------------------
# Waiver claims
# ---------------------------------------------------------------------------

def check_waiver_claims(game_date: str) -> list[dict]:
    """
    Phase 10: dedicated rotation+bullpen replaces the Phase-8 "committee"
    bullpen model. Teams now carry 4 SP + 4 RP from day one, so the
    legacy waiver-claim logic (which spawned `committee` reliever
    call-ups) no longer applies and would just pollute rosters with
    role='committee' players. Disabled.
    """
    return []

    # ----- legacy Phase-8 logic kept below for reference, unreachable -----
    MIN_BULLPEN = 2
    teams  = db.fetchall("SELECT id, name FROM teams")
    events: list[dict] = []

    for team in teams:
        committee = db.fetchall(
            "SELECT * FROM players WHERE team_id = ? AND pitcher_role = 'committee' "
            "AND (injured_until IS NULL OR injured_until <= ?)",
            (team["id"], game_date),
        )
        if len(committee) >= MIN_BULLPEN:
            continue

        rng = random.Random(hash((game_date, team["id"])) & 0x7FFFFFFF)
        skill  = round(max(0.35, min(0.65, rng.gauss(0.48, 0.06))), 3)
        speed  = round(max(0.35, min(0.70, rng.gauss(0.48, 0.08))), 3)
        pskill = round(max(0.40, min(0.70, rng.gauss(0.50, 0.07))), 3)
        stay_a = round(max(0.05, min(0.25, rng.gauss(0.10, 0.04))), 3)
        cqt    = round(max(0.20, min(0.45, rng.gauss(0.28, 0.05))), 3)
        age    = rng.randint(24, 34)
        names  = [
            "R. Callup", "B. Depth", "T. Reserve", "M. Waiver",
            "J. Scrubs", "K. Bench", "S. Bullpen", "D. Reliever",
        ]
        wname = rng.choice(names) + f"-{rng.randint(10,99)}"

        player_id = db.execute(
            """INSERT INTO players
               (team_id, name, position, is_pitcher, skill, speed,
                pitcher_skill, stay_aggressiveness, contact_quality_threshold,
                archetype, pitcher_role, hard_contact_delta, hr_weight_bonus, age)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (team["id"], wname, "RP", 1,
             _scout.to_grade(skill), _scout.to_grade(speed), _scout.to_grade(pskill),
             stay_a, cqt, "", "committee", 0.0, 0.0, age),
        )
        events.append({
            "event_type": "waiver",
            "team_id": team["id"],
            "player_id": player_id,
            "detail": (
                f"{team['name']} claim waiver reliever {wname} "
                f"to cover bullpen shortage (remaining: {len(committee)})"
            ),
        })

    return events
