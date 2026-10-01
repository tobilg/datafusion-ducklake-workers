#!/usr/bin/env python3
"""Validate the prerequisites for one workflow; never inspect credentials."""

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
pins = json.loads((root / "tools.lock.json").read_text())
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--profile",
    choices=["bootstrap", "build", "files", "catalog", "s3"],
    default="build",
)
args = parser.parse_args()
errors = []
if platform.system() not in ("Darwin", "Linux"):
    errors.append("Use macOS or Linux; Windows is not supported by the build scripts.")
if sys.version_info < (3, 12):
    errors.append("Select Python 3.12+ (3.14 was tested).")
commands = {
    "git": (["git", "--version"], None),
    "rustup": (["rustup", "--version"], None),
    "node": (["node", "--version"], "v" + pins["node"]),
    "npm": (["npm", "--version"], pins["npm"]),
    "ripgrep": (["rg", "--version"], None),
    "C compiler": (["cc", "--version"], None),
}
if args.profile != "bootstrap":
    commands["Rust"] = (
        ["rustc", "+" + pins["rust"]["channel"], "--version"],
        pins["rust"]["version"],
    )
if args.profile in ("files", "catalog"):
    commands["DuckDB"] = (["duckdb", "-init", "/dev/null", "--version"], "v1.5.5")
if args.profile == "catalog":
    commands["pnpm"] = (["pnpm", "--version"], pins["pnpm"])
if args.profile == "s3":
    commands["Go"] = (["go", "env", "GOVERSION"], "go" + pins["fixture_tools"]["go"])
for name, (command, expected) in commands.items():
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=20, check=True
        )
        actual = result.stdout.strip().splitlines()[0]
        if (
            expected
            and actual != expected
            and not (name == "DuckDB" and actual.startswith(expected + " "))
        ):
            errors.append(f"{name}: expected {expected}; found {actual}")
        else:
            print(f"OK {name}: {actual}")
    except (OSError, subprocess.SubprocessError, IndexError):
        errors.append(f"{name}: unavailable; install it and check PATH")
free = shutil.disk_usage(root).free / 1024**3
print(f"Free build space: {free:.1f} GiB; allow 20 GiB for an initial SDK/build.")
if free < 2:
    errors.append("Less than 2 GiB free; free build space before proceeding.")
for error in errors:
    print("ERROR " + error, file=sys.stderr)
if errors:
    raise SystemExit(1)
