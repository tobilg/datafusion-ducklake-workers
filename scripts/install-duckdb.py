#!/usr/bin/env python3
"""Install the pinned native fixture CLI locally, never in the Worker bundle."""

import hashlib
import io
import json
import os
import platform
import urllib.request
import zipfile
from devlib import ROOT

pins = json.loads((ROOT / "fixtures/duckdb.lock.json").read_text())
key = (platform.system() + "-" + platform.machine()).lower()
if key not in pins["platforms"]:
    raise SystemExit(
        "No pinned DuckDB fixture archive for "
        + key
        + "; install DuckDB 1.5.5 manually."
    )
entry = pins["platforms"][key]
with urllib.request.urlopen(entry["url"], timeout=60) as response:
    archive = response.read()
if hashlib.sha256(archive).hexdigest() != entry["sha256"]:
    raise SystemExit("DuckDB archive checksum mismatch")
with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
    data = bundle.read("duckdb")
path = ROOT / ".tools/bin/duckdb"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(data)
path.chmod(0o755)
print("Installed pinned local DuckDB fixture CLI.")
