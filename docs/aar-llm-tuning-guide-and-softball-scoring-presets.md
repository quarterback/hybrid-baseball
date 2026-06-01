# After-Action Report — LLM Tuning Guide + Softball-Derived Scoring Presets

**Date completed:** 2026-06-01
**Branch:** `claude/affectionate-meitner-R8A6h`
**Commits:** `53ee4dd` (guide) → `29ea44f` (links) → `617f213`/`4443e15` (2C,
shifts, Walk-Back coverage) → `bb2773a`→`2eb3fef` (`college_scoring`) → `3eb0a7c`
(`put_it_in_play`)

---

## What was asked for

> "create a tuning guide for this so that i can share it with an llm and get
> various styles of O27 that i want"

The user pasted the full **Engine Tunables** dashboard (every editable constant
with its default) and wanted a document they could hand to any LLM to generate
override blobs for a described style. The scope then grew conversationally
through several genuine asks and corrections:

1. **Surface the mechanics the first draft buried.** The user flagged that the
   stay / second-chance (**2C**) mechanic — O27's signature lever — was missing,
   then asked about shifts, weather, ballpark geometry, and the **Walk-Back**
   rule ("super important to surface, that wasn't in the original ruleset").
2. **"how do we learn from college softball stats"** — then immediately narrowed:
   *"i don't want to go to softball i just want the scoring environment."* The
   goal was to borrow softball's **run environment**, not convert O27 into
   softball.
3. **Two domain corrections on the softball model:** DPs are rare in softball
   *because the bases are 60 ft*, and **O27 plays on 90-ft basepaths** — so that
   rarity is a geometry artifact and must **not** be ported. And the real
   national K-rate is low (a put-it-in-play sport), so don't over-tune whiffs.
4. **Decouple texture from pitcher-dominance.** *"pitching in softball is a lot
   more dominant than it'd be in O27 … but the relatively low strikeouts and
   relatively high hitting is precisely what i want."* Keep the contact texture;
   drop softball's circle-suppressed scoring.

---

## What was built

### 1 — `docs/tuning-guide-for-llms.md` (the deliverable)

A self-contained, paste-into-an-LLM guide grounded in `o27v2/engine_config.py`
and `o27/config.py` (not just the dashboard dump). Sections: the output contract
(a JSON `overrides` object), the baseline run environment and its `characterize()`
band labels, the at-bat pipeline mental model (pitch outcome → contact quality →
outcome table → power/launch redistribution → park → baserunning), high-leverage
knobs with safe ranges (pulled from the engine's own guard-railed randomizer
bands), style→knob recipes, the shipped presets as worked examples, and the
couplings/guardrails (the `CONTACT_*_BASE` triple must sum to ~1.0; `POWER_REDIST_*`
scale per-player so league rates need `CONTACT_HARD_BASE`/`PARK_HR`/`GEN_SHIFT_POWER`
alongside; `GEN_SHIFT_*` only bite future-generated players).

Coverage added after user prompts:

- **Stay / 2C** — a dedicated knob section with the live decision formula
  (`stay_p = stay_aggressiveness × situational multipliers`), the distinction
  between the live multipliers and the regen-time per-player aggressiveness, the
  defense-read counters, and pesäpallo-heavy vs. station-to-station recipes.
- **Defensive shifts** — `SHIFT_*`, `ADAPTABILITY_SCALE`, `BUNT_AGAINST_SHIFT`
  as tunable, with defense-wins / shift-proof recipes.
- **Walk-Back as context, not a knob** — verified it has *no* tunable constant
  (only a cosmetic sponsor list). Surfaced in the baseline section because it
  adds a bonus-run tail to every HR, so power tunings score above their raw HR
  rate. Flagged that it wasn't in the original ruleset.
- **"What is not tunable"** — weather/start-times (infra-level, no engine
  constants) and per-park fence geometry (per-park, not a global knob); the
  `PARK_*` factors are the league-wide park lever.

Linked from the README (feature bullet + docs list) and from the Engine Tunables
page intro (`engine_settings.html`).

### 2 — `college_scoring` preset (faithful softball scaling)

Reproduces D1 softball's scoring *shape* in O27. Calibrated against the user's
**2026 NCAA Div I national totals** (true league averages, not leaderboards),
per team per 21-out game:

```
R 5.11   BA .291   OBP .376   SLG .449   ISO .157   BABIP .316
HR 0.81  2B 1.32   3B 0.18    K% 13.4    BB% 10.3    ERA 3.44
GIDP 0.20/g   DP-turned 0.32/g   SB 0.98/g   SH 0.45/g
```

Scaled to O27's 27 outs: `5.11 / 21 × 27 = 6.57` R/team/game (low-scoring band).
Run suppression comes from the circle (weak-contact pitcher dominance + a big
ace-vs-field `CONTACT_MATCHUP_SHIFT` — ERA leaders ~1.35 vs the 3.44 mean), **not**
from double plays (`GIDP` left at default — see the geometry correction below).
Low `PITCHER_DOM_SWINGING` honours the low league K%.

### 3 — `put_it_in_play` preset (texture on O27's natural state)

The user's "keep the texture, not the sport" cut. Borrows the contact texture
(low strikeouts via `BATTER_CONTACT_SWINGING`/`BATTER_DOM_SWINGING`, high BABIP,
modest power via `POWER_REDIST_HR` 0.40) but leaves pitcher dominance, the matchup
shift, and `GIDP` at O27 default — so scoring stays at O27's natural level instead
of being dragged to softball's circle-suppressed band.

Both presets are additive entries in `PRESETS` + `PRESET_LABELS`; no engine logic
or default constant was changed.

---

## The data-sourcing problem

The user first pointed at `d1softball.com/statistics/`. It and every official /
aggregator source are bot-walled or JS-rendered in this environment: `d1softball`
and `stats.ncaa.org` return **403**, `fastpitchwire` **503**, and `ncaa.com` /
`secsports` render client-side so `WebFetch` gets an empty body. The first cut of
`college_scoring` was therefore built from labelled-approximate anchors. The user
then pasted the real **NCAA national-totals tables** directly, which is what made
the final calibration exact — a reminder that for league aggregates the official
pages are read-in-a-browser sources, not scrape targets.

---

## Corrections that changed the model

1. **DP geometry (60 ft vs 90 ft).** The first calibration *lowered*
   `GIDP_BASE_PROB` to 0.13 to mimic softball's ~0.2 DP/game. The user corrected:
   softball's DP rarity is purely a 60-ft-basepath artifact, and O27 is a 90-ft
   game. So importing it would smuggle a geometry quirk into a *scoring*-
   environment tune. **Resolution:** drop the `GIDP` override entirely; let O27's
   natural DP rate stand; move run suppression onto the circle (pitching) instead.

2. **K% is low (put-it-in-play).** The national totals show K% 13.4 — softball is
   a contact sport, not a strikeout sport. `PITCHER_DOM_SWINGING` dropped
   0.055 → 0.03; the ace edge is modelled as weak contact + matchup spread, not
   whiffs.

3. **Texture vs. pitcher-dominance, decoupled.** The softball data carries three
   separable signals: contact texture (wanted), power level (port partially), and
   scoring level / pitcher dominance (a softball distortion — the circle). The
   user wanted the first two on O27's natural run level. This split produced the
   two presets: `college_scoring` (texture + circle → low scoring) and
   `put_it_in_play` (texture only → natural scoring).

4. **The "killer app" framing.** The user observed that a women's O27 is
   physically realizable: **baseball field (90-ft geometry) + softball equipment
   (bat/ball → contact + power ceiling) + O27 rules (hitting-optimized)**. Each
   layer is sourced from where it actually comes from, which is exactly why
   `put_it_in_play` is the genuine sport and `college_scoring` over-imports the
   circle. (Recorded here rather than in code at the user's request.) NCAA
   softball is the correct empirical anchor for the *equipment* layer only.

---

## Verification

Benchmark via `o27v2/batch.py` (example-team harness; reliable for **relative**
read — these example teams score lower than the live generated league, so absolute
landing should be confirmed in-app on a real roster). Runs are per team per game:

```
DEFAULT (natural O27)                 ~9.0–10.3   (harness variance across seeds)
college_scoring (softball-scaled)     ~6.3–6.8    target 6.57 ✓
put_it_in_play (texture, natural)     ~9.0        target: O27-natural ✓
```

The `college_scoring` sweep that fixed corrections #1/#2 (drop GIDP override +
lower swing-K) landed **6.61** vs the 6.57 scaled target. `put_it_in_play` landed
**9.02**, confirming the texture rides on O27's natural scoring rather than
softball's. The harness measures runs only — BA/HR/K texture is set from the
national rates by construction, not independently measured here.

**Safety check (user asked "this doesn't break my sim, just creates a preset?").**
The session's code change is `engine_config.py` **+59 lines, 0 modified**
(two `PRESETS` entries + two labels). `o27/config.py` and `o27v2/config.py` are
untouched — no default constant changed. Presets are inert until explicitly
loaded; `DEFAULTS` and live sim behaviour are identical unless a preset is chosen,
and loading one only writes a resettable override blob.

---

## Files touched

- `docs/tuning-guide-for-llms.md` — new (the guide).
- `o27v2/engine_config.py` — `college_scoring` + `put_it_in_play` presets and labels (additive).
- `README.md` — two links to the guide.
- `o27v2/web/templates/engine_settings.html` — guide link in the dashboard intro.

---

## Follow-ups (not done)

- Confirm `put_it_in_play` / `college_scoring` absolute landing via the in-app
  "Sim the current working tuning" on a live roster (the authoritative number).
- Optional `womens_o27` reseed target leaning the persistence into `GEN_SHIFT_*`
  (power down, contact up, modest speed) so the equipment-layer identity bakes
  into the talent pool, not just the per-game physics. Deferred — `put_it_in_play`
  already covers the live feel.
