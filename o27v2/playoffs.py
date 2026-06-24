"""
Phase 4 — Postseason brackets and series simulation (per-league model).

Each league runs its **own** bracket and crowns its **own** champion. The
leagues are independent: a club in the AL never meets a club in the NL during
a league bracket. When a save holds exactly two leagues (the AL/NL case) and
the World Series is enabled, the two league champions then meet in a single
interleague final — the World Series — to decide the overall title. Saves with
one league simply crown that league's bracket winner; saves with three or more
co-equal leagues each crown a champion with no cross-league final (peer-universe
configs use `postseason: none` and so never reach this code at all).

Field shape (configurable — see `playoff_settings()`):
  teams_per_league  — how many clubs from each league make the bracket. Set
                      from the live control on /playoffs (persisted to
                      sim_meta), defaulting to the league config, then to 4.

Seeding (within a league): division winners seed first (by win pct), then the
best of the rest fill the remaining slots as wild cards, up to teams_per_league.
The bracket uses standard seeding (1 vs lowest, 2 vs second-lowest, …) with the
top seeds receiving first-round byes whenever the field is not a power of two.

Series length by round kind (all configurable, menu of 3/5/7/9):
  wild_card     → best-of-3   (earliest rounds)
  division      → best-of-5
  championship  → best-of-7   (the league final / LCS)
  world_series  → best-of-7   (the interleague final)

Calendar:
  Last regular-season game date + 3 days = first playoff game.
  Within a series: one game per day, alternating venues (higher seed hosts the
  odd-numbered games — game 1, 3, 5, 7, 9). Series across leagues share the
  same calendar dates so a round wraps quickly. The next round's first game
  lands the day after the last completed game of the previous round.

Award integration:
  Regular-season awards (MVP / Cy Young / RoY) are selected the day playoffs are
  initiated. World Series MVP is selected the day the final series ends.
"""
from __future__ import annotations

import datetime as _dt
import math
import random as _random

from o27v2 import db


# Series-length defaults by round kind. Overridable per-config and via the live
# control on /playoffs (persisted to sim_meta).
_DEFAULT_SERIES_LENGTHS = {
    "wild_card":    3,
    "division":     5,
    "championship": 7,
    "world_series": 7,
}
_DEFAULT_TEAMS_PER_LEAGUE = 4
_ALLOWED_BEST_OF = (3, 5, 7, 9)

_GAP_DAYS_AFTER_REGULAR = 3   # days between the regular season and playoffs

# The interleague final carries no single league.
_WS_LEAGUE = ""


# ---------------------------------------------------------------------------
# Settings — live control (sim_meta) layered over the league config
# ---------------------------------------------------------------------------

def _active_config() -> dict | None:
    """The league config that seeded the live save, or None."""
    try:
        row = db.fetchone("SELECT value FROM sim_meta WHERE key = 'league_config'")
        config_id = row["value"] if row and row.get("value") else None
        if not config_id:
            return None
        from o27v2.league import get_config
        return get_config(config_id)
    except Exception:
        return None


def _meta_get(key: str) -> str | None:
    try:
        row = db.fetchone("SELECT value FROM sim_meta WHERE key = ?", (key,))
        return row["value"] if row and row.get("value") is not None else None
    except Exception:
        return None


def _meta_set(key: str, value: str) -> None:
    db.execute(
        "INSERT INTO sim_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def _coerce_best_of(v, default: int) -> int:
    try:
        iv = int(v)
    except (TypeError, ValueError):
        return default
    return iv if iv in _ALLOWED_BEST_OF else default


def league_names() -> list[str]:
    """Distinct league names for the live save, in config order where known."""
    cfg = _active_config()
    if cfg and cfg.get("leagues"):
        return list(cfg["leagues"])
    rows = db.fetchall("SELECT DISTINCT league FROM teams WHERE COALESCE(league,'') <> '' ORDER BY league")
    return [r["league"] for r in rows]


def playoff_settings() -> dict:
    """Resolve the live postseason settings: sim_meta override → league config
    → hard defaults. Returns:
        {teams_per_league, series_lengths: {kind: best_of}, world_series: bool,
         leagues: [...], n_leagues: int}
    """
    cfg = _active_config() or {}
    leagues = league_names()
    n_leagues = len(leagues)

    # teams_per_league
    default_tpl = int(cfg.get("playoff_teams_per_league") or _DEFAULT_TEAMS_PER_LEAGUE)
    tpl_override = _meta_get("playoff_teams_per_league")
    try:
        teams_per_league = int(tpl_override) if tpl_override is not None else default_tpl
    except (TypeError, ValueError):
        teams_per_league = default_tpl
    teams_per_league = max(2, min(16, teams_per_league))

    # series lengths
    cfg_lengths = cfg.get("playoff_series_lengths") or {}
    lengths: dict[str, int] = {}
    for kind, base in _DEFAULT_SERIES_LENGTHS.items():
        cfg_val = _coerce_best_of(cfg_lengths.get(kind), base)
        ov = _meta_get(f"playoff_bestof_{kind}")
        lengths[kind] = _coerce_best_of(ov, cfg_val) if ov is not None else cfg_val

    # world series: only meaningful with exactly two leagues
    default_ws = bool(cfg.get("world_series", n_leagues == 2)) and n_leagues == 2
    ws_override = _meta_get("playoff_world_series")
    if ws_override is not None:
        world_series = (ws_override == "1") and n_leagues == 2
    else:
        world_series = default_ws

    return {
        "teams_per_league": teams_per_league,
        "series_lengths":   lengths,
        "world_series":     world_series,
        "leagues":          leagues,
        "n_leagues":        n_leagues,
    }


def set_playoff_settings(*, teams_per_league: int | None = None,
                         series_lengths: dict | None = None,
                         world_series: bool | None = None) -> dict:
    """Persist live postseason settings to sim_meta. Only effective before the
    bracket is initiated (callers should gate on `playoffs_initiated()`)."""
    if teams_per_league is not None:
        _meta_set("playoff_teams_per_league", str(max(2, min(16, int(teams_per_league)))))
    if series_lengths:
        for kind, val in series_lengths.items():
            if kind in _DEFAULT_SERIES_LENGTHS:
                _meta_set(f"playoff_bestof_{kind}",
                          str(_coerce_best_of(val, _DEFAULT_SERIES_LENGTHS[kind])))
    if world_series is not None:
        _meta_set("playoff_world_series", "1" if world_series else "0")
    return playoff_settings()


# ---------------------------------------------------------------------------
# Field sizing + seeding (per league)
# ---------------------------------------------------------------------------

def _win_pct(t: dict) -> float:
    g = (t.get("wins") or 0) + (t.get("losses") or 0)
    return (t.get("wins") or 0) / max(1, g)


def _seed_row(t: dict, seed: int, kind: str) -> dict:
    return {
        "team_id":  t["id"],
        "name":     t.get("name", ""),
        "abbrev":   t.get("abbrev", ""),
        "league":   t.get("league") or "",
        "division": t.get("division") or "",
        "wins":     t.get("wins") or 0,
        "losses":   t.get("losses") or 0,
        "win_pct":  _win_pct(t),
        "seed":     seed,
        "kind":     kind,   # 'div_champ' | 'wild_card'
    }


def _seed_one_league(members: list[dict], teams_per_league: int) -> list[dict]:
    """Seed a single league's field: division winners first (by win pct), then
    the best of the rest as wild cards, capped at teams_per_league."""
    if not members:
        return []

    by_div: dict[str, list[dict]] = {}
    for t in members:
        by_div.setdefault(t.get("division") or "", []).append(t)

    div_champs = [max(grp, key=_win_pct) for grp in by_div.values()]
    div_champs.sort(key=_win_pct, reverse=True)
    champ_ids = {t["id"] for t in div_champs}

    rest = [t for t in members if t["id"] not in champ_ids]
    rest.sort(key=_win_pct, reverse=True)

    seeded: list[dict] = []
    seed = 1
    for t in div_champs[:teams_per_league]:
        seeded.append(_seed_row(t, seed, "div_champ"))
        seed += 1
    for t in rest[: max(0, teams_per_league - len(seeded))]:
        seeded.append(_seed_row(t, seed, "wild_card"))
        seed += 1
    return seeded


def compute_fields_by_league(teams: list[dict],
                             teams_per_league: int | None = None) -> dict[str, list[dict]]:
    """Return {league: [seeded team dicts]} — one bracket field per league.

    Leagues that can't field at least two qualifiers are dropped (no bracket).
    """
    if teams_per_league is None:
        teams_per_league = playoff_settings()["teams_per_league"]

    by_league: dict[str, list[dict]] = {}
    for t in teams:
        by_league.setdefault(t.get("league") or "", []).append(t)

    out: dict[str, list[dict]] = {}
    for lg in league_names() or list(by_league.keys()):
        members = by_league.get(lg, [])
        field = _seed_one_league(members, teams_per_league)
        if len(field) >= 2:
            out[lg] = field
    return out


def compute_field(teams: list[dict]) -> list[dict]:
    """Back-compat flat view: every league's seeded field concatenated.

    Retained for callers that just want a single projected-field list. Prefer
    `compute_fields_by_league` for the per-league bracket display.
    """
    fields = compute_fields_by_league(teams)
    flat: list[dict] = []
    for lg in sorted(fields):
        flat.extend(fields[lg])
    return flat


# ---------------------------------------------------------------------------
# Bracket layout — standard seeding with byes for non-power-of-2 fields
# ---------------------------------------------------------------------------

def _round_count(field_size: int) -> int:
    """Rounds needed to reduce field_size to one champion (ceil(log2))."""
    if field_size <= 1:
        return 0
    return math.ceil(math.log2(field_size))


def _bracket_seed_order(n: int) -> list[int]:
    """Standard single-elimination seed order for a power-of-2 bracket of size
    n. e.g. n=4 → [1,4,2,3]; n=8 → [1,8,4,5,2,7,3,6]. Pairing consecutive
    entries gives 1-vs-lowest, top seeds on opposite halves."""
    order = [1]
    while len(order) < n:
        m = len(order) * 2
        nxt: list[int] = []
        for x in order:
            nxt.append(x)
            nxt.append(m + 1 - x)
        order = nxt
    return order


def _round_one_pairings(seeded_field: list[dict]) -> list[tuple[dict | None, dict]]:
    """Round-one pairings as (low, high) tuples (index 1 = higher seed).

    A bye is encoded (None, high_seed): the slot's opponent doesn't exist, so
    the high seed simply advances and no game is simulated. Standard seeding
    places byes on the top seeds.
    """
    n = len(seeded_field)
    if n <= 1:
        return []
    p = 2 ** _round_count(n)            # next power of 2 >= n
    order = _bracket_seed_order(p)      # seed numbers 1..p, p>n entries are byes

    pairings: list[tuple[dict | None, dict]] = []
    for i in range(0, len(order), 2):
        a_seed, b_seed = order[i], order[i + 1]
        a = seeded_field[a_seed - 1] if a_seed <= n else None
        b = seeded_field[b_seed - 1] if b_seed <= n else None
        # Whichever exists and is the better seed becomes "high".
        if a is None and b is None:
            continue
        if a is None:
            pairings.append((None, b))
        elif b is None:
            pairings.append((None, a))
        else:
            high, low = (a, b) if a["seed"] < b["seed"] else (b, a)
            pairings.append((low, high))
    return pairings


# ---------------------------------------------------------------------------
# Round-kind + series-length helpers
# ---------------------------------------------------------------------------

def _kind_for_rounds_to_final(rounds_to_final: int) -> str:
    """League-bracket round flavour by distance from the league final."""
    if rounds_to_final <= 0:
        return "championship"
    if rounds_to_final == 1:
        return "division"
    return "wild_card"


def round_label(series_kind: str) -> str:
    return {
        "wild_card":    "Wild Card",
        "division":     "Division Series",
        "championship": "League Championship",
        "world_series": "World Series",
    }.get(series_kind or "", "Playoff Round")


def _best_of(series_kind: str, settings: dict | None = None) -> int:
    s = settings or playoff_settings()
    return s["series_lengths"].get(series_kind, _DEFAULT_SERIES_LENGTHS.get(series_kind, 7))


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------

def _last_regular_season_date() -> str | None:
    row = db.fetchone(
        "SELECT MAX(game_date) AS d FROM games WHERE COALESCE(is_playoff, 0) = 0"
    )
    return row["d"] if row and row["d"] else None


def regular_season_complete() -> bool:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM games WHERE COALESCE(is_playoff, 0) = 0 AND played = 0"
    )
    return bool(row and row["n"] == 0)


def playoffs_active() -> bool:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM playoff_series WHERE winner_team_id IS NULL"
    )
    return bool(row and row["n"] > 0)


def playoffs_initiated() -> bool:
    row = db.fetchone("SELECT COUNT(*) AS n FROM playoff_series")
    return bool(row and row["n"] > 0)


def champion() -> dict | None:
    """The overall champion, or None if not yet decided.

    World Series winner when a WS was played; otherwise the lone league's
    championship winner. With 3+ co-equal leagues there is no single overall
    champion, so this returns None (each league crowns its own — see
    `league_champions()`)."""
    ws = db.fetchone("""
        SELECT s.winner_team_id, t.name, t.abbrev
        FROM playoff_series s JOIN teams t ON t.id = s.winner_team_id
        WHERE s.series_kind = 'world_series' AND s.winner_team_id IS NOT NULL
        ORDER BY s.id DESC LIMIT 1
    """)
    if ws:
        return dict(ws)

    leagues = db.fetchall(
        "SELECT DISTINCT league FROM playoff_series WHERE COALESCE(league,'') <> ''"
    )
    if len(leagues) == 1:
        row = db.fetchone("""
            SELECT s.winner_team_id, t.name, t.abbrev
            FROM playoff_series s JOIN teams t ON t.id = s.winner_team_id
            WHERE s.series_kind = 'championship' AND s.winner_team_id IS NOT NULL
            ORDER BY s.id DESC LIMIT 1
        """)
        return dict(row) if row else None
    return None



def team_postseason_status(team_id: int, season: int = 1) -> dict:
    """Return a compact, UI-friendly current-season postseason status for a team.

    Empty dict means the current live season has no bracket row for the club.
    Status is derived from playoff_series, so it answers the user-facing question
    "did this team make the postseason?" even after later rounds are underway.
    """
    rows = db.fetchall(
        """SELECT * FROM playoff_series
           WHERE season = ?
             AND (high_seed_team_id = ? OR low_seed_team_id = ? OR winner_team_id = ?)
           ORDER BY round_idx ASC, id ASC""",
        (season, team_id, team_id, team_id),
    )
    if not rows:
        return {}
    first = rows[0]
    last = rows[-1]
    seed = first["high_seed"] if first["high_seed_team_id"] == team_id else first["low_seed"]
    berth = "Postseason berth"
    # playoff_series does not store bid type, so infer from initial seed versus
    # division count in that league. This mirrors compute_fields_by_league.
    try:
        div_count = (db.fetchone(
            "SELECT COUNT(DISTINCT division) AS n FROM teams WHERE COALESCE(league,'') = ?",
            (first["league"] or "",),
        ) or {}).get("n") or 0
        berth = "Division winner" if (seed or 999) <= div_count else "Wild Card"
    except Exception:
        pass
    eliminated = bool(last["winner_team_id"] is not None and last["winner_team_id"] != team_id)
    active = bool(last["winner_team_id"] is None)
    champion_row = champion()
    champion_team_id = champion_row["winner_team_id"] if champion_row else None
    won_title = bool(champion_team_id == team_id)
    if won_title:
        state = "Champion"
    elif eliminated:
        state = f"Eliminated in {round_label(last['series_kind'])}"
    elif active:
        state = f"Alive in {round_label(last['series_kind'])}"
    else:
        state = f"Advanced through {round_label(last['series_kind'])}"
    return {
        "made": True,
        "seed": seed,
        "berth": berth,
        "state": state,
        "round": round_label(last["series_kind"]),
        "series_wins": last["high_wins"] if last["high_seed_team_id"] == team_id else last["low_wins"],
        "series_losses": last["low_wins"] if last["high_seed_team_id"] == team_id else last["high_wins"],
    }

def league_champions() -> list[dict]:
    """One row per league that has crowned a champion (its 'championship'
    series winner). Useful for multi-league saves with no World Series."""
    rows = db.fetchall("""
        SELECT s.league, s.winner_team_id, t.name, t.abbrev
        FROM playoff_series s JOIN teams t ON t.id = s.winner_team_id
        WHERE s.series_kind = 'championship' AND s.winner_team_id IS NOT NULL
        ORDER BY s.league
    """)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Bracket initiation
# ---------------------------------------------------------------------------

def postseason_disabled() -> bool:
    """True when the live league opts out of the postseason (soccer model —
    the regular-season table winner is the champion).

    A per-save override (sim_meta 'postseason_format', stamped at league
    creation) is the source of truth when present, so the choice sticks for
    custom/universe saves whose config isn't reloadable by id. Older saves
    without the key fall back to the league config's `postseason` field."""
    ov = _meta_get("postseason_format")
    if ov is not None and ov.strip():
        return ov.strip().lower() == "none"
    cfg = _active_config()
    if not cfg:
        return False
    return (cfg.get("postseason") or "").lower() == "none"


def table_winner_champions() -> list[dict]:
    """Soccer model: each league's regular-season table winner is its champion.

    Returns one row per league (best win pct; ties broken by more wins), but
    only once the postseason is disabled AND the regular season is complete —
    so the live postseason page can crown the title that a no-bracket league
    actually decides. Returns [] otherwise."""
    if not postseason_disabled() or not regular_season_complete():
        return []
    rows = db.fetchall(
        "SELECT id, name, abbrev, league, division, wins, losses FROM teams")
    best: dict[str, dict] = {}
    for t in rows:
        lg = t["league"] or "—"
        w, l = (t["wins"] or 0), (t["losses"] or 0)
        pct = w / max(1, w + l)
        cur = best.get(lg)
        if cur is None or pct > cur["_pct"] or (pct == cur["_pct"] and w > cur["wins"]):
            best[lg] = {"league": lg, "name": t["name"], "abbrev": t["abbrev"],
                        "wins": w, "losses": l, "_pct": pct}
    return [best[k] for k in sorted(best)]


def _insert_series(*, season: int, round_idx: int, rounds_to_final: int,
                   bracket_position: int, league: str, series_kind: str,
                   high: dict, low: dict | None, best_of: int,
                   start_date_iso: str | None, decided_bye: bool = False) -> int:
    """Insert one playoff_series row. A bye pre-fills the high seed as winner."""
    if decided_bye or low is None:
        return db.execute(
            "INSERT INTO playoff_series ("
            "  season, round_idx, rounds_to_final, bracket_position, league, series_kind,"
            "  high_seed, low_seed, high_seed_team_id, low_seed_team_id,"
            "  best_of, high_wins, low_wins, winner_team_id, started_at, ended_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (season, round_idx, rounds_to_final, bracket_position, league, series_kind,
             high["seed"], None, high["team_id"], None,
             best_of, math.ceil(best_of / 2), 0, high["team_id"],
             start_date_iso, start_date_iso),
        )
    return db.execute(
        "INSERT INTO playoff_series ("
        "  season, round_idx, rounds_to_final, bracket_position, league, series_kind,"
        "  high_seed, low_seed, high_seed_team_id, low_seed_team_id,"
        "  best_of, high_wins, low_wins, winner_team_id, started_at, ended_at"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (season, round_idx, rounds_to_final, bracket_position, league, series_kind,
         high["seed"], low["seed"], high["team_id"], low["team_id"],
         best_of, 0, 0, None, start_date_iso, None),
    )


def initiate_playoffs(season: int = 1, rng_seed: int | None = None) -> dict:
    """Create round-1 series for every league and schedule their first games.

    Pre-conditions:
      - The active config has not opted out of the postseason.
      - No playoff_series rows exist yet.
      - Regular season complete (all is_playoff=0 games have played=1).
    """
    if postseason_disabled():
        return {"ok": False, "reason": "postseason_disabled"}
    if playoffs_initiated():
        return {"ok": False, "reason": "playoffs_already_initiated"}
    if not regular_season_complete():
        return {"ok": False, "reason": "regular_season_incomplete"}

    last_reg = _last_regular_season_date()
    if last_reg is None:
        return {"ok": False, "reason": "no_regular_season"}

    settings = playoff_settings()
    teams = db.fetchall(
        "SELECT id, name, abbrev, league, division, wins, losses FROM teams"
    )
    fields = compute_fields_by_league(teams, settings["teams_per_league"])
    if not fields:
        return {"ok": False, "reason": "field_too_small"}

    start_date = (_dt.date.fromisoformat(last_reg)
                  + _dt.timedelta(days=_GAP_DAYS_AFTER_REGULAR))

    series_ids: list[int] = []
    max_rounds = 0
    for league, field in fields.items():
        total_rounds = _round_count(len(field))
        max_rounds = max(max_rounds, total_rounds)
        pairings = _round_one_pairings(field)
        rounds_to_final = total_rounds - 1
        kind = _kind_for_rounds_to_final(rounds_to_final)
        best_of = _best_of(kind, settings)

        for pos, (low, high) in enumerate(pairings):
            if low is None:
                sid = _insert_series(
                    season=season, round_idx=0, rounds_to_final=rounds_to_final,
                    bracket_position=pos, league=league, series_kind=kind,
                    high=high, low=None, best_of=best_of,
                    start_date_iso=last_reg, decided_bye=True)
                series_ids.append(sid)
            else:
                sid = _insert_series(
                    season=season, round_idx=0, rounds_to_final=rounds_to_final,
                    bracket_position=pos, league=league, series_kind=kind,
                    high=high, low=low, best_of=best_of,
                    start_date_iso=start_date.isoformat())
                series_ids.append(sid)
                _schedule_series_game(sid, game_num=1, base_date=start_date,
                                      rng_seed=rng_seed, season=season)

    # Round 0 may be entirely byes (tiny field); advance immediately if so.
    _maybe_advance_round(season=season, rng_seed=rng_seed)

    # Regular-season awards land the day playoffs start.
    try:
        from o27v2.awards import select_regular_season_awards
        select_regular_season_awards(season=season)
    except Exception:
        pass

    return {
        "ok":            True,
        "leagues":       list(fields.keys()),
        "teams_per_league": settings["teams_per_league"],
        "world_series":  settings["world_series"],
        "rounds_total":  max_rounds,
        "series_ids":    series_ids,
        "playoff_start": start_date.isoformat(),
    }


# ---------------------------------------------------------------------------
# Round advancement
# ---------------------------------------------------------------------------

def _next_playoff_base_date() -> _dt.date:
    last_played = db.fetchone(
        "SELECT MAX(game_date) AS d FROM games "
        "WHERE COALESCE(is_playoff, 0) = 1 AND played = 1"
    )
    if last_played and last_played["d"]:
        return _dt.date.fromisoformat(last_played["d"]) + _dt.timedelta(days=1)
    return _dt.date.today()


def _advance_one_league(season: int, league: str, rng_seed: int | None) -> bool:
    """If the current round of `league` is fully decided and isn't the league
    final, build the next round's series. Returns True if it created series."""
    rows = db.fetchall(
        "SELECT * FROM playoff_series WHERE season = ? AND league = ? "
        "ORDER BY round_idx DESC, bracket_position ASC",
        (season, league),
    )
    if not rows:
        return False
    cur_round = max(s["round_idx"] for s in rows)
    in_round = [s for s in rows if s["round_idx"] == cur_round]
    if any(s["winner_team_id"] is None for s in in_round):
        return False  # still in progress
    if all((s["rounds_to_final"] or 0) == 0 for s in in_round):
        return False  # league final decided — champion crowned

    in_round.sort(key=lambda s: s["bracket_position"])
    next_round_idx = cur_round + 1
    next_rtf = (in_round[0]["rounds_to_final"] or 0) - 1
    kind = _kind_for_rounds_to_final(next_rtf)
    best_of = _best_of(kind)
    base = _next_playoff_base_date()

    created = False
    for i in range(0, len(in_round), 2):
        a = in_round[i]
        b = in_round[i + 1] if i + 1 < len(in_round) else None
        if b is None:
            continue  # odd survivor (shouldn't happen with standard seeding)
        high_seed_no = min(a["high_seed"], b["high_seed"])
        if a["high_seed"] == high_seed_no:
            high, low = a, b
        else:
            high, low = b, a
        sid = _insert_series(
            season=season, round_idx=next_round_idx, rounds_to_final=next_rtf,
            bracket_position=i // 2, league=league, series_kind=kind,
            high={"seed": high["high_seed"], "team_id": high["winner_team_id"]},
            low={"seed": low["high_seed"], "team_id": low["winner_team_id"]},
            best_of=best_of, start_date_iso=base.isoformat())
        _schedule_series_game(sid, game_num=1, base_date=base,
                              rng_seed=rng_seed, season=season)
        created = True
    return created


def _maybe_create_world_series(season: int, rng_seed: int | None) -> bool:
    """When both league champions are decided in a two-league save (and the WS
    is enabled), create the interleague final. Returns True if it created it."""
    settings = playoff_settings()
    if not settings["world_series"]:
        return False
    # Already have a WS?
    existing = db.fetchone(
        "SELECT COUNT(*) AS n FROM playoff_series "
        "WHERE season = ? AND series_kind = 'world_series'", (season,))
    if existing and existing["n"]:
        return False

    champs = db.fetchall("""
        SELECT s.league, s.winner_team_id AS team_id,
               t.wins, t.losses, t.name, t.abbrev
        FROM playoff_series s JOIN teams t ON t.id = s.winner_team_id
        WHERE s.season = ? AND s.series_kind = 'championship'
              AND s.winner_team_id IS NOT NULL
    """, (season,))
    if len(champs) != 2:
        return False

    # Higher regular-season win pct hosts (high seed 1).
    champs.sort(key=_win_pct, reverse=True)
    high = {"seed": 1, "team_id": champs[0]["team_id"]}
    low  = {"seed": 2, "team_id": champs[1]["team_id"]}
    best_of = _best_of("world_series", settings)
    base = _next_playoff_base_date()

    next_round_idx = (db.fetchone(
        "SELECT MAX(round_idx) AS r FROM playoff_series WHERE season = ?",
        (season,)) or {}).get("r")
    next_round_idx = (next_round_idx or 0) + 1

    sid = _insert_series(
        season=season, round_idx=next_round_idx, rounds_to_final=0,
        bracket_position=0, league=_WS_LEAGUE, series_kind="world_series",
        high=high, low=low, best_of=best_of, start_date_iso=base.isoformat())
    _schedule_series_game(sid, game_num=1, base_date=base,
                          rng_seed=rng_seed, season=season)
    return True


def _maybe_advance_round(season: int, rng_seed: int | None) -> None:
    """Advance every league's bracket as far as it can go, then create the
    World Series once both league champions exist. Loops until stable."""
    while True:
        changed = False
        leagues = db.fetchall(
            "SELECT DISTINCT league FROM playoff_series "
            "WHERE season = ? AND COALESCE(league,'') <> ''", (season,))
        for r in leagues:
            if _advance_one_league(season, r["league"], rng_seed):
                changed = True
        if _maybe_create_world_series(season, rng_seed):
            changed = True
        if not changed:
            return


# ---------------------------------------------------------------------------
# Game scheduling within a series
# ---------------------------------------------------------------------------

def _schedule_series_game(series_id: int, game_num: int, base_date: _dt.date,
                          rng_seed: int | None, season: int) -> int:
    """Schedule game N of a series. Higher seed hosts odd games (1,3,5,7,9);
    lower seed hosts even (2,4,6,8). Returns the new game_id."""
    s = db.fetchone("SELECT * FROM playoff_series WHERE id = ?", (series_id,))
    if not s:
        raise ValueError(f"series {series_id} not found")

    if game_num % 2 == 1:
        home_id, away_id = s["high_seed_team_id"], s["low_seed_team_id"]
    else:
        home_id, away_id = s["low_seed_team_id"], s["high_seed_team_id"]

    game_date = (base_date + _dt.timedelta(days=game_num - 1)).isoformat()

    from o27.engine.weather import draw_weather
    from o27.engine.gametime import draw_game_time
    home_row = db.fetchone("SELECT city, lat, lon FROM teams WHERE id = ?", (home_id,))
    home_city = (home_row or {}).get("city") or ""
    h_lat = (home_row or {}).get("lat")
    h_lon = (home_row or {}).get("lon")
    rng = _random.Random((rng_seed or 0) ^ (series_id * 1009 + game_num))
    w = draw_weather(rng, home_city, game_date, lat=h_lat, lon=h_lon)
    gt = draw_game_time(rng, game_date, lat=h_lat, lon=h_lon, city=home_city)

    return db.execute(
        "INSERT INTO games (season, game_date, home_team_id, away_team_id, "
        "temperature_tier, wind_tier, humidity_tier, precip_tier, cloud_tier, "
        "temperature_f, start_minute, start_utc_offset, low_light, "
        "series_id, is_playoff) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        (season, game_date, home_id, away_id,
         w.temperature, w.wind, w.humidity, w.precip, w.cloud,
         w.temperature_f, gt.start_minute, gt.utc_offset, int(gt.low_light),
         series_id),
    )


# ---------------------------------------------------------------------------
# Post-game hook — called from sim.simulate_game
# ---------------------------------------------------------------------------

def post_playoff_game(game_row: dict, season: int = 1, rng_seed: int | None = None) -> None:
    """Update series state after a playoff game completes. Schedules the next
    game of the series, or marks it decided and advances the bracket."""
    sid = game_row.get("series_id")
    if not sid:
        return
    s = db.fetchone("SELECT * FROM playoff_series WHERE id = ?", (sid,))
    if not s:
        return
    if s["winner_team_id"] is not None:
        return  # already decided — stale game write

    winner_id = game_row.get("winner_id")
    if winner_id is None:
        return  # tie? engine shouldn't produce one; bail safely

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
        # World Series ending → pick WS MVP.
        if (s.get("series_kind") or "") == "world_series":
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
        next_game_num = new_high + new_low + 1
        next_date = (_dt.date.fromisoformat(game_row["game_date"])
                     + _dt.timedelta(days=1))
        base_date = next_date - _dt.timedelta(days=next_game_num - 1)
        _schedule_series_game(sid, game_num=next_game_num,
                              base_date=base_date, rng_seed=rng_seed,
                              season=season)


# ---------------------------------------------------------------------------
# Bracket snapshot for UI
# ---------------------------------------------------------------------------

def get_bracket(season: int = 1) -> list[dict]:
    """All series for the season with team names attached, ordered for display
    (league, then round, then bracket position; World Series last)."""
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
        ORDER BY (CASE WHEN s.series_kind = 'world_series' THEN 1 ELSE 0 END),
                 s.league ASC, s.round_idx ASC, s.bracket_position ASC
    """, (season,))
    return [dict(r) for r in rows]


def get_bracket_by_league(season: int = 1) -> dict:
    """Bracket grouped for the UI:
        {"leagues": {league: [ {round_idx, rounds_to_final, series_kind,
                                series:[...]} ]},
         "world_series": [series...]}
    """
    bracket = get_bracket(season)
    leagues: dict[str, dict[int, list[dict]]] = {}
    world_series: list[dict] = []
    for s in bracket:
        if (s.get("series_kind") or "") == "world_series":
            world_series.append(s)
            continue
        leagues.setdefault(s["league"] or "", {}).setdefault(s["round_idx"], []).append(s)

    out_leagues: dict[str, list[dict]] = {}
    for lg, rounds in leagues.items():
        round_list = []
        for ridx in sorted(rounds):
            series = rounds[ridx]
            round_list.append({
                "round_idx":        ridx,
                "rounds_to_final":  series[0]["rounds_to_final"],
                "series_kind":      series[0].get("series_kind") or "",
                "best_of":          series[0]["best_of"],
                "series":           series,
            })
        out_leagues[lg] = round_list
    return {"leagues": out_leagues, "world_series": world_series}


# ---------------------------------------------------------------------------
# Auto-trigger from sim driver
# ---------------------------------------------------------------------------

def maybe_initiate(season: int = 1, rng_seed: int | None = None) -> dict | None:
    """Idempotent — call after every regular-season day. Initiates the bracket
    once the regular season finishes. Returns the summary, or None."""
    if playoffs_initiated():
        return None
    if not regular_season_complete():
        return None
    if postseason_disabled():
        # Soccer model: no bracket, but still grant regular-season awards.
        try:
            from o27v2.awards import select_regular_season_awards
            select_regular_season_awards(season=season)
        except Exception:
            pass
        return None
    return initiate_playoffs(season=season, rng_seed=rng_seed)
