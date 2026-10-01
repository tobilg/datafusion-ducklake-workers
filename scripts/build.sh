#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
variant=full
diagnostic=false
if [[ "${1:-}" == --variant ]]; then variant="$2"; shift 2; fi
case "$variant" in
  core) features=(--no-default-features);;
  full) features=(--no-default-features --features ducklake);;
  *) echo 'Expected --variant core|full' >&2; exit 2;;
esac
# The only additional application feature is the explicit local diagnostic feature.
while (($#)); do
  case "$1" in
    --features) [[ "${2:-}" == protocol-probe ]] || { echo 'Only protocol-probe may be added; use --variant for DuckLake' >&2; exit 2; }; features+=(--features protocol-probe); diagnostic=true; shift 2;;
    --features=protocol-probe) features+=(--features protocol-probe); diagnostic=true; shift;;
    *) echo 'Supported options: --variant core|full [--features protocol-probe]' >&2; exit 2;;
  esac
done
python3 scripts/build-locked.py "$variant" "$diagnostic" "${features[@]}"
