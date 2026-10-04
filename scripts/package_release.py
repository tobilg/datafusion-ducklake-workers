#!/usr/bin/env python3
"""Package verified JS/WASM, deployment config and complete dependency notices."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from devlib import ROOT, run

LEGAL = re.compile(r"^(LICENSE|LICENCE|NOTICE|COPYING|COPYRIGHT)(?:$|[-_.])", re.I)
EXCLUDED = {"target", "node_modules", ".git", ".cache", ".wrangler"}
REQUIRED_MODULES = {"index.js", "index_bg.wasm", "worker/shim.mjs", "package.json"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def legal_files(directory):
    result = []
    for base, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in EXCLUDED]
        for name in files:
            path = Path(base) / name
            if LEGAL.match(name) or any(
                p.lower() in {"licenses", "licences"}
                for p in path.relative_to(directory).parts[:-1]
            ):
                if path.is_symlink():
                    raise RuntimeError(f"Review symlinked license: {path.name}")
                result.append(path)
    return sorted(result)


def package_licenses(package):
    directory = Path(package["manifest_path"]).parent
    if directory == ROOT:
        return [(Path("LICENSE"), ROOT / "LICENSE")]
    found = [(p.relative_to(directory), p) for p in legal_files(directory)]
    # Workspace crates may inherit their license from the checkout root. Never
    # substitute this application's MIT license for a dependency's missing text.
    boundary = next(
        (
            p
            for p in (directory, *directory.parents)
            if p != ROOT and (p / ".git").exists()
        ),
        None,
    )
    if directory.is_relative_to(ROOT / "vendor"):
        boundary = ROOT / "vendor" / directory.relative_to(ROOT / "vendor").parts[0]
    if boundary and boundary != directory and boundary != ROOT:
        for ancestor in directory.parents:
            found += [
                (Path("workspace") / p.name, p)
                for p in sorted(ancestor.iterdir())
                if p.is_file() and LEGAL.match(p.name)
            ]
            if ancestor == boundary:
                break
    if package.get("license_file"):
        path = Path(package["license_file"])
        if not path.is_absolute():
            path = directory / path
        if not path.is_file():
            raise RuntimeError(f"Missing declared license: {package['name']}")
        found.append((Path("declared") / path.name, path))
    if not found:
        name = f"{package['name']}-{package['version']}"
        fallback = ROOT / "licenses/upstream" / name / "LICENSE"
        sources = json.loads((ROOT / "licenses/upstream/sources.json").read_text())
        if (
            name not in sources
            or not fallback.is_file()
            or digest(fallback) != sources[name]["sha256"]
        ):
            raise RuntimeError(f"Missing reviewed license text for {name}")
        found.append((Path("LICENSE"), fallback))
    return found


def selected_packages(metadata):
    nodes = {n["id"]: n for n in metadata["resolve"]["nodes"]}
    pending = [metadata["resolve"]["root"]]
    selected = set()
    while pending:
        key = pending.pop()
        if key in selected:
            continue
        selected.add(key)
        pending += [
            d["pkg"]
            for d in nodes[key]["deps"]
            if any(k["kind"] != "dev" for k in d["dep_kinds"])
        ]
    return sorted(
        (p for p in metadata["packages"] if p["id"] in selected),
        key=lambda p: (p["name"], p["version"]),
    )


def write_notices(destination, variant):
    command = [
        "cargo",
        "+1.98.0",
        "metadata",
        "--locked",
        "--offline",
        "--format-version",
        "1",
        "--filter-platform",
        "wasm32-unknown-emscripten",
        "--no-default-features",
    ]
    if variant == "full":
        command += ["--features", "ducklake"]
    metadata = json.loads(subprocess.check_output(command, cwd=ROOT))
    records = []
    for package in selected_packages(metadata):
        name = f"{package['name']}-{package['version']}"
        paths = []
        for relative, original in package_licenses(package):
            target = destination / "licenses" / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
            paths.append(target.relative_to(destination).as_posix())
        records.append(
            {
                "name": package["name"],
                "version": package["version"],
                "license": package["license"],
                "repository": package["repository"],
                "texts": sorted(set(paths)),
            }
        )
    pins = json.loads((ROOT / "tools.lock.json").read_text())
    sdk_version = pins["managed_tools"]["emscripten"]
    cache = (
        Path.home() / "Library/Caches"
        if platform.system() == "Darwin"
        else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    )
    sdk = cache / f"worker-build/emsdk-{sdk_version}/upstream/emscripten"
    runtime_files = [sdk / "LICENSE", sdk / "AUTHORS"] + legal_files(sdk / "system/lib")
    if len(runtime_files) < 3 or any(not p.is_file() for p in runtime_files):
        raise RuntimeError(
            "Managed Emscripten runtime licenses unavailable; build with the pinned SDK"
        )
    for path in runtime_files:
        target = (
            destination / f"licenses/emscripten-{sdk_version}" / path.relative_to(sdk)
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    sysroot = Path(
        subprocess.check_output(
            ["rustc", "+1.98.0", "--print", "sysroot"], text=True
        ).strip()
    )
    rust_notice = sysroot / "share/doc/rust/COPYRIGHT-library.html"
    if not rust_notice.is_file():
        raise RuntimeError("Pinned Rust standard-library copyright notice unavailable")
    shutil.copyfile(rust_notice, destination / "licenses/RUST-COPYRIGHT-library.html")
    (destination / "licenses/components.json").write_text(
        json.dumps(records, indent=2) + "\n"
    )
    lines = [
        "# Bundled third-party notices",
        "",
        "This package contains the generated JavaScript and WebAssembly for the selected Worker variant.",
        "The application's MIT license does not replace dependency licenses. The inventory conservatively",
        "includes normal/build dependencies in this target graph, Emscripten system-library notices and",
        "Rust's standard-library notice. It does not assert that every listed build dependency is linked.",
        "MinIO, DuckDB, QuackLake server, Wrangler and Miniflare executables are not bundled.",
        "",
        "See [component license texts](licenses/components.json), [Rust notices](licenses/RUST-COPYRIGHT-library.html)",
        f"and [Emscripten notices](licenses/emscripten-{sdk_version}/LICENSE).",
        "",
        "Exact inputs and local compatibility modifications are identified by sources.lock.json, tools.lock.json,",
        "Cargo.lock and patches/. Modified upstream source is distributed as a reproducible patch series.",
        "",
    ]
    for entry in records:
        lines.append(
            f"- {entry['name']} {entry['version']}: {entry['license']} — [license/notice]({entry['texts'][0]})"
        )
    (destination / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines) + "\n")


def copy_modules(source, destination, manifest):
    expected = PurePosixPath("build") / manifest["variant"]
    names = set()
    for entry in manifest["modules"]:
        path = PurePosixPath(entry["path"])
        if path.is_absolute() or ".." in path.parts or ".tmp" in path.parts:
            raise RuntimeError("Unsafe module path")
        relative = path.relative_to(expected)
        if relative.name.startswith("."):
            continue
        original = source / relative
        if (
            original.is_symlink()
            or not original.is_file()
            or digest(original) != entry["sha256"]
        ):
            raise RuntimeError(f"Missing or changed companion module: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        names.add(relative.as_posix())
    if not REQUIRED_MODULES <= names:
        raise RuntimeError(
            "Incomplete Worker package: " + ", ".join(sorted(REQUIRED_MODULES - names))
        )
    return names


def deployment_config(variant):
    config = {
        "name": f"datafusion-ducklake-{variant}",
        "main": "index.js",
        "compatibility_date": "2026-09-29",
        "compatibility_flags": ["new_module_registry"],
        "workers_dev": True,
        "preview_urls": False,
        "limits": {"cpu_ms": 30000},
        "observability": {"enabled": True, "head_sampling_rate": 1},
        "vars": {"QUERY_MODE": "files" if variant == "core" else "ducklake"},
    }
    if variant == "full":
        config["vars"].update(
            STORAGE_BACKEND="r2_binding",
            QUACK_URI="quack:catalog.example.com:443",
            CATALOG_ID="analytics",
            CATALOG_BUCKET="replace-with-your-bucket",
            CATALOG_DATA_PATH="r2://replace-with-your-bucket/catalogs/analytics/",
        )
        config["r2_buckets"] = [
            {"binding": "CATALOG_R2", "bucket_name": "replace-with-your-bucket"}
        ]
    return config


def package(variant):
    run(sys.executable, "scripts/check-artifacts.py", "--variant", variant, "--tests")
    manifest = json.loads(
        (ROOT / f".cache/reports/artifact-manifest-{variant}.json").read_text()
    )
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"package-{variant}-", dir=dist) as tmp:
        destination = Path(tmp)
        names = copy_modules(ROOT / "build" / variant, destination, manifest)
        for name in ("LICENSE", "Cargo.lock", "sources.lock.json", "tools.lock.json"):
            shutil.copyfile(ROOT / name, destination / name)
        (destination / "patches").mkdir()
        series = json.loads((ROOT / "patches/series.json").read_text())
        for name in ["README.md", "series.json"] + [p["file"] for p in series]:
            shutil.copyfile(ROOT / "patches" / name, destination / "patches" / name)
        write_notices(destination, variant)
        (destination / "wrangler.jsonc").write_text(
            json.dumps(deployment_config(variant), indent=2) + "\n"
        )
        version = json.loads((ROOT / "tools.lock.json").read_text())["wrangler"]
        credential_steps = (
            f"npx --yes wrangler@{version} secret put API_KEY --config wrangler.jsonc\n"
        )
        if variant == "full":
            credential_steps += f"npx --yes wrangler@{version} secret put QUACKLAKE_JWT --config wrangler.jsonc\n"
        (destination / "README.md").write_text(
            f"# DataFusion DuckLake Workers — {variant}\n\n"
            "Keep index.js, index_bg.wasm and all companion modules together. This is a Cloudflare Worker bundle, not a standalone browser/Node WASM library.\n\n"
            "Verify `SHA256SUMS` using `sha256sum -c SHA256SUMS` (macOS: `shasum -a 256 -c SHA256SUMS`) before editing configuration.\n\n"
            "Use Node 22.22.2 and a Workers Paid plan. Edit wrangler.jsonc directly: choose your Worker name and storage configuration. "
            "For full/DuckLake, first provision the catalog, dedicated bucket, policy and reader JWT; use its exact canonical r2://bucket/catalogs/catalogId/ path.\n\n"
            "From this directory (no Rust compiler or SDK needed):\n\n```bash\n"
            f"npx --yes wrangler@{version} login\n"
            f"npx --yes wrangler@{version} deploy --config wrangler.jsonc\n"
            + credential_steps
            + "```\n\n"
            "The endpoint fails closed until its secrets are set. API_KEY requires 32–4096 UTF-8 bytes; generate one with `openssl rand -hex 32`. "
            "Enter secrets at the prompts, never in configuration. Test authenticated /readyz and POST /query after deployment.\n\n"
            "For S3 mode, query examples, rotation and deployment acceptance, see the [project README](https://github.com/tobilg/datafusion-ducklake-workers#readme). "
            "Local validation does not establish Cloudflare startup, CPU or total isolate memory.\n"
        )
        metadata = {
            "variant": variant,
            "entrypoint": "index.js",
            "modules": sorted(names),
            "source_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "source_dirty": bool(
                subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
            ),
            "input_sha256": {e["path"]: e["sha256"] for e in manifest["inputs"]},
        }
        (destination / "bundle.json").write_text(json.dumps(metadata, indent=2) + "\n")
        (destination / "SHA256SUMS").write_text(
            "".join(
                f"{digest(p)}  {p.relative_to(destination).as_posix()}\n"
                for p in sorted(destination.rglob("*"))
                if p.is_file()
            )
        )
        run(
            sys.executable,
            "scripts/check-artifacts.py",
            "--variant",
            variant,
            "--tests",
        )
        final = dist / variant
        if final.exists():
            shutil.rmtree(final)
        shutil.move(str(destination), final)
    print(
        f"PASS complete {variant} JS/WASM package, licenses and checksums: dist/{variant}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("core", "full"), required=True)
    args = parser.parse_args()
    package(args.variant)
