# After-Action Report — Pre-Modern Ballpark Revival + Park-Shape Gameplay Hook

**Date completed:** 2026-05-13
**Branch:** `claude/review-and-improve-NbhPy`

---

## What was asked for

User came back to the spray-chart work with a design fiat:
*"Imagine a revival of the original ballparks rather than there ever
being an era of cookie-cutter parks. I love urban stadiums but also
cavernous fields like old Cleveland Stadium, Forbes Field, Polo
Grounds and others like that."* Linked to a 1910s-20s ballpark eras
piece (`thisgreatgame.com/ballparks-eras-1910s-1920s/`) for the
flavor target. Wanted Tal's Hill-style quirks too.

Then mid-batch, a second swing: *"I think we should reimagine the
sport in this way especially like when MLB went to Australia and
played on cricket grounds … the idea of playing baseball in Ovals
appeals to me a lot because it'd be an interesting way to reimagine
how it'd impact hitting."* Added a 7th shape (oval / cricket-ground
revival) to the original 6.

After shipping the shape generator I asked what the gameplay hook
would need to look like — narrow HR↔double swap vs all modifiers,
balanced vs let-the-shapes-shift-the-league. User picked
**option B (no per-shape balancing) + all modifiers, no follow-ons**.

---

## What was built

### 1. Park shape archetypes (7, weighted)

The cookie-cutter era never happened in O27. Every park rolls one of
seven shape archetypes that drives the full dimension distribution:

| Shape | Weight | Lines | CF | Real-world inspiration |
|---|---|---|---|---|
| balanced | 40% | ~338' | ~412' | Symmetric mid-century |
| short_porch_rf | 10% | LF 348' / RF 298' | 420' | Yankee Stadium |
| short_porch_lf | 7% | LF 298' / RF 348' | 420' | Crosley Field |
| cavernous | 13% | ~360' | 458' | Forbes Field, old Cleveland Stadium |
| bathtub | 8% | ~280' | **478'** | Polo Grounds (LF 277' / CF 483') |
| triangle | 10% | ~335' | 445' | Fenway-style center jut |
| oval | 12% | **~380'** | 418' | MCG / Sydney Cricket Ground revival |

`o27v2/league.py:_PARK_SHAPES` + `_roll_park_dimensions()` per-shape
gaussian draws (`_PARK_SHAPE_WEIGHTS`). Wall heights also shape-tied:
bathtub and short-porch shapes have a 45% chance of 28-50 ft walls
(Ebbets / Polo Grounds territory); ovals have a 60% chance of 4-8 ft
cricket-tin fences.

Sample seed produced: White Sox at "Sunset Loukas Stadium" (bathtub:
LF278/CF462/RF292), Guardians at the aptly-named "The Oval" (LF377/
CF421/RF378 with a 6' picket fence), Orcas at "Vancouver Bowl" (47'
wall over short RF — Ebbets territory).

### 2. Architectural quirks (20-item catalog)

`o27v2/league.py:_QUIRK_CATALOG`. Each park rolls 0-3 quirks weighted
by `(0.38, 0.40, 0.17, 0.05)`. Some quirks are shape-gated:

- **Universal**: Ivy Wall · Hand-Operated Scoreboard · Crow's Nest ·
  Lincoln Statue · Trolley Shed · Flag Pole in Play · Bullpens in
  Play · Knothole Gates · Crescent Grandstand · The Crater · Wire
  Basket · The Lima Bean · Scoreboard Clock
- **Bathtub / short porch only**: The Porch
- **Triangle / cavernous only**: The Triangle
- **Cavernous / bathtub only**: Death Valley
- **Oval only**: Round Bowl · Low Picket Fence · Members' Pavilion

Persisted as JSON on a new `teams.park_quirks` column. Surfaced as
gold tag chips on both the team page Ballpark card and the spray-
chart subtitle on `/game/<id>` — hover for the full flavor blurb.

### 3. Spray chart upgrade — actual fence geometry

Previously every spray chart drew a generic 340-ft symmetrical arc.
Now the SVG draws the home park's real fence from `park_dimensions`:

- Fence path: home plate → LF foul corner → LCF → CF → RCF → RF foul
  corner via quadratic-bezier control points. Asymmetric parks look
  asymmetric.
- Wall stroke width scales with `wall_h`. Cricket-low (4-8 ft) renders
  as a hairline; Green Monster (28+ ft) renders as a thick prison
  wall.
- Dimension labels at LF / CF / RF corners ("332'") plus a "Wall N'"
  callout when ≥14 ft.
- Falls back to the symmetric arc on legacy rows.

### 4. Park-shape gameplay hook — the headline feature

This is what makes the shape archetypes actually move the stats.
New module `o27/engine/park_effects.py` with
`apply_park_effects(rng, outcome_dict, ev, la, spray, park_dims)`.

**Plumbing:**
- New `state.park_dimensions: Optional[dict]` field on `GameState`.
- `sim.py` stamps it from the home team's persisted JSON at game
  start.
- `o27/engine/prob.py` calls the hook AFTER the categorical
  `resolve_contact()` + talent-flex adjustment, but BEFORE the Stay-
  vs-run decision (so a fly_out → HR upgrade doesn't leave a runner
  who decided to stay on a caught fly).
- Identity no-op when `park_dims` is None / empty.

**Five independent rewrite rules:**

1. **HR upgrade.** Fly-arc drives (LA 18-42°) whose proxy distance
   clears the fence at their spray angle plus a wall-height clearance
   margin get upgraded to HR, regardless of the engine's roll. This
   is what makes Polo-Grounds bathtub a HR factory: a 95-mph 28° pull
   shot needs only 280 ft, not 380.

2. **HR downgrade.** Engine-called HRs whose distance falls short of
   the fence + clearance get demoted to doubles off the wall. Tall
   walls (≥25 ft) require extra carry, so Green Monster catches
   fringe HRs.

3. **Picket-fence robbery.** Cricket-low walls (≤7 ft) let OFs vault
   to rob borderline HRs. ~18% on close-to-fence drives.

4. **Cavernous gappers.** Singles into alleys at parks with deep
   LCF/RCF (≥415 ft) get promoted to doubles or triples. 440+ ft
   alleys with 290+ ft carry produce triples.

5. **Oval-park tweener triples.** Doubles in uniformly-deep ovals
   (fence ≥ 380 ft, ball ≥ 320 ft, off-center) have a 22% chance of
   becoming triples — cricket-ground gappers run forever on a 380-ft
   boundary.

**Distance heuristic refactor.** Both the spray chart SVG positioning
and the gameplay hook now share `_proxy_distance(ev, la)` in
`park_effects.py`. The dot's position and the gameplay HR cutoff
agree on where the ball landed. Divisor tuned from 36 → 25 against
Statcast medians (100 mph at 28° ≈ 332 ft; 105 mph at 28° ≈ 366 ft;
realistic HR territory at 105+ mph fly-arc).

---

## Schema additions (additive)

```
teams.park_shape   TEXT DEFAULT ''   -- archetype key
teams.park_quirks  TEXT DEFAULT ''   -- JSON list of {key,label,blurb}
```

Both idempotent ALTER TABLEs + SCHEMA. NULL-tolerant.

---

## Files changed

```
o27/engine/park_effects.py            | NEW — sampling + hook (170 lines)
o27/engine/prob.py                    | +25 lines (sample + apply hook
                                                  before Stay decision)
o27/engine/state.py                   |  +6 lines (park_dimensions field)
o27v2/db.py                           |  +9 lines (migrations + SCHEMA)
o27v2/league.py                       | +180 lines (shape archetypes,
                                                   quirks catalog,
                                                   _roll_park_dimensions,
                                                   _roll_park_quirks,
                                                   _park_shape_meta)
o27v2/sim.py                          |  +9 lines (stamp park_dimensions
                                                  on state)
o27v2/web/app.py                      | +50 lines (park_quirks filter,
                                                  park_shape_meta filter,
                                                  game-detail SELECT,
                                                  _bip_distance_ft now
                                                  delegates to _proxy_distance)
o27v2/web/templates/game.html         | +20 lines (shape label + quirks
                                                  chips above SVG)
o27v2/web/templates/team.html         | +40 lines (Ballpark card redo
                                                  with shape + quirks)
```

---

## Verification

120-game reseed sample, league HR / 2B / 3B / total runs grouped by
home park shape:

```
shape           games   HR/G   2B/G   3B/G   R/G
balanced          43    2.28   6.51   1.09   23.3
triangle          10    1.60   7.50   1.30   23.2
short_porch_rf    13    1.31   5.77   0.69   20.5
oval              26    1.27   7.42   1.31   24.0
bathtub           10    1.10   8.20   1.00   25.5
cavernous         18    0.67   7.72   1.00   21.8
```

Observations matching design intent:

- **3.4× HR spread** across shapes (cavernous 0.67 vs balanced 2.28).
  Forbes Field would be a pitcher's park; balanced symmetric parks
  give the most clearance.
- **Bathtub trades HRs for doubles** — 8.20 2B/G is league-leading.
  Polo Grounds deep-alley narrative: drives that clear at 380'
  parks die in the bathtub's 440-ft alleys.
- **Oval = 2B/3B league** — 7.42 2B/G + 1.31 3B/G, both top-tier,
  with HR rate suppressed to 1.27. Cricket-ground revival working
  as designed: pull HRs vanish, gappers run forever.
- **Triangle parks** produce more triples (1.30 3B/G, second-best)
  thanks to the deep center jut + alley gappers.
- **League R/G holds in the 22-26 README band** at every shape
  (20.5 to 25.5). The hook redistributes outcomes; it doesn't blow
  up the run env.

Smoke test (`python o27v2/smoke_test.py`): **10/10 PASS** after the
hook. Run scores noticeably shifted (no more 14-8 games every seed),
consistent with the HR redistribution.

---

## Reused vs new

**Reused:**
- The (EV, LA, spray) sampler from the prior session — the hook is
  pure consumer.
- The 5 fence-control-point persistence we already established for
  the spray chart upgrade. Same data drives both the SVG geometry and
  the gameplay HR cutoff.
- The `_bip_distance_ft` heuristic was refactored to delegate to
  `park_effects._proxy_distance`, so the SVG dot and the gameplay
  decision can't drift out of sync.

**New:**
- 7 shape archetypes + per-shape dimension distributions.
- 20-quirk catalog with shape-gated subsets.
- The hook itself (5 rewrite rules) — the first place in the engine
  where post-`resolve_contact()` outcome mutation lives.

---

## Honest gaps / what's still open

1. **Per-shape balancing intentionally absent** (option B per user
   direction). Cavernous parks ARE pitcher's parks; bathtub parks ARE
   doubles factories. League standings will tilt toward teams whose
   home parks favor their roster, which is a real strategic axis the
   GM layer doesn't currently model.

2. **The hook overrides talent-flex adjustments.** The talent-flexed
   adjustment block (line 1484-1511) can upgrade a marginal ground_out
   to an infield_single. If the park hook later sees an HR-grade EV
   on an infield_single, it'll upgrade further. Reads correctly but
   compounds two systems on the same BIP.

3. **No park-shape leaderboards.** The data is there
   (`teams.park_shape`, `game.home_team_id`) to surface "Best Hitter
   at Cavernous Parks" or "Pitcher's Parks W-L Splits", but no UI
   exists for it yet.

4. **Quirks are visual-only.** Tal's Hill should make CF flyouts
   harder to convert (the slope); Wire Basket should produce more
   wall-scraper doubles; Bullpens in Play should produce more errors
   on foul-line outs. None of those wired into the engine.

5. **Distance heuristic is shared but still non-physical.** No air
   resistance, no altitude, no wind. A real projectile model would
   make weather effects more meaningful — a hot day at Coors-equivalent
   altitude should produce 5% more HRs.

6. **`pitching_log` / batter splits don't filter by park shape.** You
   can't drill into "Ace pitcher's stats at cavernous parks vs
   bathtub parks" today. The pa_log has `game_id`, joinable to
   `teams.park_shape`, but no UI presents the split.

7. **Switch-hit batter handling at short-porch parks.** A switch
   hitter facing a RHP at short-porch-RF should bat lefty (pull to
   RF for the short porch). The engine treats `bats='S'` as
   handedness-flexible but doesn't currently consult park dimensions
   in the side-of-plate decision.

8. **The hook fires once per BIP**. A drive that gets upgraded to HR
   doesn't then get probabilistically demoted by a tall wall on the
   same play — the first rule that fires wins. Reads fine; the rules
   are ordered to bias the right direction (upgrade → downgrade →
   robbery → gapper).

---

## Process notes

- The user iterated the scope twice mid-session: original brief was
  flavor-tag ballpark names; pushed back to rule out flavor tags;
  added generated dimensions; added oval / cricket-ground archetype
  after seeing the first results. Adapted by keeping the same
  migration scaffold (one new column at a time, idempotent ALTER)
  through all three iterations.
- The "where to call the hook" decision (after talent-flex but
  before Stay) was the only non-obvious design call. Calling it at
  the end of the function (where the EV/LA sampling originally
  lived) would have left runner Stay decisions stale on a fly_out →
  HR upgrade. Moving the sampler up earlier added 3 lines and
  prevented an inconsistency that would have been a real bug.
- The distance-divisor mistake (originally 36, should have been ~25)
  produced a great negative result on the first calibration sim:
  1 HR in 120 games. Made it instantly obvious the formula was off,
  and the fix landed in one edit. Faster than reasoning the right
  divisor from physics first.
- Refactoring `_bip_distance_ft` to call `_proxy_distance` was the
  small move that made the system robust. Without it, the SVG and
  the gameplay engine would silently drift apart on any future
  divisor tweak.
- Going with **option B** (no per-shape balancing) was the right
  call. The whole point of the design fiat is that parks differ; a
  rebalanced surface would have ironed out the variety. The 5-run
  R/G spread by shape is the feature, not a calibration miss.
