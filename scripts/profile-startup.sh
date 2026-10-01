#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
variant="${1:-full}"
case "$variant" in core) default_config=wrangler.core.jsonc;; full) default_config=wrangler.jsonc;; *) echo 'Expected core|full [config.jsonc]' >&2; exit 2;; esac
source_config="${2:-$default_config}"
python3 scripts/check-artifacts.py --variant "$variant"
python3 - "$variant" "$source_config" <<'PY'
import json,pathlib,sys
sys.path.insert(0,'scripts')
from devlib import read_jsonc
root=pathlib.Path.cwd();variant=sys.argv[1]
source=pathlib.Path(sys.argv[2]).resolve()
config=read_jsonc(source)
if (source.parent / config['main']).resolve() != root/f'build/{variant}/index.js':
    raise SystemExit('Configuration main must reference the selected production variant')
config.pop('build',None);config.pop('$schema',None);config['main']=str(root/f'build/{variant}/index.js')
path=root/f'.cache/startup-{variant}';path.mkdir(exist_ok=True)
(path/'wrangler.json').write_text(json.dumps(config))
PY
# The verified release is already built; omit only the redundant custom build.
config=".cache/startup-$variant/wrangler.json"
wrangler check startup --config "$config" --args="--config=$config" \
  --outfile ".cache/reports/local-startup-$variant.cpuprofile" \
  2>&1 | tee ".cache/reports/local-startup-$variant.txt"
