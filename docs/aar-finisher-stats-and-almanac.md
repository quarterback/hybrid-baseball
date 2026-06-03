# After-Action Report — Finisher stats (TO/QF/LR) + almanac wiring

**Date completed:** 2026-06-03
**Branch:** `claude/friendly-hypatia-ShSHm`

---

## TL;DR

Two pieces:

1. **Finisher counting stats** — Terminal Outs (TO), Quality Finish (QF), and
   Lead-Retention % (LR%) — built on the relief entry-context spine. They
   measure *who seals games* in O27's continuous 27-out half, scaling with the
   "back-stage finisher-starter" strategy rather than assuming a one-inning
   closer.
2. **Almanac wiring** — every new stat from this arc (RISP, bunting, and the
   full relief/finisher suite) now flows through the almanac's own aggregation
   pipeline onto its player pages and leaderboards, matching the live app.

Also fixed a latent bug: the game-end pitching spell flush never recorded
`ir_inherited`/`ir_scored`, so **closers' inherited runners were being
dropped** — now captured (league IR rose 687 → 864 on a 150-game sim).

## Finisher stats — how

The spine is per-spell **entry context** captured in the engine:

- `SpellRecord` gains `entry_lead` (fielding − batting score at entry),
  `min_lead` (running minimum of that lead during the spell), and `finished`
  (on the mound at the end of his defensive half).
- `pa.apply_event` lazily captures the entry lead on a pitcher's first event
  and tracks the running minimum after every event.
- `game._close_spell` flushes the half-finisher with `finished=1`;
  `manager.pitching_change` flushes replaced arms with `finished=0`.

`sim.py` derives per spell, then sums to the game row (season = SUM):
- **Terminal Outs** = outs in a spell entered with a lead (`entry_lead > 0`),
  never relinquished (`min_lead > 0`), and finished.
- **Quality Finish** = finished with ≥ 9 outs while never trailing
  (`min_lead ≥ 0`).
- **lead_entries / lead_held** → **LR%** = lead_held / lead_entries (share of
  lead-entries held on his watch), computed in `_aggregate_pitcher_rows`.

Persisted on `game_pitcher_stats` (+ migration, dedup SQL). Surfaced on the
player-card Relief & Finisher panel, the Stat Browser pitching views, and the
glossary.

### A note on O27's structure (why this is the right model)

O27 is one 27-out half per side, so the team that fields *second* is the one
protecting a lead — all "finishing" is a second-half-fielding phenomenon, and a
back-stage starter who enters with a lead and goes deep racks up TO exactly
like a great closer would. The opening starter enters 0-0 (no lead) and so
earns no TO, which is correct: TO measures closing, not opening.

## Almanac wiring — how

The almanac (`o27/almanac/`) has its **own** aggregation, separate from the
live app's `_aggregate_*`. Wired the new stats through it:

- `compute._BATTING_SUM_FIELDS` += RISP (`risp_*`) and bunting
  (`sh, bunt_att, bunt_hits, sqz, sqz_rbi`); `_PITCHING_SUM_FIELDS` += relief
  (`ir_inherited, ir_scored, terminal_outs, quality_finish, lead_entries,
  lead_held`).
- `_augment_batters` computes the PA-denominated RISP slash + `risp_conv` +
  `bunt_avg`; `_augment_pitchers` computes `ir_stop_pct`, `lr_pct`, and
  `late_er_per_bf`.
- **Player page** (`player.html.j2`): new Clutch · RISP, Small Ball · Bunting,
  and Relief & Finisher sections.
- **Leaderboards**: RISP and Small Ball sections on the Situational board; a
  Relief & Finisher section on the Pitching board.

The almanac reads `game_*_stats` directly (cached by DB mtime), so the new
columns appear after a resim. `gmLI` is intentionally **not** in the almanac
(it needs the WPA model the almanac doesn't run); it lives on the live leaders
+ player card. Team-level RISP/bunting/walkback/usage-shape were left to the
live `/teams/stats` page rather than duplicated on the almanac team page.

## Validation

Fresh 150-game sim:
- Finisher capture invariant-clean: `lead_held ≤ lead_entries` (0 violations);
  TO ≈ 4.8/game, QF ≈ 0.16/game (rare, as a 9+-out finish should be); top-TO
  arms read as finisher-starters (e.g. 54 TO over 81 outs).
- IR fix confirmed: league `ir_inherited` 687 → 864.
- All 1312 almanac player pages render; the RISP / Small Ball / Relief & Finisher
  blocks and the new leaderboard sections all render 200.
- `o27/tests` + `o27/almanac/tests` + records/streaks (158) pass; all 11 stat
  invariants pass on the simmed DB.

**Needs a resim to populate** (sim-time capture), as with any box-stat addition.

## Notable judgment calls / caveats

- The IR "scored" attribution remains the documented heuristic from the prior
  pass (can mis-credit by one on a rare mixed score/out event).
- `min_lead`-based "never relinquished" treats a tie (lead = 0) as relinquished
  for TO (strict lead required) but acceptable for QF (lead-or-tie).
- LR% credits holding the lead **on his watch** (didn't surrender it before
  handing off), not strictly "to the 27th out" — the cleaner skill read and it
  doesn't require finishing.
