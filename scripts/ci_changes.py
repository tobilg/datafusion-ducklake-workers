#!/usr/bin/env python3
"""Conservative CI routing: only known prose-only changes skip Worker builds."""

import json
import os
from pathlib import Path
import re
import subprocess

PROSE = {
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "THIRD_PARTY_NOTICES.md",
    "LICENSE",
}


def needs_workers(paths):
    return any(
        path not in PROSE and not (path.startswith("docs/") and path.endswith(".md"))
        for path in paths
    )


def changed_paths(event_name, event, head, run=subprocess.check_output):
    if event_name == "pull_request":
        base = event["pull_request"]["base"]["sha"]
        tip = event["pull_request"]["head"]["sha"]
        separator = "..."
    elif event_name == "push":
        base, tip, separator = event.get("before", ""), head, ".."
    else:
        return None
    if not all(
        re.fullmatch(r"[0-9a-f]{40}", value or "") and set(value) != {"0"}
        for value in (base, tip)
    ):
        return None
    try:
        data = run(
            [
                "git",
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                base + separator + tip,
                "--",
            ],
            stderr=subprocess.DEVNULL,
        )
        return [p for p in data.decode().split("\0") if p]
    except (subprocess.CalledProcessError, UnicodeError):
        return None


def routing(event_name, event, head):
    inputs = event.get("inputs", {}) if event_name == "workflow_dispatch" else {}
    layout = inputs.get("layout", "parallel")
    if layout not in ("parallel", "shared"):
        raise ValueError("Unsupported CI layout")
    paths = changed_paths(event_name, event, head)
    return {
        "workers": str(paths is None or needs_workers(paths)).lower(),
        "use_cache": str(str(inputs.get("fresh", "false")).lower() != "true").lower(),
        "matrix": json.dumps(
            {"variant": ["core", "full"] if layout == "parallel" else ["all"]},
            separators=(",", ":"),
        ),
    }


if __name__ == "__main__":
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    result = routing(os.environ["GITHUB_EVENT_NAME"], event, os.environ["GITHUB_SHA"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        for key, value in result.items():
            output.write(f"{key}={value}\n")
    print("CI routing:", json.dumps(result))
