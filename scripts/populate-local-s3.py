#!/usr/bin/env python3
"""Upload the same exact keys/data to the real local S3 fixture using SigV4."""

import json
from devlib import ROOT as root, api_key, local_s3_put, update_secrets


def put(key, body):
    local_s3_put("quacklake-fixture", key, body)


put("", b"")
directory = root / ".cache/fixture-objects"
files = sorted(directory.rglob("*.parquet"))
assert files, "Generate actual Parquet first"
for file in files:
    key = file.relative_to(directory).as_posix()
    assert key.startswith("catalogs/fixture/")
    put(key, file.read_bytes())
state = json.loads((root / ".cache/local-catalog.json").read_text())
creds = json.loads((root / ".cache/minio-credentials.json").read_text())
update_secrets(
    root / "fixtures/s3/.dev.vars",
    {
        "QUACKLAKE_JWT": state["reader_jwt"],
        "API_KEY": api_key(),
        "S3_ACCESS_KEY_ID": creds["access_key"],
        "S3_SECRET_ACCESS_KEY": creds["secret_key"],
    },
)
print(
    f"Uploaded {len(files)} actual Parquet files at identical canonical keys; local S3 secrets saved without printing."
)
