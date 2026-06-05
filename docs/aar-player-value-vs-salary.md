# After-Action Report — Splitting "Est. value" from "Salary" on the Player Page

**Date completed:** 2026-06-05
**Branch:** `claude/player-value-calculation-YjTrP`

The player detail page rendered two money figures side by side — **Salary**
and **Est. value** — and they were *always identical*. The user, looking at
Wardell Withrow (NYM, CF) reading `Salary ₳223,457` and `Est. value ₳223,457`,
asked the obvious question: if they're the same, why show both?

---

## What was actually happening

`valuation.estimate_player_value()` short-circuits: when a player carries a
non-zero persisted `salary`, it returns that salary verbatim and never reaches
the `trade_value` band math. Since every player's salary is seeded *by calling
that same function* at league-creation time, the page was calling the estimator,
the estimator was handing the persisted salary straight back, and the template
dutifully printed the same number twice. The "estimate" never had a chance to
diverge.

So the two labels weren't two concepts that happened to agree — they were one
number printed twice.

## What was asked for

The user picked **"Show surplus/deficit"** from the options: keep both numbers,
but make them mean different things and surface the gap. In baseball terms:

- **Salary** = what the player is *paid* (the frozen contract ledger).
- **Est. value** = what the player is *worth now* (a live market read that
  drifts from the contract as he ages, develops, or slumps).

A player whose value outruns his contract is a bargain; one whose contract
outruns his value is an overpay. That delta is the interesting signal, and it
was being hidden by the short-circuit.

## What changed

1. **`o27v2/valuation.py`** — extracted the recompute path into a new
   `market_value(player, *, league_name)` that *always* derives from
   `trade_value` → band map → league cap, deliberately **ignoring** any
   persisted salary. `estimate_player_value()` is unchanged in behavior
   (still prefers the persisted salary) and now delegates to `market_value`
   for its fallback. The canonical wage ledger — seeding (`league.py`),
   backfill (`manage.py`), payroll sums (`estimate_team_payroll`) — keeps
   using `estimate_player_value` and is untouched.

2. **`o27v2/web/app.py`** (player route) — the page now calls `market_value`
   (not `estimate_player_value`) so `player_est_value` is a true live figure.
   It also computes the percentage delta versus the persisted salary and a
   plain-text surplus label for the tooltip (`currency.format_money(...,
   "guilder")` — the `| money` filter emits an HTML span/button and can't live
   inside a `title=""` attribute).

3. **`o27v2/web/templates/player.html`** — Salary and Est. value now carry
   distinct tooltips ("what he's paid" vs "what he's worth now"), and Est.
   value is followed by a colored badge when the two differ:
   `+18% vs salary` in `var(--good)` (bargain) or `−12% vs salary` in
   `var(--bad)` (overpay). When they match exactly — e.g. a freshly seeded
   player who hasn't aged yet — no badge renders, so the page isn't noisy at
   league start.

## Why they'll now diverge (and why they match at seed)

At seed time salary *is* the market value (same code path), so a brand-new
league shows no badge and the two numbers agree — which is correct, not a bug.
They separate over a season as `trade_value` responds to the player's current
age curve and skill, while the contract stays put. That's the whole point of
the surplus/deficit read.

## Validation

- No `pytest` in this sandbox; ran `py_compile` on `valuation.py` and `app.py`
  (clean) and parsed `player.html` through a bare Jinja `Environment` (clean).
- Spot-checked `market_value` vs `estimate_player_value` on a constructed
  high-skill player: `market_value` returns the live band figure while
  `estimate_player_value` still returns the hand-set salary — confirming the
  split.
- **Not** re-run: the full engine/web suites (need a DB / Flask). The change is
  confined to one display figure and one new pure function; no schema, no sim,
  no seeding logic moved.

## Note on the ₳ symbol

The screenshot showed `₳` (zora), not the base guilder `ƒ`. That's just the
currency pill toggled to zora — the stored values are guilders, and the toggle
is unaffected by this change.
