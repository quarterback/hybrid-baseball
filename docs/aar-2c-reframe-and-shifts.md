# After-Action Report — 2C philosophy reframe + defensive shifts (phase 12)

**Date completed:** 2026-05-17
**Branch:** `claude/investigate-rbi-calculation-ni8ou`
**Predecessor:** `aar-second-chance-hits-and-eye-command-modifier.md` (Path A / Phase 11C)

---

## Context

Session opened as an RBI-calculation investigation. The user
reported runs dropping (10.62 → 9.95 R/half-game) after a 2C-rate
lift even though hits and 2C events were both up. Root cause turned
out to be conceptual rather than arithmetic: the 2C mechanic had
drifted into a "free hit credit" generator that padded BA without
moving runners enough to score. The user articulated a series of
design corrections that reshaped both 2C and the run-prevention
side of the engine.

Two distinct arcs shipped this session:

- **Arc 1** — 2C philosophy reframe + tuning. Bring 2C back to its
  intended role as a runner-advancement mechanic, with skill-driven
  outcomes and chain-extending rallies that the pitcher pays for in
  pitch count.
- **Arc 2** — Defensive shifts as the tactical counter. Two
  alignments (infield / outfield), a batter adaptability rating
  that erodes static defensive calls, leverage-aware ratcheting,
  bunt-against-shift manager response.

The connecting design philosophy the user articulated mid-session
ties both arcs together:

> I want the tactical abilities of this sport to be more like
> tennis scoring in that it's easy to score but you have to score
> at critical times for you to win. So it's being able to keep
> and extend rallies in key positions risp, late arcs (20-27
> outs) instead of it being predictable

Reads as: routine offense + routine defense most of the time;
**both** sides ratchet their tactical levers at RISP + late arc,
and the decisive moments are where games are won.

---

## Arc 1 — 2C philosophy reframe + tuning

### The conceptual corrections

The user walked through a series of clarifications that reshaped
the 2C model:

1. *"More 2C events should result in more runs, not just more
   credit-only hits."* — The lifted 2C rate from prior work was
   producing dilutive low-RV hits. Per-event RV needed to come up.

2. *"2C opportunities should be costly to pitchers because the
   batter is getting more chances to hit your stuff and make you
   throw more pitches."* — Every 2C is a `ball_in_play` event,
   which already increments `pitcher_pitches_this_spell` — so the
   pitcher already pays in pitch count and fatigue. Confirmed
   behavior; no change.

3. *"I want skill to dictate what happens, not random gates."* —
   Removed the `contact_quality_threshold` random gate on medium
   2C. The Path A talent gate (`eye + contact − command`) is the
   only skill check on the outcome.

4. *"2C is about advancing runners or bringing them home, not
   hits for hit's sake."* — Interpreted initially as "credit hit
   only when a run scores," which the user immediately corrected:
   *"every one of those 2C hits is a hit!"* The right read: 2C
   is a runner-advancement mechanic; when it advances runners,
   crediting a hit is correct bookkeeping; the *purpose* of
   firing it is advancement, not hit-padding.

5. *"2C should fire more often when RISP."* — Implemented as a
   RISP multiplier on `should_stay_prob`.

6. *"2C also fires when a batter is waiting for his pitch — and
   late in the game when the team is manufacturing runs even
   without RISP."* — Two more multipliers added (ahead-in-count,
   late-game).

7. *"Not foul-off mode — in O27 fouling off 3 is a Foul Out."* —
   Removed the 2-strike multiplier I'd added (MLB-foul-off
   metaphor doesn't apply to O27).

8. *"Batters should be more aggressive in this sport, not
   passive."* — Encouraged offensive aggression; defense's
   counter is being nimble/tactical, not gating offensive
   opportunities. Set up the bridge to Arc 2.

### What was built (Arc 1)

**`o27/engine/prob.py` — talent gate floors lifted (Path A
continuation).** The talent-driven runner advancement on weak /
medium 2C events:

```python
# Was:
weak:   expected = 0.5 + 0.5 * talent_factor   # neutral = 50% credit
medium: expected = 1.0 + 0.5 * talent_factor   # neutral = always 1

# Now:
weak:   expected = 1.0 + 0.5 * talent_factor   # neutral = always 1
medium: expected = 1.5 + 0.75 * talent_factor  # neutral = avg 1.5,
                                                # high-talent = 3
```

High-talent medium-contact 2C reliably hits adv=3, which is
"runner from 1B scores on 2C." Low-talent batters still produce
adv≈0.75 — the gate hasn't been removed, just shifted toward
more reliable advancement at the typical hitter.

**`o27/engine/pa.py` — skill-conditional strike burn.** Previously
every stay burned a strike from the batter's 3-strike budget,
capping multi-hit ABs at 3 stays. Now:

```python
if runner_successfully_advanced:
    # 2C earned via the talent gate — no strike cost. AB continues.
    log.append("Stay successful — count unchanged.")
else:
    # Gate failed (adv=0). Burn strike as before.
    log += _stay_credit_strike(state)
    if state.count.strikes >= 3:
        log += _end_at_bat(state)
```

Skilled batters chain 2Cs as long as they keep winning the
eye+contact-vs-command check. Pitchers pay via the
`ball_in_play` → `pitcher_pitches_this_spell` increment that
fires on every event regardless. Natural exits (4 balls → walk,
3 swinging/called → K, 3 fouls → foul out, caught fly + stay,
run-choice) still bound the AB.

**`o27/engine/prob.py` — should_stay_prob multipliers.** Four
composable lifts on the existing `batter.stay_aggressiveness`
gate (which already returns False on empty bases — hard rule
unchanged):

```python
STAY_RISP_MULT          = 1.40   # 2B or 3B occupied
STAY_1B_ONLY_MULT       = 0.70   # only 1B occupied
STAY_AHEAD_IN_COUNT_MULT= 1.15   # balls > strikes (patient hitter)
STAY_LATE_GAME_MULT     = 1.55   # outs >= 20 (manufacture mode,
                                  # also rescues 1B-only past 0.7×)
```

Max-stay scenario (late arc + ahead in count + RISP) ≈ 2.5× of
the baseline `stay_aggressiveness` value. Empty bases → no 2C
ever. RISP late-arc is where the offense leans hardest.

NB: there is intentionally NO 2-strike multiplier — that was
added briefly under a misread of "fouling off pitches" and
removed when the user pointed out that 3 fouls = FOUL OUT in
O27. The fouling-off-to-stay-alive metaphor doesn't apply.

**Configuration**: `LATE_GAME_OUTS_THRESHOLD: 18 → 20` to match
the user's "late arcs (20-27 outs)" framing. This same constant
later gates the shift leverage multiplier in Arc 2.

### Arc 1 — files touched

| File | Change |
|---|---|
| `o27/engine/prob.py` | Path A floor lifts; `should_stay_prob` multipliers |
| `o27/engine/pa.py` | Skill-conditional strike burn |
| `o27/config.py` | RISP / count / late-game constants; `LATE_GAME_OUTS_THRESHOLD` 18 → 20 |

---

## Arc 2 — Defensive shifts

### The user's design

> shifts are legal in O27 and should be encouraged better defense
> is helpful towards run prevention

> outfield shifts more including teams using 4-5 sometimes bringing
> infielders shallow

> in O27 hitters should have an adaptability rating that rolls
> along with their talent that can help them beat shifts vs your
> idea can roll with bunting

Three load-bearing pieces:
1. Defensive shifts as the legitimate tactical counter to aggressive
   offense.
2. Two alignments: infield shift (pull-side grounders) and outfield
   shift (4-man OF against pull-power FB hitters).
3. Adaptability — sustained alignments are read by skilled batters,
   so the manager has to vary the look or get beat.

### Schema additions

```sql
ALTER TABLE players ADD COLUMN pull_pct REAL DEFAULT 0.5;
ALTER TABLE players ADD COLUMN adaptability INTEGER DEFAULT 50;
ALTER TABLE teams   ADD COLUMN mgr_shift_aggression REAL DEFAULT 0.5;
ALTER TABLE games   ADD COLUMN home_shift_outs_added INTEGER DEFAULT 0;
ALTER TABLE games   ADD COLUMN home_shift_hits_lost  INTEGER DEFAULT 0;
ALTER TABLE games   ADD COLUMN away_shift_outs_added INTEGER DEFAULT 0;
ALTER TABLE games   ADD COLUMN away_shift_hits_lost  INTEGER DEFAULT 0;
```

All defaults are identity — legacy rosters are shift-immune until
a reseed populates `pull_pct` and `mgr_shift_aggression`.

### Generation

`o27v2/league.py _player_dict`:
```python
pull_pct = clamp(N(0.5, 0.12)
                 + power_dev * 0.30
                 + (0.04 if bats == "L" else 0),
                 0.05, 0.95)
adaptability = roll()   # standard tier roll, uncorrelated
```

High-power LHBs land around 0.65–0.85; contact hitters cluster
near 0.50. Pitchers' bats default to neutral 0.5 / 50 — they
don't see enough ABs for spray or adaptation to matter.

`o27v2/managers.py` — Archetype dataclass gains
`shift_aggression: float = 0.50` (defaulted, so the 14 existing
archetype defs don't all need updating). Opinionated overrides:

| Archetype | shift_aggression |
|---|---|
| dead_ball | 0.10 |
| iron_manager | 0.20 |
| old_school | 0.30 |
| modern | 0.80 |
| sabermetric_max | 0.95 |
| (others) | 0.50 default |

### Engine — Player / Team / GameState

```python
# Player
pull_pct: float = 0.5
adaptability: float = 0.5
last_shift_alignment: str = "none"   # transient per-game
shift_streak: int = 0                # transient per-game

# Team
mgr_shift_aggression: float = 0.5
shift_outs_added: int = 0
shift_hits_lost: int = 0

# GameState
current_ab_shift_type: str = "none"  # "none" | "infield" | "outfield"
current_ab_shift_decided: bool = False
```

### Decision logic (prob.py, AB start)

```python
extremity = abs(pull_pct - 0.5) * 2.0
shift_p = extremity * mgr_shift_aggression * SHIFT_DECISION_SCALE

# Leverage ratchet — RISP AND late arc both gate this multiplier
if risp and outs >= 20:
    shift_p *= SHIFT_LEVERAGE_MULT   # 1.45

if rng() < shift_p:
    shift_type = "outfield" if power >= 0.55 else "infield"
else:
    shift_type = "none"

# Adaptation streak — same alignment as last AB → streak++
if shift_type == batter.last_shift_alignment:
    batter.shift_streak += 1
else:
    batter.shift_streak = 1
    batter.last_shift_alignment = shift_type
```

### Resolution (prob.py, resolve_contact)

```python
# Per-event contact direction biased by pull_pct
went_pull = rng() < pull_pct

# Adaptability erosion — capped at streak=3
adapt_dev = (adaptability - 0.5) * 2.0
streak    = max(0, batter.shift_streak - 1)
streak    = min(streak, 3)
adapt_reduction = max(0, adapt_dev * streak * 0.10)

# Infield shift
if shift_type == "infield" and hit_type in ("single", "ground_out"):
    p_out = max(0.05, SHIFT_PULL_OUT_PROB - adapt_reduction)  # 0.30
    p_hit = max(0.05, SHIFT_OPPO_HIT_PROB - adapt_reduction)  # 0.25
    if went_pull and hit_type == "single":
        if rng() < p_out: hit_type = "ground_out"  # defensive win
    elif not went_pull and hit_type == "ground_out":
        if rng() < p_hit: hit_type = "single"      # batter beats it

# Outfield shift
elif shift_type == "outfield":
    p_xbh = max(0.05, SHIFT_OF_XBH_HELD_PROB - adapt_reduction) # 0.30
    p_hit = max(0.05, SHIFT_OF_OPPO_HIT_PROB - adapt_reduction) # 0.35
    if went_pull and hit_type in ("double", "triple"):
        if rng() < p_xbh: hit_type = "single"      # 4th OFer cuts it off
    elif not went_pull and hit_type == "ground_out":
        if rng() < p_hit: hit_type = "single"      # IF shorthanded
```

### Bunt-against-shift (manager.py should_bunt)

New no-runner path at the top of `should_bunt`:

```python
if (state.current_ab_shift_type == "infield"
        and state.outs < 24
        and not batter.is_pitcher
        and batter.speed > 0.55):
    p = BUNT_AGAINST_SHIFT_BASE_PROB * (speed - 0.5) * 2.0
    if rng() < min(0.30, p):
        return {"type": "sac_bunt", "outcome": "hit"}  # cheap hit
```

Bypasses the 1B-runner requirement and the standard
fail/sacrifice rolls — the open infield is the point.

### Telemetry

Engine-side: `Team.shift_outs_added` and `Team.shift_hits_lost`
accumulate per game. `outcome["shift_effect"]` is `"out_added"`
or `"hit_lost"` per event for the log line in `pa.py`.

Persistence: end-of-game `UPDATE games` writes
`home_shift_outs_added`, `home_shift_hits_lost`,
`away_shift_outs_added`, `away_shift_hits_lost` from the
final state's team counters.

UI: deliberately not built. Per the user:

> uI isn't necessary irl we dont have shift stats outside normal
> defensive ones

The columns exist for tuning queries; they don't surface
through the templates.

### Arc 2 — files touched

| File | Change |
|---|---|
| `o27/engine/state.py` | `pull_pct`, `adaptability`, transient shift memory on Player; `mgr_shift_aggression` and counters on Team; `current_ab_shift_type` on GameState |
| `o27/engine/prob.py` | Shift decision + adaptation streak (AB start); shift resolution with adaptability erosion (resolve_contact) |
| `o27/engine/pa.py` | Shift telemetry counter + log line on each `shift_effect` event |
| `o27/engine/manager.py` | Bunt-against-shift path at top of `should_bunt` |
| `o27/config.py` | All `SHIFT_*`, `ADAPTABILITY_SCALE`, `BUNT_AGAINST_SHIFT_BASE_PROB` |
| `o27v2/db.py` | Schema additions + ALTER migrations + games-table shift counters |
| `o27v2/league.py` | Roll `pull_pct` and `adaptability` in `_player_dict`; INSERT statement extended; team INSERT extended with `mgr_shift_aggression` |
| `o27v2/managers.py` | `Archetype.shift_aggression` (default 0.50, archetype-specific overrides); `roll_manager` emits `mgr_shift_aggression` |
| `o27v2/sim.py` | DB → engine wiring for `pull_pct`, `adaptability`, `mgr_shift_aggression`; end-of-game UPDATE writes shift counters |

---

## Connecting design — tennis-scoring leverage

What ties Arc 1 and Arc 2 together is the user's tennis-scoring
mental model. Both arcs have **the same leverage ratchet**:

| Trigger | Offensive ratchet (Arc 1) | Defensive ratchet (Arc 2) |
|---|---|---|
| RISP (2B/3B occupied) | `stay_p × 1.40` | (compounds into shift `× 1.45`) |
| Late arc (outs ≥ 20) | `stay_p × 1.55` | (compounds into shift `× 1.45`) |
| Ahead in count | `stay_p × 1.15` | — |
| Both RISP + late | (multiplies) | `shift_p × 1.45` |

Mid-half, mid-bases:
- 2C frequency at baseline `stay_aggressiveness`
- Shift call rate at baseline `extremity * mgr_shift_aggression`
- Routine baseball; runs accumulate from low-leverage events

Late arc + RISP:
- 2C frequency ratchets toward `2.0–2.5× baseline`
- Shift call rate ratchets `× 1.45`
- Adaptable batters work the persistent alignments
- Bunt-against-shift fires when speed + infield shift align

Decisive plays cluster where they should — in the leverage points
that decide the result, like tennis break points.

---

## Verification plan (numbers pending re-sim)

Reset cycle: `resetdb → sim 2430 → backfill_arc`.

### Arc 1 — Offense

| # | Spec | Target | Result |
|---|---|---|---|
| O1 | League R/team-game | back to 10+ from 9.95 floor; ideally 11-13 | _pending_ |
| O2 | League BAVG | up from .3243 (post-Path-A); ~0.34-0.36 | _pending_ |
| O3 | Per-event RV of 1B (from RV currency table) | ≥ 0.65; ideally back to ~0.70+ | _pending_ |
| O4 | Stay-rate (% PAs ending in 2C) | 5-9% (close to pre-changes 5.62%) | _pending_ |
| O5 | Δ between count-flat vs late-arc stay rate | late-arc stay rate ≥ 1.4× mid-game | _pending_ |
| O6 | RISP-state vs 1B-only stay rate | RISP ≥ 1.8× the 1B-only rate | _pending_ |
| O7 | League HR total | within ±10% of pre-changes 4652 | _pending_ |
| O8 | Pitches/PA | up from current league; should rise with 2C chains | _pending_ |

### Arc 2 — Defense

| # | Spec | Target | Result |
|---|---|---|---|
| D1 | Avg shift_outs_added per game (both teams) | 2-5 (rough estimate — calibrate from this) | _pending_ |
| D2 | Avg shift_hits_lost per game | 1-3 | _pending_ |
| D3 | League BAVG for batters with `pull_pct > 0.75` | down 10-20pp vs neutral-spray peers | _pending_ |
| D4 | League BAVG for batters with `adaptability > 0.7` who saw repeat shifts | within 5pp of mid-adaptability peers (erosion working) | _pending_ |
| D5 | Bunt-against-shift firings | rare but non-zero (1-3 per game league-wide) | _pending_ |
| D6 | Shift call frequency split: RISP+late vs other | ≥ 1.4× ratio (leverage ratchet visible) | _pending_ |
| D7 | OF-shift vs IF-shift mix | OF shift on high-power pull batters; IF on low-power pull | _pending_ |

### Spot-checks

| # | Check | _pending_ |
|---|---|---|
| S1 | Identity: legacy DB rows (pull_pct=0.5) produce zero shift events | _pending_ |
| S2 | Adaptability erosion visible: same batter's 4th-streak shift has ~50% lower defensive conversion than 1st-streak | _pending_ |
| S3 | Run distribution: more variance in RISP+late innings vs other arcs (tennis-scoring effect) | _pending_ |

---

## What's deferred

1. **UI surfacing of shift stats** — explicit user direction NOT to
   build. The columns exist on `games`; query directly for tuning.

2. **Counter-counter-tactics**:
   - Per-event spray-direction override (a contact batter could
     intentionally go oppo when seeing infield shift; currently
     pull direction is rolled purely from `pull_pct`).
   - Specific "beats_shift" pitching response (e.g., pitcher
     refusing to give in to the bunter, walks instead).

3. **OF shift secondary effects**:
   - Pull-side line drives → singles converted by 4th OFer (only
     `double`/`triple` covered for now; line drives are typed as
     `line_out` if caught or `single` if not).
   - Fly_out gaps from infielders-shallow positioning — could
     trade some `fly_out` → `single` if the IF coverage is the
     pull side.

4. **Across-game adaptation memory**. Currently `last_shift_alignment`
   and `shift_streak` are transient — rebuilt from defaults each
   game. Persisting per-batter shift memory across a series (or
   season) would model "the league has figured this guy out."

5. **Bunt-against-shift requires speed > 0.55**. A slow batter
   bunting against an infield shift is realistic in real ball
   but currently skipped. Could lift the gate.

6. **Pitcher-side adaptability**. A high-`command` pitcher with
   the infield shift on could LOCATE pull-side specifically to
   force the batter into the shift. Currently the pitcher's
   command doesn't influence contact direction.

7. **Verification**. Numbers above all pending re-sim. If anything
   blows up (R/team-game collapses, shift conversion runs hot,
   adaptability invisible) the relevant constants are all in
   `o27/config.py` under clear section headers.

---

## Commit trail

```
f286e3c  Lift HR rate and remove medium-contact 2C gate
14a17d5  Stop gating 2C-driven scoring: skill-conditional strike burn
         + lifted advancement floors
4b7efdb  Reframe 2C as runner-advancement mechanic, not hit creation
         (later partially reverted)
136cd1f  Revert "credit hit only on runs": a 2C that advances is a hit
63a39ed  Count-aware + late-game 2C frequency lifts
9d47fb4  Remove 2-strike 2C lift (wrong O27 frame); boost late-game
         multiplier
2a012bb  Defensive shifts (first cut): spray rating + manager
         shift_aggression + mechanic + telemetry
58b61f8  Shifts phase 2: outfield shift, adaptability rating,
         leverage ratchet, bunt-against-shift
```

The `4b7efdb`/`136cd1f` pair is the visible record of the
"credit hit only on runs" misinterpretation — kept in history
rather than squashed, because it documents the conceptual
correction the user made ("every one of those 2C hits is a hit").
