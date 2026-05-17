"""Synthetic mini-season for almanac tests and demos.

Produces a loader-shaped dict with 6 teams, 9 batters + 5 pitchers each,
and a short round-robin schedule of played games. Numbers are
plausible (PA/AB/hits internally consistent, ERA/WHIP in a sane band)
but deterministic — no RNG. Lets the renderer + export pipeline run
without depending on a live o27v2 DB.
"""
from __future__ import annotations

import datetime as _dt
import random
from typing import Any


TEAMS = [
    ("MNT", "Minneapolis",  "Bisons",   "AL", "North"),
    ("STP", "Saint Paul",   "Saints",   "AL", "North"),
    ("MIL", "Milwaukee",    "Brews",    "AL", "North"),
    ("CHI", "Chicago",      "Aviators", "NL", "South"),
    ("STL", "St. Louis",    "Riverkings","NL", "South"),
    ("KAN", "Kansas City",  "Reds",     "NL", "South"),
]

POSITIONS_HIT = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"]


def build_fixture() -> dict[str, Any]:
    rng = random.Random(20260517)

    teams: list[dict] = []
    players: list[dict] = []
    pid_counter = 0
    for tid, (abb, city, name, lg, div) in enumerate(TEAMS, start=1):
        teams.append({
            "id": tid, "abbrev": abb, "name": name, "city": city,
            "league": lg, "division": div, "wins": 0, "losses": 0,
            "park_name": f"{name} Field", "manager_name": f"{name} Skipper",
            "park_hr": 1.0, "park_hits": 1.0,
        })
        for j, pos in enumerate(POSITIONS_HIT):
            pid_counter += 1
            players.append({
                "id": pid_counter, "team_id": tid,
                "name": f"{city.split()[0]} {pos} {j+1}",
                "position": pos, "is_pitcher": 0, "is_joker": 0,
                "bats": rng.choice(["L", "R", "S"]),
                "throws": rng.choice(["L", "R"]),
                "country": rng.choice(["US", "DO", "JP", "VE", "KR"]),
                "age": rng.randint(21, 35),
                "archetype": "balanced",
                "skill": rng.randint(40, 80), "power": rng.randint(40, 80),
                "contact": rng.randint(40, 80), "eye": rng.randint(40, 80),
                "speed": rng.randint(40, 80),
            })
        for j in range(5):
            pid_counter += 1
            players.append({
                "id": pid_counter, "team_id": tid,
                "name": f"{city.split()[0]} P{j+1}",
                "position": "P", "is_pitcher": 1, "is_joker": 0,
                "bats": "R", "throws": rng.choice(["L", "R"]),
                "country": rng.choice(["US", "DO", "JP"]),
                "age": rng.randint(22, 38),
                "archetype": rng.choice(["workhorse", "k_specialist", "control"]),
                "pitcher_skill": rng.randint(45, 80),
                "stamina": rng.randint(40, 80),
                "command": rng.randint(40, 80),
                "movement": rng.randint(40, 80),
            })

    games: list[dict] = []
    batting: list[dict] = []
    pitching: list[dict] = []

    # Round-robin: each pair plays 4 games (2 home, 2 away).
    pairings = [(a, b) for i, a in enumerate(teams) for b in teams[i+1:]]
    gid = 0
    day = _dt.date(2026, 4, 1)
    for ta, tb in pairings:
        for round_idx in range(4):
            home, away = (ta, tb) if round_idx % 2 == 0 else (tb, ta)
            gid += 1
            day = day + _dt.timedelta(days=1)
            h_score = rng.randint(2, 11)
            a_score = rng.randint(2, 11)
            if h_score == a_score:
                h_score += 1  # avoid ties so winner_id is well-defined
            winner = home["id"] if h_score > a_score else away["id"]
            games.append({
                "id": gid, "season": 1, "game_date": day.isoformat(),
                "home_team_id": home["id"], "away_team_id": away["id"],
                "home_score": h_score, "away_score": a_score,
                "winner_id": winner, "played": 1, "super_inning": 0,
                "is_playoff": 0, "seed": rng.randint(0, 999999),
                "temperature_tier": "mild", "wind_tier": "neutral",
                "humidity_tier": "normal", "precip_tier": "none",
                "cloud_tier": "clear",
            })

            for team, runs_scored in ((away, a_score), (home, h_score)):
                team_batters  = [p for p in players if p["team_id"] == team["id"] and not p["is_pitcher"]]
                team_pitchers = [p for p in players if p["team_id"] == team["id"] and p["is_pitcher"]]
                _emit_team_lines(rng, gid, team["id"], team_batters,
                                 team_pitchers, runs_scored,
                                 against=h_score if team is away else a_score,
                                 batting=batting, pitching=pitching)

    return {
        "meta": {
            "source": "<fixture>",
            "source_kind": "fixture",
            "schema_version": "almanac/1",
            "season": 1,
            "game_count": len(games),
            "team_count": len(teams),
            "player_count": len(players),
        },
        "teams": teams,
        "players": players,
        "games": games,
        "batting": batting,
        "pitching": pitching,
        "seasons": [{
            "id": 1, "season_number": 1, "champion_abbrev": "MNT",
            "champion_team_name": "Minneapolis Bisons",
            "champion_w": 0, "champion_l": 0,
            "games_played": len(games), "year": 2026,
        }],
        "awards": [],
    }


def _emit_team_lines(rng, gid, team_id, batters, pitchers, runs_for, against,
                     *, batting, pitching) -> None:
    runs_assigned = 0
    rbi_pool      = runs_for
    for b in batters:
        pa = rng.randint(3, 5)
        bb = rng.choices([0, 1, 2], [0.6, 0.3, 0.1])[0]
        hbp = 1 if rng.random() < 0.04 else 0
        ab = max(0, pa - bb - hbp)
        k  = min(ab, rng.choices([0, 1, 2], [0.55, 0.35, 0.10])[0])
        hits = min(ab - k, rng.choices([0, 1, 2, 3], [0.45, 0.35, 0.15, 0.05])[0])
        doubles = 1 if hits >= 2 and rng.random() < 0.30 else 0
        triples = 0
        hr      = 1 if hits >= 1 and rng.random() < 0.10 else 0
        # ensure 1B count ≥ 0
        if doubles + hr > hits:
            hr = max(0, hits - doubles)
        stays   = rng.choices([0, 1, 2], [0.65, 0.30, 0.05])[0]
        rbi     = 0
        if rbi_pool > 0 and hits > 0 and rng.random() < 0.45:
            rbi = min(rbi_pool, 1 + (1 if hr else 0))
            rbi_pool -= rbi
        runs    = 0
        if runs_assigned < runs_for and rng.random() < (hits + bb) * 0.15:
            runs = 1
            runs_assigned += 1
        sb = 1 if rng.random() < 0.05 else 0
        outs = max(0, ab - hits)
        batting.append({
            "game_id": gid, "team_id": team_id, "player_id": b["id"],
            "phase": 0, "pa": pa, "ab": ab, "runs": runs, "hits": hits,
            "doubles": doubles, "triples": triples, "hr": hr, "rbi": rbi,
            "bb": bb, "k": k, "stays": stays, "outs_recorded": outs,
            "hbp": hbp, "sb": sb, "cs": 0, "fo": 0,
            "multi_hit_abs": 1 if hits >= 2 else 0,
            "stay_rbi": 1 if (stays and rbi) else 0,
            "stay_hits": 1 if (stays and hits) else 0,
            "c2_op_1b": 0, "c2_adv_1b": 0,
            "c2_op_2b": 0, "c2_adv_2b": 0,
            "c2_op_3b": 0, "c2_adv_3b": 0,
            "adv_op_1b": 0, "adv_adv_1b": 0,
            "adv_op_2b": 0, "adv_adv_2b": 0,
            "adv_op_3b": 0, "adv_adv_3b": 0,
            "rad_1b": 0, "rad_2b": 0, "rad_3b": 0,
            "game_position": b["position"], "entry_type": "starter",
            "replaced_player_id": None, "gidp": 0, "gitp": 0, "roe": 0,
            "po": rng.randint(0, 3), "a": rng.randint(0, 2), "e": 0,
        })

    # Pitcher line — split into starter + reliever.
    starter, *bullpen = rng.sample(pitchers, k=min(3, len(pitchers)))
    arms = [(starter, True)] + [(p, False) for p in bullpen]
    outs_remaining = 27
    for arm, is_starter in arms:
        chunk = rng.randint(9, 15) if is_starter else rng.randint(3, 9)
        chunk = min(chunk, outs_remaining)
        if chunk <= 0:
            break
        bf = chunk + rng.randint(0, 6)
        bb = rng.randint(0, max(0, bf // 6))
        k  = rng.randint(0, max(0, chunk // 2))
        hits_a = rng.randint(0, max(0, bf // 5))
        hr_a   = 1 if hits_a >= 1 and rng.random() < 0.12 else 0
        er = rng.randint(0, max(0, against // max(1, len(arms))))
        pitching.append({
            "game_id": gid, "team_id": team_id, "player_id": arm["id"],
            "phase": 0, "batters_faced": bf, "outs_recorded": chunk,
            "hits_allowed": hits_a, "runs_allowed": er, "er": er,
            "bb": bb, "k": k, "hr_allowed": hr_a, "pitches": chunk * 4 + bb * 5,
            "hbp_allowed": 0, "unearned_runs": 0, "sb_allowed": 0,
            "cs_caught": 0, "fo_induced": 0,
            "er_arc1": er if is_starter else 0,
            "er_arc2": 0, "er_arc3": 0 if is_starter else er,
            "k_arc1":  k  if is_starter else 0, "k_arc2": 0,
            "k_arc3":  0  if is_starter else k,
            "fo_arc1": 0, "fo_arc2": 0, "fo_arc3": 0,
            "bf_arc1": bf if is_starter else 0, "bf_arc2": 0,
            "bf_arc3": 0  if is_starter else bf,
            "is_starter": 1 if is_starter else 0,
            "singles_allowed": max(0, hits_a - hr_a),
            "doubles_allowed": 0, "triples_allowed": 0,
            "fastball_pct": 0.55, "breaking_pct": 0.30,
            "offspeed_pct": 0.15, "primary_pitch": "four_seam",
        })
        outs_remaining -= chunk
