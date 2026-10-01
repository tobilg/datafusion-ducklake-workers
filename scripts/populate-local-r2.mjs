// Local Miniflare R2 fixture only; never calls the Cloudflare control plane.
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
import { readdir, readFile, writeFile, stat } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import { Readable } from 'node:stream';
import { resolve, relative, join } from 'node:path';
const root = resolve(import.meta.dirname, '..');
const directory = join(root, '.cache/fixture-objects');
const mf = new Miniflare(convertV4MiniflareOptions({
  modules: true, script: `export default { async fetch(request, env) {
    const key=decodeURIComponent(new URL(request.url).pathname.slice(1));
    if(request.method!=="PUT" || !key.startsWith("catalogs/fixture/") || key.includes(".."))return new Response(null,{status:403});
    await env.FIXTURE_BUCKET.put(key,request.body);return new Response(null,{status:204});
  } }`,
  compatibilityDate: '2026-09-29',
  r2Buckets: { FIXTURE_BUCKET: 'quacklake-fixture' },
  resourcePersistencePath: join(root, '.cache/local-state/v3'),
}));
try {
  const bucket = await mf.getR2Bucket('FIXTURE_BUCKET');
  let count = 0;
  async function upload(dir) {
    for (const item of await readdir(dir, { withFileTypes: true })) {
      const path = join(dir, item.name);
      if (item.isDirectory()) await upload(path);
      else {
        const key = relative(directory, path);
        if (!key.startsWith('catalogs/fixture/') || !key.endsWith('.parquet')) throw Error('Unexpected fixture key');
        const response=await mf.dispatchFetch(`http://localhost/${key.split('/').map(encodeURIComponent).join('/')}`, {
          method:'PUT', body:Readable.toWeb(createReadStream(path)), duplex:'half',
          headers:{'Content-Length':String((await stat(path)).size)},
        });
        if(response.status!==204)throw Error(`Local fixture upload failed: ${response.status}`);
        count++;
      }
    }
  }
  await upload(directory);
  const range = new Uint8Array(6 * 1024 * 1024);
  for (let i = 0; i < range.length; i++) range[i] = i % 251;
  await bucket.put('catalogs/fixture/adapter/range.bin', range);
  await bucket.put('catalogs/fixture/adapter/empty.bin', new Uint8Array());
  await bucket.put('catalogs/fixture/adapter/Grüße 100%25.bin', new Uint8Array([1,2,3]));
  await bucket.put('outside/denied.bin', new Uint8Array([9]));
  for (let i=0; i<130; i++) await bucket.put(`catalogs/fixture/adapter/list/${String(i).padStart(3,'0')}`, new Uint8Array([i % 256]));
  console.log(`Uploaded ${count} real Parquet files plus range/list fixtures to local R2. No S3 credentials used.`);
} finally { await mf.dispose(); }
