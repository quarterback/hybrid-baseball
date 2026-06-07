"""CapSpace — Sportsbook (bet the sim games against the book's lines).

A solo, play-money book. Each slate the house posts a **moneyline** (who wins)
and a **run total** (over/under) for every game, priced from each team's
season form — Pythagorean win% and regressed runs-for/against — never from the
game's predetermined seed. You stake units from a persistent bankroll; bets
settle off the final score once the game is played. All normal-baseball: who
wins, how many runs.
"""

from __future__ import annotations

import datetime as _dt

from . import fdb as db  # CapSpace's own DB (separate file)
from . import data as slate_data
from ._schema_once import once
from . import wallet

PYTHAG_EXP = 1.83
PRIOR_G = 8          # regress team rates toward league avg by this many games
VIG = 0.045          # moneyline hold
TOTAL_ODDS = -110    # standard juice on the over/under
_LG_FALLBACK = 11.0  # league runs/team/game before any games are played


@once
def ensure_schema() -> None:
    conn = db.get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sb_bets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id    INTEGER NOT NULL,
            market     TEXT NOT NULL,            -- 'ml' | 'total'
            side       TEXT NOT NULL,            -- home/away | over/under
            line       REAL,                     -- total line (NULL for ml)
            odds       INTEGER NOT NULL,         -- American odds snapshot
            stake      REAL NOT NULL,
            status     TEXT NOT NULL DEFAULT 'open',  -- open|won|lost|push
            payout     REAL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sb_lines (
            game_id    INTEGER PRIMARY KEY,
            ml_home    INTEGER, ml_away INTEGER,
            total      REAL, over_odds INTEGER, under_odds INTEGER,
            created_at TEXT
        );
        """
    )
    conn.commit()


# --- odds math ---------------------------------------------------------------

def _american(p: float, vig: bool = False) -> int:
    q = min(0.98, max(0.02, p + (VIG / 2 if vig else 0.0)))
    if q >= 0.5:
        return int(round(-100 * q / (1 - q)))
    return int(round(100 * (1 - q) / q))


def _decimal(amer: int) -> float:
    return 1 + amer / 100.0 if amer > 0 else 1 + 100.0 / abs(amer)


# --- team strength -----------------------------------------------------------

def _team_stats() -> dict:
    rows = db.fetchall(
        """
        SELECT t.id, t.abbrev, t.name,
          COALESCE(SUM(CASE WHEN g.played=1 AND g.is_playoff=0 AND g.home_team_id=t.id THEN g.home_score END),0)
          + COALESCE(SUM(CASE WHEN g.played=1 AND g.is_playoff=0 AND g.away_team_id=t.id THEN g.away_score END),0) AS r,
          COALESCE(SUM(CASE WHEN g.played=1 AND g.is_playoff=0 AND g.home_team_id=t.id THEN g.away_score END),0)
          + COALESCE(SUM(CASE WHEN g.played=1 AND g.is_playoff=0 AND g.away_team_id=t.id THEN g.home_score END),0) AS ra,
          COALESCE(SUM(CASE WHEN g.played=1 AND g.is_playoff=0 AND (g.home_team_id=t.id OR g.away_team_id=t.id) THEN 1 END),0) AS gp
        FROM teams t
        LEFT JOIN games g ON (g.home_team_id = t.id OR g.away_team_id = t.id)
        GROUP BY t.id
        """
    )
    return {r["id"]: dict(r) for r in rows}


def _league_rpg(stats: dict) -> float:
    tot_r = sum(s["r"] for s in stats.values())
    tot_gp = sum(s["gp"] for s in stats.values())
    return (tot_r / tot_gp) if tot_gp else _LG_FALLBACK


def _profile(s: dict, lg: float):
    """Regressed (rs/g, ra/g, win%) for one team."""
    gp = s["gp"] or 0
    rs = (s["r"] + PRIOR_G * lg) / (gp + PRIOR_G)
    ra = (s["ra"] + PRIOR_G * lg) / (gp + PRIOR_G)
    R = s["r"] + PRIOR_G * lg
    RA = s["ra"] + PRIOR_G * lg
    wp = (R ** PYTHAG_EXP) / (R ** PYTHAG_EXP + RA ** PYTHAG_EXP) if (R + RA) else 0.5
    return rs, ra, wp


def _line_for(home_s, away_s, lg) -> dict:
    h_rs, h_ra, h_wp = _profile(home_s, lg)
    a_rs, a_ra, a_wp = _profile(away_s, lg)
    denom = h_wp + a_wp - 2 * h_wp * a_wp
    p = (h_wp - h_wp * a_wp) / denom if denom else 0.5
    p = min(0.90, max(0.10, p + 0.03))  # home-field edge + clamp
    total = round(((h_rs + a_ra) / 2 + (a_rs + h_ra) / 2) * 2) / 2
    return {
        "ml_home": _american(p, vig=True),
        "ml_away": _american(1 - p, vig=True),
        "total": total,
        "over_odds": TOTAL_ODDS, "under_odds": TOTAL_ODDS,
    }


def _line(game_id: int, home_id: int, away_id: int, stats: dict, lg: float) -> dict:
    """The line for a game — persisted on first sight so the price a bettor
    sees on the board is exactly the price their bet settles at (it never
    drifts as other games on the slate go final)."""
    row = db.fetchone(
        "SELECT ml_home, ml_away, total, over_odds, under_odds FROM sb_lines WHERE game_id = ?",
        (game_id,))
    if row:
        return dict(row)
    line = _line_for(stats.get(home_id, {"r": 0, "ra": 0, "gp": 0}),
                     stats.get(away_id, {"r": 0, "ra": 0, "gp": 0}), lg)
    conn = db.get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO sb_lines "
        "(game_id, ml_home, ml_away, total, over_odds, under_odds, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (game_id, line["ml_home"], line["ml_away"], line["total"],
         line["over_odds"], line["under_odds"],
         _dt.datetime.utcnow().isoformat(timespec="seconds")))
    conn.commit()
    return line


# --- lines / slate -----------------------------------------------------------

def _slate_games(slate_date: str) -> list[dict]:
    stats = _team_stats()
    lg = _league_rpg(stats)
    out = []
    for g in db.fetchall(
        "SELECT g.id, g.home_team_id, g.away_team_id, ht.abbrev htm, at.abbrev atm, "
        "ht.name hname, at.name aname FROM games g "
        "JOIN teams ht ON g.home_team_id = ht.id JOIN teams at ON g.away_team_id = at.id "
        "WHERE g.game_date = ? AND g.played = 0 ORDER BY g.id", (slate_date,)
    ):
        line = _line(g["id"], g["home_team_id"], g["away_team_id"], stats, lg)
        out.append({"game_id": g["id"], "home": g["htm"], "away": g["atm"],
                    "homeName": g["hname"], "awayName": g["aname"], **line})
    return out


# --- settlement --------------------------------------------------------------

def _grade(bet: dict, game: dict) -> tuple[str, float]:
    """(status, payout) for a now-final game. payout includes returned stake."""
    h, a = game["home_score"], game["away_score"]
    dec = _decimal(bet["odds"])
    if bet["market"] == "ml":
        won = (h > a) if bet["side"] == "home" else (a > h)
        if h == a:  # ties shouldn't happen (super-inning), treat as push
            return "push", bet["stake"]
        return ("won", round(bet["stake"] * dec, 2)) if won else ("lost", 0.0)
    # total
    tot = h + a
    if tot == bet["line"]:
        return "push", bet["stake"]
    over = tot > bet["line"]
    won = over if bet["side"] == "over" else not over
    return ("won", round(bet["stake"] * dec, 2)) if won else ("lost", 0.0)


def settle_bets() -> None:
    """Grade open bets whose games are final, paying winnings into the wallet."""
    ensure_schema()
    open_bets = db.fetchall("SELECT * FROM sb_bets WHERE status = 'open'")
    if not open_bets:
        return
    graded = []  # (status, payout, bet_id)
    for b in open_bets:
        g = db.fetchone("SELECT played, home_score, away_score FROM games WHERE id = ?", (b["game_id"],))
        if not g or not g["played"]:
            continue
        st, payout = _grade(dict(b), dict(g))
        graded.append((st, round(payout), b["id"]))
    if not graded:
        return
    # Write + commit the gradings FIRST so the write lock is released before
    # wallet.credit() opens its own connection — holding it across the credit
    # write self-deadlocks ("database is locked").
    conn = db.get_conn()
    for st, payout, bid in graded:
        conn.execute("UPDATE sb_bets SET status = ?, payout = ? WHERE id = ?", (st, payout, bid))
    conn.commit()
    for st, payout, _bid in graded:
        if payout > 0:
            # a push returns the stake (not a "cash"); a win is a cash
            wallet.credit(payout, cash=(st == "won"))


# --- public API --------------------------------------------------------------

def _bet_view(b: dict) -> dict:
    g = db.fetchone(
        "SELECT ht.abbrev htm, at.abbrev atm, g.played, g.home_score, g.away_score "
        "FROM games g JOIN teams ht ON g.home_team_id=ht.id JOIN teams at ON g.away_team_id=at.id "
        "WHERE g.id = ?", (b["game_id"],))
    g = dict(g) if g else {}
    if b["market"] == "ml":
        pick = (g.get("htm") if b["side"] == "home" else g.get("atm")) or b["side"]
        desc = f"{pick} ML"
    else:
        desc = f"{b['side'].title()} {b['line']}"
    matchup = f"{g.get('atm','?')} @ {g.get('htm','?')}"
    score = (f"{g['away_score']}-{g['home_score']}"
             if g.get("played") else None)
    return {"id": b["id"], "desc": desc, "matchup": matchup, "odds": b["odds"],
            "stake": round(b["stake"], 2), "status": b["status"],
            "payout": round(b["payout"], 2), "score": score}


def status() -> dict:
    ensure_schema()
    # No inline settle: settling writes the wallet/sb_bets and must not run on
    # the request path (it collided with the sim's writes → "database is
    # locked"). The background pass (blueprint._kick_settle) does it; this just
    # reads + grades for display.
    slate = slate_data._slate_date()
    games = _slate_games(slate) if slate else []
    open_bets = [_bet_view(dict(b)) for b in db.fetchall(
        "SELECT * FROM sb_bets WHERE status = 'open' ORDER BY id DESC")]
    settled = [_bet_view(dict(b)) for b in db.fetchall(
        "SELECT * FROM sb_bets WHERE status != 'open' ORDER BY id DESC LIMIT 20")]
    staked_open = sum(b["stake"] for b in open_bets)
    record = db.fetchone(
        "SELECT SUM(status='won') w, SUM(status='lost') l, SUM(status='push') p, "
        "COALESCE(SUM(payout),0) - COALESCE(SUM(CASE WHEN status!='open' THEN stake END),0) AS net "
        "FROM sb_bets WHERE status != 'open'")
    return {
        "bankroll": round(wallet.balance()),
        "slate_date": slate,
        "games": games,
        "open": open_bets,
        "settled": settled,
        "at_risk": round(staked_open, 2),
        "record": {"w": (record["w"] or 0), "l": (record["l"] or 0),
                   "p": (record["p"] or 0), "net": round(record["net"] or 0, 2)},
    }


def activity_bets() -> dict:
    """Read-only open/settled bet feed for the cross-game activity page — no
    settle and no live-odds rebuild (those made /api/activity slow and made it
    write on the request path). Background settle keeps the wallet current."""
    ensure_schema()
    open_bets = [_bet_view(dict(b)) for b in db.fetchall(
        "SELECT * FROM sb_bets WHERE status = 'open' ORDER BY id DESC")]
    settled = [_bet_view(dict(b)) for b in db.fetchall(
        "SELECT * FROM sb_bets WHERE status != 'open' ORDER BY id DESC LIMIT 20")]
    return {"open": open_bets, "settled": settled}


def place(game_id, market: str, side: str, stake) -> dict:
    ensure_schema()
    try:
        stake = int(round(float(stake)))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Invalid stake."}
    if stake <= 0:
        return {"ok": False, "error": "Stake must be positive."}
    if market not in ("ml", "total") or side not in ("home", "away", "over", "under"):
        return {"ok": False, "error": "Unknown market."}
    if stake > wallet.balance():
        return {"ok": False, "error": "Stake exceeds your wallet."}
    g = db.fetchone("SELECT id, game_date, played FROM games WHERE id = ?", (int(game_id),))
    if not g:
        return {"ok": False, "error": "No such game."}
    if g["played"]:
        return {"ok": False, "error": "That game has already started."}

    stats = _team_stats()
    lg = _league_rpg(stats)
    gg = db.fetchone("SELECT home_team_id, away_team_id FROM games WHERE id = ?", (int(game_id),))
    line = _line(int(game_id), gg["home_team_id"], gg["away_team_id"], stats, lg)
    if market == "ml":
        odds = line["ml_home"] if side == "home" else line["ml_away"]
        lval = None
        if side not in ("home", "away"):
            return {"ok": False, "error": "Pick home or away."}
    else:
        if side not in ("over", "under"):
            return {"ok": False, "error": "Pick over or under."}
        odds = TOTAL_ODDS
        lval = line["total"]

    if not wallet.debit(stake):
        return {"ok": False, "error": "Stake exceeds your wallet."}
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO sb_bets (game_id, market, side, line, odds, stake, status, created_at) "
        "VALUES (?,?,?,?,?,?, 'open', ?)",
        (int(game_id), market, side, lval, int(odds), stake,
         _dt.datetime.utcnow().isoformat(timespec="seconds")))
    conn.commit()
    return {"ok": True, "bankroll": round(wallet.balance())}
