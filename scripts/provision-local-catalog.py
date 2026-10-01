#!/usr/bin/env python3
"""Create only the loopback fixture registry/policy/credentials, not DuckLake metadata.

This intentionally cannot target a remote service. Never prints credentials.
Existing saved credentials are preserved; an unrelated existing catalog is an error.
"""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

root = Path(__file__).resolve().parents[1]
state = root / ".cache/local-catalog.json"
if state.exists():
    print("Existing fixture credential file preserved; no mutation performed.")
    raise SystemExit(0)
secrets = dict(
    line.split("=", 1)
    for line in (root / "fixtures/quacklake/.dev.vars").read_text().splitlines()
)


def request(method, path, body):
    req = Request(
        "http://127.0.0.1:8792" + path,
        method=method,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + secrets["ADMIN_TOKEN"],
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        # Responses can contain token-bearing attach SQL. Do not print them.
        raise SystemExit(f"Fixture {method} {path} failed: HTTP {error.code}") from None


catalog = "fixture"
path = "r2://quacklake-fixture/catalogs/fixture/"
created = request(
    "POST",
    "/admin/catalogs",
    {
        "catalogId": catalog,
        "r2Bucket": "quacklake-fixture",
        "dataAccessMode": "catalog_only",
        "scopes": ["catalog.admin"],
        "expiresInSeconds": 31536000,
    },
)
assert created["catalog"]["dataPath"] == path
# Save immediately, so a later policy failure cannot lose the bootstrap credential.
fd = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as output:
    json.dump(
        {
            "catalog_id": catalog,
            "data_path": path,
            "bootstrap_jwt": created["jwt"],
            "status": "registry_created",
        },
        output,
    )
policy = {
    "version": 1,
    "defaultEffect": "deny",
    "rules": [
        {
            "ruleId": "local-bootstrap",
            "effect": "allow",
            "principal": {"scopesAny": ["catalog.admin"]},
            "actions": ["*"],
            "resource": {"schema": "*", "table": "*", "column": "*"},
        },
        {
            "ruleId": "service-metadata-reader",
            "effect": "allow",
            "principal": {"scopesAny": ["query.read"]},
            "actions": ["schema.read", "table.read", "column.read"],
            "resource": {"schema": "*", "table": "*", "column": "*"},
        },
    ],
}
request("PUT", f"/admin/catalogs/{catalog}/auth-policy", policy)
reader = request(
    "POST",
    f"/admin/catalogs/{catalog}/credentials",
    {
        "scopes": ["query.read"],
        "expiresInSeconds": 31536000,
    },
)
values = json.loads(state.read_text())
values.update(
    reader_jwt=reader["jwt"],
    reader_credential_id=reader["credentialId"],
    status="registry_policy_credentials_created",
)
state.write_text(json.dumps(values))
print(
    "Local registry, policy and separate reader credential created. DuckLake metadata is NOT initialized."
)
