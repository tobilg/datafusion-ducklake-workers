#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
variant="${1:-full}"
case "$variant" in core) features=(--no-default-features);; full) features=(--no-default-features --features ducklake);; *) exit 2;; esac
mkdir -p .cache/reports
cargo tree --locked "${features[@]}" --target wasm32-unknown-emscripten -e normal,build > .cache/reports/target-packages-$variant.txt
cargo tree --locked "${features[@]}" --target wasm32-unknown-emscripten -e features,no-dev > .cache/reports/target-features-$variant.txt
if rg '(^|[ ─├└│])(rusqlite|libsqlite3-sys|duckdb|libduckdb-sys|tokio-postgres|postgres|postgres-protocol|postgres-types) v' .cache/reports/target-packages-$variant.txt; then
  echo 'Excluded catalog driver in production target graph' >&2
  exit 1
fi
echo 'No SQLite, DuckDB, or PostgreSQL driver in production target graph'
if rg 'tokio feature "rt-multi-thread"|rayon v' .cache/reports/target-features-$variant.txt; then
  echo 'Unexpected threadpool dependency/feature' >&2
  exit 1
fi

# The first line names our application, not a dependency. Its renamed package
# also starts with datafusion-ducklake, so audit only the dependency lines.
if [[ "$variant" == core ]] && tail -n +2 ".cache/reports/target-packages-$variant.txt" | rg '(datafusion-ducklake[^ ]*|ducklake-[^ ]*|quack-rs) v'; then
  echo 'DuckLake dependency in core graph' >&2; exit 1
fi
