# AAR — Postseason picture, wild-card clarity, and team playoff status

## Goal

The standings and playoffs surfaces were technically functional, but they were
not explaining the baseball logic clearly enough for either a human user or an
LLM agent to reason about the season state. The pain points were:

- standings showed records, but not the allocation logic behind the playoff
  field;
- wild cards were visually present, but it was easy to miss that division
  winners are removed before the wild-card race is sorted;
- the playoff bracket looked like generic game rows instead of a postseason
  bracket with rounds, best-of lengths, and series state;
- a team page did not answer the direct question: **did this team make the
  postseason, and what happened to it?**

The work in commit `ed0b6b5` addressed those interpretability gaps without
changing the underlying bracket engine or sim outcomes.

## What changed

### 1. Current-season team postseason status

Added `team_postseason_status(team_id, season=1)` to `o27v2/playoffs.py`.

The helper reads the live `playoff_series` table and returns a compact status
object:

- `made` — whether the team appears in the current postseason bracket;
- `seed` — the team's original postseason seed;
- `berth` — inferred as `Division winner` or `Wild Card` from the seed versus
  the league's division count;
- `state` — one of the user-facing summaries, e.g. `Alive in Division Series`,
  `Eliminated in League Championship`, or `Champion`;
- `round` — current or last round label;
- `series_wins` / `series_losses` — the current or final series score from the
  team's perspective.

This deliberately derives from `playoff_series`, not from regular-season wins,
so it stays correct after the bracket advances and after a wild-card team beats a
higher seed.

### 2. Team-page banner

`team_detail()` now calls the postseason status helper and passes
`postseason_status` into `team.html`. If the team made the live bracket, the team
page renders a **Current postseason** banner directly below the franchise honors
area:

- `Made postseason · #N Division winner/Wild Card`;
- alive/eliminated/champion state;
- current or last series score;
- a direct link to the bracket.

This gives a one-click answer to the complaint that there was no way to tell if a
team made the postseason.

### 3. Standings explanation

The standings template now includes an explicit allocation note above each
playoff-picture panel:

- first-place clubs in each division receive division berths;
- wild cards are awarded only after those division leaders are removed;
- therefore a second-place team can have a better record than another division
  leader and still be a wild card rather than a division seed.

This did not change `_playoff_picture()` or `compute_fields_by_league()` — it
made the existing model legible.

### 4. Markdown export for agents / LLM paste

`export_standings()` now accepts an optional `playoff_picture` argument, and the
`/standings/export.md` route passes the same computed playoff picture used by the
HTML standings page.

The markdown output now starts with a plain-English playoff rule and then adds,
per league:

- a seeded playoff-picture table with `Bid = Division winner` or `Wild Card`;
- a wild-card race table that explicitly says division winners have been removed;
- `IN` / `OUT` status and `WCGB` for the wild-card pool.

This was aimed specifically at the agent-failure mode: if standings markdown is
pasted into an LLM prompt, the export now carries the rules and the derived field
instead of forcing the model to infer playoff structure from raw standings.

### 5. Playoff bracket presentation

The playoff series tile macro now adds:

- a round badge (`Wild Card`, `Division Series`, `League Championship`, `World
  Series` via `round_label()`);
- a `Best of N` label;
- an in-progress summary line (`Series in progress: A 2, B 1`);
- a final summary line (`Series final: A advances, 3-1`);
- a subtle postseason-styled background treatment.

The pre-initiation format copy also explains that division winners seed first
and wild cards are the highest-ranked non-division-winners needed to fill the
field.

## Why this was UI / export work, not bracket-engine work

The bracket engine already had the important sports model:

1. group teams by league;
2. select division winners first;
3. remove those teams from the wild-card pool;
4. fill remaining playoff spots with the best records among non-division-winners;
5. create standard seeded pairings with byes when the field is not a power of two;
6. advance series winners round by round, optionally into a World Series.

The failure was that too much of that logic lived only in code. The standings and
playoff pages did not expose enough derived state, labels, or explanatory text.
The work therefore focused on *making the model inspectable* rather than
rewriting the model.

## Validation

Completed checks:

- `python -m py_compile o27v2/playoffs.py o27v2/web/app.py o27v2/web/text_export.py`
  passed.
- A synthetic call to `text_export.export_standings(...)` confirmed that the new
  playoff-picture markdown renders the explanatory rule and tables.
- `git diff --check` passed.

Attempted but environment-blocked:

- Flask test-client smoke checks for `/standings`, `/standings/export.md`, and
  `/playoffs` returned 500 because the local active DB in the container was not
  initialized and did not have `teams` / `playoff_series` tables. That failure
  was a local setup limitation, not an application assertion failure.

## Known limitations / follow-ups

- `team_postseason_status()` infers berth type from seed number versus division
  count because `playoff_series` does not persist `div_champ` / `wild_card` from
  the initial field. A future schema migration could store the bid type directly
  on `playoff_series` or a separate postseason-field table.
- Current-team status is live-season only. Archived per-season honors are handled
  elsewhere by `season_team_honors`; this change did not add a historical
  postseason-results ledger.
- The bracket page is clearer, but a future pass could add a bracket summary
  header per league: champion, active round, remaining teams, and eliminated
  teams.
- Route smoke tests should be rerun against an initialized fixture DB so the
  standings and playoffs pages are covered by an automated regression test.

## Files touched in the implementation

| File | Purpose |
|---|---|
| `o27v2/playoffs.py` | Added `team_postseason_status()`. |
| `o27v2/web/app.py` | Passed playoff picture into markdown export and postseason status into team pages. |
| `o27v2/web/text_export.py` | Added explicit playoff-rule and playoff-picture markdown output. |
| `o27v2/web/templates/standings.html` | Added human-readable playoff allocation explanation. |
| `o27v2/web/templates/team.html` | Added current-postseason banner. |
| `o27v2/web/templates/playoffs.html` | Added round/best-of labels, series summaries, and postseason styling. |
