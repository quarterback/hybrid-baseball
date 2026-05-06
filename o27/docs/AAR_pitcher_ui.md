# AAR — Pitcher UI Surface Pass (Phase 11c)

**Branch**: `claude/improve-pitcher-system-UiM9b`
**Date**: 2026-05-06
**Goal**: Surface all new pitcher attributes (Phase 11/11b) in the web UI — player scouting card and team roster tab — with no new pages and no buried routes.

---

## What Was Wrong Before

The pitcher scouting card predated the Phase 11 attribute expansion and had two outright correctness bugs:

| Label shown | Field actually read | Correct field |
|---|---|---|
| Movement | `player.speed` | `player.movement` |
| Stamina | `1.0 - player.stay_aggressiveness` | `player.stamina` |

Any pitcher card rendered between Phase 11 merge and this fix showed entirely wrong numbers for both attributes. An analyst looking at movement grades was reading foot speed. Stamina was an inverted batter attribute. Both have been reading garbage data since those fields existed.

The new attributes added in Phase 11/11b — `command`, `grit`, `pitch_variance`, `release_angle`, `repertoire`, `pitcher_archetype` — were never surfaced anywhere in the UI.

The team roster tab showed generic CON / SPD / ARM columns for every player, including pitchers, which mapped to `skill / speed / pitcher_skill` — the batter-centric view.

---

## What Was Done

### 1. Fixed wrong field mappings (player scouting card)

`web/templates/player.html:23–27`

Old block:
```html
<tr><td>Control</td><td>{{ player.skill | to_grade }}</td></tr>
<tr><td>Movement</td><td>{{ player.speed | to_grade }}</td></tr>        {# WRONG #}
<tr><td>Stamina</td><td>{{ (1.0 - player.stay_aggressiveness) | to_grade }}</td></tr>  {# WRONG #}
```

Fixed:
```html
<tr><td>Command</td><td>{{ player.command | to_grade }}</td></tr>
<tr><td>Movement</td><td>{{ player.movement | to_grade }}</td></tr>
<tr><td>Stamina</td><td>{{ player.stamina | to_grade }}</td></tr>
```

### 2. Added new pitcher scouting rows

Four new rows appended to the pitcher branch of the scouting card:

| Label | Source | Notes |
|---|---|---|
| Grit | `player.grit \| to_grade` | Direct — high = fatigue-resistant |
| Consistency | `player.pitch_variance \| pitch_consistency \| to_grade` | Inverted — high = low variance |
| Slot | `player.release_angle \| release_label` | String label, not a grade |
| Archetype | `player.pitcher_archetype \| replace('_',' ') \| title` | Hidden if empty |

### 3. Added `pitch_consistency` filter (`web/app.py`)

```python
@app.template_filter("pitch_consistency")
def pitch_consistency(pitch_variance: float) -> float:
    from o27 import config as _c
    return max(0.0, min(1.0, 1.0 - float(pitch_variance) / _c.PITCH_VARIANCE_MAX))
```

Reads `PITCH_VARIANCE_MAX` from config (currently 0.12) so the divisor stays in sync if the ceiling ever changes. Double-clamped to [0.0, 1.0] so no negative grades if variance somehow exceeds the ceiling.

### 4. Added `release_label` filter (`web/app.py`)

```python
@app.template_filter("release_label")
def release_label(v: float) -> str:
    ...
```

Bands: `0.0–0.25 → Submarine`, `0.25–0.45 → Low Sidearm`, `0.45–0.62 → Sidearm`, `0.62–0.80 → High Sidearm`, `0.80–1.0 → Three-Quarter`.

### 5. Added Repertoire block (player.html)

Below the two-column scouting / stats section, a full-width card listing each `PitchEntry` in `player.repertoire`:

- Pitch name (underscore-to-space, title-cased)
- Quality grade (20–80)
- Proportional CSS bar (140 px max, fills proportionally from grade 20–80)

Hidden entirely when `player.repertoire` is empty (legacy pitchers / position players).

### 6. Pitcher-aware roster columns (team.html)

Roster tab now branches on `p.is_pitcher`:

| Column | Pitcher | Position player |
|---|---|---|
| CON | Stuff (`pitcher_skill`) | Contact (`skill`) |
| SPD | Command | Speed |
| ARM | Movement | Arm (`pitcher_skill`) |

Column header tooltips updated to reflect the dual mapping.

Pitcher archetype badge added next to the pitcher's name (same pattern as joker archetype badges; dim/small, 0.7 opacity).

### 7. `pitcher_archetype: str = ""` added to Player (`engine/state.py`)

Placed alongside existing `archetype` field. Defaulted to `""` so all legacy callers get identity behavior. Wired into `_team_obj()` in `app.py` via `p.get("pitcher_archetype", "")`.

---

## Files Changed

- `o27/engine/state.py` — `pitcher_archetype` field
- `o27/web/app.py` — `pitcher_archetype` wiring; `pitch_consistency` filter; `release_label` filter
- `o27/web/templates/player.html` — fixed Movement/Stamina; added Grit/Consistency/Slot/Archetype rows; added Repertoire block
- `o27/web/templates/team.html` — pitcher-aware CON/SPD/ARM columns; archetype badge

---

## Design Notes

- **No new pages or routes.** Everything is on the existing player card and team roster tab, as specified. Nothing is buried in the sim UI.
- **`pitch_consistency` separates inversion logic from template.** The template no longer embeds arithmetic; the filter owns the math and stays in sync with config.
- **Repertoire block is zero-cost for non-pitchers.** The `{% if player.is_pitcher and player.repertoire %}` guard means batter cards are completely unaffected.
- **The column-header tooltips on team.html (CON / SPD / ARM) now read "Contact / Stuff", "Speed / Command", "Arm / Movement"** — the dual-audience labels are honest about the branching behavior rather than pretending the column is always one thing.

---

## Open Items

- **Batter scouting card attributes** (`skill → Contact`, `contact_quality_threshold → Eye`) are legacy proxies, not the actual `contact`, `power`, `eye` fields added in the realism layer. The batter card has its own version of this bug. Out of scope here but worth a follow-up pass.
- **Release angle tooltip or legend.** The Slot row shows a string ("Low Sidearm") with no explanation of the spectrum. A hover tooltip on the label would help — trivial CSS change.
- **Pitch quality bars assume `var(--accent)` exists in the CSS.** If the stylesheet doesn't define `--accent`, bars will be invisible. Worth a quick style check during QA.
