#!/usr/bin/env bash
# Source from build scripts. No credentials or machine-global environment edits.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export RUSTUP_TOOLCHAIN=1.98.0
export PATH="$PROJECT_ROOT/.tools/bin:$PROJECT_ROOT/node_modules/.bin:$PATH"
export WRANGLER_LOG_PATH="$PROJECT_ROOT/.cache/wrangler"
export WRANGLER_SEND_METRICS=false
mkdir -p "$PROJECT_ROOT/.cache/reports"
# The managed SDK applies the required networking patches. Overrides bypass them.
unset EMSCRIPTEN EMSDK EM_CONFIG EMCC_CFLAGS RUSTFLAGS CARGO_ENCODED_RUSTFLAGS
unset WASM_BINDGEN_BIN WASM_OPT_BIN ESBUILD_BIN
