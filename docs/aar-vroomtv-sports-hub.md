# AAR: Unassociated Press Sports Hub (vroomtv)

**Date:** 2026-06-12  
**Scope:** New standalone read-only sports aggregator hub, built as a 4th repo (`quarterback/vroomtv`), staged here in `hybrid-baseball` for handoff

---

## What was built

A Flask web app — "The Unassociated Press Sports Wire" — that reads live data from all three existing sports sims (hybrid-baseball, viperball, tennis-team-manager) and presents unified scores, standings, stat leaders, and game detail pages in a 1940s broadsheet aesthetic.

Files live in `vroomtv/` in this branch and need to be moved to `quarterback/vroomtv`.

## Why

User wanted an ESPN-style aggregator across all three sims. Feasibility was assessed first (direct DB reads = no coupling to sim web servers, no schema changes to any sim), then built. The push path from the remote execution environment to `vroomtv` hit a session permissions wall (MCP tools scoped to the original three repos only), so the code was staged here for handoff.

## Architecture decisions

- **Direct SQLite reads** — hub opens each sim's `.db` file read-only (`?mode=ro`). No HTTP calls, no dependency on sim servers being up.
- **Three adapters** (`adapters/baseball.py`, `adapters/viperball.py`, `adapters/tennis.py`) — each knows its own schema and returns plain dicts. App layer never touches SQL directly.
- **Viperball JSON blobs** — viperball stores everything as JSON in `saves.data`. The adapter parses the `pro_league` blob as a plain dict (structure is stable: `standings`, `results`, `player_season_stats`, `current_week`). No viperball Python classes imported.
- **Multi-league throughout** — viperball iterates all `pro_league` saves by `save_key`; tennis iterates all `gtt_leagues` and `seasons` rows. Adding a new viperball league or tennis season requires zero code changes.
- **Graceful degradation** — any unconfigured or missing DB shows a placeholder, not an error page.
- **1940s wire-service UI** — broadsheet fonts (Playfair Display, IM Fell English, Special Elite), ink-on-newsprint palette, three-column wire grid on the scores page. Pure server-rendered HTML, no JS.

## What was NOT done

- No live testing against real DB files (no DBs present in this environment with data)
- No deployment config (Dockerfile, fly.toml, Procfile) — add when ready to host
- Tennis adapter assumes GTT `gtt_duals.winner` column holds a franchise `id` matching `gtt_franchises.id` — verify this is correct before first run; if `winner` is 0/1 (home/away flag) the standings logic needs adjustment
- Viperball `standings` blob structure assumes `streak` and `streak_type` are separate keys — verify against a live save

## Handoff steps for next agent

1. Move `vroomtv/` contents to root of `quarterback/vroomtv` repo
2. Delete `vroomtv/` from this branch in `hybrid-baseball`
3. Point env vars at real DB files and run `python manage.py runserver`
4. Verify scores page shows data for each configured sport
5. Click through to a game detail page for each sport
6. Check that a second viperball league (if you have one) appears as a separate section
