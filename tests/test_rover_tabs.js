// Rover Control in-page tabs and the recording sensor choice (2026-11-19, static/app/10-rover-control.js and
// 11-recording.js): the chosen tab is kept per browser (storage may fail), only the buttons of the tab row change,
// leaving 수동 조종 with the Arm held sends STOP, an alias address opens the tab of its part; one check box per sensor,
// at least LiDAR or the camera, the old kind/IMU choice carried over, the request with both the new and the old fields.
// Fake DOM only. Run: node tests/test_rover_tabs.js
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource();
const between = (start, end) => { const a = source.indexOf(start), b = source.indexOf(end, a); assert.ok(a >= 0 && b > a, start); return source.slice(a, b); };

const tabCode = between('const ROVER_TABS = ', '\nfunction setArchiveRange(');
const recordCode = between('const RECORDING_SENSOR_LABEL = ', '// The recorder state as the panels read it');
const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'index.html'), 'utf8');

function setup({ stored = null, failStorage = false, lease = null } = {}) {
  const store = new Map(stored ? Object.entries(stored) : []);
  const layout = { dataset: { tab: 'manual' } };
  const buttons = ['manual', 'auto', 'setup', 'record'].map((tab) => {
    const attrs = {};
    return { dataset: { roverTabButton: tab }, attrs, tabIndex: 0, listeners: {}, setAttribute(k, v) { attrs[k] = v; },
      addEventListener(type, fn) { this.listeners[type] = fn; }, focus() {} };
  });
  const parts = { liveMapPanel: 'manual auto', roverArchiveList: 'record auto', steerCalPanel: 'setup', tuningPanel: 'setup' };
  const element = (id) => (id === 'roverLayout' ? layout : parts[id]
    ? { closest: () => ({ dataset: { roverTab: parts[id] } }) } : null);
  const events = [], stops = [], calls = { renderRecordings: 0, stopArchive: 0 };
  const context = {
    state: { roverControl: { lease, inFlight: false }, archivePlayback: { playing: true }, recordings: { sensors: ['lidar', 'imu'] } },
    $: element, document: { querySelectorAll: (sel) => (sel === '[data-rover-tab-button]' ? buttons : []) },
    localStorage: { getItem: (k) => { if (failStorage) throw new Error('blocked'); return store.has(k) ? store.get(k) : null; },
      setItem: (k, v) => { if (failStorage) throw new Error('blocked'); store.set(k, String(v)); } },
    window: { dispatchEvent: (e) => events.push(e.detail), LiveMap: { routeChanged() {} }, RoverGps: { routeChanged() {} } },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    stopRoverNow: (reason) => stops.push(reason), stopArchivePlayback: () => { calls.stopArchive += 1; },
    renderRecordings: () => { calls.renderRecordings += 1; }, requestChartAnimation() {}, renderRecordingStatus() {},
    ROVER_TAB_KEY: 'ubts-rover-tab-v1', ROVER_RECORD_SENSORS_KEY: 'ubts-record-sensors-v1',
    ROVER_RECORD_KIND_KEY: 'ubts-rover-record-kind-v1', IMU_RECORD_ENABLED_KEY: 'ubts-record-imu-enabled-v1',
  };
  vm.createContext(context);
  vm.runInContext(`${tabCode}\n${recordCode}`, context);
  return { context, store, layout, buttons, events, stops, calls };
}

// ---- tabs ----
{
  const t = setup({ stored: { 'ubts-rover-tab-v1': 'auto' } });
  t.context.initRoverTabs();
  assert.equal(t.layout.dataset.tab, 'auto', 'the kept tab');
  assert.deepEqual(t.buttons.map((b) => b.attrs['aria-selected']), ['false', 'true', 'false', 'false']);
  assert.deepEqual(t.buttons.map((b) => b.tabIndex), [-1, 0, -1, -1]);
  assert.equal(t.context.roverPartShown('liveMapPanel'), true, 'the live map is in 수동 조종 and 자율주행');
  assert.equal(t.context.roverPartShown('steerCalPanel'), false);
  assert.equal(t.context.roverTabOf('tuningPanel'), 'setup');
  t.buttons[2].listeners.click();
  assert.equal(t.layout.dataset.tab, 'setup');
  assert.equal(t.store.get('ubts-rover-tab-v1'), 'setup', 'kept per browser');
  assert.deepEqual({ ...t.events.at(-1) }, { tab: 'setup', before: 'auto' });
  assert.equal(t.calls.stopArchive, 1, 'a playing recording stops when its part leaves the screen');
  t.buttons[2].listeners.keydown({ key: 'ArrowRight', preventDefault() {} });
  assert.equal(t.layout.dataset.tab, 'record', 'arrow keys move along the row');
  t.context.setRoverTab('bogus');
  assert.equal(t.layout.dataset.tab, 'manual', 'an unknown tab is 수동 조종');
  assert.deepEqual([...t.stops], [], 'no Arm held: no STOP');
}
{
  const t = setup({ lease: 'L1' });
  t.context.initRoverTabs();
  assert.equal(t.layout.dataset.tab, 'manual', 'nothing kept: 수동 조종');
  t.context.setRoverTab('auto');
  assert.deepEqual([...t.stops], ['tab_changed'], 'leaving 수동 조종 with the Arm held sends STOP');
  t.context.setRoverTab('setup');
  assert.deepEqual([...t.stops], ['tab_changed'], 'only when leaving 수동 조종');
}
{
  const t = setup({ failStorage: true });
  t.context.initRoverTabs();
  t.context.setRoverTab('record');
  assert.equal(t.layout.dataset.tab, 'record', 'storage blocked: the tab still changes');
}
// The alias addresses open the tab of their part.
{
  const shell = between('function focusRoutePart(id) {', 'function renderRoute() {');
  assert.match(shell, /roverTabOf\(id\)/);
  assert.match(shell, /setRoverTab\(tab, \{ persist: false \}\)/);
}

// ---- recording sensors ----
{
  const t = setup();
  const c = t.context;
  assert.deepEqual([...c.storedRecordingSensors({ getItem: () => null })], ['lidar', 'imu'], 'nothing kept: the default');
  assert.deepEqual([...c.storedRecordingSensors({ getItem: (k) => (k === 'ubts-record-sensors-v1' ? '["camera","gps","bogus"]' : null) })],
    ['camera', 'gps']);
  assert.deepEqual([...c.storedRecordingSensors({ getItem: (k) => (k === 'ubts-record-sensors-v1' ? '["gps"]' : null) })], ['lidar', 'imu'],
    'a kept choice without LiDAR or camera is not used');
  const old = (kind, imu) => ({ getItem: (k) => ({ 'ubts-rover-record-kind-v1': kind, 'ubts-record-imu-enabled-v1': imu }[k] ?? null) });
  assert.deepEqual([...c.storedRecordingSensors(old('both', '0'))], ['lidar', 'camera', 'gps', 'drive', 'steering', 'executor', 'tf'],
    'the old page choice carries over');
  assert.deepEqual([...c.storedRecordingSensors(old('camera', '1'))], ['camera', 'imu', 'gps', 'drive', 'steering', 'executor', 'tf']);
  c.setRecordingSensors(['lidar', 'camera', 'imu'], true);
  assert.deepEqual([c.state.recordings.kind, c.state.recordings.includeImu, c.state.recordings.includeGps], ['both', true, false]);
  assert.equal(t.store.get('ubts-record-sensors-v1'), '["lidar","camera","imu"]');
  c.setRecordingSensor('camera', false, false);
  assert.equal(c.state.recordings.kind, 'lidar');
  assert.deepEqual(JSON.parse(JSON.stringify(c.recordingStartBody(['camera', 'gps'], 'autonomy'))),
    { sensors: ['camera', 'gps'], tag: 'autonomy', kind: 'camera', include_imu: false, include_gps: true });
  assert.equal(c.recordingStartBody(['lidar'], 'weird').tag, 'manual');
  assert.equal(c.recordingSensorsLabel(['lidar', 'camera', 'imu', 'gps', 'drive', 'steering', 'executor', 'tf']), '모든 센서');
  assert.equal(c.recordingSensorsLabel(['lidar', 'gps']), 'LiDAR+GPS');
  assert.deepEqual([...c.recordingSensorsOf({ kind: 'lidar', includeImu: true, includeGps: false })], ['lidar', 'imu'], 'an old caller');
}
// The markup: the check boxes in the recorder's order, no kind buttons left.
assert.deepEqual([...html.matchAll(/data-record-sensor="([a-z]+)"/g)].map((m) => m[1]), ['lidar', 'camera', 'imu', 'gps', 'drive', 'steering', 'executor', 'tf']);
assert.doesNotMatch(html, /data-record-kind|roverRecordImuToggle|roverRecordGpsToggle/);
console.log('PASS: Rover Control tabs (kept per browser, STOP when leaving 수동 조종 armed, alias opens its tab) and one check box per recorded sensor');
