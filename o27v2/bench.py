"""
Benchmark a tuning: sim a short throwaway season in an isolated temp database
and report the run-environment stats it produces. Run as a subprocess by the
web layer so it can never touch the user's real league DB or mutate the live
config module in the server process.

Usage:
    python -m o27v2.bench --games 40 --config 8teams --overrides '<json>'

Prints a single JSON object to stdout:
    {"ok": true, "games": N, "r_per_game": .., "hr_per_game": .., "avg": ..,
     "k_pct": .., "bb_pct": .., "label": "Deadball · pitcher-dominant"}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--config", default="8teams")
    ap.add_argument("--overrides", default="{}")
    ap.add_argument("--seed", type=int, default=20260524)
    args = ap.parse_args()

    workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if workspace not in sys.path:
        sys.path.insert(0, workspace)

    try:
        overrides = json.loads(args.overrides) or {}
    except Exception:
        overrides = {}

    # Isolate: point the DB module at a fresh temp file BEFORE anything opens
    # a connection, so this never reads or writes the real league database.
    from o27v2 import db
    tmp = tempfile.NamedTemporaryFile(prefix="o27bench_", suffix=".db",
                                      delete=False)
    tmp.close()
    db._DB_PATH = tmp.name
    db._DB_PATH_OVERRIDDEN = True

    try:
        from o27v2 import engine_config as ec
        from o27v2.league import seed_league
        from o27v2.schedule import seed_schedule
        from o27v2.sim import simulate_next_n

        db.init_db()
        # Apply the tuning BEFORE seeding so generation shifts (GEN_SHIFT_*)
        # reshape this benchmark league's new players, and pin it so the
        # per-game ensure_applied() hook doesn't reset it from the empty
        # temp-DB overrides.
        ec.apply_values(overrides)
        ec._applied = True

        seed_league(rng_seed=args.seed, config_id=args.config)
        seed_schedule(config_id=args.config, rng_seed=args.seed)

        simulate_next_n(max(1, args.games))

        row = db.fetchone(
            "SELECT COUNT(DISTINCT game_id) g, "
            "       SUM(runs) r, SUM(hr) hr, SUM(hits) h, "
            "       SUM(ab) ab, SUM(pa) pa, SUM(bb) bb, SUM(k) k "
            "FROM game_batter_stats"
        ) or {}
        games = row.get("g") or 0
        team_games = max(1, games * 2)
        ab = row.get("ab") or 0
        pa = row.get("pa") or 0
        stats = {
            "ok": True,
            "games": games,
            "config": args.config,
            "r_per_game":  round((row.get("r")  or 0) / team_games, 2),
            "hr_per_game": round((row.get("hr") or 0) / team_games, 2),
            "avg":   round((row.get("h") or 0) / ab, 3) if ab else 0.0,
            "k_pct":  round(100.0 * (row.get("k")  or 0) / pa, 1) if pa else 0.0,
            "bb_pct": round(100.0 * (row.get("bb") or 0) / pa, 1) if pa else 0.0,
        }
        stats["label"] = ec.characterize(stats)
        print(json.dumps(stats))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    finally:
        try:
            os.unlink(tmp.name)
            for ext in ("-wal", "-shm"):
                p = tmp.name + ext
                if os.path.exists(p):
                    os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
