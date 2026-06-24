# After-Action Report — Drained rosters: balanced trades + auction floor

**Date:** 2026-06-24
**Branch:** `claude/adoring-meitner-7wsd8p`
**Status:** Shipped. Reproduced the symptom's source, fixed both contributing
paths, regression-tested.

---

## 1. The report

A team in an Indian Subcontinent League save (Chennai Beisbol Sangh, 16–35) was
showing **9 batters / 5 pitchers** — and it was *one* team, mid-season. The
owner first suspected the country preset used "antiquated roster sizes."

## 2. What it actually was

- **Not the preset.** A freshly-seeded Indian Subcontinent league
  (`manage.py initdb --config region_subcontinent`) gives every team a full
  **48-man roster (28 batters / 20 pitchers)** — verified. The preset configs
  set no roster sizes; they all go through the standard 42-active draft.
- **Not injuries.** The injury system is hard-capped (`MIN_ROSTER_THRESHOLD = 7`,
  ~2 hurt at once) — it physically can't strand ~30 players. (My first guess;
  the owner rightly called it absurd.)
- **It was roster *drain* with no net-count protection,** from two paths:
  1. **Lopsided AI trades.** `o27v2/trades.py:_build_offer` builds N-for-1
     packages (`win_now_overpay` sends 3 for 1, `deadline_buyer` 2 for 1,
     `rebuild_fire_sale` 1 for 2–3, etc.). The only guard,
     `_roster_floor_ok_after`, just checks "can still field a lineup"
     (≥1 per position + `_MIN_HEALTHY_PITCHERS = 5`). So a frequent seller bleeds
     net bodies down to exactly that floor — **8 positions + 3 jokers = "Batting · 9",
     5 pitchers = "Pitching · 5".** The screenshot was the floor.
  2. **Thin auction exit.** `apply_auction` assigned each team only what it won;
     a light-spending club could leave the auction far short, with the remainder
     stranded in the FA pool and nothing to refill it.

## 3. The fix (owner-directed)

Two changes, on the existing branch per the owner's call.

### a) Trades are count-balanced (no N-for-1) — `o27v2/trades.py`
- `_balance_offer(send, recv)`: trims the longer side to the shorter side's
  length, keeping the highest-value players on each side, so **every trade is a
  1:1-ratio swap** — equal counts in, equal counts out, net roster size preserved
  on both teams. Applied at the single call site before validation.
- `_validate_offer` gains a hard backstop: reject any offer where
  `len(send) != len(recv)`.
- Value fairness is still gated downstream by `_evaluate_offer` (partner
  acceptance), so balanced deals that aren't fair simply don't fire.

### b) Auction never exits a team short — `o27v2/auction.py`
- `_guarantee_min_roster(team_ids)` (new Step 5 of `apply_auction`): tops any
  short team up from the free-agent pool until it has **every canonical position
  covered, ≥ `AUCTION_MIN_PITCHERS` (11) arms, and ≥ `AUCTION_MIN_ROSTER` (34)
  total** — best-available per need, signings logged as `auction_floor_signing`,
  pitching roles re-derived. This both *repairs* a hollowed team and prevents
  recurrence (call-ups, the way real clubs replace dumped players).

## 4. Validation

- Reproduced full 48-man seeding of the India preset (ruled out the preset).
- `_guarantee_min_roster` against a real seeded DB: a team stripped to 8 players
  refilled to 34 / 11 pitchers / all 8 positions via 26 FA signings.
- `o27v2/tests/test_auction_floor.py` (new, 2 tests): refills a skeleton; no-op
  when already full.
- `o27v2/tests/test_trades.py`: replaced the obsolete
  `test_win_now_overpay_overshoots` (which *expected* the 3-for-1 we removed)
  with `test_trades_are_roster_neutral` — 60 aggressive passes change **no**
  team's net roster count. Full targeted run: 4 passed (incl. floor + fire-sale
  + gm-noise).

## 5. Notes / limitations

- Balancing reduces the volume of lopsided "overpay" trades (win-now can no
  longer buy a star with a 3-prospect bundle); that's the intended trade-off —
  the owner's call was "the AI clearly isn't smart enough" to avoid gutting
  itself, so balance is enforced structurally rather than via smarter valuation.
- The fix prevents *future* drain and repairs teams *at the next auction*. An
  already-hollowed in-progress save (like the reported one) is healed when its
  next auction runs; if you want an immediate one-shot repair of existing saves,
  a `manage.py` backfill calling `_guarantee_min_roster` over all teams is a
  small add — say the word.
- `AUCTION_MIN_ROSTER` / `AUCTION_MIN_PITCHERS` and the trade balance are tunable
  knobs; defaults chosen to match the auction's existing `ROSTER_TARGET`.
