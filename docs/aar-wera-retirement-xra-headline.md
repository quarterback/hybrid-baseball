# After-Action Report — retiring wERA, anchoring xRA to the run environment

**Date:** 2026-06-06
**Branch:** `claude/war-oaa-reconciliation-koalas-7t0pG`
**Commits:** `d8eb5aa` (data: kill wERA, xRA headline + anchoring fix + invariant)
→ `6072cdc` (UI: relabel wERA→xRA across templates).
**Scope:** `o27v2/web/app.py` (pitching constants, aggregator, baselines),
`tests/test_stat_invariants.py` (invariant 8), 13 templates + `test_template_renders`.

---

## 1. Why wERA had to go (the fallacy)

wERA weighted earned runs by *arc*: outs 1–9 ×0.85, 10–18 ×1.00, 19–27 ×1.20 —
asserting a run allowed late is worth more than the same run early. **That theory
has nothing to stand on in O27.** In a single continuous 27-out half, every run
counts identically toward the final score; the offense has the same total outs to
work with regardless of *when* a run was given up. There's no inning structure
making late runs more "back-breaking." The real leverage of a run is situational
— it depends on score and outs remaining — which is exactly what WPA / leverage
index already capture, *not* a fixed positional multiplier that declares late =
costlier for everyone, always.

The intuition wERA imported ("late runs hurt more") is, even in MLB, an artifact
of inning structure and dwindling outs — not the run itself being worth more.
O27 has no innings, so the intuition has nothing to stand on. So wERA wasn't
measuring run prevention; it was measuring **run prevention + a wrong theory
about timing.** Dropping it removes the wrong theory.

## 2. The replacement: xRA

**xRA** (expected Runs Allowed) — expected runs from the actual events the
pitcher gave up (HR / BB / contact via RE-style linear weights), no positional
theory layered on top, always non-negative. It measures the thing wERA was
pretending to measure, without the fallacy. It's the run-prevention companion to
the RE24-native run values used for BSR and 2C Runs.

## 3. What changed (data — `d8eb5aa`)

- **Dropped the arc-weighted ER / `C_w` computation.** Nothing computes the old
  value. The `werra` / `wera_plus` keys are retained as **aliases of `xra` /
  `xra+`** so leaderboards, dispersion, standings and templates keep working with
  zero field-plumbing churn (≈40 references).
- **Anchored xRA to the realized run environment — league RA per 27 outs (ALL
  runs allowed).** xRA estimates total runs allowed, so it centers on actual
  RA/27, not earned-runs.
- **Fixed a real anchoring bug found along the way.** `xra_norm` was computed
  from *blended* league hit-shares while per-pitcher xRA used the *actual*
  allowed singles/doubles/triples breakdown — a method mismatch that left league
  xRA ~0.5 off the run environment. Both sides now go through one shared
  `_xra_run_values` helper (actual breakdown when present, else blended), and
  `xra_norm` is fit by summing each pitcher's run-values through that same
  helper: `xra_norm = total_RA / Σ(run_values_p)`. Outs-weighted league xRA now
  reconciles to league RA/27 **exactly**.
- **pWAR rebased to xRA:** VORP = `(replacement_xRA − my_xRA) × outs/27`,
  replacement = league xRA × 1.20.
- **Invariant 8 UPDATED, not deleted** (per the owner's call — keep the check,
  point it at the surviving stat): it now asserts league xRA (outs-weighted)
  tracks league RA/27 within 0.05 — same spirit as the old wERA-vs-ER/27 check
  (the expected stat must center on the realized run environment), so a
  miscalibrated `xra_norm` trips immediately. Plus a guard that `werra` aliases
  `xra`. (Its query gained the breakdown columns so the aggregator computes xRA
  the same way production routes do.)

## 4. What changed (UI — `6072cdc`)

No template says "wERA" anymore: `wERA+` → `xRA+`, standalone `wERA` → `xRA`, and
the now-false arc-weighting tooltips rewritten to describe xRA. All pages render.

## 5. Validation

- **Invariant 8 passes exactly** (league xRA == league RA/27 to ~0; was failing
  at 0.062 for the old wERA, then 0.725 → 0.0607 → 0 as the anchoring fix
  landed).
- Full stat-invariant suite: 11 passed. `o27/tests`: 106. `o27v2/tests`: 128
  passed (the 1 fail is the pre-existing flaky GM-noise trade sampling test —
  fail/pass/fail on re-run, unrelated to pitcher metrics).
- All pages render 200.

## 6. Follow-ups

- **Template column de-dup (cosmetic).** Some templates now display the xRA value
  twice — the retained `werra`-fed column sits next to the `xra`-fed one. The
  labels are correct (both "xRA"); the redundant column should be removed per
  template (header/cell pair). Left as a scoped, low-risk follow-up rather than
  risk a blind 13-template structural pass.
- **Two pre-existing `tests/test_template_renders` failures** (`gmli` field;
  season-archive writer) fail identically *before* this work on an ad-hoc sim DB
  — data/fixture-dependent, not introduced here. Worth a look on a proper
  season-archive DB.
- **Optional descriptive arc splits.** If "when did the damage happen" is wanted
  back, early/mid/late ER can live as a *descriptive* breakdown with **no
  weighting** — a fact about a pitcher, not a value judgment. Decay already
  carries the real fade signal. Not needed for the core move.
