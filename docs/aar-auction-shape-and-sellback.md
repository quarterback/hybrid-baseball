# After-Action Report — Auction Shape: Personality-Driven Bidding, Tranched Caps, Snake-Draft Fill, Sellback Trades

**Date completed:** 2026-05-08
**Branch:** `claude/new-league-structure-tqk8H`
**Commits (in order):**
- `cfa32fe` — Auction shape: personality-driven bidding, tranched caps, snake-draft fill
- `1ea08e5` — Steeper elite tier + post-clear sellback trade mechanic

This AAR picks up where `aar-live-auction-replay.md` left off — the live page
and the guilder rescale were already in. The brief here was different: make
the auction *feel right* before shipping, not just render correctly.

---

## What was asked for

The previous PR (`1dbd273`) produced a flat IPL-shape under the
guilder rescale: top 10 sales clustered at ƒ54-57 cr, mid-tier
60-OVR clearings at ƒ42-51 cr. A 7-OVR talent gap was only producing
a 12% price gap, and Vickrey was collapsing at the per-team cap
because every team's max-bid sat near the same ceiling.

The user said *"dial that in before I shop this, then,"* and then
sent a fast sequence of design notes, paraphrased:

- Wider noise — `0.45 to 1.5`
- *"There's no salary floor in this league and the engine should
  always be looking for arbitrage, rather than having everyone bid
  all the time"* — the idea is personalities that randomize per
  seed so teams pursue consistent strategies, not freewheeling
  per-lot randomness
- *"Wire the manager personalities and the organizational strength
  into the dice rolls"*
- *"The base should actually be lower than the floor, OVR 20 or 25"*
- *"Why not raise the cap?"*
- *"Why not have a flexible cap that can rise — per-round cap?
  Buckets of rounds?"*
- *"Is Vickrey the only option?"*
- *"The hard floor minimum salary deals should just operate like a
  draft not an auction at all"* / *"Snake draft, like roster fill
  I mean"*
- *"Steeper aggression on the top end is a good call. There should
  be a true difference between the top 25 players in the pool versus
  everyone else… you'd expect those first 3 picks to be marquee
  purchases"*
- A sellback / trade mechanic mirroring US-draft trade-back patterns,
  but where the asset is auction budget instead of picks/players.
  *"That should lead to an overpay in different round since the
  money doesn't roll over"*

Final framing on shape: *"I do not care what the top tier number is,
let the teams bid and let's see it play out."* Engine drives the
numbers, not a target.

---

## What shipped

### 1. Quadratic bid curve with lower pivot (cfa32fe)

Replaced the linear `overall × 5_00_000` with
`(overall − 25)² × 1_00_000`. The pivot at OVR 25 — well below the
non-keeper auction-pool floor of ~30 — stretches the curve over the
full talent range so a 67-OVR star's raw base sits 7× above a
50-OVR depth piece, instead of the linear 1.34×. Pure function
`_bid_base(overall)` so the test suite (and future calibration
sweeps) can pin its shape.

### 2. Wider noise, discipline-dampened (cfa32fe)

Per-lot noise band is now `uniform(1 − 0.55·(1−d), 1 + 0.50·(1−d))`
where `d` is the team's discipline. A 25-org team rolls in
`[0.45, 1.50]` (the user-specified band); a 90-org team shrinks to
about `[0.85, 1.10]`. Mirrors the existing in-game `Archetype.noise`
attribute but applied to auction valuations rather than tactical
in-game decisions.

### 3. Manager personality → auction strategy (cfa32fe)

New `_team_auction_profile(team_row)` reads the existing
`org_strength`, `mgr_quick_hook`, `mgr_bullpen_aggression`, and
`mgr_joker_aggression` columns (already persisted at league-seed
time) and derives three knobs:

- `discipline = (org_strength − 20) / 75` — high-org teams have
  tighter noise and identify true value more reliably.
- `star_bias = 0.5 − (mgr_quick_hook + mgr_bullpen_aggression)/2` —
  positive for traditional-leaning managers (overpay for marquee),
  negative for sabermetric-leaning (hunt depth arbitrage).
- `aggression = 0.85 + mgr_joker_aggression × 0.50` — gambler/joker
  managers go big, patient managers pull back.

Profile is sampled once at the start of `apply_auction` and passed
into every `_team_bid` call. Each team reads as a consistent
personality across the entire auction — not freewheeling per-lot
randomness — which is what the user explicitly asked for.

### 4. Tranched per-lot cap (cfa32fe)

Replaced the flat `purse − min_bid × (slots−1)` cap with
`_per_lot_cap(purse, lot_order, slots, min_bid)`. The soft cap is a
fraction of remaining purse, varying by tranche:

```python
TRANCHE_CAPS = (
    ( 50, 0.50),   # marquee — up to 50% of remaining purse on one lot
    (200, 0.20),   # core    — up to 20%
    (500, 0.10),   # depth   — up to 10%
    (None, 0.04),  # snake-fill (effectively unused — see #5)
)
```

Cutoffs match the live-auction page's stage/montage split, so
engine pacing and audience pacing line up. Forces IPL pacing where
teams blow on early stars then settle into moderation. Hard floor
still leaves `min_bid × remaining_slots` so roster fill always
clears.

### 5. Snake-draft for roster fill (cfa32fe)

Top `auction_lot_limit` (default 500) lots go through bidding; the
rest fill rosters via snake order (1..56, 56..1, 1..56, ...) at
`min_bid`. No bidding theatre on the bottom 80% of the pool — they
were producing all-floor Vickrey clearings anyway. Drafted lots are
recorded with `winning_bid = min_bid`, `second_bid = NULL`,
`price = min_bid`, plus a single rank-1 bid row for the drafting
team in `auction_lot_bids`, so the live-auction page renders them
in the montage tier without a special-case.

### 6. Purse bump to ƒ200 crore (cfa32fe)

The old ƒ100-cr cap on the marquee tier was too tight against the
new max-bid range. ƒ200 cr gives the marquee soft-cap (ƒ100 cr at
lot 1) enough headroom that not every team clips to it.

### 7. Elite bonus on top of the quadratic (1ea08e5)

The first calibration produced a top sale of ƒ42 cr, with lots 1-25
in the ƒ29-42 cr band and lots 26-50 in the ƒ24-32 cr band — only a
~33% premium for the marquee tier, which the user pushed back on:
*"there should be a true difference between the top 25 players in
the pool versus everyone else."*

Stacked a second quadratic anchored at OVR 60 with a 10× scale on
top of the existing curve:

```python
base = (overall − 25)² × 1L  +  max(0, overall − 60)² × 10L
```

OVR 65 jumps from ƒ16 cr to ƒ18.5 cr; OVR 67 from ƒ17.6 cr to
ƒ22.5 cr; OVR 68 from ƒ18.5 cr to ƒ24.9 cr. Below 60 unchanged. The
top sale moved to ƒ54 cr and the marquee top-25 vs 26-50 gap opened
up to 33% (was 18%).

### 8. Post-clear sellback trade mechanic (1ea08e5)

After every auction lot clears, the engine checks every non-full
team's *noise-free* valuation. If anyone values the player at
≥ `1.05 × winning_bid` (the `TRADE_THRESHOLD` constant), the highest
such team buys the player off the original winner at the midpoint of
`(winning_bid, buyer_valuation)`, capped by buyer's remaining purse.
Cash flows seller← buyer; player moves to buyer's roster.

Mirrors the US-draft pattern where a team picks a player higher in
the order than where another team would've taken them, then trades
them in exchange for assets — except here the asset is auction
budget. Since auction budget doesn't roll over, the seller has
incentive to deploy the cash on later lots, leading to the
"overpay in a different round" shape the brief described.

New helper `_team_valuation_noisefree(player, team_id, profile)`
computes a deterministic valuation (same shape as `_team_bid` but
noise=1.0, no cap). Used only by the trade phase — the auction
itself still uses the noisy `_team_bid` for the original allocation,
preserving the original Vickrey audit trail.

### 9. Schema changes (1ea08e5)

Two new nullable columns on `auction_results`:
- `traded_to_team_id` — final roster owner (NULL = no trade)
- `trade_price` — guilders the buyer paid the seller

Idempotent ALTER TABLE migration so existing saves attach without
manual surgery. Original Vickrey columns (`winner_team_id`,
`winning_bid`, `second_bid`, `price`) are preserved as the auction
audit trail; the trade is a follow-up event on the same row.

### 10. `get_live_auction` + live page wiring (1ea08e5)

The feed now joins to the traded-to team and exposes
`traded_to_abbrev/_name`, `trade_price`, plus a derived
`final_owner_team_id` / `final_owner_abbrev` (traded_to if a trade
occurred, else original winner). Team-purse rollup nets trade flows:
seller's spent decreases by `trade_price`, buyer's increases.
Won-count goes to the final owner so the live page's purse table
reads as "what teams ended up with."

Live page (`auction_live.html`) renders a `⇄ Traded to <TEAM> for
ƒX` follow-up line under the original winner callout when a trade
fired. The running purse board's `applyBidsToPurse` mirrors the
server-side rollup so playback stays correct under trades.

### 11. Live page currency wiring (came in earlier in the session)

This wasn't strictly part of the shape work but landed in the same
session. The first live-auction commit had hardcoded `$` +
`toLocaleString` for every dynamic price, bypassing the
`o27v2/currency.py` module entirely. Replaced with a JS `fmtMoney()`
that produces the same `<span class="o27-money">` cells the
server-side `_money` filter emits, with all three labels
(ƒ / $ / €) baked in as data attrs. Mirrors just enough of the
Python module (`format_indian`, `format_crore`, the western M/B/K
formatter) in JS, pulling rates from `window.O27_RATES`.

The user spotted this with *"CAN THIE ACUTION USE THE NE CURRENCY I
MADE or not?"* — the kind of catch I should have made before
committing. Lesson noted in #1 below.

---

### 12. Cash-drain calibration pass (post-`1ea08e5`, in this commit)

The first sellback ship had a real problem: 2 of 56 teams ended up
with massive surplus cash (max ƒ426 cr) because they got into a
"buy-flip-buy-flip" cycle, accumulating sales faster than the auction
could drain their purse, and ultimately filling their roster cap
(47 slots) with cash still in pocket. Money forfeit = bad — the user
explicitly asked for this not to happen.

Closed the loop with four stacked changes:

- **`_team_pressure(purse, lot_order, ...)`** — multiplier on
  `aggression`. A team sitting on more cash than expected at the
  current lot bids harder on subsequent lots, capped at 2.5×. Closes
  the "no rollover ⇒ overpay later" half of the brief.
- **Variable-cost snake-draft fill** — snake picks now cost
  `max(min_bid, purse_remaining // open_slots)` instead of flat
  `min_bid`. A team with ƒ50 cr left and 30 open slots pays ƒ1.7 cr
  per snake pick rather than ƒ50 lakh, sopping up cash on floor-tier
  players.
- **Two hard caps on trades**: `TRADE_LOT_LIMIT = 50` (no trades
  past the marquee tier) and `TRADE_SALES_PER_TEAM = 1` (no team
  sells more than one player). Eliminates the runaway buy-flip
  cycle directly.
- **Valuation-based trade threshold** — replaced
  `winning_bid × 1.05` with `winner_val × 1.05`. The original
  formulation was comparing apples to oranges (buyer's noise-free
  val vs winner's noise-inflated bid), and stopped firing once the
  elite-bonus pulled bids further above noise-free vals. The new
  formulation says "trade fires when the wrong team won the lot" —
  buyer's noise-free val exceeds winner's noise-free val. Trade
  clears at midpoint of those two values; seller's reservation
  price is their own valuation, not the noisy winning_bid (which is
  sunk cost).

Trade count drops from 200 to ~7 per auction, but each remaining
trade is semantically meaningful (a noise-induced mis-allocation
corrected) instead of cash-arbitrage churn. All 56 teams now drain
to ƒ±0 of zero leftover; no surplus, no overspend.

---

## End-to-end smoke (rng_seed=42, 56-team tiered)

| Tranche               | N   | Avg     | Range            |
|-----------------------|-----|---------|------------------|
| Marquee top-25 (1-25) | 25  | ƒ39.5 cr | ƒ29.1 – 54.0 cr |
| Marquee 26-50         | 25  | ƒ29.5 cr | ƒ23.5 – 38.1 cr |
| Core (51-200)         | 150 | ƒ22.5 cr | ƒ17.3 – 31.6 cr |
| Depth (201-500)       | 300 | ƒ14.9 cr | ƒ10.8 – 21.5 cr |
| Snake-fill (501+)     | 1964 | ƒ50 lakh | flat           |

**Top sale:** Santiago Hargett, OVR 68 1B → NYM **ƒ54.0 cr**.

**Trades after cash-drain pass:** 7 of 500 auction lots (~1.4%).
Each one is a noise-induced mis-allocation correction (e.g.
Jurgen Van Leeuwen, OVR 67 P, MTG → flipped to NYM at ƒ47 cr).
Down from the unthrottled rate of 41% in the first sellback ship —
but the 7 that fire are the cases that genuinely should fire.

**Cash drain:** all 56 teams within ƒ±0 of starting purse
(pressure + variable-cost snake-draft fill drain any surplus).

**Personality readback:** top spenders cluster around `small_ball` /
`players_manager` / `mad_scientist` archetypes (positive star_bias,
high joker_aggression). Bottom spenders cluster around
`sabermetric_max` / `iron_manager` (high org_strength → disciplined,
negative star_bias). The asymmetry that produced ƒ-325 to +ƒ219 cr
spend variance in the unthrottled version is now closed — every
team spends ~ƒ200 cr exactly. Personality variance shows up in the
*roster shape* (which players each team picked, distribution
across tranches) rather than in net spend.

---

## What I'd do differently

**The off-by-10× bug on `BID_QUAD_SCALE`.** Set the constant to
`10 * 1_00_000` thinking I was writing 10 lakh = ƒ10 lakh, but the
constant feeds straight into `pivot² × scale` where the resulting
unit is *guilders*, not lakh-of-guilders. So OVR 63 came out at
ƒ144 cr base — already triple the cap. Top 50 lots all clipped to
the marquee soft-cap of ƒ100 cr; Vickrey collapsed to (cap, cap);
output looked identical to the bug it was meant to fix. Took an
instrumented per-team bid-debug pass before I caught it. The unit
test would have caught this in 5 lines:
```python
assert _bid_base(60) == 12_25_000        # ƒ12.25 lakh
assert _bid_base(67) == 1_762_500        # ƒ17.6 lakh — not crore
```
Skipped writing the test because "the curve is obvious," paid for
it twice. Future lesson: pin the shape with a test before piling
calibration changes on top.

**Dead-end on "make the cap so low bids can't reach it."** When the
first calibration produced cap-collapse at ƒ85 cr, my instinct was
to compress further — bump min_bid, shrink the cap, narrow noise.
The user redirected: *"why not raise the cap?"* That was the right
answer. The whole point of the calibration was to give Vickrey room
to run, not to constrain it. Should have noticed I was solving the
problem in the wrong direction; the per-lot cap had become a hammer
when what we needed was a runway.

**Currency-system bypass on the first live-auction commit.** Wrote
`'$' + Number(n).toLocaleString('en-US')` for every JS-rendered
price without checking the codebase pattern. The user caught it
the next message. Cost: an extra commit (`eba502c`) to retrofit the
`o27-money` cell pattern in JS. Prevention is trivial — `grep
o27-money` before writing any new money rendering — but I have to
actually do it.

**Should have surfaced the trade-rate target.** Shipped the sellback
mechanic at `TRADE_THRESHOLD = 0.05`, which produced 41% trade rate
on the smoke. That's almost half of all auction lots getting flipped
immediately — borderline noise. The user might have wanted 10-15%
(only the obvious mismatches). I had no signal either way and
shipped a default. An AskUserQuestion on threshold would have been
worth a 30-second interrupt.

**The "no rollover ⇒ overpay later" loop didn't actually close.**
The user's mental model was: seller gets cash → spends it on later
lots → overpays because they have to use it. What actually happens:
seller gets cash, but their `_team_bid` formula doesn't reference
remaining purse, so their max-bid on later lots is unchanged. The
cap might rise (since cap is `% × purse_remaining`), but a team
already below the cap just sits on the surplus until auction end,
where it's forfeit. The visible result: net-seller teams exit with
ƒ-100 to -325 cr "spend" (i.e., gained money), which is mechanically
correct but doesn't match the brief's "overpay" narrative. Fix
sketched in pointer #2 below — should have flagged this gap before
shipping.

**Calibration constants live in Python, not config.** `BID_QUAD_SCALE`,
`BID_ELITE_SCALE`, `TRANCHE_CAPS`, `TRADE_THRESHOLD` are all module-
level constants. If a different league wants different shape, they
have to monkey-patch instead of editing JSON. Should have moved these
to `config["auction"]` from the start; refactor cost is low and the
auction config is already a sub-dict.

---

## Pointers for follow-up work

1. **Tune `TRADE_THRESHOLD`.** 41% trade rate is high for what's
   meant to be "the engine corrects obvious mismatches." Try
   0.10 - 0.15 to filter to only the meaningful gaps. Per-lot
   measurement: count trades where buyer's noise-free valuation
   > seller's noise-free valuation by > 20% — those are the "true"
   corrections; below that ratio, the trade is mostly noise-laundering.

2. **Close the "overpay later" loop.** Add a purse-pressure factor
   to `aggression` so a team that's selling-and-accumulating
   actually deploys the cash:
   ```python
   expected_purse_at_lot_n = purse_init × (1 − lot_order / total_auction_lots)
   pressure = max(1.0, purse_remaining / expected_purse_at_lot_n)
   effective_aggression = profile["aggression"] × pressure
   ```
   A team sitting on surplus would bid 1.2-1.5× more aggressively on
   later lots, naturally turning the cash into player acquisitions.
   Matches the brief's intent more closely than the current "cash
   forfeit at end" outcome.

3. **Multi-hop trades.** Current is single-hop: lot clears, one
   trade fires, lot is settled. If team B trades to acquire from
   team A, and team C values higher than B, C can't immediately
   re-flip. Could iterate the trade phase until no improving trade
   exists — converges fast (most lots have ≤ 1 trade anyway), and
   would let the engine fully Pareto-optimise post-Vickrey.

4. **Move calibration constants to config.** `auction.bid_curve`
   sub-dict in `56teams_tiered.json` could carry
   `quad_pivot / quad_scale / elite_pivot / elite_scale /
   tranche_caps / trade_threshold`. Different league configs could
   then have different shapes (e.g. a "low-cap minor-league" config
   with no elite bonus and a tighter overall curve). Trivial
   refactor; constants become defaults if config doesn't override.

5. **Trade visualisation in the live page is muted.** Currently a
   small accent-colored line under the winner callout. For the
   IPL-TV feel, a trade should *interrupt* the lot reveal:
   "Player X → Team A for ƒ40 cr… ⇄ flipped immediately to Team B
   for ƒ45 cr." Could fork the stage-card animation to show the
   original winner reveal, brief pause, then a second reveal for
   the trade with a different visual treatment (rotate-in, swap
   pill, etc.).

6. **`/auction` static report doesn't show trades.** The static page
   still renders only `winner_team_id` / `winning_bid` / `second_bid`.
   Should add a "Final Owner" column and an optional "Traded for"
   sub-row when `traded_to_team_id IS NOT NULL`. The data is in the
   feed; just not surfaced.

7. **Auction-format toggle.** The user asked *"is Vickrey the only
   option?"* — answered in chat with first-price (different math,
   bid-shading), English (same math, different theatre), and hybrid
   formats. Could expose `auction.format` as a config knob with
   `"vickrey" | "first_price" | "english"`. First-price is the only
   one that actually changes the engine math; English would be
   purely a re-skin of the live-page animation.

8. **Personality archetypes for auction-only behaviour.** Currently
   we derive auction profile from in-game manager axes. A strong
   sabermetric in-game manager might still be a star-chasing
   auction-day chaos agent. Could add explicit auction-day axes to
   `Archetype` (e.g. `auction_aggression`, `auction_marquee_focus`,
   `auction_patience`) so the same archetype can have heterogeneous
   in-game vs auction-day behaviour. Overlaps with the existing
   archetype catalogue but adds a meaningful new dimension.

9. **Snake-draft order is naive.** Currently `team_id` order, with
   round 2 reversed. Should be reverse-standings (worst team picks
   first) like NFL/NBA, since this is offseason fill. Falls back to
   `team_id` when no standings exist (first season). Trivial change
   in `_advance` — just sort `team_ids` by last-season W-L instead
   of by id.

10. **Calibration sweep tooling.** Right now I tweak a constant,
    re-run the seed, eyeball the output. A 30-line script that runs
    the auction across 5-10 seeds and reports tranche stats (avg /
    range / Vickrey gap / trade rate) would let calibration changes
    be data-driven instead of vibe-driven. Especially valuable when
    moving constants to config.
