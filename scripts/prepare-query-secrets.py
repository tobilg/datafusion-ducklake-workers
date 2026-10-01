#!/usr/bin/env python3
"""Install local caller/catalog credentials; never print values."""

import json
from devlib import ROOT, api_key, update_secrets

values = {"API_KEY": api_key()}
state = json.loads((ROOT / ".cache/local-catalog.json").read_text())
values["QUACKLAKE_JWT"] = state["reader_jwt"]
update_secrets(ROOT / "fixtures/.dev.vars", values)
if (ROOT / "fixtures/s3/.dev.vars").exists():
    update_secrets(ROOT / "fixtures/s3/.dev.vars", values)
print("Local credentials prepared; existing caller and S3 values preserved.")
