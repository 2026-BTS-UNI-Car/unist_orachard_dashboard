// The autonomy section's mode chip (2026-11-17, static/steer/auto-panel.mjs): the executor's SHADOW or MOTOR mode in
// the section head next to the run state, hidden while the status is missing. Fake page and elements, no browser, no
// request leaves the process. The heartbeat and STOP paths are not touched here (tests/test_auto_model.mjs).
// Run: node tests/test_auto_panel_mode.mjs
import assert from 'node:assert/strict';

function element(id) {
  const listeners = {};
  return {
    id, hidden: false, disabled: false, className: '', textContent: '', value: '', dataset: {},
    classList: { toggle() {}, add() {}, remove() {} },
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); },
    replaceChildren() {}, append() {},
  };
}
const ids = ['autoPanel', 'autoState', 'autoMode', 'autoPrimary', 'autoEnd', 'autoScale', 'autoScaleValue', 'autoScaleOffer',
  'autoScaleText', 'autoScaleAccept', 'autoScaleDismiss', 'autoNotice', 'autoDetails', 'autoRowLen'];
const els = Object.fromEntries(ids.map((id) => [id, element(id)]));
els.autoMode.hidden = true;
const intervals = [];
globalThis.setInterval = (fn, ms) => { intervals.push([fn, ms]); return intervals.length; };
globalThis.clearInterval = () => {};
globalThis.document = {
  hidden: false,
  getElementById: (id) => els[id] || null,
  addEventListener() {},
  createElement: () => element(''),
};

const { initAutoPanel } = await import('../static/steer/auto-panel.mjs');
let status = null;
const posted = [];
const page = {
  currentRoute: () => '/rover-control',
  getJson: async (url) => {
    assert.equal(url, '/api/executor/status');
    return { ok: true, configured: true, reachable: true, received_at: Date.now() / 1000, status };
  },
  postJson: async (url, body) => { posted.push([url, body]); return { ok: true }; },
};
const panel = initAutoPanel(page);
assert.ok(panel, 'the section is bound');
const settle = () => new Promise((done) => setTimeout(done, 0));
const pollFn = intervals.find(([, ms]) => ms !== 100)[0];

// The first read (status null): the chip stays hidden.
await settle();
assert.equal(els.autoMode.hidden, true);

status = { run_state: 'idle', mode: 'SHADOW' };
await pollFn(); await settle();
assert.deepEqual([els.autoMode.hidden, els.autoMode.className, els.autoMode.textContent], [false, 'status-chip', 'SHADOW · 모터 안 씀']);

status = { run_state: 'ready', mode: 'MOTOR' };
await pollFn(); await settle();
assert.deepEqual([els.autoMode.hidden, els.autoMode.className, els.autoMode.textContent], [false, 'status-chip warn', 'MOTOR · 차를 몬다']);

status = { run_state: 'idle' };
await pollFn(); await settle();
assert.equal(els.autoMode.hidden, true, 'no mode reported: hidden');

// Showing the chip sends nothing: no request but the status reads.
assert.deepEqual(posted, []);
console.log('PASS: autonomy mode chip (SHADOW / MOTOR) in the section head, hidden without a mode');
