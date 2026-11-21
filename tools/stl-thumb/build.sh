#!/bin/sh
# Rebuild static/vendor/stl-thumb.js (only needed when changing stl-thumb.src.js).
set -e
cd "$(dirname "$0")"
npm install --no-save three@0.187.0 esbuild@0.25 >/dev/null
npx esbuild stl-thumb.src.js --bundle --minify --format=esm --outfile=../../static/vendor/stl-thumb.js \
  --legal-comments=eof
