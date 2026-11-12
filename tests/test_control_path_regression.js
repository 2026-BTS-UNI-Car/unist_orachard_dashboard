// Manual drive / STOP regression guard for the UI performance work.
// The drive keepalive period, the immediate STOP request and the existing
// stop-on-hide/pagehide paths must stay exactly as deployed (b782d9b).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource();
const code = source.slice(source.indexOf('function hasActiveRoverInput() {'), source.indexOf('function roverCommandMessage('));
assert.ok(code.includes('async function stopRoverNow('));

// The original control listeners are still registered verbatim.
for (const line of [
  '$("roverStopButton")?.addEventListener("click", () => stopRoverNow("operator_stop"));',
  'window.addEventListener("blur", () => { if (state.roverControl.lease || state.roverControl.inFlight) stopRoverNow("focus_lost"); });',
  'document.addEventListener("visibilitychange", () => { if (document.hidden && (state.roverControl.lease || state.roverControl.inFlight)) stopRoverNow("page_hidden"); });',
  'setInterval(() => { if (state.roverControl.lease) { ensureRoverCommandTimer(); } }, 200);',
  'navigator.sendBeacon("/api/rover/stop", new Blob([body], { type: "application/json" }));',
]) assert.ok(source.includes(line), `control listener changed: ${line}`);
assert.ok(/state\.roverControl\.commandTimer = setInterval\(stepRoverMotion, 140\)/.test(source), 'drive period stays 140 ms');

function setup() {
  const c = {activeKeys: new Set(), speedKeys: new Set(), activeButton: '', speedPct: 30, lease: 'lease', ticket: 'one', seq: 0, epoch: 0};
  const calls = [], intervals = [];
  const context = {
    state: {roverControl: c}, document: {hidden: false}, currentRoute: () => '/rover-control', SIMULATION: false,
    setInterval: (fn, ms) => { intervals.push({fn, ms}); return intervals.length; }, clearInterval: () => {},
    renderRoverButtons() {}, renderRoverMotionReadout() {}, setRoverCommandStatus() {},
    controlRequest: async (p, payload) => { calls.push({p, payload, at: process.hrtime.bigint()}); return {ok: true, ticket: 'next', seq: (payload?.seq || 0)}; },
  };
  vm.createContext(context); vm.runInContext(code, context);
  return {context, c, calls, intervals};
}
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  // Keepalive vs command, at the 140 ms period.
  {
    const {context, c, calls, intervals} = setup();
    context.ensureRoverCommandTimer(); context.ensureRoverCommandTimer();
    assert.equal(intervals.length, 1); assert.equal(intervals[0].ms, 140);
    await context.stepRoverMotion();
    assert.equal(calls[0].p, '/api/rover/keepalive'); assert.equal(calls[0].payload.deadman, false);
    c.activeKeys.add('w'); await context.stepRoverMotion();
    assert.equal(calls[1].p, '/api/rover/command'); assert.equal(calls[1].payload.deadman, true);
    assert.deepEqual([calls[1].payload.left, calls[1].payload.right], [30, 30]);
    assert.equal(calls[1].payload.seq, calls[0].payload.seq + 1);
  }
  // STOP is sent at once, even while a drive request is still in flight,
  // and the lease is cleared before the network round trip.
  {
    const {context, c, calls} = setup();
    let release;
    const normal = context.controlRequest;
    context.controlRequest = (p, payload) => p.endsWith('/command') ? (calls.push({p, payload}), new Promise(r => { release = r; })) : normal(p, payload);
    c.activeKeys.add('w'); const pending = context.stepRoverMotion();
    assert.equal(c.inFlight, true);
    const stop = context.stopRoverNow('operator_stop');
    assert.equal(c.lease, null, 'lease cleared synchronously');
    assert.equal(calls.at(-1).p, '/api/rover/stop', 'stop posted synchronously, not queued behind the drive request');
    assert.equal(calls.at(-1).payload.reason, 'operator_stop');
    await stop; release({ok: true, ticket: 'late', seq: 1}); await pending; await flush();
    assert.equal(calls.filter(x => x.p === '/api/rover/stop').length, 1);
    assert.equal(c.lease, null); assert.equal(c.activeKeys.size, 0);
  }
  // A failed drive request stops the rover (deadman on the bridge remains the actual guarantee).
  {
    const {context, c, calls} = setup();
    context.controlRequest = async (p, payload) => { calls.push({p, payload}); if (p.endsWith('/command')) throw new Error('timeout'); return {ok: true}; };
    c.activeKeys.add('w'); await context.stepRoverMotion(); await flush();
    assert.deepEqual(calls.map(x => x.p), ['/api/rover/command', '/api/rover/stop']);
    assert.equal(calls[1].payload.reason, 'command_failed');
  }
  // Hidden or off-route pages stop instead of driving.
  for (const [hidden, route] of [[true, '/rover-control'], [false, '/connection']]) {
    const {context, c, calls} = setup();
    context.document.hidden = hidden; context.currentRoute = () => route;
    c.activeKeys.add('w'); await context.stepRoverMotion(); await flush();
    assert.deepEqual(calls.map(x => x.p), ['/api/rover/stop']);
    assert.equal(calls[0].payload.reason, 'page_inactive');
  }
  console.log('PASS: control path regression (140 ms drive period, immediate STOP, failure/hidden stops, listeners unchanged)');
})().catch(error => { console.error(error); process.exitCode = 1; });
