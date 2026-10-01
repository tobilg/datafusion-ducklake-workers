# Core and full builds

Both variants run DataFusion 55.0.0 inside the Emscripten Worker, using Rust
toolchain channel **1.98.0** and the pinned managed SDK. Neither build delegates
SQL execution to another service. Parquet is the only direct-file format.

| Variant | Build | Artifact | Configuration |
| --- | --- | --- | --- |
| Core | `bash scripts/build.sh --variant core` | `build/core/index.js` | `wrangler.core.jsonc` |
| Full (default) | `bash scripts/build.sh --variant full` | `build/full/index.js` | `wrangler.jsonc` |

Core disables Cargo defaults. Full enables the optional `ducklake` feature.
Both include native R2, S3-compatible transport, and HTTPS Parquet support.
The shared lockfile still lists optional dependencies; the production target
graphs demonstrate which dependencies each variant actually compiles.
The provider's default features stay disabled; only `quack` and
`object-store-s3` are enabled in full.

`QUERY_MODE=files` selects direct files. `QUERY_MODE=ducklake` selects the
existing catalog service and is only available in full. Defaults are files
for core and ducklake for full. A full deployment in file mode needs no
QuackLake configuration or JWT. The mode is a deployment setting, not a query
parameter. Switching modes does not change the compiled artifact's size.

For a full build in file mode, start with `wrangler.core.jsonc` and change both
`main` and `build.command` to the full variant. Keep `QUERY_MODE=files` and add
only the storage capabilities you need.

Core cannot gain DuckLake by changing a variable: deploy the full artifact and
its catalog configuration first. Building both variants initially compiles some
shared crates twice because their resolved feature sets differ. Subsequent
unchanged builds reuse Cargo's variant-specific cache. Keep the `main` path,
build command and deployment mode together when releasing or rolling back.
The build script holds a shared lock through compilation, JS/WASM collection,
and manifest creation. Use the script when building both variants; raw concurrent
`worker-build` invocations share intermediate filenames after Cargo releases its
own lock. Local diagnostic builds use separate `build/core-probe` and
`build/full-probe` directories and never overwrite the deployable artifacts.
Full does not mix DuckLake tables and direct file URLs in one query. File mode
can join its configured R2/S3 sources with public HTTPS sources.

## Direct Wrangler configuration and deployment

Run the existing bootstrap instructions in [setup](setup.md), using the exact
source/tool pins and `npm ci`. Edit the appropriate root Wrangler configuration
directly: set your Worker name, account if needed, and routes or `workers_dev`.
No configuration generator is required. Preserve its variant-specific `main`
and `build.command` together. Existing custom configurations pointing to
`build/index.js` must change to `build/full/index.js`.

For public HTTPS Parquet files, core only needs the `API_KEY` secret:

```sh
bash scripts/size.sh core
./node_modules/.bin/wrangler deploy --config wrangler.core.jsonc
./node_modules/.bin/wrangler secret put API_KEY --config wrangler.core.jsonc
```

The final command deploys to your Cloudflare account.
Use an API key of 32–4096 bytes. A new deployment fails closed until its API key is configured.

The request body remains `sql`, optional `max_rows`, and optional `timeout_ms`.
Send `Authorization: Bearer <API_KEY>` to `POST /query`; unknown body fields
are rejected. For example:

```json
{"sql":"SELECT count(*), sum(amount) FROM 's3://analytics/events/'"}
```

Single-quoted or double-quoted URLs are source names, not SQL table functions.
Aliases, CTEs, and joins across file sources are supported. There is no
`CREATE EXTERNAL TABLE` endpoint or persistent table registration.

## Storage access

**Native R2:** add actual bucket bindings and a JSON string mapping bucket names
to binding names. Dataset paths come from SQL, not this configuration:

```jsonc
"vars": {
  "QUERY_MODE": "files",
  "R2_BINDINGS": "{\"analytics\":\"ANALYTICS_R2\"}"
},
"r2_buckets": [
  { "binding": "ANALYTICS_R2", "bucket_name": "analytics" }
]
```

Query `'r2://analytics/exports/sales.parquet'` or
`'r2://analytics/exports/'`. No S3 credentials are used. An unknown binding
fails; it never falls back to S3. R2 bindings grant bucket-wide access. File
mode may map multiple buckets; DuckLake mode continues binding only its own
dedicated catalog bucket and enforcing its canonical catalog prefix.

**S3:** bucket names come from `s3://<bucket>/<key-or-prefix>`. One deployment
has one S3 endpoint/region/credential profile, usable across multiple buckets.
`S3_ENDPOINT` is optional for AWS; `S3_REGION` defaults to `us-east-1` and
`S3_ADDRESSING_STYLE` to `path`. Set the actual region when different.
Generic S3-compatible services require their HTTPS endpoint. For R2's S3 API,
set the account's R2 endpoint and `S3_REGION=auto`.

For private storage, provide `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, and
optionally `S3_SESSION_TOKEN` as Worker secrets. Missing both keys selects
unsigned requests, useful for public buckets. Partial credentials are a
configuration error. There is no metadata-service credential discovery or
anonymous fallback after an authenticated failure. Prefix queries also need
permission to list objects.

**HTTPS:** use a full public URL ending in `.parquet`. No origin allowlist or
bucket configuration is required. URL query parameters are preserved, including
GET-signed URLs where the remote server permits ranged GETs. No Worker secrets
are attached to these requests. Redirects are bounded and revalidated; non-public
IP destinations, local files, and unsupported schemes are rejected. Plain HTTP
is only supported for explicit loopback fixtures with `ALLOW_LOCAL_HTTP=true`.

HTTPS sources must support correct byte ranges and expose total size through
HEAD or a one-byte range probe. ETags/last-modified validators are used when
available; sources without validators cannot guarantee consistency across
changes during a query. Use immutable files. HTTPS directory discovery,
extensionless URLs, other formats, and custom request authentication headers
are unsupported.

Every API-key holder can read the mapped R2 buckets, the objects authorized by
the deployment's S3 credentials, and public HTTPS Parquet. Use read-only S3
credentials with the intended scope. The API provides no per-caller isolation
or table-level permissions in file mode.

## Bounds and semantics

All sources share two active read permits and a ten-second cooperative deadline,
including discovery and schema inference. The existing 48 MiB DataFusion pool,
one target partition, 1,024-row batches, 1,000 rows, 1 MiB encoded response,
4 MiB individual range, 8 MiB multi-range, and listing limits remain in effect.
At most 16 distinct URL sources are accepted. SQL is limited to eight nested
query nodes (including the outer query), 32 nested expression nodes and 2,048
visited AST/plan nodes. These are pilot limits, not
guarantees of total isolate memory or scan cost.

Prefixes must end in `/`; only `.parquet` files participate. Schemas must be
compatible. Hive directory components are not automatically exposed as columns.
File mode provides no transactional snapshot across objects or tables.

DuckLake mode preserves its catalog metadata, snapshots, deletes, and schema
handling. Directly scanning a DuckLake storage prefix does not implement those
semantics and may include obsolete files or deleted rows.

`GET /readyz` authenticates and checks configuration/engine initialization in file
mode; arbitrary remote source availability is only known when queried. Catalog
mode continues probing actual metadata. Logs include mode, row counts, read
counts/bytes, and request IDs; they omit source URLs and credentials.

## Local verification

Use the managed test runner in [setup](setup.md). It starts/stops services,
generates independent Parquet and prepares only the selected backend. Examples:

```sh
python3 scripts/validate.py --suite files --variant core --backend r2
python3 scripts/validate.py --suite files --variant all --backend all
```

Reports and manifests are generated under ignored `.cache/reports/`. They are
not committed release evidence. See [diagnostics](diagnostics.md) for probes and
profiling, and [compatibility](compatibility.md) for remaining platform acceptance.
