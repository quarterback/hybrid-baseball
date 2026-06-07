# After-Action Report — Cricket Batting Order: the earned, use-or-lose manager decision

**Date completed:** 2026-06-06
**Branch:** `claude/o27-cricket-batting-order-k6hhp`
**Precedes:** `docs/aar-cricket-batting-order.md` (the original optional-rule feature,
which this work refined) and `docs/aar-cricket-batting-order-flip-aware-lineups.md`
(the lineup-construction follow-on).

---

## TL;DR

The first cut of the Cricket Batting Order rule flipped the order *automatically*
at the end of every joker-free trip. The user reframed it: the flip is **earned,
use-or-lose, and a manager decision**, and it is **regulation-only**.

> A joker-free trip *earns* one flip (1-9 → 9-1). It's use-or-lose at the top of
> the next cycle, and whether to spend it is a manager choice driven by a
> flip-aggression persona and the game situation (score + out-arc). Deploying a
> joker forfeits the chance to earn one, so jokers and flips trade off.

The behaviour this produces: managers self-sort into **flip-minded** (hoard
jokers, live on the churn), **joker-happy** (rarely earn or spend a flip), and
**situational** (weigh joker-now vs. flip-next).

---

## What was asked for

> "There is no use-or-lose [aggregation]. You get one if you cycle through the
> lineup without using a joker, and it's use-or-lose with the first batter of the
> new cycle … there are managers who prefer to use it … teams that use jokers more
> liberally would be less inclined to use this … and a segment who use both
> situationally based on the score and where in the arc of outs we are."

Plus a separate instruction: **regulation only**.

---

## What changed

**Engine — earn / decide / apply, split across the right layers:**
- `cricket_order.py` — `cricket_order_on` (gate), `can_flip`, `flip_line`
  (describe pre-reversal; names the new leadoff), `apply_flip` (reverse in place).
- `state.py` — `Team.advance_lineup` arms `Team.pending_flip` on a joker-free wrap
  (only when the rule is on); `Team.mgr_flip_aggression` persona field.
- `game.py` — `run_half` clears `pending_flip` at half start, so an unspent flip
  is lost rather than leaked into the team's next batting half.
- `prob.py` — `_maybe_cricket_flip` consumes the pending flip (use-or-lose), gates
  rule + regulation, asks `should_use_flip`, returns a `cricket_flip` event.
- `manager.py` — `should_use_flip` (persona × situational), `joker_flip_damp`
  (folded into `should_insert_joker`), `_in_regulation`.
- `pa.py` / `render.py` — `apply_event` reverses on the `cricket_flip` event;
  `render_event` emits the precomputed line.
- `config.py` — `CRICKET_FLIP_*` and `CRICKET_JOKER_FLIP_DAMP` tunables.

**Persona axis (`mgr_flip_aggression`, 0.5 neutral):** derived inversely from each
archetype's `joker_aggression` (deploying jokers is what forfeits flips) with a
small leverage nudge toward the situational middle, in `managers.py:_flip_aggression`.
Stored on `teams.mgr_flip_aggression` (db.py column + migration), persisted in the
seeding INSERT (`league.py`), and stamped onto the engine Team (`sim.py`). Verified
spread: joker-happy archetypes ≈0.24–0.35, joker-sparing ≈0.63–0.73.

---

## Design decisions worth recording

- **Why the flip is a provider-returned event, not a mutation at the wrap.** The
  decision needs `rng` + the manager, which live in the provider — not in
  `apply_event` (no rng there). And the renderer captures PA context *before* the
  provider runs, so mutating the lineup inside the provider would desync the next
  PA's context. Returning a `cricket_flip` event — applied in `apply_event`,
  rendered via an early-return in `render_event` — decides with `rng`, reverses
  before the *next* PA's context is captured, and surfaces on both the render and
  raw-log paths. Same shape as `joker_insertion` / `declaration`.
- **Why `mgr_flip_aggression` is derived, not authored per-archetype.** The rule's
  defining tension is joker-vs-flip, so flip preference is naturally inverse to
  `joker_aggression`. Deriving it (one helper) avoided editing all 17 archetypes
  and threading a brittle new value through the single, already-partial seeding
  INSERT — while still being a real, stored, stamped, tunable persona.
- **Use-or-lose, enforced in two places.** The provider clears `pending_flip` the
  moment it reaches a decision (used or not); `run_half` clears it at every half
  start. So it never banks and never crosses a half boundary.
- **The joker trade is situational by construction.** `joker_flip_damp` only bites
  while the trip is still joker-free and folds into the existing leverage roll, so
  a genuinely high-leverage spot still clears the lowered bar — that is the
  "weigh joker-now vs flip-next" behaviour, not a hard veto.

---

## What bit me

The first (automatic) version appended the flip line in `pa.py` and assumed it
would render. It didn't: `run_half` discards the raw `apply_event` log when a
`Renderer` is present and rebuilds the play-by-play from the renderer. The
event-based redesign fixes this structurally — the `cricket_flip` event renders
like any other — and I verified the line shows on both paths.

---

## Validation

- `pytest o27/tests/test_cricket_order.py` — pending-flip arming, flip helpers,
  `should_use_flip` persona/situational monotonicity, `joker_flip_damp` scaling +
  inertness (rule-off / super-inning / joker-already-used), end-to-end.
- `pytest o27/tests` — full engine suite green.
- Provider: regulation spends the flip (event + pending cleared); super-inning
  declines and still clears it. Live game: flip line names the new leadoff and
  that hitter bats next; render and no-render paths both show it.
- DB fresh + legacy migrations land `cricket_order_enabled` and
  `mgr_flip_aggression`; INSERT counts align 31/31/31.

---

## Not changed / follow-ups
- **Flip-aware lineup construction** — addressed next; see
  `docs/aar-cricket-batting-order-flip-aware-lineups.md`.
- No new stat family; no dismissal/all-out model (breaks the 27-out half).
