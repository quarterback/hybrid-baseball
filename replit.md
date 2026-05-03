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
- `o27/web/app.py` — Flask operational GUI; registers stats Blueprint. Local dev: port 5000 (set by workflow or $PORT env var). Fly.io: port 8080 (set via fly.toml `[env] PORT = "8080"`).
- `o27/stats_site/` — read-only stats-browsing Blueprint (mounted at `/stats`)
  - `blueprint.py` — routes: /stats, /stats/standings, /stats/schedule, /stats/leaders, /stats/players, /stats/team/<abbrev>, /stats/player/<id>, /stats/game/<id>
  - `templates/stats_site/` — base.html (14 themes, O27 wordmark, keyboard shortcuts), home/standings/schedule/game/team/player/players/leaders pages
  - Theme stored in localStorage key `o27-theme`; 't' keyboard shortcut toggles theme panel; Bloomberg dark default
- `o27/tests/test_rules.py` — 102 rule-verification tests
- `o27/tune.py` — 500-game batch tuner
- `o27/config.py` — all tunable parameters
- `fly.toml` + `Dockerfile` — Fly.io deployment (app=o27, region=iad, port=8080, CMD python -m o27.web.app)

### O27 Web route changes (Task 30)
- Old `/stats` (batting/pitching leaders) is now at `/leaders` (also `/stats-leaders` alias still works)
- `/stats` now mounts the full stats-browsing Blueprint (Viperball-inspired design)

### o27v2/ — Phase 6–9 fork: 30-team league + O27 rules engine (port 8080)
- `o27v2/manage.py` — CLI: `runserver`, `initdb`, `resetdb`, `sim [N]`, `smoke`, `tune [N]`
- `o27v2/db.py` — SQLite persistence layer (o27v2/o27v2.db)
- `o27v2/league.py` — 30 teams (6 divisions × 5), player generation with age (Phase 9)
- `o27v2/schedule.py` — season schedule generation (~2430 games for 30 teams)
- `o27v2/sim.py` — game simulation: DB teams → O27 engine → store results
- `o27v2/injuries.py` — Phase 9: injury draw, IL tiers, depth chart promotion, player returns
- `o27v2/trades.py` — Phase 9: trade value model, deadline engine, waiver claims
- `o27v2/transactions.py` — Phase 9: transaction log helpers (injury, return, trade, waiver)
- `o27v2/smoke_test.py` — 10-seed smoke test (no DB required)
- `o27v2/web/app.py` — Flask routes (Baseball-Reference-style IA): /, /standings, /schedule, /players, /player/<id>, /teams, /team/<id>, /leaders (replaces /stats; /stats 302→/leaders), /game/<id>, /transactions
- `o27v2/web/templates/` — Bootstrap 5 HTML templates (dark theme); dense b-ref-style tables (`.dense`, `.dense-table`, `.num` monospace numerics, `.sortable` vanilla-JS click-sort headers in `base.html`). Scores dashboard surfaces today's line scores + last-played finals + division leaders + top-5 leaders (no stat cards). Standings is one wide sortable table per league with inline division dividers and Last5 pill column. Players is a server-paginated 50/page index filterable by team/position/kind/name. Leaders is top-25 per stat in dense card-per-stat layout.

### Key O27v2 Commands
- `python o27v2/manage.py runserver` — start web app (default port from $PORT)
- `python o27v2/manage.py initdb` — create DB + seed teams + schedule
- `python o27v2/manage.py sim [N]` — simulate next N games
- `python o27v2/manage.py tune [N] [--config ID]` — run tuning simulation, verify Phase 9 targets
- `python o27v2/smoke_test.py` — run 10-seed smoke test

### Phase 9: Injury model & trade engine

**Player age model**: Every player has an `age` column (22–38, bell curve peaking at 27–30).

**Injury system** (`injuries.py`):
- Base rate: 1.5%/game for all position players (spec-mandated floor)
- Position bonuses: catcher +0.5% → ~2.0%; workhorse pitcher +0.8% → ~2.3%
- Age modifiers: +0.2%/yr over 32, +0.4%/yr over 35
- Three IL tiers: DTD (1-3 games, P=0.50), Short-IL (10-25 games, P=0.35), Long-IL (60-100 games, P=0.15)
- Minimum active roster floor: 7 non-joker players (max 2 simultaneous injuries per team)
- Player returns automatically when `injured_until` date passes game_date
- Tuning result: ~10.5 IL stints/team/162g-equiv ✓ (target 8–15 counts IL stints only; DTD is additional)

**Trade engine** (`trades.py`):
- Trade deadline at 2/3 of season calendar (day 108 for 162-day season)
- Contenders = top 30% of teams by win pct; sellers = bottom 30%
- 1-for-1 or 2-for-2 trades; skill/age/role-weighted trade value scoring
- Tuning result: 3-8 deadline trades per league (verified ✓ with 30 teams)
- In-season trades: 0-2 minor trades fire randomly before the deadline (target ✓)
- Waiver claims: triggered when a team's bullpen drops below 2 healthy committee pitchers

**Transaction log** (`transactions.py`):
- All roster moves (injury, return, trade, waiver) appended to `transactions` table
- Browsable at `/transactions` in web app, filterable by team and event type

### DB Schema (o27v2.db)
Tables: teams, players (with age, injured_until, il_tier), games, game_batter_stats, game_pitcher_stats, team_phase_outs, transactions

### Quality gates
- **Stat invariants** (`tests/test_stat_invariants.py`, run via `make test-invariants`): nine assertions that catch every mathematically-impossible-stat bug we've shipped before.
  - Phase-outs cap (≤27 reg, ≤5 SI), OR reconciliation per phase, pitcher↔batter cross-check per game, OS% upper bound, W ≤ G per pitcher, PA == AB+BB (collapsed because HBP/SF/SH aren't persisted — see follow-up Task #61), batter+pitcher row uniqueness per (player, game, phase), and league FIP within 0.05 of league ERA.
  - DB target: `o27v2/o27v2.db` by default; override with `O27V2_DB_PATH=...`.
  - Subset target: `O27V2_INVARIANTS_GAMES=1391,1362 make test-invariants` scopes every assertion to those games — useful for verifying after a partial re-sim without re-simming the legacy backlog.
  - Run after every full-season simulation; failures should block releases.

### Architecture notes
- o27v2 imports the O27 engine directly from `o27/engine/` (same workspace)
- Active roster filtering in `o27v2/injuries.py:get_active_players` returns the `is_active=1` healthy roster and ephemerally tops up from the reserve pool (`is_active=0`) when pitchers/fielders fall below the minimums — DB flags are never flipped.
- **Task #65 (rosters + live pitcher roles):**
  - Each team carries 47 players: 34 active (12 fielders [8 starting + 4 bench] + 3 DH + 19 pitchers) + 13 reserve (8 hitters + 5 pitchers). One-for-one IL replacement: any active-roster injury triggers an ephemeral promotion of the best matching reserve so the active counts (19P / 15 position) stay whole on every game day.
  - Every attribute on every player is rolled INDEPENDENTLY against a 9-tier talent ladder (`_TALENT_TIERS` in `o27v2/league.py`), so genuine elite stars (Stuff 75-80) coexist with replacement-level depth.
  - Pitchers have separate `pitcher_skill` (Stuff) and `stamina` columns, both tier-rolled independently.
  - **No `pitcher_role` is stored or read.** `o27v2/sim.py:_db_team_to_engine` picks today's SP as the highest-Stamina active arm that did NOT pitch in the last 4 sim days (`_recently_used_pitcher_ids`). `o27/engine/manager.py:pick_new_pitcher` derives each reliever's role at appearance time from current Stuff + Stamina (outs ≥19 → max Stuff / 10-18 → mid / <10 → max Stamina). Aging or attribute drift naturally re-casts roles without any persisted re-tagging.

## Key Node Commands

- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- `pnpm --filter @workspace/api-server run dev` — run API server locally

See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details.
