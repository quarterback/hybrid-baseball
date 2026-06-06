# Review Packet — Self-host front-end assets to fix slow page loads

**Assignment:** "Loading when I type the domains are very slow."
**Branch:** `claude/slow-domain-loading-O8U1D`
**Commit:** `6fe0eb3`
**Date:** 2026-06-06
**Companion AAR:** `docs/aar-self-host-frontend-assets.md`

---

## What was your objective?

The brief was one sentence and ambiguous: the site felt slow to load. The real
objective had to be discovered, not assumed. After three rounds of clarification
it resolved to: **diagnose and fix why every page on superinnings.com /
superinnin.gs shows a blank/unstyled flash before it appears.** Not "make the
sim faster," not "optimize a heavy page" — the universal first-paint stall.

## What work did you complete?

- Diagnosed the cause as render-blocking external assets in `base.html`'s
  `<head>`: Google Fonts CSS (`fonts.googleapis.com`), the font files
  (`fonts.gstatic.com`), and Bootstrap CSS/JS (`cdn.jsdelivr.net`).
- Vendored byte-for-byte copies into `o27v2/web/static/vendor/`:
  Bootstrap 5.3.0 CSS + JS bundle; a rewritten `fonts.css`; and 4 `.woff2`
  files (Archivo + Oswald, `latin` + `latin-ext`, 117 KB total).
- Rewrote `base.html` to load all of it via `url_for('static', ...)` and
  removed the now-useless `preconnect` hints.
- Verified: Jinja parse of `base.html`, `wOF2` magic bytes on each font, and
  that the 20 `@font-face` rules collapsed to 4 local `src` URLs (variable
  fonts).
- Wrote the AAR, committed, and pushed.

## What decisions mattered most?

1. **Stopping to ask instead of coding on the first guess.** "Slow loading"
   could have been the sim engine, a heavy box-score page, or N+1 queries. I had
   already started reading an 11,630-line `app.py` looking for server-side
   hotspots. The clarifying questions redirected the entire effort to the
   front end — a completely different fix. That pivot was the single highest-
   leverage decision.
2. **Self-host rather than async-load or drop.** Self-hosting kills the external
   round-trips while keeping the exact look and requiring no build step — which
   matches the project's stated "single package, no build step" philosophy.
3. **Keeping `latin` + `latin-ext` but dropping vietnamese/cyrillic.** Balances
   file count against the reality of accented player/country names.

## What obstacles did you encounter?

- **No runnable environment.** Flask and the SQLite DB aren't present in the
  sandbox, so I could not start the server, measure load times, or visually
  confirm the rendered result. Verification is static-only.
- **Ambiguous, drip-fed requirements.** The objective arrived across five
  messages, two of which ("The sim game", "I'm not using custom fonts") pointed
  *away* from the actual cause and had to be reconciled against the code.
- **User-Agent-dependent font delivery.** Google Fonts serves `.ttf` to a
  generic UA and `.woff2` only to a browser-like UA; the first download pulled
  heavier `.ttf`. I re-fetched with a Chrome UA to get woff2.

## What mistakes did you make?

- **I committed to the front-end hypothesis early and kept building (vendoring
  fonts) before the user had confirmed they even wanted the fonts kept.** When
  they said "I'm not using custom fonts," I'd already downloaded and rewritten
  the font CSS. It happened to be the right path, but I was ahead of the
  confirmation — a small amount of speculative work that could have been wasted.
- **I almost started optimizing the wrong layer.** My first two tool calls were
  exploring server-side aggregation and the game-detail route. Had the user not
  steered me, I might have spent real effort optimizing SQL that was never the
  bottleneck.

## What assumptions did you make?

- That `latin` + `latin-ext` subsets are sufficient and the dropped subsets
  (vietnamese, cyrillic) won't matter — names needing those glyphs fall back to
  system fonts, gracefully but not identically.
- That the visitor's network being slow *to those specific CDNs* is the trigger.
  I'm confident render-blocking external CSS causes blank-then-loads in general,
  but I never measured the actual latency to googleapis/gstatic/jsdelivr from
  the user's location — the diagnosis is mechanism-based, not measured.
- That pinning the identical Bootstrap 5.3.0 means zero visual/behavioral
  change. Reasonable, but unverified in a browser.

## What would you do differently?

- **Ask the disambiguating question first, before any code exploration.** I
  burned several tool calls reading `app.py` that the answer made irrelevant.
- **Hold the speculative vendoring until after the fonts decision.** Prepare the
  plan, confirm, then download.
- **Find a way to measure.** Even a `curl -w "%{time_total}"` timing comparison
  to each CDN, or a headless render, would turn "this is the likely cause" into
  "this is the cause." I should have at least timed the external requests to
  quantify the stall.

## What evidence supports your assessment?

- **Direct:** `base.html` lines 36–41 and 1443 (pre-change) loaded three
  external domains, two as render-blocking `<head>` stylesheets — a textbook
  cause of delayed first paint.
- **Direct:** The user independently confirmed the symptom as "every page
  blank-then-loads," which is the signature of render-blocking CSS, not of slow
  server response (that would be a spinner/hang, not an unstyled flash).
- **Supporting:** Vendored files verified present and valid (`wOF2` bytes,
  Jinja parse clean, 4 unique local font URLs).
- **Gap:** No before/after load-time measurement and no browser screenshot.
  The assessment is strong on mechanism, weak on quantified proof.

## What should a manager know that isn't obvious from the final output?

- **The diff looks trivial; the work wasn't.** The commit is "swap some URLs and
  add static files." But ~70% of the effort was *figuring out what to fix* — the
  request named no page, no symptom, and two of the user's own clarifications
  pointed at the wrong layer (the sim, then "no custom fonts" while the CSS uses
  fonts in ~15 places). The value delivered was diagnosis, not typing.
- **It is not verified in a running browser.** It's verified statically. Someone
  should load a deployed build and confirm the blank flash is gone and the look
  is unchanged before calling this closed.
- **There's a known, deliberately-unfixed follow-up:** `universe_new.html` still
  loads Tailwind from `cdn.tailwindcss.com` (the dev-only JIT CDN). I left it
  because it's a single page, not on the every-page path, and a proper fix needs
  a build step the project avoids. It's flagged, not forgotten — it needs a
  product decision, not a quiet patch.
- **The "I'm not using custom fonts" exchange matters.** The owner believed the
  site had no custom fonts; the code says otherwise. That gap is worth noting —
  it suggests the design system's font usage may be under-documented or
  inherited without the owner's full awareness.
