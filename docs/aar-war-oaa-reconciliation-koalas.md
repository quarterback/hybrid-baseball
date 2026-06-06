# After-Action Report — WAR / OAA reconciliation (Memphis Koalas)

**Date:** 2026-06-06
**Branch:** `claude/war-oaa-reconciliation-koalas-7t0pG`
**Scope:** read-only audit. No engine, rating, or stat changes in this commit —
this is the *finding* record written **before** any mitigation, per the
"document the ask and the honest gaps first" convention. The full technical
deep-dive (every code trace, the arithmetic, the mitigation roadmap, the
proposed invariant, the luck-gap analysis) lives in its companion,
`docs/aar-stat-surface-reconciliation-comprehensive.md`.

---

## The ask

A Memphis Koalas center fielder shows, on two different surfaces:

- **Season card:** WAR **3.65** over 62 G, a clean fielding line (.989 FldPct,
  1 E, CF).
- **O27 Index (Savant) page:** OAA **−25.5**, Field Runs **−19.9**, BSR **+6.0**,
  wOBA **.924** vs xwOBA **.816** (+0.108 "lucky").

A −19.9 fielding contribution and a +3.65 total WAR can only coexist if the bat
is carrying ~+5.5 WAR. The owner asked: does the WAR actually consume the
OAA/Field-Runs penalty, or is it reading a different (lighter) fielding input?
If the two surfaces pull defense from different sources, that's the bug.

## TL;DR — they pull defense from different sources. That's the bug.

The season-card WAR's defensive term is **not** OAA / Field Runs. It is a
**scout-rating-derived DRS** computed from the player's *defense grade*,
regularized to games played. It never reads the batted-ball event log that
OAA/Field Runs are built from. The −19.9 Field Runs never touches WAR.

So the premise that the bat must be doing +5.5 WAR is false: it rests on a
−1.9-WAR fielding input that **isn't in the 3.65 at all**. The bat is doing
~+3.5, the scout-grade defense ~+0.1, and that is the entire number. The two
surfaces are independent estimates of the same player's defense that were never
reconciled — and the older one (scout DRS, baked into WAR) predates the newer
event-based one (OAA, on the Savant page) by a wide margin of development.

## What the WAR actually is (`o27v2/web/app.py:1626-1648`)

```
WAR = VORP_bat/rpw  +  DRS_scout/rpw

  VORP_bat  = (wOBA − 0.85·league_wOBA) × PA / 1.20        # the bat; replacement baked in
  DRS_scout = (pos_def − 0.5) × 2 × (G/162) × range[pos]   # CF range = 8.0
  pos_def   = 0.6·sub_grade + 0.4·general_grade            # _position_defense_for_row, ratings only
```

Two terms only. **Not** in the formula at all:

- **OAA / Field Runs** — `pos_def` comes from `_position_defense_for_row`
  (`app.py:697-725`), which reads the player's `defense` / `defense_outfield`
  rating fields. It never touches `game_pa_log`, never calls
  `build_fielding_value()`.
- **Baserunning** — BSR (+6.0 on the Savant page) is surfaced but is **not a
  term** in WAR.
- **A standalone positional adjustment** — folded into `_POSITION_DRS_RANGE`
  instead of carried separately.

Of the five components the owner listed (batting, baserunning, positional,
fielding, replacement), only **two** are actually in this WAR.

## The Koalas arithmetic

For this CF (range 8.0, 62 G), the fielding term is bounded and small-positive:

```
DRS = (pos_def − 0.5) × 6.12 runs       (2 × 62/162 × 8.0 = 6.12)
   pos_def 0.6 → +0.61   0.7 → +1.22   0.8 → +1.84 runs
dWAR = DRS / rpw ≈ +0.04 … +0.12        (rpw ≈ 13–18 in O27's high-run env)
```

So **3.65 ≈ +3.5 bat + ~+0.1 scout-defense**. The fielding component is **+0.1,
not −1.9.** If the Savant Field Runs (−19.9) were fed through the same `/rpw`,
dWAR ≈ **−1.3 to −2.0** and WAR would land near **1.6–2.3**. That ~1.4–2.1-win
gap *is* the discrepancy between the surfaces, quantified.

(Exact VORP/rpw split needs the live DB — see the comprehensive report for the
query. Sandbox here has no DB and no flask/numpy.)

## Is OAA −25.5 even real? Suspect — an attribution artifact, not a range grade.

−25.5 over 62 G ≈ **−0.41 OAA/game**; the worst qualified MLB defender is
~−0.13/game. Three concrete miscalibration vectors in `build_fielding_value()`
(`o27v2/analytics/expanded.py:356-436`):

1. **HRs and extra-base hits are counted as fielding chances.** Every phase-0 BIP
   with an exit velocity is a "chance"; a center-field home run docks the CF's
   OAA (`−exp`).
2. **Out-vs-hit attribution asymmetry** (the real culprit). Outs are credited by
   the engine's spray-**blind** `_select_fielder` (fly_out → CF 0.50 / LF 0.25 /
   RF 0.25, regardless of where the ball went); hits are charged by **actual**
   trajectory zone, so 100% of central-zone drops land on the everyday CF. The
   regular absorbs *all* of his zone's negatives but only a fixed share of the
   positives — systematically pushing full-time CF/SS toward negative OAA.
3. **Only ~41% of chances are exactly attributed** (per
   `docs/aar-expanded-metrics-and-prospect-index.md`); the rest — every hit — run
   through the heuristic that concentrates on regulars.

FldPct (.989) and OAA (−25.5) can both be "true" because they measure different
things: FldPct sees only the one ball he reached and muffed; OAA (mis)charges him
for ~every ball that landed in center. Neither is wrong about what it measures;
OAA's *charge set* is miscalibrated.

## Which number is right?

**Neither is a trustworthy fielding truth.** The WAR term (+0.1) is a rating
prior that structurally cannot see range (same blind spot as FldPct). The OAA
(−25.5) is event-based but over-charges everyday regulars. The honest statement:
**3.65 WAR is correct given the inputs it actually uses** — it simply does not
incorporate the simulated fielding at all, so it cannot be "wrong" about a
penalty it never applied.

## The luck gap is real (lower-priority question)

Confirmed against `o27v2/analytics/expected_woba.py` (`build_xwoba_ev_table`) and
`docs/aar-physics-first-inversion.md`: post-inversion (2026-06-01) **100% of
batted balls are physics-decided** — EV/LA are sampled from talent *before* the
outcome, and hit type is derived from the trajectory. xwOBA for a ball is the
**league-average** wOBA for its (EV, LA) bin; wOBA is the player's **realized**
result — two separate data flows, no tautology. So this player's +0.108 gap
across all **155/155 physics-decided** batted balls is genuine over-performance
vs contact quality. *Caveat:* any of his games predating the 2026-06-01 inversion
would carry echo-EV (old engine sampled EV *from* the outcome) and should be
excluded before trusting the gap on those rows.

## Invariant coverage

`tests/test_stat_invariants.py` has 10 invariants; **none** checks WAR against its
own components. A "surfaced component must equal the value that flows into WAR"
invariant would *fail immediately* today (WAR omits baserunning, omits a
standalone positional term, and reads fielding from scout DRS while the Savant
surface reads OAA/FRV). That failure *is* the finding — see the comprehensive
report for the proposed invariant.

## Root cause (owner's context)

These stats were built early in development. Since then, new metrics (BSR, OAA,
Field Runs, the physics-first xwOBA) and improved models were layered in, but the
WAR aggregation was never re-pointed at them. The breakage is drift, not a single
bad edit: WAR still reads the oldest defensive estimate (scout grade) while the
UI surfaces the newest (event-based OAA) right beside it.

## What I did NOT do (this commit)

- No engine, rating, formula, or stat changes — audit only, as directed.
- Did not pin the exact VORP/rpw numeric split (no live DB in the sandbox); the
  comprehensive report supplies the exact query to run on the owner's DB.
- Mitigations follow in a subsequent commit, after this record lands.
