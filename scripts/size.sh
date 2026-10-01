#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
[[ "${WORKER_LINK_OPT:-s}" == s ]] || { echo 'Release measurement requires WORKER_LINK_OPT=s (or unset)' >&2; exit 2; }
variant="${1:-full}"
case "$variant" in core) config=wrangler.core.jsonc;; full) config=wrangler.jsonc;; *.json|*.jsonc) config="$variant"; variant=full;; *) echo 'Expected core|full or a full-build config' >&2; exit 2;; esac
# Hash/source checks reject stale artifacts before and after the dry-run.
python3 scripts/measure_size.py "$variant" "$config"
