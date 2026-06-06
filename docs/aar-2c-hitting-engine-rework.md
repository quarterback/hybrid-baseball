# After-Action Report — 2C resolves through the hitting engine

**Date:** 2026-06-06
**Branch:** `claude/war-oaa-reconciliation-koalas-7t0pG`
**Commits:** `a724e80` (spec) → `4770f1a` (Stage 1 mechanic) → `a26ac85` (EV
tuning) → `9eaf3a9` (Stage 2 wOBA). Spec: `design-2c-hitting-engine-rework.md`.
**Status:** Stage 1 + Stage 2 shipped & validated. Balance is tunable via
`config.py` `STAY_*`; see follow-ups.

> ⚠️ This reverses prior documented design. The README and
> `aar-2c-reframe-and-shifts.md` framed 2C as *"a runner-advancement tool, not a
> hit-creation tool,"* *"multiple stays per AB, no cap,"* batter *"stays at the
> plate,"* and *"caught fly is the only out."* The owner evolved all of that.

---

## 1. Why this happened

It started as a wOBA seam (the league baseline still used MLB weights). Fixing
that surfaced the `stay_hits ⊆ singles` assumption, which the owner flagged as
flat-out wrong: **"a 2C hit can be anything — why wouldn't it be?"** Pulling that
thread exposed that the 2C mechanic was structurally broken, and the owner chose
to fix it properly with heavy documentation of the learnings.

## 2. Root-cause findings (the learnings)

The hitting engine *already* resolved a full `hit_type` (single/double/triple/HR,
park-adjusted, ITPHR) for every 2C **before** the stay decision. The brokenness
was entirely downstream:

1. **`pa.py:902` forced `batter_safe = True` on every valid stay** — negating the
   real outcome. A ground out became "safe"; a double was still credited a single.
2. **`prob.py` overrode `runner_advances` with a flat talent-gated `[adv,adv,adv]`**
   (0–3 uniform), discarding the real hit type's advancement.
3. **`render.py` credited `s.hits += 1` (a single)** regardless of type; the
   replay treated `caught_fly` as the *only* batter-out.
4. **No max-3 cap.** `stay.py` literally said *"no cap"* and the live path left the
   count **unchanged** on a successful stay — a hot batter could stay forever.

Plus an inconsistency that only surfaced here: the wOBA **derivation** bucketed a
2C as a separate `STAY` type, while the **counting** stats credited it a single —
two different stories about the same event.

## 3. The mechanic now (owner spec, captured across the session)

- **A 2C AB = at most 3 batted balls.** Each stay burns a strike; stay is
  available only at `strikes < 2`, so the 3rd batted ball forces run-or-out.
- **Resolves through the real hitting engine.** The resolved `hit_type` drives
  everything:
  - **Hit, non-HR** → batter **stays**; runners advance **by the real type**
    (single→1, double→2, triple→3); credited the real type.
  - **Fielding out** (caught fly / ground / fly / line) → batter is **OUT**, AB
    ends; runners advance per the play (a fly with a runner on third = sac fly).
    *This is the pesäpallo conflict, finally resolved.*
  - **HR** → batter **runs** (Walk-Back); never stays.
- **EV-driven, exclusively** (owner: *"2C should be EV-driven exclusively"*). No
  `stay_aggressiveness` frequency knobs. The batter stays only when advancing/
  scoring runners beats the out risk — and:
  - *High risk, must pay off, don't waste a good hit* (owner): the value of
    **running** (the hit you'd forgo) scales with contact quality, so the batter
    only gambles when the current hit is weak and the RISP payoff is large.
  - *2C is a RISP tool* (owner): with no runners in scoring position, you just
    take your hit.
  - *Skill-differentiated* (owner): out-risk falls with `contact+eye−command`,
    and the advancement is the real hit type (already skill-correlated via
    contact quality), so good hitters get more out of 2C; **jokers leverage it
    most**.

## 4. What changed

**Stage 1 — engine + renderer** (`4770f1a`, tuned `a26ac85`)
- `stay.py`: `stay_available` caps at `strikes < 2`; `stay_results_in_out` retires
  the batter on any fielding out; docstring rewritten.
- `prob.py`: deleted the `[adv,adv,adv]` override (real `runner_advances` flow);
  defense-read retained (decoupled); `should_stay_prob` rewritten EV-driven
  (quality-dependent forgone-hit value; never burn a half-ending out on a
  non-scoring stay).
- `pa.py`: stay branch routes outs → batter-out path; hits → stay + advance by
  real type; every stay burns a strike (AB continues; cap does the rest).
- `render.py`: replay mirrors the new out rule; 2C hits credit the real type.
- `config.py`: new `STAY_*` EV constants.

**Stage 2 — wOBA reconciliation** (`9eaf3a9`)
- `linear_weights.py`: `_classify_bip` buckets a credited 2C by `hit_type` (no
  STAY bucket); STAY removed from the weight construction and league-wOBA build.
- `app.py`: dropped the `singles − stay_hits` split + STAY weight in both the
  per-player aggregator and the league baseline; 2C hits weighted by real type.

## 5. Validation

- **Run environment preserved.** Standalone **8.9 R/half** (5 seeds); DB sims
  **11.4 R/half** — both near O27's ~10 baseline. (Before the EV rewrite, a
  naïve port cratered offense to 0–1 R/half — outs are now a real 2C risk; the
  EV decision is the balance fix.)
- **2C behaves as specified:** **1.12 stays/game** (rare — high bar), **100%
  with RISP**, **87% with multiple runners in scoring position** (must-pay-off).
- **2C now produces extra-base hits and retires batters on outs** (the two core
  bugs).
- **Deterministic** — seed replay byte-identical.
- **Identities/tests:** `PA == AB+BB+HBP+SH` holds; `o27/tests` 106 pass;
  `o27v2/tests` 130 pass; wRC+ centers ~101, league wOBA≈OBP, woba_weights has no
  STAY and keeps RV(1B)>RV(BB). Stat invariants 11/12 — the one miss is the
  wERA *outs-weighted reconstruction* on a 40-game DB (league wERA==ERA is
  exact); rechecked on a larger sim.
- **Skill differentiation:** on a 200-game sim, 2C attempt-rate correlates
  **+0.19** with hitting skill (contact+eye) — better hitters use it more, as
  intended. The per-player *success-rate* delta isn't cleanly measurable at the
  current 2C rarity (~1/game → too few stays per player); sharpening that delta
  (and the joker lean) is a tuning follow-up (§7).

## 6. Design decisions I had to infer (flagged for the owner)

- **"Max 3 batted balls" ⇒ strike-gated** (each stay burns a strike; stay at
  `strikes < 2`). Reuses the count rather than a separate counter.
- **Any fielding out on a stay retires the batter** (not just caught flies) —
  the literal reading of *"a 2C out → batter out."*
- **EV is a heuristic, not a true RE calc** — the engine has no run-expectancy
  matrix at sim time, so `should_stay_prob` uses an EV-flavored reward/out-risk
  model tunable via `config.py`.

## 7. Follow-ups / not done

- **Balance is first-pass and tunable.** `STAY_REWARD_*`, `STAY_OUT_RISK_*`,
  `STAY_RUN_BASELINE_*` are single-knob levers for 2C frequency, the RISP focus,
  the skill delta, and XBH rate.
- **2C wOBA credit — DONE (owner directive).** A 2C advancement is credited like
  the hit a runner-side batter would produce (the runner outcome is the same), so
  2C events are now **excluded from the run-value fit** (`_classify_bip` → None) —
  the 1B/2B/3B weights are clean normal-hit values and 2C hits are credited at
  them. wRC+ centers at exactly 100.
- **2C cost side — TODO.** The other half of the directive: an out that ends a 2C
  AB (a strikeout / out on a later segment, after the batter advanced runners) is
  *"the same as a runner being put out"* — a negative, not a free hit. Today it's
  a plain out (0 in PA-denominated wOBA), so a 2C AB that ends in an out is
  over-credited (it keeps the hit credit, ignores the out). Fix needs a stat for
  2C-AB-ending outs plus a runner-out run value subtracted in the wOBA numerator.
  Scoped next.
- **Defense-read** was retained but decoupled from the old advance gate; its
  balance should be re-checked.
- **2C skill delta is modest (+0.19) and under-powered.** The owner wants a
  *clear* poor/avg/successful/elite delta correlating with hitting, with jokers
  leveraging it most. Levers: raise 2C frequency (lower `STAY_RUN_BASELINE_*` /
  `STAY_REWARD_*`), widen the skill term (`STAY_OUT_RISK_SKILL_SCALE`), and give
  jokers a dedicated lean. Worth a dedicated tuning pass with more sim data.
- **wERA outs-weighted invariant is borderline:** on a 200-game new-engine sim,
  league wERA 10.47 vs raw ERA 10.53 — diff **0.062** vs the 0.05 tolerance. The
  league anchoring is close; the fixed wERA arc weights (0.85/1.00/1.20) are
  slightly loose under the 2C-shifted arc distribution. A small wERA-fit retune
  (separate subsystem), not a 2C correctness bug.
