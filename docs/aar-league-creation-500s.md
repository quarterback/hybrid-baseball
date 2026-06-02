# After-Action Report — League/universe creation 500s

**Date completed:** 2026-06-02
**Branch:** `claude/league-creation-500-errors-RjaSw`

---

## TL;DR

Two unrelated failure modes were both surfacing as a bare 500 page when
creating a league or universe:

1. **Intermittent "500, then a refresh works."** `db.get_conn()` set no
   `busy_timeout`, so SQLite's default of `0` made any connection throw
   `database is locked` the *instant* another connection held the writer
   instead of waiting for it. `/new-league` spawns an almanac-warm background
   thread on its way out, and the redirect to `/` immediately issues more
   queries — that overlap (plus any running sim or a second tab) is exactly the
   "sometimes it 500s, refresh and it's fine" the user described. Fixed by
   adding `PRAGMA busy_timeout = 10000` so connections wait up to 10s for the
   lock to clear.

2. **Hard crash on oversized universes.** The team-definition pool
   (`_load_teams_db()`) holds **86** teams. A universe asking for more than
   that can't be satisfied, so `_assign_universe_divisions` raises
   `ValueError` deep inside `seed_league`. Neither `/new-league` nor
   `/universe/new` wrapped the `seed_league`/`seed_schedule` calls, so the
   exception bubbled up as a 500 — *and* left the freshly-created (but
   empty/partial) save active, so the dashboard was broken too. Fixed by a
   shared `_create_and_seed_save()` helper that catches any seed failure,
   deletes the half-built save (which repoints the active save back to the
   previous working league), and flashes a human-readable message instead of
   500ing.

---

## What was asked for

> "when I start a league sometimes it will 500 you refresh and sometimes the
> league creation worked but other times it doesn't and crashes especially if
> I try to make a universe with too many leagues/teams — how can we make it so
> it doesn't just crash without warning"

So: stop the silent 500s, and degrade gracefully (with a warning) when a
config is too big.

---

## Root causes (verified)

- **Lock 500s:** `o27v2/db.py:get_conn()` opened connections with WAL +
  `synchronous=NORMAL` but no `busy_timeout`. WAL lets readers and the writer
  coexist, but two *writers* (or a writer vs. a mid-transaction connection)
  still conflict, and with `busy_timeout=0` the loser errors immediately rather
  than waiting. The almanac-warm thread launched at the tail of `/new-league`
  is a concrete second writer racing the post-redirect requests.

- **Oversized-universe crash:** confirmed empirically. With the env pointed at
  a scratch saves dir, a 10-league × 20-team (200-team) universe raises:

  ```
  ValueError: Universe league_specs sum to 200 teams but 86 were selected
  — these must match (team_count).
  ```

  Neither creation route had a `try/except` around `seed_league` /
  `seed_schedule`, so this reached Flask as a 500. The route had *already*
  called `saves.new_save()` (which switches the active save), so the user was
  also left on a broken empty save.

---

## What changed

- `o27v2/db.py` — `get_conn()` now runs `PRAGMA busy_timeout = 10000`.

- `o27v2/web/app.py` — new module-level helper `_create_and_seed_save()` that
  wraps the create-then-seed sequence (`new_save` → `init_db` → `seed_league`
  → `seed_schedule` → `set_active_league_meta`). On any exception it logs,
  calls `saves.delete_save()` on the half-built save (which auto-repoints the
  active save to the previous league), and returns `(False, message)`. Both
  `new_league_post()` and `universe_new_post()` now call it and, on failure,
  `flash(...)` a friendly error and redirect back to the form. The universe
  message explicitly hints that too many teams for the pool is a likely cause.

---

## Validation

- `python3 -m py_compile o27v2/db.py o27v2/web/app.py` — clean.
- End-to-end script against a scratch saves dir (no Flask needed):
  - A small valid universe seeds and becomes active.
  - A 200-team universe raises `ValueError` inside `seed_league` (the old 500
    source) — now catchable.
  - After the simulated rollback, `saves.get_active_id()` is restored to the
    previous good save and that save **still has its 8 teams** — i.e. the
    failure no longer leaves the user stranded on a broken active save.

## Follow-up: lift the 86-team ceiling instead of just warning at it

The graceful catch turned the oversized-universe crash into a polite
"couldn't build that" message — but the user's real intent was bigger
universes, not a nicer wall. So the cap was removed.

The "86" was never structural: it's the row count of the curated
`o27v2/data/teams_database.json`. `seed_league()` drew teams from that pool
*without replacement*, so once all 86 distinct teams were dealt, `selected`
ran short and `_assign_universe_divisions` raised (it requires
`len(selected) == team_count`).

Crucially, a procedural identity generator **already existed and was already
wired in**: universe configs set `team_naming: "generated"`, and the insert
loop overwrites each slot's name/city/abbrev with a locale-appropriate
identity from `team_naming.generate_league_teams()` (which draws on
`data/names/*` — regional city pools, mascot pools, "Baseball Club" locale
spellings — and never runs dry because it wraps its city pool). The pool was
acting purely as a slot-counter that got entirely renamed.

So the fix is a "Stage 4" in `seed_league()`'s team selection: when the
curated pool can't fill `n_teams`, pad `selected` with lightweight
placeholder slots up to `team_count` and flip on generated naming
(`force_generated`). Then every overflow team gets a real generated identity
just like the universe path always did. Placeholders deliberately omit
`lat`/`lon` so the geographic-division sort uses its missing-coord fallback.

Net effect: universes (and custom leagues) of arbitrary size now seed
instead of capping at 86. Verified:

- **200-team universe** (10 leagues × 20): seeds 200 teams, 198 distinct
  names, 163 distinct cities, 200 unique abbrevs, 0 placeholders left, 1000
  games scheduled. Sample identities: *Hamburg Universal Electricians*,
  *Hong Kong Highlanders*, *Apia Baseball Club*.
- **120-team bare custom league** (no `home_region`, so the
  geographic-division path with coord-less placeholders): 120 teams, even
  20-per-division spread, 0 placeholders, 600 games.
- **30-team league** (≤ pool): unchanged — keeps real MLB nicknames
  (`force_generated` stays False).

The earlier "try fewer leagues or teams" hint on the universe form was
removed since team-pool exhaustion is no longer a failure mode (the
try/except still guards genuinely-bad configs / other errors).

### What I did NOT change

- No *hard upper-bound* cap was added. Sizes are now limited only by time and
  memory, not the data file; an absurd request (tens of thousands of teams)
  would be slow but is still caught by the graceful handler rather than 500ing.
- `seed_league`/`seed_schedule` are still not transactional — a mid-seed
  failure can leave a partially-written save DB. That no longer matters for the
  user because the whole save is deleted on failure, but a true
  all-or-nothing transaction wrapper remains future work if seeds ever need to
  target an existing DB in place.
- pytest/flask are absent in this sandbox, so the route handlers were verified
  by exercising the underlying `db`/`saves`/`league` calls directly rather than
  via an HTTP request.
