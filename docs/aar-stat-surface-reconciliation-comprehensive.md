# Report — stat-surface reconciliation: WAR, OAA/Field Runs, BSR, xwOBA luck

**Date:** 2026-06-06
**Branch:** `claude/war-oaa-reconciliation-koalas-7t0pG`
**Companion to:** `docs/aar-war-oaa-reconciliation-koalas.md` (the tight finding
record). This document is the comprehensive technical deep-dive — every code
trace, the full arithmetic, the OAA attribution analysis, the luck-gap
verification, the proposed invariant, and the mitigation roadmap. Read-only
audit; no engine/rating/stat changes are described here as *done*, only
*recommended* (the "Mitigation roadmap" section).

The trigger was a single player (a Memphis Koalas CF, WAR 3.65 / OAA −25.5), but
the findings are league-wide and structural — they apply to every player on both
surfaces.

---

## 0. The two surfaces, side by side

| Surface | Defense input | Baserunning | Replacement | File |
| --- | --- | --- | --- | --- |
| **Season card / WAR** | scout-grade DRS (`pos_def`) | **none** | baked into VORP (0.85·lg_wOBA) | `o27v2/web/app.py:1626-1648` |
| **O27 Index (Savant)** | OAA / Field Runs (event log) | BSR (separate) | — | `o27v2/analytics/expanded.py`, `o27v2/web/templates/o27i_player.html` |

The defensive inputs come from **two entirely independent pipelines that never
meet**. That is the whole bug. Everything below is the detail behind it.

---

## 1. WAR's defensive input is scout-grade DRS, not OAA

### 1.1 The WAR formula (`o27v2/web/app.py:1626-1648`)

```python
# bVORP — value over replacement, in runs.
repl_woba = baselines.get("replacement_woba") or 0          # = 0.85 × league_wOBA
woba_scale = 1.20
b["vorp"] = ((b["woba"] - repl_woba) * pa / woba_scale) if (pa and league_woba) else 0.0

# --- Defensive value ---
rpw       = baselines.get("runs_per_win") or 10.0
pos       = str(b.get("position") or "")
games     = b.get("g") or 0
pos_def   = _position_defense_for_row(b)                     # ← RATINGS, not events
drs_range = _POSITION_DRS_RANGE.get(pos, 4.0)                # CF = 8.0
b["drs"]  = (pos_def - 0.5) * 2.0 * (games / 162.0) * drs_range if games else 0.0
b["dwar"] = b["drs"] / rpw if rpw else 0.0

bwar_off  = b["vorp"] / rpw if rpw else 0.0
b["war"]  = bwar_off + b["dwar"]
```

### 1.2 `pos_def` reads rating fields, never the event log (`app.py:697-725`)

```python
def _position_defense_for_row(row: dict) -> float:
    pos = str(row.get("position") or "")
    general = _norm(row.get("defense"))               # 20–95 scout grade → 0..1
    if pos == "C":              sub = _norm(row.get("defense_catcher"))
    elif pos in _INFIELD_POS_SET:  sub = _norm(row.get("defense_infield"))
    elif pos in _OUTFIELD_POS_SET: sub = _norm(row.get("defense_outfield"))
    else:                       sub = general
    return 0.6 * sub + 0.4 * general
```

No reference to `game_pa_log`, `build_fielding_value`, OAA, FRV, putouts, or
errors. The defensive term is a **rating prior**, scaled by games and position.
For a player with even an average-or-better CF grade it is a small **positive**,
by construction — it can no more see range than FldPct can.

### 1.3 `_POSITION_DRS_RANGE` (`app.py:678-690`)

```
C 15.0 · SS 12.0 · 2B 8.0 · CF 8.0 · 3B 7.0 · LF 5.0 · RF 5.0 · 1B 4.0 · DH 0.0 · UT 6.0 · P 2.0
```

This is the *only* place position enters WAR — there is no separate FanGraphs-style
positional run adjustment (+2.5 CF / −12.5 1B etc.). "Positional value" and
"fielding value" are fused into this one rating-scaled term.

### 1.4 What is NOT in WAR

- **OAA / Field Runs** — separate pipeline (§2), never read here.
- **Baserunning (BSR)** — surfaced on the Savant page (+6.0 for the Koala) but
  **no term** in the WAR sum. Confirmed: lines 1626-1648 contain no baserunning
  variable.
- **Standalone positional adjustment** — folded into `_POSITION_DRS_RANGE`.

So WAR = `VORP_bat/rpw + DRS_scout/rpw`. Two terms. Replacement is implicit in
`repl_woba = 0.85 × league_wOBA` (`app.py:~2093`).

### 1.5 Pitcher WAR, for completeness (`app.py:2745-2755`)

```python
repl_werra = (baselines.get("league_werra") or baselines.get("era") or 0.0) * 1.20
p["vorp"]  = (repl_werra - p["werra"]) * (outs / 27.0) if (outs and repl_werra) else 0.0
p["war"]   = p["vorp"] / rpw
```

`pWAR = (1.2·league_wERA − wERA) × (outs/27) / rpw`. Not implicated in this audit,
recorded for the component map.

---

## 2. OAA / Field Runs — the event pipeline (`o27v2/analytics/expanded.py:356-436`)

```python
def build_fielding_value(min_chances=25, team_ids=None):
    # League out-rate per (EV, LA) bin — over ALL phase-0 BIP, hits AND outs.
    rows = db.fetchall("""SELECT team_id, game_id, hit_type, exit_velocity,
                          launch_angle, spray_angle, fielder_id
                          FROM game_pa_log WHERE phase = 0 AND exit_velocity IS NOT NULL""" ...)
    ...
    out_rate = {k: binr_out[k] / binr_n[k] for k in binr_n}   # 5×4 (EV,LA) grid

    # Per (fielding_team, position) "regular" = most-used player at that position.
    ...
    for r in rows:
        is_out = r["hit_type"] in _OUT_TYPES
        if is_out and r["fielder_id"] is not None:
            fielder = r["fielder_id"]; n_exact += 1        # EXACT, engine-credited
        else:
            zone = _zone_from_trajectory(r["launch_angle"], r["spray_angle"])
            ...
            fielder = reg[0]; n_heur += 1                  # HEURISTIC, regular at zone
        exp    = out_rate.get((_ev_bin(...), _la_bin(...)), 0.0)
        actual = 1.0 if is_out else 0.0
        oaa[fielder]     += (actual - exp)                 # OAA accrual
        chances[fielder] += 1
    ...  # frv = oaa × _RUN_PER_OUT (= 0.78)
```

Constants: `_RUN_PER_OUT = 0.78` (line 30); EV edges `(80,90,100,110)`, LA edges
`(0,12,24,40)` (`expanded.py:105-124`); `_OUT_TYPES` includes `ground_out,
fly_out, line_out, fielders_choice, double_play, triple_play, itp_out`.
`Field Runs = OAA × 0.78`.

### 2.1 Three miscalibration vectors (why −25.5 is not a real range grade)

**(a) HRs and extra-base hits are counted as fielding chances.** The row query
pulls *every* phase-0 BIP with an exit velocity. A hit has `actual=0`, so it
accrues `−exp` to whoever owns its zone — **including home runs**. A center-field
HR docks the CF's OAA. (Per-event small for true barrels — their EV/LA bin has a
low league out-rate — but hard line-drive gappers sit in 0.4–0.6 out-rate bins
and each cost ~−0.5.)

**(b) Out-vs-hit attribution asymmetry — the dominant artifact.**

- **Outs** are credited by the engine's `_select_fielder`
  (`o27/engine/prob.py:1715+`), which is **spray-blind**: it samples a position
  purely by hit-type weight (`fly_out → CF 0.50 / LF 0.25 / RF 0.25`), regardless
  of where the ball actually went.
- **Hits** are charged by **actual** trajectory zone
  (`_zone_from_trajectory`, `expanded.py:339-353`) to the *single* position
  regular — so **100%** of central-zone air balls that drop land on the everyday
  CF.

The everyday regular at a high-traffic position therefore absorbs *all* of his
zone's negatives but only a *fixed share* of the positives. This is a systematic
downward bias on full-time CF and SS — exactly the positions where −25.5 shows up.

**(c) Only ~41% exact attribution.** Per
`docs/aar-expanded-metrics-and-prospect-index.md` (200-game seed), ~41% of chances
are exactly engine-attributed; the remaining ~59% — every hit, plus outs missing a
persisted `fielder_id` — run through the heuristic that concentrates on regulars.

### 2.2 Scale sanity

−25.5 OAA / 62 G = **−0.41 OAA/game**, ≈3× the worst full-season MLB rate
(~−0.13/game). Combined with a clean .989 FldPct (he makes the routine plays), an
extreme negative *range* grade is not substantiated at 41% exact attribution.

### 2.3 Confirming it on the live DB (no DB in this sandbox)

```python
from o27v2.analytics.expanded import build_fielding_value
lb   = build_fielding_value()["leaders"]
vals = sorted(x["oaa"] for x in lb)
print("min/median/max:", vals[0], vals[len(vals)//2], vals[-1])
print("worst 5:", [(x["player_name"], x["oaa"], x["chances"]) for x in lb[-5:]])
```

**Prediction:** the bottom of the list is dominated by full-time center fielders
and shortstops (the spray-concentrated positions) — the signature of the
attribution asymmetry, not real defense. If confirmed, OAA needs the §6 fixes
before it can be trusted on any surface, let alone fed into WAR.

---

## 3. Reconciling the Koalas' 3.65 WAR

Card inputs: wOBA 0.908 (season-card scope), PA 206, G 62, CF.

```
WAR = VORP_bat/rpw + DRS_scout/rpw

DRS_scout = (pos_def − 0.5) × 2 × (62/162) × 8.0 = (pos_def − 0.5) × 6.12 runs
   pos_def 0.6 → +0.61   0.7 → +1.22   0.8 → +1.84 runs
dWAR = DRS/rpw ≈ +0.04 … +0.12   (rpw ≈ 13–18 in O27's high-run env)
```

| Component | Value | Note |
| --- | ---: | --- |
| Batting VORP / rpw | **≈ +3.5** | `(0.908 − 0.85·lg_wOBA)·206/1.20 / rpw` |
| Defensive WAR (scout DRS) | **≈ +0.05 … +0.12** | small positive, rating-driven |
| Baserunning | **0** | BSR +6.0 surfaced, not in WAR |
| Positional adj. | **0** | folded into DRS range |
| **Total** | **3.65** | consistent with its *own* inputs |

**If the Savant Field Runs were used instead:** dWAR = −19.9/rpw ≈ **−1.3 to
−2.0**, new WAR ≈ **1.6–2.3**. The ~1.4–2.1-win delta is the cross-surface
discrepancy, quantified.

The owner's framing ("−19.9 fielding + 3.65 WAR ⇒ bat ≈ +5.5 WAR") assumed the
−19.9 was in the WAR. It is not; the bat is ~+3.5, not +5.5.

### 3.1 Exact split on the live DB

```python
from o27v2.web.app import _league_baselines_compute
b = _league_baselines_compute()
print("repl_woba=", b["replacement_woba"], "lg_woba=", b["league_woba"], "rpw=", b["runs_per_win"])
vorp     = (0.908 - b["replacement_woba"]) * 206 / 1.20
war_off  = vorp / b["runs_per_win"]
print("VORP=", vorp, "war_off=", war_off)
```

Also confirm the season-card wOBA (0.908) and the Savant wOBA (0.924) share a
denominator/scope before expecting any cross-surface tie-out — they are two
different aggregations of the same player.

---

## 4. The luck gap (wOBA .924 vs xwOBA .816, +0.108) — real signal

### 4.1 Independence (no tautology)

`o27v2/analytics/expected_woba.py`, `build_xwoba_ev_table` (lines 204-288):

```python
# Pass 1 — league xwOBA per (ev_bin, la_bin), over ALL players' BIP.
bin_xwoba = {k: bin_sum[k]/bin_cnt[k] for k in bin_cnt}
# Pass 2 — apply the LEAGUE-AVERAGE bin value to THIS player's EV/LA distribution.
expected_pts[pid] += bin_xwoba.get(key, 0.0) * r["n"]
actual_pts[pid]   += _bip_woba_points(weights, r["hit_type"], ...) * r["n"]
```

Two separate flows: xwOBA = league-average wOBA for the ball's (EV, LA) bin;
wOBA = the player's realized outcome. The expectation is a league aggregate, never
the ball's own result → **not circular**.

### 4.2 Physics-decided share

Post-inversion (`docs/aar-physics-first-inversion.md`, 2026-06-01) **100% of
batted balls are physics-decided** — `generate_batted_ball()` samples EV/LA from
talent with no knowledge of outcome, `resolve_batted_ball()` derives hit_type from
the trajectory. Event-level `corr(EV, hit) = 0.364` (causal). For this player:
**155/155 batted balls physics-decided, 0 sampled.** The +0.108 gap is genuine
over-performance vs contact quality — trust it.

### 4.3 The one caveat

Pre-inversion rows sampled EV *from* the outcome (echo-EV), which would make the
gap circular. Confirm this player's 62-game span is entirely ≥ 2026-06-01 before
trusting the luck number on older games. There are also **two** xwOBA
implementations in `expected_woba.py` — the legacy quality-bucket
`build_xwoba_table` (main `/analytics` page) and the physics-native
`build_xwoba_ev_table` (player O27i page). The Savant `.816` is the latter; keep
them from being compared against each other.

---

## 5. Invariant coverage and the proposed new invariant

`tests/test_stat_invariants.py` has **10** invariants (phase-out caps, OR
reconciliation, pitcher↔batter cross-checks, OS%/W bounds, PA identity, row
uniqueness, FIP/wERA anchoring, walk-back runs, TTO buckets). **None** checks WAR
against its own components.

**Proposed invariant — "surfaced ⇒ summed":** every component surfaced in the UI
must be the *same value* that flows into WAR, and WAR must equal their sum within
tolerance. As the code stands today this **fails immediately** because WAR:

- omits baserunning (BSR is surfaced, not summed),
- omits a standalone positional term,
- reads fielding from scout DRS while the Savant surface displays OAA/FRV.

That failure is the *point* — the invariant is what would have caught this drift.
It should be added **after** the §6 mitigations make it passable, so it locks the
fix in rather than landing red.

---

## 6. Mitigation roadmap (recommended — not yet applied)

Ordered by leverage. None of these touch player ratings or the sim engine; they
re-point the *aggregation/metric* layer.

1. **Decide the canonical defensive input for WAR.** Either:
   - (a) feed event-based fielding (OAA → Field Runs) into the WAR defensive term
     so the season card and Savant page agree — **but only after** §6.3 fixes OAA,
     or
   - (b) explicitly keep scout DRS in WAR and *label both surfaces* so the rating
     prior and the event metric are not read as the same quantity.
   Recommendation: (a), gated on (3).

2. **Add baserunning to WAR.** BSR (+6.0) is computed and surfaced but dropped on
   the floor. Convert BSR runs → wins (`/rpw`) and add the term, so the surfaced
   component is the summed one.

3. **Fix OAA attribution before trusting it anywhere:**
   - Exclude HRs (and ideally clearly-uncatchable XBH) from the chance set, or cap
     their `−exp` contribution.
   - Make out-credit and hit-charge use the *same* attribution model — either make
     `_select_fielder` spray-aware, or charge hits by the same weighted draw used
     for outs, so the everyday regular isn't asymmetrically penalized.
   - Raise the exact-attribution share (persist `fielder_id` on more outs;
     consider persisting a responsible-fielder hint on hits) and surface
     `exact_pct` prominently as a confidence flag.

4. **Reconcile the two xwOBA implementations** or clearly scope each to its page so
   `.816` (physics-native) is never compared against the legacy quality-bucket
   value.

5. **Add the §5 "surfaced ⇒ summed" invariant** once 1–2 land, to prevent future
   drift as new metrics are added.

6. **Audit pass for other early-built stats.** Per the owner: these metrics were
   built early; BSR, OAA, Field Runs, and physics-first xwOBA were layered in
   later. A broader sweep for other surfaces still reading superseded inputs is
   warranted — WAR-vs-OAA is unlikely to be the only drift.

---

## 7. What I did NOT do

- No engine, rating, formula, or stat changes in the audit commit that introduced
  this report — findings only.
- Did not compute the exact VORP/rpw split or the league OAA distribution: this
  sandbox has no live DB and no `flask`/`numpy`. Exact queries are provided in
  §2.3 and §3.1 for the owner's DB.
- Mitigations in §6 are recommendations; they land in subsequent, separately
  reviewable commits.
