"""
O27v2 management CLI.

Usage:
    python o27v2/manage.py runserver        — start the Flask web app
    python o27v2/manage.py initdb           — create DB schema + seed league
    python o27v2/manage.py resetdb          — drop all tables + re-seed
    python o27v2/manage.py sim [N]          — simulate N next games (default 10)
    python o27v2/manage.py smoke            — run 10-seed smoke test
"""
from __future__ import annotations
import sys
import os

_workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _workspace not in sys.path:
    sys.path.insert(0, _workspace)

from o27v2 import db
from o27v2.league import seed_league
from o27v2.schedule import seed_schedule


def cmd_initdb():
    print("Initialising database…")
    db.init_db()
    print("  Tables created.")
    seed_league()
    print("  30 teams + players seeded.")
    n = seed_schedule()
    if n:
        print(f"  {n} games scheduled.")
    else:
        print("  Schedule already exists.")
    print("Done.")


def cmd_resetdb():
    print("Dropping all tables…")
    db.drop_all()
    db.init_db()
    print("  Tables recreated.")
    seed_league()
    print("  30 teams + players seeded.")
    n = seed_schedule()
    print(f"  {n} games scheduled.")
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


def cmd_runserver():
    from o27v2.web.app import app
    # Ensure DB is initialised before starting
    db.init_db()
    seed_league()
    seed_schedule()

    port = int(os.environ.get("PORT", 5001))
    print(f"Starting O27v2 web app on port {port}…")
    print(f"  Dashboard:  http://localhost:{port}/")
    print(f"  Standings:  http://localhost:{port}/standings")
    print(f"  Teams:      http://localhost:{port}/teams")
    app.run(host="0.0.0.0", port=port, debug=False)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "runserver":
        cmd_runserver()
    elif args[0] == "initdb":
        cmd_initdb()
    elif args[0] == "resetdb":
        cmd_resetdb()
    elif args[0] == "sim":
        n = int(args[1]) if len(args) > 1 else 10
        cmd_sim(n)
    elif args[0] == "smoke":
        cmd_smoke()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
