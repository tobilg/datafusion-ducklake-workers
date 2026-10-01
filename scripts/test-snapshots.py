#!/usr/bin/env python3
"""Local fixture only: inlined reads and a metadata commit during a long query.
Run native fixture mode `inline` once before this test. Each run adds one table.
"""

from devlib import REPORTS
import concurrent.futures, json, pathlib, subprocess, time
from urllib.request import Request, urlopen

root = pathlib.Path(__file__).resolve().parents[1]
token = (root / ".cache/query-api-token").read_text()
report = []
table = "snapshot_" + str(time.time_ns())


def query(sql, port=8790):
    start = time.monotonic()
    req = Request(
        f"http://127.0.0.1:{port}/query",
        data=json.dumps({"sql": sql}).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=20) as r:
        result = json.load(r)
    report.append(
        {
            "sql": sql,
            "port": port,
            "request_id": result["request_id"],
            "rows": result["rows"],
            "start": start,
            "end": time.monotonic(),
        }
    )
    return result["rows"]


before = int(query("SELECT count(*) FROM inline_probe")[0][0])
assert query("SELECT count(*) FROM inline_probe", 8791) == [[str(before)]]
assert query("SELECT id,label FROM inline_probe WHERE id=1") == [["1", None]]
# Enough streamed work for the local native writer to commit during execution;
# the smaller 54-million-row workload can finish before a 500 ms fixed delay.
sql = "SELECT sum(a.id * b.id), (SELECT count(*) FROM inline_probe) FROM compressed a CROSS JOIN compressed b"
with concurrent.futures.ThreadPoolExecutor(1) as executor:
    pending = executor.submit(query, sql)
    time.sleep(0.05)
    mutation_start = time.monotonic()
    writer = subprocess.run(
        [
            str(root / "repro/catalog-native/target/debug/quacklake-native-probe"),
            "advance",
            table,
        ],
        capture_output=True,
        timeout=20,
    )
    assert (
        writer.returncode == 0
    ), "Local writer failed; output withheld to protect fixture credentials"
    mutation_end = time.monotonic()
    rows = pending.result()
assert rows == [[str(sum(range(1, 12001)) ** 2), str(before)]], rows
assert query(f"SELECT id,label FROM {table}") == [["7", None]]
assert query(f"SELECT id,label FROM {table}", 8791) == [["7", None]]
assert query("SELECT count(*) FROM inline_probe") == [[str(before)]]
assert query("SELECT id,label FROM inline_probe WHERE id=1") == [["1", None]]
long = next(r for r in report if r["sql"] == sql)
assert long["start"] < mutation_start < mutation_end < long["end"], (
    "Commit did not overlap the query; no concurrent-snapshot claim",
    {
        "query_ms": round((long["end"] - long["start"]) * 1000, 3),
        "commit_start_ms": round((mutation_start - long["start"]) * 1000, 3),
        "commit_end_ms": round((mutation_end - long["start"]) * 1000, 3),
    },
)
for r in report:
    r["wall_ms"] = round((r.pop("end") - r.pop("start")) * 1000, 3)
(root / ".cache/reports/snapshots.json").write_text(
    json.dumps(
        {
            "queries": report,
            "concurrent_commit_ms": round((mutation_end - mutation_start) * 1000, 3),
            "scope": "Local metadata table creation overlaps a successful read; next requests see new inlined table. Correlate snapshot IDs in Worker logs; does not simulate concurrent deletion/maintenance.",
        },
        indent=2,
    )
    + "\n"
)
print(
    "PASS metadata commit overlaps successful query; subsequent requests in both modes read the new inlined table"
)
