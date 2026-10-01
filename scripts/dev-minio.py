#!/usr/bin/env python3
"""Start the source-built, loopback-only S3 fixture with local random credentials."""

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument(
    "--native",
    action="store_true",
    help="Use the pinned host binary and persistent .cache/minio-data instead of Docker",
)
options = parser.parse_args()
path = root / ".cache/minio-credentials.json"
if not path.exists():
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(
            {
                "access_key": secrets.token_hex(10),
                "secret_key": secrets.token_urlsafe(48),
            },
            f,
        )
creds = json.loads(path.read_text())
env = {k: v for k, v in os.environ.items() if not k.startswith("MINIO_")}
env.update(
    MINIO_ROOT_USER=creds["access_key"],
    MINIO_ROOT_PASSWORD=creds["secret_key"],
    MINIO_BROWSER="off",
    GOMAXPROCS="2",
    GOMEMLIMIT="256MiB",
)
if options.native:
    binary = root / ".tools/bin/minio"
    if not binary.is_file():
        raise SystemExit(
            "Build the host binary first: bash scripts/build-minio.sh --native"
        )
    data = root / ".cache/minio-data"
    data.mkdir(exist_ok=True)
    raise SystemExit(
        subprocess.call(
            [str(binary), "server", "--address", "127.0.0.1:9000", str(data)], env=env
        )
    )
# The task's container filesystem avoids macOS's near-full APFS volume triggering
# MinIO's hard-coded 1% free-space reserve. No registry image is used or pulled.
# Docker inherits values by name; secrets never appear in command arguments.
args = [
    "docker",
    "run",
    "--rm",
    "--name",
    "datafusion-quacklake-minio-fixture",
    "--label",
    "local.fixture=datafusion-quacklake-workers",
    "--publish",
    "127.0.0.1:9000:9000",
    "--memory",
    "512m",
    "--cpus",
    "2",
]
for key in (
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "MINIO_BROWSER",
    "GOMAXPROCS",
    "GOMEMLIMIT",
):
    args += ["--env", key]
args += ["datafusion-quacklake-minio:07c3a429"]
raise SystemExit(subprocess.call(args, env=env))
