# After-Action Report — Joker rating decay + intentional walks

**Date completed:** 2026-05-20
**Branch:** `claude/balance-joker-stats-SwWKh`
**Commits:** `febd87e` (rebalance), `ba5ec8b` (IBB display fix)

---

## TL;DR

One joker per team was racking up 15-17 PAs per game with a slash line
that pulled the entire team's offensive stats out of shape — and there
was no in-game counter-pressure because the engine had **zero**
intentional-walk mechanic. Real managers would walk a hot bat with 1B
open; this engine never did.

Two new mechanics close the loop:

1. **Per-AB joker rating decay** — each successive use this game
   shrinks the joker's effective rating deviations on a hardcoded
   curve. Full strength on use #1, floors at 50% on the 10th+ use.
   The manager can keep inserting them; they just stop being
   productive — which is the realistic ceiling, not a rule.
2. **Intentional walks** — new `intentional_walk` event triggered by
   in-game hot streak + situational leverage, with a dedicated
   `mgr_ibb_aggression` persona dimension so sabermetric skippers
   walk less and old-school skippers walk more.

Plus three supporting changes: freshness-based joker selection (no
more single-joker monopoly), a pool fatigue dampener on the manager's
insertion probability, and removal of the legacy `hr_weight_bonus`
HR boost.

In a 120-game sample: heaviest joker averages **6 PAs/game (max 9)**
vs. the prior 15-17 outliers; 9+ outcomes at 0.06% of insertions;
IBBs at ~2/game with persona variation from 1.4 (sabermetric) to 2.7
(dead-ball).

---

## What was asked for

User flagged two related problems in one message:

> "We need to look at the jokers and how they're overpowered. It is
> not realistic for one guy to hit that well over the course of one
> game. At some point you'd walk the joker, you wouldn't pitch to
> them. ... If I had a joker killing me like that I definitely would
> intentionally walk the joker. ... The manager should be programmed
> to not overuse the jokers — maybe like a joker having 7 or 8 or 9
> at-bats a game is probably OK but when it starts to get into 17,
> that's a little ridiculous. ... I don't want a hard cap but I think
> as it sets right now the stats are looking a little ridiculous."

Mid-implementation the user steered the approach away from a
probability soft-cap to a **rating-decay** model:

> "The way to solve this realistically is to just create a harsher
> decay on jokers. Every joker usage comes with a pretty heavy decay
> metric. ... After 5 uses the K penalty kicks up. After 7 it gets
> higher. At 10 the joker's effectiveness is 50% of their normal
> ratings."

Then refined again on the IBB display:

> "In real baseball box scores intentional walks are at the bottom of
> the note section next to other things like doubles, triples, etc.
> They're not actually inside the part of the box score with walks."

---

## Root cause analysis

Three forces conspired to produce the 17-PA-per-joker pattern:

### 1. Joker selection always picked highest skill

`manager.should_insert_joker` at lines 538-541 and 569 (pre-change)
used `max(eligible, key=lambda j: j.skill)`. With three jokers in the
pool, the same elite joker was selected every time. The other two
sat. There was no usage tracking, no rotation, no falloff.

### 2. No effective cap on insertion rate

The modern `should_insert_joker` path did NOT check `JOKER_MAX_PER_HALF`
— that cap only applied to the unused legacy archetype path. The
weak-hitter override fired 75-95% of the time when the next batter was
below 0.44 skill. The leverage path capped at 35% per PA but had no
per-game ceiling. Across a 27-out half with multiple high-leverage
spots, a single joker could rack up 15+ PAs.

### 3. Zero counter-pressure from intentional walks

Grepping the engine for "intentional" / "IBB" / "pitch_to" returned
zero hits. Walks were purely passive — 4 balls and done. There was no
strategic mechanism for a fielding manager to refuse to pitch to a
batter who was killing them.

The legacy `hr_weight_bonus` from the Phase-8 archetype era was a
fourth, smaller, contributor — adding extra HR weight on hard contact
for jokers specifically. It wasn't the headline problem (the headline
was *frequency*, not per-AB hitting power) but the user explicitly
asked for it gone since it double-counted what the modern `power`
rating already drives.

---

## What was built

### 1. Per-game batter stats on `GameState`

`o27/engine/state.py`. Added `batter_game_stats: dict` keyed by
`player_id`, with values `{pa, h, bb, joker_pa}`. Helper method
`state.bgs(pid)` does get-or-create. Resets naturally per game since
each `GameState` instance gets a fresh dict.

Counters updated in `o27/engine/pa.py`:
- `_end_at_bat` ticks `pa` and (if `batter_override is not None`)
  `joker_pa` before clearing the override.
- `_walk` ticks `bb`.
- Hit-credit branches (run path + stay path) tick `h`.

Read by the joker decay system (uses `joker_pa`) and the intentional
walk decision (uses `pa` / `h` for the hot-streak factor).

### 2. Joker rating decay (the core mechanic)

`o27/engine/prob.py`. New `_joker_decay_factor(prior_pa_count)`
returns a multiplier in `[0.50, 1.00]` per the user's spec:

| Use # | Multiplier | Notes |
|---|---|---|
| 1 (count=0) | 1.00 | fresh, full effectiveness |
| 2-4 | 0.98 → 0.91 | small dip |
| 5 (count=4) | 0.85 | light penalty |
| 6 (count=5) | 0.78 | K penalty kicks up |
| 7 (count=6) | 0.70 | |
| 8 (count=7) | 0.62 | much steeper |
| 9 (count=8) | 0.55 | |
| 10+ (count≥9) | **0.50** | floor — anomaly territory |

The multiplier folds into the existing `today_condition` scalar in
`_pitch_probs` (line 264) so all batter-driven probability shifts —
`b_dom` (skill), `eye_dev`, `con_dev` — feel the sag together. Same
scalar is also applied in `contact_quality` (so power_tilt sags too)
and in `resolve_contact` (so `power_dev` for the HR-redistribute path
sags).

Threaded as an optional `joker_decay: float = 1.0` parameter through
`_pitch_probs`, `pitch_outcome`, and `contact_quality` so callers
that don't care (legacy tests, identity-invariant paths) see exactly
the pre-change behavior.

`_resolve_joker_decay(state, batter)` returns `1.0` unless
`state.batter_override is batter`, in which case it returns the
decay factor for the joker's prior `joker_pa` count. Counter is
incremented AFTER the AB in `_end_at_bat`, so during the AB it
reflects PRIOR usage — the first joker insertion sees count=0,
decay=1.0 (no penalty).

### 3. Pool fatigue dampener on insertion probability

`o27/engine/manager.py`. The rating decay alone was insufficient —
the manager kept inserting decayed jokers anyway, hitting 8.3% at 9+
PAs in initial sims (target was ~2%). The fix: in
`should_insert_joker`, look up the freshest joker's `joker_pa` count
and apply a probability multiplier:

| Freshest joker's PA count | Multiplier |
|---|---|
| 0-3 | 1.00 (pool fresh) |
| 4 | 0.90 |
| 5 | 0.70 |
| 6 | 0.50 |
| 7 | 0.35 |
| 8 | 0.22 |
| 9+ | 0.12 (anomaly) |

Multiplies both the weak-override `weak_p` and the leverage-roll
`insert_p`. Not a hard cap — a clutch high-leverage spot can still
fire past 8 uses, just at much lower roll rates.

### 4. Freshness-based joker selection

`_pick_freshest_joker(eligible, state)`: picks the joker with the
lowest `joker_pa` count, breaking ties by skill. Replaces both
`max(..., key=skill)` calls in `should_insert_joker`. Combined with
the rating decay, this spreads usage across all three jokers — the
old 15/0/0 monopoly pattern is gone.

### 5. Intentional walks

**Decision function:** `manager.should_intentional_walk(state, rng)`.

Hard gates (any of these → no IBB):
- 1B occupied (would just give a free base ahead)
- 2 outs with bases empty (no leverage)
- Score gap > `IBB_MAX_SCORE_GAP` (blowout)
- Super-inning (separate format)

Soft probability:
```
hot     = max(0, in_game_AVG − IBB_AVG_FLOOR) × IBB_HOT_SCALE
        + (IBB_HOT_HITS_BONUS if h ≥ IBB_HOT_HITS_THRESHOLD else 0)
elite   = max(0, batter.skill − IBB_SKILL_FLOOR) × IBB_SKILL_SCALE
late    = state.outs / 27
risp    = 1 if runners on 2B or 3B else 0
leverage = late × (0.4 + 0.6 × risp)
agg     = state.fielding_team.mgr_ibb_aggression  (default 0.5)

p = IBB_BASE_PROB + (hot + elite + leverage) × (IBB_AGG_FLOOR + IBB_AGG_SCALE × agg)
p = min(IBB_MAX_PROB, max(0, p))
```

**Event wiring:** `prob.py:_try_manager_action` checks
`should_intentional_walk` AFTER the joker-insertion decision (so the
IBB call is made against the actual batter, including a just-inserted
joker). Returns `{"type": "intentional_walk"}`.

**Apply path:** `pa.apply_event` handles `"intentional_walk"` by
logging a play-by-play line and routing through `_walk(state)` so BB
stats, force-advances, and AB-end behave identically to a 4-ball
walk.

**Persona dimension:** new `mgr_ibb_aggression: float = 0.5` field on
`Team`. Varied across the 15 manager archetypes in
`o27v2/managers.py`:

| Archetype | ibb_aggression |
|---|---|
| sabermetric_max | 0.15 (won't walk anyone the model says is gettable) |
| modern | 0.40 |
| gambler | 0.20 |
| balanced | 0.50 |
| dead_ball | 0.75 |
| old_school | 0.80 |
| hot_hand | 0.85 |

Threaded through `o27v2/managers.py` archetype rolls,
`o27v2/sim.py:_db_team_to_engine`, `o27v2/db.py` schema (with
column-add migration), and `o27v2/league.py` team-seed INSERT.

### 6. IBB display: footnote, not column

First pass added an `IBB` column to the batting line table. User
flagged that real MLB box scores list IBB as a footnote at the
bottom alongside 2B/3B/HR notes, not as a column. Reverted the
column and added a footnote line per team:

```
Run rate:  Millbrook Foxes  0.296    Ironvale Bears  0.259
Stays:     Millbrook Foxes  2    Ironvale Bears  1
IBB (Millbrook Foxes): M. Ashby; G. Osei.
IBB (Ironvale Bears): T. Wachowski.
```

Multiple IBBs per player render as `Name (count)`. Implemented as a
helper in `render.render_box_score` that filters non-zero
`BatterStats.ibb` entries and joins names; the template emits the
notes line only when the team has any IBBs.

### 7. Removed `hr_weight_bonus` boost

`o27/engine/prob.py:resolve_contact` no longer reads
`batter.hr_weight_bonus` or applies the
`archetype_dev_hr × cfg.POWER_REDIST_HR` boost to the line_out → HR
edge. The Player field is retained as a zeroed legacy stub so v2 DB
rows / seed code keep loading without churn. Comment updated to
clarify the field is unused.

---

## Calibration

Target distribution per user spec:
- 7 PAs/game = "anomaly" threshold (most jokers below this)
- 8 PAs/game = uncommon
- 9+ PAs/game = extremely rare (~2% of joker-insertion outcomes)
- No hard cap

After tuning the decay curve and pool fatigue dampener, in 120 games
(seeded 0-119) using `make_foxes()` / `make_bears()` rosters:

```
Distribution of per-game per-joker PA counts:
   1 PAs:  26 jokers (  3.6%)
   2 PAs:  73 jokers ( 10.2%)
   3 PAs: 113 jokers ( 15.8%)
   4 PAs: 144 jokers ( 20.1%)
   5 PAs: 160 jokers ( 22.3%)
   6 PAs: 143 jokers ( 20.0%)
   7 PAs:  46 jokers (  6.4%)
   8 PAs:   9 jokers (  1.3%)
   9 PAs:   2 jokers (  0.3%)

Heaviest joker per game: avg=6.0, max=9
Insertions resulting in 9+ PA: 2/3157 = 0.06%
```

Heaviest-joker average sits in the **5-7 PA comfort zone** the user
called out as realistic. The old 15-17 PA outliers are gone. 9+ PA
games still happen but are vanishingly rare.

IBB rate across 30 games at varying personas:

| `mgr_ibb_aggression` | IBBs/game |
|---|---|
| 0.10 | 1.40 |
| 0.50 | 2.17 |
| 0.90 | 2.70 |

Persona variation is real and visible. Absolute rate (~2/game at
neutral) is higher than MLB's ~0.3/game, but the user's anchoring
was that the engine had *zero* IBBs before — so the calibration is
toward visibility-of-mechanic rather than realism-of-frequency.
Drop `IBB_MAX_PROB` (currently 0.55) or `IBB_AGG_SCALE` (currently
0.60) in `config.py` to dial it down.

---

## Testing

- 183 of 183 non-flake tests pass post-change. The two pre-existing
  failures (`test_weather_calibration`,
  `test_phase8_db_migration::test_init_db_*`) fail on unmodified
  `main` too — confirmed via `git stash && pytest && git stash pop`.
  Not regressions.
- `test_roll_manager_shape_for_new_types` updated to expect the new
  `mgr_ibb_aggression` key in the roll output.
- End-to-end sanity: `python -m o27.main --seed N` produces a clean
  box score with the IBB footnote, joker insertions visible in the
  play-by-play, and reasonable joker AB counts per row.

---

## Files modified

| File | Reason |
|---|---|
| `o27/engine/state.py` | per-game stats dict, `mgr_ibb_aggression` field, `hr_weight_bonus` legacy comment |
| `o27/engine/pa.py` | stat counter updates, `intentional_walk` event handler |
| `o27/engine/prob.py` | `_joker_decay_factor`, `_resolve_joker_decay`, decay wiring through `_pitch_probs` / `contact_quality` / `resolve_contact`; IBB hook in `_try_manager_action`; `hr_weight_bonus` boost removed |
| `o27/engine/manager.py` | `_pick_freshest_joker`, `_pool_fatigue_mult`, `should_intentional_walk` |
| `o27/config.py` | new IBB constants; joker decay breakpoint constants; `hr_weight_bonus` comment cleanup |
| `o27/render/render.py` | IBB stat update in `_update_stats`; IBB notes helper in `render_box_score` |
| `o27/render/templates/box_score.j2` | IBB footnote line (column was added then reverted) |
| `o27/render/templates/play_by_play.j2` | `intentional_walk` event line |
| `o27/stats/batter.py` | `ibb: int = 0` field |
| `o27v2/managers.py` | `ibb_aggression` field on Archetype, varied across all 15 archetypes |
| `o27v2/sim.py` | read `mgr_ibb_aggression` from team_row |
| `o27v2/db.py` | schema column + migration for `mgr_ibb_aggression` |
| `o27v2/league.py` | persist `mgr_ibb_aggression` in team INSERT |
| `o27v2/tests/test_managers.py` | expect new key in roll-shape test |

---

## Process notes

Three plan iterations before implementation started:

1. **First plan:** soft-cap on insertion probability via per-joker
   usage multiplier. User read it and steered away — wanted decay on
   the joker themselves, not a cap on the manager.
2. **Second plan (decay-based):** rewrote the joker section to use
   a rating-decay mechanism modeled on the existing pitcher fatigue
   system. User approved.
3. **Mid-implementation refinement:** removing `hr_weight_bonus`,
   adding `mgr_ibb_aggression` as a dedicated persona dimension,
   tuning the decay curve to hit specific PA targets.

Calibration took two rounds: first sim with rating decay alone
showed 8.3% at 9+ PAs (because the manager didn't see the decay and
kept inserting). Adding the pool fatigue dampener overshot in the
opposite direction (0% anomalies). Softening the dampener to the
final curve landed at 0.06% — well below the 2% ceiling, with the
right shape (concentrated 4-6 PAs, light 7-8 tail, rare 9-10
anomalies).

The display fix (IBB as footnote vs column) was the user's
correction — I'd added the column on autopilot from the plan without
checking MLB conventions. Worth remembering: when adding a stat the
real sport already tracks, look at how the real sport displays it
before designing the UI.

---

## Follow-ups (resolved — commit `<pending>`)

All three follow-ups from the original pass were addressed in a
second round.

### 1. `hard_contact_delta` removed

The other legacy Phase-8 archetype field. `contact_quality()` read it
as `arch_delta` and added it directly to `hard_p` / subtracted from
`weak_p` — the same shape of unscaled additive boost as
`hr_weight_bonus`, and like that field it bypassed the per-game joker
decay entirely (a decayed joker still got the full hard-contact
bump). Removed the `arch_delta` read and its two terms in the
`weak_p` / `hard_p` calculation. Field retained as a zeroed legacy
stub on `Player` (same treatment as `hr_weight_bonus`) so v2 DB rows
keep loading. Identity-preserving for any player with the default
0.0 value, so `test_realism_identity` and friends stay green.

### 2. IBB rate dialed down

Was ~2.2 IBBs/game at neutral aggression — read as spammy. Tuned the
config:

| Constant | Before | After |
|---|---|---|
| `IBB_MAX_PROB` | 0.55 | 0.35 |
| `IBB_HOT_HITS_BONUS` | 0.35 | 0.30 |
| `IBB_AVG_FLOOR` | 0.300 | 0.350 |
| `IBB_HOT_SCALE` | 0.80 | 0.70 |
| `IBB_SKILL_FLOOR` | 0.65 | 0.70 |
| `IBB_SKILL_SCALE` | 0.50 | 0.40 |
| `IBB_AGG_FLOOR` | 0.20 | 0.12 |
| `IBB_AGG_SCALE` | 0.60 | 0.45 |

Measured rates (40-game samples) after the change:

| `mgr_ibb_aggression` | Before | After |
|---|---|---|
| 0.10 | 1.40 | 0.82 |
| 0.50 | 2.17 | 1.32 |
| 0.90 | 2.70 | 2.25 |

Still above MLB's ~0.3/game by design (visibility over strict
realism), but no longer fires every other half at neutral. Persona
spread is preserved and slightly widened.

### 3. Auction valuation — rechecked, no change needed

`o27v2/auction.py` reads `mgr_joker_aggression` only to set a team's
general *bid aggression* (0.85-1.35 multiplier), not a joker-specific
valuation. `o27v2/trades.py:trade_value` scores jokers by their raw
per-AB `skill` (via the position-player branch) — and per-AB skill is
exactly what the decay leaves untouched; only aggregate per-game
output sags. So the auction values a joker as "an elite bat for their
first several PAs," which is still accurate. No joker-specific
overvaluation exists to correct. (Pre-existing second-order point: a
joker provides no defense and limited PAs vs. a full-time regular of
equal skill, so jokers are arguably modestly overvalued in the
abstract — but that's a pre-existing modeling choice unrelated to the
decay change, and not worth a joker-specific penalty that could
destabilize the trade/auction balance.)
