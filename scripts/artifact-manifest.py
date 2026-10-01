#!/usr/bin/env python3
"""Hash local build inputs and generated JS/WASM; no credentials or cloud access."""

from devlib import REPORTS
import argparse, datetime, hashlib, json, os, pathlib
from vendor_sources import verify_sources

p = argparse.ArgumentParser()
p.add_argument("--variant", choices=["core", "full"], default="full")
p.add_argument("--diagnostic", choices=["true", "false"], default="false")
p.add_argument("--source-sha256", required=True)
a = p.parse_args()
sources = verify_sources()
if sources["sha256"] != a.source_sha256:
    raise SystemExit("Vendor sources changed during compilation; artifact rejected")
root = pathlib.Path(__file__).resolve().parents[1]
artifact = a.variant + ("-probe" if a.diagnostic == "true" else "")


def record(path):
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(root)),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


files = sorted(
    p
    for p in (root / "build" / artifact).rglob("*")
    if p.is_file() and ".tmp" not in p.parts
)
assert any(p.suffix == ".wasm" for p in files) and any(p.suffix == ".js" for p in files)
inputs = [
    root / name
    for name in [
        "Cargo.toml",
        "Cargo.lock",
        "build.rs",
        "rust-toolchain.toml",
        "package-lock.json",
        "sources.lock.json",
        "tools.lock.json",
        "scripts/build.sh",
        "scripts/build-locked.py",
        "scripts/env.sh",
        "scripts/artifact-manifest.py",
        "scripts/devlib.py",
        "scripts/vendor_sources.py",
        "patches/series.json",
    ]
]
inputs += sorted((root / "src").glob("*.rs")) + sorted(
    (root / "patches").glob("*.patch")
)
report = {
    "variant": a.variant,
    "protocol_probe": a.diagnostic == "true",
    "emscripten_link_optimization": os.environ.get("WORKER_LINK_OPT", "s"),
    "recorded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "scope": f"Build inputs and generated modules. Wrangler Total Upload in release-size-{a.variant}.txt measures the entire deployable bundle. O1 artifacts are development-only.",
    "modules": [record(p) for p in files],
    "inputs": [record(p) for p in inputs],
    "vendor_sources": sources,
}
(root / f".cache/reports/artifact-manifest-{artifact}.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print("Recorded module sizes and SHA-256 hashes; inputs include the full patch series.")
