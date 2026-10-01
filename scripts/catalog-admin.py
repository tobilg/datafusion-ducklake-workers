#!/usr/bin/env python3
"""Explicit operator actions; credentials come from environment and go to 0600 files.
Never invoked by build/deploy scripts. Never installs or changes catalog policy.
"""

import argparse, json, os, pathlib, re
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

p = argparse.ArgumentParser()
p.add_argument(
    "action", choices=["buckets", "create-registry", "issue-reader", "revoke"]
)
p.add_argument("--url", required=True)
p.add_argument("--catalog")
p.add_argument("--bucket")
p.add_argument("--credential-id")
p.add_argument("--output")
a = p.parse_args()
from urllib.parse import urlsplit

u = urlsplit(a.url)
assert (
    u.scheme == "https"
    and u.hostname
    and not u.username
    and not u.password
    and u.path in ("", "/")
    and not u.query
    and not u.fragment
)
if a.action != "buckets":
    assert a.catalog and re.fullmatch("[A-Za-z0-9_-]{1,128}", a.catalog)
if a.action in ("create-registry", "issue-reader"):
    assert a.output, "A new protected output file is required"
    assert not pathlib.Path(
        a.output
    ).exists(), "Refusing to overwrite credential material"
    pathlib.Path(a.output).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
method = "GET"
path = "/admin/r2-buckets"
body = None
if a.action == "create-registry":
    assert a.bucket and re.fullmatch("[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", a.bucket)
    method = "POST"
    path = "/admin/catalogs"
    body = {
        "catalogId": a.catalog,
        "r2Bucket": a.bucket,
        "dataAccessMode": "catalog_only",
        "scopes": ["catalog.admin"],
        "expiresInSeconds": 31536000,
    }
elif a.action == "issue-reader":
    method = "POST"
    path = f"/admin/catalogs/{a.catalog}/credentials"
    body = {"scopes": ["query.read"], "expiresInSeconds": 31536000}
elif a.action == "revoke":
    assert a.credential_id and re.fullmatch("[A-Za-z0-9_-]+", a.credential_id)
    method = "DELETE"
    path = f"/admin/catalogs/{a.catalog}/credentials/{a.credential_id}"
token = os.environ["QUACKLAKE_ADMIN_TOKEN"]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = build_opener(NoRedirect())
req = Request(
    a.url.rstrip("/") + path,
    method=method,
    data=json.dumps(body).encode() if body else None,
    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
)
try:
    with opener.open(req, timeout=20) as r:
        data = r.read()
        status = r.status
except HTTPError as e:
    raise SystemExit(
        f"Catalog admin action failed: HTTP {e.code}; no policy was changed"
    ) from None
if a.output:
    fd = os.open(a.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if a.action == "create-registry":
        assert (
            json.loads(data)["catalog"]["dataPath"]
            == f"r2://{a.bucket}/catalogs/{a.catalog}/"
        )
    print(
        f"{a.action} succeeded; credential response saved securely. Actual DuckLake metadata was not initialized."
    )
elif a.action == "buckets":
    print(data.decode())
else:
    print(
        f"{a.action} succeeded: HTTP {status}. Existing signed sessions may remain valid."
    )
