# Third-party components

The repository's original application and tooling are MIT licensed. Dependencies,
upstream source patches and generated bundles retain their respective licenses.
This inventory identifies major components; it is not a license change or a
complete transitive dependency notice bundle.

| Component | Upstream license | Use |
| --- | --- | --- |
| Apache DataFusion, Arrow and Parquet | Apache-2.0 | Query engine and formats |
| cloudflare/workers-rs | Apache-2.0 | Worker SDK and managed build tooling |
| tobilg/datafusion-ducklake-provider | MIT | Optional catalog provider |
| tobilg/quacklake | MIT | Separate catalog fixture/service |
| object_store | MIT / Apache-2.0 | Storage API and S3 transport |
| reqwest | MIT OR Apache-2.0 | HTTP transport |
| ring | Apache-2.0 AND ISC; additional upstream notices | TLS cryptography |
| Tokio / mio | MIT | Async runtime and I/O |
| MinIO | AGPL-3.0; upstream NOTICE | Optional source-built local S3 fixture only |

Exact sources and revisions are in `sources.lock.json`, `tools.lock.json` and
`fixtures/minio.lock.json`; Cargo/npm transitive versions are locked separately.
Fetched sources under ignored `vendor/` retain their LICENSE/NOTICE files.
Patch provenance is documented in [patches/README.md](patches/README.md).

Before distributing compiled Worker bundles or a MinIO fixture binary/container,
assemble the applicable transitive license texts and notices from those exact
inputs. MinIO is not linked into, bundled with, or deployed by the query Worker.
No prebuilt third-party binaries are published by this repository's workflows.
