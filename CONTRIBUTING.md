# Contributing

Start with [setup](docs/setup.md). Use the pinned Rust 1.98.0 toolchain, managed
SDK and lockfiles. Keep changes focused; describe the behavior changed and the
checks actually run. Do not claim a local dry-run is a Cloudflare deployment.

Before submitting:

```sh
npm run check
python3 scripts/validate.py --suite files --variant core --backend r2
```

Changes affecting catalog behavior need the full/catalog suite. Storage changes
need both transports. Feature/build changes need both variants, dependency graphs,
patch verification and production size checks. Keep S3 in both release builds;
SQLite, embedded DuckDB, PostgreSQL and multithread execution stay excluded.

Add meaningful regressions for behavior changes. Python workflow tests use the
standard-library unittest runner. Scripts share local process, signing and secret
helpers in `scripts/devlib.py`. Rust code is formatted with the pinned rustfmt;
Python uses conventional formatting (Black may be used locally). Do not format
vendor code independently of its reviewed patch series.

`npm run check` checks syntax, Rust formatting, workflow regressions, native
path/network policies, local links, publication candidates and credential patterns.
The scanner checks literal assignments to this project's secret names as well as
standard token formats. Blank values, explicit `<...>` placeholders and runtime
interpolation are allowed; examples must never contain a usable credential.
This is not a comprehensive security audit. Secrets, compiled files, logs and generated reports must remain
ignored. Do not attach heap dumps or raw upstream token-bearing responses to issues.

CI runs quick/native policy checks on Linux and core/full builds with native
R2, signed S3 and core anonymous S3 file tests. The full job also runs actual
catalog queries, deletes/schema/snapshots, failure/revocation/UUID tests, both
variant adapter probes, and managed SDK timer regressions. The build jobs use
separate Ubuntu 24.04 arm64 runners by default, reuse verified tool/dependency
caches, and skip Worker builds for prose-only changes. All CI jobs use Linux.
Manually requested fresh runs disable cache reads/writes. The manual shared-runner
layout runs the same suites. See [CI controls and measurement](docs/setup.md#ci-speed-cache-and-build-layout).
These workflows are prepared but must execute in GitHub before CI support is
claimed. Longer memory/soak investigations remain in the local diagnostics. CI never
deploys and needs no Cloudflare secrets. Test setup requires public package and
fixture downloads.

For dependency updates, change pins deliberately, regenerate locks, verify all
active patches, and rerun affected target suites. `patches/series.json` is the
single ordered patch inventory. Verification checks complete source trees, modes,
symlinks, additional files and initialized submodules, not only patched files.
It rejects unexplained changes without resetting the checkout. Known generated
outputs are excluded; Git-ignore status alone does not exclude source files.
Keep patches scoped and upstream references
explicit. Do not use `cargo update` or broad formatter runs as incidental cleanup.
