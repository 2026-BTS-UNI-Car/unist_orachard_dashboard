// 2026-11-20: on Rover Control the status poll asks for the drive bundle (/api/telemetry?bundle=drive: connection, meta,
// rover, rover_source, sensors, services, snapshot_age_s; no system part). The drawing threw on it, the poll counted
// that as a failed request and the bar read '연결 끊김' after three polls although the link answered every time.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app', '04-telemetry.js'), 'utf8');

function cut(name) {
  // the text of a top-level function of the part (to the next line that starts a top-level statement)
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `function ${name} is in 04-telemetry.js`);
  const from = source.lastIndexOf('\n', start) + 1;
  const end = source.indexOf('\n}\n', start);
  return source.slice(from, end + 3);
}

const DRIVE_BUNDLE = { connection: {}, meta: { generated_at: 1 }, rover: {}, rover_source: 'bridge', sensors: {},
                       services: {}, snapshot_age_s: 0.1 };

test('the drive bundle is drawn without an error and draws nothing of the other pages', () => {
  const drawn = [];
  const ctx = vm.createContext({
    text: (id) => drawn.push(id), Date, Number, Boolean,
    fmtPercent: () => '', memoryPercent: () => null, fmtBytes: () => '', memoryTotal: () => null, memoryLabel: () => '',
    renderRoverControl: () => drawn.push('renderRoverControl'), renderConnection: () => drawn.push('renderConnection'),
  });
  vm.runInContext(`${cut('isDriveBundle')}\n${cut('renderTelemetry')}`, ctx);
  assert.equal(vm.runInContext('isDriveBundle', ctx)(DRIVE_BUNDLE), true);
  assert.equal(vm.runInContext('isDriveBundle', ctx)({ ...DRIVE_BUNDLE, system: {}, network: {}, docker: {} }), false);
  assert.doesNotThrow(() => vm.runInContext('renderTelemetry', ctx)(DRIVE_BUNDLE));
  assert.deepEqual(drawn, ['updatedAt'], 'only the time of the answer');
});

test('a full answer without a load value draws "--" (the line that threw)', () => {
  assert.match(source, /const overviewLoad = Number\(\(sys\.loadavg \|\| \[\]\)\[0\]\);/);
  assert.doesNotMatch(source, /sys\.loadavg\[0\]\.toFixed/);
});

test('an answered poll is a live link even when drawing it fails', async () => {
  const calls = [];
  const state = { telemetryFailures: 2, lastTelemetryPoll: 0, tickInFlight: false, cameraLive: {} };
  const ctx = vm.createContext({
    document: { hidden: false }, state, Date, AbortController,
    window: { setTimeout: () => 0, clearTimeout: () => {} },
    telemetryIntervalMs: () => 0, telemetryPath: () => '/api/telemetry?bundle=drive',
    getJson: async () => DRIVE_BUNDLE, pushHistory: () => {},
    renderTelemetry: () => { throw new TypeError('drawing failed'); },
    requestChartAnimation: () => {}, setConnection: (level, label) => calls.push([level, label]),
    debugLog: (...args) => calls.push(['log', args[1]]),
  });
  vm.runInContext(cut('tick'), ctx);
  await vm.runInContext('tick', ctx)();
  assert.equal(state.telemetryFailures, 0, 'the answered poll clears the failure count');
  assert.deepEqual(calls[0], ['ok', '연결됨']);
  assert.ok(calls.some(([kind, text]) => kind === 'log' && /drawing it failed/.test(text)), 'the drawing error is logged');
  assert.equal(state.tickInFlight, false);
});

test('a poll that does not answer still counts towards 연결 끊김', async () => {
  const calls = [];
  const state = { telemetryFailures: 2, lastTelemetryPoll: 0, tickInFlight: false, cameraLive: {} };
  const ctx = vm.createContext({
    document: { hidden: false }, state, Date, AbortController,
    window: { setTimeout: () => 0, clearTimeout: () => {} },
    telemetryIntervalMs: () => 0, telemetryPath: () => '/api/telemetry?bundle=drive',
    getJson: async () => { throw new Error('http_failed'); }, pushHistory: () => {}, renderTelemetry: () => {},
    requestChartAnimation: () => {}, setConnection: (level, label) => calls.push([level, label]), debugLog: () => {},
  });
  vm.runInContext(cut('tick'), ctx);
  await vm.runInContext('tick', ctx)();
  assert.equal(state.telemetryFailures, 3);
  assert.deepEqual(calls, [['bad', '연결 끊김']]);
});
