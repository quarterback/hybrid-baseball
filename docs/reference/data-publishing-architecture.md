# Data-publishing architecture: Almanac · O27 Index · Gazette

> **What this is.** A reference for how O27 turns simulated game data into
> presentable, external-facing surfaces — the Almanac (stats portal), the O27
> Index (batted-ball analytics), and the Gazette (LLM news desk). The last
> section translates the pattern into a handoff for building a juniors
> tour / recruiting page in the tennis sim. Kept here because the *shared
> architecture* is the reusable part.

---

## The one rule all three obey

All three are **read-only presentation layers over the live SQLite DB**
(`o27v2/o27v2.db`). None of them touch the engine, and **none require a schema
change.** The engine sims games and writes rows; these systems aggregate those
rows and render them for a different audience. The sim never knows they exist.

That decoupling is the whole design. Every one of them follows the same four
layers, kept physically separate:

```
SQLite DB  (game_*_stats, game_pa_log, game_scoring_events, players, teams, games)
   │
   ▼  loader / serialize   — source → plain dicts. NO math here.
   │
   ▼  compute              — derived stats / percentiles / WPA. Shared math.
   │
   ▼  render               — one or more output surfaces (live + static).
   │
   ▼  export               — portable artifact (CSV / JSON bundle / .txt feed).
```

**Why the split matters:** the same computed dataset drives a live web page, a
static archive, *and* a machine-readable feed without duplicating logic. The
loader being source-agnostic is what lets an *exported* bundle become a *future*
input (round-trip). Keep `loader / compute / render / export` distinct and you
get all of that for free.

Common properties:

- **Live freshness via mtime.** Live views cache on `(db_path, db_mtime)`. A
  fresh sim bumps the file mtime; the next request re-aggregates automatically.
  No rebuild step, no manual invalidation.
- **Sport-calibrated, never MLB-borrowed.** Every threshold and weight is
  re-derived from O27's own run environment (see the O27 Index thresholds and
  the Gazette's empirical WP table below).
- **Stateless aggregators.** They track whichever save is active. No state of
  their own beyond optional caches.

---

## 1. The Almanac — `o27/almanac/`

A **Fangraphs-style stats portal** that renders the *same* Jinja templates in
two modes from one codebase.

### Two render modes, one template set

- **Live** — Flask blueprint mounted at `/almanac` on the o27v2 web app
  (`blueprint.py`). Every URL the static renderer writes is mirrored here, so
  templates render unchanged; only `base_path` differs (`/almanac/` live vs
  `""`/`../` static). Views cached per `(db_path, db_mtime)`.
- **Static** — site generator:
  `python -m o27.almanac build --source <db|json> --out site/` writes a
  self-contained HTML/CSS/JS archive (50+ pages) plus a downloadable
  CSV/JSON/ZIP bundle.

### Layer map

| File | Responsibility |
| --- | --- |
| `loader.py` | Load from **SQLite *or* a `season-bundle.json`** → standardized dict schema keyed by table. NO derived math. |
| `compute.py` | All stat math — wOBA/ERA+/WAR equivalents, league totals, percentile ranks, career aggregates → a `Views` dataclass. |
| `export.py` | Per-dataset CSVs, a round-trippable `season-bundle.json`, and `season-bundle.zip` (CSVs + JSON + a copy of the source DB). |
| `render.py` | Jinja2 → HTML pages. |
| `blueprint.py` | Live Flask routes mirroring the static URLs, with cached views. |
| `cli.py` / `__main__.py` | `build` / `serve` / `ingest` subcommands. |

### Data flow (batting leaders example)

```
loader.py     SELECT * FROM game_batter_stats WHERE COALESCE(is_playoff,0)=0   → raw rows
compute.py    aggregate per player: SUM(pa,h,ab,runs,rbi,…)
              PAVG = h/pa ; BAVG = h/ab (can exceed 1.0 from stays)
              wOBA = (0.72·BB + 0.95·1B + 1.30·2B + 1.70·3B + 2.05·HR)/PA
              wOBA+ = wOBA/league_wOBA · 100 ; VORP/WAR vs replacement baseline
              percentile ranks computed league-wide
Views.batting_season  → render.py → leaders/batting.html (sortable)
                      → export.py → batting_season.csv + season-bundle.json
```

### The round-trip contract

`export.py` emits `season-bundle.json` whose shape **matches the loader's
expected input**. Drop it into a fresh build with
`--source path/to/season-bundle.json` and the site rebuilds identically. This
is the portability seam — one season's output is another build's input.

### Triggering

```bash
make almanac-build                                              # live DB → ./site/
make almanac-serve                                              # build + preview :8765
python -m o27.almanac build --source season-bundle.json --out site/
python -m o27.almanac ingest --source <path>                   # validate a bundle
# Live: just hit /almanac — re-aggregates on DB mtime change.
```

---

## 2. The O27 Index (O27i) — `o27v2/web/` (routes in `app.py`, `o27i_*.html`)

The **Statcast-style percentile layer**. Unlike the Almanac it lives *inside*
the main web app rather than as a standalone module. It's a *ranking system* —
multiple sortable leaderboards plus per-player percentile bars — not a single
scalar "index."

### Routes

| Route | Handler | Page |
| --- | --- | --- |
| `/o27i` | `o27i_home()` | Landing: player search + EV/Barrel leader snapshots |
| `/o27i/leaders` | `o27i_leaders()` | Full sortable batted-ball leaderboards |
| `/o27i/advanced` | `o27i_advanced()` | Expanded metrics (WPA, arsenal, TTO, fielding) |
| `/player/<id>/o27i` | `player_o27i()` | Red→blue percentile slider bars vs league |

### Sport-calibrated thresholds (the important bit)

These are **hardcoded to O27's run environment, not MLB's**:

```python
_O27I_HARDHIT_EV = 100.0          # hard-hit floor (MLB uses 95)
_O27I_BARREL_EV  = 104.0          # barrel EV threshold
_O27I_BARREL_LA  = (10.0, 35.0)   # barrel launch-angle window (deg)
_O27I_SWEET_LA   = (8.0, 32.0)    # sweet-spot launch-angle window (deg)
```

Metrics include xwOBA, avg/max EV, Hard-Hit%, Barrel%, Sweet-Spot%, BB%, K%,
plus O27-natives like **Second-Chance %** (stay rate) and RISP-OPS. Pitcher rows
mirror these as "against" (xwOBA-against, EV-against, etc.).

### Data flow

```
game_pa_log         → exit_velocity, launch_angle per PA
                      aggregate by batter → avg/max EV, hardhit%, barrel%
game_batter_stats   → pa, k, bb, stays, risp → walk%, K%, RISP-OPS
analytics xwOBA-EV  → empirical xwOBA per EV/LA bin → per-player lookup
_percentile_ranks() → each metric ranked 0–100 league-relative
                      (100 = best; "lower is better" stats reversed)
→ o27i_leaders.html (sortable, ?sort=<key>) / percentile slider bars
```

Qualifier gate scales with season length: `min_bip / min_pa = max(15, games // 30)`.
League-scoped via `?league=<name>` for fair cross-league ranking. Routes can
return JSON with `?format=json`.

---

## 3. The Gazette — `o27/gazette/` (mounted at `/gazette`)

The **LLM-ready news desk**, and the cleanest example of "external tool pulls a
feed." Two layers, *deliberately decoupled*:

```
games, game_pa_log,        serialize.py  (THE DATA FEED)
game_scoring_events,    →  build_daily_payload(date) → Game Context dict
game_power_play_stats      + analytics/wpa.py             (plain JSON-able dicts)
+ empirical WP table              │
                                  ▼   (dump to JSON, hand-edit, feed back in)
voices.py roster           prompt.py  (THE PROSE LAYER)
(swappable writers)     →  build_prompt(payload, voice) → ready-to-paste prompt
```

The split is the point: *"The hard part of a good generated recap is
serialization, not the model call."* `serialize.py` only **describes** what
happened — no model, no prose. `prompt.py` + `voices.py` (classical,
sabermetrician, …) are interchangeable consumers of that one payload.

### The inflection-point signal (real WPA, no MLB numbers)

Per PA, win probability is looked up before and after from the league's own
**empirical WP table** (`o27v2.analytics.wpa`) keyed on state
`(outs, bases_occupied, score_diff)` over the stamps in `game_pa_log`. PAs are
ranked by **absolute WP swing** (from the batting team's perspective); the top 4
are then re-sorted chronologically for the recap.

### Surfaces

| Endpoint | Output |
| --- | --- |
| `/gazette` | News desk: date + voice picker, rendered payload + prompt |
| `/gazette/generate` (POST) | Calls Claude if `ANTHROPIC_API_KEY` set; caches in `gazette_articles` per `(slate_date, voice_id)` |
| `/gazette/export.json` | Raw Game Context payload (JSON) |
| `/gazette/export.txt` | Ready-to-paste prompt (text/plain) |
| `python -m o27.gazette …` | Headless CLI build/prompt |

### Payload shape (abridged)

```json
{
  "publication": "The O27 Gazette", "sport": "O27", "edition_date": "2026-06-09",
  "games_played": 5,
  "games": [{
    "home_team": "NYM", "away_team": "BOS", "final_score": "BOS 7, NYM 4",
    "inflection_points": [
      {"out_window": 5, "batter": "Judge", "pitcher": "deGrom",
       "event": "Judge homers, scoring 2 runs", "win_prob_swing_pct": 18.3}
    ],
    "scoring_summary": [ … ],
    "standouts": {"away": {"name": "Judge", "line": "3 H, 2 HR, 5 RBI"},
                  "home": {"name": "deGrom", "line": "24 outs, 4 ER, 8 K"}},
    "power_play": null, "declared_seconds": null
  }]
}
```

---

## Summary table

| System | Purpose | Input | Output | Where it lives |
| --- | --- | --- | --- | --- |
| **Almanac** | Fangraphs-style stats portal | SQLite DB *or* JSON bundle | Static HTML (50+ pages) + CSV/JSON/ZIP; same templates served live | `o27/almanac/`, live at `/almanac` |
| **O27 Index** | Batted-ball analytics + percentile leaderboards | `game_pa_log` physics + `game_*_stats` | Sortable HTML + percentile bars + JSON API | `o27v2/web/app.py` (`o27i_*`) |
| **Gazette** | LLM-ready game recaps | `games`, `game_pa_log`, `game_scoring_events` + WP table | Structured JSON payload + paste-ready prompts | `o27/gazette/`, live at `/gazette` |

---

## Handoff: building the tennis juniors tour / recruiting page

Give this to the tennis sim's agent.

> Build the juniors tour / recruiting page as a **read-only presentation layer
> over the sim DB**, never wired into the engine. Four separated layers:
>
> 1. **loader** — reads the sim DB, *and* can read its own exported JSON bundle
>    (so a season's export round-trips back in as input).
> 2. **compute** — all derived ratings, rankings, and percentiles as shared
>    math, consumed identically by every output surface.
> 3. **render** — drives a live web view *and* a static export from the **same**
>    templates (mirror the Almanac's dual-mode blueprint + generator).
> 4. **export** — emits a round-trippable JSON bundle as the portability
>    contract, plus CSVs.
>
> **Calibrate every percentile/index against the junior population itself**, not
> real-world tennis constants (this is the O27 Index lesson — its hard-hit/barrel
> cuts are re-derived from the sim, not MLB's 95 mph). Gate leaderboards on a
> minimum-sample qualifier so small-sample juniors don't top the boards.
> Render as percentile bars, not raw numbers — that's what makes a recruiting
> page legible at a glance.
>
> If you want recruiting writeups / match recaps, build the **serializer (a
> JSON feed endpoint) first**, completely separate from any model call — get the
> structured payload right (player context, momentum swings, standout
> performances), and keep prose generation as a swappable consumer of that
> payload (the Gazette lesson). Use the sim's own empirical win-probability /
> momentum table for "key moments," never borrowed numbers.

**Concrete starter module layout for the tennis repo:**

```
juniors/
  loader.py      # sim DB  -or-  juniors-bundle.json  → plain dicts
  compute.py     # ratings, rankings, percentiles (shared math)
  render.py      # Jinja templates → live + static HTML
  export.py      # CSV + juniors-bundle.json (round-trip contract)
  blueprint.py   # live Flask routes, cached on (db_path, db_mtime)
  serialize.py   # OPTIONAL: structured recap/recruiting JSON feed
  templates/ static/
```
