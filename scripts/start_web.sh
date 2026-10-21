#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR/web"
if [[ ! -f dist/index.html ]]; then
  node node_modules/vue-tsc/bin/vue-tsc.js -b
  node node_modules/vite/bin/vite.js build
fi
exec node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 5173 --strictPort
