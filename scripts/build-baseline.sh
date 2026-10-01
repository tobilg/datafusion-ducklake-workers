#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT/vendor/workers-rs/examples/emscripten-tokio"
worker-build --emscripten --release -- --locked
