# After-Action Report — Org Strength: from a stale random-walk to a live roster grade

**Date completed:** 2026-06-05
**Branch:** `claude/player-value-calculation-YjTrP`

The team page's **ORG STRENGTH** tile was meaningless. A 22–11 (.666) Orioles
club was showing `27 · Sub-Repl` — a sub-replacement grade for a first-place
team. The user's read was correct: "as it's used right now it's useless since
it doesn't update via roster strength in real time."

## What it actually was

Digging in, it was worse than stale — it was never roster-derived at all:

- At league seed the column is set by `_roll_org_grade()` — a **random** roll
  (`league.py:3101`). The comment at `league.py:3305-3307` says so outright:
  "*`teams.org_strength` … is NOT recomputed from the drafted roster.*"
- `_team_org_strength_from_roster()` existed but was **dead code** — nothing
  called it.
- Each offseason the column random-walks via a bond-market formula on win%
  (`_roll_new_org_strength`, `development.py`).

So the displayed number was a random seed that drifted on win-loss record,
disconnected from the roster it claimed to grade.

## The wrinkle: the column has a second, legitimate job

`org_strength` isn't *only* a display value. The persisted column is read as a
front-office/AI knob by:

- **`auction.py`** — bidding *discipline* (`_team_auction_profile`): a high-org
  team identifies true value, a low-org team disagrees with the market. This is
  read **before rosters exist** (the auction is what fills them), so it can't be
  roster-derived.
- **`fa_signing.py`** and **`post_auction_trades.py`** — FA/trade behaviour.

That's a real, separate concept ("front-office acumen") that happens to share a
column name with "how good is this roster." Conflating them is what made the
displayed number nonsense.

## What was asked for

Two choices, both put to the user:

- **Proxy → "Roster + bench depth."** The grade should reflect the active
  roster's talent, with a lighter-weighted contribution from non-active bench
  depth, recomputed live.
- **Development → "Unify."** The same live value should drive player
  development (μ_org), retiring the persisted column's dev role. One coherent
  number.

## What changed

The fix splits the two concepts cleanly:

1. **New `league.compute_org_strength(players)`** — a live 20–95 grade: the mean
   `_player_overall` of the active roster, blended `0.80 / 0.20` with the mean
   of the non-active bench (`_ORG_BENCH_WEIGHT`). Graceful with no bench
   (→ pure active mean) and empty rosters (→ 50). Replaces the dead
   `_team_org_strength_from_roster`.

2. **Team page (`app.py` `team_detail`, `team.html`)** — the tile now renders
   `compute_org_strength(roster)`, recomputed on every load, so trades and
   call-ups move it immediately. The tooltip now explains what it is and notes
   it drives development. The persisted column is no longer displayed.

3. **Development (`development.py`)** — `develop_players_for_team` now derives
   org strength live from its already-loaded roster when called with
   `org_strength=None` (the offseason path), so μ_org is fed the same grade the
   page shows. The `int` parameter is retained as a test override.

4. **The persisted column is left to its front-office life.** `update_org_strengths`
   still rolls it forward on win% for the auction/FA/trade AI — that behaviour
   is intentionally unchanged, since it's a pre-roster bidding knob, not a
   talent readout. Comments at both sites now spell out the split.

## Correcting an earlier claim

In the explanation that preceded this work I told the user development was org
strength's "one real mechanical job." That was wrong — it also feeds auction
bidding, FA signing, and trades. Those usages are why the column survives rather
than being deleted; only its *display* and *development* roles moved to the live
grade.

## Validation

- No `pytest`/DB/Flask in this sandbox. `py_compile` clean on `league.py`,
  `development.py`, `app.py`; `team.html` parses through a bare Jinja
  `Environment`.
- Unit-checked `compute_org_strength` directly: strong active-only roster → 80;
  same roster + weak bench → 70 (depth drags it down); thin weak roster → 38
  ("Replacement"); empty → 50; all-elite clamps at 95.
- **Not** run: full engine/web suites (need a DB). The change is confined to one
  new pure function, one display var, and the offseason dev wiring — no schema
  change, no auction/FA/trade behaviour touched.

## Follow-ups not taken (out of scope)

- The offseason report (`run_offseason` → `org_moves`) still surfaces the
  persisted column's bond-market deltas. Those now describe the front-office
  knob, not the displayed grade — worth relabeling if that report is
  user-facing, but left alone here.
- The persisted column could itself be retired and the auction/FA/trade AI
  rebased onto a different "front-office" signal, but that changes competitive
  balance and wasn't requested.
