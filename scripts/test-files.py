#!/usr/bin/env python3
"""Exercise compiled Workers against real local R2/S3/HTTP Parquet objects."""

from devlib import REPORTS
import argparse, concurrent.futures, hashlib, json, pathlib, threading, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

p = argparse.ArgumentParser()
p.add_argument("--port", type=int, default=8793)
p.add_argument("--backend", choices=["r2", "s3", "unsigned"], default="r2")
p.add_argument("--variant", choices=["core", "full"], default="core")
a = p.parse_args()
assert 8793 <= a.port <= 8797
root = pathlib.Path(__file__).resolve().parents[1]
base = f"http://127.0.0.1:{a.port}"


def artifact_hashes():
    return {
        key: hashlib.sha256(
            (root / f"build/{a.variant}/{name}").read_bytes()
        ).hexdigest()
        for key, name in [("wasm_sha256", "index_bg.wasm"), ("js_sha256", "index.js")]
    }


tested_artifacts = artifact_hashes()
token = (root / ".cache/query-api-token").read_text().strip()
results = []
reference = json.loads((root / ".cache/reports/file-reference.json").read_text())[
    "expected"
]
scheme = "r2" if a.backend == "r2" else "s3"
events = f"{scheme}://files-a/events/"
categories = f"{scheme}://files-b/categories.parquet"


def request(body=None, path="/query", auth=True):
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = "Bearer " + token
    req = Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
    )
    start = time.monotonic()
    try:
        with urlopen(req, timeout=20) as r:
            code = r.status
            raw = r.read()
    except HTTPError as e:
        code = e.code
        raw = e.read()
    assert len(raw) <= 1048576
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw.decode()
    return code, value, round((time.monotonic() - start) * 1000, 3), len(raw)


def check(name, sql=None, status=200, rows=None, verify=None, body=None, **kwargs):
    code, value, ms, size = request(
        body if body is not None else ({"sql": sql} if sql else None), **kwargs
    )
    assert code == status, (name, code, value)
    if rows is not None:
        assert value["rows"] == rows, (name, value)
    if verify:
        assert verify(value), (name, value)
    results.append(
        {
            "test": name,
            "status": code,
            "wall_ms": ms,
            "response_bytes": size,
            **(
                {"request_id": value["request_id"]}
                if isinstance(value, dict) and "request_id" in value
                else {}
            ),
        }
    )
    print("PASS", name, code, flush=True)


check("liveness", path="/healthz", auth=False)
check("unauthorized", "SELECT 1", status=401, auth=False)
check("ready without QuackLake", path="/readyz", verify=lambda v: v["ready"])
check("select one", "SELECT 1", rows=[["1"]])
check(
    "production diagnostics absent", path="/__spike/select-one", auth=False, status=404
)
check(
    "quoted S3/R2 prefix",
    f"SELECT count(*),sum(id) FROM '{events}'",
    rows=reference["aggregate"],
)
check(
    "single object",
    f"SELECT count(*),sum(id) FROM '{events}part0.parquet'",
    rows=reference["part0"],
)
check(
    "cross-bucket join",
    f"SELECT c.name,count(*),sum(e.id) FROM '{events}' e JOIN '{categories}' c ON e.category=c.id GROUP BY c.name ORDER BY c.name",
    rows=reference["join"],
)
check(
    "CTE and subquery",
    f"WITH x AS (SELECT id FROM '{events}' WHERE id<3) SELECT id,(SELECT max(id) FROM '{categories}') FROM x ORDER BY id",
    rows=[["1", "3"], ["2", "3"]],
)
nested = "SELECT 1 AS n"
for i in range(4):
    nested = f"SELECT * FROM ({nested}) t{i}"
check("nested subquery stack regression", nested, rows=[["1"]])
for i in range(4, 16):
    nested = f"SELECT * FROM ({nested}) t{i}"
check("planner query nesting cap", nested, status=422)
check("planner expression nesting cap", "SELECT " + "+".join(["1"] * 80), status=422)
check("recovery after nesting rejection", "SELECT 1", rows=[["1"]])
check("parser depth limit", "SELECT " + ("abs(" * 100) + "1" + (")" * 100), status=400)
check(
    "types and nulls",
    f"SELECT id,big,amount,label FROM '{events}' ORDER BY id LIMIT 3",
    rows=[
        ["1", "9007199254740993", "1.25", "Grüße 🌍"],
        ["2", "9007199254740994", "2.50", "Grüße 🌍"],
        ["3", "9007199254740995", "3.75", None],
    ],
)
check(
    "Unicode and percent",
    f"SELECT count(*),sum(id) FROM '{scheme}://files-a/Gr%C3%BC%C3%9Fe%20100%25.parquet'",
    rows=reference["part0"],
)
check(
    "large object projected ranges",
    f"SELECT count(*),sum(id) FROM '{scheme}://files-a/large.parquet'",
    rows=reference["large"],
)
check(
    "empty Parquet rejected",
    f"SELECT count(*) FROM '{scheme}://files-a/empty.parquet'",
    status=503,
)
check(
    "empty prefix rejected",
    f"SELECT count(*) FROM '{scheme}://files-a/absent/'",
    status=503,
)
check(
    "missing object", f"SELECT * FROM '{scheme}://files-a/missing.parquet'", status=503
)
check(
    "incompatible prefix schemas",
    f"SELECT count(*) FROM '{scheme}://files-a/incompatible/'",
    status=503,
)
check(
    "encoded response cap",
    f"SELECT label FROM '{scheme}://files-a/wide.parquet'",
    status=422,
)
if a.backend == "unsigned":
    check(
        "anonymous private bucket denied",
        "SELECT * FROM 's3://files-private/private.parquet'",
        status=503,
    )
if a.backend == "s3":
    check(
        "signed private bucket",
        "SELECT count(*),sum(id) FROM 's3://files-private/private.parquet'",
        rows=reference["part0"],
    )
if a.backend == "r2":
    check("missing R2 binding", "SELECT * FROM 'r2://unbound/file.parquet'", status=503)
for name in ["data", "nohead", "redirect"]:
    check(
        "HTTP " + name,
        f"SELECT count(*),sum(id) FROM 'http://127.0.0.1:8798/{name}.parquet'",
        rows=reference["part0"],
    )
check(
    "HTTP URL query preserved",
    "SELECT count(*) FROM 'http://127.0.0.1:8798/signed.parquet?fixture_token=not-a-real-secret'",
    rows=[["2500"]],
)
check(
    "mixed transport join",
    f"SELECT count(*) FROM '{events}' e JOIN 'http://127.0.0.1:8798/data.parquet' h ON e.id=h.id",
    rows=[["2500"]],
)
public = json.loads((root / "fixtures/files/public-https.json").read_text())
check(
    "public HTTPS Parquet",
    f"SELECT count(*),sum(id) FROM '{public['url']}'",
    rows=public["expected"],
)
check(
    "large HTTP ranges",
    "SELECT count(*),sum(id) FROM 'http://127.0.0.1:8798/large.parquet'",
    rows=reference["large"],
)
for name in [
    "ignore",
    "bad-range",
    "changed",
    "truncated",
    "missing",
    "private-redirect",
]:
    check(
        "HTTP rejects " + name,
        f"SELECT sum(id) FROM 'http://127.0.0.1:8798/{name}.parquet'",
        status=503,
    )
check(
    "redirect budget",
    "SELECT sum(id) FROM 'http://127.0.0.1:8798/loop.parquet'",
    status=422,
)
for index, sql in enumerate(
    [
        "SELECT 1; SELECT 2",
        "DELETE FROM x",
        "CREATE EXTERNAL TABLE x STORED AS PARQUET LOCATION 's3://files-a/events/'",
        "COPY (SELECT 1) TO 's3://files-a/out.parquet'",
        "SELECT * FROM 'file:///tmp/a.parquet'",
        "SELECT * FROM read_parquet('s3://files-a/events/')",
        "WITH x AS (SELECT * FROM 'file:///tmp/a.parquet') SELECT * FROM x",
        "SELECT * FROM 'ftp://example.com/a.parquet'",
        "SELECT * FROM 'https://169.254.169.254/a.parquet'",
        "SELECT * FROM 'https://user:secret@example.com/a.parquet'",
        "SELECT * FROM 's3://files-a/events/../events/part0.parquet'",
        "SELECT * FROM 's3://files-a/events/%2e%2e/events/part0.parquet'",
        "SELECT * FROM 's3://files-a/a.csv'",
        "SELECT * FROM 'https://example.com/a.json'",
        "SELECT * FROM other.main.x",
    ]
):
    check("source/write restriction " + str(index), sql, status=403)
check("unknown request field", body={"sql": "SELECT 1", "sources": []}, status=400)
check(
    "source count cap",
    " UNION ALL ".join(f"SELECT 1 FROM 's3://files-a/{i}.parquet'" for i in range(17)),
    status=422,
)
check(
    "row cap",
    body={"sql": f"SELECT id FROM '{events}' ORDER BY id", "max_rows": 3},
    verify=lambda v: v["rows"] == [["1"], ["2"], ["3"]] and v["truncated"],
)
check(
    "deadline",
    body={"sql": f"SELECT sum(id) FROM '{events}'", "timeout_ms": 1},
    status=504,
)
check("permit after deadline", "SELECT 1", rows=[["1"]])
barrier = threading.Barrier(4)


def compete(_):
    barrier.wait()
    return request({"sql": f"SELECT sum(id) FROM '{events}'"})[0]


with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    codes = list(pool.map(compete, range(4)))
assert 200 in codes and 429 in codes and set(codes) <= {200, 429}, codes
results.append({"test": "concurrency", "statuses": codes})
for i in range(20):
    check("repeated " + str(i), f"SELECT sum(id) FROM '{events}'", rows=[["12502500"]])
assert (
    artifact_hashes() == tested_artifacts
), "Artifacts changed during tests; restart the Worker and rerun"
report = {
    "variant": a.variant,
    "backend": a.backend,
    "scope": "local compiled Emscripten Worker; wall time, not platform CPU",
    **tested_artifacts,
    "results": results,
}
(root / f".cache/reports/files-{a.variant}-{a.backend}.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print("Passed", len(results), "checks")
