#!/usr/bin/env python3
"""Measure CI phases without recording command arguments, secrets or query data."""

import json
import os
import platform
import re
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path
from devlib import ROOT, REPORTS


def main():
    report = REPORTS / "ci-timings.json"
    if sys.argv[1:] == ["--summary"]:
        records = json.loads(report.read_text()) if report.exists() else []
        lines = [
            "## Worker validation timings",
            "",
            f"Host: {platform.system()} {platform.machine()}. Cache: {os.environ.get('CI_USE_CACHE', 'unknown')}. Group: {os.environ.get('CI_VARIANT', 'unknown')}.",
            "",
            "| Phase | Seconds | Exit | Max child RSS (MiB) |",
            "| --- | ---: | ---: | ---: |",
        ]
        for r in records:
            lines.append(
                f"| {r['phase']} | {r['seconds']:.1f} | {r['exit']} | {r['max_child_rss_mib']:.1f} |"
            )
        lines += [
            "",
            f"Measured phase time: {sum(r['seconds'] for r in records):.1f} s. Free disk: {shutil.disk_usage(ROOT).free / 1024**3:.1f} GiB.",
            "",
            "Phase time excludes checkout/cache actions and runner provisioning. Max child RSS is an OS process metric, not total job or Worker-isolate memory. Compare total job durations in Actions for cost decisions.",
            "",
        ]
        output = "\n".join(lines)
        print(output)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as handle:
                handle.write(output)
        return 0
    phase, *command = sys.argv[1:]
    if not re.fullmatch(r"[a-z0-9-]+", phase) or not command:
        raise SystemExit("Expected ci_run.py phase command ... or --summary")
    started = time.monotonic()
    code = 1
    try:
        code = subprocess.call(command, cwd=ROOT)
        return code
    finally:
        records = json.loads(report.read_text()) if report.exists() else []
        rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        records.append(
            {
                "phase": phase,
                "seconds": round(time.monotonic() - started, 3),
                "exit": code,
                "max_child_rss_mib": rss
                / (1024**2 if platform.system() == "Darwin" else 1024),
            }
        )
        report.write_text(json.dumps(records, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
