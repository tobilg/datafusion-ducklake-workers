import './devlib.mjs';
// Existing loopback fixture only. Adds one native-provider metadata table;
// creates and removes a separate SQL table for exact UUID wire round-trips.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { LogicalTypeId, QuackClient } from "../vendor/quacklake/node_modules/@quack-protocol/sdk/dist/index.js";

const root = new URL("../", import.meta.url);
const state = JSON.parse(await readFile(new URL(".cache/local-catalog.json", root), "utf8"));
assert.equal(state.data_path, "r2://quacklake-fixture/catalogs/fixture/");
const url = "http://127.0.0.1:8792";
const suffix = crypto.randomUUID().replaceAll("-", "_");
const metadataTable = `snapshot_uuid_${suffix}`;
const roundtripTable = `uuid_roundtrip_${suffix}`;
const expected = [
  "aabbccdd-eeff-4011-8233-445566778899",
  "12345678-4455-4677-8899-aabbccddeeff",
  "00112233-4455-6677-8899-aabbccddeeff",
  null,
];
const checks = [];
const clients = [];
let writer;
let created = false;

function native(...args) {
  const result = spawnSync(fileURLToPath(new URL("repro/catalog-native/target/debug/quacklake-native-probe", root)), args, {
    encoding: "utf8", timeout: 30000,
  });
  // Provider errors can contain internal SQL; never print child output.
  assert.equal(result.status, 0, "Native provider failed; credential-bearing output withheld");
}
async function connect(jwt) {
  const client = await QuackClient.connect(url, { authToken: jwt });
  clients.push(client);
  return client;
}
function pass(name) {
  checks.push(name);
  console.log(`PASS ${name}`);
}

try {
  native("read");
  native("advance", metadataTable);
  native("read");
  pass("native provider metadata read, table creation, fresh read and denied reader mutation");

  const reader = await connect(state.reader_jwt);
  const sql = `SELECT table_uuid FROM ducklake_table WHERE table_name = '${metadataTable}'`;
  const first = await reader.query(sql);
  assert.deepEqual(first.types.map((type) => type.id), [LogicalTypeId.UUID]);
  assert.equal(first.rows().length, 1);
  assert.match(first.rows()[0].table_uuid, /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
  const freshReader = await connect(state.reader_jwt);
  assert.deepEqual((await freshReader.query(sql)).rows(), first.rows());
  pass("provider-generated table UUID retains logical type and exact value across fresh connections");

  writer = await connect(state.bootstrap_jwt);
  await writer.query(`CREATE TABLE ${roundtripTable} (id INTEGER, value UUID)`);
  created = true;
  for (const [id, value] of expected.entries()) {
    await writer.query(`INSERT INTO ${roundtripTable} VALUES (${id}, CAST(${value === null ? "NULL" : `'${value}'`} AS UUID))`);
  }
  const result = await reader.query(`SELECT value FROM ${roundtripTable} ORDER BY id`);
  assert.deepEqual(result.types.map((type) => type.id), [LogicalTypeId.UUID]);
  assert.deepEqual(result.rows(), expected.map((value) => ({ value })));
  assert.deepEqual(await reader.values(`SELECT typeof(value) FROM ${roundtripTable} ORDER BY id`), ["text", "text", "text", "null"]);
  pass("alphabetic, numeric-leading, zero-prefixed UUIDs and NULL round-trip through SQLite and Quack");

  await writer.query(`INSERT INTO ${roundtripTable} VALUES (4, 0)`);
  await assert.rejects(reader.query(`SELECT value FROM ${roundtripTable} WHERE id = 4`), /400 Bad Request/);
  pass("corrupt numeric UUID remains rejected by wire encoding");

  const sources = JSON.parse(await readFile(new URL("sources.lock.json", root), "utf8"));
  await writeFile(new URL(".cache/reports/quacklake-uuid-refresh.json", root), JSON.stringify({
    quacklake_revision: sources.quacklake.rev,
    provider_revision: sources["datafusion-ducklake-provider"].rev,
    sdk_version: "0.2.0",
    url, metadata_table: metadataTable, checks,
    scope: "Local workerd SQLite catalog, existing native fixture executable and JS protocol client. No query Worker rebuild or cloud deployment.",
  }, null, 2) + "\n");
} catch {
  console.error("Local UUID regression failed; upstream details withheld to protect fixture credentials.");
  process.exitCode = 1;
} finally {
  if (created) {
    try { await writer.query(`DROP TABLE ${roundtripTable}`); }
    catch { console.error("Local UUID test-table cleanup failed"); process.exitCode = 1; }
  }
  for (const client of clients) {
    try { await client.disconnect(); }
    catch { console.error("Local UUID session cleanup failed"); process.exitCode = 1; }
  }
}
