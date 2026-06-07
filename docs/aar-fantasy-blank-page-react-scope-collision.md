# AAR — Fantasy (CapSpace) blank page: duplicate top-level `const` across bundles

**Status:** Fixed (branch `claude/clever-knuth-hgguw`)
**Area:** `o27v2/web/fantasy/static/` (CapSpace React bundles)
**Severity:** High — `/fantasy` rendered a blank page for everyone, not just one save.

## Summary
`/fantasy` returned HTTP 200 with all assets loading, but the page was blank.
The React app never mounted because several precompiled bundles each declared
the same names at the top level of a **classic `<script>`**, and classic scripts
share one global lexical environment. The second such script threw
`SyntaxError: Identifier 'useState' has already been declared`, which aborted
`capspace-screens.js`, `capspace-builder.js`, and `capspace-app.js` — so `App`
was never defined and `ReactDOM.createRoot(...).render()` never ran.

This was a regression from the JSX precompile work (`119b149`, "pre-compile JSX
+ self-host React"). In-browser Babel had isolated each file; the plain
classic-script artifacts do not. None of the later lag/`database is locked`
fixes caught it because their validation measured **server** response time
(`curl /fantasy/ ~14ms`), never an actual browser render.

## Root cause
`capspace-{app,ui,screens,builder}.jsx` each had, at module top level:

```js
const { useState, useEffect } = React;   // ui also: useRef
```

and both `ui` and `app` declared `const CurrencyCtx = …`. Across separate
classic scripts these go into the **same** global lexical scope, so the 2nd
declaration is a redeclaration → `SyntaxError`, and that whole script is
skipped. Load order is `data, ui, screens, builder, app`, so everything after
`ui` died and the root stayed empty.

The bundles are *designed* to share global scope (e.g. `app.js` references
`AppShell`/`HubScreen` defined in other files; `CurrencyCtx` is shared via
`window.CurrencyCtx`), so wrapping each file in an IIFE was not an option.

## Fix
Change the colliding top-level `const` declarations to `var` (redeclarable
across scripts; same value each time) in the four `.jsx` sources, and rebuild
the served `.js` with `tools/build_capspace.sh` (Babel preset-react only
transforms JSX, so `var` is preserved verbatim). A `// Do NOT change back to
const` comment explains why on each line. Files touched:

- `capspace-app.jsx` / `.js` — `useState`/`useEffect` and `CurrencyCtx` → `var`
- `capspace-ui.jsx` / `.js` — `useState`/`useEffect`/`useRef` and `CurrencyCtx` → `var`
- `capspace-screens.jsx` / `.js` — `useState`/`useEffect` → `var`
- `capspace-builder.jsx` / `.js` — `useState`/`useEffect` → `var`

`capspace-data.jsx` declared no colliding globals and was left unchanged (its
rebuilt `.js` is byte-identical).

## Validation
Reproduced and confirmed in `jsdom` loading the bundles as real `<script>`
elements (an earlier debugging pass that loaded each file via `eval` masked the
bug, because `eval` gives top-level `const` its own scope — only real
`<script>` tags share the global lexical env):

- **Before:** `#root` had 0 children; 3× `SyntaxError: Identifier 'useState'
  has already been declared`.
- **After (rebuilt bundles):** `#root` mounts (1 child, ~15 KB of markup), 0
  console/jsdom errors.
- **Against the live `manage.py runserver`** (jsdom `JSDOM.fromURL`, scripts run,
  real `/fantasy/api/*` fetches): `#root` child count 1, 0 errors — was 0
  children + SyntaxErrors before the rebuild.

Verified with a full save (866 games simmed; real player logs / slate). Did
**not** touch any server-side route, the slate/settle logic, or the separate
CapSpace DB work from the prior bug report — this was purely the client bundle
scope collision.

## Notes / follow-ups
- **De-React (downrange):** the owner wants CapSpace off React eventually, to
  match the rest of the tools (server-rendered Flask + plain JS). Not done here
  — this change only un-blanks the existing app. That rewrite spans every
  screen (Hub, Lobby, Builder, Live, Entries, Streak, Sluggers, Pilots,
  Categories, Sportsbook, Best Ball, Onboarding, Player drawer) and is a
  separate, larger effort.
- Future guard worth considering: a tiny CI/check that greps the built bundles
  for duplicate top-level lexical declarations, or an error boundary + bootstrap
  `try/catch` so a JS failure shows a diagnostic instead of a blank page.
