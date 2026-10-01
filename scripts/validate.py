#!/usr/bin/env python3
"""One entrypoint for static checks and managed local integration suites."""

import argparse
import ast
import contextlib
import os
import subprocess
import sys
from devlib import ROOT, run, service, file_fixture_dir


def quick():
    for path in sorted((ROOT / "scripts").rglob("*.py")):
        ast.parse(path.read_text(), filename=str(path))
    for path in sorted((ROOT / "scripts").glob("*.sh")):
        run("bash", "-n", path)
    for path in sorted((ROOT / "scripts").glob("*.mjs")):
        run("node", "--check", path)
    run("rustfmt", "+1.98.0", "--edition", "2024", "--check", "src/main.rs", "build.rs")
    run(
        "rustfmt", "+1.98.0", "--edition", "2024", "--check", "tests/native/policies.rs"
    )
    run(sys.executable, "-m", "unittest", "discover", "-s", "scripts/tests", "-v")
    unit()
    run(sys.executable, "scripts/check-release.py")
    print("PASS static checks, workflow tests and publication hygiene", flush=True)


def worker(config, port, inspector, persistence, log):
    command = [
        ROOT / "node_modules/.bin/wrangler",
        "dev",
        "--local",
        "--ip",
        "127.0.0.1",
        "--config",
        config,
        "--port",
        str(port),
        "--inspector-port",
        str(inspector),
        "--persist-to",
        ROOT / ".cache" / persistence,
    ]
    return service(command, port, log, extra_ports=(inspector,))


def minio(stack, args):
    command = [sys.executable, "scripts/dev-minio.py"]
    if args.minio == "native":
        if not (ROOT / ".tools/bin/minio").is_file():
            raise SystemExit(
                "Run bash scripts/build-minio.sh --native first (optional S3 fixture)."
            )
        command.append("--native")
    stack.enter_context(
        service(
            command,
            9000,
            "minio.log",
            health="/minio/health/live",
            reuse=args.reuse_services,
        )
    )


def files(args):
    variants = ["core", "full"] if args.variant == "all" else [args.variant]
    for variant in variants:
        if not args.skip_build:
            run("bash", "scripts/build.sh", "--variant", variant)
        if not (ROOT / f"build/{variant}/index.js").is_file():
            raise SystemExit(f"Missing {variant} build")
    with contextlib.ExitStack() as stack:
        if args.backend != "r2":
            minio(stack, args)
        run(
            sys.executable,
            "scripts/prepare-file-fixtures.py",
            "--backend",
            args.backend,
        )
        if args.backend != "s3":
            run("node", "scripts/populate-file-r2.mjs")
        if args.backend != "r2":
            run(sys.executable, "scripts/populate-file-s3.py")
        stack.enter_context(
            service(
                [sys.executable, "scripts/dev-file-http.py"],
                8798,
                "file-http.log",
                health="/data.parquet",
                reuse=args.reuse_services,
            )
        )
        for variant in variants:
            backends = ["r2", "s3"] if args.backend == "all" else [args.backend]
            if variant == "core" and "s3" in backends:
                backends.append("unsigned")
            for backend in backends:
                port = {
                    ("core", "r2"): 8793,
                    ("core", "s3"): 8794,
                    ("core", "unsigned"): 8795,
                    ("full", "r2"): 8796,
                    ("full", "s3"): 8797,
                }[variant, backend]
                config = file_fixture_dir(variant, backend) / "wrangler.jsonc"
                with worker(
                    config,
                    port,
                    port + 450,
                    "files-state",
                    f"files-{variant}-{backend}.log",
                ):
                    run(
                        sys.executable,
                        "scripts/test-files.py",
                        "--port",
                        port,
                        "--variant",
                        variant,
                        "--backend",
                        backend,
                    )
        if "core" in variants:
            run(sys.executable, "scripts/test-file-config.py")
        if args.backend == "all":
            run(
                sys.executable,
                "scripts/capture-file-metrics.py",
                "--variant",
                args.variant,
            )


def catalog(args):
    if not args.skip_build:
        run("bash", "scripts/build.sh", "--variant", "full")
        run("bash", "scripts/build-quacklake.sh")
    if not (ROOT / "vendor/quacklake/.wrangler-build/index.js").is_file():
        raise SystemExit("Run bash scripts/build-quacklake.sh first.")
    with contextlib.ExitStack() as stack:
        catalog_fixture(stack, args)
        if args.backend != "r2":
            minio(stack, args)
            run(sys.executable, "scripts/populate-local-s3.py")
        if args.backend != "s3":
            run("node", "scripts/populate-local-r2.mjs")
        run(sys.executable, "scripts/prepare-query-secrets.py")
        run(sys.executable, "scripts/reference-fixture.py")
        backends = ["r2", "s3"] if args.backend == "all" else [args.backend]
        for backend in backends:
            port, inspector, config = (
                (8790, 9232, "fixtures/wrangler.catalog-probe.jsonc")
                if backend == "r2"
                else (8791, 9233, "fixtures/s3/wrangler.jsonc")
            )
            stack.enter_context(
                worker(config, port, inspector, "local-state", f"query-{backend}.log")
            )
            for script in ("test-api.py", "test-extended.py", "test-pool.py"):
                run(sys.executable, "scripts/" + script, f"http://127.0.0.1:{port}")
        if args.backend == "all":
            run(sys.executable, "scripts/test-snapshots.py")
            run(sys.executable, "scripts/test-failures.py")
            run("node", "scripts/test-session-revocation.mjs")
        run("node", "scripts/test-quacklake-uuid.mjs")


def catalog_fixture(stack, args):
    stack.enter_context(
        service(
            ["bash", "scripts/dev-quacklake.sh"],
            8792,
            "quacklake.log",
            statuses=(404,),
            reuse=args.reuse_services,
            extra_ports=(9234,),
        )
    )
    run(sys.executable, "scripts/setup-catalog-fixture.py")


def probes(args):
    variants = ["core", "full"] if args.variant == "all" else [args.variant]
    if not args.skip_build:
        for variant in variants:
            run(
                "bash",
                "scripts/build.sh",
                "--variant",
                variant,
                "--features",
                "protocol-probe",
            )
        run("bash", "scripts/build-quacklake.sh")
    with contextlib.ExitStack() as stack:
        catalog_fixture(stack, args)
        run("node", "scripts/populate-local-r2.mjs")
        run(sys.executable, "scripts/prepare-query-secrets.py")
        run(sys.executable, "scripts/test-probe-variants.py", "--variant", args.variant)
    run("node", "scripts/test-runtime-cleanup.mjs")


def unit():
    run(
        "cargo",
        "+1.98.0",
        "test",
        "--locked",
        "--manifest-path",
        "tests/native/Cargo.toml",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=["quick", "unit", "files", "catalog", "probes"],
        default="quick",
    )
    parser.add_argument("--variant", choices=["core", "full", "all"])
    parser.add_argument("--backend", choices=["r2", "s3", "all"], default="r2")
    parser.add_argument("--minio", choices=["native", "docker"], default="native")
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Test existing artifacts; no build freshness claim",
    )
    parser.add_argument(
        "--reuse-services",
        action="store_true",
        help="Reuse only your already-running local MinIO/HTTP/catalog fixtures",
    )
    args = parser.parse_args()
    args.variant = args.variant or (
        {"catalog": "full", "probes": "all"}.get(args.suite, "core")
    )
    if args.suite == "catalog" and args.variant != "full":
        parser.error("The catalog suite requires --variant full")
    os.chdir(ROOT)
    os.environ["PATH"] = (
        str(ROOT / ".tools/bin")
        + os.pathsep
        + str(ROOT / "node_modules/.bin")
        + os.pathsep
        + os.environ["PATH"]
    )
    os.environ["RUSTUP_TOOLCHAIN"] = "1.98.0"
    if args.suite == "quick":
        quick()
    elif args.suite == "unit":
        unit()
    else:
        run(
            sys.executable,
            "scripts/check-environment.py",
            "--profile",
            "catalog" if args.suite == "probes" else args.suite,
        )
        {"files": files, "catalog": catalog, "probes": probes}[args.suite](args)
    print(
        "PASS validation; generated results are in ignored .cache/reports/", flush=True
    )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
