#!/usr/bin/env python3
"""Read-only acceptance against an explicitly supplied HTTPS deployment.
Token comes from the environment, never argv/output. Does not create resources.
"""

import argparse, json, os, time, urllib.parse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

p = argparse.ArgumentParser()
p.add_argument("--url", required=True)
p.add_argument("--fixture", required=True, help="JSON file with sql and expected_rows")
p.add_argument("--repeat", type=int, default=5)
a = p.parse_args()
url = urllib.parse.urlsplit(a.url)
assert url.scheme == "https" and url.hostname and not url.username and not url.password
assert url.path in ("", "/") and not url.query and not url.fragment
assert 1 <= a.repeat <= 50
token = os.environ["API_KEY"]
assert 32 <= len(token) <= 4096


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = build_opener(NoRedirect())
with open(a.fixture) as f:
    fixture = json.load(f)
body = json.dumps({"sql": fixture["sql"]}).encode()
assert len(body) <= 32768


def call(path, data=None):
    request = Request(
        a.url.rstrip("/") + path,
        data=data,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    start = time.monotonic()
    try:
        with opener.open(request, timeout=20) as response:
            result = response.read(1048577)
            assert len(result) <= 1048576
            value = json.loads(result)
    except HTTPError as error:
        raise SystemExit(
            f"Acceptance failed: HTTP {error.code} at {path}; upstream body withheld"
        ) from None
    return value, round((time.monotonic() - start) * 1000, 3), len(result)


ready, _, _ = call("/readyz")
assert ready["ready"]
results = []
for i in range(a.repeat):
    value, wall, size = call("/query", body)
    assert (
        value["rows"] == fixture["expected_rows"] and not value["truncated"]
    ), "Acceptance result mismatch"
    results.append(
        {
            "iteration": i,
            "request_id": value["request_id"],
            "row_count": value["row_count"],
            "wall_ms": wall,
            "response_bytes": size,
        }
    )
print(
    json.dumps(
        {
            "url": a.url,
            "results": results,
            "scope": "Client wall times; correlate request IDs with platform metrics. First query follows readiness and is not a proven cold start.",
        },
        indent=2,
    )
)
