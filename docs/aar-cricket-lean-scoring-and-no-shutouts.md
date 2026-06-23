# After-Action Report — Cricket-Lean Scoring + Floor Out Shutouts

**Date completed:** 2026-06-23
**Branch:** `claude/vigilant-davinci-hn34xy`
**Commits:** `170e019` (offense lift + cold-tail floor), `0dda8ce` (grounder BABIP follow-up)

---

## What was asked for

The run environment was too low for what O27 is supposed to be. The user
corrected my framing in the middle of the work, twice, and the corrections
*are* the spec:

> "The point is to get something closer to cricket... the whole point is to do
>  something more eclectic — you want it to feel like itself but nobody's going
>  to watch a 27-out marathon where nobody scores any runs, that just won't
>  work."

> "18+/- isn't a goal or target — if it goes wildly higher I'm ok with that, and
>  if it falls short I'm fine there too. I just can't have 1-0 games in this
>  sport, that won't sell."

So the brief is **not** "make it score like baseball" and **not** "hit a target
mean." It is: push toward a cricket-flavored, high-scoring feel, and — the only
hard line — **eliminate the low-scoring floor.** A 1-0 game is the unsellable
failure mode. Overshooting is fine; blowouts are fine; the cold tail is the
problem.

The earlier scoring was ~12 runs/team.

---

## What changed

All in `o27/config.py` (the shared engine config). Two moves.

### 1. Lift offense (cricket-lean run environment)

More balls in play become hits, and fewer plate appearances end in a whiff:

| Knob | Was | Now | Effect |
| --- | --- | --- | --- |
| `RES_LINER_HIT_BASE` | 0.76 | 0.90 | liner-band BABIP (the highest band) up |
| `RES_GB_HIT_BASE` | 0.44 | 0.74 | grounders find holes far more often |
| `RES_FLY_HIT_FLOOR` | 210.0 | 190.0 | shorter flies stop being automatic outs |
| `RES_FLY_DROP_SCALE` | 0.92 | 1.02 | sub-HR flies drop for XBH more readily |
| `PITCHER_DOM_SWINGING` | +0.025 | +0.005 | dominant pitchers miss fewer bats |
| `BATTER_DOM_SWINGING` | −0.05 | −0.075 | batters whiff materially less |

### 2. Floor the cold tail — asymmetrically

The shutout requirement is one-sided: the **ceiling must stay high** (blowouts
are a feature) while the **floor comes up.** The per-half form multiplier is the
right tool because it clips only the cold side:

| Knob | Was | Now | Effect |
| --- | --- | --- | --- |
| `LOCKED_FORM_MIN` | 0.77 | 0.92 | coldest possible half is now ~league-average offense |
| `LOCKED_FORM_MAX` | 2.15 | 2.15 | **untouched** — hot halves / blowouts unchanged |

At `MIN = 0.92` with `SIGMA = 0.66`, roughly the bottom ~45% of form draws clamp
to the floor, so no lineup gets a genuinely cold night, while the long hot tail
up to 2.15× is preserved.

### 3. Grounder BABIP follow-up (`0dda8ce`)

After the first pass shipped at `RES_GB_HIT_BASE = 0.62`, the user judged
grounders still too stingy relative to the cricket-lean target — liners were
sitting at 0.90 while grounders lagged. Bumped **0.62 → 0.74**. This is the
single change in the follow-up commit; it pushed the run environment up another
notch (see validation).

---

## Validation

`python3 -m pytest o27/tests tests/test_stat_invariants.py` → **195 passed.**

Behavioral check, two sims:

**After the floor pass** (`170e019`, 200-game sim, `RES_GB_HIT_BASE = 0.62`):

- **runs/team: mean ~12 → ~16.8**, median 14, **max 60** (blowouts intact).
- **Shutouts: 0. 1-0 finals: 0.** The hard requirement is met across the batch.
- Lowest single game in 200 was one **2-1**; only 3 of 200 games had a team
  under 3 runs, and only 1 team-game (of 400) scored ≤1.

**After the grounder follow-up** (`0dda8ce`, 150-game sim, `RES_GB_HIT_BASE = 0.74`):

- **runs/team: mean ~19.1**, median 17, **max 57** (blowouts intact).
- **Shutouts: 0. 1-0 finals: 0.** Floor still holds; lowest game was a **3-2**.

A 150-game sim taken *before* the `LOCKED_FORM_MIN` push still produced 1 shutout
and 1 actual 1-0 final — i.e. lifting offense alone did **not** kill the floor;
the asymmetric form clip is what did it. That's the load-bearing change.

---

## What I did *not* change, and the honest caveat

- **No structural "minimum runs" hack.** The floor is achieved entirely through
  the existing form multiplier and BABIP knobs — no special-case rule that
  hands a team runs it didn't earn. Low scores are still possible, just rare.
- **The 2-1 game is not fully gone.** The literal requirement ("no 1-0 games")
  is satisfied — zero in 200 — but a rare 2-1 (one occurrence) still slips
  through, partly because the walk-off truncates a home team's half the moment
  it leads. If even a 2-1 reads as too quiet, the next lever is
  `LOCKED_FORM_MIN` toward ~0.97 plus a weak-contact hit bump so the coldest
  team still lands ~4+. The user opted to **work with what we have** for now, so
  that push was left on the shelf rather than applied.
- **No retuning of the downstream stat lines** (AVG/OBP/SLG leaderboards,
  ERA scales, award thresholds) to the new run environment. Everything still
  computes and the invariant suite is green, but the *aesthetics* of the
  leaderboards at ~17 runs/team weren't audited — a follow-up if the inflated
  rate stats start looking wrong.
- **Single seed family.** Numbers above are one 200-game sim; directionally
  solid and the tail result is stark (0 shutouts), but not a multi-seed sweep.

---

## One-line takeaway

Lifting BABIP (grounders in particular, finally landing at 0.74) + cutting
whiffs got the run environment to a cricket-lean ~19/team; the thing that
actually killed the 1-0 game was clamping the cold side of the form multiplier
while leaving the blowout ceiling alone.
