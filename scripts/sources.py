#!/usr/bin/env python3
"""Fetch exact public source checkouts; never reset an existing checkout."""

import argparse
import json
import hashlib
import io
import pathlib
import subprocess
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures", action="store_true", help="Also fetch the QuackLake test service"
    )
    args = parser.parse_args()
    for name, source in json.loads((ROOT / "sources.lock.json").read_text()).items():
        if name == "quacklake" and not args.fixtures:
            continue
        path = ROOT / "vendor" / name
        if "sha256" in source:
            marker = path / ".source-sha256"
            if path.exists():
                if (
                    not marker.exists()
                    or marker.read_text().strip() != source["sha256"]
                ):
                    raise SystemExit(
                        f"{name}: unexpected archive source; inspect manually"
                    )
            else:
                with urllib.request.urlopen(source["url"], timeout=60) as response:
                    archive = response.read()
                if hashlib.sha256(archive).hexdigest() != source["sha256"]:
                    raise SystemExit(f"{name}: archive checksum mismatch")
                path.mkdir(parents=True)
                with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                    for member in tar.getmembers():
                        member.name = member.name.partition("/")[2]
                        if member.name:
                            tar.extract(member, path, filter="data")
                marker.write_text(source["sha256"] + "\n")
            print(f"{name}: sha256 {source['sha256']}")
            continue
        if not path.exists():
            path.parent.mkdir(exist_ok=True)
            # Only the pinned tree is needed for builds and archive verification.
            # Existing checkouts are never reset or made shallow by this path.
            run("git", "init", "--quiet", str(path))
            run("git", "-C", str(path), "remote", "add", "origin", source["url"])
            run("git", "-C", str(path), "fetch", "--depth=1", "origin", source["rev"])
            run("git", "-C", str(path), "checkout", "--detach", source["rev"])
        if run("git", "-C", str(path), "rev-parse", "HEAD") != source["rev"]:
            raise SystemExit(
                f"{name}: unexpected revision; preserve and inspect manually"
            )
        if source.get("submodules"):
            run(
                "git",
                "-C",
                str(path),
                "submodule",
                "update",
                "--init",
                "--recursive",
                "--depth=1",
            )
        print(f"{name}: {source['rev']}")


if __name__ == "__main__":
    main()
