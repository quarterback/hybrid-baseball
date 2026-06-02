# After-Action Report — CapSpace: the `/fantasy` Daily-Slate (DFS) layer

**Date completed:** 2026-06-02
**Branch:** `claude/practical-brown-aXDWt`

---

## What was asked for

A fantasy layer over O27 for immersion. The user had already mocked up the UI
in Claude Design ("CapSpace" — a standalone `/fantasy` app with its own
warm/playful Web 2.0 design system, deliberately *not* the main app's "Twilight
Diamond" Swiss look) and handed off the bundle for implementation. The brief
landed on: **start with DFS**, build it as its own app like `/almanac`, and
wire it to real save data.

Two scoping decisions were taken up front:

- **Approach:** serve the React prototype as-is (a `/fantasy` blueprint that
  ships the design's HTML/CSS/JS verbatim) and feed it real data — *not* a
  Jinja port or a bundled React build. Keeps it pixel-perfect and keeps the
  builder's client-side interactivity, with no new build toolchain.
- **v1 data scope:** **real player pool + real slate**; contests, leaderboard,
  and live scoring stay as the designed placeholders (they need contest /
  opponent infrastructure that doesn't exist yet).

## What shipped

A new blueprint at **`o27v2/web/fantasy/`** (mirrors `almanac_bp` /
`gazette_bp`), registered in `o27v2/web/app.py`:

- **`blueprint.py`** — `capspace_bp`, `url_prefix="/fantasy"`. Serves
  `GET /fantasy/` (the SPA shell) and `GET /fantasy/api/slate` (the same JSON
  blob, for refresh/debug). Never 500s: a slate-build error degrades to mock.
- **`data.py`** — the read-layer. Turns the active save into the
  `window.SLATE` shapes the front-end expects. Read-only; no engine changes,
  no new tables.
- **`templates/capspace.html`** — the entry shell. Injects
  `window.O27_RATES` (from `currency.rates_for_js()`, exactly the `base.html`
  pattern) and `window.__CAPSPACE_DATA__` (tonight's real slate + pool), then
  loads the design's scripts. The root `<App>` was lifted into its own
  `capspace-app.jsx` because its JSX object-literal props (`value={{…}}`)
  collide with Jinja's `{{ }}`.
- **`static/`** — the design assets, renamed to the canonical `capspace.*`
  names (`capspace.css` + `capspace-{data,ui,screens,builder,app}.jsx` +
  favicons). The JS namespace stays `window.SLATE` (cosmetic). `capspace-data.jsx`
  was edited to read the injected real data with a fall-back to its bundled
  mock arrays, and to source currency rates from `window.O27_RATES`.

### Data mapping (what's real)

| CapSpace field | Source |
| --- | --- |
| slate games | `games` for the live slate date (next date with an unplayed game, else most recent) |
| player pool | active, non-joker players on the slate's teams |
| salary | `valuation.estimate_player_value`, **recalibrated** into a DFS tier |
| ratings (20–80) | the `players` block — hitters `contact/power/eye/stay*/speed/field`, pilots `command/stuff/decay/control/late` |
| proj | avg DFS fantasy points over recent games, ratings-based fallback |
| game log / form | `game_batter_stats` / `game_pitcher_stats` joined to `games` |
| DFS scoring | the `_batter_game_score` weights + O27 stay bonuses |
| currency | `o27v2/currency.py` (guilder/usd/eur/zora, shared `o27.currencyDisplay`) |

### The salary calibration (the one real gotcha)

`estimate_player_value` returns **league-economy** figures (ƒ100M–ƒ1.8B) — 100–
1000× the design's **ƒ1 crore** daily cap, so *no* lineup would be buildable.
Fix: rank players by that talent signal *within* the pilot and hitter groups
and map each into a band (pilots ƒ8–18L, hitters ƒ5–15L). Salary then tracks
talent while the projection tracks recent form, so the pts-per-ƒ **value** stat
stays meaningful. Verified: cheapest full lineup ≈ ƒ44L (fits), priciest ≈
ƒ123L (busts the cap — real trade-offs).

### Pool trim

A 15-game opening-day slate fields the entire league (1,184 players, 512
pilots). Trimmed to a realistic per-position DFS depth (top-N by projection:
40 pilots, 48 OF, 24 each elsewhere → ~208 players, ~57 KB injected JSON).

## O27-specific notes

- **Nickelfielder (NF)** — O27's 10th fielder — has no dedicated CapSpace
  slot, so it collapses into `OF` (keeps the player draftable + STAY-eligible).
- **Jokers (J)** are tactical plate appearances, not a lineup slot, so they're
  filtered out of the DFS pool. They have a natural home in the *Joker Draft*
  format (already a card in the hub).
- The **STAY flex** slot and the stay/RAD-flavored game logs lean into the
  O27-native stats the design surfaces.

## Validation

- `data.py` logic unit-checked in isolation (scoring, position map, rating
  clamp, time format, salary bands, lineup buildability).
- Full route tested via Flask test client against a seeded save
  (`initdb` + `sim 30`): `GET /fantasy/` → 200 with real data injected; all 9
  referenced static assets → 200; `GET /fantasy/api/slate` → 208 players.
- Imported the **real** `o27v2/web/app.py` — blueprint registers, `/fantasy`
  renders 200 through the full app (no import cycle).
- No-games save → page still 200, injects `null`, JS falls back to mock.

## What was *not* done (deliberately deferred)

- **Contests, leaderboard, live scoring** remain the design's placeholders.
  Making them real needs `dfs_contests` + `dfs_entries` tables, a
  `POST /fantasy/enter`, and the solo benchmark (a computed "par"/field, or
  bot GMs reusing the auction AI). This is the natural next PR.
- Pilot pool surfaces all rostered arms by talent, not just projected
  starters — fine for v1, a candidate refinement.
- The seven non-DFS formats (2C/Stay, Walk-Back, Pilot Room, Skipper, Beat
  the Voyage, Hot Hand, Joker Draft) are hub cards with teasers only; each is
  a scoring preset over existing columns when built.
