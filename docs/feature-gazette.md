# Feature Report — The O27 Gazette (a steerable, LLM-ready news desk)

**Status:** complete, on branch `claude/game-gazette-generator-6HzMq`, current with `main`.
**Scope of this report:** the `o27.gazette` tool — what it produces, the two
decoupled layers (data feed vs. writer voice), the payload schema, the three
ways it is steered, and the end-to-end plumbing. It is read-only over the
existing DB; there are **no engine or schema changes**.

---

## 1. What it is

The Gazette turns a day's finished games into a **newspaper page** — without
hard-wiring a single canonical writer or a single model.

It does the one thing an LLM is actually bad at on its own (reading game rhythm
out of raw box rows) and hands the model the one thing it is great at (writing
prose from a clean, pre-parsed story of what mattered). Concretely, it builds a
structured **Game Context** payload for a slate, ranks the moments where win
probability actually moved, pairs that with a chosen **writer voice**, and emits
a **ready-to-paste prompt**. Drop the prompt into any LLM (Claude, GPT, …) and
you get the edition.

It is modeled on the **almanac** (`o27/almanac/`): a self-contained tool that
reads live from the o27v2 SQLite DB, mounts as a Flask blueprint, and also runs
standalone from a CLI.

Two design commitments drive everything:

- **Serialization, not the model call, is the hard part.** The payload is the
  product. The model is interchangeable.
- **The publication is steerable.** Data and voice are both *inputs*, so you can
  swap writers, hand-edit the data, or add your own voices — none of which
  touch the tool's code.

### What it deliberately is *not*
- **Not a live API integration.** It hands you a prompt; it does not call a
  paid model itself. (That bolt-on is a clean future addition — `build_prompt`
  already returns exactly what such a layer would send.)
- **Not a new data source.** Every fact comes from existing tables; the Gazette
  only *describes*.

---

## 2. The two layers (decoupled on purpose)

```
                       ┌────────────────────────┐
  games / game_pa_log  │  serialize.py          │   build_daily_payload(date)
  game_scoring_events  │  (the DATA FEED)        ├──►  Game Context payload
  game_power_play_stats│  + analytics/wpa.py     │     (plain dicts)
                       └────────────────────────┘            │
                                                             │  (dump, hand-edit,
                                                             │   feed back in…)
                                                             ▼
                       ┌────────────────────────┐
   voices.py roster ──►│  prompt.py             │   build_prompt(payload, voice=…)
   (the PROSE LAYER)   │  build_prompt()         ├──►  ready-to-paste prompt
                       └────────────────────────┘
```

- **`o27/gazette/serialize.py` — the data feed.** `build_daily_payload(date)`
  returns a payload of plain dicts (see §4). Read-only over `o27v2.db`, so it
  tracks whichever save the host app has active.
- **`o27/gazette/prompt.py` — the prose layer.** `build_prompt(payload, voice=…)`
  takes *any* payload dict plus a writer and returns the full prompt. It never
  queries the DB — it only formats what it is handed. That is what makes the
  layers swappable: the payload can come from the live DB **or** from a file you
  edited by hand.

---

## 3. The inflection-point signal (real WPA, no borrowed numbers)

The thing that separates a sharp recap from a robotic one is knowing *which
plays mattered*. The Gazette does not guess: it reuses the engine's own
empirical **Win Probability** table.

- `o27v2/analytics/wpa.py:build_wp_table()` builds WP(state) empirically from the
  league's own outcomes over the per-PA game-state stamps on `game_pa_log`
  (`outs_before/after`, `bases_*`, `score_diff_*`).
- For each PA, `serialize._inflection_points()` looks up WP before and after via
  `wpa.lookup_wp()` and takes the swing **from the batting team's perspective**.
- The top swings (by absolute magnitude, then re-ordered chronologically) become
  the game's `inflection_points`. The writer is told to build the narrative
  around them and *not* recap every run.

This is the same WPA machinery the analytics page uses — no MLB-borrowed win-
probability table. One consequence worth stating plainly: the empirical table is
only as smooth as the league has games. Early in a season the swings are noisy
(a routine double can show a perverse sign); over a full schedule they settle.

---

## 4. The payload schema (the "Game Context")

`build_daily_payload(date)` →

```jsonc
{
  "publication": "The O27 Gazette",
  "sport": "O27",
  "edition_date": "2026-04-17",
  "games_played": 3,
  "games": [
    {
      "game_id": 58,
      "matchup": { "away": {name, abbrev, city},
                   "home": {name, abbrev, city, park} },
      "final":   { "away_score", "home_score", "winner", "loser", "margin" },
      "went_to_extras": false,                 // legacy super_inning flag
      "weather": { "temperature", "wind", "precip" },
      "inflection_points": [                    // ranked by WP swing
        { "out_window": 2, "bases": "runners on 1st and 3rd",
          "batter": "Marshall", "pitcher": "Lacey", "stay": true,
          "runs_scored": 1, "win_prob_swing_pct": +31.4,
          "swing_for": "CMH", "note": "Marshall stays, 1 run scores" }
      ],
      "scoring":  [ { out_window, half, batter, runner, how, score } ],
      "standouts": { away_bat, home_bat, away_arm, home_arm },
      "declared_seconds": { outcome, home?: {...}, away?: {...} } | null,
      "power_play":      [ { fielder, team, windows, outs_covered, … } ] | null
    }
  ]
}
```

Notes that matter for the prose:

- **Standout batting lines lead with counting stats** (`"7 H, 4 HR, 11 RBI"`),
  not `"7-for-6"`. In O27 a single at-bat can yield several hits (the second-
  chance at-bat), so `H > AB` is real — but `"7-for-6"` reads as a typo and can
  make a model "correct" it. `ab` is still carried in the payload, so a voice
  that wants to flaunt the quirk can.
- **Rare mechanics are flagged, not narrated:** `declared_seconds`,
  `power_play` (the nickel), and `went_to_extras` are color the writer is told
  to use sparingly in a Notebook line.

---

## 5. The writer roster (and the shared brief)

`o27/gazette/voices.py` holds a `SPORT_BRIEF` plus a roster of `Voice` profiles.

**`SPORT_BRIEF`** is the load-bearing piece: it is prepended to *every* voice, so
no writer can drift into MLB language. It encodes the canonical O27 vocabulary
(sourced from `README.md` and the `docs/aar-*` build logs):

- **No innings** — game-time is the "27-out half / arc", "out N of 27", an "out
  window"; never "inning", and "top/bottom" only to mark who batted second.
- The mechanic is always the **"second-chance at-bat" (2C)** — *never* "the stay"
  or "the stay mechanic"; `"stays"/"stayed"` is allowed only as the verb.
- **Declared Seconds**, **Walk-Back**, the **nickel** (Power Play, not "the 10th
  fielder"), **jokers**, **foul-out**, **3-out frames** for extras, and the
  sidearm/submarine flavor — each named the way the docs name it.

Each `Voice` layers a persona (and, optionally, its own output shape) on top:

| id | name | voice |
|---|---|---|
| `beat` | The Beat *(default)* | hard-boiled beat reporter — concrete, result-first |
| `stathead` | The Stathead | sabermetric columnist — leans on the WP swings and 2C |
| `homer` | The Homer | partisan fan-blog energy, still factual |
| `wire` | The Wire | terse neutral wire service; its own brief-style output spec |
| `scribe` | The Old Scribe | 1920s broadsheet — ornate, period diction |

`Voice.system_prompt()` = `SPORT_BRIEF` + persona + (`output_spec` or the shared
default). `prompt.build_prompt()` then appends the slate JSON.

---

## 6. Steering it — three controls that compose

| Control | Where | Use |
|---|---|---|
| **Web voice picker** | `/gazette` dropdown (`?voice=`) | pick a writer; the copy-prompt button + exports update to that persona |
| **CLI flag** | `python -m o27.gazette --voice <id>` | script the standalone tool with any writer |
| **User voices file** | `$O27_GAZETTE_VOICES` (or `o27/gazette/voices_user.json`) | add or override writers without code changes — merges over the builtins |

The user-voices file maps an id to `{name, blurb, persona, output_spec?}`;
`voices.all_voices()` merges it over the builtins (user wins), so you can add a
"poet" voice or rewrite "The Beat" entirely from a JSON file.

And because the data is also an input: dump the payload
(`python -m o27.gazette --json > slate.json`), edit it, and re-render
(`python -m o27.gazette --from-file slate.json --voice scribe`) — no DB needed.

---

## 7. End-to-end plumbing

| Layer | File(s) | Role |
|---|---|---|
| Data feed | `o27/gazette/serialize.py` | `build_daily_payload`, per-game context, inflection points, scoring, standouts, rare-mechanic flags |
| WP reuse | `o27v2/analytics/wpa.py` | `build_wp_table` / `lookup_wp` — empirical win probability for the inflection signal |
| Voices | `o27/gazette/voices.py` | `SPORT_BRIEF`, builtin `Voice` roster, user-voice file loader |
| Prose | `o27/gazette/prompt.py` | `build_prompt(payload, voice=…)` — payload-in, prompt-out |
| Web | `o27/gazette/blueprint.py`, `templates/gazette.html` | `/gazette` page + `/gazette/export.txt` + `/gazette/export.json`; voice picker; reuses the `.copy-md` clipboard helper |
| CLI | `o27/gazette/cli.py`, `__main__.py` | `python -m o27.gazette` (`--voice/--json/--from-file/--db/--list-voices`) |
| Mount + nav | `o27v2/web/app.py`, `templates/base.html` | `register_blueprint(gazette_bp)`; Games-menu link |

**Coupling note:** `o27.gazette` depends on `o27v2` (db + wpa), the same
direction the almanac already crosses. The host app imports the blueprint at
startup next to the almanac's.

---

## 8. Verification

- **Payload shape:** every game carries `matchup / final / inflection_points /
  scoring / standouts`; the winner is one of the two abbrevs; each inflection
  point has a signed `win_prob_swing_pct` and a `swing_for` team.
- **Voice compliance:** every builtin voice composes a prompt that names the
  mechanic "second-chance at-bat", carries the "no innings" brief, and never
  uses "stay mechanic" outside its own prohibition.
- **Layer decoupling:** `build_prompt()` renders a hand-built payload dict with
  no DB access (the data feed and prose layer are independent).
- **User-voices hook:** an `$O27_GAZETTE_VOICES` file adds a usable voice
  alongside the builtins.
- **Blueprint:** `/gazette/`, `?voice=`, `export.txt`, and `export.json` all
  return 200 against a seeded, simmed save.
- **Tests:** `tests/test_gazette.py` — **5 green** (payload shape, WP inflection
  points, voice-brief compliance, decoupling, user voices, blueprint routes).
  The smoke test and the rest of the template-render suite pass on the merged
  tree.

### Known, unrelated
- `tests/test_template_renders.py::test_season_archive_writer_runs_end_to_end`
  fails on a `wrc_plus` assertion — **pre-existing on `main`**, fails identically
  with this work stashed. Not introduced here.
- **WP noise on thin samples:** on a young or small league the empirical WP table
  is sparse and some swing signs look wrong. This is data volume, not a bug; a
  full season smooths it.
- **No live model call:** the tool emits a prompt, by design. A live-render
  bolt-on would consume `build_prompt`'s output unchanged.

---

## 9. Commits (this branch, after `main` @ `e46e4cc`)

```
Add The O27 Gazette — a steerable, LLM-ready news desk
```

Adds the `o27/gazette/` package, the `/gazette` blueprint + nav link, and
`tests/test_gazette.py`. No migrations: the tool is read-only over existing
tables, so any save works the moment the branch is deployed.
