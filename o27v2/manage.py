"""
O27v2 management CLI.

Usage:
    python o27v2/manage.py runserver [--config CONFIG_ID]
    python o27v2/manage.py initdb   [--config CONFIG_ID]
    python o27v2/manage.py resetdb  [--config CONFIG_ID]
    python o27v2/manage.py sim [N]
    python o27v2/manage.py backfill_arc          — replay played games via stored seeds to populate arc-bucketed pitcher stats
    python o27v2/manage.py backfill_salaries     — recompute every player's salary in guilders from current attributes
    python o27v2/manage.py smoke
    python o27v2/manage.py configs              — list available league configs
    python o27v2/manage.py tune [SEASON_GAMES]  — sim a full season, verify Phase 9 targets

CONFIG_ID defaults to '30teams'.  Valid values: 8teams 12teams 16teams 24teams 30teams 36teams
"""
from __future__ import annotations
import sys
import os

_workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _workspace not in sys.path:
    sys.path.insert(0, _workspace)

from o27v2 import db
from o27v2.league import seed_league, get_league_configs
from o27v2.schedule import seed_schedule


def _parse_config_flag(args: list[str]) -> tuple[str, list[str]]:
    """Extract --config VALUE from args; return (config_id, remaining_args)."""
    config_id = "30teams"
    remaining = []
    i = 0
    while i < len(args):
        if args[i] == "--config" and i + 1 < len(args):
            config_id = args[i + 1]
            i += 2
        else:
            remaining.append(args[i])
            i += 1
    return config_id, remaining


def cmd_initdb(config_id: str = "30teams"):
    print(f"Initialising database (config: {config_id})…")
    db.init_db()
    print("  Tables created.")
    seed_league(config_id=config_id)
    cfg = get_league_configs()[config_id]
    print(f"  {cfg['team_count']} teams + players seeded.")
    n = seed_schedule(config_id=config_id)
    if n:
        print(f"  {n} games scheduled ({cfg['games_per_team']} per team).")
    else:
        print("  Schedule already exists.")
    print("Done.")


def cmd_resetdb(config_id: str = "30teams"):
    cfg = get_league_configs()[config_id]
    print(f"Resetting database (config: {config_id}, {cfg['team_count']} teams)…")
    db.drop_all()
    db.init_db()
    print("  Tables recreated.")
    seed_league(config_id=config_id)
    print(f"  {cfg['team_count']} teams + players seeded.")
    n = seed_schedule(config_id=config_id)
    print(f"  {n} games scheduled ({cfg['games_per_team']} per team).")
    print("Done.")


def cmd_sim(n: int = 10):
    from o27v2.sim import simulate_next_n
    print(f"Simulating {n} game(s)…")
    results = simulate_next_n(n)
    for r in results:
        if "error" in r:
            print(f"  [ERR] game {r['game_id']}: {r['error']}")
        else:
            si = " (SI)" if r.get("super_inning") else ""
            print(f"  Game {r['game_id']:4d}: {r['away_team'][:12]:12s} "
                  f"{r['away_score']} – {r['home_score']} {r['home_team'][:12]:12s}{si}")
    print(f"  {len([r for r in results if 'error' not in r])} game(s) simulated.")


def cmd_backfill_arc():
    """Replay every played game with its persisted seed so the new
    arc-bucketed counters (er_arc1/2/3, k_arc1/2/3, fo_arc1/2/3,
    bf_arc1/2/3, is_starter) get populated for the historical season.

    Engine output is fully seed-deterministic given roster state, and
    the games table persists each game's seed (db.py:102, stamped at
    sim.py:884), so we can reset played=0, wipe per-game stats, and
    re-run simulate_next_n which will pull the stored seed back via
    simulate_game's read of `games.seed`.

    NOTE: Trades / injuries / waivers fire deterministically too, but
    only off the post-game state; resetting per-game stats and clearing
    the transactions log between replays keeps the reproducibility
    contract intact.
    """
    from o27v2 import db, sim
    import time

    n_played = db.fetchone("SELECT COUNT(*) AS n FROM games WHERE played=1")
    n = (n_played or {}).get("n") or 0
    if n == 0:
        print("No played games to backfill — nothing to do.")
        return

    print(f"Backfilling {n} played games (replay via stored seeds)…")
    print("  Wiping per-game stats and resetting played flags…")
    db.execute("DELETE FROM game_pitcher_stats")
    db.execute("DELETE FROM game_batter_stats")
    db.execute("DELETE FROM team_phase_outs")
    db.execute("DELETE FROM transactions")
    db.execute(
        "UPDATE games SET played=0, home_score=NULL, away_score=NULL, "
        "winner_id=NULL, super_inning=0 WHERE played=1"
    )
    # Restore active rosters for any IL'd players so the replay
    # starts from the same baseline state seed_league() produced.
    db.execute("UPDATE players SET injured_until=NULL, il_tier=NULL")

    # Re-set the sim clock to the league's start date so simulate_next_n
    # walks games in chronological order from day 1.
    first_game = db.fetchone("SELECT MIN(game_date) AS d FROM games")
    if first_game and first_game.get("d"):
        from o27v2.sim import set_sim_date
        set_sim_date(first_game["d"])

    print("  Re-running games with persisted seeds…")
    t0 = time.time()
    completed = 0
    while True:
        results = sim.simulate_next_n(50)
        if not results:
            break
        completed += len(results)
        if completed % 200 == 0 or len(results) < 50:
            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            print(f"    {completed}/{n} games  ({rate:.1f} games/s)")
    elapsed = time.time() - t0
    print(f"  Done: {completed} games in {elapsed:.1f}s "
          f"({completed/elapsed:.1f} games/s).")
    # Spot-check arc-coverage on what we just stamped.
    cov = db.fetchone(
        """SELECT COUNT(*) AS rows,
                  SUM(CASE WHEN bf_arc1+bf_arc2+bf_arc3 > 0 THEN 1 ELSE 0 END) AS arc_rows
           FROM game_pitcher_stats"""
    ) or {}
    print(f"  Arc coverage: {cov.get('arc_rows', 0)}/{cov.get('rows', 0)} "
          f"pitcher rows have arc data populated.")


def cmd_smoke():
    from o27v2.smoke_test import run_smoke_tests
    ok = run_smoke_tests()
    sys.exit(0 if ok else 1)


def cmd_backfill_salaries():
    """Recompute every player's `salary` field from current attributes
    via valuation.estimate_player_value. Idempotent — safe to re-run.
    Use after migrating an existing live DB (where the new salary
    column starts at 0) without reseeding the league."""
    from o27v2 import db
    from o27v2.valuation import estimate_player_value

    team_league = {
        row["id"]: row["league"]
        for row in db.fetchall("SELECT id, league FROM teams")
    }
    rows = db.fetchall("SELECT * FROM players")
    if not rows:
        print("No players in DB — nothing to backfill.")
        return

    updates: list[tuple[int, int]] = []
    for p in rows:
        league_name = team_league.get(p["team_id"]) if p["team_id"] is not None else None
        # Recompute by zeroing the persisted field — `estimate_player_value`
        # short-circuits on a non-zero salary, but we want fresh math here.
        d = dict(p)
        d["salary"] = 0
        salary = estimate_player_value(d, league_name=league_name)
        updates.append((salary, p["id"]))

    with db.get_conn() as conn:
        conn.executemany("UPDATE players SET salary = ? WHERE id = ?", updates)
        conn.commit()

    print(f"Backfilled salaries on {len(updates)} player rows.")


def cmd_configs():
    configs = get_league_configs()
    print(f"{'ID':<12} {'Label':<30} {'Teams':>5} {'GPT':>5} {'Level':<5}")
    print("-" * 60)
    for cid, cfg in configs.items():
        print(f"{cid:<12} {cfg['label']:<30} {cfg['team_count']:>5} "
              f"{cfg['games_per_team']:>5} {cfg.get('level','MLB'):<5}")


def cmd_tune(n_games: int | None = None, config_id: str = "30teams"):
    """
    Simulate a full season (or N games) and print Phase 9 target verification.
    Resets the DB first so results are clean.
    """
    from o27v2.sim import simulate_next_n
    from o27v2 import db as _db

    cfg = get_league_configs()[config_id]
    gpt = cfg["games_per_team"]
    n_teams = cfg["team_count"]
    total_games = n_teams * gpt // 2

    if n_games is None:
        n_games = total_games

    print(f"Tuning run: resetting DB (config: {config_id})…")
    _db.drop_all()
    _db.init_db()
    from o27v2.league import seed_league
    from o27v2.schedule import seed_schedule
    seed_league(config_id=config_id)
    seed_schedule(config_id=config_id)
    print(f"  {n_teams} teams, {total_games} total games scheduled. Simulating {n_games}…")

    batch_size = 50
    simmed = 0
    while simmed < n_games:
        batch = min(batch_size, n_games - simmed)
        results = simulate_next_n(batch)
        if not results:
            break
        simmed += len(results)
        print(f"  {simmed}/{n_games} games…", end="\r")

    print(f"\n  {simmed} games simulated.")

    # --- Injury stats ---
    inj = _db.fetchone(
        "SELECT COUNT(*) as n FROM transactions WHERE event_type = 'injury'"
    )
    il_only = _db.fetchone(
        "SELECT COUNT(*) as n FROM transactions "
        "WHERE event_type = 'injury' AND (detail LIKE '%Short-Term%' OR detail LIKE '%Long-Term%')"
    )
    promo = _db.fetchone(
        "SELECT COUNT(*) as n FROM transactions WHERE event_type = 'promotion'"
    )
    pen = _db.fetchone(
        "SELECT COUNT(*) as n FROM transactions WHERE event_type = 'penalty'"
    )
    ret = _db.fetchone(
        "SELECT COUNT(*) as n FROM transactions WHERE event_type = 'return'"
    )
    trades_dl = _db.fetchone(
        "SELECT COUNT(*) as n FROM transactions WHERE event_type = 'deadline_trade'"
    )
    trades_is = _db.fetchone(
        "SELECT COUNT(*) as n FROM transactions WHERE event_type = 'inseason_trade'"
    )
    waivers = _db.fetchone(
        "SELECT COUNT(*) as n FROM transactions WHERE event_type = 'waiver'"
    )

    total_inj = inj["n"] if inj else 0
    il_inj    = il_only["n"] if il_only else 0
    dtd_inj   = total_inj - il_inj

    # Scale to 162-game equivalent using actual games played per team
    played_row = _db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")
    n_played   = played_row["n"] if played_row else simmed
    avg_gpt    = n_played * 2.0 / max(n_teams, 1)
    scale      = 162.0 / max(avg_gpt, 1)
    il_per_team_162  = (il_inj / max(n_teams, 1)) * scale
    all_per_team_162 = (total_inj / max(n_teams, 1)) * scale

    total_dl_trades = trades_dl["n"] if trades_dl else 0
    actual_dl_trades = total_dl_trades // 2  # 2 log entries per trade (one per team)

    print()
    print("Phase 9 tuning results")
    print("=" * 64)
    print(f"  Config:                      {config_id}  ({gpt} games/team)")
    print(f"  Total injuries logged:       {total_inj:5d}  (DTD: {dtd_inj}  IL: {il_inj})")
    print(f"  Promotions logged:           {(promo['n'] if promo else 0):5d}")
    print(f"  Penalties logged:            {(pen['n'] if pen else 0):5d}")
    print(f"  Returns logged:              {(ret['n'] if ret else 0):5d}")
    il_ok  = "✓" if 8 <= il_per_team_162 <= 15 else "!"
    all_ok = ""
    print(f"  IL stints/team (162g eq):    {il_per_team_162:5.1f}  {il_ok} (target 8–15; IL stints only)")
    print(f"  All injuries/team (162g eq): {all_per_team_162:5.1f}    (includes DTD)")

    dl_ok = "✓" if 3 <= actual_dl_trades <= 8 else "!"
    is_n  = (trades_is["n"] if trades_is else 0) // 2
    is_ok = "✓" if 0 <= is_n <= 2 else "!"
    print(f"  Deadline trades:             {actual_dl_trades:5d}  {dl_ok} (target 3–8 per league)")
    print(f"  In-season trades:            {is_n:5d}  {is_ok} (target 0–2 per league)")
    print(f"  Waiver claims:               {(waivers['n'] if waivers else 0):5d}")
    print()


def cmd_runserver(config_id: str = "30teams"):
    from o27v2.web.app import app
    db.init_db()
    existing = db.fetchone("SELECT COUNT(*) as n FROM teams")
    if not existing or existing["n"] == 0:
        seed_league(config_id=config_id)
        seed_schedule(config_id=config_id)
    else:
        print(f"Existing league found ({existing['n']} teams) — skipping seed.")

    port = int(os.environ.get("PORT", 5001))
    print(f"Starting O27v2 web app on port {port}…")
    print(f"  Dashboard:  http://localhost:{port}/")
    print(f"  Standings:  http://localhost:{port}/standings")
    print(f"  Teams:      http://localhost:{port}/teams")
    app.run(host="0.0.0.0", port=port, debug=False)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "runserver":
        config_id, _ = _parse_config_flag(args[1:] if args else [])
        cmd_runserver(config_id)
    elif args[0] == "initdb":
        config_id, _ = _parse_config_flag(args[1:])
        cmd_initdb(config_id)
    elif args[0] == "resetdb":
        config_id, _ = _parse_config_flag(args[1:])
        cmd_resetdb(config_id)
    elif args[0] == "sim":
        _, rest = _parse_config_flag(args[1:])
        n = int(rest[0]) if rest else 10
        cmd_sim(n)
    elif args[0] == "smoke":
        cmd_smoke()
    elif args[0] == "backfill_arc":
        cmd_backfill_arc()
    elif args[0] == "backfill_salaries":
        cmd_backfill_salaries()
    elif args[0] == "configs":
        cmd_configs()
    elif args[0] == "tune":
        config_id, rest = _parse_config_flag(args[1:])
        n_games = int(rest[0]) if rest else None
        cmd_tune(n_games, config_id)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
