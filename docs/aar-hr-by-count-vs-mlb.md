# After-Action Report — Home-runs-by-count: shaping O27's count → power curve

*(MLB is used below only as a diagnostic mirror — see the Framing note. O27 is
not, and is not becoming, an MLB sim.)*

**Date completed:** 2026-06-16
**Branch:** `claude/new-session-w4nwix`
**Scope:** diagnose and fix the HR-by-count distribution (persist the count,
earned-power model, smart-batter pitch table, per-count power profile),
including a mid-flight O27 rules bug and its correction.

> **Framing — this is NOT an MLB sim.** A real-MLB chart is used throughout as a
> *diagnostic mirror* — it's good at exposing structural problems (a flat,
> count-agnostic HR rate; first-pitch bombs everywhere). It is **not the
> target**, and "distance to MLB" is **not** the success criterion. O27 is its
> own sport with its own incentives. The actual objective is an internally
> coherent **count → power ordering** that matches how O27 should play:
> contact-mode on 0-0, committed/bomb-mode on a full count, defending when
> buried. The MLB columns below are kept because they're a handy reference for
> spotting flatness, not because we're trying to land on them.

---

## TL;DR

A real-MLB home-runs-by-count chart (Retrosheet, 1910–2025), used as a
diagnostic, exposed two structural problems in O27: it was hitting **~35% of all
home runs on the first pitch (0-0)**, and a home run was **count-agnostic**
(≈4% of every ball-in-play became a HR at *every* count — power was unrelated to
the count). We:

1. **Logged the count** — `game_pa_log` now stores `balls`/`strikes`, so
   outcome-by-count is a live-DB query, not just a headless harness trick.
2. **Made power earned** — contact ahead in the count carries (EV shift by
   `balls − strikes`); HR/BIP now rises in hitters' counts instead of being flat.
3. **Made batters smarter** — rewrote `PITCH_BASE` so they take far more
   early-count pitches (work the count, ~3.6 pitches/PA), which collapses the
   cheap first-pitch HR.

A fourth change followed (Part 3d): a per-count **power profile** — one
multiplier on hard contact per count, encoding the batter's *approach* (0-0 =
square it up, not a bomb; full count / ahead = commit to power). It subsumes an
earlier behind-count penalty and gives the HR/BIP curve the intended O27 shape.

Net (the win, stated in O27 terms): power is now **ordered by the count's
implied approach** — lowest on 0-0 and when buried, highest on a full count and
ahead. A full-count homer is now more likely per contact than a first-pitch one
(HR/BIP 5.4% vs 3.7%), where before it was *backwards*. 0-0's share of all
homers fell from ~35% to ~20%. HR volume preserved, identity guards green. (For
reference only, the spread-vs-the-MLB-mirror narrowed from 50.2 to 15.2.)

A subtle O27 rules error was caught and fixed along the way (see Part 3): the
first cut of the smart-batter table tried to "protect the plate" by fouling off
two-strike pitches — illegal logic here, because **3 fouls = a foul-out**. The
foul-out rate is the tell, and it's now instrumented in the harness.

---

## The reference chart (the target)

Philip Bump / CT Insider, source Retrosheet, regular-season MLB 1910–2025.
Share of **all** home runs hit on each count (and raw counts):

| | balls 0 | 1 | 2 | 3 |
|---|---:|---:|---:|---:|
| **0 strikes** | 18.3% (35,578) | 12.0% (23,301) | 5.6% (10,858) | 0.6% (1,233) |
| **1 strike** | 9.7% (18,742) | 11.1% (21,561) | 8.3% (16,128) | 4.9% (9,589) |
| **2 strikes** | 3.5% (6,857) | 7.6% (14,678) | 9.1% (17,669) | 9.2% (17,920) |

Key real-world features: a big 0-0 ambush spike (18.3%), damage concentrated in
hitters' counts, and **deep counts staying live** — 2-2 and 3-2 each ~9%
because so many real PAs grind that deep.

---

## Part 1 — The audit

### Method

The engine models pitches and a ball-strike count (`Count`,
`o27/engine/state.py:42`), with per-count pitch-outcome probabilities in
`o27/config.py:PITCH_BASE`. But the count an outcome happened on was **not
persisted** — `game_pa_log` (`o27v2/db.py`) had no balls/strikes columns, and
the count flowed through the render layer only to be discarded. So the live DB
couldn't answer "how many HRs on 0-0".

The harness `scripts/hr_by_count.py` runs fresh headless games (Foxes vs Bears,
the `main.py` rosters) and captures `state.count` at the instant the provider
yields each home-run `ball_in_play`. That instant *is* the count the HR was hit
on: the provider returns the event and `apply_event()` only afterward mutates
the count, so `state.count` still holds the pre-contact tally. It tallies HRs
**and** all balls-in-play by count, so HR/BIP is observable.

Sample: 2,500 games → 5,311 HRs / 126,718 balls in play.

### Results (pre-tuning)

| Count | O27 HR% | MLB HR% | diff | O27 BIP% | HR/BIP |
|-------|--------:|--------:|-----:|---------:|-------:|
| 0-0 | 35.0 | 18.3 | **+16.7** | 34.8 | 4.22% |
| 1-0 | 12.4 | 12.0 | +0.4 | 11.8 | 4.38% |
| 2-0 | 5.4 | 5.6 | −0.2 | 4.5 | 4.99% |
| 3-0 | 1.9 | 0.6 | +1.3 | 1.9 | 4.21% |
| 0-1 | 13.5 | 9.7 | +3.8 | 13.6 | 4.17% |
| 1-1 | 7.3 | 11.1 | −3.8 | 7.9 | 3.92% |
| 2-1 | 4.4 | 8.3 | −3.9 | 3.9 | 4.64% |
| 3-1 | 2.1 | 4.9 | −2.8 | 2.0 | 4.41% |
| 0-2 | 6.5 | 3.5 | +3.0 | 7.1 | 3.82% |
| 1-2 | 5.5 | 7.6 | −2.1 | 6.2 | 3.71% |
| 2-2 | 3.6 | 9.1 | **−5.5** | 4.0 | 3.83% |
| 3-2 | 2.4 | 9.2 | **−6.8** | 2.4 | 4.33% |

Total variation: sum of |Δ| = **50.2** (≈2× TV).

### Diagnosis — two findings, one root cause

**1. A home run was count-agnostic.** The HR/BIP column was flat (3.7–5.0% at
every count). So the O27 HR% column was essentially the O27 BIP% column — the
HR-by-count distribution *was* the ball-in-play-by-count distribution. Real
baseball isn't like that: hitters do disproportionate damage ahead in the count
and put weaker, defensive contact in play with two strikes, so HR/BIP rises in
hitters' counts.

**2. O27 put far too many balls in play on the first pitch.** 34.8% of *all*
balls in play happened at 0-0 because every PA flows through 0-0 and
`PITCH_BASE[(0,0)]` gave the first pitch a 0.23 contact probability. With
at-bats ending that early, the deep counts (2-2, 3-2) that carry ~18% of real
homers saw only ~6% of O27's. The pitch model was **front-loaded and
count-flat** (contact ~0.23 at 0-0 → 0.20 with two strikes), so the contact
distribution decayed monotonically from 0-0 instead of bulging in deep counts.

---

## Part 2 — The build (what shipped)

### Change 1 — Persist the count

`game_pa_log` gains `balls` / `strikes` (INTEGER, NULL on legacy rows):
- `o27v2/db.py`: columns in CREATE TABLE + idempotent `ALTER TABLE` migration
  (mirrors the existing `outs_before`/`fielder_id` migration pattern).
- `o27/render/render.py`: stamp `ctx.count_balls` / `ctx.count_strikes` on each
  `ball_in_play` `_pa_log` row. `ctx` is the **pre-event** snapshot, so it is
  the pre-contact count — the count the swing happened at.
- `o27v2/sim.py`: extend the `_pa_log` → `executemany` insert column list /
  value tuple.

Verified end-to-end: a 20-game sim wrote 1,091 PA rows, 100% count coverage,
all counts in range, and the live-DB HR-by-count matched the harness.

### Change 2 — Earned power (count-aware contact authority)

`o27/engine/prob.py:resolve_contact` gains a count term on the same `ev_shift`
that already carries the form / RISP EV levers:

```
ev_shift += clamp(CONTACT_COUNT_EV_SCALE * (balls − strikes), ±CLAMP)
```

- `CONTACT_COUNT_EV_SCALE = 2.0` mph per unit, `CONTACT_COUNT_EV_CLAMP = 6.0`
  (`o27/config.py`).
- Flows through the existing physics path (EV → carry → fence in
  `batted_ball.py`), so physics and the persisted EV never disagree.
- **Zero at 0-0** (`balls == strikes`), so the realism identity contract at a
  fresh count is untouched — the live identity tests (`pitch_probs`,
  `contact_quality`) stay green.
- **~Mean-zero** over the league BIP-by-count distribution, so it
  **redistributes** home runs toward hitters' counts rather than inflating
  volume.

### Change 3 — Smart-batter `PITCH_BASE`

Batters take far more early-count pitches; removed contact is routed into
**taken** pitches (called strikes + balls). 3-0 is a near-automatic take. With
two strikes, trimmed whiff weight goes into **contact** (put it in play), never
fouls (see Part 3 for why). Full shipped table is in `o27/config.py:PITCH_BASE`.

---

## Part 3 — The tuning journey (all the data)

### 3a. Calibrating the earned-power scale

Sweeping `CONTACT_COUNT_EV_SCALE` (1,200 games, seed 5000). It fixes the
*hitter's-count* dimension and preserves HR volume, but on its own does almost
nothing to the 0-0 spike or the deep-count shortfall (those are BIP-distribution
problems, not HR/BIP problems):

| scale (mph/unit) | HR/game | 0-0 HR% | deep% | ahead% | TV-dist |
|---:|---:|---:|---:|---:|---:|
| 0.0 | 1.99 | 35.2 | 5.7 | 25.4 | 25.1 |
| 1.5 | 1.97 | 34.3 | 6.1 | 29.8 | 23.5 |
| **2.0** | **2.00** | **33.1** | **6.1** | **31.9** | 24.1 |
| 2.5 | 2.10 | 33.5 | 5.8 | 33.0 | 23.6 |
| 3.0 | 2.02 | 33.6 | 6.2 | 33.7 | 24.1 |

`2.0` chosen: ahead% lands on MLB's 31.4% with flat HR volume. (MLB refs: 0-0
18.3, deep 18.3, ahead 31.4.)

### 3b. The PITCH_BASE work-the-count sweep

Candidate pitch tables that reduce early contact, with `CONTACT_COUNT_EV_SCALE`
= 2.0 active (1,500 games, seed 9000). This is where the 0-0 spike actually
moves — but it costs run environment (more takes → more walks *and* deeper
counts → more two-strike whiffs):

| table | runs/g | K% | BB% | pitches/PA | 0-0 HR% | deep% | TV-dist |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 23.4 | 16.0 | 8.3 | 3.19 | 34.2 | 6.6 | 23.9 |
| moderate | 20.8 | 21.1 | 11.4 | 3.69 | 26.8 | 10.7 | 16.4 |
| strong | 20.5 | 23.4 | 13.0 | 3.93 | 20.9 | 12.5 | 12.3 |
| smart¹ | 21.6 | 19.3 | 13.2 | 3.95 | 21.8 | 12.8 | 12.5 |

¹ "smart" trimmed two-strike whiffs to keep K% down — but parked the removed
weight in **fouls** ("protect the plate"). That is the bug fixed in 3c.

### 3c. The O27 rules bug — and the foul-out tell

**The rule:** in O27 a foul is not free. Three fouls in an at-bat is a
**foul-out** (`o27/engine/pa.py:483–496`; README §rules: *"Three fouls is a
foul-out (no infinite-foul protection like MLB)"*). The foul counter is
independent of the strike counter and keeps climbing to 3 even after strikes
freeze at 2. So "protect the plate by fouling off two-strike pitches" — standard
MLB logic — **manufactures outs** here.

The harness was extended to count foul-outs (a `foul` event when
`state.count.fouls == 2`). Comparing the true pre-tuning table, the buggy
"foul-protect" table, and the corrected "take, don't foul" table (1,500 games,
seed 9000):

| table | foul-out % of PA | 0-0 HR% | deep% | pitches/PA | K% | BB% | runs/g | TV-dist |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ORIG (pre-tuning) | **5.2** | 34.1 | 6.4 | 3.03 | 15.2 | 7.9 | 23.1 | 24.6 |
| SHIPPED-SMART (foul **bug**) | **8.0** | 21.3 | 13.3 | 3.63 | 17.9 | 12.0 | 21.8 | 11.9 |
| CORRECTED (take, don't foul) | **6.5** | 22.7 | 12.6 | 3.57 | 18.2 | 11.6 | 22.1 | 12.9 |

The bug spiked foul-outs **5.2% → 8.0%** of PAs (~1 in 12 PAs ending on a
foul-out, a ~55% jump) — purely from routing two-strike weight into fouls. The
correction deepens counts only by **taking**, keeps foul rates at/below
baseline, and routes trimmed two-strike whiffs into **contact**. Foul-out rate
falls back to **6.5%**; the small residual over baseline is legitimate (more PAs
reach two strikes, so more batters are exposed to the third foul). The thing the
owner cared about — the 0-0 HR share — still drops (34% → 23%), and deep-count
share / pitches-per-PA are essentially unchanged from the buggy version.

### 3d. Count power profile — 0-0 contact-mode, full-count bomb-mode

Two passes here. **First**, an 0-2 fix: a behind-in-the-count penalty that
degraded contact *quality* when buried — the owner's framing, "make whatever
contact 13–29% less effective." `contact_quality` already had the exact
mechanism (the weather `hard_contact_multiplier` block: cut hard-contact prob,
push the lost mass to weak), so it reused that shape with a count-driven
multiplier.

**Then** the owner sharpened the design intent: full-count home runs should be
*more* common, and 0-0 home runs *far less* — because on the first pitch the
hitter is optimizing for good contact, not a bomb, while a full count is maximum
commitment. The earlier build had this exactly backwards (3-2 HR/BIP 4.14% was
*lower* than 0-0's 4.23%).

So the penalty was generalized into a full per-count **`COUNT_POWER_PROFILE`**
(`o27/config.py`) — one hard-contact multiplier per count encoding the approach:

| | balls 0 | 1 | 2 | 3 |
|---|---:|---:|---:|---:|
| **0 strikes** | 0.82 | 1.00 | 1.08 | 1.12 |
| **1 strike** | 0.86 | 1.00 | 1.06 | 1.15 |
| **2 strikes** | 0.71 | 0.86 | 1.05 | **1.25** |

0-0 is suppressed (square it up), behind-counts defend (the old 0-2/1-2 penalty
survives as the low entries), and ahead / **full count (3-2 = 1.25)** commit to
power. `1.0` = neutral; the contact-quality identity test calls
`contact_quality` without a count so the multiplier defaults to 1.0 there — the
contract holds. Threaded via one `count_hard_mult` kwarg, looked up at the call
site (`prob.py` ~2900). The multiplier block moves mass between hard and weak,
so it both cuts (<1) and lifts (>1).

Effect (2,500-game harness): **0-0 HR/BIP 4.23% → 3.67%** (over-share +4.5 →
**+1.3**), **3-2 HR/BIP 4.14% → 5.44%** (now well above 0-0, over-share −4.2 →
−2.7). Total error 24.5 → **15.2**. HR volume and run environment essentially
unmoved (the profile only re-buckets contact quality by count).

---

## Final shipped distribution

Earned power + corrected smart `PITCH_BASE` + count power profile
(2,500 games, seed 1000 → 4,365 HRs / 108,205 BIP):

The `HR/BIP` column is the one that matters — it's the per-contact power by
count, the thing we set out to shape. (`MLB%` is the reference mirror, not a
target.)

| Count | O27 HR% | O27 BIP% | **HR/BIP** | MLB% (ref) |
|-------|--------:|---------:|-----------:|-----------:|
| 0-0 | 19.6 | 21.5 | 3.67% | 18.3 |
| 1-0 | 13.0 | 9.5 | 5.52% | 12.0 |
| 2-0 | 6.0 | 4.0 | 6.08% | 5.6 |
| 3-0 | 2.2 | 1.2 | 7.37% | 0.6 |
| 0-1 | 10.2 | 12.4 | 3.31% | 9.7 |
| 1-1 | 10.3 | 9.0 | 4.61% | 11.1 |
| 2-1 | 6.8 | 5.2 | 5.29% | 8.3 |
| 3-1 | 4.2 | 2.3 | 7.31% | 4.9 |
| 0-2 | 6.4 | 11.9 | 2.17% | 3.5 |
| 1-2 | 7.2 | 10.7 | 2.72% | 7.6 |
| 2-2 | 7.6 | 7.4 | 4.12% | 9.1 |
| 3-2 | 6.5 | 4.8 | 5.44% | 9.2 |

The HR/BIP column now reads exactly like the O27 design intent, which is the
success criterion: lowest when buried (0-2 2.17%, 1-2 2.72%), low-and-contacty
early (0-0 3.67%, 0-1 3.31%), hot when ahead (3-0 7.37%, 3-1 7.31%, 2-0 6.08%),
and a genuine **full-count spike** (3-2 5.44% — well above 0-0). Power is now a
function of the count's implied approach; before, it was flat (~4% everywhere).

0-0 and 0-2 carry the largest *share* of homers, but that's now **volume**, not
power: their per-contact HR rate is the lowest on the grid. Every PA flows
through 0-0, and the work-the-count table funnels ~12% of all balls in play
through 0-2 — so those counts accumulate share even at a low HR/BIP. Moving
them is a take/swing-split question, not a contact-power one. **This is a
deliberate stopping point — the count→power ordering is where it should be.**

---

## Code map

| File | Change |
|---|---|
| `o27v2/db.py` | `balls`/`strikes` columns on `game_pa_log` + migration |
| `o27/render/render.py` | stamp count on each `ball_in_play` `_pa_log` row |
| `o27v2/sim.py` | insert the two new columns |
| `o27/engine/prob.py` | count-aware `ev_shift` in `resolve_contact`; `count_hard_mult` in `contact_quality` + call-site `COUNT_POWER_PROFILE` lookup |
| `o27/config.py` | `CONTACT_COUNT_EV_SCALE` / `_CLAMP`; `COUNT_POWER_PROFILE`; rewritten `PITCH_BASE` |
| `scripts/hr_by_count.py` | audit harness (HR + BIP by count) |
| `scripts/hr_count_tradeoff.py` | tuning harness (HR-by-count + run env + foul-out) |

---

## O27 rules constraints to remember (for the next builder)

- **3 fouls = foul-out.** Fouls are a risk, not protection. Never route "plate
  protection" into fouls. To deepen a count, the only legal lever is **taking**
  (balls + called strikes). The foul-out rate (now in the tradeoff harness) is
  the canary — watch it whenever you touch foul probabilities.
- **Every PA flows through 0-0.** So 0-0 is structurally the biggest single
  bucket of any per-count distribution; the 0-0 share of *anything* is dominated
  by how often the first pitch is put in play.
- **O27 batters are aggressive by design.** The owner explicitly does **not**
  want to mirror MLB exactly — the old run-environment numbers (22–24 RPG, ~18%
  K, ~10% BB) were **never firm targets**. Report run-environment values as
  consequences; don't tune to them.

## Known issues / not changed

- `tests/test_realism_identity.py::test_resolve_contact_table_unchanged_when_park_neutral_and_power_neutral`
  fails on the **pre-existing baseline too** — a stale guard from the earlier
  physics-first batted-ball rework that retired the `HARD_CONTACT` categorical
  table. Untouched here (out of scope, unrelated to these changes). Worth a
  cleanup in a future pass.
- `pytest` is absent in the bare sandbox; identity tests were run by importing
  and calling the test functions directly.

## Where to build next

(All optional — the count→power ordering is at a deliberate stopping point.
These are levers, not deficiencies-vs-MLB.)

- **The whole curve is one readable dict.** `COUNT_POWER_PROFILE` (and the
  `CONTACT_COUNT_EV_SCALE`) are the only knobs for "how much power at each
  count." Want a bigger full-count thump, a quieter 0-0, hotter hitter's counts?
  Edit the dict and re-run the harness — no formula or engine surgery. Watch
  total HR volume (the profile re-buckets, but big swings on the high-BIP counts
  move the total) and that the deep counts don't get starved of XBH.
- **Count share vs count power are different levers.** A count's *share* of all
  homers = how often a ball is put in play there × its HR/BIP. The power profile
  controls the second; the first is the take/swing split in `PITCH_BASE`. If you
  ever want fewer homers concentrated on 0-0/0-2, that's a `PITCH_BASE` (work-
  the-count) change, not a power one — the power at those counts is already low.
- **Per-batter discipline.** The pitch table is league-wide; a real next step is
  making the take/swing split read batter `eye`/`discipline`, so patient hitters
  work counts and hackers don't — turning this from a league constant into a
  player skill.

## Reproduce

```
# Audit / final distribution (HR + BIP by count vs MLB):
python3 scripts/hr_by_count.py --games 2500 --seed 1000

# Tuning comparison (ORIG vs foul-bug vs shipped; with run env + foul-out rate):
python3 scripts/hr_count_tradeoff.py 1500
```

Real-MLB reference values are embedded in both scripts (`REAL`).
