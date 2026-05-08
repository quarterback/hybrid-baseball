# After-Action Report — Live Auction Replay (top-50 stage + montage) and Currency Wiring

**Date completed:** 2026-05-08
**Branch:** `claude/new-league-structure-tqk8H`
**Commits (in order):**
- `3855fc6` — Live auction replay: top-50 stage with animated bidding + montage
- `eba502c` — Live auction: render prices through the guilder currency system

---

## What was asked for

The user asked for an IPL-TV-style replay of the off-season auction:
the top 50 lots get a full "stage" treatment (player banner, animated
bidding, dramatic price reveal), and the remaining ~2400 lots roll
past in a fast-forward montage. Three or four seconds of pacing per
stage lot at 1×, with the option to speed up (2× / 5×), pause,
restart, or skip to the end. A running top-spenders board on the
side so the user can watch a team's purse drain in real time.

Mid-build, the user noticed the prices on the new page were rendering
as `$504` instead of `ƒ504` — i.e. the live page was bypassing the
guilder currency system shipped in PR #33 / commit `b35b611`. They
asked, in caps: *"CAN THIE ACUTION USE THE NE CURRENCY I MADE or not?"*
That triggered the second commit, which wires the live auction into
the existing `o27-money` cell + `O27_RATES` toggle plumbing.

Concrete answers / decisions during the build:

- **Stage tier size:** top **50** lots by player composite. Tuned for
  IPL-TV pacing — 50 × 3.5s ≈ 3 minutes of stage, then ~30 seconds
  of montage.
- **Bid sheet depth:** persist top **8** bids per lot (winner + 7
  runners-up). Caps the new table at ~13k rows for a 2500-lot
  auction; the animation never shows more than ~6 anyway.
- **Pacing:** 3.5s per stage lot at 1×, 220ms between bid reveals,
  ~12 lots/sec in the montage at 1×. Speed selector multiplies all
  three.
- **Currency:** mirror the server's `_money` Jinja filter in JS so
  the `<span class="o27-money">` cells the live page injects
  participate in the existing ƒ → $ → € toggle.

---

## What shipped

### Schema additions (`o27v2/auction.py`)

Two changes to make the auction replayable lot-by-lot, plus a new
table for the per-lot bid sheet:

- **`auction_results.lot_order`** (INTEGER) — 1-based sequence at the
  auction. Lets the live UI render lots in the exact order they
  cleared without re-sorting against player composite + jitter.
- **`auction_results.player_overall`** (INTEGER) — snapshot of the
  composite at auction time. The live page renders 1–5 stars from
  this without re-joining player rows or re-deriving the score
  (which would drift if a player got an in-season skill update).
- **`auction_lot_bids`** — new table: `(season, lot_order, team_id,
  bid, rank)`. Capped at top 8 per lot. Rank 1 is the winner; ranks
  2..8 are the runners-up by amount descending.

`init_auction_schema()` adds idempotent `ALTER TABLE` migrations for
the two new columns so older saves attach without manual surgery.

### `apply_auction` updates

The auction loop now:
1. Tracks `lot_order` (1-indexed) as it iterates the pool.
2. Sorts each lot's bids descending and persists the top 8 into
   `auction_lot_bids` — done even on **unsold** lots, so the live UI
   renders an explicit "no bids" stage card without a special-case.
3. Records `lot_order` and `player_overall` on every result row.
4. Wipes `auction_lot_bids` for the season at the start of each rerun
   alongside the existing `auction_keepers` / `auction_results` wipes.

### Read-side feed (`o27v2/auction.py::get_live_auction`)

A single function that produces the JSON payload the live page
consumes — not just the lots, but enough denormalized context that
the page renders without any further DB calls:

```
{
  "season":       int,
  "stage_lots":   [...top 50 lots, each with bids[] inline...],
  "montage_lots": [...lots 51..end...],
  "stage_count":  50,
  "team_purses":  [{abbrev, name, spent, won}, ...],
  "summary":      {n_lots, n_sold, n_unsold},
}
```

Each lot in either tier has the same shape: `lot_order`,
`player_name`, `position`, `is_pitcher`, `overall`, `stars` (1–5
mapped from overall via `_player_stars`), `winner_abbrev`,
`winner_name`, `winning_bid`, `second_bid`, `price`, `result` (sold
/ unsold), and `bids: [{abbrev, name, bid, rank}, ...]`. The Vickrey
clearing price is reconstructed as `min(winning_bid, max(min_bid_floor,
second_bid + 1))` — see "what I'd do differently" below for why this
should become a stored column.

The team-purse rollup is computed by replaying the shaped lots
once. That keeps the function pure-from-storage and means the page
doesn't have to maintain a running total via DB updates.

### `/auction/live` page (`o27v2/web/templates/auction_live.html`)

A Bootstrap two-column layout with the stage card on the left and
the running purse board on the right. The full feed is embedded as
a `<script type="application/json">` blob so the client-side IIFE
runs with zero further round-trips.

**Stage card animation:**
1. Insert all 8 bid rows into the DOM as hidden `<li>` elements with
   the runner-up at the bottom and the winner at the top.
2. Reveal them bottom-up at 220ms / `speed` per tick — bids tick
   higher and higher as the camera climbs the list.
3. When the last row reveals, the **final price** flashes in
   accent-color and a "Won by `<TEAM>`. Pushed by 2nd bid `<X>`"
   line appears below — the Vickrey runner-up callout that gives
   the price a story.
4. Linger on the card until the per-lot budget (3.5s / speed) is
   spent, then advance.

**Montage:**
- After the 50th stage lot, the stage card swaps for a stream view.
- Lots flow into the stream in batches of `4 × speed`, one batch
  every 80ms. DOM trimmed to the last 60 entries to avoid balloon.
- Top-spenders board updates per-batch.

**End screen:** top 5 sales of the day in a table (player,
winner, price, 2nd-bid), plus a link back to the static auction
report.

**Controls:** Play / Pause, Restart, Skip-to-End, and 1× / 2× / 5×
speed buttons. Skip-to-End replays the entire shaped log in memory
to bring the purse board to its final state, then jumps to the
end-screen render — no half-state on the board.

### Currency wiring (`auction_live.html`)

The first commit silently bypassed the guilder system. The second
fixes it by mirroring just enough of `o27v2/currency.py` in JS:

- `formatIndian(n)` — last 3 digits, then pairs from the right.
- `formatCrore(n)` — spell in lakh / crore with the same one-decimal
  rule the Python module uses.
- `formatWestern(amt, sym)` — compact `M/B/K` formatter for USD/EUR.
- `labelsForGuilders(g)` — produces all three label strings in one go.

`fmtMoney(g)` then produces the same `<span class="o27-money">` HTML
the server-side `_money` filter emits in `o27v2/web/app.py`: data-g,
all three `data-label-*` attrs, and the click pill. The existing
toggle script in `base.html` delegates pill clicks across the document
and re-runs `applyAll` on every cycle, so dynamically-injected cells
participate without further wiring.

Initial label respects the user's saved currency mode
(`localStorage['o27.currencyDisplay']`), so a USD-mode user lands on
USD labels in the stage card without flickering through ƒ first.

Rates come from `window.O27_RATES` (already injected by `base.html`
from `currency.rates_for_js()`) — the JS stays in sync with future
basket / anchor changes; the Python module remains source-of-truth.

### Routes (`o27v2/web/app.py`)

- `GET /auction/live` — page render. Falls back to a friendly
  "no auction recorded yet" empty state with a link to `/auction`.
- `GET /api/auction/live` — JSON feed (the same payload the page
  embeds). 404s when no auction has been run.

### Static-page entry point (`auction.html`)

A new `▶ Watch the auction` button next to the Run/Re-run button,
visible only when an auction record already exists.

---

## End-to-end smoke test

Seeded a 56-team tiered config (`56teams_tiered`) at `rng_seed=42`,
ran the auction, and verified the persistence + read paths:

- 168 keepers retained, 1736 sold, 728 unsold.
- 2464 lots got a `lot_order`; 13104 bid rows persisted; max rank 8 ✓.
- Live feed split: 50 stage / 2414 montage / 2464 total ✓.
- Stage star distribution: 2× 5★ + 48× 4★ — the top of the talent
  pool, as `_player_stars`'s thresholds intend.
- Top stage lot: Santiago Hargett, 5★ 1B, MON $504 with CIN's $503
  runner-up driving the price up. (Read "what I'd do differently"
  for why those magnitudes still look like dollars.)
- `/auction/live` renders, `/api/auction/live` returns the feed,
  `/auction` links across to the live page when an auction exists.

---

## What I'd do differently

**Should have raised the auction-magnitude problem before shipping.**
The numbers on screen are mathematically correct (ƒ100 = $1, so a
ƒ504 top sale is $5.04 USD by the headline anchor) but the
worldbuilding magnitudes belong to the pre-currency-overhaul "abstract
dollars" era. A 5★ player going for ƒ504 reads as wrong even though
the math is right. I caught it after the fact and called it out in
the chat, but I should have raised it before commit `3855fc6` shipped
— probably as an `AskUserQuestion`. Lesson: when wiring a new screen
into an existing system, sanity-check the headline numbers in the new
display unit, not just in the units the calling code happens to use.

**Should have spotted the currency-system bypass during the first
commit.** I wrote `'$' + Number(n).toLocaleString('en-US')` for every
price string in the live page without stopping to ask whether the rest
of the app does it that way. The user caught it on the next message
("CAN THIE ACUTION USE THE NE CURRENCY I MADE"). The fix in `eba502c`
is small and clean, but the asymmetry between "static templates use
the `money` filter" vs "this dynamically-injected JS doesn't" should
have been a code-review smell I caught myself. Prevention: any time a
new template renders money in JS, grep for `o27-money` first to find
the existing pattern.

**Vickrey-price reconstruction is brittle.** `get_live_auction`
recomputes `price = min(winning, max(10, second + 1))` to derive
the realised clearing price, with `10` baked in as the historical
`min_bid` floor. After the upcoming auction-magnitude rescale that
constant becomes wrong, and even if I update it the formula is
duplicating logic from `apply_auction`. The right fix is to add a
`price` column to `auction_results`, populate it during the auction
loop, and read it directly. Cheap migration, removes the magic
number, makes the read path trivially correct under future config
changes. Filed below.

**Skip-to-End walks the whole log to rebuild the purse board.**
That's fine for 2500 lots (instant), but the function recomputes the
purse rollup that `get_live_auction` already produced server-side. A
second pre-baked field on the feed (`final_team_purses`) would let
Skip-to-End just copy the array. Minor.

**The bid-reveal animation always shows 8 rows even when the actual
auction had 12 bidders.** The capped sheet is fine for storage, but
the stage card calls out the runner-up's amount as if rank-2 were the
true runner-up. For 99% of lots (where bid rank-2 in the table is
also the actual rank-2 in the auction) this is true, but for a
contested mega-star where ranks 9–12 also bid above min_bid, the
"second_bid" column in `auction_results` is the *actual* second-best
amount, and the bid sheet's rank-2 row matches that. So the
narrative is correct — but the **number of bidders** the user sees
is capped at 8, not the true count. Worth surfacing the true bidder
count in the stage card ("8 of 14 bidders shown") so the cap is
explicit, not implied.

---

## Pointers for follow-up work

1. **Rescale the auction to guilder-era magnitudes.** ✅ Shipped
   same-session in a follow-up commit (`team_purse` 1000 → 100 cr,
   `min_bid` 10 → 50 lakh, `BID_BASE_PER_OVERALL` introduced as
   `50 lakh per overall point`). Top stage lot in the seeded
   smoke-test now clears at ƒ50.4 cr (Santiago Hargett, MON) instead
   of $504, with the same Vickrey runner-up shape — headline-ready
   as "ƒ50-crore signing." Same commit also persists a `price`
   column on `auction_results` so the live page reads the realised
   clearing price directly instead of reconstructing it (folding in
   pointer #2 from this list), and switches the static `/auction`
   template's hardcoded `${{ }}` to `{{ … | money }}` (folding in
   pointer #3).

   One observation worth flagging: prices at the top of the auction
   are compressed (top 10 sales fall in ƒ54-57 cr; mid-tier 60-OVR
   sells at ƒ42-51 cr). That's a property of Vickrey collapse + the
   uniform per-team bid noise — when 8+ teams all bid near their
   max, the runner-up is right behind the winner and the clearing
   price tracks the cap. The existing auction had the same shape
   on the dollar scale; the rescale just made it visible. If the
   league wants a more exponential price curve at the top, the
   knob is `BID_BASE_PER_OVERALL` (steepen via a non-linear lookup,
   e.g. `overall_to_base = {68+: 1 cr, 60-67: 50 lakh, ...}`) or
   widen the noise band to spread the bidders out. Out of scope for
   this PR.

2. **Persist `price` on `auction_results`.** ✅ Shipped same-session
   alongside the rescale. `get_live_auction` now reads the persisted
   column with a Vickrey-shaped fallback for older rows that
   pre-date the column.

3. **Static `/auction` page currency fix.** ✅ Shipped same-session
   alongside the rescale. Both the config summary and the per-row
   winning/second-bid columns now go through `{{ … | money }}`.

4. **True-bidder-count surfacing.** The stage card should show
   "Bidders (8 of 14)" so the cap is explicit. Requires adding a
   `n_bidders` column to `auction_results` (or counting rows in
   `auction_lot_bids` server-side and capping the display from
   the read function).

5. **End-screen narrative reels.** The data is all there:
   "biggest splurges" (price > 3× the player's median band),
   "steals" (price = `min_bid` floor on a 4★+ player),
   "biggest underbids" (winner's `bid - second_bid` gap > N),
   "team that overpaid most" (sum of `bid - price` gaps).
   Two or three of these as a "Highlights" section after Top 5
   Sales would lift the end screen from "summary table" to
   "newspaper recap."

6. **Auction-coverage page integration.** The newspaper /
   transactions surfaces have nothing about the auction. A
   per-team "auction-day buys" panel on `/team/<id>` and a
   league-wide "auction recap" card on the front page would carry
   the worldbuilding into the rest of the app instead of sequestering
   it on `/auction/live`.

7. **Replay any past season, not just the latest.** `get_live_auction`
   takes a `season` arg but the route doesn't expose it. A
   `/auction/live?season=N` query param + a season-picker dropdown
   in the page header would let the user re-watch any prior auction
   from `auction_results`. Trivial route change once we're sure the
   schema migrations have backfilled `lot_order` for older saves —
   for now they'd just see the empty-state.
