# Feature Report — Cricket Batting Order (the earned, use-or-lose flip)

**Status:** complete, on branch `claude/o27-cricket-batting-order-k6hhp`.
**Scope of this report:** the optional Cricket Batting Order rule — what it does,
the manager decision that drives it, the persona axis behind it, and how it is
plumbed. Complements the build-log AAR (`docs/aar-cricket-batting-order.md`).

---

## 1. What it is

**Cricket Batting Order** is an optional, **per-league**, **regulation-only** rule.
While it is on, a side can **flip its batting order 1-9 → 9-1** at the top of a new
trip through the lineup — the tail (the pitcher hitting 9th, the weaker bats)
rotates up to lead off and the openers drop to the bottom. It is not automatic;
it is an **earned, use-or-lose manager decision**:

- **EARN** — completing a trip through the order *without deploying a joker* earns
  one flip opportunity. It does not bank or aggregate; you hold at most one.
- **USE** — at the top of the new cycle the batting manager decides whether to
  spend it. The opportunity is consumed whether he uses it or loses it; it never
  carries to a later cycle or a later half.

Because deploying a joker forfeits the chance to *earn* a flip, the two levers
trade off, and that trade is the whole point:

> A joker buys a high-leverage pinch-hitter **now**; a held joker keeps the
> **flip** alive for the top of the next cycle. A flip-minded skipper hoards his
> jokers and lives on the churn; a joker-happy skipper rarely earns a flip; a
> situational one weighs joker-now vs. flip-next by the **score** and **where in
> the 27-out arc** he is.

**Regulation only.** The use decision declines (and the pending flip is discarded)
in super-innings and Declared Seconds frames.

### Why "the flip" and not a full cricket dismissal model
This reorders the existing cycle; it does not change who bats or how outs are
recorded. A true cricket "out = retired, next man in, all-out ends the innings"
model conflicts with O27's defining 27-out half (a ~9-12 batter side would be
all-out long before 27 outs). The flip keeps every other rule — outs, stays,
jokers, walk-back, Declared Seconds — exactly as-is.

---

## 2. The manager decision

### Persona axis — `mgr_flip_aggression` (0.5 neutral)
A new manager-persona dimension, **derived** from the existing archetype axes
rather than separately authored: flip preference runs *inversely* to
`joker_aggression` (deploying jokers is what forfeits flips), with a small
leverage-awareness nudge toward the situational middle. So the league self-sorts
into the three groups the rule is meant to produce:

| Group | Example archetypes | flip_aggression |
|---|---|---|
| **Flip-minded** (hold jokers, live on the churn) | dead-ball, set-and-forget, iron manager, old-school | ~0.63–0.73 |
| **Situational** (trade joker-now vs flip-next) | balanced, small-ball, modern, players' manager | ~0.45–0.56 |
| **Joker-happy** (rarely earn or spend a flip) | mad scientist, gambler, fiery, platoon | ~0.24–0.35 |

Re-rolled per seed with the archetype's noise band, so two skippers of the same
type still diverge. Stored on `teams.mgr_flip_aggression` and stamped onto the
engine Team like every other persona.

### Spending an earned flip — `manager.should_use_flip`
Probability = `CRICKET_FLIP_BASE_PROB × persona_mult × situational`, capped at
`CRICKET_FLIP_MAX_PROB`:
- **persona_mult** centred on 1.0 at neutral, spanning `1 ± AGG_SCALE/2` across the
  persona range;
- **situational** rises when **trailing** (need offense → churn the order) and
  **later in the out-arc** (less arc left for the order to come good on its own).

### The joker opportunity cost — `manager.joker_flip_damp`
While the rule is on, in regulation, and the current trip is still joker-free,
joker-insertion probability is damped by up to `CRICKET_JOKER_FLIP_DAMP ×
mgr_flip_aggression`. Flip-minded skippers therefore hold jokers (and keep
earning flips); joker-happy ones barely flinch. The damp folds into the existing
leverage roll, so a genuinely high-leverage spot can still clear the lowered bar —
that is the situational "weigh joker-now vs flip-next" behaviour. Once a joker is
spent this cycle the flip is already gone, so further jokers that cycle are
undamped.

All tunables live in `o27/config.py` under the Cricket Batting Order section.

---

## 3. How to turn it on — controls that compose

| Control | Where | Scope | Storage |
|---|---|---|---|
| **Global default** | Engine Settings → "Optional rules" → Cricket Batting Order | every league in the save | `engine_config` → `cfg.CRICKET_BATTING_ORDER_ENABLED` |
| **Per-league (single)** | New-league builder → "Optional rules" checkbox | the league being created | `teams.cricket_order_enabled` |
| **Per-league (universe)** | Peer-league universe builder → per-league "Cricket Order" Off/On select | each league independently | `teams.cricket_order_enabled` |
| **Per-league (existing)** | `/league/edit` → per-league "CO Off / CO On" select | flip an existing league without rebuilding | `teams.cricket_order_enabled` |

**Composition (`cricket_order.cricket_order_on`):** per-team override first, else
the global default — i.e. **on if the league opted in OR the global default is
on**. Identical shape to Power Play. `sim.py` stamps the override on *both* teams
(both sides bat) when the league opted in.

---

## 4. End-to-end plumbing

| Layer | File | What |
|---|---|---|
| Rule gate + flip mechanics | `o27/engine/cricket_order.py` | `cricket_order_on`, `can_flip`, `flip_line` (describe, no mutation), `apply_flip` (reverse in place) |
| Earn the flip | `o27/engine/state.py` | `Team.advance_lineup` arms `Team.pending_flip` on a joker-free wrap (rule on); `Team.mgr_flip_aggression` field |
| Lose the flip at a half | `o27/engine/game.py` | `run_half` clears the batting team's `pending_flip` at half start (use-or-lose, no leak) |
| Decide the flip | `o27/engine/prob.py` | `ProbabilisticProvider._maybe_cricket_flip` consumes the pending flip, gates rule/regulation, calls `should_use_flip`, returns a `cricket_flip` event |
| Manager logic | `o27/engine/manager.py` | `should_use_flip`, `joker_flip_damp` (applied in `should_insert_joker`), `_in_regulation` |
| Apply + render | `o27/engine/pa.py`, `o27/render/render.py` | `apply_event` handles `cricket_flip` (reverse + line) before the next PA's context is captured; `render_event` emits the precomputed line |
| Persona seed | `o27v2/managers.py` | `_flip_aggression` derives the axis; `roll_manager` adds it to the row |
| Storage | `o27v2/db.py` | `teams.cricket_order_enabled` + `teams.mgr_flip_aggression` columns (+ idempotent migrations) |
| Persist + stamp | `o27v2/league.py`, `o27v2/sim.py` | INSERT writes `mgr_flip_aggression`; sim stamps both persona and the per-league rule flag |
| UI | `new_league.html`, `universe_new.html`, `league_edit.html` (+ `app.py`) | checkbox / selects to opt a league in |

**Off = zero behaviour change.** With the rule off, `advance_lineup` never arms a
flip, `joker_flip_damp` returns 1.0, no event is ever produced, and lineups are
built normally — the engine is byte-for-byte unchanged.

### Flip-aware lineup construction
A flip-minded skipper (`mgr_flip_aggression ≥ cfg.CRICKET_FLIP_LINEUP_AGG_MIN`,
default 0.60) whose league runs the rule builds a **"valley" order** — strongest
bats at the ends, weakest (the pitcher) in the middle — so a flip leads the next
cycle with quality instead of the tail. Everyone else builds the standard
best-to-worst order with the pitcher 9th.

**Handedness is a tiebreaker within the valley, never against it.** Directional
balance is the hard constraint; platoon alternation is optimized only by swapping
the two near-equal-talent bats *within* each mirror tier (so the valley structure
is preserved), discarding any arrangement whose forward-vs-reverse disparity
exceeds `cfg.CRICKET_FLIP_DISPARITY_MAX_RATIO` (0.25) of the standard order's, then
minimizing same-handed adjacencies. Implemented in `o27v2/sim.py` (`_valley_order`,
`_handed_valley_order`, `_ordered_lineup(..., flip_minded=…)`), gated in
`_db_team_to_engine`. See `docs/aar-cricket-batting-order-flip-aware-lineups.md`.

---

## 5. Verification

- `pytest o27/tests/test_cricket_order.py` — 16 tests: pending-flip arming (and not
  on a joker trip / rule off / per-team opt-out); flip helpers; `should_use_flip`
  monotonic in persona and rising when trailing / late; `joker_flip_damp` scaling,
  rule-off / super-inning / joker-already-used inertness; end-to-end flips for
  flip-lovers, none with the rule off, and flip-lovers flipping more than
  joker-lovers.
- `pytest o27/tests` — full engine suite, **122 passed**.
- Provider checks: a regulation half spends an earned flip (event returned,
  pending cleared); a super-inning declines and still clears the pending flip
  (use-or-lose, regulation-only). Live game: the flip line names the new leadoff
  and that hitter bats next (timing correct vs. context capture); both render and
  no-render paths surface it.
- Persona spread confirmed: joker-happy archetypes land ~0.24–0.35, joker-sparing
  ones ~0.63–0.73.
- DB: fresh schema and a legacy `teams` table both end with `cricket_order_enabled`
  and `mgr_flip_aggression`; INSERT column/placeholder/value counts align (31/31/31).

## 5b. The auxiliary hitter ("aux") — resolving the on-base leadoff

The flip puts the just-batted #9 back at the top of the next cycle. If he ended
the cycle by *reaching base*, he can't bat while he's a runner. So when the due
batter is already on base during his own turn, the manager drafts a **one-off
auxiliary** to hit in his place: the best available **bench** bat (any roster
hitter not in the active lineup — NOT a designated joker; "line cutters"). The
on-base batter keeps his runner status and the lineup advances past him, so each
stranded batter resolves independently no matter how many are on base.

Plumbing: `state.aux_override` (parallel to the joker override but the lineup DOES
advance afterward), `manager.select_auxiliary`, `prob._maybe_auxiliary`
(emits `aux_insertion`, or `aux_skip` to forfeit the turn when the bench is
empty — the engine never bats a man on base). Bench-only selection is also
load-bearing for correctness (the provider's same-batter new-PA detection).
The hard invariant — no player ever on two bases — is regression-tested. See
`docs/aar-cricket-flip-auxiliary-hitter.md`.

## 6. Not changed / possible follow-ups
- **No new stats.** The flip only reorders PAs the stat machinery already records;
  a "flips per game" telemetry line could be added if it proves interesting.

## 7. AAR trail
- `docs/aar-cricket-batting-order.md` — the optional-rule scaffold + first flip.
- `docs/aar-cricket-batting-order-manager-decision.md` — earned, use-or-lose,
  manager-decided, regulation-only; the `mgr_flip_aggression` persona.
- `docs/aar-cricket-batting-order-flip-aware-lineups.md` — the "valley" lineup.
- `docs/aar-cricket-flip-auxiliary-hitter.md` — the auxiliary that hits for a
  due batter stranded on base after a flip (the no-double-bat fix).
