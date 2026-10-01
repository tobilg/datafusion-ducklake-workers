#!/usr/bin/env python3
"""First and repeated file queries in fresh local Workers; no platform CPU claims."""

from devlib import REPORTS, service, read_jsonc, file_fixture_dir
import argparse, hashlib, json, pathlib, shutil, statistics, time
from urllib.request import Request, urlopen

p = argparse.ArgumentParser()
p.add_argument("--variant", choices=["core", "full"], required=True)
a = p.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
token = (root / ".cache/query-api-token").read_text().strip()
directory = root / ".cache/file-benchmark"
directory.mkdir(exist_ok=True)

results = []
for backend in ["r2", "s3"]:
    fixture = file_fixture_dir(a.variant, backend)
    config = read_jsonc(fixture / "wrangler.jsonc")
    config.update(main=str(root / f"build/{a.variant}/index.js"))
    (directory / "wrangler.jsonc").write_text(json.dumps(config))
    secrets = directory / ".dev.vars"
    secrets.touch(mode=0o600)
    secrets.chmod(0o600)
    shutil.copyfile(fixture / ".dev.vars", secrets)
    with service(
        [
            root / "node_modules/.bin/wrangler",
            "dev",
            "--local",
            "--ip",
            "127.0.0.1",
            "--port",
            "18899",
            "--inspector-port",
            "1949",
            "--config",
            directory / "wrangler.jsonc",
            "--persist-to",
            root / ".cache/files-state",
        ],
        18899,
        "file-benchmark/worker.log",
        extra_ports=(1949,),
    ):
        times = []
        for index in range(11):
            req = Request(
                "http://127.0.0.1:18899/query",
                data=json.dumps(
                    {
                        "sql": f"SELECT count(*),sum(id) FROM '{backend}://files-a/events/'"
                    }
                ).encode(),
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                },
            )
            start = time.monotonic()
            with urlopen(req, timeout=20) as response:
                value = json.load(response)
            assert value["rows"] == [["5000", "12502500"]], value
            times.append(round((time.monotonic() - start) * 1000, 3))
        results.append(
            {
                "backend": backend,
                "first_query_ms": times[0],
                "repeated_median_ms": statistics.median(times[1:]),
                "repeated_max_ms": max(times[1:]),
                "wall_ms": times,
            }
        )
        print(
            backend,
            "first",
            times[0],
            "repeat median",
            statistics.median(times[1:]),
            flush=True,
        )

report = {
    "variant": a.variant,
    "scope": "Fresh local Worker per backend; health check before first query, then ten repeats. Client wall time, not deployed cold start or CPU.",
    "wasm_sha256": hashlib.sha256(
        (root / f"build/{a.variant}/index_bg.wasm").read_bytes()
    ).hexdigest(),
    "results": results,
}
(root / f".cache/reports/file-latency-{a.variant}.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
