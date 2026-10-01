// Exercise the actual managed SDK timer wrappers, including signed-i32 ID wrap.
// Networking/lifetime acceptance additionally requires the real Worker soak test.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import os from 'node:os';
import path from 'node:path';

const pins = JSON.parse(await readFile(new URL('../tools.lock.json', import.meta.url), 'utf8'));
const cache = process.platform === 'darwin' ? path.join(os.homedir(), 'Library/Caches') : (process.env.XDG_CACHE_HOME || path.join(os.homedir(), '.cache'));
const sdk = process.argv[2] ?? path.join(cache, `worker-build/emsdk-${pins.managed_tools.emscripten}/upstream/emscripten`);
const source = await readFile(path.join(sdk, 'src/lib/libeventloop.js'), 'utf8');
const functions = ['safeSetTimeout', 'safeClearTimeout', 'setImmediateWrapped', 'clearImmediateWrapped'];
let nextHandle = 0;
const pending = new Map();
const schedule = fn => {
  const handle = { id: ++nextHandle };
  pending.set(handle, fn);
  return handle;
};
const context = vm.createContext({
  setTimeout: schedule, setImmediate: schedule,
  clearTimeout: handle => pending.delete(handle),
  clearImmediate: handle => pending.delete(handle),
  callUserCallback: fn => fn(), keepalive: 0,
});
for (const name of functions) {
  const match = source.match(new RegExp(`\\$${name}: (\\([^\\n]*\\) => \\{[\\s\\S]*?\\n  \\}),`));
  assert(match, `SDK function ${name}`);
  const body = match[1]
    .replace(/\{\{\{ runtimeKeepalivePush\(\) \}\}\}/g, 'keepalive++;')
    .replace(/\{\{\{ runtimeKeepalivePop\(\) \}\}\}/g, 'keepalive--;');
  vm.runInContext(`var ${name} = ${body};`, context);
}
function fire(handle) {
  const callback = pending.get(handle);
  assert(callback);
  pending.delete(handle);
  callback();
}
let completed = 0;
for (const [set, clear] of [['safeSetTimeout', 'safeClearTimeout'], ['setImmediateWrapped', 'clearImmediateWrapped']]) {
  for (let i = 0; i < 1000; i++) {
    const id = context[set](() => completed++, 1);
    assert(Number.isInteger(id) && id !== 0);
    const handle = context[set].mapping.get(id);
    if (i % 2) { context[clear](id); context[clear](id); }
    else fire(handle);
    assert.equal(context[set].mapping.size, 0);
    assert.equal(pending.size, 0);
    assert.equal(context.keepalive, 0);
  }
  context[set].next = 2147483647;
  const wrapped = context[set](() => completed++, 1);
  assert.equal(wrapped, -2147483648);
  context[clear](wrapped);
  context[set].next = -1;
  const live = context[set](() => completed++, 1);
  assert.equal(live, 1);
  context[set].next = -1;
  const skipped = context[set](() => completed++, 1);
  assert.equal(skipped, 2);
  context[clear](live); context[clear](skipped);
}
assert.equal(completed, 1000);
assert.equal(context.keepalive, 0);
console.log('PASS: managed SDK completion, cancellation, duplicate clear, bounded maps, i32 wrap and live-ID collision');
