#!/usr/bin/env python3
"""Audit both locked production graphs and Node tooling; no Worker compilation."""

import hashlib
import io
import json
import platform
import subprocess
import tarfile
import urllib.request
from devlib import ROOT, run

# Official 0.20.2 release archive digests. Keep this version in tools.lock.json.
ARCHIVES = {
    ("Linux", "aarch64"): (
        "aarch64-unknown-linux-musl",
        "995c82be0defc7a025cae49a2aa2644ce8245c9a3318fc4103907c6a285e8c7d",
    ),
    ("Linux", "x86_64"): (
        "x86_64-unknown-linux-musl",
        "9f12ed4c49936e09b48bf862b595cde2fe64fcbd9d74dfacac6131ca824c8d5f",
    ),
    ("Darwin", "arm64"): (
        "aarch64-apple-darwin",
        "fe67d82a10d8597a3549364cb733a3f9cc1bfff9031b7ae46384a9f2a72090c3",
    ),
    ("Darwin", "x86_64"): (
        "x86_64-apple-darwin",
        "248da7f581724e470071990c088ffc55c811981715f4cbdb258621fb79f8b7a6",
    ),
}


def install():
    version = json.loads((ROOT / "tools.lock.json").read_text())["cargo-deny"]
    if version != "0.20.2":
        raise RuntimeError("Update the reviewed cargo-deny archive digests")
    target, digest = ARCHIVES[(platform.system(), platform.machine())]
    archive = ROOT / f".cache/cargo-deny-{version}-{target}.tar.gz"
    if (
        not archive.exists()
        or hashlib.sha256(archive.read_bytes()).hexdigest() != digest
    ):
        url = f"https://github.com/EmbarkStudios/cargo-deny/releases/download/{version}/{archive.name}"
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise RuntimeError("cargo-deny archive checksum mismatch")
        archive.write_bytes(data)
    with tarfile.open(fileobj=io.BytesIO(archive.read_bytes())) as bundle:
        members = [
            m
            for m in bundle.getmembers()
            if m.isfile() and m.name.endswith("/cargo-deny")
        ]
        if len(members) != 1:
            raise RuntimeError("Expected one cargo-deny executable")
        data = bundle.extractfile(members[0]).read()
    binary = ROOT / ".tools/bin/cargo-deny"
    binary.parent.mkdir(parents=True, exist_ok=True)
    if not binary.exists() or binary.read_bytes() != data:
        binary.write_bytes(data)
        binary.chmod(0o755)
    return binary


def main():
    binary = install()
    failures = []
    for variant in ("core", "full"):
        command = [
            binary,
            "--config",
            ROOT / "deny.toml",
            "--locked",
            "--exclude-dev",
            "--target",
            "wasm32-unknown-emscripten",
            "--no-default-features",
        ]
        if variant == "full":
            command += ["--features", "ducklake"]
        try:
            run(*command, "check", "advisories", "--hide-inclusion-graph")
        except subprocess.CalledProcessError:
            failures.append(variant)
    try:
        run(
            "npm",
            "audit",
            "--package-lock-only",
            "--ignore-scripts",
            "--audit-level=low",
        )
    except subprocess.CalledProcessError:
        failures.append("Node tooling")
    if failures:
        raise SystemExit("Advisory checks failed: " + ", ".join(failures))
    print("PASS Rust core/full and Node dependency advisories")


if __name__ == "__main__":
    main()
