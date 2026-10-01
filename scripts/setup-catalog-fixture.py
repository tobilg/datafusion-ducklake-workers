#!/usr/bin/env python3
"""Initialize only the local test catalog. Preserve completed or partial fixtures."""

import json
import sys
from devlib import ROOT, run

run(sys.executable, "scripts/provision-local-catalog.py")
state = json.loads((ROOT / ".cache/local-catalog.json").read_text())
if not state.get("reader_jwt"):
    raise SystemExit(
        "Incomplete local registry provisioning; inspect .cache/local-catalog.json before retrying."
    )
manifest = ROOT / "repro/catalog-native/Cargo.toml"
run("cargo", "build", "--locked", "--manifest-path", manifest)
probe = ROOT / "repro/catalog-native/target/debug/quacklake-native-probe"
progress = ROOT / ".cache/catalog-setup.json"
if progress.exists():
    completed = json.loads(progress.read_text())
elif (ROOT / ".cache/fixture-objects").exists():
    # Older/manual fixtures have no journal. Verify them without re-seeding.
    run(probe, "read")
    run(probe, "attach")
    run(probe, "read_inline")
    print(
        "Preserved existing manually prepared catalog; tests will verify its objects."
    )
    raise SystemExit(0)
else:
    completed = []
for phase in ["missing", "initialize", "read", "attach", "seed", "extended", "inline"]:
    if phase in completed:
        continue
    run(probe, phase)
    completed.append(phase)
    progress.write_text(json.dumps(completed) + "\n")
print(
    "Local catalog fixture initialized; repeated runs preserve metadata and credentials."
)
