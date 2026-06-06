# AAR — CapSpace (/fantasy): pre-compile JSX, drop in-browser Babel

## Symptom
The CapSpace DFS app at `/fantasy` "lagged a lot" on load, while the main app
(after the asset self-hosting fix) and the sim ran fine.

## Diagnosis
`capspace.html` did all of the following on **every** page load:

1. Downloaded **development** builds of React + ReactDOM from unpkg.
2. Downloaded the **entire Babel compiler** (`@babel/standalone`, ~3 MB) from unpkg.
3. **Transpiled ~120 KB of JSX in the browser at runtime** (`type="text/babel"`
   across 4 files) — nothing renders until Babel finishes compiling.
4. Pulled 3 Google Font families render-blocking from `fonts.googleapis.com` /
   `fonts.gstatic.com`.

In-browser Babel is explicitly "not for production" — it was the dominant cost
(huge download + CPU-bound compile on every visit).

## Change (option chosen by the owner: "precompile + drop Babel — fastest")
The `.jsx` files are now the source of truth; the served `.js` files are
committed build artifacts. The page loads plain JS with **no Babel and no
runtime transpile**, against **production**, self-hosted React.

- Added `tools/build_capspace.sh` — installs a pinned Babel toolchain into
  `tools/.jsxbuild/` (gitignored) and transpiles each `capspace-*.jsx` →
  `.js` with `@babel/preset-react` in **classic** runtime (emits
  `React.createElement`, since React is a UMD global, not an ES import).
- Committed the 5 generated artifacts: `capspace-{data,ui,screens,builder,app}.js`.
- Self-hosted production React + ReactDOM UMD into
  `static/vendor/react*.production.min.js`.
- Self-hosted the 3 fonts (Outfit, Hanken Grotesk, Space Grotesk;
  latin + latin-ext woff2; variable fonts → 6 files) into `static/vendor/`,
  same approach as the main app's `base.html` fix.
- Rewrote `capspace.html`: local fonts.css, local prod React, plain `<script
  src=...js>` tags (load order preserved: data → ui → screens → builder → app),
  removed all unpkg/jsdelivr/googleapis references and the Babel script.

## Workflow change (important)
Editing a `capspace-*.jsx` no longer takes effect on its own — you must run
`./tools/build_capspace.sh` and commit the regenerated `.js`. This is the cost
of dropping the in-browser compiler; it's documented in the script header and
in a comment in `capspace.html`.

## Validation
- Build script run from clean (`rm -rf tools/.jsxbuild`) reproduces all 5 `.js`.
- `node --check` passes on every generated file; output contains
  `React.createElement` and no raw JSX; mount uses `ReactDOM.createRoot`.
- `capspace.html` parses under Jinja2; no external CDN/babel/.jsx refs remain.
- React UMD confirmed as the production build.
- **Not** verified in a running browser (no Flask/DB in the sandbox) — the page
  should be loaded once on a deploy to confirm the app boots and renders.

## Notes / follow-ups
- The `.jsx` sources are retained as the editable source; the `.js` are
  artifacts. Keep them in sync via the build script.
- The other CapSpace games (Pilots, Categories, Best Ball, Sportsbook) share
  this same page/bundle, so they benefit too.
