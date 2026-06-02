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
| salary | small **dollar** figure from a ratings overall, scaled to the ~$1,000 cap (stored as guilders) |
| ratings (20–80) | the `players` block — hitters `contact/power/eye/stay*/speed/field`, pilots `command/stuff/decay/control/late` |
| proj | avg DFS fantasy points over recent games, ratings-based fallback |
| game log / form | `game_batter_stats` / `game_pitcher_stats` joined to `games` |
| DFS scoring | the `_batter_game_score` weights + O27 stay bonuses |
| currency | `o27v2/currency.py` (USD default; guilder/eur/zora via the switcher; shared `o27.currencyDisplay`) |

### The salary calibration (the one real gotcha)

DFS salaries are small **dollar** figures (USD is CapSpace's default display —
the exotic currencies stay available via the switcher but aren't the canonical
read). We deliberately do **not** surface the game's economy valuations
(season/auction scale). Instead salary is computed fresh from a ratings overall
and mapped into a per-group dollar band — pilots **$80–260**, hitters
**$40–190** — sized so the priciest pilot is only a few hundred dollars and the
pool fits a **$1,000** lineup cap. Trim happens *before* pricing so each
position spans the full band (punts → studs). Salary tracks talent while
projection tracks recent form, keeping the pts-per-$ **value** stat meaningful.
Verified: cheapest lineup ≈ $585 (fits), priciest ≈ $1,550 (busts the cap —
real trade-offs). Stored internally as guilders (ƒ100 = $1) so the switcher
converts cleanly.

### Discovery / entry points

The main O27 app reaches CapSpace two ways (it's otherwise the Swiss "Twilight
Diamond" app and wouldn't surface a separate product): a permanent glossy amber
**nav pill** ("🚀 CapSpace", its own top-level link, not a dropdown item) and a
dismissible site-wide **house "banner ad"** below the topbar — glossy amber,
the astronaut mascot (reusing the CapSpace favicon), a faux "Sponsored ·
CapSpace BETA" kicker. The banner reads as an in-world ad against the Swiss
chrome; dismissal persists in `localStorage` and never blocks the nav pill.

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

## Contests / leaderboard / live (now real — `contests.py`)

The placeholders are wired to a real **computed-field + par** model (the chosen
solo-player design — no other humans, no bot-GM drafting):

- **Tables:** `dfs_contests` (generated per slate from a template, small dollar
  fees/prizes) + `dfs_entries` (the user's persisted lineups). Lazy
  `CREATE TABLE IF NOT EXISTS`.
- **Endpoints:** `POST /api/enter` (validates positions + cap, persists),
  `GET /api/contest/<id>` (the live board), `GET /api/entries`. Contests are
  also injected into the page so the lobby renders real.
- **Scoring:** one shared `_LiveContext` per slate so a lineup scores
  identically everywhere — realized DFS points once a player's team game is
  **final**, else the projection as the in-progress estimate. Reads persisted
  `game_*_stats`; never re-sims.
- **The field:** a deterministic, skill-tiered set of synthetic legal lineups
  scored by the same rule. The user's standing is scaled from their position
  in the sample up to the contest's advertised field (thousands), so the board
  reads "921st of 1,001" against **procedurally generated character handles**
  (roots × tails × number → thousands of distinct in-world usernames).
- **Par:** a strong best-possible-lineup benchmark (best per slot, then
  downgrade least-costly-to-give-up spots under the cap) — the number to chase.
- **Front-end:** Live and My Entries screens fetch the real endpoints; the
  builder's Enter posts the lineup. All five JSX files transpile clean (checked
  via Babel). Verified end-to-end on a partially-simmed slate (8/15 games):
  enter → live board (rank/par/percentile/cash, per-player Final/Live) →
  entries, with scores consistent across views.

## What was *not* done (deliberately deferred)

- Pilot pool surfaces all rostered arms by talent, not just projected
  starters — fine for v1, a candidate refinement.
- Per-entry rank in My Entries uses the contest's best-entry rank (fine for the
  typical one-entry case); payouts use a toy curve (no real wallet economy).
- The seven non-DFS formats (2C/Stay, Walk-Back, Pilot Room, Skipper, Beat
  the Voyage, Hot Hand, Joker Draft) are hub cards with teasers only; each is
  a scoring preset over existing columns when built.
