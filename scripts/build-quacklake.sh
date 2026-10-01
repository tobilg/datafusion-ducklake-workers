#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
python3 scripts/check-environment.py --profile catalog
python3 scripts/sources.py --fixtures
python3 scripts/verify-patches.py --fixtures
cd "$PROJECT_ROOT/vendor/quacklake"
pnpm install --frozen-lockfile --ignore-scripts
# Same Vite command as the pinned upstream Wrangler build; no deployment.
pnpm exec vite build --config vite.worker.config.ts
test -f .wrangler-build/index.js
