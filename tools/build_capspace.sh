#!/usr/bin/env bash
# Pre-compile the CapSpace (/fantasy) React app from JSX to plain JS.
#
# CapSpace ships as a small React app written in .jsx. We deliberately do NOT
# transpile JSX in the browser (the old setup downloaded the ~3 MB Babel
# compiler and recompiled ~120 KB of JSX on every page load — that was the
# lag). Instead the .jsx sources are the source of truth and the served .js
# files are build artifacts, committed alongside them.
#
# Run this after editing any o27v2/web/fantasy/static/capspace-*.jsx, then
# commit the regenerated .js files:
#
#   ./tools/build_capspace.sh
#
# Babel is a workspace devDependency (see the repo-root package.json). If it's
# missing, install it with the workspace's package manager:  pnpm install
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
STATIC="$REPO/o27v2/web/fantasy/static"
BABEL="$REPO/node_modules/.bin/babel"

# JSX entry points, in load order (data first; app last mounts the tree).
FILES=(capspace-data capspace-ui capspace-screens capspace-builder capspace-app)

if [ ! -x "$BABEL" ]; then
  echo "Babel not found at $BABEL — run 'pnpm install' at the repo root first." >&2
  exit 1
fi

# Classic runtime: emit React.createElement, since React is a UMD global
# (window.React) rather than an ES import. The config lives at the repo root
# (cleaned up on exit) so @babel/preset-react resolves against the workspace's
# node_modules — pnpm's symlinked layout won't resolve it from elsewhere.
CFG="$REPO/.babelrc.capspace.json"
trap 'rm -f "$CFG"' EXIT
cat > "$CFG" <<'JSON'
{ "presets": [ ["@babel/preset-react", { "runtime": "classic" }] ] }
JSON

for f in "${FILES[@]}"; do
  "$BABEL" --config-file "$CFG" "$STATIC/$f.jsx" -o "$STATIC/$f.js"
  echo "built $f.js"
done
echo "Done. Commit the regenerated $STATIC/capspace-*.js files."
