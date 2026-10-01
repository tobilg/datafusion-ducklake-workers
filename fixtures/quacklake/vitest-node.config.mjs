// Pure SQL rewrite regressions; this does not exercise Worker bindings.
// Run from vendor/quacklake. Its pinned Vitest 5 is incompatible with the
// pinned Cloudflare test pool; use scripts/test-quacklake-uuid.mjs for the
// actual SQLite/protocol/provider regression against the local Worker.
export default {
  test: {
    environment: "node",
    include: ["test/uuid-cast.test.ts", "test/sql-text.test.ts"],
  },
};
