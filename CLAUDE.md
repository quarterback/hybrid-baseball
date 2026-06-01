# CLAUDE.md — orientation for agents working in this repo

O27 is a baseball-variant simulator (one continuous 27-out half per side).
Read `README.md` for the design; this file is about **where code lives** so you
don't build into the wrong place.

## ⚠️ The single most important rule: `o27v2/` is the live app

There are two app trees. **`o27v2/` is the one that runs.** Both the
`Dockerfile` (`CMD ["python", "o27v2/manage.py", "runserver"]`) and `.replit`
serve `o27v2`.

| Path | Status | Put things here? |
| --- | --- | --- |
| `o27v2/` | **LIVE** — DB-backed app, sim orchestration, web UI, leagues, seasons | ✅ Yes — features, routes, templates, stats, schema |
| `o27v2/web/` | **LIVE** web app (Flask): `app.py`, templates, `box_text.py`, etc. | ✅ All web routes / UI / box-score work |
| `o27/web/` | **DEAD** legacy Flask app — never served, never imported by `o27v2` | ❌ **Never.** A `PreToolUse` hook blocks edits here |
| `o27/engine/`, `o27/render/`, `o27/stats/`, `o27/almanac/`, `o27/gazette/`, `o27/config.py` | **SHARED CORE** — imported heavily by `o27v2` (the sim engine, renderers, stat math) | ✅ Yes — this is where engine/sim/render/stats logic lives |

So `o27/` is **not** dead — only `o27/web/` is. The engine and renderers under
`o27/` are the real, shared core. If you're adding a **web page / route /
template / box-score feature**, it goes in **`o27v2/web/`**. If you find a
feature wired only into `o27/web/`, it is stranded and needs porting to
`o27v2/web/` — that exact mistake already happened once with the visual
scorecard (`o27/render/svg_scorecard.py` was only reachable from `o27/web/`;
see `docs/aar-scorecard-audit-and-joker-cooldown.md`).

## Running & testing

- **Run the app:** `python3 o27v2/manage.py runserver` (defaults to port 5000;
  `PORT=8080` to override). `flask` may be absent in some sandboxes.
- **Init/seed a DB:** `python3 o27v2/manage.py initdb` then `... sim N`. The DB
  path is `o27v2/o27v2.db` or `$O27V2_DB_PATH`.
- **Engine tests:** `pytest o27/tests o27v2/tests`. Some `o27v2/tests` modules
  import `flask`/need a DB and will error in a bare sandbox — that's
  environmental, not your change. The o27/engine suites run clean.
- **Single game in the engine (no DB):** `python -m o27.main --seed N`.

## Conventions

- **After-Action Reports:** non-trivial work gets a `docs/aar-<slug>.md`
  (mirror an existing one for tone — be honest about validation and what you
  did *not* change). Project arc is tracked in `docs/project-trajectory.md`.
- **Stats:** the catalog and formulas live in `docs/stats-reference.md`; the
  invariant suite is `tests/test_stat_invariants.py`.
- **Engine determinism:** games are seed-deterministic given roster state. The
  `games` row stores its seed; ad-hoc single-game re-sims on a live DB are
  *not* reliably reproducible (post-game trades/injuries mutate rosters), so
  prefer reading persisted per-game data (`game_pbp`, `game_*_stats`,
  `game_pa_log`) over re-simming.
