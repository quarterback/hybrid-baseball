# Workspace

## Overview

pnpm workspace monorepo using TypeScript + Python O27 baseball simulation.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database**: PostgreSQL + Drizzle ORM
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)

## O27 Baseball Simulation

### o27/ — Original O27 simulator (Flask web app, reference implementation)
- `o27/engine/` — game engine: game.py, pa.py, state.py, stay.py, manager.py, prob.py
- `o27/render/` — Jinja2 play-by-play renderer
- `o27/stats/` — batter/pitcher/team stat accumulators
- `o27/web/app.py` — Flask single-game viewer (port 5000)
- `o27/tests/test_rules.py` — 102 rule-verification tests
- `o27/tune.py` — 500-game batch tuner
- `o27/config.py` — all tunable parameters

### o27v2/ — Phase 6 fork: 30-team league + O27 rules engine (port 8080)
- `o27v2/manage.py` — CLI: `runserver`, `initdb`, `resetdb`, `sim [N]`, `smoke`
- `o27v2/db.py` — SQLite persistence layer (o27v2/o27v2.db)
- `o27v2/league.py` — 30 teams (6 divisions × 5), player generation
- `o27v2/schedule.py` — season schedule generation (~446 games)
- `o27v2/sim.py` — game simulation: DB teams → O27 engine → store results
- `o27v2/smoke_test.py` — 10-seed smoke test (no DB required)
- `o27v2/web/app.py` — Flask routes: /, /standings, /schedule, /game/<id>, /teams
- `o27v2/web/templates/` — Bootstrap 5 HTML templates (dark theme)

### Key O27v2 Commands
- `python o27v2/manage.py runserver` — start web app (default port from $PORT)
- `python o27v2/manage.py initdb` — create DB + seed 30 teams + schedule
- `python o27v2/manage.py sim [N]` — simulate next N games
- `python o27v2/smoke_test.py` — run 10-seed smoke test

### Architecture notes
- o27v2 imports the O27 engine directly from `o27/engine/` (same workspace)
- 9-inning game loop from Baseball-Simulation fork replaced with O27's 1-inning/27-out structure
- Stay mechanic, joker handling, and super-inning tiebreaker all ported from o27/engine/
- Batter stats tracked via o27/render/render.py Renderer; pitcher stats via spell_log

## Key Node Commands

- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- `pnpm --filter @workspace/api-server run dev` — run API server locally

See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details.
