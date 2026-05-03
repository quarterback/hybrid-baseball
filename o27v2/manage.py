"""
O27v2 management CLI.

Usage:
    python o27v2/manage.py runserver [--config CONFIG_ID]
    python o27v2/manage.py initdb   [--config CONFIG_ID]
    python o27v2/manage.py resetdb  [--config CONFIG_ID]
    python o27v2/manage.py sim [N]
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


def cmd_smoke():
    from o27v2.smoke_test import run_smoke_tests
    ok = run_smoke_tests()
    sys.exit(0 if ok else 1)


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
