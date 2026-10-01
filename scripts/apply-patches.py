#!/usr/bin/env python3
"""Apply reviewed compatibility patches exactly; refuse conflicting edits."""

from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
from devlib import patch_series

for name, checkout in patch_series():
    patch = root / "patches" / name
    command = [
        "git",
        "-C",
        str(root),
        "apply",
        "--directory=" + str(Path("vendor") / checkout),
    ]
    if (
        subprocess.run(
            command + ["--reverse", "--check", str(patch)], capture_output=True
        ).returncode
        == 0
    ):
        print(f"Already applied: {name}")
        continue
    subprocess.run(command + ["--check", str(patch)], check=True)
    subprocess.run(command + [str(patch)], check=True)
    print(f"Applied: {name}")
