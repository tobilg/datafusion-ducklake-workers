#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
python3 scripts/check-environment.py --profile bootstrap
python3 scripts/sources.py "$@"
python3 scripts/apply-patches.py
python3 scripts/verify-patches.py
rustup toolchain install "$RUSTUP_TOOLCHAIN" --profile minimal --component rustfmt --target wasm32-unknown-emscripten
npm ci --cache .cache/npm
python3 scripts/tool_cache.py worker-build -- cargo install --path vendor/workers-rs/worker-build --locked --root .tools --bin worker-build --force
