// Hidden-page sensor sockets (S04/S11) and reconnect backoff (S12).
// The manual-drive stop on visibilitychange must stay exactly as it was.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource();
const start = source.indexOf('// Sensor WebSocket reconnect policy');
const end = source.indexOf('function toggleLidarLive() {');
assert.ok(start > 0 && end > start);
const block = source.slice(start, end);

// Rover control is untouched by the stream suspension.
assert.ok(source.includes('document.addEventListener("visibilitychange", () => { if (document.hidden && (state.roverControl.lease || state.roverControl.inFlight)) stopRoverNow("page_hidden"); });'));
assert.ok(source.includes('document.addEventListener("visibilitychange", handleSensorStreamVisibility);'));
assert.ok(!/roverControl|stopRoverNow|postJson|\/api\/rover/.test(block), 'stream suspension never touches rover control');

function setup() {
  const calls = [], timers = [];
  const live = () => ({ socket: null, enabled: false, userStopped: false, reconnectAttempt: 0 });
  const context = {
    Math, Number, String, Boolean, calls, timers,
    state: { lidarLive: live(), cameraLive: live(), imuLive: live() },
    document: { hidden: false },
    window: {
      setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
      clearTimeout: id => { if (timers[id - 1]) timers[id - 1].cleared = true; },
    },
    routeSupportsLidarLive: () => true, routeSupportsCameraLive: () => true, routeSupportsImuLive: () => true,
    closeLidarLive: (m, o) => { calls.push(['closeLidar', o.userStopped]); Object.assign(context.state.lidarLive, { socket: null, enabled: false, userStopped: o.userStopped }); },
    closeCameraLive: (m, o) => { calls.push(['closeCamera', o.userStopped]); Object.assign(context.state.cameraLive, { socket: null, enabled: false, userStopped: o.userStopped }); },
    closeImuLive: (m, o) => { calls.push(['closeImu', o.userStopped]); Object.assign(context.state.imuLive, { socket: null, enabled: false, userStopped: o.userStopped }); },
    startLidarLive: () => { calls.push(['startLidar']); context.state.lidarLive.socket = {}; },
    startCameraLive: () => { calls.push(['startCamera']); context.state.cameraLive.socket = {}; context.state.cameraLive.ackExhausted = false; },
    startImuLive: () => { calls.push(['startImu']); context.state.imuLive.socket = {}; },
    tick: () => calls.push(['tick']),
  };
  vm.createContext(context);
  vm.runInContext(block, context);
  return context;
}

// Backoff sequence: 1.5 s doubling to 30 s with +/-20 % jitter; reset by a message.
{
  const c = setup();
  const mid = () => 0.5;
  const seq = [0, 1, 2, 3, 4, 5, 8].map(a => c.sensorReconnectDelay(a, {}, mid));
  assert.deepEqual(seq, [1500, 3000, 6000, 12000, 24000, 30000, 30000]);
  for (let a = 0; a < 8; a++) {
    const lo = c.sensorReconnectDelay(a, {}, () => 0), hi = c.sensorReconnectDelay(a, {}, () => 0.999999);
    const base = Math.min(30000, 1500 * 2 ** a);
    assert.ok(lo >= Math.round(base * 0.8) - 1 && hi <= Math.min(30000, Math.round(base * 1.2)) + 1, `jitter ${a}`);
  }
  assert.equal(c.sensorReconnectDelay(0, { code: 1013, reason: 'camera viewer limit' }), 30000);
  assert.equal(c.sensorReconnectDelay(5, { code: 4001, reason: 'autonomy_priority' }), 500);
  const liveState = { reconnectAttempt: 0 };
  const delays = [1, 2, 3].map(() => c.nextSensorReconnectDelay(liveState, {}));
  assert.equal(liveState.reconnectAttempt, 3);
  assert.ok(delays[0] < delays[1] && delays[1] < delays[2]);
  c.nextSensorReconnectDelay(liveState, { code: 4001, reason: 'autonomy_priority' });
  assert.equal(liveState.reconnectAttempt, 3, 'autonomy priority close does not escalate');
  // Every sensor close handler uses the policy; no fixed 1.5 s reconnect loop remains.
  assert.equal((source.match(/nextSensorReconnectDelay\(state\.(lidarLive|cameraLive|imuLive), event\)/g) || []).length, 3);
}

// Hidden for >= 1 s closes only running streams (not as a user stop) and
// visible reopens exactly those; quick hide/show does nothing.
{
  const c = setup();
  Object.assign(c.state.lidarLive, { socket: {}, enabled: true });
  Object.assign(c.state.cameraLive, { socket: {}, enabled: true });
  Object.assign(c.state.imuLive, { socket: null, enabled: false, userStopped: true }); // user stopped IMU
  c.document.hidden = true; c.handleSensorStreamVisibility();
  assert.equal(c.timers.length, 1); assert.equal(c.timers[0].ms, 1000);
  c.document.hidden = false; c.handleSensorStreamVisibility();
  assert.ok(c.timers[0].cleared, 'short hide cancelled');
  assert.deepEqual(c.calls.filter(x => x[0] !== 'tick'), []);
  c.document.hidden = true; c.handleSensorStreamVisibility(); c.timers.at(-1).fn();
  assert.deepEqual(c.calls.filter(x => x[0] !== 'tick'), [['closeLidar', false], ['closeCamera', false]]);
  c.calls.length = 0; c.document.hidden = false; c.handleSensorStreamVisibility();
  assert.deepEqual(c.calls, [['startLidar'], ['startCamera'], ['tick']]);
  assert.equal(c.state.imuLive.socket, null, 'a user-stopped IMU stays stopped');
}

// A camera paused by acknowledgement timeouts restarts when visible again (S11).
{
  const c = setup();
  Object.assign(c.state.cameraLive, { socket: null, enabled: false, userStopped: true, ackExhausted: true });
  c.document.hidden = true; c.handleSensorStreamVisibility(); c.timers.at(-1).fn();
  assert.deepEqual(c.calls, []);
  c.document.hidden = false; c.handleSensorStreamVisibility();
  assert.deepEqual(c.calls, [['startCamera'], ['tick']]);
}

// A user-stopped camera (or playback pause) is not restarted.
{
  const c = setup();
  Object.assign(c.state.cameraLive, { socket: null, enabled: false, userStopped: true, ackExhausted: false });
  c.document.hidden = true; c.handleSensorStreamVisibility(); c.timers.at(-1).fn();
  c.document.hidden = false; c.handleSensorStreamVisibility();
  assert.deepEqual(c.calls, [['tick']]);
}

// Telemetry polling stops while hidden.
assert.ok(/async function tick\(\) \{\r?\n  if \(document\.hidden\) return;/.test(source));
console.log('sensor stream hidden/backoff tests passed');
