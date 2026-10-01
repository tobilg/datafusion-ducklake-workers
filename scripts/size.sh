#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
[[ "${WORKER_LINK_OPT:-s}" == s ]] || { echo 'Release measurement requires WORKER_LINK_OPT=s (or unset)' >&2; exit 2; }
variant="${1:-full}"
case "$variant" in core) config=wrangler.core.jsonc;; full) config=wrangler.jsonc;; *.json|*.jsonc) config="$variant"; variant=full;; *) echo 'Expected core|full or a full-build config' >&2; exit 2;; esac
# Rebuild through the config, so the upload measurement cannot use an obsolete variant.
wrangler deploy --config "$config" --dry-run --outdir "bundled-$variant" 2>&1 | tee ".cache/reports/release-size-$variant.txt"
python3 - "$variant" <<'PYCODE'
import pathlib,re,sys
text=pathlib.Path(f'.cache/reports/release-size-{sys.argv[1]}.txt').read_text()
m=re.search(r'Total Upload: ([0-9.]+) KiB',text)
if not m:raise SystemExit('Missing Wrangler Total Upload; inspect the output')
size=float(m[1]);print(f'Entire upload: {size/1024:.2f} MiB; pilot headroom target <=56 MiB')
if size>56*1024:raise SystemExit('Bundle exceeds the 56 MiB pilot headroom target')
PYCODE
