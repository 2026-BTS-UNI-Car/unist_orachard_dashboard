// 2026-11-19 field: a queued /api/ request that never answers must give its slot back (REQUEST_TIMEOUT_MS),
// otherwise the status polls fail behind it and the bar shows '연결 끊김' until a reload.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function load() {
  const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app', '01-format-http.js'), 'utf8');
  const calls = [];
  const fakeFetch = (input, init) => new Promise((resolve, reject) => {
    const call = { input, init, resolve };
    calls.push(call);
    init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
  });
  const window = { fetch: fakeFetch, LinkGrade: null };
  const ctx = vm.createContext({
    window, state: { roverControl: {} }, location: { href: 'http://x/', origin: 'http://x' }, URL, DOMException,
    AbortController, setTimeout, clearTimeout, performance, console, Map, Promise, JSON, Math, Date,
    localStorage: { getItem: () => null, setItem() {} }, document: { hidden: false, addEventListener() {} },
    navigator: {}, Response: class {}, Headers: class { constructor() {} get() { return null; } },
  });
  vm.runInContext(source, ctx, { filename: '01-format-http.js' });
  return { ctx, calls };
}

test('a hung status request frees its slot after the deadline', async () => {
  let loaded;
  loaded = load();
  const { ctx, calls } = loaded;
  vm.runInContext('REQUEST_TIMEOUT_MS.status = 60', ctx);
  const fetch = ctx.window.fetch;
  const hung = [1, 2, 3, 4].map(() => fetch('/api/rover/x'.replace('rover/x', 'executor/status')).catch((e) => e.name));
  const waiting = fetch('/api/telemetry').then(() => 'answered', (e) => e.name);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(vm.runInContext('requestQueue.active', ctx), 4, 'four in flight, the telemetry poll waits');
  await new Promise((r) => setTimeout(r, 80));
  const last = calls[calls.length - 1];
  assert.equal(String(last.input), '/api/telemetry', 'the waiting poll got a slot after the hung ones timed out');
  last.resolve({ ok: true, status: 200, headers: { get: () => null }, clone() { return this; }, json: async () => ({}) });
  assert.deepEqual(await Promise.all(hung), ['AbortError', 'AbortError', 'AbortError', 'AbortError']);
  assert.ok(['answered', 'AbortError'].includes(await waiting));
});
