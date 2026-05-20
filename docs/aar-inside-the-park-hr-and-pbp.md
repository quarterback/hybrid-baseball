# After-Action Report — Inside-the-Park HRs + Web Play-by-Play + Defunct-Brand Sponsors

**Date completed:** 2026-05-20
**Branch:** `claude/analyze-home-run-value-bD5c4`

---

## What was asked for

Three things, in two waves, building on the Walk-Back rule shipped
earlier on this branch.

**Wave 1 — surfacing what already existed.**
User asked whether everything that ought to be wired into the UI was
present. Audit found two gaps:
- The engine's full **text play-by-play** was generated every game and
  then thrown away by the v2 sim — the web app only ever showed a box
  score. There was no PBP view at all.
- The **Walk-Back sponsor caption** (`[The Walk-Back is brought to you
  by …]`) existed only in `pa.py`'s raw-log path, which the Renderer-
  driven v2 sim bypasses, so it never appeared anywhere.

User wanted PBP surfaced ("if it's already extant then no reason not
to have it exist") and was fine with the sponsor caption, asking for
the pool to be **real defunct/dormant brands** instead of invented
ones.

**Wave 2 — the forgotten mechanic.**
*"Inside the park home runs aren't really in this game, but should be.
The other thing is, inside-the-park home runs are still eligible for
the same walkback — they just have to touch home plate (after clearing
the bases or if the bases are empty) for it to count as a home run,
same as a walkback."* Plus a follow-up: ITPHRs are vanishingly rare in
modern MLB (~10-20/season) because parks are small and circular, but
O27 runs **deadball-era park variety** (large, irregular outfields), so
prevalence should sit closer to that era. And the scoring rule: **if
the defense is charged with an error on the play, it's not a HR — it's
reached-on-error** ("Little League home run").

Three design calls were locked via a question prompt before building:
- **Trigger:** speed-vs-park contest on deep hits (not a flat roll, not
  error-driven).
- **Failed attempt:** roll between thrown-out-at-home and held-at-3B.
- **Stat treatment:** counts as a regular HR, no separate column.

---

## What was built

### 1. Web play-by-play (`/game/<id>/pbp`)

The v2 sim did `final_state, _log = run_game(...)` — the entire text
log discarded. Now:
- New `game_pbp` table (`game_id` PK, `pbp_text`), kept off the `games`
  row so `SELECT *` stays lean. Idempotent delete-then-insert inside the
  existing atomic write txn, matching the `game_pa_log` pattern.
- New read-only route + `pbp.html`, linked from the box score via a
  "Play-by-Play" button. Games simulated before this landed have no
  row and show a friendly notice rather than 404.

Only games run after this carry a log; historical games keep just the
box score.

### 2. Defunct-brand sponsor pool

`config.WALK_BACK_SPONSORS` swapped from invented names to ~40 real
defunct/dormant brands (Oldsmobile, Blockbuster, Enron, Zune, Pan Am,
Lehman Brothers, …). The gag: dead brands sponsor the dead-time ritual
of an HR hitter trudging back to third.

**The catch that made the caption actually appear.** There are two
render paths. `pa.py` builds raw-log strings (used by the CLI, no
Renderer). The **Renderer** rebuilds lines from events via
`render_event` (used by the v2 sim) and its HR branch returned early —
it never emitted the Walk-Back arming or sponsor lines. So persistence
alone wouldn't have surfaced them. Fixed by mirroring both captions in
the Renderer's HR branch, gated on `state_after.walk_back_pending`.

### 3. Inside-the-park home runs — the headline

New contest in `o27/engine/prob.py`, `_resolve_inside_park_hr(...)`,
called in `resolve_contact()` immediately after `apply_park_effects`
and **before** the Stay decision (a batter circling the bases is a run
outcome by definition — the contest forces `choice="run"` for its
terminal shapes so the Stay mechanic can't strand a circling batter).

**Trigger gate (all must hold):**
- `hit_type == "triple"` — a *clean* deep hit only. A ball misplayed
  for an error carries `hit_type "error"` and is scored reached-on-
  error, never a HR, so the error path can never reach this code. This
  is the "Little League home run" rule, enforced structurally rather
  than with an extra branch.
- Fence at the BIP spray angle ≥ `ITP_HR_MIN_FENCE` (400 ft) — the ball
  has to die in a genuinely deep part of the yard.
- Proxy carry ≥ `ITP_HR_MIN_DISTANCE` (330 ft) — a real drive, not a
  cheap gapper.

**Two-stage resolution:**
1. **P(attempt to circle)** = base + park-depth + speed +
   aggressiveness, capped. A fast, aggressive batter in a cavernous
   park goes; a station-to-station guy holds at third (stays a triple).
2. **P(touches home safely)** = base + speed + baserunning − OF arm
   (the fielder who ran the ball down, else neutral 0.5), clamped.
   - **Success →** `hit_type = "hr"`, `inside_park = True`. Scores like
     any HR (everyone in, batter in) and **arms the Walk-Back**
     downstream automatically — `pa.py`'s arming fires on
     `hit_type in ("hr","home_run")`, so representing the ITPHR as a HR
     gets the Walk-Back, RBI, and HR-credit semantics for free.
   - **Failure →** roll `ITP_HR_FAIL_OUT_*`: either
     **thrown out at home** (`hit_type = "itp_out"`, `batter_safe =
     False` — an out, no hit; runners ahead still score) or
     **held at 3B** (untouched triple). Aggressiveness drives the split.

**Why represent the ITPHR as `hit_type="hr"`** rather than a new type:
~15 call sites across `pa.py`, `prob.py`, `baserunning.py`, and the
Renderer already branch on `hit_type in ("hr","home_run")` for scoring,
HR-credit, Walk-Back arming, and the "everyone scores" path. A flag
(`inside_park`) on the existing HR keeps all of that working untouched;
only the PBP caption reads the flag to print "INSIDE-THE-PARK HOME
RUN!".

**`itp_out` handling.** Added to `_HIT_TYPE_DISPLAY` ("deep drive —
thrown out at home"). The Renderer's stat path credits it as an out
(no hit, PO to the fielder) because `batter_safe=False` and it's not in
the safety-hit set. It clears the bases (`runner_advances=[4,4,4]`), so
a pending Walk-Back runner is driven home — `_walk_back_should_fire`
gained `itp_out` alongside the sac-fly / productive-ground-out cases,
since the runners ahead (including the rule-placed runner) cross even
though the batter is gunned at the plate.

---

## Schema additions (additive)

```
game_pbp(game_id INTEGER PK REFERENCES games(id) ON DELETE CASCADE,
         pbp_text TEXT NOT NULL)
```

Created via `CREATE TABLE IF NOT EXISTS` in `SCHEMA` (run on every
`init_db()`), so existing DBs pick it up. No ALTER needed. The ITPHR
feature adds **no** schema (counts as a regular HR per user direction).

---

## Config knobs (`o27/config.py`)

```
ITP_HR_MIN_FENCE            400.0   fence (ft) at the BIP spray
ITP_HR_MIN_DISTANCE         330.0   proxy carry (ft)
ITP_HR_BASE_ATTEMPT         0.16    base P(attempt) on a qualifying triple
ITP_HR_DEPTH_SCALE          0.0030  +per ft of fence beyond MIN_FENCE
ITP_HR_ATTEMPT_SPEED_SCALE  0.55    +per (speed-0.5)
ITP_HR_ATTEMPT_AGGRO_SCALE  0.35    +per (run_aggressiveness-0.5)
ITP_HR_ATTEMPT_MAX          0.85
ITP_HR_BASE_SUCCESS         0.52    base P(safe at home | attempt)
ITP_HR_SUCCESS_SPEED_SCALE  0.55
ITP_HR_SUCCESS_BASERUN_SCALE 0.35
ITP_HR_SUCCESS_ARM_SCALE    0.45    −per (of_arm-0.5)
ITP_HR_SUCCESS_MIN/MAX      0.08 / 0.92
ITP_HR_FAIL_OUT_BASE        0.55    P(out at home | failed attempt)
ITP_HR_FAIL_OUT_AGGRO_SCALE 0.40    aggressive runners get gunned
```

---

## Files changed

```
o27/config.py                       sponsor pool → defunct brands; ITP_HR_* block
o27/engine/prob.py                  _resolve_inside_park_hr + call + Stay gate
o27/engine/pa.py                    itp_out added to _walk_back_should_fire
o27/render/render.py                ITP caption + Walk-Back/sponsor captions in
                                    Renderer HR branch; itp_out display label;
                                    import _pick_walk_back_sponsor
o27v2/db.py                         game_pbp table in SCHEMA
o27v2/sim.py                        capture + persist pbp_text
o27v2/web/app.py                    /game/<id>/pbp route
o27v2/web/templates/game.html       Play-by-Play link button
o27v2/web/templates/pbp.html        NEW — PBP view
docs/aar-inside-the-park-hr-and-pbp.md  NEW — this report
```

---

## Verification

**ITPHR rate by park shape** (120 games each, cavernous test rosters):

```
shape       ITPHR/120g   /game
balanced         1       0.008
oval             3       0.025
short_porch_rf   3       0.025
cavernous        9       0.075
```

Clean park-depth gradient, exactly the design intent: near-zero in
small symmetric parks (the ball has nowhere to hide), peaking in
cavernous parks. In the cavernous run, 10 ITPHRs against 107 over-the-
fence HRs (~9% of all HRs) plus 8 thrown-out-at-home plays — risk/
reward is real. League-wide this lands well above modern MLB's
~10-20/season but stays concentrated in the deep/irregular parks O27's
generator produces, matching the deadball-era brief.

**PBP route** (Flask test client): game with a log → 200, sponsor +
"INSIDE-THE-PARK HOME RUN!" render; game without → 200 + notice;
missing game → 404; box page links to it.

**Tests:** `o27/tests/` (incl. full Walk-Back suite) + `tests/` +
`o27v2/tests/` all pass in isolation. Two full-suite failures
(`test_gm_noise_can_be_lopsided`, `test_extreme_weather…`) and the
`test_stat_invariants` sqlite errors are pre-existing cross-file
isolation/seed artifacts — each passes alone, and the weather envelope
still holds with the new HR distribution.

---

## Reused vs new

**Reused:**
- `park_effects._fence_at_angle` + `_proxy_distance` — the ITPHR gate
  shares the exact geometry the park-shape hook and spray chart use, so
  "deep enough" means the same thing everywhere.
- The Walk-Back arming / scoring / RBI machinery — the ITPHR rides it
  unchanged by being a `hit_type="hr"` with a flag.
- The `game_pa_log` delete-then-insert idempotency pattern for the new
  `game_pbp` write.

**New:**
- The two-stage ITPHR contest (first post-`resolve_contact` outcome
  rewrite to model a *baserunning* decision rather than pure ball
  physics).
- `itp_out` outcome type and its Walk-Back interaction.
- The web PBP surface and the Renderer-side Walk-Back/sponsor captions.

---

## Honest gaps / what's still open

1. **`itp_out` doesn't credit a catcher PO + OF assist.** A runner
   gunned at home is, in real scoring, an OF assist and a catcher
   putout. The Renderer credits the PO to `fielder_id` (the OF) and no
   assist, because the assist branch is gated on
   ground_out/FC/DP/TP. Cosmetic fielding-stat gap only.

2. **OF arm uses `outcome.fielder_id` if present, else neutral 0.5.**
   On many deep triples `fielder_id` isn't populated, so the relay
   contest leans on park depth + runner attributes more than on the
   specific outfielder's arm. Good-enough; a fuller model would resolve
   the chasing OF from spray angle.

3. **No ITPHR split anywhere.** Per user direction it counts as a plain
   HR. The only place an ITPHR is distinguishable is the PBP caption
   (`inside_park` isn't persisted to `game_pa_log`). If a split is ever
   wanted, add an `inside_park` column to `game_pa_log` and a leaderboard.

4. **Park irregularity is proxied by depth, not angles.** The brief
   mentions odd outfield *angles* causing crazy bounces. The gate keys
   off fence depth at the spray angle, not on quirks like Tal's Hill or
   the Triangle. Those quirks remain visual-only (a standing gap from
   the park-revival AAR).

5. **ITPHR rate not yet checked on a full seeded-league sim** — only on
   fixed cavernous test rosters. Real leagues weight cavernous parks at
   ~13%, so the league-wide rate will be a depth-weighted blend of the
   gradient above; worth a confirming season sim before tuning further.

6. **`itp_out` RBI.** The batter is credited RBI for runners who scored
   ahead of him (the Renderer adds `runs_scored` regardless of
   batter_safe), consistent with how the engine already credits sac
   flies / productive outs. Defensible, but not a strict MLB-scoring
   audit.

---

## Process notes

- The single most important discovery was the **two render paths**.
  The first instinct ("persist the log, done") would have shipped a PBP
  page that silently lacked the Walk-Back/sponsor captions, because the
  v2 sim uses the Renderer, not `pa.py`'s raw log. Tracing why a
  caption that "obviously" existed never appeared (10 games with zero
  hits in grep) is what surfaced it.
- Putting the ITPHR contest **before** the Stay decision but forcing
  `choice="run"` for its terminal shapes mirrors exactly the call the
  park-revival AAR flagged (sample EV/LA early so Stay sees the final
  hit_type). Same hazard, same fix shape.
- Representing the ITPHR as `hit_type="hr"+flag` instead of a third
  hit_type value was the move that kept the diff small: Walk-Back
  arming, HR credit, and "everyone scores" all came for free. The only
  genuinely new outcome type is `itp_out`, and it only needed wiring in
  the few places that distinguish hit-from-out.
- Park-depth gating produced the right gradient on the first calibration
  sim — no divisor-style miss this time, because the gate reuses the
  already-tuned `_proxy_distance` / `_fence_at_angle` rather than a new
  heuristic.
