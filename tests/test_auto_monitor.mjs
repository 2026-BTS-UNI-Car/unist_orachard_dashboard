// The 자율주행 tab (2026-11-19): the GPS choice of a run (sent with 준비, the executor's refusal shown), the monitoring
// tiles and the centreline metrics (static/steer/auto-model.mjs), the run recording (every sensor before a go, stopped
// after the run only when this panel started it) and the executor picture of the run (static/steer/run-view.mjs).
// 2026-11-20: the panel with its three buttons (준비 does ack / end / start / Arm by itself, 자율 주행 시작 only once the
// corridor is acquired and with the recording first, 멈추기 always) and the one status sentence; no record button.
// Fake page and elements, no browser, no request leaves the process. Run: node --test tests/test_auto_monitor.mjs
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {
  GNSS_MODES, GNSS_MODE_TEXT, RUN_RECORD_SENSORS, gnssTile, metricsView, monitorView, prepareSequence, recordAfterRun, recordBeforeGo,
} from '../static/steer/auto-model.mjs';
import { bandCentre, point, polyline, pose, sceneBox, sceneFromState, sceneFromStatus } from '../static/steer/run-view.mjs';
import { trailStep } from '../static/steer/local-grid.mjs';

const now = 1_800_000_000_000;
const reply = (status) => ({ ok: true, configured: true, reachable: true, received_at: now / 1000, status });
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
  assert.deepEqual(gnssTile({}, 'log_only'), ['GPS 기록만', '실행기가 GPS 상태를 보내지 않음', '']);
  const run = { gnss: { mode: 'geofence', configured_mode: 'off', state: 'fix', hacc_m: 1.234, accepted: 12, rejected: 3 } };
  assert.deepEqual(gnssTile(run, 'geofence'), ['GPS 범위 감시', '확정 · ±1.23 m · 받음 12 · 거부 3', '']);
  const [, sub, level] = gnssTile(run, 'weak_constraint');
  assert.equal(level, 'warn');
  assert.match(sub, /^요청\(GPS 사용함\)과 다름/, 'an older executor that ignored the field shows it');
  assert.equal(gnssTile({ gnss: { mode: 'weak_constraint', state: 'rejected_no_fix' } })[1], '위치 없음(그 fix 안 씀)',
    'no fix indoors: the run goes on without GPS');
  assert.equal(gnssTile({ gnss: { mode: 'weak_constraint', rtk: { state: 'fixed' }, counts: { accepted: 5, jump: 2, quality: 1 } } })[1],
    'RTK FIXED · 받음 5 · 거부 3');
  assert.equal(gnssTile({ gnss: { mode: 'geofence', fence_tripped: true } })[2], 'bad');
  assert.equal(gnssTile({ gnss: { mode: 'new_mode', state: 'odd' } })[0], 'new_mode', 'unknown values are shown as sent');
});

test('monitor tiles: six on the screen (state, speed, lateral error, remaining distance, GPS, recording), the others for 자세히', () => {
  const status = { run_state: 'running', mode: 'MOTOR', T: 35, S: 87, steer: { meas: 70 },
    single_row: { lateral_error_m: -0.123, heading_error_deg: 2.34, distance_m: 12.34, end_remaining_m: 20.5, speed_m_s: 0.31, phase: 'FOLLOW' },
    speed: { target_mps: 0.35, limit_mps: 0.3, limit_reason: 'corridor' }, gnss: { mode: 'log_only', state: 'fix', hacc_m: 2 } };
  const bridge = { steering: { measured_mrad: 60, pwm: -142, pwm_peak: 180 }, steering_host: { target_mrad: 80 },
    drive: { left: { acked: 89 }, right: { acked: 89 }, max_pwm: 255 } };
  const view = monitorView(reply(status), bridge, { known: true, active: true, tag: 'autonomy', auto: true }, 'log_only', now);
  assert.deepEqual(view.tiles.map(([name]) => name), ['상태', '속도', '횡 오차', '남은 거리', 'GPS', '기록']);
  assert.deepEqual(view.more.map(([name]) => name), ['방향 오차', '속도 제한', '단계', '조향 명령 / 측정', '구동 duty', '조향 모터 PWM']);
  const other = (name) => view.more.find(([label]) => label === name);
  assert.deepEqual(tile(view, '상태').slice(1, 4), ['주행 중', '', 'ok']);
  assert.deepEqual(tile(view, '속도').slice(1, 3), ['0.31 m/s', '목표 0.35']);
  assert.equal(tile(view, '횡 오차')[1], '-0.12 m');
  assert.deepEqual(tile(view, '남은 거리').slice(1, 3), ['20.5 m', '간 거리 12.3 m']);
  assert.deepEqual(other('속도 제한').slice(1, 3), ['0.30 m/s', '통로 신뢰도']);
  assert.equal(other('방향 오차')[1], '2.3°');
  assert.equal(other('조향 명령 / 측정')[1], '왼 5.0° / 왼 4.0°', 'the executor numbers first (S, steer.meas)');
  assert.equal(other('구동 duty')[1], '35 %');
  assert.equal(other('구동 duty')[2], '보드 PWM 89 / 89 · 상한 255');
  assert.deepEqual(other('조향 모터 PWM').slice(1, 3), ['-142', '최근 최대 180']);
  assert.deepEqual(tile(view, '기록').slice(1, 4), ['기록 중', 'run 자동 기록(모든 센서)', 'ok']);
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
    ['멈춤', '운영자 STOP', 'bad']);
  // recording: waits for the start while no run drives; a run that drives without one is a warning
  assert.deepEqual(tile(monitorView(reply({}), null, { known: true, active: false }, '', now), '기록').slice(1, 4), ['대기', '출발하면 자동 시작', '']);
  assert.deepEqual(tile(monitorView(reply({ run_state: 'running' }), null, { known: true, active: false }, '', now), '기록').slice(1, 4), ['기록 안 함', '', 'warn']);
  assert.deepEqual(tile(monitorView(reply({ run_state: 'ended' }), null, { known: true, active: true, auto: true, stopFailed: true }, '', now), '기록').slice(1, 4),
    ['기록 중', '자동으로 멈추지 못함: 기록 탭에서 중지', 'warn']);
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

test('run recording: every sensor before a go, kept when one runs, stopped only when this panel started it', () => {
  assert.deepEqual(RUN_RECORD_SENSORS, ['lidar', 'camera', 'imu', 'gps', 'drive', 'steering', 'executor', 'tf']);
  assert.equal(recordBeforeGo({ known: true, active: false }, true), 'start');
  assert.equal(recordBeforeGo({ known: true, active: true }, true), 'keep');
  assert.equal(recordBeforeGo({ known: false }, true), 'unknown');
  assert.equal(recordBeforeGo(null, false), 'skip');
  const rec = { known: true, active: true, id: 'r1' };
  for (const state of ['ended', 'latched', 'idle']) assert.equal(recordAfterRun(reply({ run_state: state }), 'r1', rec), true, state);
  for (const state of ['running', 'paused', 'armed', 'ready']) assert.equal(recordAfterRun(reply({ run_state: state }), 'r1', rec), false, state);
  assert.equal(recordAfterRun(reply({ run_state: 'ended' }), null, rec), false, 'not started here: never stopped here');
  assert.equal(recordAfterRun(reply({ run_state: 'ended' }), 'r1', { ...rec, id: 'r2' }), false, 'another recording');
  assert.equal(recordAfterRun({ ok: false }, 'r1', rec), false, 'no status: wait');
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
async function panel({ status = READY, rec = { known: true, active: false }, mode = 'MOTOR' } = {}) {
  const ids = ['autoPanel', 'autoStatus', 'autoPrepare', 'autoPrimary', 'autoStopButton', 'autoEnd', 'autoMode', 'autoHint', 'autoRowLen',
    'autoGnssMode', 'autoMonitorGrid', 'autoMoreGrid', 'autoMonitorState', 'autoMetricsBlock', 'autoMetricsGrid', 'autoMetricsSource', 'autoScale',
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
  const log = { actions: [], recordings: [], order: [] };
  const state = { status, rec, mode, refuse: {}, blockers: [], onRequest: null, onRecord: null };
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
        state.rec = { known: true, active: true, id: 'rec-9', sensors: options.sensors };
        return { ok: true, recording_id: 'rec-9' };
      },
      stop: async () => { log.recordings.push(['stop']); log.order.push('record-stop'); state.rec = { known: true, active: false }; return { ok: true }; },
    },
    driveStatus: () => null,
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
  assert.deepEqual(p.line(), ['', '준비를 누르세요']);
  assert.equal(p.els.autoGnssMode.value, 'off', 'two choices since 11/20: an older remembered mode falls back to off');
  p.els.autoGnssMode.value = 'weak_constraint'; p.els.autoGnssMode.fire('change');
  assert.equal(p.store.get('ubts-auto-gnss-mode-v1'), 'weak_constraint');
  p.state.refuse.start = 'gnss_rtk_not_fixed';
  p.els.autoPrepare.fire('click'); await p.done();
  assert.deepEqual(p.log.actions[0], ['start', { row_len_m: null, gnss_mode: 'weak_constraint' }]);
  assert.deepEqual(p.line(), ['bad', '준비 실패(새 run): RTK FIXED 가 아님: RTK 보정을 쓸 수 없다'], 'where and why');
  await p.poll();
  assert.equal(p.line()[1], '준비 실패(새 run): RTK FIXED 가 아님: RTK 보정을 쓸 수 없다', 'it stays while nothing changed');
  p.state.refuse = {};
  p.els.autoGnssMode.value = 'off'; p.els.autoGnssMode.fire('change');
  p.els.autoPrepare.fire('click'); await p.done();
  assert.deepEqual(p.log.actions.find(([a], k) => a === 'start' && k > 0)[1], { row_len_m: null, gnss_mode: 'off' }, 'GPS 사용 안 함 is sent as off');
  assert.deepEqual(p.sent(), ['start', 'start', 'arm'], 'MOTOR: the run, then Arm, in the one press');
  assert.deepEqual(p.line(), ['ok', '출발할 수 있음']);
  // a row length that is no number: nothing is sent
  const q = await panel({ status: { run_state: 'idle', mode: 'MOTOR' } });
  q.els.autoRowLen.value = '-3';
  q.els.autoPrepare.fire('click'); await q.done();
  assert.deepEqual([q.sent(), q.line()[1]], [[], '열 길이는 0보다 큰 수이거나 비워 둔다']);
});

test('panel: 준비 does every step by itself: a stopped run is acknowledged, an open run ended; no latch button, no record button', async () => {
  const p = await panel({ status: STOPPED });
  assert.deepEqual(p.line(), ['bad', '멈춤: 운영자 STOP — 차를 다시 놓고 준비'], 'why it stopped stays in the sentence until 준비');
  assert.deepEqual([p.els.autoPrepare.disabled, p.els.autoPrimary.disabled, p.els.autoPrimary.textContent], [false, true, '자율 주행 시작']);
  assert.equal(p.els.autoRowLen.disabled, false, 'the row length and the GPS choice can be set before 준비');
  await p.poll();
  assert.equal(p.line()[1], '멈춤: 운영자 STOP — 차를 다시 놓고 준비');
  p.els.autoRowLen.value = '42.5';
  p.els.autoPrepare.fire('click'); await p.done();
  assert.deepEqual(p.sent(), ['ack', 'start', 'arm'], 'the latch is acknowledged by 준비, then the run and Arm');
  assert.deepEqual(p.log.actions.find(([a]) => a === 'start')[1], { row_len_m: 42.5, gnss_mode: 'off' });
  assert.deepEqual(p.line(), ['ok', '출발할 수 있음']);
  assert.deepEqual([p.els.autoPrepare.disabled, p.els.autoPrimary.disabled], [false, false]);
  assert.deepEqual([p.els.autoRowLen.disabled, p.els.autoGnssMode.disabled], [true, true], 'fixed for the run that is armed');
  // 준비 again while the run waits: the open run is ended, a new one made with the values on the screen
  p.els.autoPrepare.fire('click'); await p.done();
  assert.deepEqual(p.sent(), ['ack', 'start', 'arm', 'end', 'start', 'arm']);
  // SHADOW: start arms by itself, and the sentence says the vehicle will not move
  const q = await panel({ status: { run_state: 'ended', mode: 'SHADOW', stop_reason: 'ROW_END', stop_detail: 'row_end' }, mode: 'SHADOW' });
  assert.deepEqual(q.line(), ['ok', '멈춤: 열 끝 도착']);
  q.els.autoPrepare.fire('click'); await q.done();
  assert.deepEqual([q.sent(), q.line()[1]], [['start'], '출발할 수 있음 · SHADOW(차는 안 움직임)']);
  for (const id of ['autoRecordButton', 'autoRecordState', 'autoRecordRuns', 'autoNotice']) assert.ok(!p.asked.has(id) && !q.asked.has(id), `${id} is gone`);
});

test('panel: the start is on only once the corridor is acquired; 다시 출발 after a pause; 준비 off while driving', async () => {
  const p = await panel({ status: { run_state: 'armed', mode: 'MOTOR', go_ready: false, go_blockers: ['corridor_not_acquired'] } });
  assert.deepEqual([p.els.autoPrimary.disabled, p.line()], [true, ['testing', '통로 찾는 중']]);
  p.els.autoPrimary.fire('click'); await p.done();
  assert.deepEqual([p.sent(), p.log.recordings], [[], []], 'nothing is sent and nothing recorded before the corridor');
  p.state.status = { run_state: 'armed', mode: 'MOTOR', go_ready: false, go_blockers: ['operator_link_not_fresh'] };
  await p.poll();
  assert.deepEqual([p.els.autoPrimary.disabled, p.line()], [false, ['ok', '출발할 수 있음']], 'the operator link is what the press itself brings');
  p.state.status = { run_state: 'paused', mode: 'MOTOR', go_ready: true, go_blockers: [], stop_reason: 'OPERATOR_LINK_LOST', stop_detail: 'operator_link_lost' };
  await p.poll();
  assert.deepEqual([p.els.autoPrimary.disabled, p.els.autoPrimary.textContent, p.line()[1]], [false, '다시 출발', '일시정지: 화면 연결 끊김 — 다시 출발을 누르세요']);
  p.state.status = { run_state: 'running', mode: 'MOTOR', single_row: { distance_m: 12.34, speed_m_s: 0.38 }, operator_link: { fresh: true, timeout_s: 0.5 } };
  await p.poll();
  assert.deepEqual([p.els.autoPrepare.disabled, p.els.autoPrimary.disabled, p.els.autoPrimary.textContent], [true, true, '자율 주행 시작']);
  assert.deepEqual(p.line(), ['ok', '주행 중 · 12.3 m · 0.38 m/s']);
  assert.equal(p.els.autoHint.textContent, '화면을 가리면 0.5초 뒤 선다', "the executor's own time limit");
  assert.equal(p.els.autoRowLen.disabled, true);
  // a refused heartbeat shows in the sentence and goes with the next one that passes
  p.state.refuse.heartbeat = 'session_conflict';
  await p.beat(); p.screen.draw();
  assert.deepEqual(p.line(), ['bad', '연결 신호 거절: 다른 화면이 출발 권한을 가짐']);
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
  assert.deepEqual(recordTile(), ['기록', '기록 중', 'run 자동 기록(모든 센서)'], 'the recording shows as one tile');
  p.state.status = { run_state: 'paused' };
  await p.poll();
  assert.equal(p.log.recordings.length, 1, 'paused: the recording goes on');
  p.state.status = STOPPED;
  await p.poll();
  assert.deepEqual(p.log.recordings.at(-1), ['stop'], 'stopped (STOP latched the run): the auto recording stops');
  assert.equal(p.screen.autoRecording(), null);
  await p.poll();
  assert.equal(p.log.recordings.length, 2, 'once');
});

test('panel: no go when the recording cannot start; a hand-started recording is kept', async () => {
  const p = await panel();
  p.page.recording.start = async () => ({ ok: false, error: 'recorder_unavailable' });
  p.els.autoPrimary.fire('click'); await p.done();
  assert.ok(!p.sent().includes('go'), 'no go without the recording');
  assert.deepEqual(p.line(), ['bad', '기록을 시작하지 못해 출발하지 않음: recorder_unavailable']);
  const u = await panel({ rec: { known: false } });
  u.els.autoPrimary.fire('click'); await u.done();
  assert.ok(!u.sent().includes('go'), 'recorder state not known: no go');
  assert.match(u.line()[1], /^기록 상태를 몰라 출발하지 않음/);
  const q = await panel({ rec: { known: true, active: true, id: 'by-hand', sensors: RUN_RECORD_SENSORS } });
  q.els.autoPrimary.fire('click'); await q.done();
  assert.equal(q.log.recordings.length, 0, 'a running recording of every sensor is kept');
  assert.equal(q.sent().at(-1), 'go');
  q.state.status = { run_state: 'ended' };
  q.state.rec = { known: true, active: true, id: 'by-hand' };
  assert.equal(recordAfterRun(reply(q.state.status), q.screen.autoRecording(), q.state.rec), false, 'never stopped by the panel');
  // 11/20: a running recording without every sensor is replaced by one of every sensor before the go
  const r = await panel({ rec: { known: true, active: true, id: 'no-camera', sensors: ['lidar', 'imu', 'gps'] } });
  r.els.autoPrimary.fire('click'); await r.done();
  assert.deepEqual(r.log.recordings.slice(0, 2).map(([a]) => a), ['stop', 'start'], 'stopped, then every sensor');
  assert.equal(r.sent().at(-1), 'go');
  // a go the executor refuses: its reason in words
  const g = await panel();
  g.state.refuse.go = 'corridor_not_acquired';
  g.els.autoPrimary.fire('click'); await g.done();
  assert.deepEqual(g.line(), ['bad', '출발 거절: 통로를 아직 못 찾음']);
});

test('panel: 멈추기 always works and wins: no Arm after it during 준비, no go after it while the recording starts', async () => {
  // during 준비 (the start is on its way): nothing more of 준비 is sent, and the run it left open is stopped
  const p = await panel({ status: { run_state: 'idle', mode: 'MOTOR' } });
  p.state.onRequest = async (action) => { if (action === 'start') { p.state.onRequest = null; p.els.autoStopButton.fire('click'); } };
  p.els.autoPrepare.fire('click'); await p.done();
  assert.deepEqual(p.sent(), ['start', 'stop'], 'no Arm; the open run is stopped');
  assert.equal(p.state.status.run_state, 'latched');
  assert.deepEqual(p.line(), ['bad', '멈추기를 눌러 준비를 그만둠']);
  assert.equal(p.els.autoStopButton.disabled, false, '멈추기 is never switched off');
  // while the recording of a start is starting: the executor is stopped and the go is not sent
  const q = await panel();
  q.state.onRecord = async () => { q.els.autoStopButton.fire('click'); await q.settle(); };
  q.els.autoPrimary.fire('click'); await q.done();
  assert.deepEqual(q.sent(), ['stop'], 'the stop of the armed run; no go');
  assert.equal(q.line()[1], '멈추기를 눌러 출발하지 않음');
  await q.poll();
  assert.deepEqual(q.log.recordings.map(([a]) => a), ['start', 'stop'], 'the recording of the run that never left stops with it');
  // a plain 멈추기 on a run: the executor stop, and the reason stays in the sentence
  const r = await panel({ status: { run_state: 'running', mode: 'MOTOR' } });
  r.els.autoStopButton.fire('click'); await r.settle(); await r.poll();
  assert.deepEqual([r.sent(), r.line()], [['stop'], ['bad', '멈춤: 운영자 STOP — 차를 다시 놓고 준비']]);
  // no run: nothing for the executor (the page's own handler still sends the bridge stop)
  const i = await panel({ status: { run_state: 'idle', mode: 'MOTOR' } });
  i.els.autoStopButton.fire('click'); await i.settle();
  assert.deepEqual(i.sent(), []);
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
