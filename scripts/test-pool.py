#!/usr/bin/env python3
"""Force work much larger than the result limit; require a bounded failure and recovery."""

from devlib import REPORTS
import json, pathlib, sys, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

root = pathlib.Path(__file__).resolve().parents[1]
base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8790"
assert base in ("http://127.0.0.1:8790", "http://127.0.0.1:8791")
token = (root / ".cache/query-api-token").read_text()


def query(sql):
    req = Request(
        base + "/query",
        data=json.dumps({"sql": sql, "max_rows": 1}).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    start = time.monotonic()
    try:
        with urlopen(req, timeout=25) as r:
            status = r.status
            value = json.load(r)
    except HTTPError as e:
        status = e.code
        value = json.load(e)
    return status, value, round((time.monotonic() - start) * 1000, 3)


status, value, elapsed = query(
    "SELECT s.id AS a,t.id AS b FROM sales s CROSS JOIN sales t ORDER BY s.id+t.id"
)
assert status in (422, 504), (status, value)
recovery, rows, _ = query("SELECT 1")
assert recovery == 200 and rows["rows"] == [["1"]], (recovery, rows)
path = (
    root
    / ".cache/reports"
    / ("pool-s3.json" if base.endswith("8791") else "pool-r2.json")
)
path.write_text(
    json.dumps(
        {
            "test": "20.25 million-row cross join/sort with max_rows=1",
            "status": status,
            "error": value["error"],
            "wall_ms": elapsed,
            "recovery_status": recovery,
        },
        indent=2,
    )
    + "\n"
)
print("PASS bounded expensive query and permit recovery:", status, elapsed, "ms")
