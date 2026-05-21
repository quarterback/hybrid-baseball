# After-Action Report — Box Score Pitcher Decisions (W/L) + Seconds-Display Clarification

**Date completed:** 2026-05-21
**Branch:** `claude/fix-seconds-calculation-HszBM`
**PR:** #73
**Commit:** `b1787ef` (per-game W/L tag instead of season totals)

---

## What the user reported

Two things, raised against a single rendered box score (`Isotopes 18, Bisons 16`):

1. **"There's something getting the seconds inning wrong."** The line score showed
   Buffalo with a `-` in the seconds column while Albuquerque had a `1`, and the footer
   read `Seconds: ABQ o26 (17-0).`
2. **"Why is everyone getting credited with a win or loss?"** Nearly every pitcher on
   both staffs rendered with a `(W)` next to their name, plus one stray `(L)` — including
   an `(L)` on the *winning* team's reliever (Spears-Jennings).

---

## Issue 1 — the seconds display was not a bug

Walked the box through with the user. The display was correct:

- Albuquerque (home) declared Seconds at out 26 with the score 17-0 in their favor at that
  moment — i.e. they batted first this game (`home_bats_first` was true), banked their last
  out, and the parenthetical score is stamped *at the moment of declaration*, not at the end.
- Buffalo never declared, so they banked no outs and had nothing to bat with in the seconds
  round. That is why their seconds-column cell is `-`.
- Albuquerque used their one banked out in the seconds round, scored 1, and won 18-16.

The line score collapses each team's runs across phases, so both teams appear in column "1"
(regulation) regardless of who batted first; the `-` correctly reads as "did not bat in this
round." The user confirmed they had simply misread "Buffalo didn't bat" as an error — it was
the rule working as designed. **No code change for this.**

The Declared Seconds mechanic had no standalone documentation, so a companion AAR
(`docs/aar-declared-seconds-rule.md`) was written to capture the rule end-to-end.

## Issue 2 — pitcher W/L decisions were reading season totals

This was a real bug.

The box-score decisions map (`o27v2/web/app.py`, in `game_detail`) was built like this:

```python
for prow in away_pitching_consolidated + home_pitching_consolidated:
    if (prow.get("w") or 0) > 0:
        _decisions[pid] = "W"
    elif (prow.get("l") or 0) > 0:
        _decisions[pid] = "L"
    elif (prow.get("sv") or 0) > 0:
        _decisions[pid] = "S"
```

The trap: those `w` / `l` fields are **career season totals**, not the per-game decision.
They are written onto each consolidated row by `_aggregate_pitcher_rows`
(`o27v2/web/app.py:1995-1999`), which copies them out of the season W/L map
(`_pitcher_wl_map()`):

```python
if wl is not None:
    d = wl.get(pid, {"w": 0, "l": 0})
    p["w"] = d["w"]
    p["l"] = d["l"]
```

So any pitcher with even one win on the season rendered `(W)` in *every* box score, and any
pitcher with a season loss could render `(L)` — which is exactly why a winning-team reliever
showed up tagged with the loss.

### The fix

A per-game decision must be derived from *this game's* result, not lifetime stats. The
plaintext renderer in `o27v2/web/box_text.py` already had the right heuristic
(`_pick_decisions`), so the HTML path was brought in line with it:

- **W** → the last pitcher (by appearance/row order) on the **winning** side who actually
  recorded an out.
- **L** → the pitcher on the **losing** side with the most runs allowed (must be > 0).

```python
_winner_id = game.get("winner_id")
if _winner_id is not None:
    if _winner_id == game.get("away_team_id"):
        _win_rows, _lose_rows = away_pitching_consolidated, home_pitching_consolidated
    elif _winner_id == game.get("home_team_id"):
        _win_rows, _lose_rows = home_pitching_consolidated, away_pitching_consolidated
    else:
        _win_rows, _lose_rows = [], []
    for r in reversed(_win_rows):
        if (r.get("outs_recorded") or 0) > 0:
            _decisions[r["player_id"]] = "W"
            break
    if _lose_rows:
        _worst = max(_lose_rows, key=lambda r: (r.get("runs_allowed") or 0))
        if (_worst.get("runs_allowed") or 0) > 0:
            _decisions[_worst["player_id"]] = "L"
```

Result: at most one `(W)` (on the winning side) and one `(L)` (on the losing side) per box,
which is the correct shape.

### Note on saves

The previous code also assigned `(S)` from a `sv` field. The new heuristic drops the save tag
— `box_text.py`'s `_pick_decisions` doesn't compute one either, and there's no reliable
per-game save signal on the consolidated rows (the `sv` field, like `w`/`l`, would have been a
season total). If a per-game save is wanted later it needs a real lead-protection rule
(entered with the tying run not on base / at bat, finished the game, recorded ≥ some outs),
not a season-total read.

---

## Files changed

- `o27v2/web/app.py` — `game_detail`'s `_decisions` map: replaced the season-total read with
  the per-game W/L heuristic (mirrors `box_text._pick_decisions`).

---

## Things to remember

- **`prow["w"]` / `prow["l"]` on a pitcher row are season totals, not the game decision.**
  `_aggregate_pitcher_rows` decorates every row it touches with the lifetime W/L from
  `_pitcher_wl_map()`. Any per-game W/L/S display must be derived from the game's `winner_id`
  and the per-appearance lines, never from those fields.
- There are two box-score renderers — the HTML/`<pre>` path (`box_score.py`, driven by the
  `decisions` dict assembled in `app.py`) and the plaintext path (`box_text.py`, which computes
  decisions inline via `_pick_decisions`). They share the same heuristic now; keep them in sync.
- Pitcher decisions are heuristic, not engine-tracked. The engine doesn't stamp a pitcher of
  record, so "last winning-side pitcher with an out" + "worst losing-side run total" is the
  best approximation available without a data-model change.
