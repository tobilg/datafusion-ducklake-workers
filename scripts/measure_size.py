#!/usr/bin/env python3
"""Measure verified release modules through Wrangler without rebuilding them."""

import argparse
import contextlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from devlib import ROOT, REPORTS, read_jsonc


@contextlib.contextmanager
def measurement_config(source, variant, root=ROOT):
    root = root.resolve()
    source = Path(source).resolve()
    config = read_jsonc(source)
    if (source.parent / config["main"]).resolve() != root / f"build/{variant}/index.js":
        raise ValueError(
            "Configuration main must reference the selected production variant"
        )
    config.pop("build", None)
    # Keep the config beside the original so all relative paths retain their meaning.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonc", prefix=".wrangler-size-", dir=source.parent
    ) as handle:
        json.dump(config, handle)
        handle.flush()
        yield Path(handle.name)


def measure(source, variant):
    check = [
        sys.executable,
        str(ROOT / "scripts/check-artifacts.py"),
        "--variant",
        variant,
    ]
    subprocess.run(check, check=True, cwd=ROOT)
    with measurement_config(source, variant) as config, tempfile.TemporaryDirectory(
        prefix=f"size-{variant}-", dir=ROOT / ".cache"
    ) as output:
        command = [
            str(ROOT / "node_modules/.bin/wrangler"),
            "deploy",
            "--config",
            str(config),
            "--dry-run",
            "--outdir",
            output,
        ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        (REPORTS / f"release-size-{variant}.txt").write_text(result.stdout)
        print(result.stdout, end="", flush=True)
        result.check_returncode()
    # Reject input/module changes while Wrangler was reading the release.
    subprocess.run(check, check=True, cwd=ROOT)
    match = re.search(r"Total Upload: ([0-9.]+) KiB", result.stdout)
    if not match:
        raise RuntimeError("Missing Wrangler Total Upload; inspect the output")
    size = float(match[1]) / 1024
    print(f"Entire upload: {size:.2f} MiB; pilot headroom target <=56 MiB")
    if size > 56:
        raise RuntimeError("Bundle exceeds the 56 MiB pilot headroom target")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant", choices=("core", "full"))
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    try:
        measure(args.config, args.variant)
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
