#!/usr/bin/env python3
"""Prepare a reviewable staging configuration. Does not contact or mutate Cloudflare."""

import argparse, json, pathlib, re, urllib.parse

p = argparse.ArgumentParser()
for name in ["name", "account-id", "quack-host", "catalog-id", "bucket", "data-path"]:
    p.add_argument("--" + name, required=True)
p.add_argument("--backend", choices=["r2_binding", "s3"], default="r2_binding")
p.add_argument("--jurisdiction", choices=["eu", "fedramp"])
p.add_argument("--s3-endpoint")
p.add_argument("--s3-region", default="auto")
p.add_argument("--s3-addressing-style", choices=["path", "virtual"], default="path")
p.add_argument("--output", default="wrangler.staging.json")
a = p.parse_args()
assert re.fullmatch("[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", a.bucket)
assert re.fullmatch("[A-Za-z0-9_-]{1,128}", a.catalog_id)
assert re.fullmatch("[a-z0-9-]{1,63}", a.name)
assert re.fullmatch("[a-fA-F0-9]{32}", a.account_id)
assert re.fullmatch("[a-zA-Z0-9.-]+", a.quack_host) and "." in a.quack_host
assert (
    a.data_path == f"r2://{a.bucket}/catalogs/{a.catalog_id}/"
), "Use the exact provisioned path; no overrides"
out = pathlib.Path(a.output)
assert out.parent == pathlib.Path("."), "Configuration must be at repository root"
assert not out.exists(), "Refusing to overwrite an existing configuration"
config = {
    "$schema": "./node_modules/wrangler/config-schema.json",
    "name": a.name,
    "account_id": a.account_id,
    "main": "build/full/index.js",
    "compatibility_date": "2026-09-29",
    "compatibility_flags": ["new_module_registry"],
    "workers_dev": True,
    "preview_urls": False,
    "build": {"command": "bash scripts/build.sh --variant full"},
    "limits": {"cpu_ms": 30000},
    "observability": {"enabled": True, "head_sampling_rate": 1},
    "vars": {
        "STORAGE_BACKEND": a.backend,
        "QUACK_URI": f"quack:{a.quack_host}:443",
        "CATALOG_ID": a.catalog_id,
        "CATALOG_BUCKET": a.bucket,
        "CATALOG_DATA_PATH": a.data_path,
        "MAX_RESULT_ROWS": "1000",
        "MAX_RESPONSE_BYTES": "1048576",
        "QUERY_TIMEOUT_MS": "10000",
        "DATAFUSION_MEMORY_BYTES": "50331648",
    },
}
if a.backend == "r2_binding":
    binding = {"binding": "CATALOG_R2", "bucket_name": a.bucket}
    if a.jurisdiction:
        binding["jurisdiction"] = a.jurisdiction
    config["r2_buckets"] = [binding]
else:
    assert a.s3_endpoint and a.s3_endpoint.startswith(
        "https://"
    ), "Explicit HTTPS S3 endpoint required"
    endpoint = urllib.parse.urlsplit(a.s3_endpoint)
    assert (
        endpoint.hostname
        and endpoint.path in ("", "/")
        and not endpoint.username
        and not endpoint.password
        and not endpoint.query
        and not endpoint.fragment
    ), "Endpoint must be an origin without credentials, path, query or fragment"
    config["vars"].update(
        S3_ENDPOINT=a.s3_endpoint,
        S3_REGION=a.s3_region,
        S3_BUCKET=a.bucket,
        S3_ADDRESSING_STYLE=a.s3_addressing_style,
    )
out.write_text(json.dumps(config, indent=2) + "\n")
print(
    f"Prepared {out}. Review the account/catalog/bucket; no resources were created or deployed."
)
