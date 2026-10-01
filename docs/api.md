# API and boundaries

`GET /healthz` returns `ok` without opening a catalog. `GET /readyz` authenticates and validates configuration/engine initialization; in DuckLake mode it also connects to the existing catalog and registers a read-only snapshot. `POST /query` accepts exactly `sql`, optional `max_rows`, optional `timeout_ms`. Unknown fields and attempts to raise configured limits fail. Store the caller credential in the `API_KEY` secret and send it as `Authorization: Bearer <API_KEY>`. It must contain 32–4096 bytes. Generate a high-entropy token, independent of the catalog JWT.

Success contains `request_id`, `columns` (`name`, Arrow `type`), `rows` as arrays, `row_count`, `truncated`, and `elapsed_ms`. Row arrays preserve duplicate column positions. No raw SQL, values, tokens, internal ATTACH SQL or upstream error text is logged. Structured logs record request ID, status/category, wall time, rows/response bytes, backend, snapshot, object range count and fetched bytes. Failed/cancelled requests currently report status/time but do not publish partial read counters.

| Type | JSON |
| --- | --- |
| Null, boolean, UTF-8 (including view/large variants) | null, boolean, string |
| Int8/16/32, UInt8/16/32 | number |
| Int64/UInt64 | decimal integer string |
| Decimal128/256 | exact decimal string with declared scale |
| Float32/64 | finite JSON number; NaN/infinity rejected |
| Binary/large/view/fixed binary | standard padded base64 |
| Date32 | signed days since 1970-01-01, as string |
| Date64 | signed milliseconds since Unix epoch, as string |
| Timestamp | signed ticks since Unix epoch in the Arrow `type` unit, as string; timezone annotation is retained in `type` |
| Time32/64 | signed ticks since midnight in the Arrow `type` unit, as string |
| Duration | signed ticks in the Arrow `type` unit, as string |
| Nested, dictionary, interval and other output types | explicit 422; no silent coercion |

Timestamp strings are **numeric ticks, not ISO dates**. For example `Timestamp(Nanosecond, None)` and `1788266096000000000` represent the fixture's 2026-09-01 12:34:56 timestamp. Consumers must use the reported unit and timezone. Native reference comparisons normalize to that same unit.

| Error | Status |
| --- | --- |
| Invalid JSON, SQL, limits, fields or content type | 400 |
| Missing/wrong caller token | 401 |
| Disallowed statement, function, source or plan | 403 |
| Body over 32 KiB | 413 |
| Unsupported output or bounded-resource failure | 422 |
| Another query owns this isolate's permit | 429 |
| Missing configuration/service secret, catalog/storage failure | 503 |
| Cooperative deadline exceeded | 504 |

Errors contain `{request_id,error:{code,message}}`. Responses use `Cache-Control: no-store`. A wrong service JWT produces a service error, not caller 401. Wrong methods/unrecognized routes return 404. The `protocol-probe` Cargo feature exposes unauthenticated diagnostic routes **for local testing only**; it is absent from production builds.

The SQL parser accepts one query statement, nonrecursive CTEs and supported subqueries. The logical-plan walk includes subqueries and permits only read operators and actual `DuckLakeTable<QuackCatalog>` sources. Other catalog names, file/URL sources, table functions, writes, COPY, session settings, recursive CTEs and unapproved functions are rejected. String concatenation (`||`) is also rejected to prevent repeated CTE expansion from allocating large intermediate strings outside the pool. Only explicitly listed scalar/aggregate/window functions in `src/policy.rs` are available through the API. Stored views currently fail the resolved-provider check; view expansion is not an MVP promise. No plugins, UDF registration, arbitrary external sources or per-user row/column security are exposed.

Each request selects the latest snapshot once and registers that exact version for its tables. Later requests discover later snapshots. This does not protect against an external operator prematurely removing files retained by an in-flight snapshot; concurrent schema/data maintenance still requires operational coordination.

Pilot defaults are 48 MiB DataFusion pool, one target partition, 1024-row batches, two simultaneous object reads, 1000 output rows, 1 MiB total encoded response, 16 KiB SQL, 32 KiB body, one active query per isolate, and a 10-second deadline including setup. The pre-planning AST walk caps query depth at eight including the outer query, expression depth at 32, and visited nodes at 2,048; the resolved plan also has a 2,048-node cap. Deployment vars may lower pool/rows/bytes/deadline, never raise them. No disk spill. A row that cannot fit by itself is 422; an extra row or byte encounter sets `truncated=true`. SQL's own LIMIT/OFFSET is preserved.

Native R2 copies at most 1 MiB from JS per range chunk. Both stores cap logical reads at 4 MiB, multiple ranges at 8 MiB/128 ranges, and listing at 8192 objects. They reject all write methods and historical-version reads. A sealed registry prevents fallback/replacement and permits only the configured canonical `r2://bucket` root. Independent key checks enforce `catalogs/catalogId/`; this is application defense in depth, **not a prefix-scoped R2 binding**.

Quack HTTP responses are capped at 2 MiB with a 5-second HTTP timeout and no redirects. On Emscripten the decoder bounds counts/fields to 16384 and accounted allocations to 8 MiB per response, and rejects nested/encoded-expression/compressed-vector metadata encodings. Fetch chains are capped. These are explicit pilot compatibility limits; large catalogs can fail closed. Parquet pages are capped at 8 MiB before decompression, with streaming-codec output bounded separately. Oversized column chunks fail the 4 MiB object-read budget rather than buffering entire files.

These bounds do not prove total isolate memory below 128 MB. Pool accounting excludes many Arrow, codec, metadata, JS and WASM allocations. LIMIT cannot bound scan/sort/join costs. Tokio deadlines are cooperative and cannot preempt non-yielding Rust; the configured platform CPU limit is the final boundary. Streams are dropped and a best-effort catalog disconnect runs for up to 500 ms after timeout/error/success. A cancelled connection attempt can leave a server session whose ID the client never received; server expiry remains necessary. Isolate concurrency is not a global service limit.

## File mode

`QUERY_MODE=files` permits quoted Parquet object URLs and bucket prefixes directly
in SQL, using native R2, S3-compatible storage, or public HTTPS. The request and
response shapes and caller `API_KEY` are unchanged. File mode requires no
QuackLake credential; its readiness check validates configuration/engine rather
than an arbitrary dataset. CSV, JSON, external-table DDL, and file table functions
remain unsupported. See [configuration, examples, and limits](build-variants.md).

The catalog-only source restrictions above apply to `QUERY_MODE=ducklake`. File
mode validates URLs before I/O and then checks every resolved table provider.
