#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
python3 scripts/prepare-local-fixture.py
exec wrangler dev --config fixtures/quacklake/wrangler.jsonc --local --ip 127.0.0.1 \
  --port 8792 --inspector-port 9234 --persist-to "$PROJECT_ROOT/.cache/local-state"
