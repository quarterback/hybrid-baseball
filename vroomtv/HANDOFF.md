# Handoff Note — Unassociated Press Sports Hub

This directory contains the full vroomtv app (the cross-sport ESPN-style aggregator). It was built in the `hybrid-baseball` session and needs to be moved to `quarterback/vroomtv`.

## What to do

1. Copy everything in `vroomtv/` to the root of `quarterback/vroomtv`
2. Delete this `vroomtv/` folder from `hybrid-baseball`
3. Run `pip install flask` and `python manage.py runserver` to verify it starts

## How the app works

Flask app reading three SQLite DBs via env vars — no writes to any sim.

| Env var | Points at |
|---|---|
| `BASEBALL_DB` | `o27v2/o27v2.db` in hybrid-baseball |
| `VIPERBALL_DB` | `data/viperball.db` in viperball |
| `TENNIS_DB` | `tennis.db` in tennis-team-manager |

Routes: `/` scores, `/standings`, `/leaders`, `/game/baseball/<id>`, `/game/viperball/<save_key>/<week>/<matchup_key>`, `/game/tennis/<gtt|ncaa>/<id>`

All three DB paths are optional — missing/unconfigured sports show a placeholder rather than erroring.

## Multi-league support

- **Viperball**: iterates all `pro_league` rows in `saves` table by `save_key`
- **Tennis GTT**: iterates all `gtt_leagues` rows, scopes by `league_id`
- **Tennis NCAA**: iterates all `seasons` rows, scopes by `season_id`
- **Baseball**: one DB, scoped to current season via `MAX(season)`

## AAR

See `docs/aar-vroomtv-sports-hub.md` in this repo for full after-action report.
