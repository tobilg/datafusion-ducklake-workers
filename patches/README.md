# Compatibility patches

`series.json` is the single ordered inventory of the 14 active patches. Apply
with `python3 scripts/apply-patches.py` after fetching pinned sources. Application
refuses conflicting local edits and recognizes already-applied patches.
`python3 scripts/verify-patches.py` applies the series to pristine source exports
and compares every source file, including modes, symlinks, unexpected files and
required submodules. Known build outputs are excluded; other ignored source
files still count. The provider's unused DuckDB extension submodules may remain
absent. `--fixtures` additionally checks QuackLake and installed MinIO sources.
Builds check source trees before and after compilation and record the resulting
digest with the artifact. Reports remain local under `.cache/reports/`.

| Patch | Scope and reason |
| --- | --- |
| 0001 worker-build snippets | Host build tooling: find companion JS snippets belonging to the exact emitted Emscripten output. Supports both Rust 1.98 and the investigated newer Cargo directory layout; avoids collecting another build's snippets. |
| 0002 reqwest transport | Emscripten: use native asynchronous HTTP/TLS instead of the browser WASM implementation. Keep native-host and browser behavior unchanged. |
| 0003 ring randomness | Emscripten: enable the existing getrandom/libc getentropy implementation. No replacement RNG or weakened randomness. Source is the checksummed published crate, not an assumed Git tag. |
| 0004 object_store HTTP | Emscripten: compile native HTTP response/configuration plumbing. Disable address randomization in the application because the upstream shuffle resolver needs spawn_blocking. |
| 0005 DataFusion file payloads | Emscripten: make Arrow/CSV/JSON datasource matches exhaustive while explicitly rejecting local-file payloads. Does not enable those formats in the query API. |
| 0006 provider features/API | Disable the actual provider workspace's DataFusion defaults; expose existing typed read-only registration APIs. Supports request snapshots and explicit disconnect without token-bearing SQL in the query handler. |
| 0008 R2 range dictionary | SDK binding: read R2-returned ranges as imported JS dictionaries, validating safe integers. Avoid a Rust-class cast that traps on real binding responses. |
| 0009 Quack response bounds | Transport: five-second request/three-second connect timeouts, no redirects, 2 MiB responses and bounded fetch chains, including the native fixture client. Emscripten decoder additionally bounds counts/allocations and rejects unsupported compressed/nested encodings. Large catalogs may be rejected. |
| 0010 Parquet bounds | Emscripten: reject pages above 8 MiB before allocation; bound streaming codec output and LZ4 loops. Native behavior is unchanged. These checks do not account for every Arrow/codec allocation. |
| 0011 example lockfiles | Official timer/TCP examples: match worker package versions and remove a redundant unused libc patch record that destabilizes locked Cargo resolution. |
| 0013 schema history | Provider read planning, all backends: fall back to snapshot history when optional per-table schema history is empty, preserving historical inlined rows after unrelated schema changes. Adds metadata lookups; does not mutate metadata. |
| 0014 runtime cleanup | Managed SDK: detach anonymous socket/pipe nodes, cancel epoll deliveries and reclaim completed/cancelled timer handles. Preserve required socket/DNS patches and reject unknown SDK patch stamps. |
| 0015 mio FD clone | Emscripten/Rust 1.98: use fcntl descriptor duplication because std OwnedFd::try_clone rejects this target. Preserve independent descriptor ownership and other targets' behavior. |
| 0016 S3 listing bounds | Emscripten: cap each listing response at 2 MiB before XML decoding, including unknown-length responses. Application-wide object listing budgets remain separate. |

Source revisions/checksums are in `sources.lock.json` and `tools.lock.json`.
Each patch names its source in the series manifest. QuackLake 0.2.1 includes the
former UUID casting and fixture build-policy fixes upstream; retired patches
0007 and 0012 are no longer shipped or applied.

The application uses root preview Tokio/mio/libc overrides. Both libc dependency
origins resolve to the same pinned source. Do not replace these with arbitrary
Emscripten packages or remove S3 to shrink a release. Cargo features are additive:
disabling only the provider defaults does not disable its DataFusion defaults.

All application builds reserve a 256 KiB stack; bounded SQL nesting protects
planning frames. `WORKER_LINK_OPT=1` is a faster development link, while release
uses `-Os`. Measure the current complete bundle with `scripts/size.sh`; no old
artifact-size report is a release gate.

The [local suites](../docs/setup.md) cover actual R2 JS bindings, signed S3,
provider metadata, deletes and schema history. [Diagnostics](../docs/diagnostics.md)
cover timer cleanup, network probes, memory and startup. Native tests alone do
not establish Worker binding lifetimes or platform capacity. Keep compatibility
updates focused, preserve the source pins and run affected target regressions.
