#!/usr/bin/env python3
"""Create an ignored, local-only JS diagnostic wrapper around the exact release WASM.
The production modules are never edited. No resources are provisioned.
"""

import argparse, hashlib, json, pathlib, re, shutil
from devlib import read_jsonc, file_fixture_dir

root = pathlib.Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("backend", choices=["r2", "s3"])
p.add_argument("--files", action="store_true")
p.add_argument("--variant", choices=["core", "full"], default="full")
a = p.parse_args()
assert a.files or a.variant == "full"
key = (a.variant + "-files-" if a.files else "") + a.backend
fixture = root / (
    "fixtures/wrangler.catalog-probe.jsonc"
    if a.backend == "r2"
    else "fixtures/s3/wrangler.jsonc"
)
if a.files:
    fixture = file_fixture_dir(a.variant, a.backend) / "wrangler.jsonc"
destination = root / ".cache" / ("memory-probe-" + key)
destination.mkdir(exist_ok=True)
source = (root / f"build/{a.variant}/index.js").read_text()
matches = re.findall(r"function [\w$]+\(\)\{return ([\w$]+)\.buffer\}", source)
assert len(matches) == 1, "Generated JS layout changed; inspect before instrumenting"
(destination / "index.js").write_text(
    source
    + "\nexport const localWasmBytes = () => "
    + matches[0]
    + ".buffer.byteLength;\n"
)
wasm = destination / "index_bg.wasm"
if wasm.is_symlink():
    wasm.unlink()
assert not wasm.exists(), "Refusing to replace a regular file"
wasm.symlink_to(root / f"build/{a.variant}/index_bg.wasm")
(destination / "entry.mjs").write_text(
    """import Worker, { localWasmBytes } from './index.js';
export default class extends Worker {
  async fetch(request) {
    const response = await super.fetch(request);
    const headers = new Headers(response.headers);
    headers.set('X-Local-Wasm-Bytes', String(localWasmBytes()));
    return new Response(response.body, {status: response.status, headers});
  }
}
"""
)
config = read_jsonc(fixture)
config.update(
    name="local-wasm-memory-" + key,
    main="entry.mjs",
    workers_dev=False,
    preview_urls=False,
)
config.pop("build", None)
config.pop("$schema", None)
(destination / "wrangler.jsonc").write_text(json.dumps(config, indent=2) + "\n")
secrets = destination / ".dev.vars"
secrets.touch(mode=0o600)
secrets.chmod(0o600)
shutil.copyfile(fixture.parent / ".dev.vars", secrets)
report = {
    "scope": "Local JS diagnostic wrapper. WASM byte-identical to release; production JS unchanged.",
    "release_js_sha256": hashlib.sha256(source.encode()).hexdigest(),
    "wasm_sha256": hashlib.sha256(
        (root / f"build/{a.variant}/index_bg.wasm").read_bytes()
    ).hexdigest(),
}
(destination / "source.json").write_text(json.dumps(report, indent=2) + "\n")
print(
    f"Prepared ignored local diagnostic in .cache/memory-probe-{key}; production modules unchanged."
)
