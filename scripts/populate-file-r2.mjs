// Actual local R2 bindings, populated through Miniflare. Never uses remote bindings.
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {readdir,stat} from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import {Readable} from 'node:stream';
import {resolve,join,relative} from 'node:path';
const root=resolve(import.meta.dirname,'..');
const mf=new Miniflare(convertV4MiniflareOptions({modules:true,compatibilityDate:'2026-09-29',
  script:`export default { async fetch(req,env) { const url=new URL(req.url);
    const bucket=env[url.hostname]; if(!bucket||req.method!=="PUT")return new Response(null,{status:403});
    await bucket.put(decodeURIComponent(url.pathname.slice(1)),req.body); return new Response(null,{status:204}); }};`,
  r2Buckets:{'files-a':'files-a','files-b':'files-b'}, resourcePersistencePath:join(root,'.cache/files-state/v3')}));
let count=0;
try {
  for (const bucket of ['files-a','files-b']) {
    const base=join(root,'.cache/file-fixtures',bucket);
    async function upload(dir) {
      for(const item of await readdir(dir,{withFileTypes:true})) {
        const path=join(dir,item.name);if(item.isDirectory()){await upload(path);continue;}
        const key=relative(base,path).split('/').map(encodeURIComponent).join('/');
        const res=await mf.dispatchFetch(`http://${bucket}/${key}`,{method:'PUT',
          body:Readable.toWeb(createReadStream(path)),duplex:'half',headers:{'Content-Length':String((await stat(path)).size)}});
        if(res.status!==204)throw Error('Fixture PUT failed');count++;
      }
    }
    await upload(base);
  }
  console.log(`Uploaded ${count} standalone Parquet objects to local R2.`);
} finally {await mf.dispose();}
