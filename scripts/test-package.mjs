// Load only the packaged modules in local workerd. Catalog/transport correctness
// is checked by the separate suites against these same hashed production bytes.
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

const variant = process.argv[2];
assert(['core', 'full'].includes(variant), 'Expected core|full');
const root = resolve(import.meta.dirname, '..', 'dist', variant);
const sums = (await readFile(join(root, 'SHA256SUMS'), 'utf8')).trim().split('\n');
for (const line of sums) {
  const [expected, name] = line.split('  ');
  const file = resolve(root, name);
  assert(file.startsWith(root + '/'), 'Checksum path must remain in the package');
  const bytes = await readFile(file);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), expected, `Checksum ${name}`);
}
const config = JSON.parse(await readFile(join(root, 'wrangler.jsonc'), 'utf8'));
const bundle = JSON.parse(await readFile(join(root, 'bundle.json'), 'utf8'));
assert(!config.build, 'Prebuilt deployment must not require the source build');
const modules = [...new Set([config.main, ...bundle.modules])]
  .filter(name => /\.(?:m?js|wasm)$/.test(name))
  .map(name => ({ type: name.endsWith('.wasm') ? 'CompiledWasm' : 'ESModule', path: join(root, name) }));
const key = randomBytes(32).toString('hex');
const mf = new Miniflare(convertV4MiniflareOptions({
  modules,
  modulesRoot: root,
  compatibilityDate: config.compatibility_date,
  compatibilityFlags: config.compatibility_flags,
  // Both variants support files mode. This smoke isolates packaging from any
  // operator catalog, bucket, credentials or remote infrastructure.
  bindings: { QUERY_MODE: 'files', API_KEY: key },
}));
try {
  const response = await mf.dispatchFetch('http://localhost/query', {
    method: 'POST',
    headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ sql: 'SELECT 1 AS value' }),
  });
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).rows, [['1']]);
  console.log(`PASS ${variant} package checksums, JS/WASM loading and authenticated SELECT 1`);
} finally {
  await mf.dispose();
}
