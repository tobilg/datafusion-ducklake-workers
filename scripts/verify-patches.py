#!/usr/bin/env python3
"""Verify patches and every source file, including submodules and extra files."""

import argparse
import json
from devlib import REPORTS
from vendor_sources import verify_sources

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--fixtures",
    action="store_true",
    help="Also verify QuackLake and an installed MinIO checkout",
)
args = parser.parse_args()
try:
    report = verify_sources(fixtures=args.fixtures)
except RuntimeError as error:
    raise SystemExit(str(error)) from None
(
    REPORTS
    / (
        "fixture-source-verification.json"
        if args.fixtures
        else "patch-verification.json"
    )
).write_text(json.dumps(report, indent=2) + "\n")
print(
    f"PASS {len(report['patches'])} patches; {sum(v['files'] for v in report['trees'].values())} source files match across {len(report['trees'])} complete trees"
)
