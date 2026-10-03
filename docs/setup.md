# Developer workflow

Start with the [core quickstart](../README.md). The application build requires
Git, rustup, a C compiler, ripgrep, Python 3.12+ and Node 22.22.2/npm 10.9.7.
Python 3.14 and macOS arm64 were used locally. CI uses Linux for every job,
including validated Worker builds on arm64. Windows build scripts are
unsupported. Use Bash for shell scripts.

`bash scripts/bootstrap.sh` fetches pinned runtime sources, applies the active
patch series, installs Rust 1.98.0 with rustfmt and the Emscripten target, runs
`npm ci`, and builds or reuses the pinned worker-build tool. Reuse requires matching
input and executable hashes; source verification still runs. New Git checkouts
fetch only the pinned revision and required submodules. Existing checkouts are
preserved. It does not fetch the QuackLake service unless `--fixtures` is supplied.
The provider checkout remains necessary for Cargo's optional dependency resolution.

The managed SDK supplies Emscripten and the required networking patches. Do not
substitute another SDK, use `--all-features`, or build the provider CLI/workspace.
On macOS its cache is under `~/Library/Caches/worker-build`; sandboxed builds need
access there. `scripts/env.sh` selects the pinned tools and clears unsupported
SDK/flag overrides. Allow approximately 20 GiB for an initial build.

## Everyday commands

Use the npm commands for routine development and `scripts/validate.py` for
integration suites. Other scripts are focused diagnostics or internal helpers;
they do not need to be run individually during normal development.

| Task | Command |
| --- | --- |
| Bootstrap pinned tools/sources | `npm run bootstrap` |
| Core development server and local key | `npm run dev` |
| Validate prerequisites | `python3 scripts/check-environment.py --profile build` |
| Static, workflow, native policy and publication checks | `npm run check` |
| Core build | `npm run build:core` |
| Full build | `npm run build:full` |
| Core/R2 integration | `npm test` |

Replace `core` with `full` where applicable. Development links and probe builds
are not release artifacts. Builds serialize compilation and JS/WASM collection;
use the build script instead of concurrent raw worker-build commands. Every build
verifies complete pinned vendor trees before/after compilation. Undocumented
edits, additions, deletions, modes and symlinks fail verification without being
reset. Only known generated output directories are excluded. Required submodules
are verified recursively; unused provider DuckDB extension submodules may remain
absent. Artifact checks repeat verification against the recorded source digest.

For release checks, run `bash scripts/dependency-graphs.sh core`,
`bash scripts/size.sh core` and `python3 scripts/check-artifacts.py --variant core --tests`,
then repeat for full. Run both file transports first for the `--tests` check.
The size command requires an existing production build and verifies its source,
input and module hashes before and after Wrangler's dry-run. It omits the custom
build command in a temporary adjacent config, preserves relative paths, and
never recompiles. Stale artifacts fail with a request to rebuild first.
`python3 scripts/verify-patches.py --fixtures` also checks the separate catalog
and an installed MinIO source checkout. Generated reports stay ignored.

Successful CI build jobs upload `datafusion-worker-core-<commit>` and
`datafusion-worker-full-<commit>` under the workflow run's **Artifacts** section,
with 3-day retention for PRs and 14 days otherwise. Each archive contains the contents of `build/<variant>/`,
including WASM, the JavaScript entrypoint, package metadata and compatibility
shim. Keep these files together; the WASM is not a standalone Worker. Uploads
exclude temporary build files and diagnostic variants. Deployment still requires
your own Wrangler configuration, bindings and secrets.

## CI speed, cache and build layout

Quick checks run on every PR and main-branch push. Only changes limited to known
root prose files or Markdown under `docs/` skip Worker jobs. Deleted/renamed
source files, unknown paths, workflow changes and unavailable Git history all
cause full validation. Manual runs always validate both variants.
All CI jobs use Ubuntu 24.04: Worker builds use arm64 runners, while quick
checks and the final validation job use x64 runners.
Use the final **Validation** job as the required branch-protection check so a
documentation-only change can complete successfully.

Normal runs restore separate caches for pinned tools/managed SDK, package
downloads, and compiled dependencies. Keys include OS/architecture and relevant
pins, locks and patches; compiled outputs also use the variant and commit, with
fallback only within the same dependency key. Only successful main-branch jobs
save caches. PRs only restore. The matrix has one writer for each shared cache.
Do not cache fixture state, generated credentials, deployment artifacts or the
entire workspace. Cache hits never replace source verification or test execution.
The host tool helper also rejects changed binaries, changed inputs or a different
architecture. Source-built MinIO is reused with the same checks.

Keep the repository's default cache storage limit; these workflows do not raise
it or enable paid overflow. Old entries can be evicted. Package/tool caches are
shared between variants. The default `shared` layout bootstraps once and builds
core and full sequentially in the same job, reusing compiled dependencies when
their features and compiler settings match. The optional `parallel` layout uses
separate compilation caches and can build tools twice while common caches are
empty. Shared and parallel layouts use distinct compilation cache keys.

Manual **Run workflow** inputs:

| Input | Default | Purpose |
| --- | --- | --- |
| `fresh` | `false` | Set `true` to disable all cache restores and saves. |
| `layout` | `shared` | Build/test core and full on one runner; select `parallel` for separate runners. |

Fresh runs check project builds on a new hosted runner. Runner-provided tooling
can still exist; this does not guarantee an empty machine.

Each Worker job reports phase durations, exit status, available disk and maximum
child-process RSS in the Actions summary. Use actual total job durations for
runner cost comparisons; phase timings exclude cache transfers and provisioning.
RSS is a host-process measurement, not isolate or whole-job memory accounting.
Compare the same commit with parallel/shared layouts and warm/fresh caches.
Both layouts execute the same correctness suites and are not allowed to pass on
failure. Linux CI covers the complete build, size, file, catalog and probe
suites. Check each commit's run before using its artifacts; passing CI does not
establish deployed startup or isolate capacity.

## File integration tests

Install **DuckDB 1.5.5** as the independent fixture generator with the checksummed
local installer. It writes only `.tools/bin/duckdb`. Native R2 tests
need no MinIO, Go, Docker, catalog or S3 credentials:

```sh
python3 scripts/install-duckdb.py
python3 scripts/validate.py --suite files --variant core --backend r2
python3 scripts/validate.py --suite files --variant full --backend r2
```

The runner builds the selected variant, generates actual Parquet, populates local
R2 through Miniflare, starts the loopback range server and Worker, runs the suite,
and stops the processes it owns even on failure. Results go to `.cache/reports/`,
logs to `.cache/`. Tests include actual public HTTPS and therefore need internet
access. `--skip-build` tests existing artifacts explicitly; it does not certify
that they were compiled from current inputs.

File fixture JSONC templates under `fixtures/files/` are read-only inputs.
Generated configurations and secrets live in `.cache/fixtures/files/`. Existing
legacy fixture secret files are copied on first use and preserved. Plain R2 and
unsigned fixtures reject unexpected S3 secrets instead of silently deleting them.

For S3, install **Go 1.25.3** and build MinIO from its pinned source:

```sh
python3 scripts/check-environment.py --profile s3
bash scripts/build-minio.sh --native
python3 scripts/validate.py --suite files --variant all --backend all
```

This adds signed and anonymous S3 coverage and checks range/concurrency metrics.
Native MinIO binds only to loopback and persists under `.cache/minio-data`.
Its volume must exceed MinIO's 1% free-space reserve. Alternatively build with
`bash scripts/build-minio.sh` and pass `--minio docker`; the local source-built
container is ephemeral and uses no registry image. On x86 Docker hosts set
`MINIO_FIXTURE_ARCH=amd64` when building it.

The runner refuses occupied ports. `--reuse-services` explicitly reuses your
already-running local HTTP/MinIO/QuackLake fixtures and never stops them. Query
Workers are always freshly started. Default file ports are 8793–8799, catalog
ports 8790–8792, S3 9000; corresponding inspectors use 9232–9249. Do not run suites
concurrently against shared fixture state.

## Catalog integration tests

Additionally install **pnpm 12.4.2**. QuackLake uses its upstream frozen lockfile
with lifecycle scripts disabled:

```sh
python3 scripts/validate.py --suite catalog --backend r2
# With the source-built MinIO binary available:
python3 scripts/validate.py --suite catalog --backend all
```

The runner starts only local resources, creates the registry separately from
actual DuckLake metadata initialization, installs the fixture reader policy,
seeds Parquet/deletions/schema fixtures, prepares credentials and tests the full
Worker. Both-backend runs add snapshot and credential failure/revocation tests.
Setup journals completed steps and preserves existing metadata and credentials.
An interrupted seed may require manual inspection; the runner never silently
replaces tables or resets a catalog. Older manually prepared fixtures are checked
and reused without reseeding. Snapshot tests add a dedicated metadata-only table.

Native policy tests compile the actual path/URL/IP policy modules in a small
locked host crate without DataFusion or the Worker SDK. `npm run check` includes
them; use `python3 scripts/validate.py --suite unit` to run only those tests.
To build and exercise diagnostic routes, real R2 adapter contracts and SDK timer
cleanup with managed local services:

```sh
WORKER_LINK_OPT=1 python3 scripts/validate.py --suite probes
```

This uses both variants by default and the catalog fixture prerequisites above.
Probe artifacts remain separate from release outputs. Native policy tests alone
do not verify Worker bindings; the probe suite exercises those in local workerd.

To inspect a running service manually, use `bash scripts/dev-quacklake.sh` or
`bash scripts/dev-files.sh core r2`. Focused `test-*.py` scripts remain available
for debugging; they expect their named local fixtures to be running. Tests and
fixture provisioning do not accept production endpoints. Only the separate
operator tools documented in [operations](operations.md) can target live services.

## Clean sources and diagnostics

```sh
python3 scripts/prepare-clean-build.py --name clean-release
cd .cache/clean-release
bash scripts/bootstrap.sh
bash scripts/build.sh --variant core
bash scripts/build.sh --variant full
```

This exports publication candidates into a new local repository with no project
caches. Host Cargo/SDK caches may still be reused. `--reuse-caches` explicitly
links existing project caches for a faster source-completeness check. Neither is
an empty-machine claim; fresh hosted CI execution is a separate check.

The [diagnostics guide](diagnostics.md) covers the official baseline, protocol
probes, startup profiles and memory investigations. All generated results are
local and ignored; no obsolete evidence bundle is included in source control.
