// NET-05: control and STOP never wait behind other requests; status polls cannot
// take all browser connections; bulk requests wait while the lease is held.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app', '01-format-http.js'), 'utf8');

function fixture() {
  const started = [];
  const fetch = (input, init = {}) => new Promise((resolve, reject) => {
    const row = {path: new URL(String(input), 'http://dash.local').pathname, init, resolve: () => resolve({ok: true, status: 200, json: async () => ({ok: true})}), reject};
    started.push(row);
    init.signal?.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name: 'AbortError'})));
  });
  const timers = [];
  const context = {
    URL, AbortController, DOMException, Promise, JSON, Error, Number, String, Math,
    location: {href: 'http://dash.local/rover-control', origin: 'http://dash.local'},
    state: {roverControl: {lease: null}},
    setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; }, clearTimeout: () => {},
  };
  context.window = {fetch};
  vm.createContext(context);
  vm.runInContext(source, context);
  return {context, started, timers, get: p => context.window.fetch(p, {cache: 'no-store'})};
}
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  {
    const f = fixture();
    for (let i = 0; i < 6; i++) f.get('/api/telemetry');
    await flush();
    assert.equal(f.started.length, 4, 'at most 4 status requests at a time');
    assert.equal(f.started[0].init.priority, 'low', 'background requests ask for low priority');
    const answer = f.context.controlRequest('/api/rover/command', {seq: 1});
    await flush();
    assert.equal(f.started.length, 5, 'the control request leaves at once');
    assert.equal(f.started[4].path, '/api/rover/command');
    assert.equal(f.started[4].init.priority, 'high');
    assert.equal(f.started[4].init.method, 'POST');
    f.started[4].resolve();
    assert.deepEqual(await answer, {ok: true});
    f.started[0].resolve(); await flush(); await flush();
    assert.equal(f.started.length, 6, 'a finished status request lets the next one go');
  }
  {
    const f = fixture();
    f.context.state.roverControl.lease = 'lease';
    for (let i = 0; i < 3; i++) f.get('/api/autonomy/observe');
    f.get('/api/recordings');
    f.get('/api/recordings/status');
    await flush();
    assert.deepEqual(f.started.map(r => r.path), ['/api/autonomy/observe', '/api/autonomy/observe'], 'while driving at most 2');
    f.started[0].resolve(); await flush(); await flush();
    assert.equal(f.started[2].path, '/api/autonomy/observe', 'status requests go before bulk ones');
    f.started[1].resolve(); await flush(); await flush();
    assert.equal(f.started[3].path, '/api/recordings/status', '/api/recordings/status is a status request');
    f.started[2].resolve(); await flush(); await flush();
    assert.equal(f.started[4].path, '/api/recordings', 'the bulk request goes when nothing else waits');
    f.get('/api/recordings/abc/frames'); await flush();
    assert.equal(f.started.length, 5, 'one bulk request at a time while driving');
    f.started[4].resolve(); await flush(); await flush();
    assert.equal(f.started.length, 6);
  }
  {
    const f = fixture();
    for (let i = 0; i < 4; i++) f.get('/api/telemetry');
    const controller = new AbortController();
    const queued = f.context.window.fetch('/api/live/map', {signal: controller.signal});
    controller.abort();
    await assert.rejects(queued, 'an aborted waiting request is rejected');
    f.started[0].resolve(); await flush(); await flush();
    assert.equal(f.started.length, 4, 'and it never starts');
    f.context.window.fetch('/api/recordings/start', {method: 'POST', body: '{}'});
    f.context.window.fetch('/static/app/02-shell.js');
    f.context.window.fetch('http://elsewhere.invalid/api/telemetry');
    await flush();
    assert.equal(f.started.length, 7, 'user actions, files and other hosts are not queued');
  }
  {
    const f = fixture();
    const answer = f.context.controlRequest('/api/rover/stop', {reason: 'operator_stop'});
    await flush();
    assert.equal(f.timers.at(-1).ms, 8000, 'STOP waits up to 8 s');
    f.timers.at(-1).fn();
    await assert.rejects(answer, /응답 없음 8초/);
    const command = f.context.controlRequest('/api/rover/command', {});
    await flush();
    assert.equal(f.timers.at(-1).ms, 5000, 'a drive command waits up to 5 s');
    f.timers.at(-1).fn();
    await assert.rejects(command, /응답 없음 5초/);
  }
  console.log('Control requests bypass the limiter; status 4 (2 while driving); bulk waits; timeouts: PASS');
})().catch(error => { console.error(error); process.exit(1); });
