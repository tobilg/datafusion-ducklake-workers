#!/usr/bin/env python3
"""Reuse pinned host tools only when their input and executable hashes match."""

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from devlib import ROOT


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(name, root=ROOT):
    paths = [
        "tools.lock.json",
        "sources.lock.json",
        "rust-toolchain.toml",
        "scripts/tool_cache.py",
    ]
    if name == "worker-build":
        paths += ["scripts/bootstrap.sh", "scripts/sources.py", "patches/series.json"]
        paths += [
            p.relative_to(root).as_posix()
            for p in sorted((root / "patches").glob("*.patch"))
        ]
    elif name == "minio":
        paths += ["fixtures/minio.lock.json", "scripts/build-minio.sh"]
    elif name == "duckdb":
        paths += ["fixtures/duckdb.lock.json", "scripts/install-duckdb.py"]
    else:
        raise ValueError("Unknown cached tool")
    value = {
        "platform": platform.system(),
        "architecture": platform.machine(),
        "inputs": {p: digest(root / p) for p in paths},
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def reuse_or_build(name, command, root=ROOT):
    # Check the fixture's source even when its native binary is restored.
    if name == "minio":
        from vendor_sources import export_git, compare_trees

        pin = json.loads((root / "fixtures/minio.lock.json").read_text())
        with tempfile.TemporaryDirectory(
            dir=root / ".cache", prefix="verify-minio-"
        ) as tmp:
            export_git(root / "vendor/minio", Path(tmp), pin["revision"])
            compare_trees(Path(tmp), root / "vendor/minio")
    binary = root / ".tools/bin" / name
    stamp = root / ".tools/cache" / (name + ".json")
    key = fingerprint(name, root)
    try:
        recorded = json.loads(stamp.read_text())
    except (OSError, ValueError):
        recorded = {}
    if (
        binary.is_file()
        and os.access(binary, os.X_OK)
        and recorded == {"inputs": key, "binary": digest(binary)}
    ):
        print(f"Reusing verified pinned {name}", flush=True)
        return
    print(f"Building/installing pinned {name}", flush=True)
    subprocess.run(command, check=True)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError(f"{name} did not produce an executable")
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps({"inputs": key, "binary": digest(binary)}) + "\n")


if __name__ == "__main__":
    name, separator, *command = sys.argv[1:]
    if separator != "--" or not command:
        raise SystemExit(
            "Expected tool_cache.py worker-build|minio|duckdb -- command ..."
        )
    reuse_or_build(name, command)
