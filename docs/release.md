# Release procedure

Publish this project as experimental. A successful local suite establishes local
behavior; Cloudflare deployment acceptance is a separate operator task. Generated
test results stay in ignored `.cache/reports/` and are not shipped as historical
evidence in the repository or release packages.

## Validate and package the intended source

After bootstrap, build production variants with the default `-Os` link. Do not
use development links or the `protocol-probe` feature for distribution.

```bash
npm run check
npm run audit
npm run build:core
npm run build:full
bash scripts/dependency-graphs.sh core
bash scripts/dependency-graphs.sh full
python3 scripts/validate.py --suite files --variant all --backend all --skip-build
python3 scripts/validate.py --suite catalog --backend all --skip-build
bash scripts/size.sh core
bash scripts/size.sh full
npm run package:core
npm run package:full
```

The integration prerequisites and separate diagnostic suite are in
[setup](setup.md). CI runs the file, catalog, probe, dependency, advisory, source
verification and size checks before uploading complete packages. The current
source commit must have a successful aggregate **Validation** check. An earlier
commit's success is insufficient. Commit and push changes yourself through a
branch/PR; this project's branch protection requires Validation on `main` and
prevents force pushes and deletion, including for administrators.

Packaging checks exact production input/module hashes and matching file tests.
Each variant has its own JavaScript glue and WASM: do not mix builds or ship the
`.wasm` alone. Archives contain `index.js`, `index_bg.wasm`, `worker/shim.mjs`,
package metadata, `wrangler.jsonc`, a deployment README, licenses/notices, source
pins and compatibility patches. `bundle.json` records the source revision and
whether local source was uncommitted; CI release artifacts should be clean.
`SHA256SUMS` covers every packaged file except itself. These integrity files do
not constitute a signature or deployed acceptance evidence.

Build outputs remain in `build/`; distributable packages are generated in ignored
`dist/core/` and `dist/full/`. GitHub Actions uploads the complete contents as
`datafusion-worker-core-<commit>` and `datafusion-worker-full-<commit>`. Retention
is 3 days for PRs and 14 days otherwise. For a durable tagged release, attach the
complete archives from a successful run for that exact commit using the owner's
release process. The workflow does not automatically publish a GitHub Release.

## Deploy a downloaded package

Extract the whole archive and follow its README. Verify `SHA256SUMS` before
editing `wrangler.jsonc`. Configure Worker name, mode, bindings and non-secret
storage/catalog settings directly in that file. It has no custom build command;
the included pinned Wrangler command deploys the supplied JS/WASM without Rust
or Emscripten installed. Supply `API_KEY` and, for DuckLake mode,
`QUACKLAKE_JWT` using secret prompts. S3 transport needs its explicit settings
and credentials as described in [operations](operations.md).

The operator still needs to verify deployed startup, CPU, cold/warm queries and
total isolate memory. For full/DuckLake acceptance, read real external Parquet
with deletion handling through the native R2 binding without S3 credentials,
then compare the same catalog/bucket through R2's S3 API. Local MinIO is a generic
S3 fixture and does not establish that Cloudflare parity. Do not infer these
properties from a dry-run, pool counters or `SELECT 1`.

## GitHub safeguards

The reviewed branch-protection configuration is
[`.github/branch-protection.json`](../.github/branch-protection.json). An owner
can apply it to an otherwise unconfigured repository with:

```bash
gh api --method PUT repos/tobilg/datafusion-ducklake-workers/branches/main/protection \
  --input .github/branch-protection.json
```

For a fork or a repository with existing rules, review and merge the desired
settings instead of overwriting unrelated protections. No approving-review
requirement is imposed on a solo maintainer.

GitHub offers [private vulnerability reporting for public repositories](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository).
Enable and verify it after changing visibility. The endpoint returns 404 while this repository is private;
repository visibility is deliberately a separate owner action.

```bash
gh api --method PUT repos/tobilg/datafusion-ducklake-workers/private-vulnerability-reporting
gh api repos/tobilg/datafusion-ducklake-workers/private-vulnerability-reporting
```

Require `enabled: true` and verify that the Security tab offers private reporting.
Follow [SECURITY.md](../SECURITY.md) for reporting and support scope. No scheduled
validation or automatic Cloudflare deployment is configured.
