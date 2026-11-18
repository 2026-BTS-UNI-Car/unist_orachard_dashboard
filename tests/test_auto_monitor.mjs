// The 자율주행 tab (2026-11-19): the GPS choice of a run (sent with 준비, the executor's refusal shown), the monitoring
// tiles and the centreline metrics (static/steer/auto-model.mjs), the run recording (every sensor before a go, stopped
// after the run only when this panel started it) and the executor picture of the run (static/steer/run-view.mjs).
// Fake page and elements, no browser, no request leaves the process. Run: node --test tests/test_auto_monitor.mjs
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {
  GNSS_MODES, GNSS_MODE_TEXT, RUN_RECORD_SENSORS, gnssTile, metricsView, monitorView, prepareRun, recordAfterRun, recordBeforeGo,
} from '../static/steer/auto-model.mjs';
import { bandCentre, point, polyline, pose, sceneBox, sceneFromState, sceneFromStatus } from '../static/steer/run-view.mjs';
import { trailStep } from '../static/steer/local-grid.mjs';

const now = 1_800_000_000_000;
const reply = (status) => ({ ok: true, configured: true, reachable: true, received_at: now / 1000, status });
const tile = (view, name) => view.tiles.find(([label]) => label === name);

test('GPS per run: four modes, sent with start only when chosen', async () => {
  assert.deepEqual(GNSS_MODES, ['off', 'log_only', 'geofence', 'weak_constraint']);
  assert.deepEqual(Object.values(GNSS_MODE_TEXT), ['GPS 안 씀', 'GPS 기록만', 'GPS 범위 감시', 'RTK 약한 보정']);
  for (const [gnssMode, body] of [[null, { row_len_m: 40 }], ['geofence', { row_len_m: 40, gnss_mode: 'geofence' }]]) {
    const sent = [];
    await prepareRun({ send: async (action, b) => { sent.push([action, b]); return { ok: true, reply: { status: { run_state: 'armed' } } }; },
      readStatus: async () => null, sleep: async () => {}, rowLen: 40, gnssMode });
    assert.deepEqual(sent[0], ['start', body]);
  }
});

test('GPS tile: the run mode, its state, a mismatch with the request, an executor without the block', () => {
  assert.deepEqual(gnssTile({}, 'log_only'), ['GPS 기록만', '실행기가 GPS 상태를 보내지 않음', '']);
  const run = { gnss: { mode: 'geofence', configured_mode: 'off', state: 'fix', hacc_m: 1.234, accepted: 12, rejected: 3 } };
  assert.deepEqual(gnssTile(run, 'geofence'), ['GPS 범위 감시', '확정 · ±1.23 m · 받음 12 · 거부 3', '']);
  const [, sub, level] = gnssTile(run, 'weak_constraint');
  assert.equal(level, 'warn');
  assert.match(sub, /^요청한 RTK 약한 보정과 다름/, 'an older executor that ignored the field shows it');
  assert.equal(gnssTile({ gnss: { mode: 'weak_constraint', rtk: { state: 'fixed' }, counts: { accepted: 5, jump: 2, quality: 1 } } })[1],
    'RTK FIXED · 받음 5 · 거부 3');
  assert.equal(gnssTile({ gnss: { mode: 'geofence', fence_tripped: true } })[2], 'bad');
  assert.equal(gnssTile({ gnss: { mode: 'new_mode', state: 'odd' } })[0], 'new_mode', 'unknown values are shown as sent');
});

test('monitor tiles: run state, errors, distance, speed, steering command against measured, duty, steering PWM, recording, GPS', () => {
  const status = { run_state: 'running', mode: 'MOTOR', T: 35, S: 87, steer: { meas: 70 },
    single_row: { lateral_error_m: -0.123, heading_error_deg: 2.34, distance_m: 12.34, end_remaining_m: 20.5, speed_m_s: 0.31 },
    speed: { target_mps: 0.35, limit_mps: 0.3, limit_reason: 'corridor' }, gnss: { mode: 'log_only', state: 'fix', hacc_m: 2 } };
  const bridge = { steering: { measured_mrad: 60, pwm: -142, pwm_peak: 180 }, steering_host: { target_mrad: 80 },
    drive: { left: { acked: 89 }, right: { acked: 89 }, max_pwm: 255 } };
  const view = monitorView(reply(status), bridge, { known: true, active: true, tag: 'autonomy', auto: true }, 'log_only', now);
  assert.deepEqual(view.tiles.map(([name]) => name), ['상태', '횡 오차', '방향 오차', '진행 거리', '속도', '조향 명령 / 측정', '구동 duty',
    '조향 모터 PWM', '기록', 'GPS']);
  assert.deepEqual(tile(view, '상태').slice(1, 4), ['주행 중', '', 'ok']);
  assert.equal(tile(view, '횡 오차')[1], '-0.12 m');
  assert.equal(tile(view, '진행 거리')[2], '남은 20.5 m');
  assert.equal(tile(view, '속도')[2], '목표 0.35 · 제한 0.30 (통로 신뢰도)');
  assert.equal(tile(view, '조향 명령 / 측정')[1], '왼 5.0° / 왼 4.0°', 'the executor numbers first (S, steer.meas)');
  assert.equal(tile(view, '구동 duty')[1], '35 %');
  assert.equal(tile(view, '구동 duty')[2], '보드 PWM 89 / 89 · 상한 255');
  assert.deepEqual(tile(view, '조향 모터 PWM').slice(1, 3), ['-142', '최근 최대 180']);
  assert.deepEqual(tile(view, '기록').slice(1, 4), ['기록 중', 'run 자동 기록(모든 센서)', 'ok']);
  // without the executor's steering numbers: the bridge's target and measurement
  const bare = monitorView(reply({ run_state: 'idle' }), bridge, null, '', now);
  assert.equal(tile(bare, '조향 명령 / 측정')[1], '왼 4.6° / 왼 3.4°');
  assert.equal(tile(bare, '기록'), undefined, 'no recorder view: no tile');
  // old or missing status
  assert.deepEqual(tile(monitorView(reply(status), null, null, '', now + 2000), '상태').slice(1, 4), ['끊김', '', 'bad']);
  assert.equal(tile(monitorView({ ok: false, configured: false }, null, null), '상태')[1], '실행기 없음');
  assert.equal(tile(monitorView(null, null, null), '상태')[1], '확인 중');
  assert.equal(tile(monitorView(reply({ run_state: 'ended', stop_reason: 'ROW_END' }), null, null, '', now), '상태')[2], '정상 열 끝');
  assert.equal(tile(monitorView(reply({}), null, { known: true, active: false }), '기록')[1], '기록 안 함');
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

// ---- the panel: GPS choice kept and sent, the refusal text, the run recording ----
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

async function panel({ status = { run_state: 'armed', mode: 'MOTOR', go_ready: true }, rec = { known: true, active: false } } = {}) {
  const ids = ['autoPanel', 'autoState', 'autoMode', 'autoPrimary', 'autoEnd', 'autoScale', 'autoScaleValue', 'autoScaleOffer',
    'autoScaleText', 'autoScaleAccept', 'autoScaleDismiss', 'autoNotice', 'autoDetails', 'autoRowLen', 'autoGnssMode', 'autoRecordRuns',
    'autoRecordButton', 'autoRecordState', 'autoMonitorGrid', 'autoMonitorState', 'autoMetricsBlock', 'autoMetricsGrid', 'autoMetricsSource',
    'roverTabAutoBadge', 'autoStopButton'];
  const els = Object.fromEntries(ids.map((id) => [id, element(id)]));
  const store = new Map([['ubts-auto-gnss-mode-v1', 'geofence']]);
  globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) };
  globalThis.document = { hidden: false, getElementById: (id) => els[id] || null, addEventListener() {}, createElement: () => element('') };
  const realSet = globalThis.setInterval;
  const intervals = [];
  globalThis.setInterval = (fn, ms) => { const id = realSet(() => {}, 1e6); timers.push(id); intervals.push([fn, ms]); return id; };
  const { initAutoPanel } = await import('../static/steer/auto-panel.mjs');
  const log = { actions: [], recordings: [] };
  const state = { status, rec, refuse: null };
  const page = {
    currentRoute: () => '/rover-control',
    getJson: async () => ({ ok: true, configured: true, reachable: true, received_at: Date.now() / 1000, status: state.status }),
    controlRequest: async (url, body) => {
      log.actions.push([url.split('/').pop(), body]);
      if (state.refuse && url.endsWith('/start')) { const err = new Error(`${url}: 409`); err.status = 409; err.body = { ok: false, error: state.refuse }; throw err; }
      if (url.endsWith('/start')) state.status = { run_state: 'armed', mode: 'SHADOW' };
      if (url.endsWith('/go')) state.status = { run_state: 'running', mode: 'MOTOR' };
      return { ok: true };
    },
    recording: {
      view: () => state.rec,
      start: async (options) => { log.recordings.push(['start', options]); state.rec = { known: true, active: true, id: 'rec-9' }; return { ok: true, recording_id: 'rec-9' }; },
      stop: async () => { log.recordings.push(['stop']); state.rec = { known: true, active: false }; return { ok: true }; },
    },
    driveStatus: () => null,
  };
  const screen = initAutoPanel(page);
  globalThis.setInterval = realSet;
  const settle = async () => { for (let i = 0; i < 6; i += 1) await new Promise((done) => setTimeout(done, 0)); };
  await settle();
  const poll = async () => { await intervals.find(([, ms]) => ms === 500)[0](); await settle(); };
  return { els, store, page, state, log, screen, settle, poll };
}

test('panel: the kept GPS choice goes with 준비; the executor refusal is shown in words', async () => {
  const p = await panel({ status: { run_state: 'idle', mode: 'MOTOR' } });
  assert.equal(p.els.autoGnssMode.value, 'geofence', 'the last choice of this browser');
  p.els.autoGnssMode.value = 'weak_constraint'; p.els.autoGnssMode.fire('change');
  assert.equal(p.store.get('ubts-auto-gnss-mode-v1'), 'weak_constraint');
  p.state.refuse = 'gnss_rtk_not_fixed';
  p.els.autoPrimary.fire('click'); await p.settle();
  assert.deepEqual(p.log.actions[0], ['start', { row_len_m: null, gnss_mode: 'weak_constraint' }]);
  assert.match(p.els.autoNotice.textContent, /^준비 거절: RTK FIXED 가 아님/);
  p.state.refuse = null;
  p.els.autoGnssMode.value = ''; p.els.autoGnssMode.fire('change');
  p.els.autoPrimary.fire('click'); await p.settle();
  assert.deepEqual(p.log.actions.find(([a], k) => a === 'start' && k > 0)[1], { row_len_m: null }, 'the executor setting: no field');
});

test('panel: a go starts a recording of every sensor first; the run end stops only that one', async () => {
  const p = await panel();
  await p.screen.draw();
  p.els.autoPrimary.fire('click'); await p.settle();
  assert.deepEqual(p.log.recordings[0], ['start', { sensors: RUN_RECORD_SENSORS, tag: 'autonomy' }]);
  assert.equal(p.log.actions.at(-1)[0], 'go', 'then the go');
  assert.equal(p.screen.autoRecording(), 'rec-9');
  p.state.status = { run_state: 'paused' };
  await p.poll();
  assert.equal(p.log.recordings.length, 1, 'paused: the recording goes on');
  p.state.status = { run_state: 'latched', stop_reason: 'OPERATOR_OR_BACKSTOP' };
  await p.poll();
  assert.deepEqual(p.log.recordings.at(-1), ['stop'], 'stopped (STOP latched the run): the auto recording stops');
  assert.equal(p.screen.autoRecording(), null);
  await p.poll();
  assert.equal(p.log.recordings.length, 2, 'once');
});

test('panel: no go when the recording cannot start (auto recording on); a hand-started recording is kept', async () => {
  const p = await panel();
  p.page.recording.start = async () => ({ ok: false, error: 'recorder_unavailable' });
  p.els.autoPrimary.fire('click'); await p.settle();
  assert.ok(!p.log.actions.some(([a]) => a === 'go'), 'no go without the recording');
  assert.match(p.els.autoNotice.textContent, /기록을 시작하지 못해 출발하지 않음: recorder_unavailable/);
  const q = await panel({ rec: { known: true, active: true, id: 'by-hand' } });
  q.els.autoPrimary.fire('click'); await q.settle();
  assert.equal(q.log.recordings.length, 0, 'a running recording is kept');
  assert.equal(q.log.actions.at(-1)[0], 'go');
  q.state.status = { run_state: 'ended' };
  q.state.rec = { known: true, active: true, id: 'by-hand' };
  assert.equal(recordAfterRun(reply(q.state.status), q.screen.autoRecording(), q.state.rec), false, 'never stopped by the panel');
  const r = await panel();
  r.els.autoRecordRuns.checked = false; r.els.autoRecordRuns.fire('change');
  r.els.autoPrimary.fire('click'); await r.settle();
  assert.equal(r.log.recordings.length, 0, 'switched off: no recording');
  assert.equal(r.log.actions.at(-1)[0], 'go');
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
