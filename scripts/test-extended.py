#!/usr/bin/env python3
from devlib import REPORTS
import json, pathlib, sys, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

root = pathlib.Path(__file__).resolve().parents[1]
base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8790"
assert base in ("http://127.0.0.1:8790", "http://127.0.0.1:8791")
token = (root / ".cache/query-api-token").read_text()
cases = [
    (
        "compressed row groups",
        "SELECT count(*),sum(id),min(big),max(big) FROM compressed",
        200,
        [["12000", "72006000", "9223372036854775809", "9223372036854787808"]],
    ),
    (
        "compressed projection/filter",
        "SELECT id,big,label FROM compressed WHERE id BETWEEN 5999 AND 6002 ORDER BY id",
        200,
        [
            ["5999", "9223372036854781807", "Grüße 🌍"],
            ["6000", "9223372036854781808", None],
            ["6001", "9223372036854781809", "Grüße 🌍"],
            ["6002", "9223372036854781810", "Grüße 🌍"],
        ],
    ),
    (
        "large object small projection",
        "SELECT count(*),sum(id) FROM large_object",
        200,
        [["9000", "40504500"]],
    ),
    ("large object column budget", "SELECT label FROM large_object LIMIT 1", 422, None),
    (
        "partitions and evolved defaults",
        "SELECT category,count(*),sum(id),sum(extra) FROM partitioned GROUP BY category ORDER BY category",
        200,
        [["0", "50", "2550", "350"], ["1", "51", "2601", "359"]],
    ),
]
report = []
for name, sql, expected, rows in cases:
    req = Request(
        base + "/query",
        data=json.dumps({"sql": sql}).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    start = time.monotonic()
    try:
        with urlopen(req, timeout=20) as r:
            status = r.status
            result = json.load(r)
    except HTTPError as e:
        status = e.code
        result = json.load(e)
    assert status == expected, (name, status, result)
    if rows is not None:
        assert result["rows"] == rows, (name, result)
    report.append(
        {
            "test": name,
            "status": status,
            "request_id": result["request_id"],
            "wall_ms": round((time.monotonic() - start) * 1000, 3),
            "rows": result.get("rows"),
        }
    )
    print("PASS", name)
(
    root
    / ".cache/reports"
    / ("extended-s3.json" if base.endswith("8791") else "extended-r2.json")
).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
