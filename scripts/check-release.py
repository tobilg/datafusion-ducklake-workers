#!/usr/bin/env python3
"""Check publication candidates without printing potential secret values."""

import json
from pathlib import Path
import re
import subprocess
from devlib import ROOT, patch_series
from release_checks import secret_findings

paths = [
    ROOT / name
    for name in subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
    )
    .decode()
    .split("\0")
    if name
]
errors = []
for path in paths:
    if not path.is_file():
        continue
    name = path.relative_to(ROOT).as_posix()
    if any(
        part in {".cache", ".secrets", "node_modules", "target", "vendor"}
        for part in Path(name).parts
    ):
        errors.append(f"Generated/private path: {name}")
    if (
        ".dev.vars" in name or Path(name).name.startswith(".env")
    ) and not name.endswith(".example"):
        errors.append(f"Secret file: {name}")
    if path.suffix in {".wasm", ".map", ".cpuprofile", ".heapsnapshot"}:
        errors.append(f"Generated artifact: {name}")
    try:
        text = path.read_text()
    except UnicodeError:
        errors.append(f"Unexpected binary: {name}")
        continue
    for finding in secret_findings(text, name):
        errors.append(f"{finding} in {name} (value withheld)")
    if path.suffix == ".md":
        for link in re.findall(r"\]\(([^)]+)\)", text):
            target = link.split("#")[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (path.parent / target).exists():
                errors.append(f"Broken link in {name}: {target}")
series = patch_series()
if len(series) != len(set(name for name, _ in series)):
    errors.append("Duplicate patch in series.json")
if {name for name, _ in series} != {p.name for p in (ROOT / "patches").glob("*.patch")}:
    errors.append("Patch series differs from active patch files")
package = json.loads((ROOT / "package.json").read_text())
lock = json.loads((ROOT / "package-lock.json").read_text())["packages"][""]
if package["devDependencies"] != lock["devDependencies"]:
    errors.append("npm dependencies differ from lockfile")
if (ROOT / "docs/evidence").exists():
    errors.append("Obsolete evidence directory must not be recreated")
if errors:
    raise SystemExit("\n".join(errors))
print(
    f"PASS publication hygiene: {len(paths)} candidates; local links and active patch series checked"
)
