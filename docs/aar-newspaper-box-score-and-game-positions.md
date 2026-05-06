# After-Action Report — Box Score Reformatting + Per-Game Fielding Positions

**Date completed:** 2026-05-06
**Branch:** `claude/decouple-hits-runs-rGteJ`
**Commits:** `b8bffbf` (positions + plaintext box score), `50d6f77` (PH/sub indent + season HR)

---

## What was asked for

Five user messages over the course of this work, each one peeling back another layer of the rendering problem:

> "Why arn't all 9 fielders (pitcher included) not represented in box scores, then DH separately (jokers) so I know who started and then any pinch hitters or defensjve subs. You need to explore how a baseball box score is designed."

> "Nobody should ever be UT in the box score in real life a box score transmits all the info who played where if yoh moved positions jt can show that too"

> "Jokers should be J not DH every team has just 3 you cannot sub a joker for another guy only fielders jokers can play in the field to replace someone but you only then have 2 or 1 jokers. This should be a very rare thing"

> "I'll search for current best practices on baseball box score formatting and Python implementation patterns. Now I have enough context. Here's a directive for an LLM agent on how to build a proper O27 box score in a Python sim ..."  *(followed by a detailed spec for monospace-plaintext newspaper-style rendering)*

> "This box score is rendering as separate HTML tables stacked vertically — three different tables ... Mobile-responsive UI design language, not newspaper box score language. ..."

Two distinct asks emerged:

1. **The data model is wrong.** Box scores show "UT" for half the lineup because the engine never assigns concrete fielding positions to utility players at game time. They keep their static "UT" tag every game.
2. **The presentation is wrong.** Three styled HTML tables stacked vertically with cream-banded title bars is a database-dump UI, not a box score. The reference target is a 1990s newspaper box score: monospace plaintext, dot leaders, no internal grid lines, sections separated by whitespace.

---

## Diagnosis

### Data model

The screenshot showed positions like `SS, RF, LF, UT, CF, UT, C, 1B, P, UT, DH` — three "UT" entries among the 11 batters. Tracing back through the box-score query in `o27v2/web/app.py:1387`:

```sql
SELECT bs.*, p.name as player_name,
       CASE WHEN p.is_joker = 1 THEN 'J' ELSE p.position END as position
FROM game_batter_stats bs JOIN players p ON bs.player_id = p.id
```

The `position` column comes straight off `players.position` — the player's *primary* position. There's no per-game "this player played CF tonight" record. Utility players whose primary position is `UT` keep that tag in every box score they appear in, even though in any given game they took a specific defensive slot.

The engine was already doing the position selection implicitly (it knows which 8 fielders are starting; the manager AI knows where they line up); it just wasn't *recording* the assignment. The fix: assign concrete slots at lineup-build time and persist per-game.

### Presentation

The existing `o27v2/web/templates/game.html` was three Bootstrap-styled `<table class="table table-sm dense-table">` blocks per phase, plus a "Game Totals" consolidated section, plus a "Notable Plays" card, plus a styled scoreboard panel at the top. The styling was clean — no gridlines, no row striping — but the *structure* was wrong. The user's diagnosis was correct: it was a database dump rendered with table primitives, not a box score format rendered as plaintext.

The user pasted a comprehensive spec referencing Henry Chadwick (the inventor of the modern box score in 1858) and the typographic conventions that became the standard format through the 20th century. Key points:

- **Single `<pre>` block** of monospace plaintext.
- **Dot leaders** connecting names to stats — the iconic visual element.
- **Fixed column widths**: name+pos = 22 chars, stats = 4 chars right-aligned.
- **No HTML tables, no markdown tables, no grid lines, no row striping.**
- **Sections separated by blank lines**, not by chrome.
- **Pitcher decision (W/L/S) inline with name**, not a separate column.
- **Annotations indented 2 spaces** following each batting block: `2B:`, `HR:`, `SB:`, `E:`.
- **HR notation includes season total in parens**: `Smith 2 (12)` = "hit two HR; season total stands at 12."
- **Adapt to O27**: REG / SI1 / SI2... phase columns on the line score, OS% on pitching, 2C column on batting.

---

## What was built (commit `b8bffbf`)

### 1. Per-game fielding positions

`o27/engine/state.py` — new field on `Player`:

```python
# Per-game fielding position. Assigned at game start by the lineup
# builder so that even utility players (`position == "UT"`) get pinned
# to a concrete defensive spot for that day's box score. Mid-game
# defensive moves can extend the string (e.g., "SS-2B" for a player
# who started at SS and moved to 2B). Defaults to "" — the box-score
# renderer falls back to `position` when this is empty.
game_position: str = ""
```

`o27v2/sim.py` — new helper `_assign_game_positions(starters, sp, dhs)` called from `_db_team_to_engine` right after the starting fielders are picked. The algorithm:

- Players whose static `position` is one of the canonical 8 (`C/1B/2B/3B/SS/LF/CF/RF`) keep it. First-come wins if two players have the same primary position.
- Remaining players are placed via greedy best-fit against open slots:
  - Infield slots (`1B/2B/3B/SS`) score against `defense_infield`.
  - Outfield slots (`LF/CF/RF`) score against `defense_outfield`.
  - Catcher slot scores against `defense_catcher`.
  - At each step the highest-scoring (player, slot) pair wins.
- The SP gets `"P"`. Jokers (the 3 DH-pool tactical pinch-hitters every team carries) get `"J"` — explicitly *not* `"DH"`, per the user's clarification that jokers and DHs are different concepts in O27.

This means a utility player with strong infield ratings will land on whichever of `1B/2B/3B/SS` is open after the canonical-position players are slotted; an outfield-leaning utility lands in the outfield. The engine no longer punts.

### 2. Persistence

`o27v2/db.py` — new column with migration:

```sql
game_position TEXT DEFAULT '',
```

```python
try:
    conn.execute(
        "ALTER TABLE game_batter_stats ADD COLUMN game_position TEXT DEFAULT ''")
    conn.commit()
except Exception:
    pass
```

`o27v2/sim.py` — `_extract_batter_stats` now reads the engine player's `game_position` and includes it in the row dict. Both INSERT paths (the inline one in `play_game` and the helper `_insert_batter_stats`) updated to write the column.

`o27v2/web/app.py` — the box-score query now exposes `box_position` with fallback:

```sql
COALESCE(NULLIF(bs.game_position, ''),
         CASE WHEN p.is_joker = 1 THEN 'J' ELSE p.position END)
  AS box_position
```

So new games render with the per-game position; legacy rows fall back to the player's primary position. (Legacy games with utility players will still show "UT" until they're re-simmed; new games render correctly.)

### 3. Newspaper-style plaintext renderer

New file `o27v2/web/box_score.py`. Pure rendering — no DB access; takes already-built row sets and emits a single string. Sections in order:

1. **Top rule** — `=` × 78 chars.
2. **Title line** — `BOS 12, AZ 11                                                 2026-05-06 · #42`. Score in the title; date and game ID right-aligned to the rule width.
3. **Top rule** again.
4. **Line score** — `REG R H E` columns (or `REG SI1 SI2 ... R H E` for super-inning games), one row per team.
5. **Visiting team batting block** — `RED SOX` as a single caps line, then the column header, then player rows with dot leaders, then a `Totals` row.
6. **Visiting team annotations** — `2B:`, `3B:`, `HR:`, `SB:`, `CS:`, `E:`, `HBP:` lines, indented 2 spaces, period-terminated, comma-separated names.
7. **Home team batting block** + annotations.
8. **Visiting + Home pitching tables** — same format, with W/L/S inline by name.
9. **Game notes** — weather, seed, super-innings.
10. **Bottom rule.**

Column widths driven by module-level constants:

```python
NAME_POS_WIDTH = 22   # "Lastname  pos" + dot leaders
STAT_W = 4            # each batting stat column
PIT_STAT_W = 5        # each pitching stat column
RULE_WIDTH = 78
```

Helpers (`_last_name`, `_pos_short`, `_name_pos_with_dots`, `_rj`, `_rate`) handle the typographic plumbing. `_rj` is right-justify; `_rate` formats H/AB as `.000` to 3 decimals (or above 1.000 if 2C events produced multi-hit ABs — the H/AB column is intentionally unbounded).

### 4. Template rewrite

`o27v2/web/templates/game.html` reduced from 472 lines to 228. Removed entirely:

- The styled scoreboard panel.
- The styled line-score `<table>`.
- The per-phase batting + pitching `<table>` blocks (one set per phase).
- The "Game Totals" consolidated `<table>` blocks.
- The "Notable Plays" card.

Replaced with a single `<pre>` block:

```html
<pre style="font-family:'Courier New',Consolas,Menlo,monospace;
            font-size:13px; line-height:1.35; white-space:pre;
            color:var(--fg, inherit); background:transparent;
            padding:1rem; margin:0; overflow-x:auto;">{{ box_score_text }}</pre>
```

The Bootstrap nav strip (Prev/Next, JSON export) above the box score is preserved — the box score is the page content, not the entire page chrome.

### 5. App wiring

`o27v2/web/app.py:game_detail` calls `box_score.render_box_score(...)` with the consolidated row sets and the line-score totals already computed for the existing template. Builds a decisions dict (`{pitcher_id: "W"|"L"|"S"}`) from the W/L/SV columns the aggregator already produces.

### Sample output

```
==============================================================================
BOS 12, AZ 11                                                 2026-05-06 · #42
==============================================================================

                    REG    R    H    E
Red Sox              12   12   16    2
Diamondbacks         11   11    9    1

RED SOX
                        AB   R   H  2B  3B  HR RBI  BB   K  2C   H/AB
Biggio      ss ......    5   2   3   0   0   0   1   0   1   0  0.600
Edwards     rf ......    1   1   1   0   0   0   1   0   1   0  1.000
Barrera     lf ......    5   1   2   0   0   0   1   0   1   0  0.400
Lopez       2b ......    5   1   2   1   0   0   1   0   1   0  0.400
Richardson  cf ......    4   1   1   0   0   0   1   1   1   0  0.250
Fletcher    3b ......    5   1   1   1   0   0   1   0   1   0  0.200
Gomez       c  ......    2   1   0   0   0   0   1   1   1   0  0.000
Logan       1b ......    4   1   2   0   0   0   1   1   1   0  0.500
Luna        p  ......    3   2   3   0   0   0   1   2   1   0  1.000
York        j  ......    5   1   1   0   0   0   1   1   1   0  0.200
Hart        j  ......    1   0   0   0   0   0   1   0   1   0  0.000
Totals                  40  12  16   2   0   0  11   6  11   0  0.400
  2B: Lopez, Fletcher.

[…]

RED SOX PITCHING
                         BF  OUT  OS%    H    R   ER   BB    K   HR    P
Luna (W) ............    13    9  33%    5    2    2    2    4    1   80
```

Every position is concrete. No "UT" anywhere. Jokers carry the `j` tag. Pitcher's W is inline with the name. Dot leaders connect names to stats. Stats columns are right-aligned and locked in place — every row aligns to the row above.

---

## Follow-up — PH/sub indent + season HR totals (commit `50d6f77`)

Two pieces from the spec that didn't make the first cut:

> "Pinch hitters and defensive subs should be indented under the player they replaced."
>
> "HR notation includes season total in parentheses."

### Capturing pinch hits

The engine emits `pinch_hit` events through `prob.py` → `pa.py` → `manager.pinch_hit(state, replacement)`. The render layer already had a `pinch_hit` handler at `render.py:762`; previously it just stamped a display name. Now it also marks the replacement on `BatterStats`:

```python
elif etype == "pinch_hit":
    replacement = event.get("replacement")
    d["replacement_name"] = replacement.name if replacement else "?"
    replaced = ctx.get("batter")
    if replacement is not None:
        rs = self._get_stats(replacement)
        rs.entry_type = "PH"
        if replaced is not None:
            rs.replaced_player_id = str(replaced.player_id)

elif etype == "joker_inserted":
    joker_id = event.get("joker_id")
    joker_name = event.get("joker_name", "")
    if joker_id and joker_id in self._batter_stats:
        self._batter_stats[joker_id].entry_type = "joker"
    elif joker_id:
        self._batter_stats[joker_id] = BatterStats(
            player_id=str(joker_id), name=joker_name, entry_type="joker"
        )
```

`BatterStats` got two new fields:

```python
entry_type: str = "starter"   # starter | PH | sub | joker
replaced_player_id: str = ""
```

Persisted via two new `game_batter_stats` columns (`entry_type TEXT`, `replaced_player_id INTEGER`) with `ALTER TABLE` migrations. Read back via the box-score SELECT.

### Indent ordering

`o27v2/web/box_score.py:_ordered_rows_with_indent` orders the batting rows for newspaper display:

- **Starters** in their original game-row order (lineup order, since rows are inserted in batting-order).
- **PH/sub rows** indented 2 chars and placed *immediately after* the starter they replaced. The position column for a PH row reads `ph` instead of the fielding slot they didn't actually take.
- **Jokers** trail at the end, un-indented, with position `j`.

`_name_pos_with_dots` got an `indent` parameter that shifts the name right by N spaces while keeping total prefix width at `NAME_POS_WIDTH`. The dot-leader columns and stat columns still align perfectly under starter rows:

```
Biggio      ss ......    5   2   3   0   0   0   1   0   1   0  0.600
Lopez       2b ......    3   1   1   1   0   0   1   0   1   0  0.333
  Roberts   ph ......    1   1   1   0   0   1   2   0   1   0  1.000
Payne       1b ......    4   1   3   0   0   1   3   0   1   0  0.750
McJoke      j  ......    1   0   0   0   0   0   1   0   1   0  0.000
```

The `Roberts ph` row is a pinch hitter who came in for `Lopez 2b`; the indent visually telegraphs the substitution.

### Season HR totals

`o27v2/web/app.py:game_detail` runs one cheap query per side before rendering:

```sql
SELECT bs.player_id AS pid, SUM(bs.hr) AS hr_total
FROM game_batter_stats bs JOIN games g ON bs.game_id = g.id
WHERE bs.team_id = ?
  AND (g.game_date < ? OR (g.game_date = ? AND g.id <= ?))
GROUP BY bs.player_id
```

Result is keyed by player_id and stamped onto each consolidated batting row as `season_hr`. The annotations renderer then formats:

```python
if n > 1:
    hr_items.append(f"{last} {n} ({season})")   # "Smith 2 (12)"
else:
    hr_items.append(f"{last} ({season})")        # "Lopez (4)"
```

Producing newspaper-correct output:

```
  HR: Roberts (12), Payne (8).
```

`Roberts (12)` = "Roberts hit a HR; that was his 12th of the season." `Payne (8)` = same form. If a player went deep multiple times in this game, the day's count appears between the name and the parenthesized season total: `Smith 2 (12)`.

---

## What's still rough

A few items from the spec that aren't shipped on this branch:

- **Defensive subs** — the engine's `pinch_hit` event carries dual-use semantics (offensive PH and defensive sub use the same event type). Right now everything that flows through it is tagged `entry_type="PH"`. A proper "PH" vs "DEF" split would need either a flag on the event or a separate event type. Punt.
- **Pinch runners** — the engine doesn't currently emit a pinch-runner event. Would need engine support, then the same render plumbing pattern.
- **Joker-into-the-field as defensive sub** — when a joker enters the field to replace a fielder mid-game, the spec calls for that to show as `"J→SS"` or similar in the position column, with the joker count visibly dropping from 3 → 2. The plumbing exists (`game_position` is a free-form string that can extend), but the trigger event isn't being captured into the stats layer. Punt.
- **GIDP annotation** — DPs are now tracked at the engine level (per the earlier hits/runs decoupling commits), but they're not surfaced in the box-score annotations yet. Easy follow-up.
- **LOB-RISP** — left on base with runners in scoring position. Not currently tracked.

---

## Identity / regression check

`tests/test_realism_identity.py` still passes 6/6 across both commits. None of the rendering or schema changes touch the realism axes the identity contract pins.

---

## Files touched

**Commit `b8bffbf` (positions + plaintext box score):**

- `o27/engine/state.py` — added `game_position` field on `Player`.
- `o27v2/sim.py` — `_assign_game_positions` helper; called from `_db_team_to_engine`; persisted via the bstat extract dict and the two INSERT paths.
- `o27v2/db.py` — `game_position TEXT` column on `game_batter_stats` with `ALTER TABLE` migration.
- `o27v2/web/box_score.py` — *new file*. The whole renderer (header, line score, batting + annotations, pitching, game notes).
- `o27v2/web/app.py` — query exposes `box_position`; route calls the renderer; `box_score_text` passed to template.
- `o27v2/web/templates/game.html` — reduced from 472 → 228 lines; replaced HTML tables with a single `<pre>` block.

Total diff: 487 insertions, 262 deletions across 6 files.

**Commit `50d6f77` (PH/sub indent + season HR):**

- `o27/stats/batter.py` — `entry_type` and `replaced_player_id` fields on `BatterStats`.
- `o27/render/render.py` — capture `pinch_hit` and `joker_inserted` events; mark BatterStats accordingly.
- `o27v2/db.py` — `entry_type` and `replaced_player_id` columns + migrations.
- `o27v2/sim.py` — write the new fields from `_extract_batter_stats`; INSERT path updated.
- `o27v2/web/app.py` — `_season_hr_through` per-team query; `season_hr` stamped on each row; SELECT exposes `entry_type` / `replaced_player_id`.
- `o27v2/web/box_score.py` — `_ordered_rows_with_indent` ordering; `_name_pos_with_dots` accepts `indent`; HR annotation reads `season_hr`.

Total diff: 160 insertions, 13 deletions across 6 files.
