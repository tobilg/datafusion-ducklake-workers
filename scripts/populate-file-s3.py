#!/usr/bin/env python3
"""Populate the source-built localhost S3 fixture, including anonymous-read policies."""

import json
from devlib import ROOT as root, local_s3_put as put

for bucket in ["files-a", "files-b", "files-private"]:
    put(bucket, "", b"")
    base = root / ".cache/file-fixtures" / bucket
    for file in base.rglob("*.parquet"):
        put(bucket, file.relative_to(base).as_posix(), file.read_bytes())
    if bucket != "files-private":
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:ListBucket"],
                    "Resource": ["arn:aws:s3:::" + bucket],
                },
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": ["arn:aws:s3:::" + bucket + "/*"],
                },
            ],
        }
        put(bucket, "", json.dumps(policy).encode(), "policy=")
print("Local S3 files and public/private fixture policies installed.")
