# Design — Forking O27 into a College Dual-Match Tennis Simulator

*Research + architecture + roadmap. Origin record for a new standalone repo.*

## Context

Fork the structures in this repo (`hybrid-baseball`) — plus `viperball` and
`or-tennis-data` — into a **college dual-match tennis simulator**: real schools /
fictional players, a globally-expanded footprint via the existing name
generators, **D1/D2/D3 × men's/women's simulated concurrently**, a **juniors +
high-school circuit** that exists mainly to populate *accomplishments for
recruiting*, and a **modified-UTR rating** as the connective metric.

**Headline finding:** this is not a from-scratch fork. ~70–80% of the *ecosystem*
already exists here in working form — the college tier is the recruiting premise
already built, the youth tier is the juniors/HS feeder, the peer-universe config
runs concurrent independent leagues with a `gender` field, the name generators are
global + gender-aware, and the **currency / valuation / auction / motivation**
systems are exactly the substrate for a scholarship economy + recruit-decision
engine. The genuinely new builds are the **tennis match engine**, the
**modified-UTR rating**, the **junior/HS tournament & ranking circuits**, and the
**recruiting layer** (scholarships, academics, pro-defection).

**Decisions locked:** new **standalone repo**; **hybrid** match fidelity
(point-by-point for college/showcase, fast model for bulk juniors/HS); **true
UTR-style** rating but **remapped to a distinctive band** (not 1–17 — see §2);
deliverable now is this **architecture + roadmap doc** (no code yet).

> Scope note: `viperball` and `or-tennis-data` were not mounted when this doc was
> written. The name pools here are already "viperball-derived," so viperball's
> contribution is largely present. `or-tennis-data` + public sources (UTR site,
> USTA/ITF junior schedules, college recruiting sites/sims) are the **data
> sources** — flagged per phase in §7–§8.

---

## What already exists and should be lifted (don't rebuild)

| Capability | Where it lives now | Tennis mapping |
|---|---|---|
| **Recruiting w/ hidden potential + scouting reveal** | `o27v2/college.py`, `college_potential.py`, `college_league.py` (`docs/aar-college-tier.md`) | The recruiting model itself: hidden `potential_X` + static `access_X` lens; displayed = `round(potential×access)`; growth via interest-rate × cap; **two independent scouting reports** (shared service + own dept) blurred ±fog; **reveal on commit**. Re-skin grades as tennis attributes. |
| **Juniors / HS feeder** | `o27v2/youth.py`, `youth_sim.py` (YPI suppression) | Youth tiers that accrue results/accomplishments at suppressed visibility, then feed recruiting. |
| **Concurrent independent leagues (D1/D2/D3 × M/W)** | `data/league_configs/*.json`, `_UNIVERSE_SPEC.md`, `schedule_mode:"independent"`, `gender` field, `teams.league`/`division` cols | Six co-equal universes in one DB, each its own talent style + locale. |
| **Global, gender-aware name generation** | `zaryan_names.py`, `league.py:make_name_picker`, `data/names/{regions,male_first,female_first,surnames,team_naming}.json` | Global, locale-weighted, gender-aware, ISO-country-tagged for flags. Per-school locale blends skew rosters; a tunable domestic/intl slice (§5) rides on this. |
| **Scholarship money substrate** | `o27v2/currency.py` (guilder + tier-caps), `valuation.py` (`estimate_player_value`, `trade_value`) | Per-division cap → per-program scholarship budget; player value → asking price. |
| **Competing-offer mechanics** | `o27v2/auction.py` (personality-driven bid profiles, tranched caps) | Re-tool from sealed single-lot to **open multi-offer** recruiting. |
| **Recruit-decision engine** | `o27v2/trades.py` + `front_office.py` (motivation scorers `(value,ctx)`, acceptance thresholds scaled by context) | Near-perfect fit: swap trade motivations for prestige / playing-time / location / pro-development / **academic-fit**; threshold seeded from player character attrs. |
| **Pro-defection mechanic** | `college.py:sign_to_pro` + scouting fog | Invert: a recruit's "go pro / never enroll" is the same reveal machinery — discovered when they decline you. |
| **Character attrs for decisions** | `players.leadership`, `work_ethic`, `work_habits`, `grit` (`db.py`) | Seed loyalty / training-drive; **add a new `academic_rating`** (§5). |
| **DB + persistence, season archive, career, HOF, awards, leaders, aging** | `db.py`, `sim.py` atomic writes, `season_archive.py`, `player_career_lines`, `hof.py`, `awards.py`, `development.py` | Accomplishments ledger + rankings history + career/All-American/HOF; tennis age curve. |
| **Deterministic seeded engine skeleton** | `o27/engine/game.py:run_game`, event-provider, single `random.Random(seed)`, renderer/stat accumulation, persisted `games.seed` | Keep the shape; swap the sport inside. |
| **Stats site + LLM narrative** | `o27/almanac/`, `o27/gazette/` | Rankings/recruiting site + recruit narrative profiles. |

---

## What is genuinely new

### 1. Tennis match engine (hybrid fidelity)

Mirror the `o27/engine` skeleton (deterministic, event-provider, renderer):

- **Point-by-point** (college + showcase): serve (1st/2nd, **ace/double-fault/
  in-play**) → rally → point outcome (**winner/forced/unforced error**) → game
  (15-30-40, deuce/ad) → set (first-to-6 by 2, **tiebreak at 6–6**) → match
  (best-of-3, optional 3rd-set match-tiebreak). Native stats: aces, DFs, 1st-serve
  %, serve points won, break points faced/saved/converted, winners, UEs, set
  scores; full PBP like `game_pbp`.
- **Fast game-level** (bulk juniors + HS): per-server **hold probability** from
  rating diff → games → sets → scoreline only. For volume.
- **Tier routing:** `simulate_match(..., fidelity="full"|"fast")`.
- **Dual-match team layer (NCAA format):** 3 doubles → **1 team point** (win 2 of
  3), then 6 singles → 6 points; **first to 4 of 7 clinches** (model
  abandoned-after-clinch as a flag). Lineup ordering (singles 1–6 by rating),
  doubles pairings. *(or-tennis-data: confirm per-division/gender format + no-ad.)*
- **Attributes → outcomes:** serve power, serve placement, return, forehand,
  backhand, movement, stamina, mental/clutch, consistency — feeding the serve/rally
  tables (same "talent shifts the distribution" idiom as `o27/engine/prob.py`).
  Optional **surface** (hard/clay/grass) modifier — deferred unless wanted in v1.

### 2. Modified-UTR rating (the metric)

True-UTR math, **remapped display band**:

- Each match → a **match rating** from **% of games won** vs an
  **expected-games-won-vs-rating-diff** curve (fitted logistic). Beating the
  expected share against a strong opponent pulls you up.
- Player rating = **reliability/recency/competitiveness-weighted** average of
  recent match ratings (rolling window); reliability grows with match count and is
  shown alongside.
- Opponent-relative → **cross-division & cross-gender comparable on one axis** —
  the single recruiting yardstick spanning juniors → HS → college → (pro).
- **Display band:** keep the internal computation, then **linearly remap to a
  distinctive band** (e.g. **31.0–48.0**, tunable) so the number reads as "ours,"
  not real UTR's 1–16.5. Final band is a one-line tunable; 31–48 is the working
  default. Separate from the **points-ranking** (§4) — rating ≠ ranking.
- Implementation: post-event **iterative convergence pass** over the match corpus
  (ratings depend on opponents' ratings → fixed point). Persist **rating-history**
  per player; surface on profiles + a global rankings page. *(or-tennis-data / UTR
  site: calibrate the expected curve.)*

### 3. Tennis stat catalog + recruiting profile

A `docs/stats-reference.md` analog (every tennis stat + formula) and a recruiting
profile (rating + reliability, W/L, results-vs-top-N, titles, surface splits,
**academic band**, ranking lists) — built on career-line / leaders / almanac code.

---

## §4 — Junior/HS tournament & ranking circuits (new)

Mirror the real competitive structure; bulk-sim with the fast model; output drives
rankings + UTR + accomplishments.

- **Junior tournament tiers — mirror real:**
  - **USTA national pyramid:** Level 5 → Level 4 → Level 3 → Level 2 → Level 1
    (sectional → national), each with its own draw size, field strength, and
    **ranking-points table**.
  - **ITF World Tennis Tour Juniors:** grades (J30 … J500 / Grade A) +
    **junior Grand Slams** as the apex events.
  - A **calendar/schedule** placing these across the year, so juniors accumulate a
    real results record (not just a season W/L).
- **Ranking lists (the recruiting surface):**
  - **National Top 100 by graduating class** (so coaches recruit a class).
  - **State-by-state lists** (domestic depth).
  - **International:** generated **Top 100 juniors** worldwide + **Top 10 by
    nation**.
  - Rankings are **points-based** (from tournament results) and live alongside the
    modified-UTR number — two distinct signals.
- **High school:** model from college-website HS-sports conventions / existing
  college-recruiting sims; produces the domestic class/state lists above and the
  pool eligible for **US scholarships**.

---

## §5 — Recruiting model (the core of the sim)

### Availability & pro-defection (discovered through the reveal)

- A configurable top slice of juniors (e.g. **Top ~10–15 globally**, tunable N,
  plus high-end domestic) carry a hidden **`pro_track` propensity**: they may
  **turn pro / never enroll**, making them unavailable to recruit.
- **Discovery via the reveal:** for some it's **apparent early** (strong signal in
  scouting); for others it's a **dice roll resolved late** — you invest recruiting
  effort, then they **decline and go pro**. This reuses the college scouting-fog +
  the `trades.py` motivation-acceptance threshold, and is literally the inverse of
  `sign_to_pro`.

### Scholarship economy (equivalency-sport, partial scholarships)

- **Fractional offers:** full, **½, ¼, ⅙** (an equivalency-sport budget split),
  on top of `currency.py` per-division caps → **per-program scholarship budget**.
- **Division weighting:** **D1 weighted highest**, D2 middle; **D3 = commitment
  slots** (no athletic money IRL) — but **top-tier D3 + Ivy prestige** carries a
  **multiplier** that makes them competitive with D1 *for the right recruit*
  (because tennis skews academic). This is what makes the D3/Ivy path real.
- **Domestic-signing knob:** a tunable **% of D1 signings reserved for domestic
  (US) players** — HS players (domestic and international) *can* get US
  scholarships, but you dial the domestic/intl mix at D1.
- **Offer flow:** open competing offers (re-tooled `auction.py`), recruit weighs
  them via the **motivation engine** (`trades.py`/`front_office.py`):
  prestige · playing-time path · location · pro-development · **academic fit** ·
  scholarship size — threshold seeded from `leadership`/`work_ethic`/new attrs.

### Academic model (directs top students to top schools)

- New **`academic_rating`** attribute on players (banded distribution); programs
  carry an **`academic_strength`** rating (Ivies, UAA, NESCAC, Stanford, etc.).
- An **academic-fit motivation** routes high-academic recruits toward
  high-academic programs — the lever that lets Ivy/top-D3 out-recruit a lesser D1
  for an academically-inclined player.

---

## §6 — Proposed new-repo architecture (mirror the o27v2 split)

```
tennis-sim/
  engine/      match.py (point→game→set→match) · rally.py (serve/rally tables)
               · fast.py (hold-prob scoreline) · dual.py (doubles+6 singles, clinch)
               · state.py · render.py (stats + PBP)
  app/         db.py (lifted schema/txn) · league.py · schedule.py · sim.py
               · season_archive.py · rating.py (NEW modified-UTR + convergence + history)
               · circuits.py (NEW juniors/HS tournaments + points rankings)
               · recruiting.py (college.py+youth.py re-skinned: potential/access/scouting,
                 scholarships, academics, pro-defection, commit-reveal)
               · economy.py (currency.py+valuation.py+auction.py re-skinned)
               · awards.py · hof.py · development.py · web/ (Flask + templates)
  generators/  zaryan_names.py + data/names/*   (lifted verbatim)
  data/        schools/ (D1/D2/D3 × M/W) · conferences/ · tournaments/ (USTA L5–L1,
               ITF grades, junior slams) · league_configs/ (6 peer universes)
  almanac/ gazette/   (lifted)   docs/   (tennis stats-reference, AARs, this plan)
```

**Ecosystem flow:** juniors + HS circuits (fast sim, suppressed visibility, real
tournament calendar) → results accrue rating + ranking + accomplishments →
**recruiting** (hidden potential/access; scholarship offers; academic + motivation
fit; pro-defection discovered via reveal) → committed players enter the six
concurrent college divisions → dual matches (full fidelity) → rating updates
continuously across all tiers and both genders → career archive / All-American /
HOF. The modified-UTR is the single currency spanning every tier.

---

## §7 — Roadmap (phased; each phase demoable)

- **P0 — Scaffold + lift.** New repo; copy generators, `db.py` idioms, engine
  skeleton, currency/valuation/auction/motivation modules. Build schools /
  conferences / locale data per division × gender (seed from `or-tennis-data`;
  reuse the college-tier 195-program catalog pattern).
- **P1 — Singles match engine (full).** Point→game→set→match, deterministic,
  stats + PBP, `simulate_match --seed N`. Determinism + scoring tests.
- **P2 — Dual-match team layer.** Doubles + 6 singles, lineup order, clinch-to-4.
- **P3 — Fast model + tier routing.** Hold-prob model; `fidelity` switch for bulk.
- **P4 — Leagues/seasons/schedule.** Six concurrent peer-universe divisions
  (D1/D2/D3 × M/W); schedules, standings, atomic persistence, season archive.
- **P5 — Modified-UTR rating.** Match-rating + reliability + convergence +
  history; remapped display band; global rankings page. Calibrate vs. real UTR.
- **P6 — Junior/HS circuits + rankings.** USTA L5–L1 + ITF grades + junior slams
  calendar; points-based national-by-class / state / intl-Top-100 / Top-10-by-
  nation lists.
- **P7 — Recruiting layer.** Scholarship economy (fractions, division weighting,
  D3/Ivy prestige, domestic-signing knob); academic-rating + academic-fit;
  pro-defection + commit-reveal; recruiting board.
- **P8 — Web UI + content.** Rankings, recruit profiles, match/dual box scores,
  recruiting board; almanac site + gazette narratives.
- **P9 — Career/awards/HOF.** Career archive, All-American/conference honors,
  points-based HOF analog; optional pro tier graduates feed into.

---

## §8 — Data sourcing (what to mirror, from where)

- **Modified-UTR calibration & shape:** UTR site (rating semantics, reliability,
  games-won model) → fit the expected curve; pick the display band.
- **Junior tournament tiers & points:** USTA Level 5→1 structure; ITF World Tennis
  Tour Juniors grades + junior Grand Slams; build the points tables + calendar.
- **HS + recruiting conventions:** college-website HS-sports data and/or existing
  college-recruiting sims; national Top-100-by-class + state-by-state lists.
- **International juniors:** generated Top-100 + Top-10-by-nation lists.
- **Schools/rosters/results:** `or-tennis-data` (real programs per division ×
  gender, roster sizes, dual-match format specifics, results to validate against).
- **Names/footprint:** already in-repo (viperball-derived pools); extend regions
  for any missing tennis nations.

---

## §9 — Verification

- **Determinism:** same seed → identical transcript + scoreline, per fidelity.
- **Scoring correctness:** deuce/ad, tiebreak at 6–6, best-of-3, dual clinch-at-4
  (incl. abandoned-after-clinch), optional no-ad.
- **Rating sanity:** rating diff predicts games-won share within the fitted curve;
  convergence stable/idempotent; reliability rises with matches; cross-division/
  gender numbers land in plausible bands; display remap is monotonic.
- **Circuit/ranking sanity:** points tables produce sensible Top-100/state/nation
  ordering; better players rise; class lists are stable across a season.
- **Recruiting sanity:** scholarship budgets never exceed caps; fraction sums are
  valid; pro-defection rate ≈ the configured top-N; domestic-signing knob moves the
  D1 mix; academic-fit routes high-academic recruits to high-academic programs.
- **Stat invariants:** tennis `test_stat_invariants.py` (points won+lost=total;
  aces ≤ 1st-serve points; BP saved+lost=faced; team points ≤ 7, clinch at 4).
- **End-to-end:** seed all six divisions + juniors/HS, sim a full year, run the
  convergence pass, smoke-test rankings + a recruit profile + a dual-match box.

---

## §10 — Open questions (early-build, non-blocking)

1. **Pro tier ceiling?** Model a pro circuit graduates/defectors enter (the
   baseball side already signs college→pro), or stop at college?
2. **User role:** does the user play a coach recruiting against hidden grades (the
   college-tier draft board), or is recruiting fully simmed?
3. **Final modified-UTR band:** 31–48 (default) vs 51–67 vs other.
4. **Surfaces in v1** (hard/clay/grass), or defer?
5. **Tunable defaults:** top-N pro-defection size; D1 domestic-signing %; D3/Ivy
   prestige multiplier magnitude.
