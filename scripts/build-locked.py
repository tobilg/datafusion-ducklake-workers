#!/usr/bin/env python3
"""Serialize linking, companion-module collection, and manifest creation.

Cargo releases its lock before worker-build collects the Emscripten JS/WASM.
Both feature variants use the same intermediate filenames. Hold our lock across
that whole operation, including builds from exports sharing the target cache.
"""

import fcntl
import pathlib
import subprocess
import sys
from vendor_sources import verify_sources

root = pathlib.Path(__file__).resolve().parents[1]
variant, diagnostic, *features = sys.argv[1:]
assert variant in ("core", "full") and diagnostic in ("true", "false")
artifact = variant + ("-probe" if diagnostic == "true" else "")
target = root / "target"
target.mkdir(exist_ok=True)
with (target / ".worker-build.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    verified = verify_sources()
    subprocess.run(
        [
            "worker-build",
            "--emscripten",
            "--release",
            "--out-dir",
            f"build/{artifact}",
            "--",
            "--locked",
            *features,
        ],
        cwd=root,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "scripts/artifact-manifest.py",
            "--variant",
            variant,
            "--diagnostic",
            diagnostic,
            "--source-sha256",
            verified["sha256"],
        ],
        cwd=root,
        check=True,
    )
