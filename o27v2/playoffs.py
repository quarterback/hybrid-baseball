"""
Phase 4 — Postseason bracket and series simulation.

Field shape:
  n_div_champs  = number of distinct division winners
  n_wild_cards  = max(0, (team_count - 24) // 2)
                  → 0 at ≤24 teams, 3 at 30, 6 at 36, 9 at 42, …
  playoff_teams = max(4, n_div_champs + n_wild_cards)

Single league-wide bracket — division champs seed first (1..N_div by win
pct), then wild cards fill the lower seeds (N_div+1..total by win pct).
For non-power-of-2 fields, top seeds receive a first-round bye until
the surviving count IS a power of 2, then straight elimination.

Series length by rounds-from-final (per the user's spec — 3/5/7/7):
  rounds_to_final == 0  (the final)        → best-of-7
  rounds_to_final == 1  (LCS / semis)      → best-of-7
  rounds_to_final == 2  (DS / quarters)    → best-of-5
  rounds_to_final >= 3  (WC / earliest)    → best-of-3

Calendar:
  Last regular-season game date + 3 days = first playoff game.
  Within a series: one game per day, alternating venues (higher seed
  hosts the odd-numbered games — game 1, 3, 5, 7).
  Across series in the same round: parallel — different series share
  the same calendar dates so the round wraps quickly.
  When a round completes, the next round's first game lands on the day
  after the last completed game of the previous round.

Award integration:
  Phase 4e gives MVP / Cy Young / RoY for the regular season (selected
  the day playoffs are initiated). World Series MVP is selected the
  day the championship series ends.
"""
from __future__ import annotations

import datetime as _dt
import math
import random as _random

from o27v2 import db


# Series-length lookup. Index = rounds-to-final (0 = final).
# Tail-extends to best-of-3 for any deeper bracket.
_SERIES_BEST_OF = [7, 7, 5]   # final, semi, quarter; deeper rounds = 3
_DEFAULT_BEST_OF = 3

_GAP_DAYS_AFTER_REGULAR = 3   # user spec: 3 days between regular season and playoffs


# ---------------------------------------------------------------------------
# Field sizing + seeding
# ---------------------------------------------------------------------------

def _wild_card_count(team_count: int) -> int:
    """Wild-card slots scale with league size: 3 at 30 teams, 6 at 36,
    increasing by 3 per +6 teams. Clamped at 0 for small leagues."""
    return max(0, (team_count - 24) // 2)


def _best_of_for(rounds_to_final: int) -> int:
    if 0 <= rounds_to_final < len(_SERIES_BEST_OF):
        return _SERIES_BEST_OF[rounds_to_final]
    return _DEFAULT_BEST_OF


def compute_field(teams: list[dict]) -> list[dict]:
    """Return the playoff field as a list of seeded team dicts.

    Each entry: {team_id, name, abbrev, seed, kind ('div_champ' or
    'wild_card'), wins, losses, win_pct, division, league}.

    Seeding rule: division winners first (sorted by win pct desc),
    then wild cards (next-best by win pct from the remaining teams).
    Min field of 4 — if (n_div + n_wild) < 4 the gap fills with the
    next-best teams by record regardless of bucket.
    """
    if not teams:
        return []

    # Group by division to find each division's winner. Empty division
    # strings (single-league configs) all share one bucket.
    by_div: dict[str, list[dict]] = {}
    for t in teams:
        by_div.setdefault(t.get("division") or "", []).append(t)

    div_champs: list[dict] = []
    for div, members in by_div.items():
        # Highest win pct in the division.
        best = max(members, key=_win_pct)
        div_champs.append(best)
    div_champs.sort(key=_win_pct, reverse=True)
    div_champ_ids = {t["id"] for t in div_champs}

    # Wild cards: best of the rest.
    rest = [t for t in teams if t["id"] not in div_champ_ids]
    rest.sort(key=_win_pct, reverse=True)

    n_wild = _wild_card_count(len(teams))
    field_size = max(4, len(div_champs) + n_wild)

    seeded: list[dict] = []
    seed = 1
    for t in div_champs[: field_size]:
        seeded.append(_seed_row(t, seed, "div_champ"))
        seed += 1
    for t in rest[: field_size - len(seeded)]:
        seeded.append(_seed_row(t, seed, "wild_card"))
        seed += 1

    # If we still don't have a full field (shouldn't happen with
    # enough teams), trim seed numbers.
    return seeded[:field_size]


def _seed_row(t: dict, seed: int, kind: str) -> dict:
    g = (t.get("wins") or 0) + (t.get("losses") or 0)
    pct = (t.get("wins") or 0) / max(1, g)
    return {
        "team_id":  t["id"],
        "name":     t.get("name", ""),
        "abbrev":   t.get("abbrev", ""),
        "league":   t.get("league") or "",
        "division": t.get("division") or "",
        "wins":     t.get("wins") or 0,
        "losses":   t.get("losses") or 0,
        "win_pct":  pct,
        "seed":     seed,
        "kind":     kind,
    }


def _win_pct(t: dict) -> float:
    g = (t.get("wins") or 0) + (t.get("losses") or 0)
    return (t.get("wins") or 0) / max(1, g)


# ---------------------------------------------------------------------------
# Bracket layout — bye logic for non-power-of-2 fields
# ---------------------------------------------------------------------------

def _round_count(field_size: int) -> int:
    """Total rounds needed to whittle field_size down to a single
    champion. ceil(log2) — e.g. 4 → 2, 8 → 3, 9 → 4 (one bye round)."""
    if field_size <= 1:
        return 0
    return math.ceil(math.log2(field_size))


def _round_one_pairings(seeded_field: list[dict]) -> list[tuple[dict | None, dict]]:
    """Return round-one pairings as a list of (high, low) tuples.

    Top seeds get byes when field isn't a power of 2. With N teams
    and the next power of 2 being P, there are (P - N) bye slots
    awarded to seeds 1..(P - N); the remaining 2*(N - (P - N)) =
    2N - P teams pair up high-vs-low among seeds (P - N + 1)..N.

    A bye is encoded as (None, top_seed): the high slot is unused
    (the team is just "advancing"), and we don't sim any game.
    """
    n = len(seeded_field)
    if n <= 1:
        return []
    p = 2 ** _round_count(n)        # next power of 2 ≥ n
    n_byes = p - n                  # how many teams skip round 1
    pairings: list[tuple[dict | None, dict]] = []

    # Top n_byes seeds advance automatically.
    for i in range(n_byes):
        pairings.append((None, seeded_field[i]))

    # Remaining teams pair seed (n_byes + i) vs seed (n - 1 - i).
    playing = seeded_field[n_byes:]
    half = len(playing) // 2
    for i in range(half):
        high = playing[i]
        low  = playing[len(playing) - 1 - i]
        pairings.append((low, high))   # (low, high) so high_seed is index 1

    return pairings


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------

def _last_regular_season_date() -> str | None:
    row = db.fetchone(
        "SELECT MAX(game_date) AS d FROM games WHERE COALESCE(is_playoff, 0) = 0"
    )
    return row["d"] if row and row["d"] else None


def _last_playoff_date() -> str | None:
    row = db.fetchone(
        "SELECT MAX(game_date) AS d FROM games WHERE COALESCE(is_playoff, 0) = 1"
    )
    return row["d"] if row and row["d"] else None


def regular_season_complete() -> bool:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM games WHERE COALESCE(is_playoff, 0) = 0 AND played = 0"
    )
    return (row and row["n"] == 0)


def playoffs_active() -> bool:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM playoff_series WHERE winner_team_id IS NULL"
    )
    return bool(row and row["n"] > 0)


def playoffs_initiated() -> bool:
    row = db.fetchone("SELECT COUNT(*) AS n FROM playoff_series")
    return bool(row and row["n"] > 0)


def champion() -> dict | None:
    """Return the team that won the final series, or None if no
    playoffs have completed yet."""
    row = db.fetchone("""
        SELECT s.winner_team_id, t.name, t.abbrev
        FROM playoff_series s
        JOIN teams t ON t.id = s.winner_team_id
        WHERE s.rounds_to_final = 0 AND s.winner_team_id IS NOT NULL
        ORDER BY s.id DESC LIMIT 1
    """)
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Bracket initiation + round wiring
# ---------------------------------------------------------------------------

def initiate_playoffs(season: int = 1, rng_seed: int | None = None) -> dict:
    """Create round-1 series rows and schedule their first games.
    Returns a summary dict suitable for flashing to the user.

    Pre-conditions:
      - Regular season complete (all is_playoff=0 games have played=1).
      - No playoff_series rows exist yet.
    """
    if playoffs_initiated():
        return {"ok": False, "reason": "playoffs_already_initiated"}
    if not regular_season_complete():
        return {"ok": False, "reason": "regular_season_incomplete"}

    last_reg = _last_regular_season_date()
    if last_reg is None:
        return {"ok": False, "reason": "no_regular_season"}

    teams = db.fetchall(
        "SELECT id, name, abbrev, league, division, wins, losses FROM teams"
    )
    field = compute_field(teams)
    if len(field) < 2:
        return {"ok": False, "reason": "field_too_small"}

    total_rounds = _round_count(len(field))
    pairings = _round_one_pairings(field)

    start_date = (_dt.date.fromisoformat(last_reg)
                  + _dt.timedelta(days=_GAP_DAYS_AFTER_REGULAR))

    # Insert round-1 series rows; auto-advance byes.
    series_ids: list[int] = []
    for pos, (low, high) in enumerate(pairings):
        rounds_to_final = total_rounds - 1   # round 0 == round_idx 0
        best_of = _best_of_for(rounds_to_final)

        if low is None:
            # Bye — high seed advances. high_wins set to ceil(best_of/2)
            # so the existing-series logic treats it as decided.
            sid = db.execute(
                "INSERT INTO playoff_series ("
                "  season, round_idx, rounds_to_final, bracket_position,"
                "  high_seed, low_seed, high_seed_team_id, low_seed_team_id,"
                "  best_of, high_wins, low_wins, winner_team_id, started_at, ended_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (season, 0, rounds_to_final, pos,
                 high["seed"], None, high["team_id"], None,
                 best_of, math.ceil(best_of / 2), 0, high["team_id"],
                 last_reg, last_reg),
            )
            series_ids.append(sid)
        else:
            sid = db.execute(
                "INSERT INTO playoff_series ("
                "  season, round_idx, rounds_to_final, bracket_position,"
                "  high_seed, low_seed, high_seed_team_id, low_seed_team_id,"
                "  best_of, high_wins, low_wins, winner_team_id, started_at, ended_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (season, 0, rounds_to_final, pos,
                 high["seed"], low["seed"], high["team_id"], low["team_id"],
                 best_of, 0, 0, None, start_date.isoformat(), None),
            )
            series_ids.append(sid)
            _schedule_series_game(sid, game_num=1, base_date=start_date,
                                  rng_seed=rng_seed, season=season)

    # If round 0 is entirely byes (impossible with our field sizing,
    # but defensive), advance to the next round immediately.
    _maybe_advance_round(season=season, rng_seed=rng_seed)

    # Regular-season awards (MVP / Cy Young / RoY) get given out the
    # day playoffs start. WS MVP is selected when the final ends.
    try:
        from o27v2.awards import select_regular_season_awards
        select_regular_season_awards(season=season)
    except Exception:
        # Don't let an awards bug block playoffs starting.
        pass

    return {
        "ok":            True,
        "field_size":    len(field),
        "rounds_total":  total_rounds,
        "series_ids":    series_ids,
        "playoff_start": start_date.isoformat(),
    }


def _maybe_advance_round(season: int, rng_seed: int | None) -> None:
    """If every series in the current round has a winner, generate the
    next round's pairings + first games. No-op when the current round
    is still in progress."""
    while True:
        cur = db.fetchall(
            "SELECT * FROM playoff_series WHERE season = ? "
            "ORDER BY round_idx DESC, bracket_position ASC",
            (season,),
        )
        if not cur:
            return

        cur_round = max(s["round_idx"] for s in cur)
        in_round = [s for s in cur if s["round_idx"] == cur_round]
        if any(s["winner_team_id"] is None for s in in_round):
            return  # round still in progress

        # If the round IS the final, we're done.
        if all(s["rounds_to_final"] == 0 for s in in_round):
            return

        # Build next-round pairings: pair winners (bracket_position 0
        # vs 1, 2 vs 3, etc.) — preserves the bracket spine so 1-seed
        # only meets 2-seed in the final under chalk.
        in_round.sort(key=lambda s: s["bracket_position"])
        next_round_idx       = cur_round + 1
        next_rounds_to_final = (in_round[0]["rounds_to_final"] - 1)
        best_of              = _best_of_for(next_rounds_to_final)

        # First game of the next round lands the day after the last
        # completed game of the current round.
        last_played = db.fetchone(
            "SELECT MAX(game_date) AS d FROM games "
            "WHERE COALESCE(is_playoff, 0) = 1 AND played = 1"
        )
        if last_played and last_played["d"]:
            base = (_dt.date.fromisoformat(last_played["d"])
                    + _dt.timedelta(days=1))
        else:
            base = _dt.date.today()

        new_series_ids: list[int] = []
        for i in range(0, len(in_round), 2):
            a = in_round[i]
            b = in_round[i + 1] if i + 1 < len(in_round) else None
            if b is None:
                # Odd survivor — auto-advance them to the next round.
                # Shouldn't happen with our bracket sizing, but defensive.
                continue
            # Higher seed (lower seed number) is the "high" side.
            high_team_id = a["winner_team_id"]
            low_team_id  = b["winner_team_id"]
            high_seed_no = min(a["high_seed"], b["high_seed"])
            low_seed_no  = max(a["high_seed"], b["high_seed"])
            # Resolve which winner is which by seed number.
            a_seed_no = a["high_seed"]
            if a_seed_no != high_seed_no:
                high_team_id, low_team_id = low_team_id, high_team_id

            sid = db.execute(
                "INSERT INTO playoff_series ("
                "  season, round_idx, rounds_to_final, bracket_position,"
                "  high_seed, low_seed, high_seed_team_id, low_seed_team_id,"
                "  best_of, high_wins, low_wins, winner_team_id, started_at, ended_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (season, next_round_idx, next_rounds_to_final, i // 2,
                 high_seed_no, low_seed_no, high_team_id, low_team_id,
                 best_of, 0, 0, None, base.isoformat(), None),
            )
            new_series_ids.append(sid)
            _schedule_series_game(sid, game_num=1, base_date=base,
                                  rng_seed=rng_seed, season=season)

        if not new_series_ids:
            return  # nothing to schedule, bail


# ---------------------------------------------------------------------------
# Game scheduling within a series
# ---------------------------------------------------------------------------

def _schedule_series_game(series_id: int, game_num: int, base_date: _dt.date,
                           rng_seed: int | None, season: int) -> int:
    """Schedule game N of a series into the games table. Higher seed
    hosts odd games (1, 3, 5, 7); lower seed hosts even (2, 4, 6).
    Returns the new game_id."""
    s = db.fetchone("SELECT * FROM playoff_series WHERE id = ?", (series_id,))
    if not s:
        raise ValueError(f"series {series_id} not found")

    if game_num % 2 == 1:
        home_id, away_id = s["high_seed_team_id"], s["low_seed_team_id"]
    else:
        home_id, away_id = s["low_seed_team_id"], s["high_seed_team_id"]

    game_date = (base_date + _dt.timedelta(days=game_num - 1)).isoformat()

    # Weather: re-roll per playoff game using the home park's city.
    from o27.engine.weather import draw_weather
    home_row = db.fetchone("SELECT city FROM teams WHERE id = ?", (home_id,))
    home_city = (home_row or {}).get("city") or ""
    rng = _random.Random((rng_seed or 0) ^ (series_id * 1009 + game_num))
    w = draw_weather(rng, home_city, game_date)

    return db.execute(
        "INSERT INTO games (season, game_date, home_team_id, away_team_id, "
        "temperature_tier, wind_tier, humidity_tier, precip_tier, cloud_tier, "
        "series_id, is_playoff) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,1)",
        (season, game_date, home_id, away_id,
         w.temperature, w.wind, w.humidity, w.precip, w.cloud,
         series_id),
    )


# ---------------------------------------------------------------------------
# Post-game hook — called from sim.simulate_game
# ---------------------------------------------------------------------------

def post_playoff_game(game_row: dict, season: int = 1, rng_seed: int | None = None) -> None:
    """Update series state after a playoff game completes. Schedules
    the next game of the series, or marks the series decided and
    advances the round if applicable.

    `game_row` must include id, series_id, winner_id, game_date.
    """
    sid = game_row.get("series_id")
    if not sid:
        return
    s = db.fetchone("SELECT * FROM playoff_series WHERE id = ?", (sid,))
    if not s:
        return
    if s["winner_team_id"] is not None:
        return  # series already decided — stale game write

    winner_id = game_row.get("winner_id")
    if winner_id is None:
        return  # tie? engine shouldn't produce one; bail safely

    # Increment the appropriate side's win count.
    if winner_id == s["high_seed_team_id"]:
        new_high, new_low = (s["high_wins"] or 0) + 1, (s["low_wins"] or 0)
    else:
        new_high, new_low = (s["high_wins"] or 0), (s["low_wins"] or 0) + 1

    needed = math.ceil(s["best_of"] / 2)
    decided_winner: int | None = None
    if new_high >= needed:
        decided_winner = s["high_seed_team_id"]
    elif new_low >= needed:
        decided_winner = s["low_seed_team_id"]

    if decided_winner is not None:
        db.execute(
            "UPDATE playoff_series SET high_wins = ?, low_wins = ?, "
            "winner_team_id = ?, ended_at = ? WHERE id = ?",
            (new_high, new_low, decided_winner, game_row["game_date"], sid),
        )
        # If this series was the final, pick WS MVP. Either way, try to
        # advance the bracket.
        if (s["rounds_to_final"] or 0) == 0:
            try:
                from o27v2.awards import select_ws_mvp
                select_ws_mvp(season=season)
            except Exception:
                pass
        _maybe_advance_round(season=season, rng_seed=rng_seed)
    else:
        db.execute(
            "UPDATE playoff_series SET high_wins = ?, low_wins = ? WHERE id = ?",
            (new_high, new_low, sid),
        )
        # Schedule the next game of the series — the day after this one.
        next_game_num = new_high + new_low + 1
        next_date = (_dt.date.fromisoformat(game_row["game_date"])
                     + _dt.timedelta(days=1))
        # Recompute the base_date relative to game 1 so the
        # "alternating-host" math works.
        base_date = next_date - _dt.timedelta(days=next_game_num - 1)
        _schedule_series_game(sid, game_num=next_game_num,
                              base_date=base_date, rng_seed=rng_seed,
                              season=season)


# ---------------------------------------------------------------------------
# Bracket snapshot for UI
# ---------------------------------------------------------------------------

def get_bracket(season: int = 1) -> list[dict]:
    """Return all series for the given season, grouped by round, with
    team names attached for display."""
    rows = db.fetchall("""
        SELECT s.*,
               h.name AS high_name, h.abbrev AS high_abbrev,
               l.name AS low_name,  l.abbrev AS low_abbrev,
               w.name AS winner_name, w.abbrev AS winner_abbrev
        FROM playoff_series s
        LEFT JOIN teams h ON h.id = s.high_seed_team_id
        LEFT JOIN teams l ON l.id = s.low_seed_team_id
        LEFT JOIN teams w ON w.id = s.winner_team_id
        WHERE s.season = ?
        ORDER BY s.round_idx ASC, s.bracket_position ASC
    """, (season,))
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Auto-trigger from sim driver
# ---------------------------------------------------------------------------

def maybe_initiate(season: int = 1, rng_seed: int | None = None) -> dict | None:
    """Idempotent — call after every regular-season day. If the
    regular season just finished and no playoffs are scheduled yet,
    initiate the bracket. Returns the initiation summary or None."""
    if playoffs_initiated():
        return None
    if not regular_season_complete():
        return None
    return initiate_playoffs(season=season, rng_seed=rng_seed)
