# Changelog

## Unreleased

- Core and full Rust DataFusion Workers on the pinned Emscripten/Tokio stack.
- Read-only Parquet queries over native R2, explicit S3 and public HTTPS.
- Optional DuckLake metadata through QuackLake with canonical R2 paths.
- Separate caller API key, bounded execution/output and request-scoped sessions.
- Managed local integration runner, pinned builds and public contribution workflow.
- Complete vendor-source verification, assignment-aware secret checks, JSONC
  diagnostics, generated local fixture configs and automated transport/catalog tests.
- Cached CI tooling/dependencies, documentation-only build gating, verified size
  measurements without rebuilding, manual uncached checks and build layout
  comparisons with phase timings. All CI jobs use Linux.

Initial release status is experimental. Cloud acceptance and hosted CI execution
must be recorded separately; historical validation files are not distributed.
