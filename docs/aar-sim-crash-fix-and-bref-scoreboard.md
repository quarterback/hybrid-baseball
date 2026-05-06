# After-Action Report — Sim Crash Fix + b-ref Game-Card Grid

**Date completed:** 2026-05-06
**Branch:** `claude/decouple-hits-runs-rGteJ`
**Commits:** `e9a42d1` (sim crash fix), `d4c1b7a` (date headline), `f71c744` (b-ref game-card grid)

---

## What was asked for

Three messages from the user, escalating from "the engine is broken"
to "the UI is wrong":

> "i realie now that i'm simming that the simulation engine does not load, you can click everything but no games generate on any of the league types, it just advances days and no games play"

> "the UI needs work too, the all-star break and end of season dates are not necessary to be prominent like that because i have no idea what the actual game date is it's in the corner when i sim/advance but the design of this is freally confusing when all i want to know is what is happening and advancing in the daily sim."

> "did you review anty of the influences i shared designwise"

> "since there's only one innings in O27, i think it'd be rendered T1/B1, O16 telling you what out we're on and where it's top of the bottom. supper inning would be S1,23"

The user also re-shared their three reference screenshots: Fangraphs
scoreboard, Baseball Reference scores+standings, MLB.com top game-tile
strip.

---

## Diagnosis

### Sim crash

`simulate_next_n` had silently been swallowing per-game exceptions:

```python
for i, g in enumerate(games):
    try:
        r = simulate_game(g["id"], seed=seed)
        results.append(r)
    except Exception as e:
        results.append({"game_id": g["id"], "error": str(e)})
```

So from the user's perspective, days advanced (the schedule cursor
moved) but no games produced finals (`played` stayed 0 on every row).
Reproducing locally with a freshly seeded DB surfaced the actual error:

```
{'game_id': 1, 'error': "name 'player' is not defined"}
```

Dropped-on-the-floor regression from a prior commit. In
`o27v2/sim.py:_extract_batter_stats`, this line referenced an
undefined `player` variable:

```python
"game_position": str(getattr(player, "game_position", "") or ""),
```

The function only had access to `engine_pid` (string ID) and `bstat`
(BatterStats); the engine `Player` object lived on the team, which
never got passed in.

A second, related bug surfaced once the sim was running: the box score
still showed `UT` for some players. Investigation:

- `_assign_game_positions(starting_fielders, todays_sp, dhs)` only
  marks `dhs` with `"J"`.
- Jokers are picked from `bench_pool = list(dhs) + list(fielders[8:])`.
  When fewer than 3 DHs are available, bench fielders fill in.
- Those bench fielders never go through `_assign_game_positions` and
  end up with `game_position=""`. The box-score `COALESCE` falls back
  to `players.position`, which for a utility player reads `UT`.

### UI / scoreboard

The screenshot the user shared told the story:

- "All-Star Break 2026-07-12" and "Season ends 2026-09-24" rendered as
  a prominent accent-colored row near the top.
- The current sim date (`2026-04-03`) was hidden inside a tiny date
  input next to "Sim Today" in the upper right.
- Today's games rendered as a wide table:
  `Away | R | H | Home | R | H | Status |`. That's the same
  database-dump problem the user already called out for the box score,
  just at the schedule level.

The reference targets all do this differently:

- **Fangraphs** — compact one-line-per-game format (`TOR (0) @ TBR (2)
  Top 7`), AL/NL split, no card chrome.
- **Baseball Reference** — small per-game boxes in a 6-column grid;
  team + score on each line; W/L pitchers with season records inline
  underneath (`W: Brayan Bello (2-4)`); prev/next date arrows
  (`« May 4, 2026   May 5, 2026   May 6, 2026 »`).
- **MLB.com** — horizontal strip of game tiles with status badges.

The b-ref pattern fits O27 best — compact, dense, no chrome, every
game's full state legible at a glance.

---

## What was built

### Commit `e9a42d1` — sim crash fix

**`o27v2/sim.py:_extract_batter_stats`** — added an `engine_team`
parameter and built an engine-player lookup so `game_position` could
be pulled off the actual `Player` object:

```python
def _extract_batter_stats(renderer, team_id, players, engine_team=None):
    engine_players_by_id: dict[str, object] = {}
    if engine_team is not None:
        for ep in (getattr(engine_team, "roster", None) or []):
            engine_players_by_id[str(ep.player_id)] = ep
        for ep in (getattr(engine_team, "lineup", None) or []):
            engine_players_by_id.setdefault(str(ep.player_id), ep)
        for ep in (getattr(engine_team, "jokers_available", None) or []):
            engine_players_by_id.setdefault(str(ep.player_id), ep)
    ...
    "game_position": str(getattr(
        engine_players_by_id.get(str(engine_pid)),
        "game_position", "") or ""),
```

`simulate_game` updated to pass `visitors_team` / `home_team` in.

**Joker `J` tag fix** — every joker now gets stamped explicitly after
`_pick_jokers`, regardless of which pool they came from:

```python
bench_pool = list(dhs) + list(fielders[8:])
jokers     = _pick_jokers(bench_pool, n=3)
for j in jokers:
    j.game_position = "J"
```

**PH inheriting position** — `mgr.pinch_hit` now copies the replaced
player's `game_position` to the substitute, so a PH who later plays
the field has a sensible position label instead of falling back to
their primary (often `UT`):

```python
if not getattr(replacement, "game_position", "") and getattr(replaced, "game_position", ""):
    replacement.game_position = replaced.game_position
```

After the fix: `simulate_next_n(n=10, seed_base=1)` produces 10 final
games with proper score/winner data.

### Commit `d4c1b7a` — promote current date

Small precursor to the full UI rework. The header strip now leads with
the current date as a `1.6rem` bold headline. All-Star Break and
Season End dates collapse into a single `0.78rem` grey reference line
underneath. Action buttons (Season History, New Season, Multi-Season
Test) stay right-aligned.

### Commit `f71c744` — b-ref game-card grid + day navigation

The substantive UI rework, modeled on Baseball Reference's classic
scores layout.

**`_attach_decisions(games)`** — new helper in `app.py` that pulls
the W/L pitcher for each finished game and stamps `g["w_pitcher"]` /
`g["l_pitcher"]` dicts with the format `{"name", "w", "l"}` — last
name and the pitcher's season W-L through this game. Logic mirrors
the existing `_pitcher_wl_map`:

- W: SP if they recorded ≥12 outs, else the most-effective reliever
  (max `outs - er`, tiebreak on outs).
- L: pitcher with the most ER, tiebreak on earliest appearance.

One round-trip per page load; cheap.

**Date navigation** — the index route now reads a `?date=` query param
that overrides `get_current_sim_date()`. Two helpers compute the
nearest prev/next dates that have any scheduled games (so the arrows
don't dead-end on empty calendar days):

```python
prev_date = db.fetchone("SELECT MAX(game_date) AS d FROM games WHERE game_date < ?", (today,))["d"]
next_date = db.fetchone("SELECT MIN(game_date) AS d FROM games WHERE game_date > ?", (today,))["d"]
```

Template renders `«` / `»` arrows flanking the date headline:

```jinja
<a href="?date={{ prev_date }}" title="Previous day with games">&laquo;</a>
<div style="font-size:1.6rem; font-weight:700">{{ sim.current_date }}</div>
<a href="?date={{ next_date }}" title="Next day with games">&raquo;</a>
```

When there's no prev/next (start/end of schedule), the arrow renders
as a greyed-out span in the same position so the layout doesn't shift.

**`game_card(g)` macro** — the heart of the redesign. One small
bordered box per game, three-line content:

```
MON Expos                 10
PIT Pirates                3
Final
W: Bello (2-4)
L: Valdez (2-2)
```

Implementation notes:

- The box has a thin 1px border, `.4rem .55rem` padding, `.82rem`
  base font. No grid lines internally.
- Away and home each on their own flex row with the team abbrev
  + name on the left and score on the right. The winning team's
  abbrev and score render bold; the loser stays normal weight.
- Status line is `.72rem` text-secondary. For finals, "Final" is a
  link to the box score (no underline; weight bumps to 600 for
  scannability). Super-inning games render as "Final/SI".
- W/L pitcher lines render only when set. Format is bare last name +
  `(W-L)` season-totals — same convention as a 1990s newspaper.
- For unfinished games the status line just reads "Scheduled".

**6-up grid** without media queries — pure CSS Grid auto-fill:

```html
<div style="display:grid;
            grid-template-columns:repeat(auto-fill, minmax(220px, 1fr));
            gap:.5rem">
```

220px minimum tile width packs to roughly 6 columns at lg viewports,
3 at md, 2 at sm, 1 at xs — the columns adjust to whatever space is
available. Gaps stay consistent.

**Same treatment for "Last Played"** — yesterday's games block uses
the same `game_card` macro. So today and yesterday now share a single
visual vocabulary: scoreboard cards in a grid.

The wide tables (`Away | R | H | Home | R | H | Status`) are gone.

---

## What didn't ship

The user mentioned an in-progress status format (`T1/B1, O16` for
regulation, `S1,23` for super-innings). Since O27 currently sims games
to completion in one pass — there's no concept of pause/resume mid-game
— there are no in-progress games to display. The card status line
collapses to "Scheduled" or "Final" today.

The status logic is a single ternary in the macro, so when live-sim
support lands, swapping in a `_status_label(g)` helper that returns
the T/B/O notation is a one-line change.

---

## Identity / regression check

`tests/test_realism_identity.py` still passes 6/6 across all three
commits. None of the schema, route, or template changes touch the
realism axes pinned by the contract.

End-to-end smoke after the sim fix:

```
$ simulate_next_n(n=10, seed_base=1)
{'game_id': 1, 'away_team': 'Expos', 'home_team': 'Marlins',
 'away_score': 10, 'home_score': 3, 'winner': 'visitors', ...}
{'game_id': 2, 'away_team': 'Astros', 'home_team': 'Cubs',
 'away_score': 6, 'home_score': 12, 'winner': 'home', ...}
{'game_id': 3, 'away_team': 'Rockies', 'home_team': 'Pirates',
 'away_score': 10, 'home_score': 21, 'winner': 'home', ...}
```

Index page renders 200, headline reads `2026-04-01`, first game card
shows `MON Expos 10 / MIA Marlins 3 / Final`, both team abbrevs as
team-page links.

---

## Files touched

**Commit `e9a42d1`** (sim crash fix):
- `o27/engine/manager.py` — `mgr.pinch_hit` copies `game_position`.
- `o27v2/sim.py` — `_extract_batter_stats` accepts `engine_team`;
  builds player lookup; `simulate_game` passes engine teams in;
  jokers force-stamped with `"J"` after `_pick_jokers`.

**Commit `d4c1b7a`** (date headline):
- `o27v2/web/templates/index.html` — current date promoted to `1.6rem`
  headline; All-Star / Season-End collapsed to a small grey subtitle.

**Commit `f71c744`** (b-ref game-card grid):
- `o27v2/web/app.py` — `_attach_decisions(games)` helper; `?date=`
  query-param support; `prev_date` / `next_date` computed and passed
  to the template.
- `o27v2/web/templates/index.html` — `game_card(g)` macro at the top;
  prev/next arrows around the date headline; today's-games and
  yesterday's-games tables replaced with the CSS-grid card layout.

Total diff: ~205 insertions, ~64 deletions across 3 files for the UI
work; ~33 insertions, ~4 deletions for the sim crash fix.
