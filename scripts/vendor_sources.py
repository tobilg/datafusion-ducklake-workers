"""Verify complete effective vendor trees without modifying existing checkouts."""

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
import urllib.request

from devlib import ROOT, patch_series

# Only additional build-output trees are excluded. Tracked files under these
# names are still checked. Arbitrary Git-ignored source files are NOT excluded.
GENERATED = {
    "target",
    "node_modules",
    ".wrangler",
    ".wrangler-build",
    ".cache",
    ".pnpm-store",
}


def inventory(directory, expected_paths=None, generated_paths=()):
    records = {}
    expected_parents = set()
    for path in expected_paths or ():
        expected_parents.update(parent.as_posix() for parent in Path(path).parents)
    for base, dirs, files in os.walk(directory, followlinks=False):
        relative = Path(base).relative_to(directory)
        for name in list(dirs):
            path = Path(base) / name
            key = (relative / name).as_posix()
            if name == ".git" or (
                expected_paths is not None
                and (name in GENERATED or key in generated_paths)
                and key not in expected_parents
            ):
                dirs.remove(name)
            elif path.is_symlink():
                dirs.remove(name)
                files.append(name)
        for name in files:
            if name == ".git":
                continue
            path = Path(base) / name
            key = (relative / name).as_posix()
            if (
                expected_paths is not None
                and key in generated_paths
                and key not in expected_paths
            ):
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                records[key] = ("symlink", os.readlink(path))
            elif stat.S_ISREG(mode):
                records[key] = (
                    "executable" if mode & 0o111 else "file",
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            else:
                raise RuntimeError(f"Unexpected vendor file type: {key}")
    return records


def compare_trees(expected, actual, generated_paths=()):
    wanted = inventory(expected)
    found = inventory(actual, wanted, generated_paths)
    changes = [
        name
        for name in sorted(wanted.keys() | found.keys())
        if wanted.get(name) != found.get(name)
    ]
    if changes:
        # Paths only: local source diffs might contain credentials.
        raise RuntimeError(
            "Unrecorded vendor changes (preserved): " + ", ".join(changes[:20])
        )
    return hashlib.sha256(json.dumps(wanted, sort_keys=True).encode()).hexdigest(), len(
        wanted
    )


def export_git(checkout, destination, revision, required_submodules=False):
    actual = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != revision:
        raise RuntimeError(
            f"Unexpected vendor revision: {checkout.name}; preserve and inspect it"
        )
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.check_output(["git", "-C", str(checkout), "archive", revision])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(destination, filter="data")
    entries = subprocess.check_output(
        ["git", "-C", str(checkout), "ls-tree", "-r", "-z", revision]
    )
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.decode().split("\t", 1)
        mode, _, commit = metadata.split()
        if mode == "160000":
            child = checkout / name
            if (child / ".git").exists():
                export_git(child, destination / name, commit, required_submodules)
            elif required_submodules or (child.exists() and any(child.iterdir())):
                raise RuntimeError(
                    f"Missing or unexpected submodule: {checkout.name}/{name}"
                )
            # Provider's optional DuckDB extension source submodules are unused
            # by the Worker and intentionally left absent by bootstrap.


def export_crate(source, destination):
    package = source["url"].rsplit("/", 1)[-1]
    cached = next((Path.home() / ".cargo/registry/cache").glob("*/" + package), None)
    if cached:
        archive = cached.read_bytes()
    else:
        with urllib.request.urlopen(source["url"], timeout=60) as response:
            archive = response.read()
    if hashlib.sha256(archive).hexdigest() != source["sha256"]:
        raise RuntimeError(f"Archive checksum mismatch: {package}")
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            member.name = member.name.partition("/")[2]
            if member.name:
                tar.extract(member, destination, filter="data")
    (destination / ".source-sha256").write_text(source["sha256"] + "\n")


def verify_sources(root=ROOT, fixtures=False):
    sources = json.loads((root / "sources.lock.json").read_text())
    if not fixtures:
        sources.pop("quacklake")
    else:
        pin = json.loads((root / "fixtures/minio.lock.json").read_text())
        if (root / "vendor/minio").exists():
            sources["minio"] = {"rev": pin["revision"]}
    series = patch_series()
    records = {}
    with tempfile.TemporaryDirectory(
        prefix="verify-sources-", dir=root / ".cache"
    ) as tmp:
        dest = Path(tmp)
        for name, source in sources.items():
            if "rev" in source:
                export_git(
                    root / "vendor" / name,
                    dest / name,
                    source["rev"],
                    source.get("submodules", False),
                )
            else:
                export_crate(source, dest / name)
        subprocess.run(["git", "init", "--quiet", str(dest)], check=True)
        for filename, name in series:
            directory = [] if name == "." else ["--directory=" + name]
            subprocess.run(
                [
                    "git",
                    "apply",
                    "--whitespace=nowarn",
                    *directory,
                    str(root / "patches" / filename),
                ],
                cwd=dest,
                check=True,
            )
        for name in sources:
            try:
                outputs = (
                    ("examples/emscripten-tokio/build", "examples/emscripten-tcp/build")
                    if name == "workers-rs"
                    else ()
                )
                if name == "quacklake":
                    outputs = ("tsconfig.tsbuildinfo",)
                digest, count = compare_trees(
                    dest / name, root / "vendor" / name, outputs
                )
            except RuntimeError as error:
                raise RuntimeError(f"{name}: {error}") from None
            records[name] = {"sha256": digest, "files": count}
    return {
        "trees": records,
        "sha256": hashlib.sha256(
            json.dumps(records, sort_keys=True).encode()
        ).hexdigest(),
        "patches": [
            {
                "patch": name,
                "sha256": hashlib.sha256(
                    (root / "patches" / name).read_bytes()
                ).hexdigest(),
            }
            for name, _ in series
        ],
    }
