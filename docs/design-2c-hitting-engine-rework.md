# Design + implementation spec — 2C resolves through the hitting engine

**Date:** 2026-06-06
**Branch:** `claude/war-oaa-reconciliation-koalas-7t0pG`
**Status:** SPEC (implementation in progress). This doc is the heavy
documentation of the mechanic, the drift that produced the bugs, the design
decisions (incl. the ones I had to infer), the file-by-file change list, and the
validation plan. It becomes the AAR once results are in.

> ⚠️ This reverses documented design. The README and
> `aar-2c-reframe-and-shifts.md` framed 2C as *"a runner-advancement tool, not a
> hit-creation tool"* with *"multiple stays per at-bat, no cap"* and the batter
> *"stays at the plate."* The owner has decided to evolve that — see below.

---

## 1. What's broken (root-cause findings)

The hitting engine already resolves a full `hit_type` (single/double/triple/HR,
park-adjusted, ITPHR) for **every** 2C *before* the stay decision
(`prob.py:resolve_contact` → `outcome_dict["hit_type"]`). The brokenness is
entirely downstream:

1. **`pa.py:902`** sets `modified_outcome["batter_safe"] = True` on *every* valid
   stay — negating the real outcome. A ground out becomes "safe"; a double is
   still credited as a single.
2. **`prob.py:3006-3044`** overrides `runner_advances` with a flat talent-gated
   `[adv,adv,adv]` (0-3, uniform), discarding the real hit type's advancement.
3. **`render.py:1740`** credits `s.hits += 1` (a single) regardless of hit type;
   the renderer's replay (`render.py:1248-1267`) treats `caught_fly` as the only
   batter-out.
4. **No max-3 cap.** `stay.py:15` says *"no cap"* and the live path leaves the
   count **unchanged** on a *successful* stay (`pa.py:950-951`) — a hot batter
   can stay indefinitely.

## 2. The intended mechanic (owner spec)

- **A 2C AB = at most 3 batted balls.** Each batted ball the batter stays on
  **burns a strike**. Stay is available only at **strikes < 2**, so the **3rd
  batted ball (at 2 strikes) forces run-or-out** — no stay.
- **Each 2C resolves through the real hitting engine.** The already-resolved
  `hit_type` drives everything:
  - **Hit, non-HR** → batter **stays** (if cap allows); runners advance **by the
    real hit type** (single→1, double→2, triple→3); credited the **real type**.
  - **Fielding out** (ground/fly/line/caught) → batter is **OUT**, AB ends;
    runners advance per the fielding play, so a fly with a runner on third is a
    **sac fly**. *(Resolves the pesäpallo conflict: O27 kept baseball's out
    structure, so a caught 2C must retire the batter.)*
  - **HR** → batter **always runs** (Walk-Back to 3B); never stays. *(No
    incentive to stay when runners score and you reach third anyway.)*
- **Decision is EV-driven, exclusively.** Stay only when expected run value of
  advancing runners beats the out risk, scaled by runners / outs / score /
  batter skill. **Never burn the last out** on a non-scoring stay; sac flies are
  the manufacture-runs option when offense stalls.

## 3. Design decisions I had to infer (please sanity-check)

- **"Max 3 batted balls" ⇒ strike-gated.** Each stay burns a strike; stay
  available at `strikes < 2`. A fresh batter can stay on balls 1 and 2 (→1, →2
  strikes); ball 3 (at 2 strikes) forces run-or-out. If the batter already had 2
  strikes from pitches, ball 1 is forced — he's in a 2-strike hole. (This reuses
  the count system rather than a separate batted-ball counter.)
- **Out-risk is what makes "stay" a real choice.** The decision is made on the
  *situation + contact quality + skill*, not the exact resolved hit type, so a
  stay can land on an out (→ batter out). That's the risk; a skilled hitter
  (contact/eye vs command) has a lower out rate, so staying is more often
  EV-positive for them.
- **EV is a heuristic, not a true RE calc.** The engine has no run-expectancy
  matrix at sim time (RE lives in the analytics layer over `game_pa_log`). So
  `should_stay_prob` uses an EV-flavored heuristic: run-driving opportunity
  (runner on 3B/<2 outs, RISP), minus out risk, gated to never waste the last
  out on a non-scoring stay. Tunable via new `cfg` constants.
- **A "stay" on a double forgoes the batter's base.** Per the owner's choice
  (batter stays, runners advance by type). EV will usually *run* a clean
  double/triple (taking the base is better), so big-XBH 2C stays are rare — but
  the advancement now reflects the real contact instead of a flat talent gate.

## 4. File-by-file change list

**Engine**
- `o27/engine/stay.py`
  - `stay_available`: `runners_on_base AND state.count.strikes < 2`.
  - `stay_results_in_out(state, outcome)`: True when the resolved `hit_type` is
    an out (`ground_out/fly_out/line_out`, `batter_safe` False) OR `caught_fly`.
  - `should_stay` (deterministic, used in tests): out/HR/cap rules + EV-ish.
  - Docstring: replace the "no cap" / "caught-fly-only-out" rules.
- `o27/engine/prob.py`
  - Delete the `[adv,adv,adv]` override (3006-3044); let `hit_type`'s
    `runner_advances` (from `runner_advances_for_hit`, already on the dict) flow.
  - Rewrite `should_stay_prob` → EV-driven heuristic (new `cfg` constants).
- `o27/engine/pa.py`
  - Stay branch (867-958): out detection via `stay_results_in_out(state,
    outcome)`; on a hit, advance by the real `runner_advances`, credit the real
    type, **burn a strike every stay**, batter stays; HR can't reach (forced run
    upstream).
- `o27/engine/config.py`: new `STAY_EV_*` constants; retire/repurpose the old
  `STAY_RISP_MULT` / `STAY_AHEAD_IN_COUNT_MULT` / `STAY_LATE_GAME_MULT` family.

**Renderer (replay reconstruction — must mirror the engine)**
- `o27/render/render.py`
  - `1248-1267`: `stay_batter_out` for any out type (not just caught fly);
    recompute `stay_hit_credited`.
  - `1730-1799`: credit the **real hit type** on a stay (mirror the run-path
    `s.doubles/s.triples` crediting at `1841-1843`); burn-a-strike accounting;
    `stay_batter_out` path for all out types (`outs_recorded`).

**Stats (Stage 2 — wOBA reconciliation)**
- `o27v2/web/app.py`: `stay_hits` is no longer ⊆ singles, so drop the
  `true_singles = singles − stay_hits` split + `STAY` weight in **both** the
  per-player aggregator (`~1548-1554`) and the league baseline (`~2121`). 2C hits
  now sit in their real type buckets.
- `o27v2/analytics/linear_weights.py` / `expected_woba.py`: revisit the `STAY`
  linear-weight / `was_stay,stay_credited` handling now that 2C hits carry real
  types.

## 5. Validation plan

- **Run environment:** fresh `initdb` + `sim N` before/after; report R/G,
  hits/2B/3B by 2C, 2C-out rate, BAVG. Expect R/G to drop (outs are now a real
  risk) and 2C XBH to appear.
- **Determinism:** same seed → identical box (the new rng draws must be
  deterministic; count draws carefully).
- **Identities:** `PA == AB + BB + HBP`; the stat-invariant suite; `pytest
  o27/tests o27v2/tests`.
- **Renderer/engine agreement:** the replay-reconstructed stats must match the
  engine's bgs ledger (no double-count / drift between the two stay
  implementations).

## 6. Staging

1. **Stage 1 — engine + renderer mechanic** (§4 engine + renderer). One
   reviewable unit; validate run-env + determinism + suite.
2. **Stage 2 — stats/wOBA** (§4 stats). After the new stat semantics are stable.
