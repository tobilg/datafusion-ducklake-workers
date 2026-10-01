#!/usr/bin/env python3
"""Run immediately after explicitly reloading both fixtures, before API tests.
Measures client wall time only; no inference about deployed CPU/startup/memory.
"""

from devlib import REPORTS
import hashlib, json, pathlib, time
from urllib.request import Request, urlopen

root = pathlib.Path(__file__).resolve().parents[1]
token = (root / ".cache/query-api-token").read_text()
report = {
    "scope": "First query after explicit local fixture reload, followed by five repeats. Client wall times include local catalog and storage. Not deployed cold-start/CPU measurements.",
    "wasm_sha256": hashlib.sha256(
        (root / "build/full/index_bg.wasm").read_bytes()
    ).hexdigest(),
    "results": [],
}
for mode, port in [("r2", 8790), ("s3", 8791)]:
    for iteration in range(6):
        req = Request(
            f"http://127.0.0.1:{port}/query",
            data=json.dumps({"sql": "SELECT count(*),sum(id) FROM sales"}).encode(),
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        )
        start = time.monotonic()
        with urlopen(req, timeout=20) as response:
            value = json.load(response)
        assert value["rows"] == [["4500", "11250000"]]
        report["results"].append(
            {
                "backend": mode,
                "iteration": iteration,
                "request_id": value["request_id"],
                "wall_ms": round((time.monotonic() - start) * 1000, 3),
            }
        )
(root / ".cache/reports/local-latency.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print("Recorded first/repeated local query wall times for both transports.")
