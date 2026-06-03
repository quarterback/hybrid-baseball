# AAR — CapSpace bankroll & pressure (one wallet, buy-ins everywhere, records)

## Context

Play-testing raised the real question: *"does this work like a real one — with
a stake, fees, pressure? I want to be playing against myself, my wallet, and
pressure."* At that point only DFS (fees → wallet) and Sportsbook (its own
separate "units" bankroll) had stakes; the other four games were free to enter.
This pass made the **wallet the spine of the whole app** and added the
you-vs-yourself layer.

## What shipped

- **One shared wallet** (`wallet.py`). Extracted the per-save play-money
  balance (guilders, seeded at ƒ50 lakh) into its own module with
  `debit/credit/balance` and career records baked in. DFS routes through it;
  **Sportsbook was unified into it** — it dropped its separate "units" bankroll,
  bets in guilders (money-formatted stake chips), and settles winnings into the
  same wallet. One bankroll, every game.

- **Buy-ins on every game** (`buyins.py` — a shared ledger, one row per
  game+key, idempotent):
  - **Daily games** (Sluggers, Pilots): ƒ1,000 buy-in charged once per slate on
    your first pick; cash out vs the field when the slate finals — 5x near the
    ceiling, 2x beating the field average, money back above 60%, else gone.
  - **Season leagues** (Categories, Best Ball): ƒ5,000 buy-in at draft (once per
    league/save), paid at the **season's end** (when the schedule is exhausted)
    by final rank — 10x to win, 3x top-10%, money back in the top half.
  - DFS and Sportsbook already settle to the wallet, so all six games now risk
    the same money.

- **Career records — you vs yourself** (`cap_records`, surfaced on the hub).
  Peak bankroll, total wagered/won, net P&L, biggest single win, entries,
  cashes (and best Go-Streaking streak), updated as the wallet moves. A "Your
  career" card on the hub makes the bests something to chase.

- Central `_settle_all()` in the blueprint settles every game then reports the
  balance, so winnings land in the wallet no matter which screen you're on;
  `/api/wallet` returns `{balance, records}`.

## Validation

- **Unified wallet:** Sportsbook bankroll == wallet; a ƒ5,000 bet debits the
  shared wallet; loss reconciles to net.
- **DFS cycle (earlier):** enter ƒ1,000 → debit → slate final → +winnings →
  reconciles to `start − fee + payout`.
- **Daily buy-ins:** charged exactly once across three picks; settles on a final
  slate; wallet + records reconcile (lost the ƒ1,000 on a sub-threshold night).
- **Season buy-ins:** ƒ5,000 charged at draft, idempotent on re-draft, two
  leagues → ƒ10,000 staked / 2 entries; rank-payout curve correct
  (10x/3x/1x/0); mid-season settle is a no-op (only fires at season's end).
- All game endpoints + `/api/wallet` serve 200.

## Notes / not done

- **Bug fixed mid-build:** `buyins` called `db.get_conn()` twice (execute on one
  connection, `.commit()` on another — `get_conn()` returns a fresh connection
  each call), which left the write uncommitted and locked the DB. Collapsed to a
  single connection.
- **Season payout realism:** season leagues only realize their payout when the
  save's schedule is fully played. Mid-season the buy-in is a sunk cost you're
  chasing — the pressure is real, the cash-out is deferred. A mid-season
  cash-out-for-equity option could be added later.
- `pytest` absent in the sandbox; validated via the Flask `test_client` and
  direct module calls.

## Follow-up pass — personas, lifetime status, soft landing

A research subagent surveyed loyalty-tier gamification (Delta Million-Miler,
Caesars Seven Stars, DraftKings Dynasty, AmEx Centurion, Duolingo/Robinhood,
goal-gradient/endowed-progress psychology). Headline finding: the fun is a
**monotonic lifetime counter that only goes up and grants permanent status** —
Delta's two-track model (volatile MQDs vs. sacred lifetime miles). Built it:

- **Personas (pick-your-player onboarding).** Realistic small starting
  bankrolls replace the flat ƒ50-lakh seed: Broke college student ($500),
  Responsible father of two ($2,500), Swears he doesn't have a problem
  ($5,000), PE guy chasing the rush ($25,000). The wallet isn't seeded until
  you choose; "New run" wipes all play state and re-picks.
- **Lifetime-earnings status tiers.** `total_won` (monotonic) drives a
  permanent tier across $5K/$10K/$25K/$50K/$100K/$250K/$500K/$1M — Buy-In →
  Penny Pincher → Table Regular → Card Shark → High Roller → The Whale → Pit
  Boss → Big Kahuna → The Legend (research's High-Roller naming set; top tier
  left a little mysterious, AmEx-style). Tier never drops on a bust.
- **Soft landing (the research's best steal).** Busting isn't game-over: "Back
  from the felt" tops you to a tier-scaled floor ($250 base, +$250/tier)
  **without** crediting lifetime, so status is untouched and the message says
  so. Converts the worst moment into a retention beat.
- **Hub status gauge:** tier name, lifetime won (the hero number), a progress
  bar with "$X to next tier" (goal-gradient), plus the career tiles.

Verified: onboarding gate, tier crossing, status persisting through a $0 bust,
restart floor scaling with tier, and the "still got chips" rejection.

Deferred (pieces now in place): the framed season challenge (target / days-left
/ no-reload bust), tier *unlocks* beyond the soft-landing scale (cosmetic
badges, higher buy-in brackets, the mysterious $1M reveal), and streaks.
