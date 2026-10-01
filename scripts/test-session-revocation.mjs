import './devlib.mjs';
// Dedicated loopback fixture only; create and revoke one temporary reader credential.
import {readFile,writeFile} from 'node:fs/promises';
import {QuackClient} from '../vendor/quacklake/node_modules/@quack-protocol/sdk/dist/index.js';
const root=new URL('../',import.meta.url);
const secrets=Object.fromEntries((await readFile(new URL('fixtures/quacklake/.dev.vars',root),'utf8')).trim().split('\n').map(s=>{const i=s.indexOf('=');return [s.slice(0,i),s.slice(i+1)];}));
async function admin(method,path,body){
 const r=await fetch('http://127.0.0.1:8792'+path,{method,headers:{Authorization:`Bearer ${secrets.ADMIN_TOKEN}`,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,redirect:'error',signal:AbortSignal.timeout(10000)});
 if(!r.ok)throw Error(`Local catalog admin HTTP ${r.status}`);
 return r.json();
}
let credential,client,revoked=false;
try{
 credential=await admin('POST','/admin/catalogs/fixture/credentials',{scopes:['query.read'],expiresInSeconds:3600});
 client=await QuackClient.connect('http://127.0.0.1:8792',{authToken:credential.jwt});
 await client.query('SELECT count(*) FROM ducklake_snapshot');
 await admin('DELETE','/admin/catalogs/fixture/credentials/'+credential.credentialId);revoked=true;
 let existingAccepted=false;
 try{await client.query('SELECT count(*) FROM ducklake_snapshot');existingAccepted=true;}catch{}
 let newRejected=false;
 try{const unexpected=await QuackClient.connect('http://127.0.0.1:8792',{authToken:credential.jwt});await unexpected.disconnect();}catch{newRejected=true;}
 if(!newRejected)throw Error('Fresh connection accepted revoked credential');
 await writeFile(new URL('.cache/reports/session-revocation.json',root),JSON.stringify({scope:'Actual local QuackLake and frozen JS protocol SDK; query Worker uses request-scoped sessions.',existing_session_accepted_after_revocation:existingAccepted,new_connection_rejected:newRejected},null,2)+'\n');
 console.log(`PASS fresh connection rejects revoked credential; existing signed session accepted=${existingAccepted}`);
}catch{console.error('Session lifecycle regression failed; credential-bearing upstream details withheld.');process.exitCode=1;}
finally{if(client)await client.disconnect().catch(()=>{});if(credential&&!revoked)await admin('DELETE','/admin/catalogs/fixture/credentials/'+credential.credentialId).catch(()=>{process.exitCode=1;});}
