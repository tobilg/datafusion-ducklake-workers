#!/usr/bin/env python3
"""Generate independent Parquet files and local configurations; no cloud access."""

from devlib import REPORTS
import argparse, json, pathlib, subprocess
from devlib import api_key, prepare_file_config

root = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--backend", choices=["r2", "s3", "all"], default="r2")
args = parser.parse_args()
data = root / ".cache/file-fixtures"
for name in ("files-a/events", "files-a/incompatible", "files-b", "files-private"):
    (data / name).mkdir(parents=True, exist_ok=True)
sql = """CREATE TABLE events AS SELECT i::BIGINT id, (i%4)::BIGINT category,
    (9007199254740992+i)::BIGINT big, (i*1.25)::DECIMAL(18,2) amount,
    CASE WHEN i%3=0 THEN NULL ELSE 'Grüße 🌍' END AS "label" FROM range(1,5001) t(i);
"""
for index, condition in enumerate(("id<=2500", "id>2500")):
    file = data / f"files-a/events/part{index}.parquet"
    subprocess.run(
        [
            "duckdb",
            "-init",
            "/dev/null",
            "-c",
            sql
            + f"COPY (SELECT * FROM events WHERE {condition}) TO '{file}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 2048);",
        ],
        check=True,
        capture_output=True,
    )
subprocess.run(
    [
        "duckdb",
        "-init",
        "/dev/null",
        "-c",
        f"COPY (SELECT i::BIGINT id, 'group-'||i::VARCHAR AS name FROM range(4) t(i)) TO '{data}/files-b/categories.parquet' (FORMAT PARQUET);",
    ],
    check=True,
    capture_output=True,
)
for file in [
    data / "files-a/Grüße 100%.parquet",
    data / "files-private/private.parquet",
]:
    file.write_bytes((data / "files-a/events/part0.parquet").read_bytes())
large = data / "files-a/large.parquet"
if not large.exists():
    subprocess.run(
        [
            "duckdb",
            "-init",
            "/dev/null",
            "-c",
            f"COPY (SELECT i::BIGINT id, md5(i::VARCHAR)||repeat('x',16384) payload FROM range(9000) t(i)) TO '{large}' (FORMAT PARQUET, COMPRESSION UNCOMPRESSED, ROW_GROUP_SIZE 2048);",
        ],
        check=True,
        capture_output=True,
    )
assert large.stat().st_size > 128 * 1024 * 1024
(data / "files-a/empty.parquet").write_bytes(b"")
(data / "files-a/incompatible/numbers.parquet").write_bytes(
    (data / "files-a/events/part0.parquet").read_bytes()
)
subprocess.run(
    [
        "duckdb",
        "-init",
        "/dev/null",
        "-c",
        f"COPY (SELECT 'text'::VARCHAR id) TO '{data}/files-a/incompatible/strings.parquet' (FORMAT PARQUET); COPY (SELECT repeat('x',1100000) AS label FROM range(3)) TO '{data}/files-a/wide.parquet' (FORMAT PARQUET, COMPRESSION ZSTD);",
    ],
    check=True,
    capture_output=True,
)
token = api_key()
creds = (
    json.loads((root / ".cache/minio-credentials.json").read_text())
    if args.backend != "r2"
    else None
)
for variant, backend in [
    ("core", "r2"),
    ("core", "s3"),
    ("core", "unsigned"),
    ("full", "r2"),
    ("full", "s3"),
]:
    if args.backend == "r2" and backend != "r2":
        continue
    if args.backend == "s3" and backend == "r2":
        continue
    values = {"API_KEY": token}
    if backend == "s3":
        values.update(
            S3_ACCESS_KEY_ID=creds["access_key"],
            S3_SECRET_ACCESS_KEY=creds["secret_key"],
        )
    prepare_file_config(variant, backend, values)
reference = {
    "aggregate": [["5000", "12502500"]],
    "part0": [["2500", "3126250"]],
    "large": [["9000", "40495500"]],
    "join": [
        [f"group-{c}", "1250", str(sum(i for i in range(1, 5001) if i % 4 == c))]
        for c in range(4)
    ],
}
(root / ".cache/reports/file-reference.json").write_text(
    json.dumps(
        {
            "engine": subprocess.check_output(
                ["duckdb", "-init", "/dev/null", "--version"], text=True
            ).strip(),
            "method": "Independent generated integer sequences; standalone Parquet, no DuckLake metadata",
            "expected": reference,
            "large_object_bytes": large.stat().st_size,
        },
        indent=2,
    )
    + "\n"
)
print(
    "Standalone Parquet fixtures and local configs prepared; secrets preserved and not printed."
)
