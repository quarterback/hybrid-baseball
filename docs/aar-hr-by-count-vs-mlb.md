# After-Action Report — Home-runs-by-count: O27 vs real MLB (audit → tuning)

**Date completed:** 2026-06-16
**Branch:** `claude/new-session-w4nwix`
**Scope:** realism audit of the HR-by-count distribution, then a three-part fix
(persist the count, earned-power model, smart-batter pitch table) including a
mid-flight O27 rules bug and its correction.

---

## TL;DR

A chart of real MLB home-runs-by-count (Retrosheet, 1910–2025) showed O27 was
hitting **~35% of all home runs on the first pitch (0-0)** vs MLB's 18.3% — and
that a home run in O27 was **count-agnostic** (≈4% of every ball-in-play became
a HR at *every* count). We:

1. **Logged the count** — `game_pa_log` now stores `balls`/`strikes`, so
   outcome-by-count is a live-DB query, not just a headless harness trick.
2. **Made power earned** — contact ahead in the count carries (EV shift by
   `balls − strikes`); HR/BIP now rises in hitters' counts instead of being flat.
3. **Made batters smarter** — rewrote `PITCH_BASE` so they take far more
   early-count pitches (work the count, ~3.6 pitches/PA), which collapses the
   cheap first-pitch HR.

A fourth change followed (Part 3d): a **behind-in-the-count contact penalty**
that degrades the *quality* of two-strike-behind contact (the 0-2 fix), so a
buried hitter's barrel becomes a mishit.

Net: **0-0 HR share 34% → 23%**, total HR-by-count error (½·Σ|Δ| vs MLB)
**from 50.2 → 21.1** (sum of |Δ|), HR volume preserved, identity guards green.

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

### 3d. The 0-2 fix — a behind-in-the-count contact penalty

After the smart-batter table shipped, 0-2 was still HR-rich (+4.2 pts) — but the
*tell* was that its over-share was **volume**, not effectiveness: 0-2 became
~12% of all balls in play (batters take so many early strikes), so even a low
HR/BIP carries share. The owner's call was to degrade the *contact itself* when
behind: "make whatever contact 13–29% less effective."

`contact_quality` already had the exact mechanism — the weather
`hard_contact_multiplier` block, which cuts the hard-contact probability and
pushes the lost mass to weak. So the fix reuses that shape with a count-driven
multiplier (`o27/config.py`):

```
penalty   = min(CONTACT_BEHIND_HARD_PENALTY_CAP,        # 0.29
                CONTACT_BEHIND_HARD_PENALTY * max(0, strikes − balls))  # 0.145/unit
hard_mult = 1 − penalty
```

So 0-2 (`strikes − balls = 2`) takes the full ~29% hard-contact cut, 1-2 and 0-1
~14%, and **even/ahead counts take none** — which deliberately spares the deep
2-2 / 3-2 counts that are already *under* MLB. Zero at 0-0, so the
contact-quality identity contract holds. Threaded via one new
`count_hard_mult` kwarg to `contact_quality`, computed at the call site
(`prob.py` ~2900) where the count is in scope.

Effect (2,500-game harness): 0-2 HR/BIP **2.66% → 2.20%**, 0-2 over-share
**+4.2 → +3.2**; 0-1 lands exactly on MLB (+0.0) and 1-2 on it (+0.2); deep
counts unchanged. Total error 24.5 → **21.1**. Run environment essentially
unmoved (the penalty only re-buckets contact quality at behind counts).

---

## Final shipped distribution

Earned power + corrected smart `PITCH_BASE` + behind-count contact penalty
(2,500 games, seed 1000 → 4,270 HRs / 108,470 BIP):

| Count | O27 HR% | MLB HR% | diff | O27 BIP% | HR/BIP |
|-------|--------:|--------:|-----:|---------:|-------:|
| 0-0 | 22.8 | 18.3 | +4.5 | 21.2 | 4.23% |
| 1-0 | 12.6 | 12.0 | +0.6 | 9.6 | 5.19% |
| 2-0 | 6.1 | 5.6 | +0.5 | 4.1 | 5.87% |
| 3-0 | 2.1 | 0.6 | +1.5 | 1.2 | 6.99% |
| 0-1 | 9.7 | 9.7 | +0.0 | 12.3 | 3.10% |
| 1-1 | 9.6 | 11.1 | −1.5 | 9.0 | 4.17% |
| 2-1 | 6.5 | 8.3 | −1.8 | 5.1 | 5.00% |
| 3-1 | 3.4 | 4.9 | −1.5 | 2.3 | 5.70% |
| 0-2 | 6.7 | 3.5 | +3.2 | 11.9 | 2.20% |
| 1-2 | 7.8 | 7.6 | +0.2 | 10.9 | 2.82% |
| 2-2 | 7.6 | 9.1 | −1.5 | 7.4 | 4.04% |
| 3-2 | 5.0 | 9.2 | −4.2 | 4.8 | 4.14% |

Total variation: sum of |Δ| = **21.1** (down from 50.2 at the start).

The HR/BIP column is now a clean monotone-by-count signal: highest ahead in the
count (3-0 6.99%, 2-0 5.87%, 3-1 5.70%) and lowest two-strike-behind (0-2
2.20%, 1-2 2.82%). That is the earned-power EV shift **and** the behind-count
contact penalty working together — power is now a function of the count, as in
real baseball. The remaining over-shares (0-0, 0-2) are now **volume**, not
effectiveness: every PA flows through 0-0, and the work-the-count table funnels
a lot of PAs through 0-2 (11.9% of all BIP), so even at the lowest HR/BIP those
buckets carry share. Pushing them lower means fewer PAs reaching them, not
weaker contact.

---

## Code map

| File | Change |
|---|---|
| `o27v2/db.py` | `balls`/`strikes` columns on `game_pa_log` + migration |
| `o27/render/render.py` | stamp count on each `ball_in_play` `_pa_log` row |
| `o27v2/sim.py` | insert the two new columns |
| `o27/engine/prob.py` | count-aware `ev_shift` in `resolve_contact`; `count_hard_mult` in `contact_quality` + call-site computation |
| `o27/config.py` | `CONTACT_COUNT_EV_SCALE` / `_CLAMP`; `CONTACT_BEHIND_HARD_PENALTY` / `_CAP`; rewritten `PITCH_BASE` |
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

- **Deep counts still trail MLB** (2-2/3-2 ≈ 12% vs 18%) — because O27 batters
  remain more aggressive, fewer PAs reach 3-2. Closing it means dialing the take
  harder still (a `STRONG` variant in `hr_count_tradeoff.py` gets 0-0 to ~21%
  but at ~23% K). That's a deliberate aggression-vs-realism choice for the owner.
- **0-2 / 0-0 over-shares are now volume, not effectiveness.** HR/BIP at those
  counts is already the lowest on the grid; further reduction has to come from
  fewer PAs reaching them, i.e. dialing the take/swing split, not the contact
  model. The behind-count penalty (`CONTACT_BEHIND_HARD_PENALTY`) can be pushed
  past 0.29 if more bite is wanted, but watch that it doesn't starve XBH there.
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
