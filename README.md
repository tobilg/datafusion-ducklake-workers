# DataFusion on Cloudflare Workers

Run read-only SQL inside a Rust Cloudflare Worker against remote Parquet files,
with optional DuckLake metadata from QuackLake. DataFusion runs in the Worker,
compiled for `wasm32-unknown-emscripten` with the experimental Tokio integration.

| Build | Query sources | Wrangler config |
| --- | --- | --- |
| **Core** | Parquet files/prefixes over native R2, S3-compatible storage, and individual HTTPS files | `wrangler.core.jsonc` |
| **Full** | Core capabilities, or a DuckLake catalog served by QuackLake | `wrangler.jsonc` |

This is an **experimental** project. macOS arm64 is the locally tested build
platform. Linux and hosted CI execution remain to be verified. Cloud deployment,
live R2/S3 parity, platform CPU and total isolate-memory capacity require operator
acceptance. See [compatibility and limits](docs/compatibility.md).

## Quickstart: core

Install Git, rustup, a C compiler, ripgrep, Python 3.12+, and
Node 22.22.2/npm 10.9.7. Allow roughly 20 GiB for initial build tools and artifacts.
Rust **1.98.0**, source revisions, managed Emscripten tooling and dependencies are
pinned. QuackLake, Docker, Go and DuckDB are unnecessary for this quickstart.

```sh
git clone https://github.com/tobilg/datafusion-quacklake-workers.git
cd datafusion-quacklake-workers
npm run bootstrap
# Prepares a local API key, builds with the development link, and starts workerd.
npm run dev
```

In another terminal, load the locally generated key and execute SQL:

```sh
export API_KEY="$(cat .cache/query-api-token)"
curl http://127.0.0.1:8787/query \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"sql":"SELECT 1 AS value"}'
```

For a real remote Parquet read, use an immutable HTTPS file ending in `.parquet`:

```json
{"sql":"SELECT count(*) FROM 'https://raw.githubusercontent.com/apache/parquet-testing/56653c437c8092f704a092d0d1d4e600124cd49f/data/alltypes_plain.parquet'"}
```

This pinned Apache Parquet fixture contains eight rows. Your own HTTPS sources
must support ranged GET requests.
For R2/S3 prefix queries and DuckLake, follow [configuration and build variants](docs/build-variants.md).
Caller authentication uses `API_KEY`; DuckLake additionally uses a separate
`QUACKLAKE_JWT`. The query Worker never needs catalog admin/signing secrets.

## Development

```sh
npm run check
# Build, prepare independent Parquet, start services, test, and stop services:
python3 scripts/install-duckdb.py
npm test
```

Integration fixtures additionally require DuckDB 1.5.5. Optional S3 fixtures use
source-built MinIO; no registry image is required. See [development workflows](docs/setup.md).
Generated builds, secrets and diagnostic reports remain ignored. Historical
validation artifacts are not shipped with the repository.

- [API, type encoding and restrictions](docs/api.md)
- [Deployment, catalog setup, rotation and rollback](docs/operations.md)
- [Contributing and CI](CONTRIBUTING.md)
- [Compatibility patches](patches/README.md)
- [Security reporting](SECURITY.md)
- [Third-party components](THIRD_PARTY_NOTICES.md)

Project source is licensed under [MIT](LICENSE).
