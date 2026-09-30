// Fake DOM and deferred HTTP only. Never starts a real recording.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
const code = source.slice(source.indexOf('async function refreshRecordingStatus()'), source.indexOf('async function loadRecordings()'));
const ids = ['roverRecordingStatus', 'roverRecordStartButton', 'roverRecordStopButton', 'roverRecordingTimer', 'roverRecorderState', 'roverRecorderKind', 'roverRecordImuToggle', 'roverRecordGpsToggle'];
const nodes = Object.fromEntries(ids.map(id => [id, {textContent: '', disabled: false}]));
let now = 10000, getCalls = 0, resolveGet;
const timers = new Map(); let timerId = 0;
const live = (active, duration_s=0) => ({ok: true, active, state: active ? 'recording' : 'idle', duration_s, kind: 'both'});
const context = {
  state: {recordings: {kind: 'both', includeImu: true, includeGps: true, status: live(false), lastStatusAt: now, lastStatusSteadyAt: now}},
  $: id => nodes[id], text: (id, value) => {nodes[id].textContent = value;},
  Date: {now: () => now}, document: {querySelectorAll: () => []},
  performance: {now: () => now}, AbortController,
  setTimeout: callback => {timers.set(++timerId, callback); return timerId;},
  clearTimeout: id => timers.delete(id),
  fmtClock: seconds => `elapsed:${seconds}`, currentRoute: () => '/rover-control',
  debugLog() {}, getJson: () => {getCalls++; return new Promise(resolve => {resolveGet = resolve;});},
  postJson: async path => ({status: live(path.endsWith('/start'))}), loadRecordings: async () => {},
};
vm.createContext(context); vm.runInContext(code, context);
(async () => {
  context.renderRecordingStatus();
  assert.equal(nodes.roverRecordStartButton.disabled, false);
  assert.equal(nodes.roverRecordStopButton.disabled, true);
  const first = context.refreshRecordingStatus();
  await context.refreshRecordingStatus();
  assert.equal(getCalls, 1, 'one status read at a time');
  resolveGet(live(true, 2)); await first;
  assert.equal(nodes.roverRecordingTimer.textContent, 'elapsed:2');
  assert.equal(nodes.roverRecordStartButton.disabled, true);
  assert.equal(nodes.roverRecordStopButton.disabled, false);
  now += 5001; context.renderRecordingStatus();
  assert.equal(nodes.roverRecordingTimer.textContent, '--:--');
  assert.equal(nodes.roverRecorderState.textContent, '상태 미확인');
  assert.equal(nodes.roverRecordStartButton.disabled, true);
  assert.equal(nodes.roverRecordStopButton.disabled, false, 'stop remains available when status is unknown');
  context.state.recordings.status = {ok: false, active: null, status_fresh: false,
    last_known: live(true, 99), state: 'unknown'};
  context.state.recordings.lastStatusAt = now; context.renderRecordingStatus();
  assert.equal(nodes.roverRecordingTimer.textContent, '--:--');
  assert.doesNotMatch(nodes.roverRecordingStatus.textContent, /기록 중/);
  const pending = context.refreshRecordingStatus();
  const staleGet = resolveGet;
  await context.stopRecording();
  const freshAfterStop = context.refreshRecordingStatus();
  assert.equal(getCalls, 3, 'an aborted old read does not block polling after STOP');
  staleGet(live(true, 123)); await pending;
  resolveGet(live(false)); await freshAfterStop;
  assert.equal(context.state.recordings.status.active, false, 'older GET cannot undo STOP response');
  assert.equal(nodes.roverRecordStartButton.disabled, false);
  assert.equal(nodes.roverRecordingTimer.textContent, '00:00');
  const beforeStart = context.refreshRecordingStatus();
  await context.startRecording('both');
  resolveGet(live(false)); await beforeStart;
  assert.equal(context.state.recordings.status.active, true, 'older idle GET cannot undo START response');
  assert.equal(nodes.roverRecordStopButton.disabled, false, 'button unlocks after action settles');
  const slow = context.refreshRecordingStatus();
  now += 6001; resolveGet(live(false)); await slow;
  assert.equal(nodes.roverRecordStartButton.disabled, true, 'late arrival cannot renew original request age');
  assert.equal(nodes.roverRecordingTimer.textContent, '--:--');
  const hung = context.refreshRecordingStatus(); const hungResolve = resolveGet;
  for (const callback of Array.from(timers.values())) callback();
  assert.equal(context.state.recordings.status.state, 'unavailable');
  assert.equal(context.state.recordings.statusInFlight, null);
  const recovered = context.refreshRecordingStatus();
  resolveGet(live(false)); await recovered;
  hungResolve(live(true, 999)); await hung;
  assert.equal(context.state.recordings.status.active, false, 'timeout cannot overwrite recovered status');
  assert.equal(nodes.roverRecordStartButton.disabled, false);
  context.postJson = async () => {now += 6001; return {status: live(true)};};
  await context.startRecording('both');
  assert.equal(nodes.roverRecordingTimer.textContent, '--:--', 'action response age also includes transit time');
  testSecondaryRecorderRow();
  console.log('recording status UI: 11 lifecycle cases + 8 detail-row/timer freshness cases PASS');
})().catch(error => {console.error(error); process.exitCode = 1;});

// Execute the real generic row renderer, rover render, and existing one-second
// UI timer. The receiver and HTTP are absent; no telemetry event drives expiry.
function testSecondaryRecorderRow() {
  const elements = new Map();
  class Node {
    constructor() { this.textContent = ''; this.className = ''; this.dataset = {}; this.children = []; this.fields = {}; }
    set id(value) { this._id = value; elements.set(value, this); }
    get id() { return this._id; }
    set innerHTML(value) {
      this._html = value; this.children = []; this.fields = {};
      for (const selector of ['.status-dot', 'small', 'b']) this.fields[selector] = new Node();
      const dot = value.match(/class="(status-dot [^"]*)"/);
      if (dot) this.fields['.status-dot'].className = dot[1];
    }
    get innerHTML() { return this._html || ''; }
    append(child) { this.children.push(child); }
    querySelector(selector) { return this.fields[selector]; }
  }
  const node = id => { if (!elements.has(id)) { const n = new Node(); n.id = id; } return elements.get(id); };
  for (const id of [...ids, 'roverControlDetails']) node(id);
  let clock = 50000, localTick;
  const c = {
    state: {recordings: {kind: 'both', includeImu: true, includeGps: true,
      status: {...live(true), recording_id: 'current-session'}, lastStatusSteadyAt: clock}, imuLive: {}},
    SIMULATION: true, window: {SimMap: {autonomyStatus: () => ({active:false})}},
    $: id => elements.get(id), text: (id, value) => {node(id).textContent = value;},
    performance: {now: () => clock}, document: {createElement: () => new Node(), querySelectorAll: () => []},
    escapeHtml: value => String(value), renderRoverMotionReadout() {}, renderImuLiveState() {},
    fmtClock: seconds => `elapsed:${seconds}`, currentRoute: () => '/rover-control',
    routeSupportsCameraLive: () => false,
    setInterval: (callback, ms) => {assert.equal(ms, 1000); localTick = callback;},
    getJson: () => {throw Error('Freshness rendering must not require network progress');},
  };
  vm.createContext(c);
  const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
  vm.runInContext(slice('function renderStatusRows(', 'function syncItems(')
    + slice('function renderRoverControl(', 'function initRoverControl(') + code, c);
  vm.runInContext(source.slice(source.lastIndexOf('setInterval(() => {')), c);
  const telemetry = {services: {data_recorder: {...live(false), recording_id: 'stale-telemetry'}}};
  const field = selector => elements.get('roverRecorderDetail').querySelector(selector);
  c.renderRoverControl(telemetry);
  assert.equal(node('roverControllerLabel').textContent, 'Gazebo', 'SIM controller label is preserved');
  assert.equal(field('.status-dot').className, 'status-dot ok');
  assert.equal(field('small').textContent, 'current-session', 'ordered recorder response beats telemetry');
  assert.match(field('b').textContent, /\uae30\ub85d \uc911/);
  clock += 5000; localTick();
  assert.equal(field('.status-dot').className, 'status-dot ok', 'same inclusive5s gate as primary controls');
  clock += 1; localTick();
  assert.equal(field('.status-dot').className, 'status-dot warn', 'expires without another rover/telemetry render');
  assert.equal(field('b').textContent, '\uc0c1\ud0dc \ubbf8\ud655\uc778');
  assert.equal(node('roverRecorderState').textContent, field('b').textContent);
  assert.equal(node('roverRecordStartButton').disabled, true);
  assert.equal(node('roverRecordStopButton').disabled, false);
  assert.notEqual(field('small').textContent, 'current-session', 'expired ID is not presented as current');
  c.renderRoverControl({services: {data_recorder: live(true)}});
  assert.equal(field('.status-dot').className, 'status-dot warn', 'late general telemetry cannot refresh the row');
  c.state.recordings.status = live(false); c.state.recordings.lastStatusSteadyAt = clock;
  localTick();
  assert.equal(field('.status-dot').className, 'status-dot ok');
  assert.equal(field('b').textContent, 'idle', 'fresh ordered response recovers without general telemetry');
  c.state.recordings.status = {...live(true), status_fresh: false, last_known: live(true)};
  localTick();
  assert.equal(field('.status-dot').className, 'status-dot warn', 'historical snapshot cannot become live');
  c.state.recordings.status = null; c.renderRoverControl({services: {data_recorder: live(true)}});
  assert.equal(field('.status-dot').className, 'status-dot warn', 'missing ordered status cannot fall back to telemetry');
  c.state.recordings.status = {...live(false), active: 'false'}; localTick();
  assert.equal(field('.status-dot').className, 'status-dot warn', 'active must be a boolean');
  c.state.recordings.status = live(false); clock = c.state.recordings.lastStatusSteadyAt - 1; localTick();
  assert.equal(field('.status-dot').className, 'status-dot warn', 'clock regression is unknown');
  clock = Infinity; localTick();
  assert.equal(field('.status-dot').className, 'status-dot warn', 'nonfinite age is unknown');
}
