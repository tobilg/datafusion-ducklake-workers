#!/usr/bin/env python3
"""Read release WASM linear-memory high-water allocation via a local diagnostic wrapper.
This is not total isolate memory or peak live allocator usage.
"""

from devlib import REPORTS
import argparse, json, pathlib
from urllib.request import Request, urlopen
from urllib.error import HTTPError

root = pathlib.Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("backend", choices=["r2", "s3"])
p.add_argument("--files", action="store_true")
p.add_argument("--variant", choices=["core", "full"], default="full")
a = p.parse_args()
key = (a.variant + "-files-" if a.files else "") + a.backend
port = (
    (18793 if a.backend == "r2" else 18794)
    if a.files
    else (18790 if a.backend == "r2" else 18791)
)
token = (root / ".cache/query-api-token").read_text()
cases = [
    ("initial health", None, 200),
    ("constant query with catalog", "SELECT 1", 200),
    ("sales with position deletes", "SELECT count(*),sum(id) FROM sales", 200),
    ("large object projection", "SELECT count(*),sum(id) FROM large_object", 200),
    (
        "pool exhaustion",
        "SELECT s.id AS a,t.id AS b FROM sales s CROSS JOIN sales t ORDER BY s.id+t.id",
        422,
    ),
    ("recovery", "SELECT 1", 200),
] + [("repeat sales " + str(i), "SELECT sum(id) FROM sales", 200) for i in range(10)]
if a.files:
    prefix = f"{a.backend}://files-a/"
    cases = [
        ("initial health", None, 200),
        ("constant query", "SELECT 1", 200),
        ("file aggregate", f"SELECT count(*),sum(id) FROM '{prefix}events/'", 200),
        (
            "large file projection",
            f"SELECT count(*),sum(id) FROM '{prefix}large.parquet'",
            200,
        ),
        (
            "pool exhaustion",
            f"SELECT s.id AS a,t.id AS b FROM '{prefix}events/' s CROSS JOIN '{prefix}events/' t ORDER BY s.id+t.id",
            422,
        ),
        ("recovery", "SELECT 1", 200),
    ] + [
        (f"repeat {i}", f"SELECT sum(id) FROM '{prefix}events/'", 200)
        for i in range(10)
    ]
observations = []
for name, sql, expected in cases:
    request = Request(
        f"http://127.0.0.1:{port}" + ("/query" if sql else "/healthz"),
        data=json.dumps({"sql": sql, "max_rows": 1}).encode() if sql else None,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    try:
        response = urlopen(request, timeout=20)
    except HTTPError as error:
        response = error
    with response:
        response.read()
        assert response.status == expected, (name, response.status)
        size = int(response.headers["X-Local-Wasm-Bytes"])
        observations.append(
            {"case": name, "status": response.status, "wasm_linear_bytes": size}
        )
        print(name, response.status, size)
report = json.loads(
    (root / ".cache" / ("memory-probe-" + key) / "source.json").read_text()
)
report.update(
    backend=a.backend,
    observations=observations,
    limitation="WASM linear allocation only; excludes JS heap, compiled code, native host allocations and total isolate memory.",
)
(root / ".cache/reports" / ("wasm-memory-" + key + ".json")).write_text(
    json.dumps(report, indent=2) + "\n"
)
