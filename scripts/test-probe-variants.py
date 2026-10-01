#!/usr/bin/env python3
"""Run feature-gated probes from separate, local-only diagnostic build directories."""

from devlib import REPORTS, service, read_jsonc
import argparse, hashlib, json, pathlib, shutil
from urllib.request import urlopen
from urllib.error import HTTPError

root = pathlib.Path(__file__).resolve().parents[1]
results = []
directory = root / ".cache/variant-probe"
directory.mkdir(exist_ok=True)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--variant", choices=["core", "full", "all"], default="all")
args = parser.parse_args()
variants = ["core", "full"] if args.variant == "all" else [args.variant]
for variant in variants:
    config = read_jsonc(root / "fixtures/wrangler.catalog-probe.jsonc")
    config["main"] = str(root / f"build/{variant}-probe/index.js")
    (directory / "wrangler.json").write_text(json.dumps(config))
    secret = directory / ".dev.vars"
    secret.touch(mode=0o600)
    secret.chmod(0o600)
    if variant == "full":
        shutil.copyfile(root / "fixtures/.dev.vars", secret)
    else:
        secret.write_text("")
    with service(
        [
            root / "node_modules/.bin/wrangler",
            "dev",
            "--local",
            "--ip",
            "127.0.0.1",
            "--port",
            "18993",
            "--inspector-port",
            "19993",
            "--config",
            directory / "wrangler.json",
            "--persist-to",
            root / ".cache/local-state",
        ],
        18993,
        "variant-probe/worker.log",
        extra_ports=(19993,),
    ):
        for route in ["select-one", "r2-contract", "catalog", "attach"]:
            expected = (
                404 if variant == "core" and route in ["catalog", "attach"] else 200
            )
            try:
                response = urlopen(
                    "http://127.0.0.1:18993/__spike/" + route, timeout=20
                )
            except HTTPError as error:
                response = error
            with response:
                body = response.read()
                assert response.status == expected, (
                    variant,
                    route,
                    response.status,
                )
                value = json.loads(body) if response.status == 200 else None
                if route == "select-one":
                    assert value["value"] == "1"
                if route == "attach" and variant == "full":
                    assert value["sql_wrapper_tested"] and value["rows"] == "4500"
                results.append(
                    {
                        "variant": variant,
                        "route": route,
                        "status": response.status,
                        "result": value,
                    }
                )
                print("PASS", variant, route, response.status, flush=True)
report = {
    "scope": "Separate protocol-probe builds; not deployment artifacts.",
    "hashes": {
        v: hashlib.sha256(
            (root / f"build/{v}-probe/index_bg.wasm").read_bytes()
        ).hexdigest()
        for v in variants
    },
    "results": results,
}
(root / ".cache/reports/probe-variants.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
