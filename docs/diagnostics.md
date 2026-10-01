# Focused diagnostics

Use the pinned tools and local fixtures from [setup](setup.md). Diagnostics write
to ignored `.cache/reports/`. Never deploy probe artifacts or commit heap dumps,
which can contain query data and credentials.

| Purpose | Command |
| --- | --- |
| Official timer baseline | `bash scripts/build-baseline.sh`, then `bash scripts/dev-baseline.sh` and `python3 scripts/smoke-baseline.py` in another terminal |
| TLS/DNS/ring probe | `bash scripts/probe-https.sh` |
| Managed protocol/R2/timer probes | `WORKER_LINK_OPT=1 python3 scripts/validate.py --suite probes` |
| One variant's probes | Add `--variant core` or `--variant full` to that command |
| Startup profile | `bash scripts/profile-startup.sh core` or `full`; optionally append a custom JSONC path |
| File query latency | `python3 scripts/benchmark-files.py --variant core` |
| Catalog query latency | `python3 scripts/benchmark-local.py` with catalog Workers running |
| WASM allocation wrapper | `python3 scripts/run-memory-probe.py r2 --files --variant core` |
| Repeated-query JS heap | `node scripts/observe-local-memory.mjs 200 files-core-r2` with the corresponding Worker running |
| Runtime timer cleanup | `node scripts/test-runtime-cleanup.mjs <managed-emscripten-directory>` |

Probes live under `build/core-probe/` and `build/full-probe/`, separately from
production outputs. Their unauthenticated routes exist only with `protocol-probe`.
The official timer/TCP example and HTTPS reproducer retain their own source and
lockfiles. They are diagnostic examples, not supported public endpoints.

Patch 0014 addresses retained closed sockets, anonymous pipes, epoll callbacks
and timer handles. The timer cleanup test uses the actual managed SDK source.
Its default SDK path follows the managed macOS/Linux cache and pinned SDK version;
an explicit directory remains available. Startup profiling accepts JSONC comments
and trailing commas, and verifies that the chosen config references the selected
production artifact. For example: `bash scripts/profile-startup.sh core wrangler.local.jsonc`.
Allocation wrappers use a byte-identical WASM module and separate JS. Inspector
heap, linear-memory allocation and wall time are distinct observations; none
alone measures total isolate memory, platform CPU or deployed capacity.
