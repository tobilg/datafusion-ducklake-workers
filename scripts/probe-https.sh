#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT/repro/https"
worker-build --emscripten --release -- --locked
cd "$PROJECT_ROOT"
exec wrangler dev --config fixtures/wrangler.https-probe.jsonc --local --ip 127.0.0.1 \
  --port 8789 --inspector-port 9231
