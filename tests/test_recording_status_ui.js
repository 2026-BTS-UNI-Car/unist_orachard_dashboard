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
  console.log('recording status UI: 11 lifecycle/freshness/race/slow-link scenarios PASS');
})().catch(error => {console.error(error); process.exitCode = 1;});
