#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
variant="${1:-core}"
backend="${2:-r2}"
case "$variant-$backend" in
  core-r2) port=8793; inspector=9243;;
  core-s3) port=8794; inspector=9244;;
  core-unsigned) port=8795; inspector=9245;;
  full-r2) port=8796; inspector=9246;;
  full-s3) port=8797; inspector=9247;;
  *) echo 'Expected core r2|s3|unsigned, or full r2|s3' >&2; exit 2;;
esac
config=".cache/fixtures/files/$variant-$backend/wrangler.jsonc"
[[ -f "$config" ]] || { echo 'Prepare fixtures with scripts/validate.py --suite files first' >&2; exit 1; }
exec wrangler dev --config "$config" --local \
  --ip 127.0.0.1 --port "$port" --inspector-port "$inspector" --persist-to "$PROJECT_ROOT/.cache/files-state"
