# Feasibility: Unified Gambling Platform + Tennis Betting

**Date:** 2026-06-12  
**Status:** Research/design only — nothing built yet  
**Context:** User asked whether a unified gambling platform spanning all three sims (baseball, viperball, tennis) was feasible, and whether tennis betting specifically could be added. This doc captures what was found and the recommended path forward.

---

## Current state of each sim's gambling infrastructure

### hybrid-baseball — mature
Full CapSpace system in `o27v2/web/fantasy/`:
- Play-money wallet with persona tiers (college/father/degen/PE), career records, restart bankrolls by status tier
- Sportsbook: moneyline + run totals, Pythagorean win% odds with vigorish — `sportsbook.py`
- DFS: GPP/Double Up/H2H/Freeroll contests, daily slate, full scoring + settlement — `contests.py`
- Season-long: Sluggers (HR game), Pilots (pitching game), Roto categories, Best Ball, Hit-streak survivor
- Separate DB file (`o27v2.db-capspace.db`) attached as `sim` in fantasy queries
- Settlement runs post-game, triggered from `blueprint.py`

### viperball — solid
DraftyQueenz in `engine/draftyqueenz.py`:
- 10,000 DQ$ starting bankroll; min 250, max 25,000 per bet
- Moneyline, spread, over/under, props (chaos O/U, kick-pass O/U), parlays
- Weekly fantasy contest: 5-slot roster, 15k salary cap, 10-competitor synthetic field, 2,500 DQ$ entry
- Booster donations: spend DQ$ for dynasty effects (recruiting, NIL, facilities, etc.) — unique mechanic
- Persisted as JSON blob in `saves` table (`save_type='dq_manager'`)
- Tightly coupled to session/season lifecycle in `api/main.py`

### tennis-team-manager — nothing
Zero gambling infrastructure. No odds, no bets, no wallet, no settlement hooks. Pure sim.

---

## Unified wallet: recommended architecture

**Decision (user-confirmed):** Wallet lives in vroomtv (the 4th repo) as a shared service. The three sims are satellites — they don't own money, they just report results.

### Why this is the right call
- No cross-repo coupling between sims (baseball doesn't import viperball code, etc.)
- Single source of truth for balance, career records, transaction history
- Adding a 4th sport later requires only a new adapter, not touching existing sims
- vroomtv already reads each sim's DB directly — settlement can use the same read path

### Wallet DB (new, lives in vroomtv)
Modeled on baseball's CapSpace schema (`o27v2/web/fantasy/fdb.py`):
- `wallet` — single-row balance in a shared currency (rename guilders → something sport-agnostic, or keep)
- `wallet_records` — career stats: peak, total wagered, total won, biggest win
- `bets` — unified bet ledger: `sport` (baseball/viperball/tennis), `game_ref` (sport-specific game id), `market`, `side`, `odds`, `stake`, `status`, `payout`
- `transactions` — full history log

### Settlement coupling (the key design question)
Each sim needs to signal "game X is done, here are the results" so vroomtv can settle open bets. Two options:

**Option A — polling (simplest):** vroomtv's settlement worker periodically reads each sim's DB, checks for newly completed games against open bets, and settles. No changes to any sim. Works because vroomtv already has direct DB read access.

**Option B — webhooks:** Each sim POSTs a result notification to vroomtv after game completion. Cleaner but requires adding outbound HTTP calls to three codebases.

**Recommendation: Option A.** The sims are single-player tools, not high-frequency systems. Polling on page load (settle any pending bets when the user hits vroomtv) is sufficient and requires zero sim changes.

---

## Tennis betting: what needs to be built

This is the largest gap. Everything below is net-new for tennis.

### 1. Odds generation
Baseball uses Pythagorean win% from season W/L records (`sportsbook.py` lines ~40-80). Tennis equivalent:
- Player/team ratings already exist — `players.rating` (modified-UTR) in `tennis.db`
- For GTT duals: compare franchise average ratings → win probability → moneyline with vig
- For NCAA duals: compare team Power Index (computed from `duals` results in `app/str_rating.py`)
- Simple logistic function: `win_prob = 1 / (1 + exp(-(r_home - r_away) * k))` where k is a calibration constant
- American odds from win probability: standard formula used in baseball's `sportsbook.py`

### 2. Markets to offer
- **Moneyline** (dual winner): simplest, maps directly to win probability
- **Line score total** (total lines won by both teams, O/U): analogous to baseball run total
- **Individual match props** (e.g. "will line 1 singles go 3 sets"): more complex, skip for v1

### 3. Bet placement
New route in vroomtv: `POST /bet/tennis` — takes `dual_id`, `source` (gtt/ncaa), `market`, `side`, `stake`. Validates against wallet balance, writes to `bets` table.

### 4. Settlement
After a dual completes (status = 'complete' in `gtt_duals` or `duals`), the polling settler:
- Reads `home_points` / `away_points` to determine winner
- Grades open bets against the result
- Credits/debits wallet accordingly

### 5. Line storage
New table in vroomtv wallet DB: `tennis_lines` — `dual_id`, `source`, `ml_home`, `ml_away`, `total`, `over_odds`, `under_odds`, `created_at`. Pre-generate lines when the dual is scheduled (status = 'scheduled' or week not yet played).

---

## Viperball: what needs to change

Very little. DraftyQueenz already works well as a standalone system within viperball. For the unified wallet:
- Keep DraftyQueenz as-is inside viperball (it has the booster donation mechanic which is viperball-specific and makes no sense cross-sport)
- Add a thin read path in vroomtv: read the `dq_manager` blob from viperball's `saves` table, surface DQ$ balance and recent bets on the vroomtv dashboard as a "viperball account" sidebar
- OR: migrate DQ$ into the unified wallet and retire the separate viperball bankroll — more work, user decision

**Recommended for v1:** leave viperball's DraftyQueenz standalone, just surface it read-only in vroomtv alongside the unified wallet. Migration is a v2 decision.

## Baseball: what needs to change

Also very little. CapSpace is mature. Same two options as viperball:
- Surface CapSpace balance read-only in vroomtv as a "baseball account" panel
- OR migrate the CapSpace wallet into vroomtv's unified wallet

**Recommended for v1:** read-only surfacing. The CapSpace system is too feature-rich (personas, tiers, DFS, season-long games) to migrate without significant work.

---

## Recommended sequencing

1. **Build vroomtv unified wallet DB** — schema only, no UI yet. One balance, one bet ledger, one transaction log.
2. **Build tennis odds generation** — logistic model on UTR ratings, pre-generate lines for scheduled GTT/NCAA duals.
3. **Build tennis bet placement + settlement** — moneyline only, Option A polling settler.
4. **Wire into vroomtv UI** — bet slip on scores page, open bets panel, wallet balance in nav.
5. **Surface viperball DQ$ and baseball CapSpace read-only** — unified dashboard shows all three accounts.
6. **v2 decision:** migrate viperball/baseball wallets into unified wallet, or keep federated.

---

## What was not validated

- Tennis `gtt_duals.winner` column: assumed to hold a franchise `id`. If it holds 0/1 (home/away flag) the settlement logic differs — verify before building.
- Tennis Power Index computation lives in `app/str_rating.py` — not read in detail; verify it's queryable without running the full app.
- Viperball DraftyQueenz booster mechanic: user may want to keep this viperball-only regardless of wallet unification.
- No estimate on how many bets per day the user actually wants to place — informs whether polling-on-page-load is sufficient or a background job is needed.
