# Build Plan — Unified per-half "locked in" form (RISP follow-up)

**Status:** ✅ IMPLEMENTED (see `aar-hits-runs-variance.md` Follow-up 4 for
results). The shipped values differ from the plan's seed estimates after tuning
(σ 0.66, moderate gains, + a `LOCKED_FORM_MEAN_BASE` center knob that the plan
didn't anticipate but the convex stacking required).
**Branch:** `claude/baseball-hits-runs-variance-Cv0iW`
**Closes the open item from:** `aar-hits-runs-variance.md` (Follow-ups 2 & 3)

---

## The problem this solves

There are currently **two independent per-half random draws** in
`o27/engine/prob.py`. Both use the *identical* mechanism — one Gaussian draw,
clamped, lazily cached on `state` and re-rolled when the `(inning, half)` label
changes (no explicit half-start hook; first contact-resolution call in the half
triggers the draw):

| draw | fn (line) | cache keys | anchor | channels it drives |
|---|---|---|---|---|
| sequencing form | `_batting_seq_form` (1295) | `state._seq_form` / `._seq_form_half` | flat mean 1.0 | single↔XBH↔HR redistribution, baserunner score-roll shift, GIDP rate |
| RISP clutch form | `_risp_clutch_form` (1257) | `state._risp_clutch` / `._risp_clutch_half` | **best hitter** (max over lineup of `0.5·power + 0.5·skill`) + manager `mgr_risp_pressure` vibes | RISP talent penalty, RISP XBH suppression |

Read sites today (5 total): seq form at `prob.py:1380` (baserunning shift),
`:1767` (slugging redistribution), `:2864` (GIDP); clutch form at `:1321`
(talent penalty), `:1787` (XBH suppression relief).

The AAR's repeated finding: the RISP clutch form **signal works** (good rosters
run hot ~50% of halves, bad ones ~37% cold) but its **transmission is too
narrow** — it only modulates the RISP penalty, a thin slice of total run
production, so even a big swing in it barely moves game-level R/H or the
efficiency tails. Meanwhile the sequencing form already drives the channels
that *do* move runs hardest (slugging, baserunning, GIDP), but rolls
**independently**, so on any given night a hot-converting half and a hot-slugging
half don't line up — they average out across the game instead of compounding.

**Every prior RISP pass lowered R/H but never widened the game-to-game
efficiency tails** ("blow-it-open vs leave-'em-loaded"). That is the unmet ask.

## The idea

Collapse both draws into **one latent per-half factor** — "this lineup is
locked in tonight" — and feed it into **all** channels at once, correlated
across the half's ~45 PAs. On a hot night the *same* draw simultaneously:

- squares up with RISP (relieves the talent wobble + XBH suppression),
- slugs for more XBH/HR (sequencing redistribution),
- runs the bases more aggressively (score-roll shift),
- hits into fewer rally-killing double plays (GIDP multiplier).

Because one draw now moves conversion **and** slugging **and** baserunning
**together within a single game**, the effects **compound instead of
cancelling** — which is the mechanism that finally widens the efficiency tails.

## Design

### 1. One draw, best-hitter anchored

Rename/repurpose `_risp_clutch_form` → `_locked_in_form(rng, state)` (it already
has the best-hitter + vibes anchor from Follow-up 3, exactly what the user
asked for) and **retire `_batting_seq_form`** as a separate draw — both become
one cached value:

```
mean = 1.0 + LOCKED_MEAN_SCALE * [ LOCKED_BAT_W*(best_bat - 0.5)
                                   + LOCKED_MGR_W*(mgr_risp_pressure - 0.5) ]
form = clamp(rng.gauss(mean, LOCKED_SIGMA), LOCKED_MIN, LOCKED_MAX)   # cached on state per (inning,half)
```

with `LOCKED_BAT_W = 0.85`, `LOCKED_MGR_W = 0.15` (carried from the re-anchor).
Keep the existing lazy `state`-cache pattern (`state._locked_form` /
`._locked_form_half`) — it's what guarantees one draw per half and seed
reproducibility. **RNG-stream note:** collapsing two `rng.gauss` calls into one
shifts every downstream draw, so existing seeded outputs will change. That's
expected (it's a behavior change), but it means "split vs unified" can't be a
pure same-seed diff — compare them distributionally over many games, not
game-for-game.

### 2. Per-channel transmission gains

The single `form` feeds each channel through its **own gain constant**, so each
effect can be tuned independently without de-correlating them. Each gain at 0 =
that channel ignores the form (identity); this preserves the existing
disable-ability and lets the A/B isolate channels.

```
# RISP conversion  (was driven by risp_clutch)
risp_penalty *= (1.0 - (form - 1.0) * LOCKED_RISP_GAIN)      # hot → smaller penalty
xbh_suppress *= (1.0 - (form - 1.0) * LOCKED_XBH_GAIN)       # hot → less suppression

# Slugging redistribution (was seq_form)
redist_strength = base * (1.0 + (form - 1.0) * LOCKED_SLG_GAIN)

# Baserunning score-roll (was seq_form)
score_roll += (form - 1.0) * LOCKED_SCORE_GAIN

# GIDP (was seq_form)
gidp_prob *= 1.0 + (form - 1.0) * LOCKED_GIDP_GAIN           # hot → fewer DPs (gain<0)
```

### 3. Config — one block, replaces the two

In `o27/config.py`, add a `LOCKED_*` block. The two existing draws have
**different σ and clamps** today — seq form σ=0.62, clamp [0.08, 2.10]; clutch
form σ=0.45, clamp [0.12, 1.95] — so the unified σ is a genuine tuning choice,
not a mechanical port. Start `LOCKED_SIGMA ≈ 0.55`, clamp `[0.10, 2.00]`.

**Per-channel gains seeded from today's effective strengths** so the *first*
run (gains at their ported values) is close to today's behavior, isolating the
correlation effect before amplitude tuning:

| new gain | ported from | current value |
|---|---|---|
| `LOCKED_SLG_GAIN` | `SEQ_FORM_POWER_SCALE` | 1.30 |
| `LOCKED_SCORE_GAIN` | `SEQ_FORM_SCORE_SCALE` | 1.00 |
| `LOCKED_GIDP_GAIN` | `SEQ_FORM_GIDP_SCALE` | 1.10 |
| `LOCKED_RISP_GAIN` | `RISP_CLUTCH_PENALTY_RELIEF` | 0.85 |
| `LOCKED_XBH_GAIN` | `RISP_CLUTCH_XBH_RELIEF` | 0.90 |

Keep the old `SEQ_FORM_*` / `RISP_CLUTCH_*` constants defined (as the source of
those ported values / for any external importers) but route all 5 prob.py read
sites through the `LOCKED_*` gains. The tuning pass then raises the gains
**together** to widen the tails.

### 4. Wiring

There is no `simulate_half_inning` to edit — the draws are lazy. Point all 5
existing read sites (`prob.py:1321, 1380, 1767, 1787, 2864`) at
`_locked_in_form(rng, state)` instead of the two old functions, each applying
its own `LOCKED_*_GAIN`. `_batting_seq_form` and `_risp_clutch_form` can become
thin shims returning `_locked_in_form(...)` (so nothing dangles) or be deleted
once all call sites are migrated. No new state fields beyond the one cache pair,
no signature changes.

## Verification protocol (this is the part that matters)

This is a tuning change; it lives or dies on measurement. Use the committed
`scripts/measure_hr_coupling.py` harness, **identical seeds**, in-process A/B:

1. **Tail metric is the headline.** Report R/H per-game **p10 / p90**, the
   `"few hits→many runs"` and `"many hits→few runs"` shares, and run-std. These
   are what every prior pass failed to move — success = p90−p10 spread widens
   and both tail shares grow.
2. **Three A/Bs, identical seeds each:**
   - **off vs on** (all `LOCKED_*_GAIN = 0` vs tuned) — proves the unified draw
     widens tails where the split draws didn't.
   - **split vs unified** (old two-draw code vs new) at matched total strength —
     isolates the *correlation* benefit from the raw-amplitude benefit.
   - **good vs bad club** (best bat ±, manager ±) — confirms the streak stays
     performance-grounded (good clubs blow games open more often).
3. **Guardrails (full-sim sanity, must stay in band):** BA ≈ .40–.48,
   R/G ≈ 33–38, K% ~13%, BB% ~10%, **super-inning < 10%**. The mean R/H should
   stay ≈ 0.93 (don't undo "a hit ≠ a run"); we're widening the *spread*, not
   shifting the center.
4. **Tests:** the o27 + o27v2 engine suites must stay green; the
   `test_risp_pressure.py` directional invariants especially.

## Risks / watch-items

- **Tail-widening vs super-inning blowout cap.** Pushing the gains up will
  create more lopsided halves; watch the super-inning rate doesn't breach 10%.
- **Mean drift.** Asymmetric clamps or a skewed gain can shift mean R/H off
  0.93; check the center every A/B, not just the tails.
- **Double-counting.** The old `SEQ_FORM` mean-nudge used lineup *average*; the
  unified form uses best *hitter*. That's intended (Follow-up 3), but it changes
  the sequencing channel's anchor slightly — note it in the AAR.
- **One knob too coarse?** If a single σ can't satisfy both "wide tails" and
  "sane mean," the per-channel gains are the release valve — keep them.

## Why this should work where the others didn't

The diagnosis was never "the lever is too small" — it was "the lever is too
*narrow* and *uncorrelated*." Raising any single channel just makes that one
channel noisier. Correlating all of them off one latent draw is what makes a
hot night *compound* into a blow-out and a cold night *compound* into
leave-'em-loaded — i.e. it converts the already-working team-level streak
signal into visible **game-to-game** variance.

---

### Implementation note (environment)

This container's shell I/O degraded mid-session (output buffer pollution +
one file-truncation incident on `o27v2/web/app.py`, since restored). Because
this change *requires* iterative A/B sim runs to tune the gains, it should be
implemented in a clean session where `python o27v2/manage.py` /
`scripts/measure_hr_coupling.py` run reliably. The plan above is
implementation-ready; the work is (a) the refactor, then (b) the gain tuning
against the tail metrics.
