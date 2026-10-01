# Provision, deploy and operate

Deployment is an explicit operator action. Local fixtures are separate from live resources. For a DuckLake deployment, prepare the account ID, Worker name, QuackLake host and deployed revision, catalog ID, dedicated R2 bucket/jurisdiction, exact catalog path, a reader service JWT and separate caller token supplied securely, and the reference dataset/results. Live S3 parity also needs that bucket's R2 S3 endpoint, region/addressing and narrowly scoped credentials. Never put admin or signing secrets in the query Worker. File mode needs no catalog setup or JWT; follow the [variants guide](build-variants.md) for its direct Wrangler configuration.

Use QuackLake 0.2.1 at `b3ef21778aa809763bc83e0472eb70d84c1fe906`, or a
verified compatible later revision. This pin includes the UUID CAST fix upstream;
no local catalog patch is required. Deploy the catalog update through its own
release process and record its deployed revision. The fix prevents new UUID
corruption; it does not repair numeric UUIDs written by an older server.
See [compatibility](compatibility.md) for the upstream test-runner limitation.

## Provisioning order

1. Create **one dedicated R2 bucket per catalog**, using the intended jurisdiction. With the pinned CLI: `wrangler r2 bucket create <bucket>`; include `--jurisdiction <jurisdiction>` when required. This is an explicit operator infrastructure action, not part of a build.
2. Add exactly that bucket to QuackLake's R2 bindings and `DUCKLAKE_R2_BINDINGS` map, preserving its existing configuration and other policies. Deploy that catalog-service configuration through its normal approval path.
3. Verify binding availability: `python3 scripts/catalog-admin.py buckets --url https://<quacklake-host>`. The admin bearer token is read only from `QUACKLAKE_ADMIN_TOKEN` in the operator environment. This does not require or reveal the signing secret.
4. Create the **registry entry only**: `python3 scripts/catalog-admin.py create-registry --url https://<host> --catalog <id> --bucket <bucket> --output .secrets/bootstrap.json`. It uses `catalog_only`, returns a bootstrap `catalog.admin` credential with a 365-day expiry and verifies `r2://<bucket>/catalogs/<id>/`. Protect the entire response: it includes one-time JWT/SQL material. Existing entries must be inspected, not recreated or pointed to a different path.
5. Install a reviewed catalog policy before attempting metadata SQL. A valid JWT alone is insufficient. [reader-policy.example.json](../fixtures/reader-policy.example.json) is the exact reader capability shape tested locally, not a command to replace a pre-existing policy. A new bootstrap needs a separate temporary administrator rule for its writer; preserve intended other rules, and remove/revoke bootstrap authority after use. `PUT /admin/catalogs/<id>/auth-policy` replaces policy: review and back up the current value first. Never grant the query credential admin rights to work around a denied metadata query.
6. Initialize actual DuckLake metadata and seed representative external objects with an authorized provisioning client. The local native provider fixture exercises this step separately from registry creation. For live initialization, use the pinned QuackLake [getting-started guide](https://github.com/tobilg/quacklake/blob/b3ef21778aa809763bc83e0472eb70d84c1fe906/guides/getting-started.md) with the operator's own S3 credentials, or adapt the typed native provider initialization options from `repro/catalog-native`. The inspected DuckDB guide requires `core_nightly` quack/ducklake extension fixes; remote extension hashes/versions have **not** been validated here and must be recorded before claiming a reproducible remote bootstrap. Flush inlined data and verify real objects. This Worker deliberately cannot create/migrate metadata.
7. Issue the separate service credential: `python3 scripts/catalog-admin.py issue-reader --url https://<host> --catalog <id> --output .secrets/reader-new.json`. It requests only `query.read`, 31536000 seconds. Verify an actual provider metadata query and a denied mutation with this credential before deployment.
8. Edit `wrangler.jsonc` directly for native R2: replace the Worker name,
   `QUACK_URI`, `CATALOG_ID`, `CATALOG_BUCKET`, `CATALOG_DATA_PATH`, and matching
   `r2_buckets` bucket. Set `workers_dev: true` or configure your route. Add
   `account_id` when needed and bucket `jurisdiction` when applicable. Use exactly
   `r2://<bucket>/catalogs/<catalogId>/`; the application rejects mismatches.
   For S3, copy `wrangler.s3.example.jsonc` to ignored `wrangler.local.jsonc` and
   set the same catalog identity plus its S3 endpoint/bucket/region. That example
   binds no R2 bucket. File mode uses the [variant guide](build-variants.md).

The optional `scripts/configure-deployment.py` remains available for scripted
setup; neither builds nor deployment instructions require it.

The native configuration binds exactly one bucket as `CATALOG_R2`. R2 bindings are bucket-wide; no platform setting makes them prefix-scoped or read-only. The application independently restricts prefixes and operations. Use `STORAGE_BACKEND=s3`, `S3_ENDPOINT`, `S3_REGION=auto` and `S3_ADDRESSING_STYLE=path` for R2’s S3 transport. This config has no R2 binding, retains the canonical R2 root, and requires the actual referenced data in the named S3 bucket. There is no fallback between modes. Plain HTTP is only available for explicitly configured loopback tests.

The query URI is `quack:<host>:443`; the provider appends `/quack`. Its SQL wrapper syntax, tested separately, is `ducklake:quack:quack:<host>:443`. DuckDB's generated attachment has different layering; do not paste it into this provider. The query Worker uses typed read-only registration and never logs token-bearing attachment SQL.

## Release and acceptance

Run these commands in Bash. For the S3 example, substitute `wrangler.local.jsonc`
for `wrangler.jsonc` and pass that path to `scripts/size.sh`. Use a Workers Paid
plan for this CPU configuration; see [platform compatibility](compatibility.md).

```sh
bash scripts/build.sh
bash scripts/dependency-graphs.sh
bash scripts/size.sh full
# Operator performs this manual deployment after local checks:
source scripts/env.sh
wrangler deploy --config wrangler.jsonc
wrangler secret put QUACKLAKE_JWT --config wrangler.jsonc
wrangler secret put API_KEY --config wrangler.jsonc
# S3 deployments also require separate S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY
# and optionally S3_SESSION_TOKEN, each supplied through wrangler secret put.
```

For a new Worker, deploying code first creates a fail-closed endpoint: without both secrets queries/readiness return 503; only health is public. Then secret commands can update the existing Worker without implicit placeholder creation. For existing deployments, use the team's normal version/secret release workflow and retain the previous version. Secret changes may deploy a version; record every returned version ID. Do not log shell-expanded tokens, use shell tracing, or store secrets in JSONC vars.

Verify health, authenticated readiness, a fresh catalog connection, exact fixture results and actual object byte/range counters. Exercise native R2 with **all S3 secrets absent**, then the same initialized catalog/bucket through R2's S3 API using a separate explicit staging config. Generic local MinIO success does not replace this test. Record Wrangler's uncompressed **Total Upload**, deployment startup time, platform CPU, cold/warm wall times, failure cases and measured memory behavior. The 64 MiB total bundle, 1-second startup and 128 MB isolate constraints apply; the pilot size goal is at most 56 MiB. A dry-run is not deployment acceptance.

Use `wrangler tail --config wrangler.jsonc` for sanitized request IDs/categories and read counters. Run `python3 scripts/smoke-staging.py --url https://<worker-host> --fixture <acceptance-query.json>` with `API_KEY` supplied securely in the environment. The fixture JSON contains `sql` and exact `expected_rows`; `fixtures/acceptance-query.example.json` matches only this repository's dataset. The script refuses redirects and does not print credentials or result data. Reproduce `scripts/test-api.py` queries against the approved acceptance dataset; that script intentionally accepts only local fixture URLs.

When upgrading a deployment that used `QUERY_API_TOKEN`, install `API_KEY` with the same caller credential before deploying the renamed version. Requests still use `Authorization: Bearer <API_KEY>`. After verifying the new version, remove the old secret when it is no longer needed for rollback. Update local `.dev.vars` files and restart local Workers as well.

## Credential rotation and revocation

There is no permanent-token or refresh-token workflow in the inspected first-party implementation. Record each credential ID and `exp` securely; schedule rotation well before the initial 365-day expiry.

1. Create a **new** `query.read` credential with `catalog-admin.py issue-reader`, saving its one-time response to a new protected file. Do not change catalog policy during routine rotation.
2. Update `QUACKLAKE_JWT` using `wrangler secret put` for the exact environment. Keep the separate caller token unchanged unless it is also being rotated.
3. Verify authenticated readiness and an actual file-reading query through a **fresh** connection, correct catalog/path, and the intended reader policy. The Worker opens request-scoped sessions, so subsequent requests normally provide this fresh check.
4. Revoke the old credential with `catalog-admin.py revoke --url https://<host> --catalog <id> --credential-id <old-id>`. Verify a new connection using the old JWT is rejected. Never print the JWT to confirm which one was used.
5. Existing signed Quack sessions can survive credential revocation. In-flight requests may finish; this service closes sessions best-effort. Emergency rotation of QuackLake's `CONNECTION_SIGNING_SECRET` invalidates broader active sessions and requires explicit catalog-service operator coordination. It is not a query-Worker secret or a routine silent rotation step.

If the new credential fails, restore the prior still-valid secret and test a fresh connection before revoking anything. If it has already been revoked, issue another reader credential; JWT revocation is not reversed by rolling back code. Wrong issuer/audience, expired/revoked/wrong-catalog JWT, absent policy and missing metadata all fail readiness rather than widening permissions.

## Rollback and recovery

Record the deployed version ID, source/lockfile hashes, complete bundle size, configuration, catalog version and secret credential IDs. Use `wrangler rollback <previous-version-id> --config wrangler.jsonc` after reviewing that version's binding/config/secret compatibility. If secret restoration is required, perform it explicitly and verify a fresh query. The MVP performs no catalog migrations; rollback must not rewrite paths or attempt to reverse schema migrations.

After a timeout or 429, retry with a smaller query/lower concurrency; do not queue unbounded work. A 422 can indicate output, page, column-chunk or pool limits. A 503 requires checking binding presence, endpoint/signing, policy, canonical path and object availability. Use request IDs and sanitized server diagnostics. Do not enable token/SQL logging in shared logs.

Stop local servers with Ctrl-C. Stop only the named fixture MinIO container to reclaim its ephemeral data. Local catalog credentials/state live under ignored `.cache/` and `.dev.vars`; retain them to resume or archive/remove them explicitly as a coordinated reset. Never delete a live bucket/catalog to repair a local fixture.
