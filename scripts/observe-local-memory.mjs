import './devlib.mjs';
// Inspector observations are NOT a measurement of total deployed isolate memory.
import { readFile, writeFile } from 'node:fs/promises';
import WebSocket from 'ws';
import { createHash } from 'node:crypto';
const root=new URL('../',import.meta.url);
const queries=Number(process.argv[2]??50);
if(!Number.isInteger(queries)||queries<10||queries>500||queries%10)throw Error('Query count must be a multiple of 10, between 10 and 500');
const mode=process.argv[3]??'r2';
const gcMode=process.argv[4]??'forced';
if(!['forced','natural'].includes(gcMode))throw Error('GC mode must be forced or natural');
const modes={
  'files-core-r2':{port:8793,inspector:9243,path:'/query',sql:"SELECT sum(id) FROM 'r2://files-a/events/'"},
  'files-core-s3':{port:8794,inspector:9244,path:'/query',sql:"SELECT sum(id) FROM 's3://files-a/events/'"},
  'files-full-r2':{port:8796,inspector:9246,path:'/query',sql:"SELECT sum(id) FROM 'r2://files-a/events/'"},
  r2:{port:8790,inspector:9232,path:'/query',sql:'SELECT sum(id) FROM sales'},
  s3:{port:8791,inspector:9233,path:'/query',sql:'SELECT sum(id) FROM sales'},
  select1:{port:8790,inspector:9232,path:'/query',sql:'SELECT 1'},
  ready:{port:8790,inspector:9232,path:'/readyz'},
  health:{port:8790,inspector:9232,path:'/healthz',anonymous:true},
  baseline:{port:8787,inspector:9236,path:'/sleep?ms=1',tick:'/',anonymous:true},
  http:{port:8789,inspector:9231,path:'/loopback',tick:'/healthz',anonymous:true},
  'http-no-pool':{port:8789,inspector:9231,path:'/loopback?pool=off',tick:'/healthz',anonymous:true},
};
const workload=modes[mode];
if(!workload)throw Error(`Unknown workload; choose ${Object.keys(modes).join(', ')}`);
const base=`http://127.0.0.1:${workload.port}`;
const inspector=`http://127.0.0.1:${workload.inspector}`;
const token=workload.anonymous?null:await readFile(new URL('.cache/query-api-token',root),'utf8');
const targets=await (await fetch(`${inspector}/json`)).json();
const ws=new WebSocket(targets[0].webSocketDebuggerUrl,{origin:inspector});
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=event=>reject(Error(event.message));});
let id=1000;const pending=new Map();
ws.onmessage=event=>{const m=JSON.parse(event.data);if(m.id){const p=pending.get(m.id);if(!p)return;pending.delete(m.id);clearTimeout(p.timer);if(m.error)p.reject(Error(m.error.message));else p.resolve(m.result);}};
function call(method){return new Promise((resolve,reject)=>{const n=++id;const timer=setTimeout(()=>{pending.delete(n);reject(Error("Inspector command timeout"));},5000);pending.set(n,{resolve,reject,timer});ws.send(JSON.stringify({id:n,method}));});}
await call("Runtime.enable");
await call("HeapProfiler.enable");
const observations=[];
try {
  for(let round=0;round<=queries/10;round++) {
    if(round)for(let i=0;i<10;i++) {
      const response=await fetch(`${base}${workload.path}`,{
        method:workload.sql?'POST':'GET',
        headers:{...(token?{Authorization:`Bearer ${token}`}:{}) ,...(workload.sql?{'Content-Type':'application/json'}:{})},
        body:workload.sql?JSON.stringify({sql:workload.sql}):undefined,
        signal:AbortSignal.timeout(15000),
      });
      if(response.status!==200)throw Error(`Query status ${response.status}`);
      await response.arrayBuffer();
    }
    // Inspector console history is debugger-owned retention, not application state.
    await call('Runtime.discardConsoleEntries');
    if(gcMode==='forced') {
      const collection=call('HeapProfiler.collectGarbage');
      // workerd processes forced GC on an active isolate turn. An idle request times out.
      await new Promise(resolve=>setTimeout(resolve,100));
      const tick=await fetch(`${base}${workload.tick??'/healthz'}`,{signal:AbortSignal.timeout(5000)});
      await tick.arrayBuffer();await collection;
    }
    const sample={completed_queries:round*10,gc:gcMode==='forced'?'collected_after_health_tick':'natural',...await call('Runtime.getHeapUsage')};
    observations.push(sample);
    if(round%5===0)console.log(JSON.stringify(sample));
  }
} finally {ws.terminate();}
const artifact=mode==='baseline'?'vendor/workers-rs/examples/emscripten-tokio/build/index.js':
  mode.startsWith('http')?'repro/https/build/index.js':mode.startsWith('files-core')?'build/core/index.js':'build/full/index.js';
const moduleHash=createHash('sha256').update(await readFile(new URL(artifact,root))).digest('hex');
const warmed=observations.find(sample=>sample.completed_queries===50);
const last=observations.at(-1);
const output=gcMode==='natural'?`.cache/reports/memory-${mode}-natural.json`:
  mode==='r2'?'.cache/reports/local-memory.json':`.cache/reports/memory-${mode}.json`;
await writeFile(new URL(output,root),JSON.stringify({
  scope:`Local workerd inspector heap after discarding debugger console entries; ${gcMode} GC. Forced GC uses an active health request. Excludes unreported native/WASM allocations and is not total deployed isolate memory or platform CPU.`,
  target_title:targets[0].title,
  workload:mode,
  artifact,js_sha256:moduleHash,
  peak_sampled_used_js_heap_bytes:Math.max(...observations.map(sample=>sample.usedSize)),
  change_after_50_requests:warmed?{
    used_js_heap_bytes:last.usedSize-warmed.usedSize,
    embedder_heap_bytes:last.embedderHeapUsedSize-warmed.embedderHeapUsedSize,
    backing_storage_bytes:last.backingStorageSize-warmed.backingStorageSize,
  }:null,
  observations,
},null,2)+'\n');
console.log(`Recorded local inspector observations across ${queries} queries.`);
