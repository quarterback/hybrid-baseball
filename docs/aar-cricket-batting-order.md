# After-Action Report — Cricket Batting Order (the earned, use-or-lose flip)

**Date completed:** 2026-06-06
**Branch:** `claude/o27-cricket-batting-order-k6hhp`

---

## TL;DR

The user wanted O27 to take on a "cricket-style batting order." Cricket's defining
order rule (bat in fixed order, retired once dismissed, all-out ends the innings)
conflicts head-on with O27's 27-out half, so we landed on a smaller, native
mechanic shipped as an **optional, per-league, regulation-only rule**:

> Completing a trip through the order *without deploying a joker* earns a flip
> (1-9 → 9-1). It's **use-or-lose** at the top of the next cycle, and **whether to
> spend it is a manager decision** driven by a flip-aggression persona and the game
> situation (score + out-arc). Deploying a joker forfeits the chance to earn one,
> so jokers and flips trade off.

The headline behaviour: managers self-sort into **flip-minded** (hoard jokers,
live on the churn), **joker-happy** (rarely earn or spend a flip), and
**situational** (weigh joker-now vs. flip-next) — which is exactly what the user
asked to "see manifest."

---

## How the design evolved (three rounds)

1. **Scope.** Initial ask was vague; I surfaced the conflict with the 27-out core
   and the user chose "reorder strategy only," shipped as an optional per-league
   rule (the Power Play pattern).
2. **Mechanic.** The user specified: invert 1-9 → 9-1 at the end of a cycle, but
   only on a *joker-free* trip. I built that as an automatic flip inside
   `advance_lineup`.
3. **Refinement (this AAR's real work).** The user reframed it: the flip is **not
   automatic** — it's **earned**, **use-or-lose** at the top of the new cycle, and a
   **manager choice**. Some skippers prefer it (and build around it), joker-happy
   ones don't, and a segment trades the two off situationally. Plus **regulation
   only**. That turned a one-line lineup reversal into a persona-driven decision
   wired through the provider and manager AI.

---

## What changed

**Engine — earn / decide / apply, split across the right layers:**
- `cricket_order.py` — `cricket_order_on` (gate), `can_flip`, `flip_line`
  (describe pre-reversal, names the new leadoff), `apply_flip` (reverse in place).
- `state.py` — `Team.advance_lineup` arms `Team.pending_flip` on a joker-free wrap
  (only when the rule is on); `Team.mgr_flip_aggression` persona field.
- `game.py` — `run_half` clears `pending_flip` at half start so an unspent flip is
  lost, never leaked into the team's next batting half.
- `prob.py` — `_maybe_cricket_flip` consumes the pending flip (use-or-lose), gates
  rule + regulation, asks `should_use_flip`, and returns a `cricket_flip` event.
- `manager.py` — `should_use_flip` (persona × situational), `joker_flip_damp`
  (folded into `should_insert_joker`), `_in_regulation`.
- `pa.py` / `render.py` — `apply_event` reverses on the `cricket_flip` event;
  `render_event` emits the precomputed line.
- `config.py` — `CRICKET_FLIP_*` and `CRICKET_JOKER_FLIP_DAMP` tunables.

**Persona + league plumbing:**
- `managers.py` — `_flip_aggression` derives the axis from existing archetype axes
  (inverse to joker aggression, leverage nudge); `roll_manager` seeds it.
- `db.py` — `teams.cricket_order_enabled` + `teams.mgr_flip_aggression` (+ migrations).
- `league.py` / `sim.py` — INSERT writes the persona; sim stamps persona + rule flag.
- `engine_config.py` + web UI (new-league checkbox, universe select, league-edit
  toggle) — opt a league in.

---

## Design decisions worth recording

- **Why the flip is a provider-returned event, not a mutation at the wrap.** The
  decision needs `rng` + the manager, which live in the provider, not in
  `apply_event` (no rng there). And the renderer captures PA context *before* the
  provider runs, so mutating the lineup inside the provider would desync the
  next PA's context. Returning a `cricket_flip` event — applied in `apply_event`,
  rendered via an early-return in `render_event` — decides with `rng`, reverses
  before the *next* PA's context is captured, and surfaces on both the render and
  raw-log paths. This is the same shape as `joker_insertion` / `declaration`.
- **Why `mgr_flip_aggression` is derived, not authored per-archetype.** The rule's
  defining tension is joker-vs-flip, so flip preference is naturally inverse to
  `joker_aggression`. Deriving it (one helper) avoided editing all 17 archetype
  definitions and threading a brittle new value through the single, already-partial
  seeding INSERT — while still producing a clean three-group spread and being a
  real, stored, stamped, tunable persona.
- **Use-or-lose, enforced in two places.** The provider clears `pending_flip` the
  moment it reaches a decision (used or not); `run_half` clears it at every half
  start. So it never banks and never crosses a half boundary.

---

## What bit me (carried from the first pass)

The first (automatic) version appended the flip line in `pa.py` and assumed it
would render. It didn't: `run_half` discards the raw `apply_event` log when a
`Renderer` is present and rebuilds the play-by-play from the renderer. The
event-based redesign fixes this structurally — the `cricket_flip` event is
rendered like any other — and I verified the line shows on both paths.

---

## Validation

- `pytest o27/tests/test_cricket_order.py` — 16 tests (arming, helpers,
  `should_use_flip` persona/situational monotonicity, `joker_flip_damp` scaling +
  inertness, end-to-end). All pass.
- `pytest o27/tests` — **122 passed.**
- Provider: regulation spends the flip (event + pending cleared); super-inning
  declines and still clears it. Live game: flip line names the new leadoff and that
  hitter bats next; render and no-render paths both show it.
- Persona spread verified (joker-happy ≈0.24–0.35, joker-sparing ≈0.63–0.73). DB
  fresh + legacy migrations both land the two new columns; INSERT counts align 31/31/31.
- All changed Python byte-compiles.

---

## Not changed / follow-ups
- **Flip-aware lineup construction** (`_ordered_lineup`): a flip-minded skipper
  should build an order that reads well in both directions. Deferred — today the
  order is built independently of `mgr_flip_aggression`. This is the clearest next
  step toward the user's "build their lineups with the flip in mind."
- **No new stat family** — the flip only reorders PAs the stat machinery already
  records.
- **No dismissal/all-out model** — explicitly out of scope (breaks the 27-out half).
