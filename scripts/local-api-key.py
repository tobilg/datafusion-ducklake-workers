#!/usr/bin/env python3
"""Prepare a local caller key without replacing a different configured key."""

from devlib import ROOT, api_key, update_secrets

path = ROOT / ".dev.vars"
existing = {}
if path.exists():
    existing = dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
key = api_key()
configured = existing.get("API_KEY", "").strip().strip("\"'")
if configured and configured != key:
    raise SystemExit(
        "Existing .dev.vars API_KEY differs from the local fixture key; both were preserved. Use your configured key or reconcile them explicitly."
    )
update_secrets(path, {"API_KEY": key})
print(
    "Local API_KEY saved to ignored .dev.vars; value is in .cache/query-api-token (0600)."
)
