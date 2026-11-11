// Archive-only tests with fake DOM/transport: never connects to a rover.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource();
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
// trimArchiveTimelineCache() in the first cut reads this top-level constant, which is
// declared outside the cut range. Take the real declaration instead of a copied value.
const segmentSeconds = source.match(/^const ARCHIVE_SEGMENT_SECONDS = \d+;/m);
assert.ok(segmentSeconds, 'app.js declares ARCHIVE_SEGMENT_SECONDS');
const code = segmentSeconds[0] + '\n' + slice('function timelineShellFromRecording(', 'function archiveFrameKey(')
  + slice('function mergeArchiveMotorSegment(', 'function nearestTimelineFrame(');
const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
const elements = new Map(['roverArchiveMotorPanel', 'roverArchiveMotorRows', 'roverArchiveMotorTime', 'roverArchiveMotorStatus', 'roverArchiveMotorCaption', 'roverArchiveMotorOutputLabel'].map(id => [id, {hidden: false, textContent: '', innerHTML: ''}]));
const context = {state: {recordings: {selectedId: 'test', timeline: null}, archivePlayback: {cache: {}}},
  $: id => elements.get(id), text: (id, value) => {elements.get(id).textContent = value;}, escapeHtml,
  clearTimeout() {}, URL: {revokeObjectURL() {}}, archiveFrameKey: () => {throw Error('No unrelated sensor frame expected');}};
vm.createContext(context);
vm.runInContext(code, context);
const descriptor = {schema: 'orchard.motor_pwm.timeline.v1', enabled: true, sample_hz: 2, stale_after_s: 1.5, frame_count: 3,
  pins: [{pin: 'D8', gpio: 15, role: 'left_forward'}, {pin: 'D1', gpio: 5, role: 'driver_enable'}]};
const frame = (t, requested, reported, extra = {}) => ({t_s: t, source_stale: false, source_age_s: 0.1, status: {connected: true},
  pins: {D8: {requested_pwm_derived: requested, reported_pwm: reported}, D1: {requested_pwm_derived: null, reported_pwm: null}}, ...extra});
context.state.recordings.timeline = context.timelineShellFromRecording({id: 'test', timeline_summary: {ok: true, motor_frames: 3, duration_s: 8}});
assert.equal(context.state.recordings.timeline.motor.frame_count, 3);
assert.equal(context.state.recordings.timeline.sync.motor_frames, 3);
context.prepareArchiveTimelineCache(context.state.recordings.timeline);
// Segments can arrive in any order and include a carry-in/duplicate sample.
context.mergeArchiveTimelineSegment({motor: {...descriptor, frames: [frame(4.2, 204, 180), frame(0.2, 64, 30)]}});
context.mergeArchiveTimelineSegment({motor: {frames: [frame(0.2, 64, 30), frame(1.5, 128, 90), frame(null, 0, 0), frame('0', 0, 0), frame(-1, 0, 0)]}});
const frames = context.state.archivePlayback.cache.motorFrames;
assert.deepEqual(Array.from(frames, item => item.t_s), [0.2, 1.5, 4.2]);
assert.equal(context.state.recordings.timeline.motor.pins.length, 2);
assert.equal(context.state.recordings.timeline.motor.frames.length, 0, 'descriptor does not duplicate cached frames');
for (const t of [-1, 0, 0.199, 0.2, 0.8, 1.499, 1.5, 4.199, 4.2, 9]) {
  const expected = frames.filter(item => item.t_s <= t).at(-1) || null;
  assert.equal(context.archiveMotorFrameAtOrBefore(frames, t), expected, `last preceding frame at ${t}`);
}
assert.equal(context.archiveMotorFrameAtOrBefore(frames, NaN), null);
assert.equal(context.archiveMotorFrameAtOrBefore([], 1), null);
for (const value of [null, undefined, '', '0', NaN, Infinity, -1, 256, 0.5]) assert.equal(context.archiveMotorPwmLabel(value), '미기록');
assert.equal(context.archiveMotorPwmLabel(0), '0 / 255 · 0.0%');
assert.equal(context.archiveMotorPwmLabel(255), '255 / 255 · 100.0%');
const motor = context.state.recordings.timeline.motor;
context.renderArchiveMotor(motor, frames, 0.19);
assert.match(elements.get('roverArchiveMotorStatus').textContent, /이전의 모터 스냅샷이 없습니다/);
assert.doesNotMatch(elements.get('roverArchiveMotorRows').innerHTML, /64 \/ 255|30 \/ 255/);
context.renderArchiveMotor(motor, frames, 1.49);
assert.match(elements.get('roverArchiveMotorRows').innerHTML, /64 \/ 255 · 25.1%/);
assert.match(elements.get('roverArchiveMotorRows').innerHTML, /30 \/ 255 · 11.8%/);
assert.doesNotMatch(elements.get('roverArchiveMotorRows').innerHTML, /128 \/ 255|204 \/ 255/);
assert.match(elements.get('roverArchiveMotorRows').innerHTML, /D1.*미기록.*미기록/);
assert.match(elements.get('roverArchiveMotorRows').innerHTML, /GPIO 15/);
context.renderArchiveMotor(motor, frames, 3.1);
assert.match(elements.get('roverArchiveMotorStatus').textContent, /오래되거나/);
assert.match(elements.get('roverArchiveMotorRows').innerHTML, /128 \/ 255/, 'stale value stays visibly historical');
context.renderArchiveMotor(motor, [frame(1, null, 40, {source_stale: true, source_age_s: null, status: {connected: false}})], 1.01);
assert.match(elements.get('roverArchiveMotorStatus').textContent, /오래되거나.*연결 끊김/);
assert.doesNotMatch(elements.get('roverArchiveMotorStatus').textContent, /상태 파일 경과/);
assert.match(elements.get('roverArchiveMotorRows').innerHTML, /미기록.*40 \/ 255/);
// Simulation speed-derived outputs are never labeled as physical MCU reports.
context.renderArchiveMotor({...motor, source_kind: 'simulation', sample_hz: 4}, [frame(1, 64, null, {
  status: {connected: true, simulated: true}, pins: {D8: {requested_pwm_derived: 64, reported_pwm: null, simulated_output_pwm: 127}}
})], 1.2);
assert.equal(elements.get('roverArchiveMotorOutputLabel').textContent, '시뮬레이션 출력 PWM');
assert.match(elements.get('roverArchiveMotorCaption').textContent, /4 Hz 시뮬레이션.*가상 속도 명령.*실제 GPIO/);
assert.match(elements.get('roverArchiveMotorRows').innerHTML, /127 \/ 255 · 49.8%/);
assert.match(elements.get('roverArchiveMotorStatus').textContent, /가상 구동 준비됨/);
assert.doesNotMatch(elements.get('roverArchiveMotorStatus').textContent, /MCU 연결/);
context.renderArchiveMotor({...motor, source_kind: 'simulation', sample_hz: 4}, [], 0);
assert.equal(elements.get('roverArchiveMotorOutputLabel').textContent, '시뮬레이션 출력 PWM');
context.renderArchiveMotor(motor, frames, 1.5);
assert.equal(elements.get('roverArchiveMotorOutputLabel').textContent, 'MCU 보고 PWM');
assert.match(elements.get('roverArchiveMotorCaption').textContent, /2 Hz 상태 스냅샷/);
// Partial/failed archives are visibly marked, without exposing raw diagnostics.
for (const issue of [{error: 'private raw error'}, {sidecar_read_error: 'private raw error'}, {sidecar_malformed_rows: 2}, {dropped_time_frames: 1}]) {
  context.renderArchiveMotor({...motor, ...issue}, frames, 1.5);
  assert.match(elements.get('roverArchiveMotorStatus').textContent, /일부 모터 기록 누락 또는 읽기 오류/);
  assert.doesNotMatch(elements.get('roverArchiveMotorStatus').textContent, /private raw error/);
}
context.renderArchiveMotor({...motor, error: null, sidecar_malformed_rows: 0, dropped_time_frames: 0}, frames, 1.5);
assert.doesNotMatch(elements.get('roverArchiveMotorStatus').textContent, /일부 모터 기록/);
// Rewind must replace, not preserve, the later signal display.
context.renderArchiveMotor(motor, frames, 4.2);
context.renderArchiveMotor(motor, frames, 0.2);
assert.match(elements.get('roverArchiveMotorRows').innerHTML, /64 \/ 255/);
assert.doesNotMatch(elements.get('roverArchiveMotorRows').innerHTML, /204 \/ 255/);
context.renderArchiveMotor({enabled: true, pins: [{pin: '<img src=x>', gpio: null, role: '<script>'}]}, [], 0);
assert.match(elements.get('roverArchiveMotorRows').innerHTML, /&lt;img/);
assert.doesNotMatch(elements.get('roverArchiveMotorRows').innerHTML, /<script>|GPIO 0/);
// Legacy archives show absence, never invented zero PWM or a current pin map.
context.state.recordings.timeline = context.timelineShellFromRecording({id: 'legacy', timeline_summary: {ok: true, lidar_frames: 1}});
context.prepareArchiveTimelineCache(context.state.recordings.timeline);
assert.equal(context.state.archivePlayback.cache.motorFrames.length, 0);
context.renderArchiveMotor(context.state.recordings.timeline.motor, [], 0);
assert.match(elements.get('roverArchiveMotorStatus').textContent, /기록이 없습니다/);
assert.match(elements.get('roverArchiveMotorRows').innerHTML, /핀 매핑 기록 없음/);
assert.doesNotMatch(elements.get('roverArchiveMotorRows').innerHTML, /0 \/ 255/);
context.state.recordings.selectedId = null;
context.renderArchiveMotor(null, [], 0);
assert.equal(elements.get('roverArchiveMotorPanel').hidden, true);
for (const id of elements.keys()) assert.match(html, new RegExp(`id="${id}"`));
assert.ok(slice('function renderArchiveFrame(', 'function mergeArchiveMotorSegment(').includes('renderArchiveMotor(timeline?.motor, cache.motorFrames || [], timeS)'), 'archive playhead renders motor table');
// Switching archives clears the previous signal table immediately; a late
// detail response from the old archive must not replace the new selection.
vm.runInContext(slice('async function selectRecording(', 'function timelineShellFromRecording('), context);
(async () => {
  const pending = new Map();
  const selected = [];
  context.getJson = url => new Promise((resolve, reject) => pending.set(url, {resolve, reject}));
  context.stopArchivePlayback = () => {};
  context.renderRecordings = () => {};
  context.renderRecordingDetail = detail => {selected.push(detail.id);};
  context.scheduleArchiveSegmentsForTime = async () => {};
  context.renderArchiveFrame = () => {};
  context.renderStatusRows = () => {throw Error('Stale request errors must not reach the selected archive');};
  const oldSelection = context.selectRecording('old');
  const newSelection = context.selectRecording('new');
  assert.match(elements.get('roverArchiveMotorRows').innerHTML, /핀 매핑 기록 없음/);
  pending.get('/api/recordings/new').resolve({id: 'new', timeline_summary: {ok: true, motor_frames: 1}});
  await newSelection;
  pending.get('/api/recordings/old').resolve({id: 'old', timeline_summary: {ok: true, motor_frames: 50}});
  await oldSelection;
  assert.deepEqual(selected, ['new']);
  assert.equal(context.state.recordings.timeline.id, 'new');
  const failingOldSelection = context.selectRecording('failing-old');
  const finalSelection = context.selectRecording('final');
  pending.get('/api/recordings/final').resolve({id: 'final', timeline_summary: {ok: true}});
  await finalSelection;
  pending.get('/api/recordings/failing-old').reject(Error('late failure'));
  await failingOldSelection;
  assert.equal(context.state.recordings.timeline.id, 'final');
  console.log('PASS: motor archive segment cache, preceding-only seek/rewind, PWM unknowns, stale/disconnected snapshots, legacy archives, escaping and out-of-order archive selection');
})().catch(error => {console.error(error); process.exitCode = 1;});
