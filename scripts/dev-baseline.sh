#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
exec wrangler dev --local --ip 127.0.0.1 --config fixtures/wrangler.baseline.jsonc
