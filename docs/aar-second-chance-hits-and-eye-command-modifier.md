# After-Action Report — Second-Chance Hits column + eye-vs-command second-swing modifier

**Date completed:** 2026-05-06
**Branch:** `claude/fix-dark-theme-baseball-terms-7UhIv`
**Predecessor:** `aar-stay-advancement-and-2c-rbi.md` (Phase 11A/B/C)

---

## Context

Phase 11A/B/C shipped: surfaced 2C-RBI / 2C-RBI% analytics, added the
medium-contact `[2,2,2]` runner advancement, found that the per-player
shape-correction signal at the contact-rating level was weak (Δ-by-
contact decile slope was nearly flat). Closing notes documented that
the conversion mechanic itself wasn't measurable from the schema —
`stays` and total `hits` were tracked but "hits credited on a 2C
event" was folded into total hits with no separate counter.

User then articulated a target: top hitters should convert ~60–70%
of 2C events into hits, marginal hitters 30–40%. Worth measuring
against. User then locked the implementation path:

> Subsequent swings within the same PA get two competing modifiers:
> - Batter side: contact_quality bonus scaled by batter.eye
>   (high-eye reads the pitcher across multiple swings)
> - Pitcher side: contact_quality penalty for the batter scaled
>   by pitcher.command (pitcher knows what's coming on swing 2)
>
> Don't touch should_stay_prob. Don't add anything that changes
> who CAN take second-chance ABs or how often they're decided.
> The only thing changing is the OUTCOME quality on second and
> third swings within a PA, modulated by talent.

---

## What was built

### 1. `stay_hits` column

`o27v2/db.py` — added `stay_hits INTEGER DEFAULT 0` to the
`game_batter_stats` schema and to the migration ALTER list. Symmetric
with `stay_rbi` and `multi_hit_abs`.

`o27/stats/batter.py` — `BatterStats.stay_hits: int = 0`.

`o27/render/render.py` — increment `s.stay_hits += 1` next to the
existing `s.hits += 1` when `stay_hit_credited` fires (line 865).
Added `stay_hits` to `_stat_delta`'s field list and to the
team-totals propagation in `_totals` (renderer's own per-game
aggregation path).

`o27v2/sim.py` — `_extract_batter_stats` carries `stay_hits` from
`bstat`; both INSERT call-sites (sub-game phase rows and the
`_insert_batter_stats` helper) write the column.

`o27v2/web/app.py` — bulk-added `COALESCE(SUM(bs.stay_hits),0) as
stay_hits` to all 9 SELECT queries that already SUM `stay_rbi`.
Added `stay_hits` to both `_BAT_NUM` consolidation tuples.
`_aggregate_batter_rows` derives `stay_conv_pct = stay_hits / stays`.

### 2. Eye-vs-command swing-2+ modifier

`o27/engine/state.py` — new field `current_at_bat_swings: int = 0`
on `GameState`. Counts contact events within the current AB.

`o27/engine/pa.py` — bumped at top of `_resolve_contact` (so
"swings_in_ab=0 on swing 1, =1 on swing 2, =2 on swing 3"). Reset
in `_end_at_bat` alongside `current_at_bat_hits`.

`o27/config.py` — new constants:
```python
SECOND_SWING_EYE_SCALE: float     = 0.20
SECOND_SWING_COMMAND_SCALE: float = 0.20
```

`o27/engine/prob.py` — `contact_quality()` accepts `swings_in_ab`
parameter. When `swings_in_ab >= 1`, augments the matchup `shift`:
```python
eye_dev = (batter.eye - 0.5) * 2 * plat
cmd_dev = (pitcher.command - 0.5) * 2
shift += (eye_dev * SECOND_SWING_EYE_SCALE
          - cmd_dev * SECOND_SWING_COMMAND_SCALE)
```
Caller (`prob.py:1087`) passes `swings_in_ab=state.current_at_bat_swings`.
`should_stay_prob` deliberately untouched — the gate on WHO can take
2C remains universal per the user's lock.

### 3. UI surfacing

- `stats_browse.html` Advanced + All batting: 2C-H counting column
  and 2C-Conv% rate column added next to existing 2C cluster
- `leaders.html`: 2C-H counting and 2C-Conv% rate leaderboard cards
- `player.html` Advanced batting row: 2C-H + 2C-Conv% columns
- `distributions.html`: `stay_conv_pct` histogram via the bat_specs list

---

## Verification

Re-sim path: `resetdb` → `sim 2430` → `backfill_arc`.

### Verification 1 — 2C-Conv% by eye decile

| Verification target | Result |
|---|---|
| Top eye decile (~72) | **71.8%** |
| Bottom eye decile (~22) | **75.4%** |
| Spec target | top 60-70%, bottom 30-40% |

The conversion rate is ~74% league-wide and **essentially flat
across the eye spectrum, slightly inverted at the extremes**.
**Spec NOT met.**

### Verification 2 — Δ (BAVG-PAVG) by contact decile

| Decile | Contact | Δ |
|---|---|---|
| 1 | 23.8 | +.0527 |
| 5 | 42.3 | +.0554 |
| 10 | 72.8 | +.0606 |

Slope still essentially flat (matches pre-fix Δ-by-contact decile
behavior documented in the previous AAR). **Spec NOT met** (target
was top decile ≥ .080, bottom decile ≤ 0).

### Verification 3 — League stay_rate stable

- Post-fix: **5.51%** (pre-fix: 5.47%)
- Inside the 4-8% target band. ✓ Spec met (no gate change, behavior
  as expected).

### Side measurements (informational, not part of spec gate)

- League PAVG = .2837; BAVG = .3369
- hits/team-game = 12.80; runs/team-game = 12.67
- League HR total = 4549
- 14/14 routes return 200
- 7/7 redistribute tests pass

---

## Why the spec wasn't met (root cause)

The architecture the user prescribed — *"the only thing changing is
the outcome quality on second and third swings within a PA"* — is
load-bearing in a way that limits how much eye signal can reach the
2C-Conv% metric the spec measures.

**Most 2C events happen on swing 1 of an AB.** A 2C event = the
batter chose `stay` after a contact. The chain caps at 3 swings
(count → 3 ends the AB). Most ABs that touch 2C territory have
exactly one stay; chains of 2 or 3 stays are rare. So when we
measure `stay_hits / stays`, the bulk of the denominator is swing-1
2Cs — which the modifier explicitly does not touch.

For swing-1 2Cs, the conversion mechanic is dominated by Phase 11C's
medium-contact `[2,2,2]` advancement: any medium-contact 2C
basically always credits a hit (a runner advances). Weak-contact 2Cs
use the underlying hit_type's runner_advances which still produce
some advancement most of the time. The base conversion rate is high
(74%) and not very eye-sensitive at swing 1.

The modifier IS firing on swing 2+, but those events are a small
share of the 2C population, so the population-level conversion rate
is dominated by swing-1 mechanics that the modifier doesn't reach.

**The fix that would meet the spec is one of:**
1. Extend the eye/command modifier to swing 1 as well — explicitly
   scoped out by the user.
2. Revisit Phase 11C's `[2,2,2]` base conversion to lower the floor
   so eye signal has more headroom (would also lower league
   conversion; the spec's implied league mean is ~50%).
3. Add a separate eye-driven conversion gate that runs after the
   stay decision — modifies `stay_hit_credited` directly based on
   batter.eye vs pitcher.command, decoupled from contact_quality.

Both (2) and (3) re-open Phase 11C's design. (1) violates the user's
scope lock. None of these is appropriate to ship without an explicit
green light.

---

## What shipped vs what didn't

**Shipped:**
- Permanent `stay_hits` schema column with full read path
  (aggregator, leaderboard, advanced view, all view, distributions,
  player page)
- Permanent eye-vs-command second-swing modifier in
  `contact_quality`, scoped to swings 2+ as specified
- Diagnostic infrastructure to keep measuring 2C-Conv% going forward

**NOT shipped (and why):**
- The eye-driven conversion spread the spec called for. Architecture
  prescribed by the user (modifier scoped to subsequent swings) does
  not have enough surface area to produce a 30-pp top-vs-bottom
  spread, because the bulk of 2C events are swing 1.

---

## Files touched

| File | Change |
|---|---|
| `o27v2/db.py` | `stay_hits` column + ALTER migration |
| `o27/stats/batter.py` | BatterStats.stay_hits field |
| `o27/render/render.py` | s.stay_hits increment, _stat_delta field, _totals propagation |
| `o27v2/sim.py` | extract + INSERT stay_hits |
| `o27v2/web/app.py` | aggregator stay_conv_pct, 9 SELECTs add stay_hits, _BAT_NUM tuples, distributions bat_specs |
| `o27v2/web/templates/stats_browse.html` | 2C-H + 2C-Conv% on Advanced + All |
| `o27v2/web/templates/leaders.html` | 2C-H + 2C-Conv% leaderboard cards |
| `o27v2/web/templates/player.html` | 2C-H + 2C-Conv% on Advanced batting |
| `o27/engine/state.py` | current_at_bat_swings field |
| `o27/engine/pa.py` | bump at top of _resolve_contact, reset in _end_at_bat |
| `o27/engine/prob.py` | contact_quality swings_in_ab parameter + modifier; caller passes from state |
| `o27/config.py` | SECOND_SWING_EYE_SCALE + SECOND_SWING_COMMAND_SCALE |

Commits: `13fcf86` on `claude/fix-dark-theme-baseball-terms-7UhIv`.

---

## Follow-ups for user decision

1. **Re-architect the conversion-spread mechanism** — the prescribed
   "swing 2+ only" scope can't produce the spec's spread because
   most 2Cs are swing 1. Three options sketched above (extend to
   swing 1, lower the [2,2,2] floor, or separate eye-driven
   conversion gate). Each re-opens a previous decision.

2. **Accept current behavior and adjust the spec** — if the design
   intent is "subsequent swings reward talent" rather than
   "population-level 2C-Conv% reflects talent", the current
   implementation does what was asked. The spec target (60-70 / 30-40)
   would shift to "swing-2+ conversion in chains of 2+ stays shows
   eye signal" — which we have data to test if requested.

3. **Per-PA event log (Phase 11D, still parked)** — would let us
   separately measure swing-1 vs swing-2+ conversion, which would
   make finding (2) testable directly. Would also unlock the
   slugger-high-leverage 2C usage check parked from Phase 11A.
