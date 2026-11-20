// The 자율주행 tab (2026-11-19): the GPS choice of a run (sent with 준비, the executor's refusal shown), the monitoring
// tiles and the centreline metrics (static/steer/auto-model.mjs), the run recording (every sensor before a go, stopped
// after the run only when this panel started it) and the executor picture of the run (static/steer/run-view.mjs).
// 2026-11-20: the panel with its three buttons (준비 does ack / end / start / Arm by itself, 자율 주행 시작 only once the
// corridor is acquired and with the recording first, 멈추기 always) and the one status sentence; no record button.
// One recording per driven stretch (it stops at a pause too, 다시 출발 starts a new one), the wait between a new
// recording and its go, the driving time in the sentence and a tile.
// Fake page and elements, no browser, no request leaves the process. Run: node --test tests/test_auto_monitor.mjs
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {
  GNSS_MODES, GNSS_MODE_TEXT, GO_AFTER_RECORD_MS, GO_SETTLE_MS, RUN_RECORD_SENSORS, durationText, gnssTile, metricsView, monitorView, prepareSequence,
  recordBeforeGo, recordStopWanted, runTimes, statusLine, tripSentence,
} from '../static/steer/auto-model.mjs';
import { bandCentre, point, polyline, pose, sceneBox, sceneFromState, sceneFromStatus } from '../static/steer/run-view.mjs';
import { trailStep } from '../static/steer/local-grid.mjs';

const now = 1_800_000_000_000;
const reply = (status) => ({ ok: true, configured: true, reachable: true, received_at: now / 1000, page_ms: now, status });
const tile = (view, name) => view.tiles.find(([label]) => label === name);

test('GPS per run: four modes, sent with start only when chosen', async () => {
  assert.deepEqual(GNSS_MODES, ['off', 'log_only', 'geofence', 'weak_constraint']);
  assert.deepEqual(Object.values(GNSS_MODE_TEXT), ['GPS 사용 안 함', 'GPS 기록만', 'GPS 범위 감시', 'GPS 사용함']);
  for (const [gnssMode, body] of [[null, { row_len_m: 40 }], ['geofence', { row_len_m: 40, gnss_mode: 'geofence' }]]) {
    const sent = [];
    await prepareSequence({ send: async (action, b) => { sent.push([action, b]); return { ok: true, reply: { status: { run_state: 'armed' } } }; },
      readStatus: async () => ({ ok: true, status: { run_state: 'idle' } }), sleep: async () => {}, rowLen: 40, gnssMode });
    assert.deepEqual(sent[0], ['start', body]);
  }
});

test('GPS tile: the run mode, its state, a mismatch with the request, an executor without the block', () => {
  assert.deepEqual(gnssTile({}, 'log_only'), ['GPS 기록만', '실행기가 GPS 상태를 보내지 않습니다.', '']);
  const run = { gnss: { mode: 'geofence', configured_mode: 'off', state: 'fix', hacc_m: 1.234, accepted: 12, rejected: 3 } };
  assert.deepEqual(gnssTile(run, 'geofence'), ['GPS 범위 감시', '확정 · ±1.23 m · 받음 12 · 거부 3', '']);
  const [, sub, level] = gnssTile(run, 'weak_constraint');
  assert.equal(level, 'warn');
  assert.match(sub, /^요청한 설정과 다름/, 'an older executor that ignored the field shows it');
  assert.equal(gnssTile({ gnss: { mode: 'weak_constraint', state: 'rejected_no_fix' } })[1], '위치 미확정이라 쓰지 않음',
    'no fix indoors: the run goes on without GPS');
  assert.equal(gnssTile({ gnss: { mode: 'weak_constraint', rtk: { state: 'fixed' }, counts: { accepted: 5, jump: 2, quality: 1 } } })[1],
    'RTK FIXED · 받음 5 · 거부 3');
  assert.equal(gnssTile({ gnss: { mode: 'geofence', fence_tripped: true } })[2], 'bad');
  assert.equal(gnssTile({ gnss: { mode: 'new_mode', state: 'odd' } })[0], 'new_mode', 'unknown values are shown as sent');
});

test('monitor tiles: seven on the screen (state, speed, lateral error, remaining distance, driving time, GPS, recording), the others for 자세히', () => {
  const status = { run_state: 'running', mode: 'MOTOR', T: 35, S: 87, steer: { meas: 70 },
    single_row: { lateral_error_m: -0.123, heading_error_deg: 2.34, distance_m: 12.34, end_remaining_m: 20.5, speed_m_s: 0.31, phase: 'FOLLOW' },
    speed: { target_mps: 0.35, limit_mps: 0.3, limit_reason: 'corridor' }, gnss: { mode: 'log_only', state: 'fix', hacc_m: 2 } };
  const bridge = { steering: { measured_mrad: 60, pwm: -142, pwm_peak: 180 }, steering_host: { target_mrad: 80 },
    drive: { left: { acked: 89 }, right: { acked: 89 }, max_pwm: 255 } };
  const view = monitorView(reply(status), bridge, { known: true, active: true, tag: 'autonomy', auto: true }, 'log_only', now);
  assert.deepEqual(view.tiles.map(([name]) => name), ['상태', '속도', '횡 오차', '남은 거리', '주행 시간', 'GPS', '기록']);
  assert.deepEqual(tile(view, '주행 시간').slice(1, 3), ['--', ''], 'an executor that sends no driving time');
  assert.deepEqual(view.more.map(([name]) => name), ['방향 오차', '속도 제한', '단계', '조향 명령 / 측정', '구동 출력', '조향 모터 PWM']);
  const other = (name) => view.more.find(([label]) => label === name);
  assert.deepEqual(tile(view, '상태').slice(1, 4), ['주행 중', '', 'ok']);
  assert.deepEqual(tile(view, '속도').slice(1, 3), ['0.31 m/s', '목표 0.35']);
  assert.equal(tile(view, '횡 오차')[1], '-0.12 m');
  assert.deepEqual(tile(view, '남은 거리').slice(1, 3), ['20.5 m', '간 거리 12.3 m']);
  assert.deepEqual(other('속도 제한').slice(1, 3), ['0.30 m/s', '통로 신뢰도']);
  assert.equal(other('방향 오차')[1], '2.3°');
  assert.equal(other('조향 명령 / 측정')[1], '왼 5.0° / 왼 4.0°', 'the executor numbers first (S, steer.meas)');
  assert.equal(other('구동 출력')[1], '35 %');
  assert.equal(other('구동 출력')[2], '보드 PWM 89 / 89 · 상한 255');
  assert.deepEqual(other('조향 모터 PWM').slice(1, 3), ['-142', '최근 최대 180']);
  assert.deepEqual(tile(view, '기록').slice(1, 4), ['기록 중', '모든 센서 자동 기록', 'ok']);
  // without the executor's steering numbers: the bridge's target and measurement
  const bare = monitorView(reply({ run_state: 'idle' }), bridge, null, '', now);
  assert.equal(bare.more.find(([label]) => label === '조향 명령 / 측정')[1], '왼 4.6° / 왼 3.4°');
  assert.equal(tile(bare, '기록'), undefined, 'no recorder view: no tile');
  assert.deepEqual(tile(bare, '남은 거리').slice(1, 3), ['--', ''], 'no row length, no distance yet');
  assert.deepEqual(monitorView(reply({ run_state: 'armed', speed: { target_mps: 0.4, limit_mps: null, limit_reason: 'not_running' } }), null, null, '', now)
    .more.find(([label]) => label === '속도 제한').slice(1, 3), ['--', ''], 'not driving: no limit and no reason code');
  // old or missing status
  assert.deepEqual(tile(monitorView(reply(status), null, null, '', now + 2000), '상태').slice(1, 4), ['끊김', '', 'bad']);
  assert.equal(tile(monitorView({ ok: false, configured: false }, null, null), '상태')[1], '실행기 없음');
  assert.equal(tile(monitorView(null, null, null), '상태')[1], '확인 중');
  assert.deepEqual(monitorView(null, null, null).tiles.map(([name]) => name), ['상태', 'GPS']);
  // why the run stopped, in words, under the state (the executor's own codes are in the folded rows)
  assert.equal(tile(monitorView(reply({ run_state: 'ended', stop_reason: 'ROW_END' }), null, null, '', now), '상태')[2], '열 끝 도착');
  assert.deepEqual(tile(monitorView(reply({ run_state: 'latched', stop_reason: 'OPERATOR_OR_BACKSTOP', stop_detail: 'operator_stop' }), null, null, '', now), '상태').slice(1, 4),
    ['멈춤', 'STOP 누름', 'bad']);
  // recording: waits for the start while no run drives; a run that drives without one is a warning
  assert.deepEqual(tile(monitorView(reply({}), null, { known: true, active: false }, '', now), '기록').slice(1, 4), ['대기', '출발하면 자동 시작', '']);
  assert.deepEqual(tile(monitorView(reply({ run_state: 'running' }), null, { known: true, active: false }, '', now), '기록').slice(1, 4), ['기록 꺼짐', '', 'warn']);
  assert.deepEqual(tile(monitorView(reply({ run_state: 'paused' }), null, { known: true, active: false }, '', now), '기록').slice(1, 4), ['대기', '출발하면 자동 시작', ''],
    'a paused run has no recording by design (one recording per driven stretch)');
  assert.deepEqual(tile(monitorView(reply({ run_state: 'ended' }), null, { known: true, active: true, auto: true, stopFailed: true }, '', now), '기록').slice(1, 4),
    ['기록 중', '자동으로 멈추지 못했습니다. 기록 탭에서 중지하세요.', 'warn']);
  assert.equal(tile(monitorView(reply({}), null, { known: false }, '', now), '기록')[1], '확인 중');
});

test('metrics: the reference centreline against the travelled path, grid figures beside, unknown names kept', () => {
  assert.equal(metricsView(reply({})), null);
  const m = metricsView(reply({ metrics: { lat_rms_m: 0.0412, lat_max_abs_m: 0.11, within_0_10_pct: 97.25, head_rms_deg: 1.234,
    grid_lat_rms_m: 0.05, samples: 240, distance_m: 18.2, new_figure: 0.5 } }));
  const rows = Object.fromEntries(m.tiles.map(([name, value, sub]) => [name, [value, sub]]));
  assert.deepEqual(rows['횡 RMS'], ['0.041 m', '격자 중심선 0.050 m']);
  assert.deepEqual(rows['±10 cm 안'], ['97.3 %', '']);
  assert.deepEqual(rows['방향 RMS'], ['1.23°', '']);
  assert.deepEqual(rows['표본'], ['240', '18.2 m 동안']);
  assert.deepEqual(rows.new_figure, ['0.5', '']);
  assert.equal(rows['횡 평균'], undefined, 'a figure the executor does not send is not shown');
});

test('run recording: a new one of every sensor before each go, stopped as soon as the run does not drive, only when this panel started it', () => {
  assert.deepEqual(RUN_RECORD_SENSORS, ['lidar', 'camera', 'imu', 'gps', 'drive', 'steering', 'executor', 'tf']);
  assert.equal(recordBeforeGo({ known: true, active: false }, true), 'start');
  assert.equal(recordBeforeGo({ known: true, active: true }, true), 'keep');
  assert.equal(recordBeforeGo({ known: true, active: true, tag: 'manual', sensors: RUN_RECORD_SENSORS }, true, RUN_RECORD_SENSORS), 'keep', 'by hand, every sensor');
  assert.equal(recordBeforeGo({ known: true, active: true, tag: 'autonomy', sensors: RUN_RECORD_SENSORS }, true, RUN_RECORD_SENSORS), 'restart',
    'the recording of an earlier stretch that nobody stopped: each stretch gets its own');
  assert.equal(recordBeforeGo({ known: false }, true), 'unknown');
  assert.equal(recordBeforeGo(null, false), 'skip');
  const rec = { known: true, active: true, id: 'r1' };
  const drove = { sawRunning: true, sinceGoMs: 9000 };
  for (const state of ['ended', 'latched', 'idle', 'paused', 'armed', 'ready']) assert.equal(recordStopWanted(reply({ run_state: state }), 'r1', rec, drove), true, state);
  assert.equal(recordStopWanted(reply({ run_state: 'running' }), 'r1', rec, drove), false, 'driving: the recording goes on');
  assert.equal(recordStopWanted(reply({ run_state: 'paused' }), null, rec, drove), false, 'not started here: never stopped here');
  assert.equal(recordStopWanted(reply({ run_state: 'paused' }), 'r1', { ...rec, id: 'r2' }, drove), false, 'another recording');
  assert.equal(recordStopWanted(reply({ run_state: 'paused' }), 'r1', { ...rec, active: false }, drove), false, 'already stopped');
  assert.equal(recordStopWanted({ ok: false }, 'r1', rec, drove), false, 'no status: wait (the server keeper decides)');
  // the go of the recording is on its way, or was just accepted: armed / paused is not "stopped" yet
  assert.equal(recordStopWanted(reply({ run_state: 'armed' }), 'r1', rec, { going: true }), false, 'recording, wait, heartbeat, go');
  assert.equal(recordStopWanted(reply({ run_state: 'latched' }), 'r1', rec, { going: true }), false, 'the go flow itself drops the recording');
  assert.equal(recordStopWanted(reply({ run_state: 'armed' }), 'r1', rec, { sinceGoMs: 300 }), false, 'the run shows running a tick after the go');
  assert.equal(recordStopWanted(reply({ run_state: 'armed' }), 'r1', rec, { sinceGoMs: GO_SETTLE_MS + 1 }), true, 'the go came to nothing');
  assert.equal(recordStopWanted(reply({ run_state: 'armed', operator_link: { go_active: true } }), 'r1', rec, { sinceGoMs: 60000 }), false,
    'the executor still holds the press (input warm-up): the run may yet leave');
  assert.equal(recordStopWanted(reply({ run_state: 'latched' }), 'r1', rec, { sinceGoMs: 300 }), true, 'a run that is over is over at once');
  assert.equal(recordStopWanted(reply({ run_state: 'armed' }), 'r1', rec), true, 'no go was sent for it');
  assert.equal(GO_AFTER_RECORD_MS, 2500, 'the wait between a new recording and its go');
});

test('driving time: words, the run total from the executor, the stretch since a start again, the sentence after a stop', () => {
  assert.deepEqual([0, 0.4, 35.2, 59.6, 60, 105, 3600, 3725, 7260].map(durationText),
    ['0초', '0초', '35초', '1분', '1분', '1분 45초', '1시간', '1시간 2분', '2시간 1분']);
  const run = (state, extra = {}) => reply({ run: 7, run_state: state, single_row: { distance_m: 43.2, speed_m_s: 0.38 },
    metrics: { run_id: 7, time_s: 105.2, distance_m: 43.1 }, ...extra });
  assert.deepEqual(runTimes(run('running')), { time_s: 105.2, distance_m: 43.2, part: null, source: 'executor' });
  assert.deepEqual(statusLine(run('running'), now), ['ok', '주행 중 · 43.2 m · 0.38 m/s · 1분 45초']);
  assert.deepEqual(statusLine(run('ended', { stop_reason: 'ROW_END', stop_detail: 'row_end' }), now),
    ['ok', '열 끝에 도착해 멈췄습니다. 1분 45초 동안 43.2 m를 주행했습니다.']);
  assert.deepEqual(statusLine(run('latched', { stop_reason: 'OPERATOR_OR_BACKSTOP', stop_detail: 'operator_stop' }), now),
    ['bad', 'STOP을 눌러 멈췄습니다. 1분 45초 동안 43.2 m를 주행했습니다. 다시 하려면 준비를 누르세요.']);
  assert.deepEqual(statusLine(run('paused', { stop_reason: 'OPERATOR_LINK_LOST', stop_detail: 'operator_link_lost', go_ready: true, go_blockers: [] }), now),
    ['warn', '화면 연결이 끊겨 잠시 멈췄습니다. 1분 45초 동안 43.2 m를 주행했습니다. 다시 출발을 누르세요.']);
  // the numbers of another run are not this run's; a run that did not drive says nothing
  assert.equal(runTimes(reply({ run: 8, run_state: 'armed', metrics: { run_id: 7, time_s: 105.2 } })).time_s, null);
  assert.deepEqual(statusLine(reply({ run: 8, run_state: 'latched', stop_reason: 'OPERATOR_OR_BACKSTOP', stop_detail: 'operator_stop',
    metrics: { run_id: 8, time_s: 0.3, distance_m: 0.0 } }), now), ['bad', 'STOP을 눌러 멈췄습니다. 다시 하려면 준비를 누르세요.']);
  assert.equal(tripSentence({ time_s: 12, distance_m: null }), '12초 동안 주행했습니다.');
  assert.equal(tripSentence(null), '');
  // a run that was started again: the whole run, then the stretch since the last start (the server's record of it)
  const open = { recording_id: 'b', run: 7, segment: 2, resumed: true, start: { run_drive_time_s: 85.2, run_distance_m: 33.4 }, go: { epoch_s: now / 1000 - 20 }, end: null };
  assert.deepEqual(runTimes(run('running'), open).part, { time_s: 20, distance_m: 9.800000000000004 });
  assert.deepEqual(tile(monitorView(run('running'), null, null, '', now, open), '주행 시간').slice(1, 3), ['1분 45초', '다시 출발 뒤 20초']);
  assert.equal(statusLine(run('running'), now, open)[1], '주행 중 · 43.2 m · 0.38 m/s · 1분 45초', 'the sentence stays the whole run');
  const closed = { ...open, end: { text: '열 끝 도착' }, drive_time_s: 20.1, distance_m: 9.8, run_drive_time_s: 105.2, run_distance_m: 43.2 };
  assert.equal(statusLine(run('ended', { stop_reason: 'ROW_END' }), now, closed)[1],
    '열 끝에 도착해 멈췄습니다. 모두 1분 45초 동안 43.2 m를 주행했습니다. 다시 출발한 뒤로는 20초 동안 9.8 m를 주행했습니다.');
  assert.equal(runTimes(run('ended'), { ...closed, run: 6 }).part, null, 'the record of another run is not used');
  assert.equal(runTimes(run('ended'), { ...closed, segment: 1, resumed: false }).part, null, 'a first stretch is the whole run');
  // an executor without the metrics: the server's clock of the stretches
  const bare = (state) => ({ ...reply({ run: 7, run_state: state, single_row: { distance_m: 5.0 } }), received_at: now / 1000 });
  const first = { recording_id: 'a', run: 7, segment: 1, resumed: false, start: { run_drive_time_s: 0, run_distance_m: 0 }, go: { epoch_s: now / 1000 - 12 }, end: null };
  assert.deepEqual(runTimes(bare('running'), first), { time_s: 12, distance_m: 5.0, part: null, source: 'clock' });
  assert.equal(runTimes(bare('ended'), { ...first, end: {}, run_drive_time_s: 13.5 }).time_s, 13.5);
  assert.equal(runTimes(bare('running')).time_s, null, 'nothing known: no time');
  assert.equal(statusLine(bare('running'), now)[1], '주행 중 · 5.0 m');
});

test('run picture: scene from the live status and from a recorded state message', () => {
  const sample = JSON.parse(fs.readFileSync(new URL('./fixtures/executor_status_front_steer.json', import.meta.url), 'utf8'));
  const status = { local_grid_map: sample.local_grid_map,
    single_row_geometry: { base: [2.3, 0.4, 0.1], band: { heading_rad: 0.1, centre_m: 0.05, width_m: 2.5 }, path: [[0, 0], [1, 0.1], [2.3, 0.4]],
      lookahead: [4, 0.5] } };
  const scene = sceneFromStatus(status);
  assert.deepEqual(scene.pose, [2.3, 0.4, 0.1]);
  assert.equal(scene.path.length, 3);
  assert.equal(scene.centre.length, 2, 'the grid fit centreline');
  assert.equal(scene.reference.length, 2, 'the band centreline');
  assert.deepEqual(scene.lookahead, [4, 0.5]);
  assert.equal(scene.axis, sample.local_grid_map.axis_rad);
  const box = sceneBox(scene, 720, 440);
  const [lx, ly] = box.screen(...scene.lookahead), [px, py] = box.screen(2.3, 0.4);
  assert.ok(lx > px, 'ahead is to the right');
  assert.ok(lx >= 0 && lx <= 720 && ly >= 0 && ly <= 440);
  assert.equal(sceneFromStatus({}), null, 'nothing to draw');
  assert.deepEqual(sceneFromStatus({ local_grid_map: { base_pose: [1, 2, 0] } }).pose, [1, 2, 0], 'an executor without the geometry');
  // recorded messages (schema ubts.row_executor.state.v1; read tolerantly)
  const msgs = [0, 1, 2, 3].map((k) => ({ pose: { x: k, y: 0.1 * k, yaw_deg: 5 }, reference: { points: [[-1, 0], [10, 0.2]] },
    lookahead: { x: k + 2, y: 0.2 } }));
  const recorded = sceneFromState(msgs[3], msgs.slice(0, 3));
  assert.equal(recorded.path.length, 4);
  assert.ok(Math.abs(recorded.pose[2] - 5 * Math.PI / 180) < 1e-12);
  assert.deepEqual(recorded.lookahead, [5, 0.2]);
  assert.ok(Math.abs(recorded.axis - Math.atan2(0.2, 11)) < 1e-12, 'the reference line is the axis');
  const box2 = sceneBox(recorded, 340, 260);
  for (const [x, y] of recorded.path) { const [sx, sy] = box2.screen(x, y); assert.ok(sx >= 0 && sx <= 340 && sy >= 0 && sy <= 260); }
  assert.equal(sceneFromState({}), null);
  assert.deepEqual(point({ x: 1, y: 2 }), [1, 2]);
  assert.equal(point([1, 'x']), null);
  assert.deepEqual(polyline({ a: [0, 0], b: [1, 1] }), [[0, 0], [1, 1]]);
  assert.equal(polyline([[0, 0]]), null);
  assert.deepEqual(pose([1, 2, 3, 4]), [1, 2, 3]);
  const line = bandCentre({ heading_rad: 0, centre_m: 0.5 }, [3, 0, 0], 2, 8);
  assert.deepEqual(line, [[1, 0.5], [11, 0.5]]);
});

test('path of an older executor: the base poses of the grid reads, a new list for a new run', () => {
  let trail = trailStep(null, { run: 1, local_grid_map: { base_pose: [0, 0, 0], updates: 1 } });
  trail = trailStep(trail, { run: 1, local_grid_map: { base_pose: [0.01, 0, 0], updates: 2 } });
  assert.equal(trail.points.length, 1, 'less than 5 cm: not a new point');
  trail = trailStep(trail, { run: 1, local_grid_map: { base_pose: [0.5, 0, 0], updates: 3 } });
  assert.equal(trail.points.length, 2);
  trail = trailStep(trail, { run: 2, local_grid_map: { base_pose: [9, 9, 0], updates: 1 } });
  assert.deepEqual(trail.points, [[9, 9]]);
});

// ---- the panel: three buttons, one status sentence, the GPS choice kept and sent, the run recording ----
function element(id) {
  const listeners = {};
  return {
    id, hidden: false, disabled: false, checked: true, className: '', textContent: '', value: '', dataset: {}, children: [],
    classList: { toggle() {}, add() {}, remove() {} },
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); },
    fire(name) { for (const fn of listeners[name] || []) fn({}); },
    replaceChildren(...items) { this.children = items; }, append(...items) { this.children.push(...items); },
  };
}
const timers = [];
after(() => { for (const id of timers) clearInterval(id); });
const READY = { run_state: 'armed', mode: 'MOTOR', go_ready: true, go_blockers: [] };
const STOPPED = { run_state: 'latched', mode: 'MOTOR', stop_reason: 'OPERATOR_OR_BACKSTOP', stop_detail: 'operator_stop' };

// The page with a small executor behind it: the run states its requests make (simulation repository
// tools/row_executor/executor_main.py: ack clears the latch, end closes the run, start makes it ready (MOTOR) or armed
// (SHADOW), arm arms it, go drives, stop latches). state.refuse[action] = an executor refusal code (HTTP 409).
async function panel({ status = READY, rec = { known: true, active: false }, mode = 'MOTOR', goWaitMs = 20 } = {}) {
  const ids = ['autoPanel', 'autoStatus', 'autoPrepare', 'autoPrimary', 'autoStopButton', 'autoEnd', 'autoMode', 'autoHint', 'autoRowLen',
    'autoSpeed', 'autoSpeedNote', 'autoGnssMode', 'autoMonitorGrid', 'autoMoreGrid', 'autoMonitorState', 'autoMetricsBlock', 'autoMetricsGrid', 'autoMetricsSource', 'autoScale',
    'autoScaleValue', 'autoScaleOffer', 'autoScaleText', 'autoScaleAccept', 'autoScaleDismiss', 'autoDetails', 'roverTabAutoBadge'];
  const els = Object.fromEntries(ids.map((id) => [id, element(id)]));
  const asked = new Set();
  const store = new Map([['ubts-auto-gnss-mode-v1', 'geofence']]);
  globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) };
  globalThis.document = { hidden: false, getElementById: (id) => { asked.add(id); return els[id] || null; }, addEventListener() {},
    createElement: () => element('') };
  const realSet = globalThis.setInterval;
  const intervals = [];
  globalThis.setInterval = (fn, ms) => { const id = realSet(() => {}, 1e6); timers.push(id); intervals.push([fn, ms]); return id; };
  const { initAutoPanel } = await import('../static/steer/auto-panel.mjs');
  const log = { actions: [], recordings: [], order: [], params: [] };
  const state = { status, rec, mode, refuse: {}, blockers: [], onRequest: null, onRecord: null, refuseParams: null, paramsDown: false, recordings: 0 };
  // POST /api/executor/params as the dashboard relays it: a set of target_speed_m_s changes the executor's value; a
  // refusal answers the executor's code (and problems) with its HTTP status; paramsDown: the request itself fails.
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    log.params.push([url, init.method, body]);
    if (state.paramsDown) throw new TypeError('fetch failed');
    if (state.refuseParams) return { ok: false, status: state.refuseParams.code, json: async () => state.refuseParams.body };
    const old = state.status.tuning.values.basic.target_speed_m_s, next = body.target_speed_m_s;
    const tuning = state.status.tuning;
    state.status = { ...state.status, speed: { ...(state.status.speed || {}), target_mps: next },
      tuning: { ...tuning, revision: tuning.revision + 1, basic: tuning.basic.map((item) => (item.name === 'target_speed_m_s' ? { ...item, value: next } : item)),
        values: { ...tuning.values, basic: { ...tuning.values.basic, target_speed_m_s: next } } } };
    return { ok: true, status: 200, json: async () => ({ ok: true, action: 'set', changes: [{ key: 'target_speed_m_s', old, new: next }], tuning: { revision: tuning.revision + 1 } }) };
  };
  const page = {
    currentRoute: () => '/rover-control',
    getJson: async () => ({ ok: true, configured: true, reachable: true, received_at: Date.now() / 1000, status: state.status }),
    controlRequest: async (url, body) => {
      const action = url.split('/').pop();
      log.actions.push([action, body]);
      log.order.push(action);
      if (state.onRequest) await state.onRequest(action);
      if (state.refuse[action]) { const err = new Error(`${url}: 409`); err.status = 409; err.body = { ok: false, error: state.refuse[action] }; throw err; }
      const at = state.status.run_state;
      if (action === 'ack' && at === 'latched') state.status = { ...state.status, run_state: 'ended' };
      if (action === 'end') state.status = { run_state: 'ended', mode: state.mode, stop_detail: 'operator_end' };
      if (action === 'start') state.status = { run_state: state.mode === 'MOTOR' ? 'ready' : 'armed', mode: state.mode, go_ready: !state.blockers.length, go_blockers: [...state.blockers] };
      if (action === 'arm') state.status = { ...state.status, run_state: 'armed' };
      if (action === 'go') state.status = { run_state: 'running', mode: state.mode, single_row: { distance_m: 0.4, speed_m_s: 0.2 } };
      if (action === 'stop') state.status = { ...STOPPED, mode: state.mode };
      return { ok: true };
    },
    recording: {
      view: () => state.rec,
      start: async (options) => {
        log.recordings.push(['start', options]); log.order.push('record-start');
        if (state.onRecord) await state.onRecord();
        state.recordings += 1;
        const id = state.recordings === 1 ? 'rec-9' : `rec-${8 + state.recordings}`;
        state.rec = { known: true, active: true, id, sensors: options.sensors, tag: options.tag };
        return { ok: true, recording_id: id };
      },
      stop: async () => {
        log.recordings.push(['stop']); log.order.push('record-stop');
        if (state.onRecordStop) await state.onRecordStop();
        state.rec = { known: true, active: false, segment: state.rec.segment };
        return { ok: true };
      },
    },
    driveStatus: () => null,
    goWaitMs,
  };
  const screen = initAutoPanel(page);
  globalThis.setInterval = realSet;
  const settle = async () => { for (let i = 0; i < 6; i += 1) await new Promise((done) => setTimeout(done, 0)); };
  await settle();
  // An action of a button (준비, the start) is over when the panel is not busy any more.
  const done = async () => { for (let i = 0; i < 600 && screen.busy(); i += 1) await new Promise((r) => setTimeout(r, 10)); await settle(); };
  const poll = async () => { await intervals.find(([, ms]) => ms === 500)[0](); await settle(); };
  const beat = async () => { await intervals.find(([, ms]) => ms === 100)[0](); await settle(); };
  const sent = () => log.actions.map(([action]) => action).filter((action) => action !== 'heartbeat');
  const line = () => [els.autoStatus.className.replace('test-result auto-status', '').trim(), els.autoStatus.textContent];
  return { els, store, page, state, log, screen, settle, done, poll, beat, sent, line, asked };
}

test('panel: the kept GPS choice goes with 준비; a refusal is one sentence in the status line until the state moves on', async () => {
  const p = await panel({ status: { run_state: 'idle', mode: 'MOTOR' } });
  assert.deepEqual(p.line(), ['', '준비를 누르세요.']);
  assert.equal(p.els.autoGnssMode.value, 'off', 'two choices since 11/20: an older remembered mode falls back to off');
  p.els.autoGnssMode.value = 'weak_constraint'; p.els.autoGnssMode.fire('change');
  assert.equal(p.store.get('ubts-auto-gnss-mode-v1'), 'weak_constraint');
  p.state.refuse.start = 'gnss_rtk_not_fixed';
  p.els.autoPrepare.fire('click'); await p.done();
  assert.deepEqual(p.log.actions[0], ['start', { row_len_m: null, gnss_mode: 'weak_constraint' }]);
  assert.deepEqual(p.line(), ['bad', '준비하지 못했습니다. RTK FIXED 상태가 아니어서 RTK 보정을 쓸 수 없습니다.'], 'what happened and why');
  await p.poll();
  assert.equal(p.line()[1], '준비하지 못했습니다. RTK FIXED 상태가 아니어서 RTK 보정을 쓸 수 없습니다.', 'it stays while nothing changed');
  p.state.refuse = {};
  p.els.autoGnssMode.value = 'off'; p.els.autoGnssMode.fire('change');
  p.els.autoPrepare.fire('click'); await p.done();
  assert.deepEqual(p.log.actions.find(([a], k) => a === 'start' && k > 0)[1], { row_len_m: null, gnss_mode: 'off' }, 'GPS 사용 안 함 is sent as off');
  assert.deepEqual(p.sent(), ['start', 'start', 'arm'], 'MOTOR: the run, then Arm, in the one press');
  assert.deepEqual(p.line(), ['ok', '출발할 수 있습니다.']);
  // a row length that is no number: nothing is sent
  const q = await panel({ status: { run_state: 'idle', mode: 'MOTOR' } });
  q.els.autoRowLen.value = '-3';
  q.els.autoPrepare.fire('click'); await q.done();
  assert.deepEqual([q.sent(), q.line()[1]], [[], '열 길이는 0보다 큰 수로 넣거나 비워 두세요.']);
});

test('panel: 준비 does every step by itself: a stopped run is acknowledged, an open run ended; no latch button, no record button', async () => {
  const p = await panel({ status: STOPPED });
  assert.deepEqual(p.line(), ['bad', 'STOP을 눌러 멈췄습니다. 다시 하려면 준비를 누르세요.'], 'why it stopped stays in the sentence until 준비');
  assert.deepEqual([p.els.autoPrepare.disabled, p.els.autoPrimary.disabled, p.els.autoPrimary.textContent], [false, true, '자율 주행 시작']);
  assert.equal(p.els.autoRowLen.disabled, false, 'the row length and the GPS choice can be set before 준비');
  await p.poll();
  assert.equal(p.line()[1], 'STOP을 눌러 멈췄습니다. 다시 하려면 준비를 누르세요.');
  p.els.autoRowLen.value = '42.5';
  p.els.autoPrepare.fire('click'); await p.done();
  assert.deepEqual(p.sent(), ['ack', 'start', 'arm'], 'the latch is acknowledged by 준비, then the run and Arm');
  assert.deepEqual(p.log.actions.find(([a]) => a === 'start')[1], { row_len_m: 42.5, gnss_mode: 'off' });
  assert.deepEqual(p.line(), ['ok', '출발할 수 있습니다.']);
  assert.deepEqual([p.els.autoPrepare.disabled, p.els.autoPrimary.disabled], [false, false]);
  assert.deepEqual([p.els.autoRowLen.disabled, p.els.autoGnssMode.disabled], [true, true], 'fixed for the run that is armed');
  // 준비 again while the run waits: the open run is ended, a new one made with the values on the screen
  p.els.autoPrepare.fire('click'); await p.done();
  assert.deepEqual(p.sent(), ['ack', 'start', 'arm', 'end', 'start', 'arm']);
  // SHADOW: start arms by itself, and the sentence says the vehicle will not move
  const q = await panel({ status: { run_state: 'ended', mode: 'SHADOW', stop_reason: 'ROW_END', stop_detail: 'row_end' }, mode: 'SHADOW' });
  assert.deepEqual(q.line(), ['ok', '열 끝에 도착해 멈췄습니다.']);
  q.els.autoPrepare.fire('click'); await q.done();
  assert.deepEqual([q.sent(), q.line()[1]], [['start'], '출발할 수 있습니다. 연습 모드라서 차는 움직이지 않습니다.']);
  for (const id of ['autoRecordButton', 'autoRecordState', 'autoRecordRuns', 'autoNotice']) assert.ok(!p.asked.has(id) && !q.asked.has(id), `${id} is gone`);
});

test('panel: the start is on only once the corridor is acquired; 다시 출발 after a pause; 준비 off while driving', async () => {
  const p = await panel({ status: { run_state: 'armed', mode: 'MOTOR', go_ready: false, go_blockers: ['corridor_not_acquired'] } });
  assert.deepEqual([p.els.autoPrimary.disabled, p.line()], [true, ['testing', '통로를 찾고 있습니다.']]);
  p.els.autoPrimary.fire('click'); await p.done();
  assert.deepEqual([p.sent(), p.log.recordings], [[], []], 'nothing is sent and nothing recorded before the corridor');
  p.state.status = { run_state: 'armed', mode: 'MOTOR', go_ready: false, go_blockers: ['operator_link_not_fresh'] };
  await p.poll();
  assert.deepEqual([p.els.autoPrimary.disabled, p.line()], [false, ['ok', '출발할 수 있습니다.']], 'the operator link is what the press itself brings');
  p.state.status = { run_state: 'paused', mode: 'MOTOR', go_ready: true, go_blockers: [], stop_reason: 'OPERATOR_LINK_LOST', stop_detail: 'operator_link_lost' };
  await p.poll();
  assert.deepEqual([p.els.autoPrimary.disabled, p.els.autoPrimary.textContent, p.line()[1]], [false, '다시 출발', '화면 연결이 끊겨 잠시 멈췄습니다. 다시 출발을 누르세요.']);
  p.state.status = { run_state: 'running', mode: 'MOTOR', single_row: { distance_m: 12.34, speed_m_s: 0.38 }, operator_link: { fresh: true, timeout_s: 0.5 } };
  await p.poll();
  assert.deepEqual([p.els.autoPrepare.disabled, p.els.autoPrimary.disabled, p.els.autoPrimary.textContent], [true, true, '자율 주행 시작']);
  assert.deepEqual(p.line(), ['ok', '주행 중 · 12.3 m · 0.38 m/s']);
  assert.equal(p.els.autoHint.textContent, '화면을 끄거나 가리면 0.5초 뒤 차가 멈춥니다.', "the executor's own time limit");
  assert.equal(p.els.autoRowLen.disabled, true);
  // a refused heartbeat shows in the sentence and goes with the next one that passes
  p.state.refuse.heartbeat = 'session_conflict';
  await p.beat(); p.screen.draw();
  assert.deepEqual(p.line(), ['bad', '화면 연결 신호가 거절됐습니다. 다른 화면에서 조종하고 있습니다.']);
  p.state.refuse = {};
  await p.beat(); p.screen.draw();
  assert.equal(p.line()[1], '주행 중 · 12.3 m · 0.38 m/s');
});

test('panel: the start records every sensor first, then the heartbeat and the go; the run end stops only that recording', async () => {
  const p = await panel();
  p.els.autoPrimary.fire('click'); await p.done();
  assert.deepEqual(p.log.recordings[0], ['start', { sensors: RUN_RECORD_SENSORS, tag: 'autonomy' }]);
  assert.deepEqual(p.log.order, ['record-start', 'heartbeat', 'go'], 'the recording, the operator link, then the go');
  assert.deepEqual(p.log.actions.at(-1), ['go', { session: p.screen.session() }]);
  assert.equal(p.screen.autoRecording(), 'rec-9');
  assert.equal(p.line()[1], '주행 중 · 0.4 m · 0.20 m/s');
  const recordTile = () => p.els.autoMonitorGrid.children.map((row) => row.children.map((c) => c.textContent)).find(([name]) => name === '기록');
  assert.deepEqual(recordTile(), ['기록', '기록 중', '모든 센서 자동 기록'], 'the recording shows as one tile');
  await p.poll();
  assert.equal(p.log.recordings.length, 1, 'driving: the recording goes on');
  p.state.status = STOPPED;
  await p.poll();
  assert.deepEqual(p.log.recordings.at(-1), ['stop'], 'stopped (STOP latched the run): the auto recording stops');
  assert.equal(p.screen.autoRecording(), null);
  await p.poll();
  assert.equal(p.log.recordings.length, 2, 'once');
});

test('panel: one recording per driven stretch: a pause stops it at once, 다시 출발 starts a new one, the end stops that one', async () => {
  const p = await panel();
  p.els.autoPrimary.fire('click'); await p.done();
  assert.deepEqual([p.log.order, p.screen.autoRecording()], [['record-start', 'heartbeat', 'go'], 'rec-9']);
  // the screen connection is lost: the executor pauses the run
  p.state.status = { run: 7, run_state: 'paused', mode: 'MOTOR', go_ready: true, go_blockers: [], stop_reason: 'OPERATOR_LINK_LOST', stop_detail: 'operator_link_lost',
    single_row: { distance_m: 12.3 }, metrics: { run_id: 7, time_s: 35.2, distance_m: 12.3 } };
  await p.poll();
  assert.deepEqual(p.log.recordings.map(([a]) => a), ['start', 'stop'], 'paused: the recording of the stretch is stopped and saved');
  assert.equal(p.screen.autoRecording(), null);
  assert.deepEqual(p.line(), ['warn', '화면 연결이 끊겨 잠시 멈췄습니다. 35초 동안 12.3 m를 주행했습니다. 다시 출발을 누르세요.']);
  const recordTile = () => p.els.autoMonitorGrid.children.map((row) => row.children.map((c) => c.textContent)).find(([name]) => name === '기록');
  assert.deepEqual(recordTile(), ['기록', '대기', '출발하면 자동 시작'], 'no warning: a paused run has no recording');
  await p.poll();
  assert.equal(p.log.recordings.length, 2, 'once');
  // 다시 출발: a new recording, then the go
  assert.equal(p.els.autoPrimary.textContent, '다시 출발');
  p.log.order.length = 0;
  p.els.autoPrimary.fire('click'); await p.done();
  assert.deepEqual(p.log.order, ['record-start', 'heartbeat', 'go'], 'a recording of its own, then the go');
  assert.deepEqual(p.log.recordings.at(-1), ['start', { sensors: RUN_RECORD_SENSORS, tag: 'autonomy' }]);
  assert.equal(p.screen.autoRecording(), 'rec-10', 'another recording than the first stretch');
  // the server's record of this stretch arrives with the recorder status: the tile says the stretch since the start again
  p.state.rec = { ...p.state.rec, segment: { recording_id: 'rec-10', run: 7, segment: 2, resumed: true, start: { run_drive_time_s: 35.2, run_distance_m: 12.3 }, end: null } };
  p.state.status = { run: 7, run_state: 'running', mode: 'MOTOR', single_row: { distance_m: 20.0, speed_m_s: 0.4 }, metrics: { run_id: 7, time_s: 55.2, distance_m: 20.0 } };
  await p.poll();
  const timeTile = () => p.els.autoMonitorGrid.children.map((row) => row.children.map((c) => c.textContent)).find(([name]) => name === '주행 시간');
  assert.deepEqual(timeTile(), ['주행 시간', '55초', '다시 출발 뒤 20초']);
  assert.equal(p.line()[1], '주행 중 · 20.0 m · 0.40 m/s · 55초');
  // the row end: the second recording stops; the sentence says the whole run and the last stretch
  p.state.rec = { ...p.state.rec, segment: { ...p.state.rec.segment, end: { text: '열 끝 도착' }, drive_time_s: 70, distance_m: 30.9 } };
  p.state.status = { run: 7, run_state: 'ended', mode: 'MOTOR', stop_reason: 'ROW_END', stop_detail: 'row_end', single_row: { distance_m: 43.2 },
    metrics: { run_id: 7, time_s: 105.2, distance_m: 43.2 } };
  await p.poll();
  assert.deepEqual(p.log.recordings.map(([a]) => a), ['start', 'stop', 'start', 'stop']);
  assert.deepEqual(p.line(), ['ok', '열 끝에 도착해 멈췄습니다. 모두 1분 45초 동안 43.2 m를 주행했습니다. 다시 출발한 뒤로는 1분 10초 동안 30.9 m를 주행했습니다.']);
});

test('panel: 다시 출발 while the recording of the stretch before is still being saved waits for it, then records anew', async () => {
  const p = await panel();
  p.els.autoPrimary.fire('click'); await p.done();
  let release = null;
  p.state.onRecordStop = () => new Promise((done) => { release = done; });        // the recorder takes its time to finish
  p.state.status = { run_state: 'paused', mode: 'MOTOR', go_ready: true, go_blockers: [] };
  await p.poll();
  assert.ok(release, 'the stop of the first recording is on its way');
  p.log.order.length = 0;
  p.els.autoPrimary.fire('click'); await p.settle();
  assert.deepEqual([p.line(), p.log.order], [['testing', '이전 구간의 기록을 저장하고 있습니다.'], []], 'nothing is started before the old recording is saved');
  p.state.onRecordStop = null;
  release(); await p.done();
  assert.deepEqual(p.log.order, ['record-start', 'heartbeat', 'go']);
  assert.equal(p.screen.autoRecording(), 'rec-10');
  // a recording of an earlier stretch that nobody stopped (the page was reloaded while paused): ended first, then a new one
  const q = await panel({ status: { run_state: 'paused', mode: 'MOTOR', go_ready: true, go_blockers: [] },
    rec: { known: true, active: true, id: 'old-stretch', tag: 'autonomy', sensors: RUN_RECORD_SENSORS } });
  await q.poll();
  assert.deepEqual(q.log.recordings, [], 'not started by this page: not stopped by its status reads (the server keeper does that)');
  q.els.autoPrimary.fire('click'); await q.done();
  assert.deepEqual(q.log.order, ['record-stop', 'record-start', 'heartbeat', 'go']);
});

test('panel: a new recording runs for a moment before the go; 멈추기 in that moment stops the recording and nothing leaves', async () => {
  const p = await panel({ goWaitMs: 250 });
  const started = Date.now();
  let during = null;
  p.state.onRequest = async (action) => { if (action === 'go') during = Date.now() - started; };
  p.els.autoPrimary.fire('click');
  for (let i = 0; i < 40 && p.line()[1] !== '기록을 시작했습니다. 잠시 뒤 출발합니다.'; i += 1) await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(p.line(), ['testing', '기록을 시작했습니다. 잠시 뒤 출발합니다.'], 'what the wait is for');
  assert.ok(!p.sent().includes('go'), 'the go is not sent while the recording settles');
  await p.done();
  assert.equal(p.sent().at(-1), 'go');
  assert.ok(during >= 240, `the go came ${during} ms after the press (the wait is 250 ms here)`);
  // 멈추기 during the wait
  const q = await panel({ goWaitMs: 400 });
  q.els.autoPrimary.fire('click');
  for (let i = 0; i < 40 && q.line()[1] !== '기록을 시작했습니다. 잠시 뒤 출발합니다.'; i += 1) await new Promise((r) => setTimeout(r, 5));
  const pressed = Date.now();
  q.els.autoStopButton.fire('click'); await q.done();
  assert.ok(Date.now() - pressed < 350, 'the wait ends with the press, not after its whole time');
  assert.deepEqual(q.sent(), ['stop'], 'the executor stop; no go');
  assert.deepEqual(q.log.recordings.map(([a]) => a), ['start', 'stop'], 'the recording that was just started is stopped');
  assert.equal(q.line()[1], '멈추기를 눌러 출발하지 않았습니다.');
  // a recording that was kept (started by hand, every sensor): no wait, and it is never dropped
  const r = await panel({ goWaitMs: 5000, rec: { known: true, active: true, id: 'by-hand', sensors: RUN_RECORD_SENSORS } });
  const t0 = Date.now();
  r.els.autoPrimary.fire('click'); await r.done();
  assert.ok(Date.now() - t0 < 1500 && r.sent().at(-1) === 'go', 'no wait for a recording that was already running');
  // after a go the run shows running one status later: the recording is not stopped in between
  const s = await panel();
  const realRequest = s.page.controlRequest;
  s.page.controlRequest = async (url, body) => { const out = await realRequest(url, body); if (url.endsWith('/go')) s.state.status = { ...READY }; return out; };
  s.els.autoPrimary.fire('click'); await s.done();
  await s.poll();
  assert.deepEqual(s.log.recordings.map(([a]) => a), ['start'], 'armed right after an accepted go: the recording stays');
  s.state.status = { run_state: 'running', mode: 'MOTOR' };
  await s.poll();
  s.state.status = { run_state: 'paused', mode: 'MOTOR' };
  await s.poll();
  assert.deepEqual(s.log.recordings.map(([a]) => a), ['start', 'stop'], 'it drove and paused: stopped');
});


test('panel: no go when the recording cannot start; a hand-started recording is kept', async () => {
  const p = await panel();
  p.page.recording.start = async () => ({ ok: false, error: 'recorder_unavailable' });
  p.els.autoPrimary.fire('click'); await p.done();
  assert.ok(!p.sent().includes('go'), 'no go without the recording');
  assert.deepEqual(p.line(), ['bad', '기록을 시작하지 못해 출발하지 않았습니다. 알려 온 이유는 recorder_unavailable입니다.']);
  const u = await panel({ rec: { known: false } });
  u.els.autoPrimary.fire('click'); await u.done();
  assert.ok(!u.sent().includes('go'), 'recorder state not known: no go');
  assert.match(u.line()[1], /^기록 상태를 확인할 수 없어 출발하지 않았습니다./);
  const q = await panel({ rec: { known: true, active: true, id: 'by-hand', sensors: RUN_RECORD_SENSORS } });
  q.els.autoPrimary.fire('click'); await q.done();
  assert.equal(q.log.recordings.length, 0, 'a running recording of every sensor is kept');
  assert.equal(q.sent().at(-1), 'go');
  q.state.status = { run_state: 'ended' };
  q.state.rec = { known: true, active: true, id: 'by-hand' };
  assert.equal(recordStopWanted(reply(q.state.status), q.screen.autoRecording(), q.state.rec, { sawRunning: true }), false, 'never stopped by the panel');
  await q.poll();
  assert.equal(q.log.recordings.length, 0, 'and it is not: the hand-started recording goes on after the run');
  // 11/20: a running recording without every sensor is replaced by one of every sensor before the go
  const r = await panel({ rec: { known: true, active: true, id: 'no-camera', sensors: ['lidar', 'imu', 'gps'] } });
  r.els.autoPrimary.fire('click'); await r.done();
  assert.deepEqual(r.log.recordings.slice(0, 2).map(([a]) => a), ['stop', 'start'], 'stopped, then every sensor');
  assert.equal(r.sent().at(-1), 'go');
  // a go the executor refuses: its reason in words
  const g = await panel();
  g.state.refuse.go = 'corridor_not_acquired';
  g.els.autoPrimary.fire('click'); await g.done();
  assert.deepEqual(g.line(), ['bad', '출발하지 못했습니다. 통로를 찾고 있습니다.']);
  assert.deepEqual(g.log.recordings.map(([a]) => a), ['start', 'stop'], 'the recording of a stretch that never began is stopped');
  assert.equal(g.screen.autoRecording(), null);
});

test('panel: 멈추기 always works and wins: no Arm after it during 준비, no go after it while the recording starts', async () => {
  // during 준비 (the start is on its way): nothing more of 준비 is sent, and the run it left open is stopped
  const p = await panel({ status: { run_state: 'idle', mode: 'MOTOR' } });
  p.state.onRequest = async (action) => { if (action === 'start') { p.state.onRequest = null; p.els.autoStopButton.fire('click'); } };
  p.els.autoPrepare.fire('click'); await p.done();
  assert.deepEqual(p.sent(), ['start', 'stop'], 'no Arm; the open run is stopped');
  assert.equal(p.state.status.run_state, 'latched');
  assert.deepEqual(p.line(), ['bad', '멈추기를 눌러 준비를 그만두었습니다.']);
  assert.equal(p.els.autoStopButton.disabled, false, '멈추기 is never switched off');
  // while the recording of a start is starting: the executor is stopped and the go is not sent
  const q = await panel();
  q.state.onRecord = async () => { q.els.autoStopButton.fire('click'); await q.settle(); };
  q.els.autoPrimary.fire('click'); await q.done();
  assert.deepEqual(q.sent(), ['stop'], 'the stop of the armed run; no go');
  assert.equal(q.line()[1], '멈추기를 눌러 출발하지 않았습니다.');
  assert.deepEqual(q.log.recordings.map(([a]) => a), ['start', 'stop'], 'the recording of the run that never left stops with it');
  await q.poll();
  assert.equal(q.log.recordings.length, 2, 'once');
  // a plain 멈추기 on a run: the executor stop, and the reason stays in the sentence
  const r = await panel({ status: { run_state: 'running', mode: 'MOTOR' } });
  r.els.autoStopButton.fire('click'); await r.settle(); await r.poll();
  assert.deepEqual([r.sent(), r.line()], [['stop'], ['bad', 'STOP을 눌러 멈췄습니다. 다시 하려면 준비를 누르세요.']]);
  // no run: nothing for the executor (the page's own handler still sends the bridge stop)
  const i = await panel({ status: { run_state: 'idle', mode: 'MOTOR' } });
  i.els.autoStopButton.fire('click'); await i.settle();
  assert.deepEqual(i.sent(), []);
});

// The executor's tuning block in the status (the shape read from the SIM stack on 2026-11-20).
const tuningOf = ({ value = 0.4, editable = true } = {}) => ({
  preset: 'default', revision: 0, editable, not_editable_because: editable ? null : 'tuning_locked_while_running', writable: false,
  basic: [{ name: 'speed_scale', value: 1, low: 0.5, high: 1.3, unit: '' }, { name: 'target_speed_m_s', value, low: 0.2, high: 0.5, unit: 'm/s' }],
  values: { basic: { speed_scale: 1, target_speed_m_s: value }, advanced: { min_speed_m_s: 0.2 }, locked: { max_speed_m_s: 0.5, overspeed_margin_m_s: 0.05 } } });

test('panel: the speed choice follows the executor (steps, value now, lock); a change sends one key and is not saved', async () => {
  const p = await panel({ status: { run_state: 'idle', mode: 'MOTOR', speed: { target_mps: 0.4 }, tuning: tuningOf() } });
  const select = p.els.autoSpeed, note = p.els.autoSpeedNote;
  const options = () => select.children.map((o) => [o.value, o.textContent]);
  const target = () => p.els.autoMonitorGrid.children.map((row) => row.children.map((c) => c.textContent)).find(([name]) => name === '속도');
  assert.deepEqual(options(), [['0.25', '0.25 m/s'], ['0.3', '0.30 m/s'], ['0.35', '0.35 m/s'], ['0.4', '0.40 m/s'], ['0.45', '0.45 m/s'], ['0.5', '0.50 m/s']],
    'the range of the executor in 0.05 m/s steps (0.20 is not offered: the executor refuses a target at its minimum speed)');
  assert.deepEqual([select.value, select.disabled, note.hidden], ['0.4', false, true], "the executor's value is the one shown");
  assert.equal(target()[2], '목표 0.40');
  // a change: one request, one key, no save
  select.value = '0.45'; select.fire('change'); await p.done();
  assert.deepEqual(p.log.params, [['/api/executor/params', 'POST', { target_speed_m_s: 0.45 }]]);
  assert.deepEqual(p.sent(), [], 'nothing else is sent: no run is made, nothing is saved');
  assert.deepEqual([select.value, select.disabled], ['0.45', false]);
  assert.equal(target()[2], '목표 0.45', "the monitoring tile shows the new target");
  assert.deepEqual([note.hidden, note.textContent], [false, '과속 한도(0.55 m/s)에 가까워 주행 중 잠깐 느려질 때가 있습니다.'], 'the one line of caution from 0.45 m/s');
  assert.deepEqual(p.line(), ['', '준비를 누르세요.'], 'no sentence for a change that went through');
  select.value = '0.3'; select.fire('change'); await p.done();
  assert.deepEqual(p.log.params.at(-1)[2], { target_speed_m_s: 0.3 });
  assert.deepEqual([select.value, note.hidden], ['0.3', true]);
  // the same value again: nothing is sent
  select.value = '0.3'; select.fire('change'); await p.done();
  assert.equal(p.log.params.length, 2);
  // a value changed elsewhere (the field tuning part, another screen) shows here, also one between the steps
  p.state.status = { ...p.state.status, speed: { target_mps: 0.38 }, tuning: tuningOf({ value: 0.38 }) };
  await p.poll();
  assert.deepEqual([select.value, options().map(([value]) => value)], ['0.38', ['0.25', '0.3', '0.35', '0.38', '0.4', '0.45', '0.5']]);
});

test('panel: a refused speed change goes back to the executor value and the status line says why', async () => {
  const p = await panel({ status: { run_state: 'ended', mode: 'MOTOR', speed: { target_mps: 0.4 }, tuning: tuningOf() } });
  const select = p.els.autoSpeed;
  p.state.refuseParams = { code: 409, body: { ok: false, error: 'tuning_locked_vehicle_moving' } };
  select.value = '0.5'; select.fire('change'); await p.done();
  assert.deepEqual(p.log.params, [['/api/executor/params', 'POST', { target_speed_m_s: 0.5 }]]);
  assert.equal(select.value, '0.4', 'back to the value the executor has');
  assert.deepEqual(p.line(), ['bad', '속도를 바꾸지 못했습니다. 차가 움직이고 있어 바꿀 수 없습니다.'], 'the refusal table of the field tuning part');
  assert.equal(p.els.autoSpeedNote.hidden, true, 'no caution for a value that was not taken');
  // the executor's own problem lines and a code the table does not know are shown, not hidden
  p.state.refuseParams = { code: 400, body: { ok: false, error: 'invalid_params', problems: ['target_speed_m_s: need min_speed_m_s < target_speed_m_s <= max_speed_m_s (locked)'] } };
  select.value = '0.25'; select.fire('change'); await p.done();
  assert.equal(p.line()[1], '속도를 바꾸지 못했습니다. 실행기가 값을 받지 않았습니다. target_speed_m_s: need min_speed_m_s < target_speed_m_s <= max_speed_m_s (locked)');
  assert.equal(select.value, '0.4');
  p.state.refuseParams = { code: 409, body: { ok: false, error: 'some_new_code' } };
  select.value = '0.3'; select.fire('change'); await p.done();
  assert.equal(p.line()[1], '속도를 바꾸지 못했습니다. 알려 온 이유는 some_new_code입니다.');
  // the request itself fails
  p.state.refuseParams = null; p.state.paramsDown = true;
  select.value = '0.3'; select.fire('change'); await p.done();
  assert.deepEqual([p.line()[1], select.value], ['속도를 바꾸지 못했습니다. 실행기에 연결하지 못했습니다.', '0.4']);
  // and the three buttons are as before
  p.state.paramsDown = false;
  p.els.autoPrepare.fire('click'); await p.done();
  assert.deepEqual(p.sent(), ['start', 'arm']);
});

test('panel: the speed choice is off while a run is open, and off with the value only when the executor sends no range', async () => {
  const p = await panel({ status: { ...READY, speed: { target_mps: 0.4 }, tuning: tuningOf({ editable: false }) } });
  const select = p.els.autoSpeed;
  assert.deepEqual([select.disabled, select.value, p.els.autoRowLen.disabled], [true, '0.4', true], 'armed: locked like the row length and GPS');
  select.value = '0.5'; select.fire('change'); await p.done();
  assert.deepEqual([p.log.params, select.value], [[], '0.4'], 'a change that got through a locked choice is not sent and is put back');
  for (const state of ['running', 'paused', 'ready']) {
    p.state.status = { run_state: state, mode: 'MOTOR', speed: { target_mps: 0.4 }, tuning: tuningOf({ editable: false }) };
    await p.poll();
    assert.equal(select.disabled, true, state);
  }
  p.state.status = { ...STOPPED, speed: { target_mps: 0.4 }, tuning: tuningOf() };
  await p.poll();
  assert.equal(select.disabled, false, 'stopped: the speed of the next run can be chosen before 준비');
  // 준비 keeps it off while it works (one thing at a time)
  p.state.onRequest = async () => { assert.equal(select.disabled, true, 'off during 준비'); };
  p.els.autoPrepare.fire('click'); await p.done();
  p.state.onRequest = null;
  // an executor without the range: the value now, nothing to choose
  const q = await panel({ status: { run_state: 'idle', mode: 'MOTOR', speed: { target_mps: 0.4 }, tuning: { preset: null, revision: 0, editable: false } } });
  assert.deepEqual([q.els.autoSpeed.disabled, q.els.autoSpeed.children.map((o) => o.textContent), q.els.autoSpeed.value], [true, ['0.40 m/s'], '0.4']);
  const r = await panel({ status: { run_state: 'idle', mode: 'MOTOR' } });
  assert.deepEqual([r.els.autoSpeed.disabled, r.els.autoSpeed.children.map((o) => o.textContent)], [true, ['--']], 'no value known: a dash');
});

test('state v1 message: centreline.odom, grid_centreline.odom and lookahead.odom are drawn', async () => {
  const mod = await import('../static/steer/run-view.mjs');
  const build = mod.sceneFromState || mod.stateScene || mod.sceneFromMessage;
  if (typeof build !== 'function') return;          // the builder name differs: nothing to check here
  const msg = { schema: 'ubts.row_executor.state.v1', pose: { x: 1, y: 0.1, yaw_deg: 2 },
    centreline: { odom: [[-1, 0], [9, 0]], base: [[-2, 0], [8, 0]], axis_deg: 0, width_m: 3.1 },
    grid_centreline: { odom: [[-1, 0.02], [9, 0.02]] }, lookahead: { base: [2.5, -0.1], odom: [3.5, 0] } };
  const scene = build(msg, []);
  assert.ok(scene && scene.reference && scene.reference.length === 2);
  assert.deepEqual(scene.lookahead, [3.5, 0]);
  assert.ok(scene.centre && scene.centre.length === 2);
});
