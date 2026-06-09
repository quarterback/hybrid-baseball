# AAR — Franchise honors banner (division titles, wild cards, pennants, championships)

## Goal

On the team page, show a banner of franchise accolades across seasons — each
with the **years won** — once a save has simmed past season 1. Four honors,
using the game's canonical playoff names:

- **Division Title** — finished 1st in its (league, division).
- **Wild Card** — reached the playoff field as a non-division-winner.
- **League Champion** — won its league final (`series_kind = 'championship'`).
- **World Series Champion** (or **Champion** in a single-league universe) — the
  overall title (`world_series` winner, or the lone league's champion).

Design reference was a Viperball team page: a small filled pill under the team's
record line, all inside the hero banner.

## The constraint that shaped the design

`playoff_series` is **wiped at rollover** (`season_archive._reset_for_next_history_season`),
and `seasons` persists only the single overall `champion_abbrev` per season. So:

- Division titles and the overall champion are **reconstructable** from
  `season_standings` + `seasons` for already-archived seasons.
- Pennants and wild-card berths are **not** recoverable for past seasons — the
  bracket is gone.

Therefore the only way to track all four going forward is a small capture at
archive time, while the bracket still exists.

## What changed

**`o27v2/db.py`** — new `season_team_honors` table in `SCHEMA` (one row per team
per season that earned an honor; flags for the four accolades plus `year` /
`season_number` for the banner). Added to `SCHEMA`, so existing saves pick it up
on next boot via `executescript`.

**`o27v2/season_archive.py`**
- `_snapshot_team_honors(season_id, season_number, year)` — called from
  `archive_current_season` right after `_snapshot_standings`, before the bracket
  is wiped. Reads the live `teams` table (division winners by record) and the
  live `playoff_series` rows (playoff field, pennant winners) plus
  `playoffs.champion()` for the overall title. No season filter on
  `playoff_series` is needed — only the current bracket is ever present.
- `backfill_team_honors()` — reconstructs the two derivable honors (division
  titles, overall champion) for archived seasons with no honors rows. Pennants /
  wild cards stay 0 for those seasons (unrecoverable).
- `_ensure_team_honors_backfilled()` — one-time lazy backfill guarded by a
  `sim_meta` flag, so existing multi-season saves light up without a manual step.

**`o27v2/web/app.py`** — `_team_honors(team)` aggregates the table into per-honor
year lists, picks the canonical series label by league count (multi-league →
"World Series Champion" + a separate "League Champion" pill; single-league →
"Champion", no redundant pennant pill), and is passed to `team.html`.

**`o27v2/web/templates/team.html`** — a `honors-banner` row of pills directly
under the hero record, each pill showing the label, an `×N` count, and the years
(`2025 · 2026 · 2027`). Scoped CSS, colour-coded per honor (gold / indigo /
green / blue).

**`o27v2/manage.py`** — `backfill_honors` subcommand (mirrors the existing
`backfill_*` commands) for an explicit run.

## Why not the literal MLB spec from the request

Same reasoning as the standings change: the honor set is config-driven. The
"League Champion" pill is suppressed in single-league universes (where the league
final *is* the title), and the overall-title label switches between "World
Series Champion" and "Champion" by league count — no hardcoded two-league shape.

## Validation

- `py_compile` clean across all four modules; `team.html` and `standings.html`
  parse.
- Fresh-DB `init_db()` creates `season_team_honors` with the expected columns.
- End-to-end test on a seeded 8-team / 2-league DB with two archived seasons and
  a live bracket: capture produced the correct division winners, the single
  correct wild card (appeared in the bracket, not a division winner), both
  pennant winners, and the World Series champion; backfill reconstructed division
  titles and overall champions for the two prior seasons (pennants/wild cards
  correctly left empty). Example output — ALA: Series 2027 · League 2027 ·
  Division 2025·2026·2027; ALB: Wild Card 2027 only.

## Notes / scope

- Franchise identity is the stable `team_id` across seasons within a save, with
  `team_abbrev` as the fallback for backfilled rows.
- The lazy backfill writes on first team-page view of an un-migrated save; it is
  idempotent and flag-guarded.
- The almanac team pages are not touched here — this is the o27v2 live app team
  page, where the playoff data lives. A bundle-independent version could follow.
