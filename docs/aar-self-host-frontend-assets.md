# AAR — Self-host front-end assets (fix slow "blank-then-loads" page loads)

## Symptom
Loading the live site (superinnings.com / superinnin.gs) felt slow: **every**
page showed a blank/unstyled flash for a while before appearing.

## Diagnosis
The cause was front-end, not the server. Every page extends `base.html`, whose
`<head>` render-blocked on **three external domains** before the browser could
paint:

- `fonts.googleapis.com` — Google Fonts CSS (Archivo + Oswald)
- `fonts.gstatic.com` — the woff2 font files
- `cdn.jsdelivr.net` — Bootstrap 5.3.0 CSS (render-blocking in `<head>`) + JS

A stylesheet `<link>` blocks first paint until it loads. When those CDNs are
slow/throttled from the visitor's network, the page sits blank even though the
Flask server responded instantly. The fonts are used throughout the CSS
(headlines, scores, team abbrevs, stat tables all key off Oswald), so simply
dropping them would have changed the look — the owner chose to keep it.

## Change
Vendored the exact same assets into `o27v2/web/static/vendor/` and pointed
`base.html` at them via `url_for('static', ...)`, so first paint is same-origin
with zero external round-trips. No version changes, no build step (consistent
with the project's "single package, no build step" stance).

- `static/vendor/bootstrap.min.css` / `bootstrap.bundle.min.js` — Bootstrap 5.3.0, byte-for-byte from jsDelivr.
- `static/vendor/fonts.css` — Google Fonts CSS, rewritten so `src: url(...)`
  points at local files. Kept the `latin` + `latin-ext` subsets (covers
  accented player/country names); dropped vietnamese/cyrillic. `font-display:
  swap` preserved.
- `static/vendor/fonts/*.woff2` — 4 files, 117 KB total. Archivo and Oswald are
  variable fonts, so one woff2 per family/subset serves every weight.

Removed the now-unneeded `preconnect` hints to the Google Fonts domains.

## Not changed / follow-ups
- `universe_new.html` still loads `cdn.tailwindcss.com` (the dev-only JIT CDN).
  It's a single page, not part of the every-page `base.html` path, and
  vendoring Tailwind properly needs a build step the project avoids — left as a
  separate decision.
- Left Flask's default static caching (ETag/conditional 304s) as-is. The
  first-paint win is from removing the external hops; long-lived `Cache-Control`
  on these immutable vendor files is a possible future tweak for repeat visits.

## Validation
- `base.html` parses under Jinja2.
- woff2 files verified (`wOF2` magic bytes); fonts.css rewritten to 4 unique
  local `src` URLs across 20 `@font-face` rules.
- Could not run the live server here (no Flask/DB in the sandbox); the change is
  template + static assets only, with no Python/route changes.
