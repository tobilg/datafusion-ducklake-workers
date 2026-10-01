#!/usr/bin/env python3
"""Reject stale, diagnostic, or development artifacts before release measurement."""

from devlib import REPORTS
import argparse, hashlib, json, pathlib
from vendor_sources import verify_sources

p = argparse.ArgumentParser()
p.add_argument("--variant", choices=["core", "full"], required=True)
p.add_argument("--tests", action="store_true")
a = p.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
manifest = json.loads(
    (root / f".cache/reports/artifact-manifest-{a.variant}.json").read_text()
)
if manifest.get("vendor_sources", {}).get("sha256") != verify_sources()["sha256"]:
    raise SystemExit("Missing or stale complete vendor-source verification; rebuild")
assert (
    manifest["variant"] == a.variant
    and manifest["emscripten_link_optimization"] == "s"
    and not manifest["protocol_probe"]
), "Expected production artifact"
for record in manifest["inputs"] + manifest["modules"]:
    assert (
        hashlib.sha256((root / record["path"]).read_bytes()).hexdigest()
        == record["sha256"]
    ), f"Stale input/artifact: {record['path']}"
wasm = next(r["sha256"] for r in manifest["modules"] if r["path"].endswith(".wasm"))
js = next(r["sha256"] for r in manifest["modules"] if r["path"].endswith("/index.js"))
if a.tests:
    for backend in ["r2", "s3"] + (["unsigned"] if a.variant == "core" else []):
        report = json.loads(
            (root / f".cache/reports/files-{a.variant}-{backend}.json").read_text()
        )
        assert (
            report["wasm_sha256"] == wasm
        ), f"Tests reference a different {a.variant} artifact"
        assert (
            report["js_sha256"] == js
        ), f"Tests reference different {a.variant} JavaScript"
print(
    a.variant,
    "production inputs, modules" + (" and file tests" if a.tests else "") + " match",
)
