"""
Weather model calibration test.

Sims a small number of games with EXTREME-stack weather (HR-friendly /
HR-killer / K-prone / clean conditions) and confirms league-aggregate rates
stay within ~10-15% of the neutral-weather baseline.

Weather is flavor, not outcome determination — these bounds protect the
"flavor" property: a season simulated under extreme weather shouldn't look
like a different sport from one simulated under neutral weather.
"""
from __future__ import annotations
import os
import random
import tempfile

import pytest

from o27.engine.weather import Weather
from o27.engine.state import GameState
from o27.engine.game import run_game
from o27.render.render import Renderer
from o27.engine.prob import ProbabilisticProvider


def _setup_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.environ["O27V2_DB_PATH"] = tmp.name

    from o27v2 import db, league, schedule
    db.init_db()
    league.seed_league(rng_seed=11)
    schedule.seed_schedule(rng_seed=11)
    return tmp.name


def _sim_n_with_weather(weather: Weather | None, n_games: int, base_seed: int) -> dict:
    """Simulate `n_games` consecutive unplayed games with `weather` stamped
    on each. Returns aggregate rate dict."""
    from o27v2 import db
    from o27v2.sim import (
        _db_team_to_engine,
        _get_active_players,
        _promote_pitcher_role,
        _recently_used_pitcher_ids,
        _pitcher_workload_state,
        _position_player_workload,
        _find_pitcher_id,
    )

    games = db.fetchall(
        "SELECT * FROM games WHERE played = 0 ORDER BY id LIMIT ?", (n_games,)
    )

    totals = {"pa": 0, "ab": 0, "h": 0, "hr": 0, "bb": 0, "k": 0, "errors": 0}

    for i, game in enumerate(games):
        gd = game["game_date"]
        home_id, away_id = game["home_team_id"], game["away_team_id"]
        home_row = db.fetchone("SELECT * FROM teams WHERE id = ?", (home_id,))
        away_row = db.fetchone("SELECT * FROM teams WHERE id = ?", (away_id,))
        home_row = dict(home_row); home_row["id"] = home_id
        away_row = dict(away_row); away_row["id"] = away_id

        home_players = _promote_pitcher_role(_get_active_players(home_id, gd))
        away_players = _promote_pitcher_role(_get_active_players(away_id, gd))

        visitors_team = _db_team_to_engine(
            away_row, away_players, "visitors",
            recently_used_pitcher_ids=_recently_used_pitcher_ids(away_id, gd),
            workload=_pitcher_workload_state(away_id, gd),
            position_workload=_position_player_workload(away_id, gd),
            game_date=gd,
        )
        home_team = _db_team_to_engine(
            home_row, home_players, "home",
            recently_used_pitcher_ids=_recently_used_pitcher_ids(home_id, gd),
            workload=_pitcher_workload_state(home_id, gd),
            position_workload=_position_player_workload(home_id, gd),
            game_date=gd,
        )

        state = GameState(visitors=visitors_team, home=home_team)
        state.current_pitcher_id = _find_pitcher_id(home_team)
        state.weather = weather

        rng = random.Random(base_seed + i)
        renderer = Renderer()
        provider = ProbabilisticProvider(rng)
        final_state, _ = run_game(state, provider, renderer)

        # Aggregate from renderer (per-batter BatterStats dataclasses).
        for row in renderer._batter_stats.values():
            totals["pa"] += row.pa
            totals["ab"] += row.ab
            totals["h"]  += row.hits
            totals["hr"] += row.hr
            totals["bb"] += row.bb
            totals["k"]  += row.k
            totals["errors"] += row.e

    return totals


def _rates(t: dict) -> dict:
    pa = max(1, t["pa"])
    ab = max(1, t["ab"])
    return {
        "hr_per_pa":  t["hr"] / pa,
        "k_per_pa":   t["k"]  / pa,
        "bb_per_pa":  t["bb"] / pa,
        "h_per_ab":   t["h"]  / ab,
        "e_per_pa":   t["errors"] / pa,
    }


@pytest.fixture(scope="module")
def seeded_db():
    path = _setup_db()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def _ratio(extreme: float, neutral: float) -> float:
    if neutral <= 0:
        return 1.0
    return extreme / neutral


def test_extreme_weather_within_calibration_envelope(seeded_db):
    """League-aggregate offensive rates under extreme stacks stay close
    to the neutral baseline. Sample size: 20 games per condition."""
    n = 20

    neutral = _rates(_sim_n_with_weather(Weather(), n, base_seed=1000))

    hr_friendly = Weather(temperature="hot", wind="out", humidity="dry",
                          precip="none", cloud="clear")
    hr_killer   = Weather(temperature="cold", wind="in", humidity="humid",
                          precip="light", cloud="dusk")

    up   = _rates(_sim_n_with_weather(hr_friendly, n, base_seed=2000))
    down = _rates(_sim_n_with_weather(hr_killer,   n, base_seed=3000))

    # HR rate envelope: each multiplier stack is ~ ±11%, plus sampling
    # noise on a 20-game window. Allow 25% movement on the noisy single-
    # event rate.
    assert 0.75 < _ratio(up["hr_per_pa"],   neutral["hr_per_pa"]) < 1.30, (
        f"HR-friendly weather out of envelope: "
        f"neutral={neutral['hr_per_pa']:.4f} extreme={up['hr_per_pa']:.4f}"
    )
    assert 0.70 < _ratio(down["hr_per_pa"], neutral["hr_per_pa"]) < 1.20, (
        f"HR-killer weather out of envelope: "
        f"neutral={neutral['hr_per_pa']:.4f} extreme={down['hr_per_pa']:.4f}"
    )

    # H/AB (overall hit rate) shifts by even less because contact
    # quality moves a small share of weak->hard. Looser envelope here
    # covers sampling noise on the larger denominator.
    assert 0.92 < _ratio(up["h_per_ab"],   neutral["h_per_ab"]) < 1.10
    assert 0.92 < _ratio(down["h_per_ab"], neutral["h_per_ab"]) < 1.10

    # K rate envelope: per-pitch K mult bounded ~±4%. Allow 12% on
    # league rate including sampling noise.
    assert 0.88 < _ratio(up["k_per_pa"],   neutral["k_per_pa"]) < 1.12
    assert 0.88 < _ratio(down["k_per_pa"], neutral["k_per_pa"]) < 1.12
