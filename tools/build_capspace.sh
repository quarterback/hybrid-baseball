#!/usr/bin/env bash
# Pre-compile the CapSpace (/fantasy) React app from JSX to plain JS.
#
# CapSpace ships as a small React app written in .jsx. We deliberately do NOT
# transpile JSX in the browser (the old setup downloaded the ~3 MB Babel
# compiler and recompiled 120 KB of JSX on every page load — that was the lag).
# Instead the .jsx sources are the source of truth and the served .js files are
# build artifacts, committed alongside them.
#
# Run this after editing any o27v2/web/fantasy/static/capspace-*.jsx, then
# commit the regenerated .js files.
#
#   ./tools/build_capspace.sh
#
# Requires Node + npm on PATH. Babel is installed into tools/.jsxbuild/
# (gitignored) on first run; delete that dir to force a clean reinstall.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
STATIC="$REPO/o27v2/web/fantasy/static"
BUILD="$REPO/tools/.jsxbuild"

# JSX entry points, in load order (data first; app last mounts the tree).
FILES=(capspace-data capspace-ui capspace-screens capspace-builder capspace-app)

mkdir -p "$BUILD"
if [ ! -x "$BUILD/node_modules/.bin/babel" ]; then
  echo "Installing Babel toolchain into $BUILD ..."
  (cd "$BUILD" \
    && npm init -y >/dev/null 2>&1 \
    && npm install --no-audit --no-fund \
         @babel/core@7 @babel/cli@7 @babel/preset-react@7 >/dev/null 2>&1)
fi

# Classic runtime: emit React.createElement, since React is a UMD global
# (window.React) rather than an ES import.
cat > "$BUILD/babel.config.json" <<'JSON'
{ "presets": [ ["@babel/preset-react", { "runtime": "classic" }] ] }
JSON

cd "$BUILD"
for f in "${FILES[@]}"; do
  ./node_modules/.bin/babel "$STATIC/$f.jsx" -o "$STATIC/$f.js"
  echo "built $f.js"
done
echo "Done. Commit the regenerated $STATIC/capspace-*.js files."
