# After-Action Report — Substitution tags stripped by `_stat_delta`

**Date completed:** 2026-05-19
**Branch:** `claude/normalize-game-scores-3AXlu`
**PRs:** #74 → #78 → #79 → #80
**Final commit:** `7952fec`

---

## TL;DR

After three rounds of "fixing" the substitution rendering pipeline,
box scores for fresh post-merge games STILL showed:

- Jokers mid-lineup instead of trailing as a tactical pool
- Duplicate-position rows where DEF subs should have been indented
- All-zero "phantom starter" rows where defensive replacements landed
- Zero `a-`/`b-`/`c-` footnote letters anywhere

The actual root cause was one layer deeper than the bug we kept
re-finding: `Renderer._stat_delta()` constructed the per-phase delta
row that `o27v2/sim.py` persists to SQLite, and silently dropped the
three identity tags (`entry_type`, `replaced_player_id`,
`entered_inning`) every time. The engine produced correct stamps in
memory, the renderer correctly transferred them to the cumulative
`BatterStats` dict, but the final hop from cumulative-bstat to
per-phase-delta zeroed them back to dataclass defaults. By the time
SQLite saw the row, the tag was gone.

The fix is three lines in one function. Finding it took unwinding two
earlier "fixes" that each closed a real bug but couldn't possibly
have surfaced this one.

---

## The bug chain

This subsystem had three layered bugs, each masking the one below.

### Layer 1 — Renderer key-mismatch (shipped as `fec36ab` on main, also `a185154` on this branch)

`render_event()` receives the **provider's** event dict from
`prob.py`, which carries Player objects under `player_in` /
`player_out` / `runner_in` / `joker`. The four substitution handlers
in `render.py` were reading keys (`in_id`, `out_id`, `joker_id`) that
only exist on the **manager's** state.events entry, which the
manager-side functions append AFTER firing. Result: `pinch_hit`
worked by accident (its provider key `replacement` happened to match
what the handler read), but `defensive_sub` / `pinch_runner` /
`joker_to_field` silently no-op'd in the renderer.

Diagnostic that surfaced it: a 30-game synthetic sim with
`should_defensive_sub` instrumented showed 59 fires → 0 DEF rows
landing in `Renderer._batter_stats`.

**Status:** real bug, real fix, doesn't get all the way to the
displayed symptom.

### Layer 2 — Legacy joker rows had `entry_type='starter'` (shipped as `0cd078d`)

Pre-Layer-1 simulations persisted joker insertions with
`entry_type='starter'` because the handler never fired. The
underlying signal (which player got inserted as a joker) was lost,
but it's recoverable for jokers specifically because
`players.is_joker` is a permanent identity flag — every joker
insertion necessarily landed on an `is_joker=1` player. Added a
SQL-level fallback to `web/app.py` so the box-score SELECT now
returns `entry_type='joker'` for any row where `p.is_joker=1`,
regardless of what's stamped in `game_batter_stats`.

**Status:** real bug, real fix, makes old games partially recover
(joker grouping at the trailing end of the batting block), but
non-joker subs in old games stay broken because there's no
permanent-identity signal to fall back on.

### Layer 3 — `_stat_delta` strips identity tags (shipped as `7952fec`, the actual root cause)

Even with the Layer-1 fix in main, the user reported fresh post-merge
games STILL showing all the original symptoms. The renderer was
stamping `entry_type='DEF'` etc. correctly on the in-memory
cumulative bstat, but the rows landing in `game_batter_stats` had
`entry_type='starter'` on every sub.

`o27v2/sim.py:758` reads from `renderer.batter_stats_for_phase(phase)`
— per-phase **delta** rows, not the cumulative dict. The delta is
constructed by `Renderer._stat_delta(end_s, prev_s)` at
`render.py:1665`:

```python
def _stat_delta(end_s: BatterStats, prev_s: Optional[BatterStats]) -> BatterStats:
    d = BatterStats(player_id=end_s.player_id, name=end_s.name)
    prev_get = (lambda f: getattr(prev_s, f)) if prev_s else (lambda f: 0)
    for f in ("pa", "ab", "runs", "hits", ...):     # counters only
        setattr(d, f, getattr(end_s, f) - prev_get(f))
    return d
```

`BatterStats` has three identity fields (`entry_type`,
`replaced_player_id`, `entered_inning`) that are set ONCE when a
player enters the game and never incremented. `_stat_delta` only
diffs the counter fields. The identity fields stayed at the
dataclass defaults (`'starter'`, `''`, `0`) on every delta row that
went out to SQLite.

Net effect: every PH / PR / DEF / joker stamp the engine produced
was lost between the renderer's in-memory state and the persistence
layer. From the user's perspective, the engine was producing flat
box scores because the *file format* between the renderer and the
DB writer was lossy.

**Fix:** three lines at the top of `_stat_delta`:

```python
d.entry_type         = end_s.entry_type
d.replaced_player_id = end_s.replaced_player_id
d.entered_inning     = end_s.entered_inning
```

Identity tags propagate as-is. They're static flags; subtracting
them makes no semantic sense.

---

## Why two rounds of "fixing" didn't catch the third bug

Layer 1 was diagnosed by instrumenting `should_*` call counts vs.
events landing in `Renderer._batter_stats` (the cumulative dict).
The instrumentation confirmed the fix worked: 168 PH / 4 PR / 54 DEF
events all landing in the cumulative dict with correct `entry_type`.
That's a valid measurement — but it stops one layer short of the
DB.

The diagnostic I should have run instead — and didn't, until the
user reported a third-round failure — was:

```python
phase0 = renderer.batter_stats_for_phase(0)
for pid, b in phase0.items():
    if b.entry_type != 'starter':
        print(b)
```

If I'd done that initially, the missing rows in the per-phase delta
would have surfaced immediately. The cumulative dict had the tags;
the per-phase delta dict had everything except the tags. That gap
WAS the bug.

The general shape: **when a feature crosses an in-memory → derived
→ persistence boundary, instrumenting any single layer isn't enough**.
The renderer's cumulative dict is one layer; the per-phase delta is
a derived second layer; the SQLite row is a third. Each
transformation is a place where data can be lost. I tested layer 1
and assumed layers 2 and 3 were pass-throughs. They weren't.

Going forward, the cheap diagnostic for any sim-to-DB feature is:

```
sim → renderer cumulative → renderer per-phase delta → DB row
```

— sample one player at each of the four points and check the field
of interest. Three lookups, deterministic, takes 60 seconds.

---

## Status

- **Engine and renderer:** producing correct tags end-to-end as of
  `7952fec`. 30-game synthetic sim: 5 jokers, 2 DEF subs, 1 PH —
  all stamped properly, all `entered_inning` populated, all
  surviving the per-phase delta hop.
- **107 tests green** including the new
  `test_phase_delta_preserves_entry_type_tags` regression test that
  asserts a `BatterStats` with `entry_type='joker'`,
  `replaced_player_id='17'`, `entered_inning=5` round-trips through
  `_stat_delta` with the tags intact.
- **Old games:** unrecoverable. Once a sub event fires without
  stamping `entry_type`, the DB row has no signal to reconstruct it
  from (except for jokers, via the Layer-2 `is_joker` fallback).
  Re-simulating affected games is the only way to recover their
  sub indentation, ph/pr labels, and footnotes.

## Recommendation

If presentation fidelity on early-season box scores matters: wipe
the `played` flag on games from the affected window and re-run the
schedule. The engine itself is deterministic by seed (the seed is
stored on each game row), so the replayed games will be
statistically identical to the originals — only the sub
presentation will change.

If presentation fidelity on those games doesn't matter: do nothing.
All games simulated from `7952fec` forward will render correctly.

---

## Process lesson

This is the third time in this branch that the **right** instrument
caught a bug the **almost-right** instrument missed:

1. The renderer key-mismatch was invisible until I counted
   `should_*` fires vs. cumulative-dict rows (not vs. event-log
   appearances).
2. The legacy-data joker issue was invisible until the user pasted a
   post-fix box score where jokers were still mid-lineup (the
   in-memory test couldn't reproduce because synthetic games have no
   legacy data).
3. The `_stat_delta` bug was invisible until the user pasted a
   post-Layer-1-fix box score and I finally inspected
   `batter_stats_for_phase()` instead of `_batter_stats`.

The pattern in all three: I instrumented the layer I was actively
editing, declared it fixed when that layer behaved, and shipped.
The bug was always in the next layer downstream that I hadn't
looked at because I had no reason to suspect it.

The right discipline for a multi-layer pipeline is to **sample the
field of interest at every layer between source and destination**,
even when each layer's code looks like an obvious pass-through. The
"obvious" ones are exactly where lossy transforms hide, because
nobody bothers to verify them.
