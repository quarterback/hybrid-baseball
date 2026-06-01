# O27 Engine Tuning Guide (built to hand to an LLM)

This document is **self-contained**. Paste the whole thing into a chat with any
capable LLM, then tell it the run environment you want ("a deadball pitcher's
league", "a launch-angle home-run circus", "a speed-and-steals track meet",
"junkball, lots of weak contact, nobody strikes out"). It will hand back a
**tuning blob** you drop into the O27 **Engine Tunables** dashboard
(*Manage → Engine Tunables*), optionally saved as a named environment.

It teaches the model three things: the **output format** it must produce, the
**baseline** O27 plays at, and **which knobs move which outcomes** (with safe
ranges and the couplings you must not break).

---

## 0. TL;DR for the LLM

You are tuning the O27 baseball simulation. The user will describe a *style*.
Return a JSON object of `CONSTANT_NAME: value` overrides — **only** the knobs you
actually change — plus a one-line `environment_name` and a short rationale.
Follow the **ranges** in §5, respect the **couplings** in §4 (especially: the
three `CONTACT_*_BASE` values must sum to ~1.0), and remember O27 runs **hot** by
default (§3). Do not invent constant names; use only the ones listed here.

---

## 1. What O27 is, mechanically

O27 is a 27-out baseball sim. Every editable knob is a scalar (`int`, `float`, or
`bool`) read live by the engine, so a saved tuning takes effect on the **next
game simulated** — no restart. Changes persist across seasons and are wiped only
on a full league reseed.

Two classes of knob:

- **Engine knobs** (most of them) retune the *physics of play* for all existing
  players immediately — contact quality, power, pitcher/batter dominance,
  baserunning, steals, defense, manager behavior, optional rules.
- **`GEN_SHIFT_*` talent-pool knobs** only shape **players generated after you
  save** — they bias the *talent pipeline*, not the current roster. They bite on
  a reseed or as new youth/draft players enter. Use them to make a style *stick*
  in the population (e.g. a power era needs both more HR physics **and** a
  higher-power talent pool), but know they do nothing to today's veterans.

**Not editable** (structural, with internal invariants): the pitch-probability
table (`PITCH_BASE`), the contact-outcome tables (`WEAK_CONTACT`,
`MEDIUM_CONTACT`, `HARD_CONTACT`), and the pitch catalog. You tune *around* these
tables, not inside them. Do not emit these names.

---

## 2. Output format (what the LLM returns)

Return exactly this shape:

```json
{
  "environment_name": "Deadball Revival",
  "summary": "Suppressed power, more weak contact, small-ball baserunning. ~12 R/G, <1 HR/G per team.",
  "overrides": {
    "POWER_REDIST_HR": 0.12,
    "CONTACT_WEAK_BASE": 0.30,
    "CONTACT_MEDIUM_BASE": 0.52,
    "CONTACT_HARD_BASE": 0.18,
    "PITCHER_DOM_CONTACT": -0.08,
    "RUNNER_EXTRA_DOUBLE_FROM_1B": 0.22
  }
}
```

Rules for the `overrides` object:

- Include **only** knobs you change from default. Anything you omit stays at its
  shipped default.
- Keys must be exact constant names from §5 / §6. Values must respect the type
  (the `bool` knobs `POWER_PLAY_ENABLED` and `IBB_ENABLE` take `true`/`false`).
- If you touch contact quality, set **all three** `CONTACT_*_BASE` together so
  they sum to ~1.0 (see §4).
- To enter it by hand: each line in the dashboard is one constant; type the new
  value and hit **Save Tuning**. To save a reusable preset, put
  `environment_name` in the "save as environment" box.

---

## 3. The baseline (what "default" produces)

O27 is a **high-scoring** sport by design. At stock defaults, per team per game:

- **~19 runs/game**, **~2.8 HR/game** per team (so ~38 R and ~5–6 HR combined).
- Contact mix: 18% weak / 50% medium / 32% hard.

The engine self-labels a tuning by what it *produces*, using these bands (per
team, per game). Aim your knobs at the band you want:

| Power (HR/team/game) |             | Scoring (R/team/game) |                  |
| -------------------- | ----------- | --------------------- | ---------------- |
| < 1.0                | Deadball    | < 12                  | pitcher-dominant |
| 1.0–2.0              | Low-power   | 12–17                 | low-scoring      |
| 2.0–3.5              | Normal      | 17–23                 | normal-scoring   |
| 3.5–5.0              | High-power  | 23–30                 | high-scoring     |
| > 5.0                | Extreme     | > 30                  | explosive        |

So "Deadball · pitcher-dominant" = HR < 1.0 **and** R < 12; "Extreme-power ·
explosive" = the launch-circus extreme. Tell the LLM the band and it can target it.

### Rules the tuning lives inside (no knobs — but they shape scoring)

Some O27 rules have **no tunable constants** but materially change the run
environment, so account for them when you tune. The big one:

- **The Walk-Back rule (always on; not in the original O27 ruleset).** After
  *every* home run the HR-hitter trots back out and stands on 3B as a bonus
  runner **for the next batter's PA only**. If the next batter drives him in with
  the bat (hit, sac fly, productive grounder) it's **+1 extra team run** (an
  unearned, Manfred-runner-style run, excluded from ERA but counted in the
  score). If the next batter makes an out without driving him in, he evaporates.
  **Consequence for tuning:** every HR carries a bonus-run tail, so a chunk of
  homers are effectively worth ~1.x–2 runs. **Power-heavy tunings score more than
  their raw HR rate suggests — calibrate `POWER_REDIST_HR` / `CONTACT_HARD_BASE`
  a notch conservatively, especially if you're also raising on-base (the next
  batter needs to be up with contact ability to cash the Walk-Back).** There's no
  knob to disable or scale it; the only related constant is a cosmetic sponsor
  list. It's part of the sport's identity — surface it in your `summary` so the
  user knows their HR tuning is amplified.
- **Other always-or-optionally-on rules:** Power Play (10th "nickel" fielder —
  toggle `POWER_PLAY_ENABLED`), the "Seconds"/declared-out blowout frame
  (`SECONDS_*`, `DECLARE_*`), Joker archetypes (one power/speed/contact star per
  team), and intentional walks (`IBB_ENABLE`). These *do* have knobs and are
  covered in §5.

### What is *not* a tunable here

- **Weather & start-times** are rolled per game / per league at the
  infrastructure level — there are **no editable weather engine constants** in
  this dashboard. You can't dial "rainy pitcher's weather" as a global knob;
  approximate the effect with the contact/power knobs instead.
- **Per-park fence geometry** is a property of each ballpark, not a global knob.
  What you *can* tune league-wide is the **park-factor envelope**:
  `PARK_HR_MIN/MAX` and `PARK_HITS_MIN/MAX` (in §5) scale how much parks push
  HRs and hits.
- **Structural probability tables** (`PITCH_BASE`, `WEAK/MEDIUM/HARD_CONTACT`,
  `PITCH_CATALOG`) — see §1.

---

## 4. How the at-bat pipeline works (the mental model that makes tuning legible)

Each plate appearance flows through stages. Knowing the order tells you which
knob to reach for.

**Stage A — Pitch outcomes (ball / called / swinging / contact).**
Pitcher and batter skill nudge the per-pitch outcome split:

- `PITCHER_DOM_*` (when the pitcher out-classes the batter): `…_BALL` (fewer
  balls when negative), `…_CALLED` / `…_SWINGING` (more strikes), `…_CONTACT`
  (negative = suppresses contact → more strikeouts).
- `BATTER_DOM_*` (skilled batter): `…_SWINGING` (negative = fewer whiffs),
  `…_CONTACT` (positive = more balls in play).
- `BATTER_EYE_BALL` raises walks for disciplined hitters; `PITCHER_COMMAND_*`
  governs the pitcher's strike-throwing. These are your **strikeout and walk**
  dials — i.e. the "three-true-outcomes vs. ball-in-play" axis.

**Stage B — Contact quality (weak / medium / hard).**
When a ball is hit, it's classified into a quality tier:

```
base = {WEAK: CONTACT_WEAK_BASE, MEDIUM: CONTACT_MEDIUM_BASE, HARD: CONTACT_HARD_BASE}
shift by (batter.skill − pitcher.skill) × CONTACT_MATCHUP_SHIFT
```

`CONTACT_HARD_BASE` is the single biggest **BABIP + extra-base** lever: more hard
contact → more doubles, triples, HRs, and hits overall. `CONTACT_MATCHUP_SHIFT`
controls how much talent gaps widen that mix (high = aces dominate scrubs).
**These three bases must sum to ~1.0** — always set them as a triple.

**Stage C — Outcome table.**
Each quality tier draws an outcome (single/double/triple/HR/ground out/fly
out/line out/FC) from a fixed, *non-editable* weight table. You don't edit the
table; you edit how often each tier is reached (Stage B) and how the result is
redistributed (Stage D).

**Stage D — Power & launch redistribution (per-player spread).**
`POWER_REDIST_*` shift weight between outcome rows **scaled by the batter's power
deviation from average**. The value is the *fraction of the "from" row moved at
full ±1.0 power*:

- `POWER_REDIST_HR`: HARD line-outs → HRs (the marquee power dial).
- `POWER_REDIST_HARD_S2D` / `_HARD_D2T`: hard singles→doubles, doubles→triples.
- `POWER_REDIST_MED_S2D`, `_MED_GO2FO`: medium singles→doubles, grounders→flies.
- `POWER_REDIST_WEAK_S2FO`: weak singles → fly outs (low-power "pop-up" tax).

**Key consequence:** because POWER_REDIST scales with a hitter's *power*, raising
it widens the gap between sluggers and slap hitters but barely moves a
league-average bat. **To move the whole league's HR rate, pair `POWER_REDIST_HR`
with `CONTACT_HARD_BASE` (more hard contact for everyone), `PARK_HR_MAX`, and/or
`GEN_SHIFT_POWER` (a more powerful talent pool).** The shipped Juiced/Deadball
presets do exactly this — they move all four together.

**Stage E — Park & launch.** `PARK_HR_MIN/MAX` and `PARK_HITS_MIN/MAX` scale HR
and hit rates by ballpark factor (the league has varied parks). Launch-angle bias
(`LAUNCH_REDIST_GO2FO`) trades ground outs for fly outs per pitch.

**Stage F — Baserunning & events.** Runner advancement (`ADVANCE_*`), extra bases
on speed (`RUNNER_EXTRA_*`, `SPEED_ADVANCE_MOD`), steals (`SB_*`), inside-the-park
HRs (`ITP_HR_*`), double plays (`GIDP_*`), errors (`DEFENSE_ERROR_*`), and
manager tactics (bunts, hit-and-run, pitching changes) resolve last. This is the
**small-ball vs. station-to-station** axis — independent of the power axis, so a
deadball-but-aggressive league is a real, distinct style.

**Stage F′ — The stay / second-chance decision (O27's signature fork).** On
marginal contact the batter may *decline to run* (a "stay"): runners advance and
he gets another swing. This is a distinct stylistic axis with its own knobs —
see the **Stay / 2C** subsection in §5. A stay-heavy league plays like
pesäpallo (constant runner movement, worked counts); a stay-light league
resolves at-bats fast and station-to-station.

---

## 5. The high-leverage knobs, with safe ranges

These are the knobs that visibly change a run environment. Ranges are the
engine's own guard-railed bands (roughly default ±50%, clamped well inside the
math limits) — stay inside them unless you deliberately want a stress test.
Defaults in brackets.

### Power / extra-base hits
| Constant | Default | Safe range | Effect ↑ |
| --- | --- | --- | --- |
| `POWER_REDIST_HR` | 0.50 | 0.10–0.85 | more HRs from sluggers |
| `POWER_REDIST_HARD_S2D` | 0.30 | 0.18–0.42 | more doubles on hard contact |
| `POWER_REDIST_HARD_D2T` | 0.10 | 0.04–0.22 | more triples |
| `POWER_REDIST_MED_S2D` | 0.20 | 0.10–0.32 | more doubles on medium contact |
| `POWER_REDIST_MED_GO2FO` | 0.15 | 0.06–0.40 | grounders → fly balls (air-ball) |
| `POWER_REDIST_WEAK_S2FO` | 0.20 | 0.08–0.40 | weak singles → outs (TTO tax) |
| `PARK_HR_MAX` | 1.20 | 1.00–1.40 | hitter-park HR ceiling |
| `PARK_HR_MIN` | 0.85 | 0.80–0.95 | pitcher-park HR floor |

### Contact quality (set the first three as a triple summing to ~1.0)
| Constant | Default | Safe range | Effect ↑ |
| --- | --- | --- | --- |
| `CONTACT_WEAK_BASE` | 0.18 | 0.08–0.34 | more weak grounders/singles |
| `CONTACT_MEDIUM_BASE` | 0.50 | 0.42–0.52 | more medium contact |
| `CONTACT_HARD_BASE` | 0.32 | 0.14–0.50 | **more XBH/HR + higher BABIP** |
| `CONTACT_MATCHUP_SHIFT` | 0.25 | 0.15–0.35 | talent gaps matter more |

### Pitcher / batter dominance (the K and BB axis)
| Constant | Default | Safe range | Effect |
| --- | --- | --- | --- |
| `PITCHER_DOM_BALL` | −0.07 | −0.12…−0.02 | more negative = fewer walks by aces |
| `PITCHER_DOM_CALLED` | 0.015 | 0.01–0.06 | more called strikes |
| `PITCHER_DOM_SWINGING` | 0.025 | −0.02…0.07 | ↑ = more strikeouts |
| `PITCHER_DOM_CONTACT` | −0.06 | −0.12…−0.01 | more negative = fewer balls in play (more K) |
| `BATTER_DOM_SWINGING` | −0.05 | −0.11…0.00 | more negative = batters whiff less (contact league) |
| `BATTER_DOM_CONTACT` | 0.03 | 0.00–0.07 | ↑ = more balls in play |
| `BATTER_EYE_BALL` | 0.04 | 0.01–0.08 | ↑ = more walks |
| `PITCHER_COMMAND_CALLED` | 0.03 | 0.01–0.06 | ↑ = pitchers steal more strikes |
| `PITCHER_COMMAND_BALL` | −0.07 | −0.10…−0.04 | command suppresses walks |

### Baserunning, steals, defense, double plays
| Constant | Default | Safe range | Effect ↑ |
| --- | --- | --- | --- |
| `SB_SUCCESS_BASE` | 0.58 | 0.40–0.78 | steals succeed more |
| `SB_ATTEMPT_PROB_PER_PITCH` | 0.045 | 0.02–0.10 | runners try to steal more |
| `SB_ATTEMPT_SPEED_THRESHOLD` | 0.52 | 0.45–0.55 | lower = more players run |
| `RUNNER_EXTRA_SPEED_SCALE` | 0.35 | 0.20–0.55 | speed → extra bases |
| `RUNNER_EXTRA_DOUBLE_FROM_1B` | 0.30 | 0.22–0.45 | 1st→3rd on doubles |
| `SPEED_ADVANCE_MOD` | 0.12 | 0.08–0.20 | aggressive advancement |
| `ITP_HR_BASE_ATTEMPT` | 0.16 | 0.08–0.35 | inside-the-park HR attempts |
| `ITP_HR_BASE_SUCCESS` | 0.52 | 0.45–0.65 | ITP-HR success |
| `GIDP_BASE_PROB` | 0.26 | 0.07–0.18* | double-play rate (*lower than default suppresses DPs) |
| `DEFENSE_ERROR_BASE` | 0.045 | 0.020–0.075 | more errors / unearned runs |
| `DEFENSE_RANGE_SHIFT_SCALE` | 0.15 | 0.10–0.18 | range turns more hits into outs |
| `MOVEMENT_GB_WEIGHT_SCALE` | 0.04 | 0.02–0.12 | more ground balls (sinker league) |

### Optional rules & context (booleans / scalars)
| Constant | Default | Effect |
| --- | --- | --- |
| `POWER_PLAY_ENABLED` | (off) | enables the 10th-defender "nickel fielder" rule |
| `IBB_ENABLE` | (on) | intentional walks |
| `HOME_ADVANTAGE_SKILL` | 0.0 | skill bonus to the home team (0.0–0.05 for a real edge) |

### Talent-pool shifts — `GEN_SHIFT_*` (grade points, affect future players only)
Units are grade points; the randomizer treats roughly **−12…+15** as the sane
band. Use to make a style persist in the population after a reseed.
`GEN_SHIFT_SKILL`, `_CONTACT`, `_POWER`, `_EYE`, `_SPEED`, `_DEFENSE`, `_ARM`,
`_PITCHING` (stuff + arsenal), `_STAMINA`.

### Joker archetypes (one power / speed / contact "joker" per team)
Each team carries three signature players whose grade *centers* you set:
`JOKER_POWER_*`, `JOKER_SPEED_*`, `JOKER_CONTACT_*` (each with `_POWER`,
`_CONTACT`, `_SPEED`, `_EYE`). Raise `JOKER_POWER_POWER` for cartoon sluggers,
`JOKER_SPEED_SPEED` for burner archetypes, etc. These shape star-player flavor,
not the league baseline.

### Pitcher usage / stamina (bullpen-depth feel)
`WORKHORSE_CHANGE_BASE` (28), `WORKHORSE_STAMINA_THRESHOLD` (0.62),
`RELIEVER_CHANGE_BASE` (12), `RELIEVER_ENTRY_OUTS_MIN` (18),
`FATIGUE_DEBT_PER_PITCH` (0.005), `PITCHER_CHANGE_BASE` (10). Raise the workhorse
knobs / `RELIEVER_ENTRY_OUTS_MIN` to make starters go deep ("workhorse era").

### Stay / second-chance at-bats — the **2C** mechanic (O27's signature lever)

This is the defining O27 mechanic, borrowed from pesäpallo: on marginal contact a
batter can **"stay"** instead of running — he declines to advance, the runners
move up, and he gets **another swing** (a second-chance at-bat). A single plate
appearance can contain several stays. It's a whole stylistic axis of its own — a
*stay-heavy* league is a running, runner-advancing, count-working game; a
*stay-light* league is station-to-station and resolves at-bats faster.

How the stay decision is built each contact (so you know which knob bites):

```
stay_p = batter.stay_aggressiveness                    # per-player (set at generation)
  × STAY_RISP_MULT          if a runner is on 2B or 3B
  × STAY_1B_ONLY_MULT       if only 1B is occupied
  × STAY_AHEAD_IN_COUNT_MULT if balls > strikes
  × STAY_LATE_GAME_MULT     if outs ≥ LATE_GAME_OUTS_THRESHOLD
stay happens if random() < stay_p
```

So the **live, league-wide 2C-frequency dials** (read every sim — no reseed
needed) are the four situational multipliers and the late-game threshold. The raw
per-batter `stay_aggressiveness` is set at player generation and isn't a live
knob; to crank the *baseline* stay rate league-wide, raise the multipliers
together (and, for a permanent population shift, regenerate — there's no
`GEN_SHIFT` for it).

| Constant | Default | Safe range | Effect ↑ |
| --- | --- | --- | --- |
| `STAY_RISP_MULT` | 1.40 | 1.0–2.0 | more stays with a runner in scoring position |
| `STAY_1B_ONLY_MULT` | 0.70 | 0.4–1.3 | more stays with only 1B occupied (>1.0 flips it from a damper to a boost) |
| `STAY_AHEAD_IN_COUNT_MULT` | 1.15 | 1.0–1.5 | patient hitters stay more when ahead |
| `STAY_LATE_GAME_MULT` | 1.55 | 1.0–2.2 | late-inning "manufacture runs" push |
| `LATE_GAME_OUTS_THRESHOLD` | 20 | 16–24 (int) | lower = late-game stay push starts earlier |
| `TALENT_2C_SHIFT_SCALE` | 1.00 | 0.0–1.5 | talent decides 2C outcomes more (aces convert, weak bats punished) |
| `SECOND_SWING_EYE_SCALE` | 0.20 | 0.0–0.4 | high-eye batters do more damage on the next swing |
| `SECOND_SWING_COMMAND_SCALE` | 0.20 | 0.0–0.4 | high-command pitchers shut down the next swing |
| `GIDP_STAY_MULTIPLIER` | 0.30 | 0.0–1.0 | double-play risk *while staying* (↑ makes 2C riskier) |

Defense's counter to the stay game (raise to suppress 2C, lower to let it run
free):

| Constant | Default | Safe range | Effect ↑ |
| --- | --- | --- | --- |
| `STAY_DEFENSE_READ_BASE` | 0.07 | 0.03–0.20 | defense breaks up more valid stays (lead runner caught) |
| `STAY_DEFENSE_READ_MAX` | 0.28 | 0.15–0.40 | ceiling on that catch-the-runner rate |
| `STAY_DEFENSE_READ_MIN` | 0.03 | 0.01–0.10 | floor |
| `STAY_DEFENSE_READ_TEAM_SCALE` | 0.20 | 0.10–0.35 | how much team defense matters |
| `STAY_DEFENSE_READ_CATCHER_SCALE` | 0.20 | 0.10–0.35 | how much catcher arm matters |

Related running-game risk (stretching a hit for the extra base — *thrown out
trying*): `TOOTBLAN_SAFE_BASE` (0.46, range 0.32–0.88), `TOOTBLAN_SKILL_SCALE`
(0.40), `TOOTBLAN_SPEED_SCALE` (0.20). Lower the safe base for a punishing,
"don't get greedy" defensive league.

> `PLAYER_DEFAULT_STAY_AGGRESSIVENESS` (0.40) and
> `PLAYER_DEFAULT_CONTACT_QUALITY_THRESHOLD` (0.45) appear in the dashboard but —
> like `GEN_SHIFT_*` — they're player-creation fallbacks; in normal league play
> every player already carries a generated value, so editing the default only
> reaches players made without explicit attrs. Use the multipliers above for a
> live, roster-wide effect.

### Defensive shifts (the offense-aggression counter)

O27's design is "offense is aggressive, defense counters by being nimble" —
shifts are the main counter. They're decided per-AB from the batter's spray and
the manager's aggression; on contact, a shift converts some pull-side hits to
outs (and concedes some oppo-side outs to hits). All tunable:

| Constant | Default | Safe range | Effect ↑ |
| --- | --- | --- | --- |
| `SHIFT_BASE_PROB` | 0.35 | 0.10–0.60 | shift floor — even neutral hitters get shifted |
| `SHIFT_DECISION_SCALE` | 1.8 | 1.0–2.5 | spray-pull sensitivity (steeper = pull hitters shifted harder) |
| `SHIFT_DECISION_MAX` | 0.95 | 0.7–0.98 | cap on per-AB shift probability |
| `SHIFT_PULL_OUT_PROB` | 0.42 | 0.25–0.60 | infield shift turns pull singles → outs |
| `SHIFT_OPPO_HIT_PROB` | 0.25 | 0.10–0.40 | the cost: oppo grounders → singles |
| `SHIFT_OF_XBH_HELD_PROB` | 0.40 | 0.20–0.55 | outfield shift holds pull doubles → singles |
| `SHIFT_OF_OPPO_HIT_PROB` | 0.35 | 0.20–0.50 | outfield-shift cost on oppo grounders |
| `SHIFT_OF_POWER_THRESHOLD` | 0.55 | 0.45–0.70 | power level that triggers OF (4-man) over IF shift |
| `SHIFT_LEVERAGE_MULT` | 1.45 | 1.0–2.0 | shifts ratchet up in RISP / late-game |
| `ADAPTABILITY_SCALE` | 0.10 | 0.0–0.25 | hitters read a repeated shift (erodes its effect) |
| `BUNT_AGAINST_SHIFT_BASE_PROB` | 0.18 | 0.05–0.40 | speedy hitters bunt against the shift for hits |

Raise the shift-out probs and base/scale for a **low-BABIP, defense-wins** league;
drop them (and raise `ADAPTABILITY_SCALE` / `BUNT_AGAINST_SHIFT_BASE_PROB`) for a
**shift-proof, hits-fall-in** league.

> Everything else in the dashboard's "All other constants" section is editable
> too (manager tactics, the "Seconds"/declare-out timing rule `SECONDS_*`, form
> variance `TODAY_FORM_*` / `LOCKED_FORM_*` / `SEQ_FORM_*`, familiarity / times-
> through-order `FAMILIARITY_*`, clutch `RISP_*`, etc.). They're lower-leverage
> for a *run environment* and easy to over-tune — touch them only with a specific
> intent, and keep them near default otherwise.

---

## 6. Style → knob recipes

Reach-for-these cheat sheet. Combine and scale within §5 ranges.

- **More home runs / juiced:** ↑`POWER_REDIST_HR`, ↑`CONTACT_HARD_BASE` (lower
  weak), ↑`PARK_HR_MAX`, ↑`GEN_SHIFT_POWER`.
- **Deadball / suppress power:** ↓`POWER_REDIST_HR`, ↓`CONTACT_HARD_BASE` +
  ↑`CONTACT_WEAK_BASE`, ↓`PARK_HR_MAX`, ↓`GEN_SHIFT_POWER`, slightly negative
  `PITCHER_DOM_CONTACT`.
- **Pitcher-dominant / low-scoring:** negative `PITCHER_DOM_BALL` &
  `_CONTACT`, ↑`PITCHER_DOM_SWINGING`, ↑`PITCHER_COMMAND_CALLED`, low
  `CONTACT_HARD_BASE`, ↑`GEN_SHIFT_PITCHING`, ↓`GEN_SHIFT_POWER`.
- **Three-true-outcomes / launch circus:** ↑`POWER_REDIST_HR`,
  ↑`POWER_REDIST_MED_GO2FO` & `_WEAK_S2FO` (air-ball + strikeout tax),
  ↑`PITCHER_DOM_SWINGING`, ↑`BATTER_EYE_BALL`, ↓`GIDP_BASE_PROB`,
  ↑`GEN_SHIFT_POWER` & `_EYE`, ↓`GEN_SHIFT_CONTACT`.
- **Contact carnival / high-BABIP, low-K:** ↑`CONTACT_HARD_BASE` &
  `_MEDIUM`, very negative `BATTER_DOM_SWINGING` & `BATTER_CONTACT_SWINGING`,
  modest `POWER_REDIST_HR`, ↑`GEN_SHIFT_CONTACT`, ↓`GEN_SHIFT_POWER`.
- **Junkball / soft stuff, weak contact, low K:** ↑`CONTACT_WEAK_BASE`,
  ↑`MOVEMENT_GB_WEIGHT_SCALE` & `CONTACT_MOVEMENT_TILT`, negative
  `BATTER_DOM_SWINGING`, ↑`GIDP_BASE_PROB`, ↓`GEN_SHIFT_PITCHING` & `_POWER`.
- **Speed demon / steals & triples:** ↑`SB_ATTEMPT_PROB_PER_PITCH` &
  `SB_SUCCESS_BASE`, ↓`SB_ATTEMPT_SPEED_THRESHOLD`, ↑`ITP_HR_BASE_*`,
  ↑`RUNNER_EXTRA_*` & `SPEED_ADVANCE_MOD`, ↑`POWER_REDIST_HARD_D2T`,
  ↑`GEN_SHIFT_SPEED`, ↓`GEN_SHIFT_POWER`.
- **Workhorse era / starters go deep:** ↑`WORKHORSE_CHANGE_BASE`,
  ↓`WORKHORSE_STAMINA_THRESHOLD`, ↑`RELIEVER_ENTRY_OUTS_MIN`,
  ↓`FATIGUE_DEBT_PER_PITCH`, ↑`GEN_SHIFT_STAMINA`.
- **Sloppy / chaotic defense:** ↑`DEFENSE_ERROR_BASE`, ↓`DEFENSE_RANGE_SHIFT_SCALE`,
  ↓`GEN_SHIFT_DEFENSE` & `_ARM`.
- **Pesäpallo / stay-heavy 2C running game:** ↑`STAY_RISP_MULT`,
  ↑`STAY_1B_ONLY_MULT` (toward/above 1.0), ↑`STAY_AHEAD_IN_COUNT_MULT` &
  `STAY_LATE_GAME_MULT`, ↓`LATE_GAME_OUTS_THRESHOLD`, ↓`STAY_DEFENSE_READ_BASE`;
  pair with high `CONTACT_MEDIUM_BASE` (marginal contact is what triggers a stay).
- **Station-to-station / stay-light, fast at-bats:** ↓ all `STAY_*_MULT` toward
  1.0 or below, ↑`STAY_DEFENSE_READ_BASE` & `_MAX` (defense punishes stays),
  ↑`GIDP_STAY_MULTIPLIER` (staying gets you doubled up).
- **Talent-defined 2C (stars shine on second chances):** ↑`TALENT_2C_SHIFT_SCALE`,
  ↑`SECOND_SWING_EYE_SCALE`, ↑`CONTACT_MATCHUP_SHIFT`.
- **Defense-wins / low-BABIP shift league:** ↑`SHIFT_BASE_PROB`,
  ↑`SHIFT_DECISION_SCALE`, ↑`SHIFT_PULL_OUT_PROB` & `SHIFT_OF_XBH_HELD_PROB`,
  ↑`SHIFT_LEVERAGE_MULT`, ↑`GEN_SHIFT_DEFENSE`.
- **Shift-proof / hits-fall-in league:** ↓`SHIFT_BASE_PROB` & `SHIFT_PULL_OUT_PROB`,
  ↑`ADAPTABILITY_SCALE` & `BUNT_AGAINST_SHIFT_BASE_PROB`, ↑`CONTACT_HARD_BASE`.

---

## 7. Worked examples (shipped presets — use as templates)

These are the engine's own built-in presets. Each is a complete, balanced tuning
for a named identity — copy the shape, adjust to taste.

**Deadball era** — suppressed power, more small ball:
```json
{"POWER_REDIST_HR":0.12,"POWER_REDIST_HARD_S2D":0.26,"POWER_REDIST_HARD_D2T":0.18,
 "CONTACT_WEAK_BASE":0.30,"CONTACT_MEDIUM_BASE":0.52,"CONTACT_HARD_BASE":0.18,
 "PITCHER_DOM_CONTACT":-0.08,"RUNNER_EXTRA_DOUBLE_FROM_1B":0.22}
```

**Juiced / live-ball** — inflated power:
```json
{"POWER_REDIST_HR":0.72,"POWER_REDIST_HARD_S2D":0.34,
 "CONTACT_WEAK_BASE":0.12,"CONTACT_MEDIUM_BASE":0.46,"CONTACT_HARD_BASE":0.42}
```

**1968 Year of the Pitcher** — pitcher-dominant, low power, some speed:
```json
{"CONTACT_WEAK_BASE":0.26,"CONTACT_MEDIUM_BASE":0.52,"CONTACT_HARD_BASE":0.22,
 "POWER_REDIST_HR":0.20,"PITCHER_DOM_BALL":-0.10,"PITCHER_DOM_SWINGING":0.055,
 "PITCHER_DOM_CONTACT":-0.10,"PITCHER_COMMAND_CALLED":0.05,"GIDP_BASE_PROB":0.16,
 "SB_SUCCESS_BASE":0.62,"SB_ATTEMPT_PROB_PER_PITCH":0.06,
 "GEN_SHIFT_PITCHING":12,"GEN_SHIFT_POWER":-8,"GEN_SHIFT_CONTACT":-4,"GEN_SHIFT_SPEED":4}
```

**2010s Launch Angle / Three True Outcomes** — HR + K + BB:
```json
{"POWER_REDIST_HR":0.60,"POWER_REDIST_MED_GO2FO":0.30,"POWER_REDIST_WEAK_S2FO":0.30,
 "CONTACT_WEAK_BASE":0.14,"CONTACT_MEDIUM_BASE":0.46,"CONTACT_HARD_BASE":0.40,
 "PITCHER_DOM_SWINGING":0.05,"BATTER_DOM_SWINGING":-0.02,"BATTER_EYE_BALL":0.06,
 "GIDP_BASE_PROB":0.10,"GEN_SHIFT_POWER":10,"GEN_SHIFT_EYE":6,
 "GEN_SHIFT_CONTACT":-6,"GEN_SHIFT_PITCHING":6}
```

**Speed Demon** — steals, triples, inside-the-park HRs:
```json
{"SB_ATTEMPT_PROB_PER_PITCH":0.09,"SB_SUCCESS_BASE":0.70,"SB_ATTEMPT_SPEED_THRESHOLD":0.45,
 "ITP_HR_BASE_ATTEMPT":0.30,"ITP_HR_BASE_SUCCESS":0.62,"RUNNER_EXTRA_SPEED_SCALE":0.55,
 "RUNNER_EXTRA_DOUBLE_FROM_1B":0.45,"SPEED_ADVANCE_MOD":0.20,"POWER_REDIST_HARD_D2T":0.20,
 "POWER_REDIST_HR":0.40,"GEN_SHIFT_SPEED":15,"GEN_SHIFT_POWER":-6}
```

**Junkball League** — soft stuff, weak contact, low K:
```json
{"POWER_REDIST_HR":0.28,"CONTACT_WEAK_BASE":0.30,"CONTACT_MEDIUM_BASE":0.52,
 "CONTACT_HARD_BASE":0.18,"MOVEMENT_GB_WEIGHT_SCALE":0.10,"CONTACT_MOVEMENT_TILT":0.18,
 "PITCHER_DOM_SWINGING":-0.01,"BATTER_DOM_SWINGING":-0.09,"PITCHER_COMMAND_CALLED":0.05,
 "GIDP_BASE_PROB":0.17,"GEN_SHIFT_PITCHING":-8,"GEN_SHIFT_POWER":-6}
```

**College Softball scoring environment** — borrows softball's *scoring shape*
(high contact / high BABIP, low power, pitching-checked → low-scoring) without
making O27 play like softball:
```json
{"CONTACT_WEAK_BASE":0.26,"CONTACT_MEDIUM_BASE":0.56,"CONTACT_HARD_BASE":0.18,
 "CONTACT_MATCHUP_SHIFT":0.32,"POWER_REDIST_HR":0.12,"POWER_REDIST_HARD_S2D":0.24,
 "POWER_REDIST_HARD_D2T":0.10,"PITCHER_DOM_SWINGING":0.03,"PITCHER_DOM_CONTACT":-0.10,
 "BATTER_DOM_SWINGING":-0.05,"BATTER_EYE_BALL":0.04,"PARK_HR_MAX":1.00,
 "GEN_SHIFT_POWER":-10,"GEN_SHIFT_CONTACT":8,"GEN_SHIFT_PITCHING":6}
```
Calibrated to 2026 NCAA Div I **national totals** (true league averages, per team
per 21-out game): R 5.11, BA .291, OBP .376, SLG .449, ISO .157, BABIP .316, HR
0.81, **K% 13.4** (a put-it-in-play league, so swing-strikes stay modest), BB%
10.3, ERA 3.44. Run suppression comes from the circle (weak-contact pitcher
dominance + a big ace-vs-field `CONTACT_MATCHUP_SHIFT`), **not** from double
plays — softball's rare DPs are a 60-ft-basepath artifact, and O27 plays on 90-ft
bases, so `GIDP` is left at default. Benchmarks to ~6.6 R/team/game once scaled to
O27's 27 outs — the softball low-scoring band.

(The engine also ships *1987 Lively Ball*, *Contact Carnival*, *Workhorse Era*,
and two intentional stress tests — *Knife's Edge* max-offense and *Pitcher's
Hellscape* min-offense. Same construction: move a coherent cluster of knobs.)

---

## 8. Guardrails — don't break these

1. **`CONTACT_WEAK_BASE + CONTACT_MEDIUM_BASE + CONTACT_HARD_BASE ≈ 1.0.**
   Always change all three together. A mix that doesn't sum to ~1 is normalized
   but the *ratios* are what matter — design the ratios deliberately.
2. **`POWER_REDIST_*` are per-player power multipliers, not league rates.** To
   move a league-wide rate, pair them with `CONTACT_HARD_BASE`, `PARK_HR_*`,
   and/or `GEN_SHIFT_POWER`. Cranking `POWER_REDIST_HR` alone mostly just widens
   the slugger-vs-slap gap.
3. **`GEN_SHIFT_*` only affect future-generated players.** They do nothing to the
   current roster until a reseed/regen or new player intake. If you want a style
   live *today*, you need engine knobs too.
4. **Stay in the §5 ranges** unless you explicitly want a stress test. Values far
   outside (e.g. `POWER_REDIST_HR` at 0.95, near-zero `PITCHER_DOM_BALL`) can
   produce cartoon outputs — fine for "Knife's Edge", bad for a believable league.
5. **Never emit structural names** — `PITCH_BASE`, `WEAK/MEDIUM/HARD_CONTACT`,
   `PITCH_CATALOG`. They aren't exposed and will be ignored.
6. **Keep `CONTACT_MEDIUM_BASE` in a tight band (~0.42–0.52).** Medium contact is
   the league's spine; gut it and the run environment gets weird fast.
7. **Booleans are `true`/`false`** (`POWER_PLAY_ENABLED`, `IBB_ENABLE`), not 0/1
   in the JSON.
8. **The stay/2C multipliers are conditional**, not a global stay rate — each
   fires only in its situation (RISP, 1B-only, ahead in count, late game). To
   shift the *overall* 2C frequency, move them as a group, and remember the
   bases-empty floor comes from per-player aggressiveness (regen-time), not a
   live knob. `STAY_1B_ONLY_MULT` < 1.0 *suppresses* stays; > 1.0 promotes them.

---

## 9. Copy-paste prompt template

> Use the **O27 Engine Tuning Guide** I've pasted above. I want a run
> environment that is: **\<describe the style — e.g. "a 1900s deadball pitcher's
> duel league: almost no homers, ~10–12 runs/game, lots of bunts and steals,
> aces overpower weak hitters">**.
>
> Target band (from §3): **\<e.g. "Deadball · pitcher-dominant">**.
> Persist it in the talent pool for future seasons: **\<yes / no>**.
> Optional rules: **\<Power Play on/off, IBB on/off>**.
>
> Return the JSON object from §2 (`environment_name`, `summary`, `overrides`),
> using only knobs and ranges from §5–§6, respecting the couplings in §8. Add a
> 2–3 sentence rationale explaining which levers you pulled and why.

That's the whole loop: describe the style → get a blob → paste into *Engine
Tunables* → **Save Tuning** (or save as a named environment) → it's live on the
next simulated game. Build a deadball era, a launch-angle circus, a junkball
league, and switch between them at will.
