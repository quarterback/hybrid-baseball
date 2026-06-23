# After-Action Report — Cricket-stat ports: PAI, DO%, xO, DOA%, and O27 Index wiring

**Date completed:** 2026-06-23  
**Branch:** `work`  
**Status:** Shipped analytics + UI wiring; no new persistent DB schema.

---

## TL;DR

The request started as an exploration of whether three cricket analytics ideas
could be ported into O27:

1. **Match Impact / True Strike Rate** → O27 **Pressure-Adjusted Impact (PAI)**,
   plus **TRR+**.
2. **Dot Ball Percentage** → pitcher **Dead Out Percentage (DO%)**, plus hitter
   **Dead Out Avoidance (DOA%)**.
3. **Expected Wickets** → pitcher **Expected Outs (xO)**, split into pure xO and
   power-play-supported xO.

The final build shipped all three families as derived analytics, exposed them
on leaderboards, stats browse, O27 Index leaderboards, and O27 Index player
cards. The implementation deliberately reuses existing logs (`game_pa_log`,
`game_pitcher_stats`, `game_batter_stats`, `game_power_play_stats`) instead of
adding new schema columns.

---

## What was asked for

The original user idea was to port cricket stats if O27 did not already have
native versions:

- **Match Impact / True Strike Rate**: pressure-adjust batter value against live
  run requirements instead of raw slash lines.
- **Dot Ball Percentage**: track empty, pressure-building outs.
- **Expected Wickets**: evaluate how many outs a pitcher should have created
  from contact quality / sequence quality independent of plain box-score luck.

Follow-up clarifications were important:

- A two-out DP should count as **two** dead outs, not one.
- xO should include defense/power-play context, not be purely defense-stripped.
- The stats should be wired into leaderboards and the O27 Savant/O27 Index
  portal.
- After reviewing possible extensions, the approved next additions were PAI,
  hitter DOA%, and a pure-vs-supported xO split.

---

## What changed

### 1. Design record

Added `o27/docs/cricket_stat_ports.md` as the working design note for the three
metric families. It now records build status for PAI, DO%, xO, DOA%, and the
supported-xO behavior.

### 2. Pressure-Adjusted Impact (PAI)

Added `o27v2/analytics/pressure.py` with `build_pressure_impact()`.

Implementation shape:

- Uses `build_re_table()` as the run-expectancy backbone.
- Computes event run value as:

```text
runs_scored + RE_after - RE_before
```

- Applies a chase-pressure multiplier from deficit and outs remaining:

```text
pressure = 1 + min(3.0, RRR/3O / 6.0)
```

- Emits:
  - `pai`
  - `pai_per_pa`
  - `trr_plus`
  - `avg_pressure`
  - `run_value`

This is intentionally not WPA. WPA answers “did this move win probability?”;
PAI answers “did this PA beat the live chase requirement inside the 27-out
envelope?”

### 3. Expected Outs (xO)

Extended `o27v2/analytics/expected_woba.py` with `build_expected_outs_table()`.

Implementation shape:

- Reuses EV/LA binning from the xwOBA pipeline.
- Builds league-average outs-per-BIP by EV/LA bucket from `game_pa_log`.
- Credits strikeouts and foul-outs as automatic expected outs.
- Adds power-play support via `game_power_play_stats.ppp_hits_saved` because the
  user explicitly wanted defense/PP included in production xO.
- Emits both:
  - pure xO (`pure_xouts`, `pure_xouts_per_27`)
  - supported xO (`xouts`, `xouts_per_27`, `defense_support_xouts`)

### 4. Dead Out Percentage (DO%)

Added `build_dead_outs_table()` for pitchers.

Rules:

- Strikeouts and foul-outs are automatic dead outs.
- Contact outs qualify when they record at least one out, score no run, and do
  not show clear runner advancement.
- Double/triple plays are treated as erasers; a two-out DP contributes **two**
  dead outs and one dead-out PA.

Outputs:

- `dead_outs`
- `dead_out_pas`
- `dead_out_pct`
- `dead_out_pa_pct`

### 5. Hitter Dead Out Avoidance (DOA%)

Added `build_hitter_dead_outs_table()` as the batting-side mirror of pitcher
DO%.

Outputs:

- `dead_outs_bat`
- `dead_out_pas_bat`
- `dead_out_pa_pct_bat`
- `dead_out_avoid_pct`

This lets the game ask both sides of the same question:

- Which pitchers create dead outs?
- Which hitters avoid empty, envelope-burning outs?

### 6. Analytics exports

Updated `o27v2/analytics/__init__.py` to export:

- `build_expected_outs_table`
- `build_dead_outs_table`
- `build_hitter_dead_outs_table`
- `build_pressure_impact`

### 7. Web app wiring

Updated `o27v2/web/app.py` so the new analytics are merged into:

- `/leaders` batting rows (`PAI`, `PAI/PA`, `TRR+`, `DOA%`)
- `/leaders` pitching rows (`xO/27`, `O−xO`, `DO%`, `DO-PA%`, dead outs)
- stats browse batting rows
- stats browse pitching rows
- O27 Index hitter metric rows
- O27 Index pitcher metric rows
- O27 Index home snapshots
- O27 Index leaders page
- O27 Index player-page context

### 8. Templates / UI surfaces

Updated templates:

- `o27v2/web/templates/leaders.html`
  - new Batting · Pressure & Dead-Out Avoidance section
  - new Pitching · Pressure & Expected Outs section
- `o27v2/web/templates/o27i_leaders.html`
  - added pitcher-side sortable O27 Index leaderboard
- `o27v2/web/templates/o27i_player.html`
  - added xO/27, O−xO, DO%, and pure/support xO split
- `o27v2/web/templates/stats_browse.html`
  - added PAI/TRR+/DOA% to advanced batting table
  - added xO/27 and DO% to pitching tables

---

## Why these choices were made

### No DB schema change

The metrics are derived from existing event and stat tables. Persisting them
would add migration and archive complexity before the definitions have had a
full balancing pass. Keeping them computed keeps the implementation reversible
and lets the formulas evolve.

### PAI is run-expectancy-based, not WPA-based

WPA already exists. Rebranding WPA would not answer the cricket prompt. PAI is
anchored to run value and live chase pressure, which better matches True Strike
Rate / Match Impact logic.

### xO includes power-play support, but still exposes pure xO

The user explicitly wanted xO to include defense/PP. The compromise is a
production xO that includes nickel hits saved, while retaining pure xO fields so
future UI can explain how much was pitcher/contact shape versus support.

### DO% counts outs, not just PAs

This was clarified directly by the user: a two-out double play is two dead outs.
The implementation keeps both out-count and PA-count versions.

---

## Validation performed

Commands run during the build:

```bash
python -m py_compile o27v2/analytics/pressure.py o27v2/analytics/expected_woba.py o27v2/analytics/__init__.py o27v2/web/app.py
```

```bash
python - <<'PY'
from o27v2.analytics import build_pressure_impact, build_hitter_dead_outs_table
print(build_pressure_impact.__name__)
print(build_hitter_dead_outs_table.__name__)
PY
```

```bash
python -m pytest o27v2/tests/test_linear_weights.py
```

Result: compile/import checks passed and the targeted linear-weight tests passed.

---

## Known limitations / residual risk

1. **PAI pressure model is a first-pass proxy.** It uses deficit and outs
   remaining to estimate RRR/3O. It does not yet know explicit declared targets,
   batting-order strategy, or manager intent.
2. **Dead-out runner advancement is mask-based.** `game_pa_log` stores base
   occupancy, not runner identity, so advancement detection is necessarily a
   conservative heuristic.
3. **xO support only includes explicit power-play hits saved.** General defense
   is included through league outcome bins and actual event outcomes, but a full
   fielder-specific supported-xO model would need richer attribution.
4. **No season-archive persistence yet.** These are live derived metrics. If the
   definitions settle, archive tables / exports can persist snapshots later.
5. **No full route-render smoke committed as a test.** Manual/import checks were
   run, but a formal Flask render regression test would be a good follow-up.

---

## Recommended follow-ups

1. Add glossary entries for PAI, TRR+, DO%, DOA%, xO/27, pure xO, and supported
   xO.
2. Add a small unit test fixture for:
   - two-out DP → two dead outs
   - one dead-out PA
   - hitter DOA mirror
   - xO pure/support split
3. Add archive/export support once metric definitions stabilize.
4. Add a PAI explanation tooltip/card to O27 Index player pages, mirroring the
   pitcher xO card.
5. Consider a future `Pressure DO%` split once PAI pressure states are trusted.
