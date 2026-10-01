#!/usr/bin/env python3
"""Correlate sanitized local Worker counters with successful fixture request IDs."""

from devlib import REPORTS
import json, pathlib

root = pathlib.Path(__file__).resolve().parents[1]
evidence = root / ".cache/reports"
reports = []
for mode, log in [("r2", "query-r2.log"), ("s3", "query-s3.log")]:
    rows = json.loads((evidence / f"extended-{mode}.json").read_text())
    snapshots = evidence / "snapshots.json"
    if snapshots.exists():
        rows += [
            v
            for v in json.loads(snapshots.read_text())["queries"]
            if v["port"] == (8790 if mode == "r2" else 8791)
        ]
    ids = {r["request_id"]: r.get("test", r.get("sql")) for r in rows}
    for line in (root / ".cache" / log).read_text().splitlines():
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if value.get("request_id") in ids:
            reports.append({"test": ids[value["request_id"]], **value})
(evidence / "query-metrics.json").write_text(json.dumps(reports, indent=2) + "\n")
print(f"Recorded {len(reports)} sanitized log entries matching fixture requests.")
