#!/usr/bin/env python3
"""Production-like file-mode configuration checks in fresh local Workers."""

from devlib import REPORTS, service
import hashlib, json, os, pathlib, subprocess, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

root = pathlib.Path(__file__).resolve().parents[1]
token = (root / ".cache/query-api-token").read_text().strip()
directory = root / ".cache/file-config-test"
directory.mkdir(exist_ok=True)
secret = directory / ".dev.vars"
secret.touch(mode=0o600)
secret.chmod(0o600)

cases = [
    ("core default", {}, "SELECT 1", 200),
    ("mode unavailable", {"QUERY_MODE": "ducklake"}, "SELECT 1", 503),
    ("unknown mode", {"QUERY_MODE": "unknown"}, "SELECT 1", 503),
    ("bad S3 endpoint", {"S3_ENDPOINT": "http://example.com"}, "SELECT 1", 503),
    ("bad S3 region", {"S3_REGION": ""}, "SELECT 1", 503),
    ("invalid R2 map", {"R2_BINDINGS": "[]"}, "SELECT 1", 503),
    (
        "plain HTTP denied",
        {},
        "SELECT * FROM 'http://127.0.0.1:8798/data.parquet'",
        403,
    ),
    (
        "loopback HTTPS denied",
        {},
        "SELECT * FROM 'https://127.0.0.1/data.parquet'",
        403,
    ),
    ("public HTTPS default", {}, None, 200),
]
for bucket in ["oversized-list", "streamed-list"]:
    cases.append(
        (
            bucket,
            {"S3_ENDPOINT": "http://127.0.0.1:8798", "ALLOW_LOCAL_HTTP": "true"},
            f"SELECT count(*) FROM 's3://{bucket}/events/'",
            422,
        )
    )
public = json.loads((root / "fixtures/files/public-https.json").read_text())
for bucket in ("normal-list", "namespace-list", "attribute-list"):
    cases.append(
        (
            bucket,
            {"S3_ENDPOINT": "http://127.0.0.1:8798", "ALLOW_LOCAL_HTTP": "true"},
            f"SELECT count(*) FROM 's3://{bucket}/events/'",
            503 if bucket == "namespace-list" else 200,
        )
    )
auth_cases = {
    "missing API key": (None, token, 503, "missing_secret"),
    "empty API key": ("", token, 503, "invalid_length"),
    "short API key": ("invalid-local-test", token, 503, "invalid_length"),
    "oversized API key": ("x" * 4097, token, 503, "invalid_length"),
    "minimum API key": ("m" * 32, "m" * 32, 200, None),
    "maximum API key": ("m" * 4096, "m" * 4096, 200, None),
    "wrong caller key": (token, "invalid-local-test", 401, None),
}
cases += [(name, {}, "SELECT 1", values[2]) for name, values in auth_cases.items()]
results = []
for name, variables, sql, expected in cases:
    configured, supplied, _, reason = auth_cases.get(
        name, (token, token, expected, None)
    )
    secret.write_text("" if configured is None else "API_KEY=" + configured + "\n")
    config = {
        "name": "file-config-test",
        "main": str(root / "build/core/index.js"),
        "compatibility_date": "2026-09-29",
        "compatibility_flags": ["new_module_registry"],
        "workers_dev": False,
        "preview_urls": False,
        "vars": variables,
    }
    (directory / "wrangler.jsonc").write_text(json.dumps(config))
    with service(
        [
            root / "node_modules/.bin/wrangler",
            "dev",
            "--local",
            "--ip",
            "127.0.0.1",
            "--port",
            "8799",
            "--inspector-port",
            "9249",
            "--config",
            directory / "wrangler.jsonc",
        ],
        8799,
        "file-config-test/worker.log",
        extra_ports=(9249,),
    ):
        started = time.monotonic()
        body = {"sql": sql or f"SELECT count(*),sum(id) FROM '{public['url']}'"}
        req = Request(
            "http://127.0.0.1:8799/query",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + supplied,
            },
        )
        try:
            response = urlopen(req, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            status = response.status
            value = json.load(response)
        assert status == expected, (name, status, value)
        if name == "namespace-list":
            assert value["error"]["code"] == "QUERY_EXECUTION_FAILED", value
        if name in ("namespace-list", "attribute-list", "normal-list"):
            assert (
                time.monotonic() - started < 5
            ), "XML parsing exhausted the query deadline"
        if name in ("attribute-list", "normal-list"):
            assert value["rows"] == [["2500"]], value
        if reason:
            assert value["error"]["code"] == "AUTH_CONFIGURATION_INVALID", value
            assert "API_KEY" in value["error"]["message"], value
        elif name == "wrong caller key":
            assert value["error"]["code"] == "UNAUTHORIZED", value
        if name == "public HTTPS default":
            assert value["rows"] == public["expected"], value
        print("PASS", name, status, flush=True)
        results.append({"test": name, "status": status})
    if reason:
        log = (directory / "worker.log").read_text()
        assert '"configuration_key":"API_KEY"' in log and f'"reason":"{reason}"' in log
        for credential in (configured, supplied):
            if credential:
                assert credential not in log and credential not in json.dumps(
                    value
                ), name

report = {
    "wasm_sha256": hashlib.sha256(
        (root / "build/core/index_bg.wasm").read_bytes()
    ).hexdigest(),
    "results": results,
}
(root / ".cache/reports/file-config-tests.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
