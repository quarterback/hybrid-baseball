"""
O27v2 management CLI.

Usage:
    python o27v2/manage.py runserver [--config CONFIG_ID]
    python o27v2/manage.py initdb   [--config CONFIG_ID]
    python o27v2/manage.py resetdb  [--config CONFIG_ID]
    python o27v2/manage.py sim [N]
    python o27v2/manage.py smoke
    python o27v2/manage.py configs              — list available league configs

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


def cmd_runserver(config_id: str = "30teams"):
    from o27v2.web.app import app
    db.init_db()
    seed_league(config_id=config_id)
    seed_schedule(config_id=config_id)

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
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
