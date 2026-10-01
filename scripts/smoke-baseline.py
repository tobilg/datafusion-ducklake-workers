#!/usr/bin/env python3
"""Check the actual official Worker running locally, not a mock runtime."""

import json
import urllib.request

base = "http://127.0.0.1:8787"
expect = {
    "/sleep?ms=20": "slept 20ms",
    "/timeout": "fast: completed, slow: timed out",
    "/spawn": "[1, 4, 9]",
    "/mutex": "counter = 10",
    "/join": "abc",
}
for path, result in expect.items():
    with urllib.request.urlopen(base + path, timeout=10) as response:
        body = json.load(response)
    assert body["result"] == result, (path, body)
    if path.startswith("/sleep"):
        assert body["elapsed_ms"] >= 20, body
    print(json.dumps({"path": path, **body}))
