# After-Action Report — Pitcher Tilt, Emergent Strategy, Elite+ Talent

**Date completed:** 2026-05-03
**Branch:** `claude/improve-sim-realism-UHvKE`
**Commit:** `a0b570a`

---

## What was asked for

The previous push (stats expansion + workload model) had calibrated the league with an MLB-flavored target band — K% 17–19%, BB% 9–10%. The user pushed back on two angles:

> "My sense is that steals and contact ought to be higher than MLB average in this variant of baseball given hitters are optimizing for different things. Using MLB logic is a mistake here."

> "I think we should optimize for dominant pitcher — a power pitcher archetype in this sport, or a Maddux who can keep guys off base, is worth his weight in gold. Given that batters are also not getting many breaks in-between at-bats or being on base and the '2nd and 3rd time around the order' doesn't give them the same gaps that you get in baseball right now, this can also tilt some of the energy back towards pitchers... There will be teams who opt to do the rotation by committee approach to games — openers would be a key component of this sport — but others would stick to a model that resembles baseball now, with a starter, middle relief and closer."

> "I do think also that the super elite .01% of hitters and pitchers should be able to go beyond any gates we're assuming exist right now. I want the engine to operate without strong levers pushing it downward, and to let the sport become its own based on talent / ratings / what happens on the field."

Three intertwined directives:

1. **Tilt back toward pitchers** — elite arms should be premium, especially in O27's no-rest, more-PAs-per-game structure.
2. **Make team strategy emerge from roster composition** — workhorse vs opener vs committee, no persisted tag.
3. **Stop capping elite talent.** Drop the soft levers that compress the distribution. Let .01% transcendent players exist beyond the standard 20-80 scale.

User answers to the scoping question were: **strong pitcher tilt** (aces sub-3 in MLB-equivalent terms), **emergent strategy each game** (not seeded), **strategy first** (then recalibrate after seeing how it shifts the baseline). All three landed in this single commit because they're one cohesive design.

---

## What was built

### 1. Emergent SP role from Stamina (`o27/engine/manager.py`)

The previous `should_change_pitcher` consulted a stored `pitcher_role` tag — but Task #65 cleared that tag for every pitcher, so the legacy WORKHORSE / RELIEVER / COMMITTEE branches were dead-coded. Every pitcher fell through to the generic 10/20 threshold.

Replaced with **live derivation from Stamina** at the moment the pitcher is on the mound:

| Stamina | Strategy | Pull threshold base | Pre-RELIEVER_ENTRY guard |
|---|---|---|---|
| ≥ 0.62 | Workhorse — ride deep | `WORKHORSE_CHANGE_BASE` (28) | yes (don't pull early) |
| ≤ 0.40 | Opener — pull fast | `OPENER_CHANGE_BASE` (7) | no |
| else | Classical SP | `PITCHER_CHANGE_BASE` (10) | no |

Subsequent spells (any spell after the first in the same half) use `RELIEVER_CHANGE_BASE` regardless — the spell IS already a relief appearance. Detected by inspecting `state.spell_log` for any prior entry with `half == state.half`.

Two new config knobs (`OPENER_CHANGE_BASE`, `OPENER_CHANGE_SCALE`) and two threshold constants (`WORKHORSE_STAMINA_THRESHOLD`, `OPENER_STAMINA_THRESHOLD`).

### 2. Elite+ talent tier (`o27v2/league.py`, `o27v2/scout.py`)

The 9-tier `_TALENT_TIERS` ladder previously capped at grade 80 (= 0.85 unit). The user explicitly called out this cap as a soft lever pushing the distribution downward. Added a **0.5% Elite+ tier** at grades 81-95:

```python
(0.005, 81, 95),  # Elite+ (transcendent)
(0.02,  75, 80),  # Elite
(0.05,  65, 74),  # Excellent
...
```

Trimmed the `Average` tier from 0.20 → 0.195 to absorb the new 0.5% mass.

`scout.to_unit()` extended to handle grades > 80:
```python
if g <= 80.0:
    unit = 0.15 + (g - 20.0) / 60.0 * 0.70
else:
    # Extended slope: 0.85 → 1.00 over 15 grade points.
    unit = 0.85 + (g - 80.0) / 15.0 * 0.15
```

`to_grade()` ceiling raised from 80 → 95.

Result: ~6 transcendent attribute rolls in a fresh 1410-player league seed, scattered across Stuff, Stamina, Command, Movement, Power, Eye. The .01% talents now numerically exist.

### 3. Loosened probability floors (`o27/engine/prob.py`)

`contact_quality` had `max(0.05, ...)` on weak / hard / medium probabilities. That 0.05 was a soft lever — it artificially capped how much an elite pitcher could suppress hard contact, or how much an elite hitter could suppress weak. Dropped to `max(0.01, ...)` — preserves probability sanity (no negatives) without holding the elites back.

A super-elite Stuff pitcher facing a sub-replacement-Power batter can now drive hard-contact probability practically to zero, which is exactly what should happen.

### 4. Strong pitcher tilt (`o27/config.py`)

Bumped per-pitch dominance magnitudes ~50%:

| Constant | Was | Now |
|---|---|---|
| `PITCHER_DOM_BALL` | −0.04 | −0.06 |
| `PITCHER_DOM_CALLED` | +0.02 | +0.03 |
| `PITCHER_DOM_SWINGING` | +0.02 | +0.03 |
| `PITCHER_DOM_CONTACT` | −0.03 | −0.04 |
| `PITCHER_COMMAND_BALL` | −0.05 | −0.07 |
| `PITCHER_COMMAND_CALLED` | +0.02 | +0.03 |

Combined with the loosened floors, an elite-Stuff or elite-Command pitcher facing an average batter now swings the per-pitch distribution **hard** toward strikes / weak contact. `BATTER_DOM_*` magnitudes left unchanged so the pitcher edge is structurally bigger.

### 5. Stamina becomes the workhorse moat (`o27/config.py` + `o27/engine/prob.py`)

Two related changes:

**(a) `FATIGUE_THRESHOLD_SCALE` bumped 20 → 40.** Math:
- Elite-Stamina (0.85) arm fatigues at `24 + round(0.85 × 40)` = **58 BF threshold** — effectively never within a 27-out half.
- Sub-replacement-Stamina (0.25) arm fatigues at `24 + round(0.25 × 40)` = 34 BF — visibly tiring through the order.

This is what gives Stamina disproportionate value in O27 vs MLB. A Pedro-tier Stuff arm with weak Stamina still has a ceiling because they tire across 27 outs; a real workhorse doesn't.

**(b) Within-game fatigue threshold now reads `pitcher.stamina`, not `pitcher.pitcher_skill`.** The legacy code said "higher-skill pitchers get longer spells" — but Stuff (`pitcher_skill`) doesn't make a pitcher endure, Stamina does. Changing this corrects the model and ties the bumped scale to the right axis.

---

## Calibration evidence (400-game fresh-seeded league)

**ERA percentiles across 181 qualified pitchers (>50 outs):**

| P10 | P25 | P50 | P75 | P90 |
|---|---|---|---|---|
| 9.00 | 10.96 | 12.77 | 14.56 | 16.50 |

For O27-vs-MLB scaling, the league median ERA of 12.77 maps to ~3× MLB's 4.30 (the run environment is ~3× MLB by structural design). Translating elite ERA back to MLB-equivalent: P10 of 9.00 ≈ MLB 3.03, top observed elite Tim Lee at 5.21 ≈ MLB sub-2.

**Top-tier Stuff (Elite+) producing top-tier lines:**

| Pitcher | Stuff | Stamina | ERA | K/27 | oAvg |
|---|---|---|---|---|---|
| Kazuomi Ahn | 92 | 64 | 7.22 | 10.4 | .167 |
| Evan Stratton | 92 | 43 | 9.09 | 7.7 | — |
| Stephen Clifton | 83 | 43 | 10.73 | 8.0 | — |

Ahn's line is a Pedro-style stat sheet in O27 terms. The two Stratton/Clifton arms are still elite-K but limited by their 43 stamina — they can't ride deep enough to dominate full halves. That's correct: Stuff alone isn't enough; Stamina is the multiplier.

**Strategy variance across teams (avg BF per "starter" appearance):**

| Tier | Avg BF | Pattern |
|---|---|---|
| Top 5 teams | ~33 BF | Workhorse riding all 27 outs |
| Middle teams | 25-30 BF | Classical SP with one reliever close-out |
| Bottom 5 teams | 19-28 BF | Earlier hooks; SLC at 19.2 BF/start (committee-ish) |

The opener archetype (BF < 10) is rare because most teams have at least one stamina-≥-49 active pitcher. That's structurally correct — openers are a fallback for stamina-poor staffs, not a default mode. When a team's roster genuinely lacks the arms for a workhorse approach, the threshold logic naturally pulls the SP fast and the bullpen fills in.

---

## Key decisions and trade-offs

### "Let the sport be its own"

The driving principle the user articulated — and the design lens for every floor / cap / clamp in the codebase. Every soft lever pushing the distribution toward the middle is a lie about the sport. The Elite+ tier, the loosened floors, and the bumped dominance magnitudes are all about removing those compressing levers. The ERA spread of 5.21 to 19.97 is the design intent, not a calibration error.

### Why stamina, not Stuff, sets the fatigue threshold

It was always wrong. The legacy comment said "higher-skill pitchers get longer spells" — but in real baseball Stuff is about how nasty your stuff is per pitch, not how many pitches you can throw. A flame-thrower who needs to be pulled in the 5th has elite Stuff and no Stamina; a Maddux can grind 9 innings on Stamina alone. With Stuff and Stamina now independently rolled (Task #65), the engine can finally model this distinction. One-line fix; large semantic improvement.

### Workhorse ride vs MLB starter pull patterns

`WORKHORSE_CHANGE_BASE` is 28 BF — that's deeper than any modern MLB starter. In O27 with 27-out halves and high-stamina arms, a true workhorse really should ride the full half. Pre-1980s baseball had complete games; O27's structural rules naturally bias the same way. The workhorse path also has a "don't pull early" guard (`RELIEVER_ENTRY_OUTS_MIN`) so a workhorse doesn't get yanked in the early innings just because they crossed BF threshold.

### Why the opener archetype is rare and that's OK

With the talent ladder, ~85% of pitchers have Stamina ≥ 40 (the opener threshold). So most teams have plenty of starters. The opener strategy emerges only when a team is genuinely stamina-poor — exactly when it should. Forcing more openers would be back to the "soft lever pushing the engine" anti-pattern.

### Why Strategy First (before recalibration)

The user picked this sequence and it was the right call. Letting strategy emerge naturally first surfaced what teams actually do — and the recalibration knobs landed correctly because they were tuned against real strategy variance, not against everyone running the same script.

---

## What was verified

- **6/6 identity tests pass** — at all neutral inputs the engine still produces pre-realism output. The Elite+ tier doesn't break neutrals; the loosened floors only affect non-neutral configurations; the bumped dominance magnitudes scale to 0 at neutral.
- **`o27v2/smoke_test.py`** — 10/10 games complete cleanly.
- **`o27/tune.py --games 100`** — K% 18.13%, BB% 8.61%, BA .286, SLG .443, R/T/G ~25 (structural). All within or close to the contact-era band; aggregate league rates barely moved because the changes affect SPREAD, not central tendency.
- **400-game fresh-seeded league** — ERA spread 5.21 to 19.97, median 12.77, P10 9.00. Elite+ pitchers produce elite lines. Workhorse vs committee variance visible across teams.

---

## Files changed

| File | Change |
|---|---|
| `o27/config.py` | Bumped PITCHER_DOM_*, PITCHER_COMMAND_* magnitudes; bumped FATIGUE_THRESHOLD_SCALE; added OPENER_CHANGE_BASE / OPENER_CHANGE_SCALE / WORKHORSE_STAMINA_THRESHOLD / OPENER_STAMINA_THRESHOLD |
| `o27/engine/manager.py` | `should_change_pitcher` rewritten to derive role from Stamina + spell-log relief detection |
| `o27/engine/prob.py` | Floors in `contact_quality` loosened 0.05 → 0.01; fatigue threshold now reads `pitcher.stamina` |
| `o27v2/league.py` | `_TALENT_TIERS` adds Elite+ (grade 81-95, ~0.5%); Average tier trimmed 0.20 → 0.195 |
| `o27v2/scout.py` | `to_unit()` extends grades 81-95 → units 0.85-1.00; `to_grade()` ceiling 80 → 95 |

---

## Known issues / follow-up candidates

- **SB/contact recalibration deferred.** The user separately noted that steals and contact rates should be higher than MLB in O27. Current SB attempt rate (`SB_ATTEMPT_PROB_PER_PITCH = 0.015`) and threshold (`SB_ATTEMPT_SPEED_THRESHOLD = 0.62`) are still calibrated MLB-style. Worth its own pass — bump attempt rate to ~0.04-0.06, drop threshold to ~0.50, raise success base, and consider scaling success by pitcher pitch_debt (tired battery → easier steals).
- **Sabermetrics surface still thin.** O27-native stats (Stay%, Stay-RBI per stay, P/PA, FO%, wOBA with O27-tuned linear weights, Pythagorean W%, OPS+/ERA+) all proposed but not yet shipped. Would surface what's distinctive about O27 vs the MLB-clone advanced stats already shipped.
- **Sub-replacement Stamina + Stuff combo should also produce sub-2 ERAs sometimes.** Currently the best ERAs land around 5.21. If we want truly transcendent aces (sub-3 in O27 terms), we'd need a further tilt — bigger PITCHER_DOM_* magnitudes still, or larger Elite+ tier reach. Defer until we see a multi-season run.
- **Per-team strategy display** — the emergent strategy has no UI surface. A "today's pitching plan" indicator on the team page (workhorse / classical / opener) computed from the projected SP would make the strategy variance visible to users.
- **Within-half SP "the order is the order" problem.** A workhorse who throws 33 BF faces ~3 trips through the order. The model has no specific "third time through the order" penalty — fatigue is purely batters-faced based. A real second-look modifier (Player.skill / contact / power scaled up by recent_PAs_against_this_pitcher) would model the through-the-order effect. Defer.
- **Catcher-side stats absent.** SB allowed / CS caught are credited to the pitcher. A separate catcher-attribution layer (the catcher of record per pitch, stamped on game_pitcher_stats or a parallel table) would let us surface true CS% per catcher. Defer.
