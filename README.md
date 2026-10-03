# DataFusion DuckLake Workers

Run read-only SQL inside a Rust Cloudflare Worker against remote Parquet files,
with optional DuckLake metadata from QuackLake. DataFusion runs in the Worker,
compiled for `wasm32-unknown-emscripten` with the experimental Tokio integration.

| Build | Query sources | Wrangler config |
| --- | --- | --- |
| **Core** | Parquet files/prefixes over native R2, S3-compatible storage, and individual HTTPS files | `wrangler.core.jsonc` |
| **Full** | Core capabilities, or a DuckLake catalog served by QuackLake | `wrangler.jsonc` |

This is an **experimental** project. Both variants have passed Linux arm64 CI;
macOS arm64 is also tested locally. Cloud deployment,
live R2/S3 parity, platform CPU and total isolate-memory capacity require operator
acceptance. See [compatibility and limits](docs/compatibility.md).

## Quickstart: core

Install Git, rustup, a C compiler, ripgrep, Python 3.12+, and
Node 22.22.2/npm 10.9.7. Allow roughly 20 GiB for initial build tools and artifacts.
Rust **1.98.0**, source revisions, managed Emscripten tooling and dependencies are
pinned. QuackLake, Docker, Go and DuckDB are unnecessary for this quickstart.

```sh
git clone https://github.com/tobilg/datafusion-ducklake-workers.git
cd datafusion-ducklake-workers
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

For real Parquet reads, try the [example queries](#example-queries) below.
For R2/S3 prefix queries and DuckLake, follow [configuration and build variants](docs/build-variants.md).
Caller authentication uses the `API_KEY` Worker secret, which must contain
**32–4096 bytes**. Generate a key with:

```sh
openssl rand -hex 32
```

This produces 32 random bytes encoded as 64 hexadecimal characters, satisfying
the length requirement. Store that value as the deployed Worker's `API_KEY`
secret and send the same value in `Authorization: Bearer <API_KEY>`. Setting a
local shell variable alone does not configure the deployed secret. A missing
or invalid-length secret causes HTTP 503.

DuckLake additionally uses a separate `QUACKLAKE_JWT`. The query Worker never
needs catalog admin/signing secrets.

## Quickstart: full (DuckLake)

This deploys the full build against an existing QuackLake catalog using native
R2 access. Use the same local tools and clone/bootstrap steps as the core
quickstart. Use a [Workers Paid plan](https://developers.cloudflare.com/workers/platform/limits/#cpu-time)
for the configured CPU allowance.

Before deploying, prepare:

- A compatible QuackLake service and a `catalog_only` catalog with **initialized
  DuckLake metadata** and a table containing external Parquet data. Creating the
  catalog registry entry alone does not initialize its metadata.
- **One dedicated R2 bucket for that catalog**, configured in QuackLake, with
  the exact data path `r2://<bucket>/catalogs/<catalogId>/`.
- A dedicated reader service JWT and a catalog policy allowing its metadata
  reads. A valid JWT alone is insufficient. Keep admin/signing secrets outside
  this query Worker.

Follow the [catalog provisioning guide](docs/operations.md#provisioning-order)
if these resources are not ready. The query Worker opens existing catalogs
read-only; it does not create or migrate them.

Edit the existing root `wrangler.jsonc` directly. Keep its `main` pointing to
`build/full/index.js`, its full build command, and its compatibility settings.
Replace these fields with your deployment values:

| Field | Value |
| --- | --- |
| `name` | Your full Worker's name, e.g. `datafusion-ducklake-full` |
| `account_id` | Add your Cloudflare account ID |
| `workers_dev` | `true` to enable the public Workers URL, or configure a custom route |
| `vars.QUERY_MODE` | `ducklake` |
| `vars.STORAGE_BACKEND` | `r2_binding` |
| `vars.QUACK_URI` | `quack:<quacklake-host>:443` (no `https://` or `/quack`) |
| `vars.CATALOG_ID` | Your catalog ID, e.g. `analytics` |
| `vars.CATALOG_BUCKET` | The catalog's dedicated R2 bucket |
| `vars.CATALOG_DATA_PATH` | Exactly `r2://<bucket>/catalogs/<catalogId>/` |
| `r2_buckets[0].binding` | `CATALOG_R2` |
| `r2_buckets[0].bucket_name` | The same bucket as `CATALOG_BUCKET` |

Set the binding's `jurisdiction` too if your bucket requires it. This native R2
configuration needs no S3 credentials. The binding grants bucket-wide access;
the application enforces the catalog prefix and read-only operations.

From the repository root, log in and deploy. Wrangler runs the full release
build automatically, then the secret commands prompt for their values:

```sh
npx wrangler login
npx wrangler deploy --config wrangler.jsonc
npx wrangler secret put QUACKLAKE_JWT --config wrangler.jsonc
npx wrangler secret put API_KEY --config wrangler.jsonc
```

Use the catalog reader JWT for `QUACKLAKE_JWT` and a separate **32–4096 byte**
caller key for `API_KEY` (`openssl rand -hex 32` generates a suitable key).
The new endpoint rejects queries until its required secrets are configured.
Secrets belong in Wrangler's prompts, not `vars` or committed files.

Set the URL printed by Wrangler and enter that same caller key locally. Check
readiness, then query a table; replace `lake.main.sales` with an existing table
in your catalog. `lake` is the Worker's catalog alias; `main` is the example's
DuckLake schema, independent of your configured catalog ID.

```sh
WORKER_URL='https://<worker-name>.<account-subdomain>.workers.dev'
API_KEY="$(python3 -c 'import getpass; print(getpass.getpass("Caller API key: "))')"

curl --fail-with-body "$WORKER_URL/readyz" \
  -H "Authorization: Bearer $API_KEY"

curl --fail-with-body "$WORKER_URL/query" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"sql":"SELECT * FROM lake.main.sales LIMIT 10","max_rows":10}'
```

For S3 transport, credential rotation and troubleshooting, see the
[operations guide](docs/operations.md). For a fully local catalog fixture, use
the [catalog integration workflow](docs/setup.md#catalog-integration-tests).
The full build can also run in `QUERY_MODE=files`; see
[build variants](docs/build-variants.md) for that configuration. Each deployment
selects one mode; DuckLake mode queries catalog tables rather than direct URLs.

## Example queries

These examples use core (or full with `QUERY_MODE=files`) and the public
[AWS edge locations dataset](https://github.com/tobilg/aws-edge-locations).
No storage credentials are needed for this HTTPS source. Set `WORKER_URL` to
your local server or deployed Worker URL, without a trailing slash, and use the
matching caller key in `$API_KEY`:

```sh
WORKER_URL='http://127.0.0.1:8787'
# For a deployment, use https://<worker-name>.<account-subdomain>.workers.dev
PARQUET_URL='https://raw.githubusercontent.com/tobilg/aws-edge-locations/main/data/aws-edge-locations.parquet'
```

Read ten locations:

```sh
curl --fail-with-body "$WORKER_URL/query" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  --data "{\"sql\":\"SELECT * FROM '$PARQUET_URL' LIMIT 10\",\"max_rows\":10}"
```

Select columns and filter locations in Germany:

```sh
curl --fail-with-body "$WORKER_URL/query" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  --data "{\"sql\":\"SELECT code, city, country FROM '$PARQUET_URL' WHERE country = 'Germany' ORDER BY city, code LIMIT 20\"}"
```

Count locations by country and return the ten largest groups:

```sh
curl --fail-with-body "$WORKER_URL/query" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  --data "{\"sql\":\"SELECT country, count(*) AS locations FROM '$PARQUET_URL' GROUP BY country ORDER BY locations DESC, country LIMIT 10\"}"
```

The outer shell string uses double quotes with escaped JSON quotes, preserving
the SQL single quotes around URLs and string values. These examples use the
dataset's current `main` revision; pin a commit in the URL for reproducible
results. HTTPS sources must support ranged GET requests.

After configuring your own [R2 bindings or S3 access](docs/build-variants.md#storage-access),
you can replace the SQL with these queries. The bucket and paths are examples;
they must exist and be accessible to your Worker.

| Source | SQL |
| --- | --- |
| Native R2 file | `SELECT * FROM 'r2://analytics/exports/sales.parquet' LIMIT 10` |
| Native R2 prefix | `SELECT count(*) AS events FROM 'r2://analytics/events/'` |
| S3 prefix | `SELECT count(*) AS events FROM 's3://analytics/events/'` |

Prefix URLs must end in `/`; only Parquet files with compatible schemas are
included. Results are capped at 1,000 rows and 1 MiB of JSON. `LIMIT` bounds the
returned rows, not the work needed for scans, joins, or aggregations. See the
[API reference](docs/api.md) for response types and execution limits.

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
