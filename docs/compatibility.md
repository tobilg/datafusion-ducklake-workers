# Compatibility and release status

This project is experimental. The supported architecture is DataFusion executing
inside `wasm32-unknown-emscripten` through Workers' experimental Tokio integration.
The application uses an empty binary main, one target partition, request-scoped
sessions and the managed SDK. It does not create a nested Tokio runtime.

| Component | Pin |
| --- | --- |
| Rust | 1.98.0 |
| DataFusion / Arrow / Parquet | 55.0.0 / 59.2.0 / 59.2.0 |
| object_store | 0.13.2 |
| workers-rs | b57ba6ef8198c65499c2f92b1845cc2412dd6e8c |
| DuckLake provider | 6b97e804e50c4765964e0362dc5e665e1c630f72 |
| QuackLake | 0.2.1, b3ef21778aa809763bc83e0472eb70d84c1fe906 |
| Wrangler / Miniflare / workerd | 4.147.0 / 5.20261001.0-alpha / 1.20261001.1 |
| quick-xml / rustls | 0.41.0 / 0.23.45 |
| Managed Emscripten / wasm-bindgen / wasm-opt | 6.0.10 / 0.2.129 / 132 |

Full source/tool and preview Tokio/mio/libc pins are in `sources.lock.json`,
`tools.lock.json`, `Cargo.lock` and `package-lock.json`. Compatibility patches
remain necessary at these pins; see [their rationale](../patches/README.md).
QuackLake includes the UUID casting fix upstream and needs no downstream patch.
Its upstream Worker test runner has a Vitest/pool version mismatch at this pin;
the local integration runner exercises the actual service/provider independently.

Linux arm64 builds and integration suites have passed GitHub Actions; macOS arm64
is also exercised locally. Check the current commit's [CI results](https://github.com/tobilg/datafusion-ducklake-workers/actions/workflows/ci.yml)
and generate local results with the commands in [setup](setup.md). Historical
reports are not distributed. A local test, dry-run or SELECT 1 is not deployed acceptance.

For a public release, record the final source revision and run both variant
builds, dependency checks, the local file/catalog suites and size checks. The
operator must separately test actual R2 bindings and the same bucket/catalog
through R2's S3 API, then measure startup, CPU, cold/warm queries and total memory.
Generic MinIO and local workerd results do not establish those properties.

Cloudflare documents a **64 MiB uncompressed entire-bundle** limit, **128 MB per
isolate**, and **one second** of global startup. The project's size check aims for
56 MiB. Use a Workers Paid plan for the supplied 30-second CPU configuration;
Free allows only 10 ms CPU. Recheck [official limits](https://developers.cloudflare.com/workers/platform/limits/)
when deploying. The application's ten-second timeout remains cooperative and
its 48 MiB pool does not cover all isolate allocations.

CSV/JSON, writes, maintenance, table functions, per-user row/column security,
HTTPS directory discovery, and mixed DuckLake/direct-file queries are unsupported.
File mode does not implement DuckLake snapshots/deletion semantics. See
[API restrictions](api.md) and [storage configuration](build-variants.md).
