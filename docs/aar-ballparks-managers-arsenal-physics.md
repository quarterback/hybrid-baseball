# After-Action Report — Ballparks, Named Managers, Arsenal Chips, Handedness Splits, Pitch-Type Matchup, Batted-Ball Physics

**Date completed:** 2026-05-13
**Branch:** `claude/review-and-improve-NbhPy`

---

## What was asked for

Open-ended continuation of the prior session. Items piled on over the
course of the conversation:

1. **Surface handedness in the UI** — bats / throws were already
   persisted and consumed by the engine but invisible on the box score
   and team page. Player page already had it. (Earlier batch.)
2. **Arsenal chips on the pitcher page** — show the full repertoire
   as quality-graded chips, not bucket percentages.
3. **Handedness splits on the player page** — vs LHP / vs RHP for
   batters, vs LHB / vs RHB for pitchers.
4. **Ballparks** — generate distinctive park names; surface park
   factors; generate dimensions ("what does the ideal O27 ballpark
   look like? idk!"). User explicitly ruled out flavor tags mid-stream.
5. **Pitch-type matchup in `pick_new_pitcher()`** — the manager
   should consult the opposing lineup's Power / Eye / handedness and
   prefer arms whose repertoire fits.
6. **Manager characters** — generate named managers, render the
   eight tactical axes with customer-facing labels (fog of war) so
   the underlying mechanism stays hidden, video-game-style.
7. **Batted-ball physics (hybrid layer)** — sample synthetic (exit
   velocity, launch angle, spray angle) per BIP, persist on
   `game_pa_log`, render spray charts on the game page. User asked
   for the hybrid approach specifically: keep the categorical contact
   engine canonical, layer EV/LA/spray for visualization only.

Final ask: AAR covering the whole batch.

---

## What was built

### 1. Handedness UI (`6dc8fcd`)

- Verified ratios match design intent on a fresh seed:
  - Hitter bats: 54.3% R / 34.2% L / 11.4% S vs design 55 / 33 / 12
  - Pitcher throws: 73.2% R / 26.8% L vs design 70 / 30
- Box score now shows `(L)` / `(R)` / `(S)` after batter names and
  `(LHP)` / `(RHP)` after pitcher names (`game.html`).
- Team page: new `B/T` column on the batter table; new `T` column on
  the pitcher table (`team.html`).
- Player page already surfaced it in the header + bio panel.

### 2. Ballparks (`1686938`)

- New SQL columns on `teams`: `park_name TEXT`, `park_dimensions TEXT`
  (JSON), and a parallel `manager_name TEXT`. Migrations + SCHEMA both
  updated.
- `_roll_ballpark_name(rng, city, surname_pool, used)` in
  `o27v2/league.py:777-820` produces names weighted across templates:
  surname-based ("Hadley Field"), adjective-themed ("Crescent Park"),
  city-themed ("Oakland Yards"), cricket-evoking singletons ("The
  Oval", "The Crucible", "The Pavilion"), and rare compounds. Pulls a
  surname pool from the existing name-data infrastructure so the
  names track the league's regional preset (Sri Lankan-named
  "Hasaranga Ground" actually showed up in a sample seed).
- `_roll_park_dimensions(rng)` generates LF / LCF / CF / RCF / RF in
  feet with realistic asymmetry (left/right "skew"), plus an outfield
  wall height with a heavy tail (6% chance of Green Monster class,
  14% high wall, 80% standard).
- Team page now has a Ballpark card showing name, dimensions, and the
  existing park_hr / park_hits multipliers.
- Plaintext box-score header (`o27v2/web/box_score.py`) now renders
  `at <Park Name>` on the second line.
- Explicit non-goals (per user pushback mid-session):
  - **No flavor tags** ("Bandbox", "Pitcher's Park"). User ruled out.
  - **No new gameplay dials.** Dimensions are visualization-only.
    park_hr / park_hits remain the mechanical multipliers.

### 3. Named managers + fog-of-war ratings (`1686938`)

- Each team rolls a named manager via the league's regional name
  picker (`mgr_name_picker` in `seed_league`). Names match the
  league's gender + region weights, so a Nordic preset produces
  Nordic-named managers.
- The team page's Manager card was rewritten:
  - First line: **manager name** + archetype label.
  - Second line: the eight tactical axes rendered as 5-dot ratings
    (`●●●●○`) under customer-facing labels:
    - Quick Hook · Bullpen Touch · Reads the Moment · Wild Card Energy
      · Matchup Hunter · Plays the Splits · Greenlight · Plays the Bench
  - New Jinja filter `rating_stars` (`o27v2/web/app.py:_rating_stars`)
    maps 0..1 floats to a 5-tier dot bar so the exact internal number
    stays in fog of war.
- The internal mechanism (`mgr_quick_hook` 0.78, etc.) is unchanged.
  The manager AI keeps reading the raw floats — only the display layer
  is reframed.

### 4. Arsenal chips on player page (`1686938`)

- New Jinja filter `repertoire` (`o27v2/web/app.py:_repertoire`) parses
  the JSON-encoded `players.repertoire`, maps each `quality` float to
  a 20-80 scout grade, assigns a tier ("elite" / "plus" / "avg" /
  "fringe" / "org"), and humanizes pitch names ("4-Seam", "Vulcan
  Change", "10-to-2 Curve").
- Pitcher page (`player.html:388-412`) now renders the full
  repertoire as tier-colored chips above the existing in-game usage
  panel. Each chip shows label + grade. Hover for usage weight.
- Sorted by `usage_weight` descending so the primary pitch comes first.

### 5. Handedness splits (`1686938`)

- Two new SQL helpers (`o27v2/web/app.py`):
  `_player_handedness_split_batter(player_id, throws)` and
  `_player_handedness_split_pitcher(player_id, bats)`. Both aggregate
  `game_pa_log` rows filtered by the opposing player's handedness.
- Player page gets a new "Handedness Splits" panel under the existing
  splits table (`player.html:660+`). Batters see vs LHP / vs RHP rows
  with BIP / H / 2B / 3B / HR / RBI / BA / SLG / ISO; pitchers see
  vs LHB / vs RHB rows.
- **Honest limitation flagged in the panel header**: `game_pa_log`
  captures BIP events only, not Ks or BBs, so these read as "contact
  production vs L/R-handed opposition" — not full plate-appearance
  splits. Switch hitters (`bats='S'`) are excluded from the pitcher
  side since the engine doesn't resolve their effective side per AB.

### 6. Pitch-type matchup in `pick_new_pitcher` (`1686938`)

- The manager scoring function (`o27/engine/manager.py:_score`) now
  layers a per-candidate `_matchup_bonus(p)` on top of the existing
  Stuff / Stamina / rest score:
  - Reads the next 3 batters' Power, Eye, and handedness mix from
    `state.batting_team.lineup`.
  - For each pitch in the candidate's repertoire, scores `k_delta`
    against `(power_dev + eye_dev)` (K-driving pitches help vs slugger
    lineups that are vulnerable to whiffs), `hard_contact_shift`
    against power_dev (HR-suppressors help vs Power), and
    `opposite_heavy` pitches against same-side-heavy lineups.
  - Weighted by `usage_weight × quality` so a 75-grade primary slider
    counts way more than a 35-grade show-me curve.
- The whole matchup bonus is scaled by `fielding.mgr_platoon_aggression`
  and capped at ±0.12, so a dead-ball traditionalist barely consults
  the matchup while a sabermetric maximalist leans on it — but it
  can never override a real Stuff / Stamina / rest gap.

### 7. Batted-ball physics — hybrid layer (THIS COMMIT)

This is the headline feature of the session. Approach: keep the
categorical contact engine canonical, **layer** synthetic (EV, LA,
spray) on top of each BIP for visualization. The engine never reads
the new fields; they're flavor-and-charts only.

**Sampler** (`o27/engine/batted_ball.py`, ~110 lines):
- `sample_batted_ball(rng, quality, hit_type, batter_power,
   pitch_hard_contact_shift, batter_bats)` returns `(EV mph, LA deg,
   spray deg)`.
- EV distribution per contact quality: weak (μ 74, σ 7.5, [52, 88]),
  medium (μ 88, σ 6, [76, 100]), hard (μ 102, σ 5.5, [92, 119]).
- Hit-type EV nudges: HR `+4` mph, infield_single `-3.5`, etc.
- Batter Power: ±10 mph across the 0-1 power range.
- Pitch `hard_contact_shift`: a HR-suppressing pitch (negative) drags
  EV down by up to ~1.5 mph; HR-prone pitches push it up.
- LA distribution per hit_type: ground_out μ -5°, single μ +13°,
  double μ +22°, HR μ +28°, fly_out μ +38°, line_out μ +16°.
- Spray: gaussian around a pull-handedness skew (RHB μ -12°, LHB
  μ +12°, switch μ 0°), σ 16, clamped to ±44°.

**Persistence** (`o27v2/db.py`): three new `game_pa_log` columns —
`exit_velocity`, `launch_angle`, `spray_angle` (all REAL DEFAULT NULL).
Migration + SCHEMA both updated. NULL on legacy rows and non-BIP
events (Ks / BBs / HBPs).

**Wiring**:
- `o27/engine/prob.py`: after `quality = contact_quality()` and
  `outcome_dict = resolve_contact()`, sample the triple and stamp it
  onto the return event dict alongside `pitch_type`.
- `o27/render/render.py`: capture `event.get("exit_velocity")` etc.
  into the `_pa_log` row dict.
- `o27v2/sim.py`: extend the `INSERT INTO game_pa_log` to bind the
  three new columns.

**Spray chart UI** (`o27v2/web/templates/game.html`):
- New "Spray Charts" panel under the box score. Two side-by-side
  SVGs — one per team — showing each BIP as a dot.
- Distance-from-home heuristic: `_bip_distance_ft(ev, la)` — for
  grounders (LA < 8°) distance = max(40, ev × 0.9); for line drives
  and fly balls a simplified projectile range
  `(EV² × sin(2 × LA)) / 36`, clamped to [60, 430] ft. Not physical —
  visually plausible.
- SVG includes infield diamond, foul lines at ±45°, dashed
  200/300/400 ft distance arcs, home plate marker.
- Dots colored by outcome: Out (grey), Single (yellow), 2B/3B (orange),
  HR (red), Reached on Error (light blue). Hover for batter name +
  EV/LA/distance.

**Sanity check** on a 10-game sample:
```
EV by quality:    weak μ 70.9    medium μ 85.9    hard μ 103.1
LA by hit_type:   ground_out μ -5°   single μ +14°   double μ +22°
                  triple μ +18°      hr μ +29°       fly_out μ +38°
```
Distributions land where they should. Test client hit on `/game/<id>`
returned 200, SVG present, panel rendered.

---

## Schema changes (additive only)

`teams`:
- `park_name TEXT DEFAULT ''`
- `park_dimensions TEXT DEFAULT ''` — JSON {lf, lcf, cf, rcf, rf, wall_h}
- `manager_name TEXT DEFAULT ''`

`game_pa_log`:
- `exit_velocity REAL DEFAULT NULL`
- `launch_angle  REAL DEFAULT NULL`
- `spray_angle   REAL DEFAULT NULL`

All migrations idempotent. Fresh-DB SCHEMA blocks updated to match.

---

## Files changed across the session

```
o27/engine/batted_ball.py            | NEW  (sampling module)
o27/engine/manager.py                | +75 lines (matchup bonus)
o27/engine/prob.py                   | +17 lines (sample + stamp event)
o27/render/render.py                 |  +3 lines (capture pa_log fields)
o27v2/db.py                          | +25 lines (migrations + SCHEMA)
o27v2/league.py                      | +120 lines (park + mgr name gen,
                                                  park dims)
o27v2/sim.py                         |  +6 lines (INSERT new columns)
o27v2/web/app.py                     | +280 lines (filters, handedness
                                                  split helpers, BIP
                                                  prep, route plumbing)
o27v2/web/box_score.py               |  +5 lines (venue line)
o27v2/web/templates/game.html        | +75 lines (spray chart SVG)
o27v2/web/templates/team.html        | +80 lines (manager fog-of-war +
                                                 ballpark card)
o27v2/web/templates/player.html      | +95 lines (arsenal chips +
                                                  handedness splits)
```

---

## What's reused, what's new

**Reused:**
- The name picker infrastructure (`_load_name_pools`, `make_name_picker`)
  for both park surnames and manager names. No new name-data file.
- The 20-80 scout grade ladder for repertoire chip grades — keeps the
  whole talent system reading consistently.
- The existing `_credit_fielder` / pa_log scaffolding for the
  batted-ball stamping. No new event-bus.
- `PITCH_CATALOG`'s `hard_contact_shift` and `platoon_mode` as inputs
  to both the matchup bonus AND the EV sampler — one source of truth
  for "what does this pitch do."

**New mechanics that are flavor-only (no engine math):**
- Ballpark dimensions: stored, displayed; do not feed back into the
  engine (park_hr / park_hits remain the mechanical knobs).
- EV / LA / spray: stored, displayed; do not feed back into hit_type
  resolution.

Per the prior AAR's backlog plan: this is the hybrid layer. Full
physics rewrite (where EV/LA *drive* the fielding outcome) is still
deferred.

---

## Honest gaps / what's still open

1. **Handedness splits are BIP-only.** K / BB / HBP outcomes don't
   appear in `game_pa_log` today, so the vs LHP / vs RHP split misses
   the K rate and walk rate. The renderer would need to append pa_log
   rows for terminal-K / terminal-BB / terminal-HBP events to close
   the gap.
2. **Batted-ball physics is decorative.** Engine fielding outcomes
   are still the categorical model. The full "EV/LA drives the
   fielding outcome" rewrite is still on the backlog.
3. **Distance heuristic is non-physical.** `_bip_distance_ft()` is a
   visual approximation. A real projectile-physics distance (with air
   resistance, park altitude, wind) would let the spray chart respect
   actual park dimensions and produce robbed-homer / wall-scraper
   narratives.
4. **Spray chart doesn't respect park dimensions.** Every park's SVG
   uses the same 400-ft arc and ±45° foul lines. Asymmetric and
   high-walled parks would benefit from drawing the actual fence
   shape from `park_dimensions`.
5. **Switch hitters are excluded from pitcher handedness splits.**
   The engine doesn't expose their effective side per AB; a `bats='S'`
   filter would need to read the engine's platoon resolution.
6. **Manager pitch-type matchup isn't surfaced.** The manager card
   doesn't show that this manager will lean on slider arms vs Power
   lineups — it's just a hidden bonus on top of the existing score.
7. **No EV/LA/spray export.** The plaintext box-score and markdown
   export still emit categorical hit_type only. The spray-chart data
   only lives in the HTML page today.

---

## Process notes

- The user iterated heavily mid-stream: ballpark scope started at
  "names + flavor tags + park factors," shrank to "names + park
  factors + generated dimensions" after pushback, and then the
  manager work was added partway through after I'd already opened a
  ballpark migration. Adapted by extending the same migration to
  carry both new fields and threading both generators through the
  same Phase-1 seed loop. Result: one ALTER TABLE per column, not
  two passes.
- The fog-of-war framing turned what would have been a routine
  template polish into a small filter design problem. The 5-dot
  rating scale was chosen over star ratings because it reads as
  "rating bar" rather than "review score" (less consumer-product,
  more sports-game-y).
- The hybrid-physics layer was the right scope. A full rewrite would
  have invalidated the existing engine calibration AND required an
  O27-specific probability surface (MLB Statcast data is wrong for
  the 12-batter / 27-out / sidearm structure). Decorating the
  existing engine output got the UI win at <300 lines of new code.
- Got tripped up by `_stat_delta` whitelist again — the assists work
  earlier this session had the same pattern (new field on BatterStats
  must also be added to `_stat_delta`). The EV/LA work didn't have
  that issue because it doesn't go through BatterStats at all; it
  lives entirely on game_pa_log.
