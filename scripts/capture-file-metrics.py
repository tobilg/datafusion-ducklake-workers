#!/usr/bin/env python3
"""Match release integration request IDs to sanitized transport counters."""

from devlib import REPORTS
import argparse, json, pathlib

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--variant", choices=["core", "full", "all"], default="all")
args = parser.parse_args()

root = pathlib.Path(__file__).resolve().parents[1]
evidence = root / ".cache/reports"
output = []
for variant, backend in [
    ("core", "r2"),
    ("core", "s3"),
    ("core", "unsigned"),
    ("full", "r2"),
    ("full", "s3"),
]:
    if args.variant != "all" and variant != args.variant:
        continue
    report = json.loads((evidence / f"files-{variant}-{backend}.json").read_text())
    cases = {r["request_id"]: r["test"] for r in report["results"] if "request_id" in r}
    counters = {}
    for line in (
        (root / f".cache/files-{variant}-{backend}.log").read_text().splitlines()
    ):
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("request_id") in cases and "object_bytes" in item:
            assert item["peak_object_reads"] <= 2, item
            counters[cases[item["request_id"]]] = item
    for name in [
        "quoted S3/R2 prefix",
        "cross-bucket join",
        "large object projected ranges",
        "public HTTPS Parquet",
        "large HTTP ranges",
    ]:
        assert name in counters, (variant, backend, name)
        item = counters[name]
        assert item["range_requests"] > 0 and item["object_bytes"] > 0, item
        if name.startswith("large"):
            assert item["object_bytes"] < 1024 * 1024, item
        output.append(
            {
                "variant": variant,
                "backend": backend,
                "test": name,
                "wasm_sha256": report["wasm_sha256"],
                **item,
            }
        )
http = [
    json.loads(line)
    for line in (root / ".cache/file-http-requests.jsonl").read_text().splitlines()
]
assert all(
    not r["authorization_present"] for r in http
), "Unexpected credential header on HTTP fixture"
assert any(r["path"] == "/large.parquet" and r["range"] for r in http)
(evidence / f"file-query-metrics-{args.variant}.json").write_text(
    json.dumps(
        {
            "scope": "Successful request payload counters; HEAD/probe bytes and protocol overhead excluded. HTTP fixture confirms no Authorization header.",
            "results": output,
        },
        indent=2,
    )
    + "\n"
)
print(
    f"Verified range reads, shared concurrency and credential isolation for {args.variant} file deployments."
)
