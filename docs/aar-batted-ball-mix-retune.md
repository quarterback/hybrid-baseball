# After-Action Report — Batted-ball mix retune (more air, ground/liner swap)

**Date completed:** 2026-06-23
**Branch:** `claude/vigilant-davinci-hn34xy`

---

## What was asked for

A live-feel retune of the batted-ball type distribution, in three quick steps
the user dialed in by watching the resulting mix:

1. > "fly ball rate should be higher too"
2. > "and can tweak ground ball up a bit more too"
3. > "lower the liner number down to around .39 for liners" → then, after seeing
   >  the mix, > "ground balls could swap with liners even"

The starting point was a **liner-dominant** distribution (the resolver's
`RES_TEXTURE_WEIGHTS` put 0.53 of medium/hard contact on liners). The end goal:
a more varied air/ground mix where fly balls and ground balls are both prominent
and liners are no longer the runaway plurality.

## The lever

`o27/config.py :: RES_TEXTURE_WEIGHTS` — the resolver's per-contact-quality
`(dribbler, grounder, liner, flyball)` mix, consumed by
`o27/engine/batted_ball.py :: _roll_texture`. This is the ground-truth
batted-ball-type generator (the LA bands then map within each texture), so it is
the direct knob for the league's air/ground frequencies. Each row sums to 1.0.

| contact | dribbler | grounder | liner | flyball |
| --- | --- | --- | --- | --- |
| weak (was) | 0.22 | 0.34 | 0.34 | 0.10 |
| **weak (now)** | 0.20 | **0.46** | **0.18** | **0.16** |
| medium (was) | 0.05 | 0.22 | 0.53 | 0.20 |
| **medium (now)** | 0.04 | **0.38** | **0.27** | **0.31** |
| hard (was) | 0.0 | 0.06 | 0.53 | 0.41 |
| **hard (now)** | 0.0 | **0.22** | **0.27** | **0.51** |

Net: fly weight up across the board (the step-1 ask), grounder weight up (step 2),
and liner pulled down out of dominance and below the grounder share (step 3 — the
"swap").

## Result (60-game engine sim, instrumenting `_roll_texture`)

| texture | before | after |
| --- | --- | --- |
| dribbler | 6.0% | 5.4% |
| grounder | 22.2% | 34.5% |
| liner | ~45%+ (pre-retune) → 38.2% (mid) | **25.5%** |
| flyball | ~24% (pre-retune) → 33.7% (mid) | **34.6%** |
| **ground (dribbler+grounder)** | 28.1% | **39.9%** |

So the final mix is **~40% ground, ~35% fly, ~25% liner** — ground and liner have
traded places, with fly balls a strong second, exactly the varied air/ground feel
the user was after.

Scoring stayed in the cricket-lean band and the no-shutout floor held: runs/team
**mean ~21, median 19**, **0 shutouts / 0 one-nothing finals** across the batch.
(Scoring eased off the ~27/team peak seen mid-retune when fly balls were highest,
because grounders carry less run value than liners — a fair trade for the mix the
user wanted, still comfortably high and floored.)

## Validation

- `o27/tests` + `tests/test_stat_invariants.py` → **197 passed**. The change is
  config-only (probability weights); no engine logic touched.
- Batted-ball mix and runs/team measured directly by instrumenting the texture
  roll over a 60-game sim (above).

## Scope / not changed

- **Weights only.** The LA band cut points (`RES_FLY_LA`, `RES_LINER_LA`,
  `RES_POPUP_LA`) and the per-band hit rates (`RES_*_HIT_BASE`, raised earlier
  this session) are untouched — only the *frequency* of each batted-ball type
  moved, not what happens once a ball is in a given band.
- The legacy calibration note in the config header (the old "GO ~14%, FO ~13%,
  LO ~8.5%" target) is now historical — this retune is a deliberate, owner-driven
  move toward a cricket-lean air/ground mix, away from that pre-inversion mark.
