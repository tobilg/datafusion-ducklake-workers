#!/usr/bin/env python3
"""Real local Worker + QuackLake + Parquet integration, without logging credentials."""

from devlib import REPORTS
import concurrent.futures, datetime, json, pathlib, sys, threading, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

root = pathlib.Path(__file__).resolve().parents[1]
base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8790"
assert base in (
    "http://127.0.0.1:8790",
    "http://127.0.0.1:8791",
), "Local fixture URLs only"
token = (root / ".cache/query-api-token").read_text()
results = []


def request(body=None, path="/query", auth=token, raw=None):
    headers = {"Content-Type": "application/json"}
    if auth is not None:
        headers["Authorization"] = "Bearer " + auth
    data = (
        raw
        if raw is not None
        else (json.dumps(body).encode() if body is not None else None)
    )
    req = Request(base + path, data=data, headers=headers)
    start = time.monotonic()
    try:
        with urlopen(req, timeout=20) as r:
            status = r.status
            b = r.read()
            h = r.headers
    except HTTPError as e:
        status = e.code
        b = e.read()
        h = e.headers
    assert len(b) <= 1048576, (status, len(b))
    return (
        status,
        json.loads(b) if h.get_content_type() == "application/json" else b.decode(),
        (time.monotonic() - start) * 1000,
        len(b),
    )


def check(name, body=None, status=200, verify=None, **kwargs):
    code, value, elapsed, size = request(body, **kwargs)
    assert code == status, (name, code, value)
    if verify:
        assert verify(value), (name, value)
    results.append(
        {
            "test": name,
            "status": code,
            "wall_ms": round(elapsed, 3),
            "response_bytes": size,
        }
    )
    print("PASS", name, code)
    return value


check("liveness", path="/healthz", auth=None)
check("probe routes absent", path="/__spike/catalog", status=404, auth=None)
check("missing caller", {"sql": "SELECT 1"}, status=401, auth=None)
check("wrong caller", {"sql": "SELECT 1"}, status=401, auth="invalid")
check("ready", path="/readyz", verify=lambda v: v["ready"])
check(
    "select one",
    {"sql": "SELECT 1 AS value"},
    verify=lambda v: v["rows"] == [["1"]] and not v["truncated"],
)
check("unknown field", {"sql": "SELECT 1", "endpoint": "http://invalid"}, status=400)
check("body cap", raw=b" " * 32769, status=413)
check("SQL cap", {"sql": "SELECT " + (" " * 16384)}, status=400)
check("cannot raise row cap", {"sql": "SELECT 1", "max_rows": 1001}, status=400)
check("cannot raise timeout", {"sql": "SELECT 1", "timeout_ms": 10001}, status=400)
for i, sql in enumerate(
    [
        "SELECT 1; SELECT 2",
        "DELETE FROM lake.main.sales",
        "CREATE TABLE x(a INT)",
        "ATTACH 'ducklake:quack:quack:evil:443' AS other",
        "COPY (SELECT 1) TO 'r2://elsewhere/x'",
        "SET target_partitions=8",
        "SELECT * FROM read_parquet('https://example.com/x')",
        "SELECT * FROM ducklake_scan('file:///tmp/x')",
        "SELECT * FROM other.main.sales",
        "WITH x AS (SELECT * FROM other.main.sales) SELECT * FROM x",
        'SELECT * FROM "https://example.com/x.parquet"',
        "SELECT 1 INTO forbidden",
        "WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT * FROM x) SELECT * FROM x",
        "SELECT repeat('x',1000000000)",
        "SELECT * FROM sales FOR UPDATE",
        "SELECT 'a' || 'b'",
        "WITH x AS (SELECT 'a' AS s) SELECT s || s FROM x",
    ]
):
    check("read-only/source " + str(i), {"sql": sql}, status=403)
check("invalid SQL", {"sql": "SELECT FROM"}, status=400)
check(
    "aggregate and deletes",
    {"sql": "SELECT count(*) AS n, sum(id) AS s FROM lake.main.sales"},
    verify=lambda v: v["rows"] == [["4500", "11250000"]],
)
check(
    "bounded ordered projection",
    {
        "sql": "SELECT id,large_int,amount,label,happened_at FROM sales ORDER BY id",
        "max_rows": 3,
    },
    verify=lambda v: v["rows"]
    == [
        ["1", "9007199254740993", "1.25", "Grüße 🌍", "1788266096000000000"],
        ["2", "9007199254740994", "2.50", "Grüße 🌍", "1788266096000000000"],
        ["3", "9007199254740995", "3.75", None, "1788266096000000000"],
    ]
    and v["truncated"],
)
check(
    "SQL limit semantics",
    {"sql": "SELECT id FROM sales ORDER BY id LIMIT 2 OFFSET 8", "max_rows": 2},
    verify=lambda v: v["rows"] == [["9"], ["11"]] and not v["truncated"],
)
expected = []
for c in range(4):
    ids = [i for i in range(1, 5001) if i % 10 and i % 4 == c]
    expected.append(["group-" + str(c), str(len(ids)), str(sum(ids))])
check(
    "join and grouped aggregate",
    {
        "sql": "SELECT c.name,count(*) AS n,sum(s.id) AS s FROM sales s JOIN categories c ON s.category=c.id GROUP BY c.name ORDER BY c.name"
    },
    verify=lambda v: v["rows"] == expected,
)
check(
    "CTE and scalar subquery",
    {
        "sql": "WITH x AS (SELECT id FROM sales WHERE id<4) SELECT id,(SELECT max(id) FROM categories) AS c FROM x ORDER BY id"
    },
    verify=lambda v: v["rows"] == [["1", "3"], ["2", "3"], ["3", "3"]],
)
nested = "SELECT 1 AS n"
for i in range(16):
    nested = f"SELECT * FROM ({nested}) t{i}"
check("planner query nesting cap", {"sql": nested}, status=422)
check(
    "planner expression nesting cap",
    {"sql": "SELECT " + "+".join(["1"] * 80)},
    status=422,
)
check(
    "recovery after nesting rejection",
    {"sql": "SELECT 1"},
    verify=lambda v: v["rows"] == [["1"]],
)
check("nonfinite rejected", {"sql": "SELECT CAST('NaN' AS DOUBLE)"}, status=422)
check(
    "nested rejected",
    {"sql": "SELECT CAST(NULL AS BIGINT[]) AS nested_value"},
    status=422,
)
check(
    "binary and unsigned encoding",
    {
        "sql": "SELECT X'0001fe' AS binary, CAST(9007199254740993 AS BIGINT UNSIGNED) AS unsigned"
    },
    verify=lambda v: v["rows"] == [["AAH+", "9007199254740993"]],
)
wide = "x" * 7000
check(
    "byte cap truncates",
    {"sql": f"SELECT '{wide}' AS s FROM sales"},
    verify=lambda v: v["truncated"] and 1 < v["row_count"] < 1000,
)
columns = ",".join(f"s AS c{i}" for i in range(180))
check(
    "single oversized row",
    {"sql": f"WITH x AS (SELECT '{wide}' AS s) SELECT {columns} FROM x"},
    status=422,
)
check(
    "deadline cleanup",
    {"sql": "SELECT count(*) FROM sales", "timeout_ms": 1},
    status=504,
)
check(
    "permit reusable after timeout",
    {"sql": "SELECT 1"},
    verify=lambda v: v["rows"] == [["1"]],
)
barrier = threading.Barrier(8)


def compete(_):
    barrier.wait()
    return request({"sql": "SELECT sum(s.id) FROM sales s CROSS JOIN categories c"})[0]


with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    codes = list(pool.map(compete, range(8)))
assert 200 in codes and 429 in codes and set(codes) <= {200, 429}, codes
results.append({"test": "isolate concurrency", "statuses": codes})
for i in range(10):
    check(
        "repeated query " + str(i),
        {"sql": "SELECT sum(id) FROM sales"},
        verify=lambda v: v["rows"] == [["11250000"]],
    )
out = (
    root
    / ".cache/reports"
    / ("api-s3.json" if base.endswith("8791") else "api-r2.json")
)
out.write_text(
    json.dumps(
        {
            "url": base,
            "results": results,
            "scope": "local workerd; wall times are not CPU or total isolate memory",
        },
        indent=2,
    )
    + "\n"
)
print("All", len(results), "checks passed; evidence:", out.relative_to(root))
