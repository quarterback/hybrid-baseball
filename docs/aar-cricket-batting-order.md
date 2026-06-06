# After-Action Report — Cricket Batting Order: the optional rule (scaffold + first flip)

**Date completed:** 2026-06-06
**Branch:** `claude/o27-cricket-batting-order-k6hhp`
**Followed by:** `docs/aar-cricket-batting-order-manager-decision.md` (turned the
automatic flip below into an earned, use-or-lose manager decision) and
`docs/aar-cricket-batting-order-flip-aware-lineups.md`. The living spec is
`docs/feature-cricket-batting-order.md`.

---

## TL;DR

The user wanted O27 to take on a "cricket-style batting order." Cricket's defining
order rule — bat in fixed order, retired once dismissed, all-out ends the innings —
conflicts head-on with O27's 27-out half (a ~9–12 batter side would be all-out long
before 27 outs). So I clarified scope up front and landed on a much smaller,
native-feeling mechanic, shipped as an **optional, per-league rule** (the Power Play
pattern):

> When enabled, the batting order flips 1-9 → 9-1 at the end of a trip through the
> order that the side completed **without deploying a joker**. Using a joker locks
> the order for that cycle.

This AAR covers the **scope decision and the optional-rule scaffold** — the gate,
the per-league plumbing, and the first (automatic) flip mechanic. A follow-on AAR
turns the automatic flip into a manager decision; this document is the historical
record of the foundation.

---

## Scope (two questions up front)

I surfaced the conflict with the 27-out core and asked: (1) what should "cricket
batting order" mean — full dismissal model, partnerships, reorder-only, or a design
doc; and (2) how broad — global change or optional per-league rule. The user chose
**reorder-only**, shipped as an **optional per-league rule**. So no other rule
(outs, stays, jokers, walk-back, Declared Seconds) changes; the rule only reorders
the existing cycle, and only when a league turns it on.

---

## What it hooks into

The existing cycle machinery already had the needed signal: `Team.advance_lineup`
detects the wrap to the top of the order, increments `lineup_cycle_number`, and
resets `jokers_used_this_cycle` — and that set (populated in `manager.py` when a
joker is inserted) is exactly the record of "was a joker deployed this trip?",
readable at the wrap before it's cleared.

---

## What was built (the optional-rule scaffold)

Mirrors Power Play end to end, so the two optional rules behave identically with
respect to global-vs-per-league control:

- **Gate** — `o27/engine/cricket_order.py:cricket_order_on(team)`: per-team
  override (stamped from the league flag) first, else the global config default;
  i.e. **on if the league opted in OR the global default is on**.
- **Global default** — `cfg.CRICKET_BATTING_ORDER_ENABLED`, auto-exposed on the
  Engine Settings dashboard via `engine_config`.
- **Storage** — `teams.cricket_order_enabled` column (CREATE TABLE + idempotent
  ALTER migration) in `db.py`.
- **Per-game read** — `sim.py` stamps the override on *both* teams (both sides bat)
  when the league opted in.
- **UI opt-in** — new-league checkbox, peer-universe per-league `<select>`, and a
  `/league/edit` toggle for existing leagues, with their `app.py` handlers.

### The first flip mechanic (later superseded)
The initial mechanic flipped the order **automatically** inside `advance_lineup`:
on a joker-free wrap, reverse the lineup for the next cycle. This worked (verified:
8 flips/game with two joker-less sides; the #9-hitting pitcher led off the next
cycle), and was off-by-default = byte-for-byte unchanged. It was then reframed into
a manager decision — see the manager-decision AAR — which also fixed a rendering
gap (the raw `apply_event` log is discarded when a Renderer is present).

---

## Validation (this line of work)

- New engine test module exercising the flip and the gate; full `o27/tests` suite
  green.
- Off-by-default verified inert; on-by-default flips fire in live games.
- DB column + migration verified on fresh and legacy `teams` tables; all changed
  Python byte-compiles; edited Jinja templates parse.

---

## Not changed / handed to follow-ons
- The flip as a **manager decision** (earn / use-or-lose / persona / regulation
  only) → `aar-cricket-batting-order-manager-decision.md`.
- **Flip-aware lineup construction** → `aar-cricket-batting-order-flip-aware-lineups.md`.
- No new stats; no dismissal/all-out model (out of scope — breaks the 27-out half).
