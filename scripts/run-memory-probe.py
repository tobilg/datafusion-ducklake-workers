#!/usr/bin/env python3
"""Run and stop an isolated local wrapper around verified release WASM."""

import argparse, pathlib, subprocess

p = argparse.ArgumentParser()
p.add_argument("backend", choices=["r2", "s3"])
p.add_argument("--files", action="store_true")
p.add_argument("--variant", choices=["core", "full"], default="full")
a = p.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
subprocess.run(
    ["python3", "scripts/check-artifacts.py", "--variant", a.variant],
    cwd=root,
    check=True,
)
args = [a.backend, "--variant", a.variant] + (["--files"] if a.files else [])
subprocess.run(
    ["python3", "scripts/prepare-memory-probe.py", *args], cwd=root, check=True
)
key = (a.variant + "-files-" if a.files else "") + a.backend
port = (
    (18793 if a.backend == "r2" else 18794)
    if a.files
    else (18790 if a.backend == "r2" else 18791)
)
directory = root / ".cache" / ("memory-probe-" + key)
from devlib import service

with service(
    [
        root / "node_modules/.bin/wrangler",
        "dev",
        "--local",
        "--ip",
        "127.0.0.1",
        "--port",
        str(port),
        "--inspector-port",
        str(port + 1000),
        "--config",
        directory / "wrangler.jsonc",
        "--persist-to",
        root / (".cache/files-state" if a.files else ".cache/local-state"),
    ],
    port,
    "memory-probe-" + key + "/worker.log",
    extra_ports=(port + 1000,),
):
    subprocess.run(
        ["python3", "scripts/measure-wasm-memory.py", *args], cwd=root, check=True
    )
