// The distance move section of Rover Control (static/steer/drive-distance.mjs, 2026-11-17): the view (rows with the
// data grades and the October 2026 source, why the button is off), and the page binding with a small fake DOM and a
// fake steering controller: the button runs only while held (pointer up, blur and a hidden page release it), the lease
// chain goes to the server and comes back, STOP drops it, the tape feedback and the reset. The steering controller's
// hand-over (beginExternal / endExternal) is checked against the real rules too. No browser, no network.
// Run: node --test tests/test_drive_distance.mjs
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { distView, initDriveDistance, message, speedAt, dutyRange, sliderRange, distanceLimit, tapeView, MAIN_ROWS } from '../static/steer/drive-distance.mjs';
import '../static/steer/steer-control-rules.js';

const R = globalThis.UbtsSteerRules;
const flush = () => new Promise((resolve) => setImmediate(resolve));

const TABLE = {
  version: '202610', terrains: [
    { key: 'road_paved', label: '도로·포장', day: '2026-10-03', note: '', duty_min: 40, duty_max: 88, default_duty: 40, duties: [
      { duty: 40, speed_m_s: 0.373, grade: '참고', reference: 'LiDAR', segments: 1 },
      { duty: 88, speed_m_s: 0.771, grade: '측정', reference: 'LiDAR', segments: 5 }] },
    { key: 'orchard_row_1010', label: '과수원 열 안', day: '2026-10-10', note: '', duty_min: 90, duty_max: 90, default_duty: 90, duties: [
      { duty: 90, speed_m_s: 0.706, grade: '측정', reference: 'LiDAR', segments: 12 }] }],
  limits: { distance_default_m: 1.0, distance_max_m: 3.0, distance_max_backward_m: 2.0 },
};
const NO_CORRECTION = { factor: 1.0, samples: 0, from_duty: null, exact: false };
const PLAN = {
  ok: true, problem: null, terrain: 'road_paved', duty: 40, throttle: 40, pwm: 102, distance_m: 1.0, drive_s: 2.897,
  expected_m: 1.0, min_distance_m: 0.4, max_distance_m: 3.0, factor: 1.0, grade: '모델', correction: NO_CORRECTION,
  duty_min: 40, duty_max: 88, default_duty: 40,
  speed: { value: 0.373, grade: '참고', reference: 'LiDAR', segments: 1, interpolated: false, between: null },
  lag: { value: 0.61, grade: '가정' }, coast: { value: 0.147, grade: '모델' },
};
const rows = (view) => Object.fromEntries(view.rows);

test('view: rows with grades and source, the button off with its reason', () => {
  const base = { table: TABLE, corrections: {}, plan: PLAN, bridge: { refusal: null, max_pwm: 255, max_duty: 100 }, armed: true, fresh: true };
  let v = distView(base);
  assert.equal(v.go.enabled, true);
  assert.equal(rows(v)['계획 시간'], '2.90 s · duty 40 % (PWM 102)');
  assert.equal(rows(v)['예상 거리'], '1.00 m · 등급 모델');
  assert.match(rows(v)['속도'], /0\.373 m\/s \[참고, LiDAR, 구간 1\]/);
  assert.match(rows(v)['출발 지연'], /가정: 100 % 출발 값을 씀/);
  assert.equal(rows(v)['보정 배율'], '1.00 (줄자 기록 없음)');
  assert.equal(rows(v)['줄자 기록 있는 속도'], undefined);
  assert.equal(rows(v)['표에 있는 duty'], '40 · 88 % (그 사이는 직선 보간)');
  assert.equal(rows(v)['구동 상한'], 'PWM 255 (duty 100 %까지)');
  assert.match(rows(v)['자료'], /2026-10 기록, 조향 장치 전 차\(ESP8266\)\. 지금 차는 재지 않음/);
  v = distView({ ...base, plan: { ...PLAN, factor: 1.12, correction: { factor: 1.12, samples: 3, from_duty: 40, exact: true } },
    corrections: { road_paved: { samples: 4, duties: [{ duty: 40, factor: 1.12, samples: 3 }, { duty: 88, factor: 0.95, samples: 1 }] } } });
  assert.equal(rows(v)['보정 배율'], '1.12 (줄자 3번)');
  assert.equal(rows(v)['줄자 기록 있는 속도'], '40 % ×1.12 (3번) · 88 % ×0.95 (1번)');
  assert.equal(distView({ ...base, armed: false }).go.title, message('distance_needs_arm'));
  assert.equal(distView({ ...base, fresh: false }).go.enabled, false);
  assert.match(distView({ ...base, plan: { ...PLAN, problem: 'distance_too_short_for_duty' } }).go.title, /너무 짧다/);
  v = distView({ ...base, bridge: { refusal: 'distance_drive_cap_zero', max_pwm: 0 } });
  assert.equal(v.go.enabled, false);
  assert.match(v.go.title, /구동 상한 0/);
  assert.match(rows(v)['구동 상한'], /PWM 0/);
  assert.equal(distView({ ...base, table: null }).go.enabled, false);
  v = distView({ ...base, move: { state: 'driving', elapsed_s: 1.2, planned_s: 2.897 } });
  assert.equal(v.go.enabled, false);
  assert.match(v.status.text, /거리 이동 중 · 1\.2 \/ 2\.90 s/);
  v = distView({ ...base, result: { state: 'closed', reason: 'planned', planned_s: 2.897, elapsed_s: 2.91, expected_m: 1.0, feedback_open: true } });
  assert.equal(v.feedback.enabled, true);
  assert.match(v.status.text, /계획 시간 끝/);
  v = distView({ ...base, result: { state: 'closed', reason: 'released', planned_s: 2.9, elapsed_s: 0.8, expected_m: 1.0, feedback_open: false } });
  assert.equal(v.feedback.enabled, false);
  assert.equal(v.status.kind, 'warn');
});

test('view: the speed readout, an interpolated speed and a borrowed correction', () => {
  const base = { table: TABLE, corrections: {}, plan: PLAN, bridge: { refusal: null, max_pwm: 120, max_duty: 47 }, armed: true, fresh: true };
  assert.equal(distView({ ...base, input: { terrain: 'road_paved', duty: 40 } }).speed, '40 % · 0.37 m/s · 2.90 s');
  // the slider moved: the table speed at once (as the server interpolates), the time once the plan is for this duty
  assert.equal(distView({ ...base, input: { terrain: 'road_paved', duty: 64 } }).speed, '64 % · 0.57 m/s · 계산 중');
  assert.equal(distView({ ...base, input: { terrain: 'road_paved', duty: 64 }, planError: 'http_failed' }).speed, '64 % · 0.57 m/s · --');
  assert.equal(distView({ ...base, input: { terrain: 'road_paved', duty: 40 }, plan: { ...PLAN, problem: 'distance_too_short_for_duty' } }).speed,
    '40 % · 0.37 m/s · 이 거리 안 됨');
  assert.equal(distView({ ...base }).speed, '--');
  assert.equal(rows(distView(base))['구동 상한'], 'PWM 120 (duty 47 %까지)');
  const plan = { ...PLAN, duty: 64, pwm: 163, drive_s: 2.1, factor: 1.12, grade: '가정',
    speed: { value: 0.572, grade: '모델', reference: 'LiDAR', segments: null, interpolated: true, between: [40, 88] },
    correction: { factor: 1.12, samples: 3, from_duty: 40, exact: false } };
  const v = distView({ ...base, plan, input: { terrain: 'road_paved', duty: 64 } });
  assert.equal(v.speed, '64 % · 0.57 m/s · 2.10 s');
  assert.equal(rows(v)['속도'], '0.572 m/s [모델: 표의 40 %·88 % 사이 직선 보간, LiDAR]');
  assert.equal(rows(v)['보정 배율'], '1.12 (40 %의 배율을 빌림)', 'a duty without its own tape: the nearest measured one, named');
  assert.match(rows(v)['이 속도로 갈 수 있는 거리'], /m$/);
});

test('speedAt and dutyRange follow the server (straight line between rows, nothing outside)', () => {
  const road = TABLE.terrains[0];
  assert.equal(speedAt(road, 40), 0.373);
  assert.equal(speedAt(road, 88), 0.771);
  assert.ok(Math.abs(speedAt(road, 64) - (0.373 + (0.771 - 0.373) * 24 / 48)) < 1e-12);
  assert.equal(speedAt(road, 39), null);
  assert.equal(speedAt(road, 89), null);
  assert.deepEqual(dutyRange(road), { min: 40, max: 88, def: 40 });
  assert.deepEqual(dutyRange({ duties: [{ duty: 58 }, { duty: 45 }] }), { min: 45, max: 58, def: 45 }, 'a view without the range fields');
});

class El {
  constructor(id = null, tag = 'div') {
    Object.assign(this, { id, tagName: tag.toUpperCase(), textContent: '', className: '', disabled: false, hidden: false,
      title: '', value: '', open: false, dataset: {}, children: [], listeners: {}, captured: new Set(),
      classList: { toggle: () => {} } });
  }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  fire(name, event = {}) { for (const fn of this.listeners[name] || []) fn({ preventDefault() {}, ...event }); }
  replaceChildren(...items) { this.children = items; if (this.tagName === 'SELECT' && items.length && !items.some((o) => o.value === this.value)) this.value = items[0].value; }
  append(...items) { this.children.push(...items); }
  setAttribute(name, value) { this[name] = value; }
  querySelectorAll(selector) { return selector === '[data-dist-direction]' ? this.directionButtons || [] : []; }
  setPointerCapture(id) { this.captured.add(id); }
  releasePointerCapture(id) { this.captured.delete(id); }
}

function fakeController() {
  return {
    lease: 'L1', ticket: 'T5', seq: 5, external: false, abandoned: null, ended: [],
    fresh: () => true,
    beginExternal() { if (!this.lease || this.external) return null; this.external = true; return { lease: this.lease, ticket: this.ticket, seq: this.seq, steer_mrad: 12 }; },
    endExternal(chain, reason) { this.ended.push([chain, reason]); this.external = false; if (chain) { this.ticket = chain.ticket; this.seq = chain.seq; } else { this.lease = null; } return Boolean(chain); },
    abandon(reason) { this.abandoned = reason; this.lease = null; this.external = false; },
  };
}

const opened = [];
after(() => { for (const close of opened) close(); });

function page() {
  const els = {};
  const ids = ['steerDistPanel', 'steerDistDetails', 'steerDistState', 'steerDistRows', 'steerDistStatus', 'steerDistStop',
    'steerDistMeters', 'steerDistGo', 'steerDistActual', 'steerDistFeedback', 'steerDistReset', 'steerDistSpeed',
    // 2026-11-19: the folded plan details, the direction, and the tape part of the 보정·설정 tab
    'steerDistMore', 'steerDistMetersLabel', 'steerDistToTape', 'steerDistTapePanel', 'steerDistTapeState', 'steerDistLast',
    'steerDistTapeRows', 'steerDistTapeStatus'];
  for (const id of ids) els[id] = new El(id, id === 'steerDistGo' ? 'button' : 'div');
  els.steerDistTapeTerrain = new El('steerDistTapeTerrain', 'select');   // 2026-11-20: the tape part's own terrain choice
  els.steerDistPanel.directionButtons = ['forward', 'backward'].map((way) => {
    const button = new El(null, 'button'); button.dataset.distDirection = way; return button;
  });
  els.steerDistTerrain = new El('steerDistTerrain', 'select');
  els.steerDistDuty = new El('steerDistDuty', 'input');               // <input type="range">
  els.steerDistDuty.value = '40';
  els.steerDistMeters.value = '1.0';
  els.steerDistDetails.open = true;
  const docListeners = {};
  const doc = { hidden: false, getElementById: (id) => els[id] || null, createElement: (tag) => new El(null, tag),
    addEventListener: (name, fn) => { (docListeners[name] ||= []).push(fn); } };
  const ctl = fakeController();
  const log = { posts: [] };
  const state = { move: null, finishOn: null };
  const timers = [];
  const answer = (route, body) => {
    if (route.endsWith('/plan')) return { ok: true, plan: { ...PLAN, terrain: body.terrain, duty: body.duty, direction: body.direction || 'forward' }, bridge: state.bridge || { refusal: null, max_pwm: 255 } };
    if (route.endsWith('/start')) { state.move = { id: 7, state: 'driving', elapsed_s: 0, planned_s: 2.897, terrain: body.terrain }; return { ok: true, move: state.move, plan: PLAN }; }
    if (route.endsWith('/hold')) {
      if (state.finishOn === 'hold') return { ok: true, move: { ...state.move, state: 'closed', reason: 'planned', elapsed_s: 2.9, expected_m: 1, feedback_open: true }, chain: { lease: 'L1', ticket: 'T40', seq: 40 } };
      return { ok: true, move: state.move };
    }
    if (route.endsWith('/release')) return { ok: true, move: { ...state.move, state: 'closed', reason: 'released', elapsed_s: 0.5, feedback_open: false }, chain: { lease: 'L1', ticket: 'T9', seq: 9 } };
    if (route.endsWith('/feedback')) return { ok: true, sample: { model_m: 1.0, ratio: 1.1, duty: 40 }, corrections: { road_paved: { samples: 1, duties: [{ duty: 40, factor: 1.1, samples: 1 }] } }, correction: { factor: 1.1, samples: 1, from_duty: 40, exact: true } };
    if (route.endsWith('/reset')) return { ok: true, corrections: {} };
    return { ok: true };
  };
  const api = {
    currentRoute: () => '/rover-control',
    getJson: async () => ({ ok: true, table: TABLE, corrections: {}, move: null }),
    postJson: async (route, body) => { log.posts.push([route, body]); return answer(route, body); },
    controlRequest: async (route, body) => { log.posts.push([route, body]); return answer(route, body); },
    driveController: () => ctl,
  };
  const win = new EventTarget();
  const screen = initDriveDistance(api, {
    document: doc, window: win, planDebounceMs: 0, confirm: () => true,
    setInterval: (fn, ms) => { timers.push({ fn, ms, on: true }); return timers.length - 1; },
    clearInterval: (id) => { if (timers[id]) timers[id].on = false; },
  });
  const holdTimers = () => timers.filter((t) => t.on && t.ms === 100);
  const tickHold = async () => { for (const t of holdTimers()) await t.fn(); await flush(); };
  const routes = () => log.posts.map(([route]) => route.split('/').pop());
  const settle = async () => { for (let i = 0; i < 5; i += 1) { await flush(); await new Promise((r) => setTimeout(r, 2)); } };
  opened.push(() => screen.stop());
  return { els, doc, win, docListeners, ctl, log, state, screen, tickHold, holdTimers, routes, settle };
}

test('page: the speed slider spans the terrain table, starts at its lowest duty; inputs re-plan', async () => {
  const p = page();
  await p.settle();
  const slider = () => [p.els.steerDistDuty.min, p.els.steerDistDuty.max, p.els.steerDistDuty.step, p.els.steerDistDuty.value];
  const lastPlan = () => p.log.posts.filter(([route]) => route.endsWith('/plan')).at(-1)[1];
  assert.deepEqual(p.els.steerDistTerrain.children.map((o) => o.value), ['road_paved', 'orchard_row_1010']);
  assert.deepEqual(slider(), ['40', '88', '1', '40']);
  assert.deepEqual(lastPlan(), { terrain: 'road_paved', duty: 40, distance_m: 1 }, 'the default speed is the 11-17 default');
  assert.equal(p.els.steerDistSpeed.textContent, '40 % · 0.37 m/s · 2.90 s');
  p.els.steerDistTerrain.value = 'orchard_row_1010';
  p.els.steerDistTerrain.fire('change');
  await p.settle();
  assert.deepEqual(slider(), ['90', '90', '1', '90']);
  assert.deepEqual(lastPlan(), { terrain: 'orchard_row_1010', duty: 90, distance_m: 1 });
  assert.equal(p.els.steerDistGo.disabled, false);
  p.els.steerDistTerrain.value = 'road_paved';
  p.els.steerDistTerrain.fire('change');
  await p.settle();
  assert.equal(p.els.steerDistDuty.value, '40', 'not touched: the terrain default again');
  p.els.steerDistDuty.value = '64';
  p.els.steerDistDuty.fire('input');
  assert.equal(p.els.steerDistSpeed.textContent, '64 % · 0.57 m/s · 계산 중', 'the readout follows the slider at once');
  await p.settle();
  assert.deepEqual(lastPlan(), { terrain: 'road_paved', duty: 64, distance_m: 1 });
  assert.equal(p.els.steerDistSpeed.textContent, '64 % · 0.57 m/s · 2.90 s');
  p.els.steerDistGo.fire('pointerdown', { pointerId: 4 });
  await p.settle();
  assert.equal(p.log.posts.find(([route]) => route.endsWith('/start'))[1].duty, 64, 'the chosen speed goes with the start');
  p.els.steerDistGo.fire('pointerup', { pointerId: 4 });
  await p.settle();
  p.els.steerDistTerrain.value = 'orchard_row_1010';
  p.els.steerDistTerrain.fire('change');
  await p.settle();
  assert.equal(p.els.steerDistDuty.value, '90', '64 % is outside 90..90: the terrain default');
});

test('page: held -> start with the lease chain, heartbeats, release stops and the chain comes back', async () => {
  const p = page();
  await p.settle();
  p.els.steerDistGo.fire('pointerdown', { pointerId: 3 });
  await p.settle();
  assert.equal(p.ctl.external, true);
  const start = p.log.posts.find(([route]) => route.endsWith('/start'))[1];
  assert.deepEqual(start, { terrain: 'road_paved', duty: 40, distance_m: 1, lease: 'L1', ticket: 'T5', seq: 5, steer_mrad: 12 });
  assert.equal(p.holdTimers().length, 1);
  await p.tickHold();
  await p.tickHold();
  assert.deepEqual(p.routes().filter((r) => r === 'hold').length, 2);
  p.els.steerDistGo.fire('pointerup', { pointerId: 3 });
  await p.settle();
  assert.ok(p.routes().includes('release'), 'the release goes at once');
  assert.deepEqual(p.ctl.ended.at(-1), [{ lease: 'L1', ticket: 'T9', seq: 9 }, undefined], 'the chain is taken back');
  assert.equal(p.ctl.external, false);
  assert.equal(p.holdTimers().length, 0, 'no more heartbeats');
  assert.match(p.els.steerDistStatus.textContent, /버튼을 놓음/);
});

test('page: blur and a hidden page release a held move; a finished move hands back the chain while still held', async () => {
  for (const how of ['blur', 'hidden']) {
    const p = page();
    await p.settle();
    p.els.steerDistGo.fire('pointerdown', { pointerId: 1 });
    await p.settle();
    if (how === 'blur') p.win.dispatchEvent(new Event('blur'));
    else { p.doc.hidden = true; for (const fn of p.docListeners.visibilitychange || []) fn(); }
    await p.settle();
    assert.ok(p.routes().includes('release'), how);
    assert.equal(p.ctl.external, false, how);
  }
  const p = page();
  await p.settle();
  p.els.steerDistGo.fire('pointerdown', { pointerId: 2 });
  await p.settle();
  p.state.finishOn = 'hold';
  await p.tickHold();
  await p.settle();
  assert.deepEqual(p.ctl.ended.at(-1)[0], { lease: 'L1', ticket: 'T40', seq: 40 });
  assert.equal(p.els.steerDistFeedback.disabled, false, 'a full move can be measured with the tape');
  p.els.steerDistGo.fire('pointerup', { pointerId: 2 });
  await p.settle();
  assert.ok(!p.routes().includes('release'), 'nothing left to release');
  p.els.steerDistActual.value = '1.1';
  p.els.steerDistFeedback.fire('click');
  await p.settle();
  const feedback = p.log.posts.find(([route]) => route.endsWith('/feedback'))[1];
  assert.deepEqual(feedback, { move_id: 7, actual_m: 1.1 });
  assert.equal(p.els.steerDistTapeStatus.textContent, '저장: 도로·포장 앞으로 · 실제 1.1 m / 모델 1 m · 배율 1.10 (줄자 1번)',
    'the answer shows in the tape part (보정·설정) and names the terrain it was filed under');
  assert.equal(p.els.steerDistTapeStatus.hidden, false);
  p.els.steerDistReset.fire('click');
  await p.settle();
  assert.deepEqual(p.log.posts.find(([route]) => route.endsWith('/reset'))[1], { terrain: 'road_paved', direction: 'forward' },
    'the reset of the chosen direction');
});

test('page: STOP drops the lease and ends the heartbeats', async () => {
  const p = page();
  await p.settle();
  p.els.steerDistGo.fire('pointerdown', { pointerId: 5 });
  await p.settle();
  p.els.steerDistStop.fire('click');
  await p.settle();
  assert.equal(p.ctl.abandoned, 'operator_stop');
  assert.ok(p.routes().includes('stop'));
  assert.equal(p.holdTimers().length, 0);
});

test('page: not armed -> the button stays off and nothing is sent', async () => {
  const p = page();
  p.ctl.lease = null;
  await p.settle();
  p.els.steerDistGo.fire('pointerdown', { pointerId: 1 });
  await p.settle();
  assert.ok(!p.routes().includes('start'));
  assert.equal(p.els.steerDistGo.disabled, true);
  assert.match(p.els.steerDistGo.title, /조종 활성화/);
});

test('rules: the controller hands its chain over and takes it back; nothing is sent meanwhile', async () => {
  let t = 1000;
  const sent = [];
  const status = { ok: true, armed: true, vehicle_profile: 'front_steer', steer_limit_mrad: { left: 349, right: 349 },
    steer_control: { kind: 'esp32_abs_hold', keys: 'full_limit' }, outputs: { D8: 0, D5: 0, D3: 0, D0: 0, D1: 0, D2: 0 },
    steering: { measured_mrad: 20, age_ms: 20, age_limit_ms: 300, seq: 1, faults: [] }, steering_host: { stale: false } };
  const ctl = new R.SteerController({ now: () => t, sleep: async () => {}, transport: {
    status: async () => status,
    post: async (route, body) => { sent.push(route); return { ok: true, lease: 'L', ticket: `T${sent.length}`, seq: (body.seq || 0), status }; } } });
  await ctl.pollStatus();
  await ctl.arm();
  await flush();
  const chain = ctl.beginExternal();
  assert.deepEqual(Object.keys(chain), ['lease', 'ticket', 'seq', 'steer_mrad']);
  assert.equal(chain.steer_mrad, 20, 'the steering target where it is');
  const before = sent.length;
  ctl.keyDown('w'); ctl.keyDown('a');
  for (let i = 0; i < 10; i += 1) { t += 100; await ctl.pollStatus(); ctl.tick(); await flush(); }
  assert.equal(sent.length, before, 'no drive or keepalive while the server holds the chain');
  assert.equal(ctl.keys.w, false, 'keys are ignored meanwhile');
  assert.equal(ctl.endExternal({ lease: 'L', ticket: 'T99', seq: 99 }), true);
  assert.deepEqual([ctl.ticket, ctl.seq], ['T99', 99]);
  t += 600; ctl.tick(); await flush();
  assert.ok(sent.length > before, 'keepalives again');
  ctl.beginExternal();
  assert.equal(ctl.endExternal(null, 'distance_move_lost'), false);
  assert.equal(ctl.lease, null, 'a lost chain drops the lease locally');
});

// ---- 2026-11-19: backward, the bridge cap on the slider, the folded plan rows and the tape part ----
test('view: backward plans, its limits and its own corrections; the rows under the controls', () => {
  assert.equal(distanceLimit(TABLE, 'forward'), 3.0);
  assert.equal(distanceLimit(TABLE, 'backward'), 2.0);
  assert.equal(distanceLimit(null, 'backward'), 2.0, 'without a table view: 2 m');
  const base = { table: TABLE, corrections: { road_paved: { duties: [{ duty: 40, factor: 1.2, samples: 3 }],
    backward_duties: [{ duty: 40, factor: 0.9, samples: 1 }] } }, bridge: { refusal: null, max_pwm: 255, max_duty: 100 }, armed: true, fresh: true };
  let v = distView({ ...base, plan: PLAN });
  assert.equal(v.go.text, '앞으로 이동 (누르고 있기)');
  assert.deepEqual(v.main.map(([name]) => name), MAIN_ROWS);
  assert.ok(v.more.every(([name]) => !MAIN_ROWS.includes(name)) && v.more.length + v.main.length === v.rows.length);
  assert.equal(Object.fromEntries(v.rows)['줄자 기록 있는 속도'], '40 % ×1.20 (3번)');
  // the direction moved and the plan is still the forward one: no go until the backward plan is back
  v = distView({ ...base, plan: PLAN, direction: 'backward' });
  assert.equal(v.go.enabled, false);
  assert.equal(v.go.text, '뒤로 이동 (누르고 있기)');
  const back = { ...PLAN, direction: 'backward', throttle: -40, grade: '가정',
    correction: { factor: 0.9, samples: 1, from_duty: 40, exact: true, direction: 'backward' } };
  v = distView({ ...base, plan: back, direction: 'backward' });
  assert.equal(v.go.enabled, true);
  const r = Object.fromEntries(v.rows);
  assert.match(r['예상 거리'], /뒤로$/);
  assert.equal(r['보정 배율'], '0.90 (뒤로 줄자 1번)');
  assert.equal(r['줄자 기록 있는 속도'], '40 % ×0.90 (1번)', 'the backward corrections');
  assert.match(r['자료'], /뒤로는 앞으로 잰 표를 그대로 씀/);
  assert.match(distView({ ...base, plan: { ...back, problem: 'distance_out_of_range_backward' }, direction: 'backward' }).go.title, /뒤로는 0\.2~2\.0 m/);
  assert.match(distView({ ...base, plan: { ...back, correction: { factor: 1, samples: 0, from_duty: null } }, direction: 'backward' }).rows
    .find(([name]) => name === '보정 배율')[1], /^1\.00 \(뒤로 줄자 기록 없음\)$/);
});

test('sliderRange: the terrain table range, its top cut at the bridge cap', () => {
  const road = TABLE.terrains[0];
  assert.deepEqual(sliderRange(road, null), { min: 40, max: 88, def: 40 });
  assert.deepEqual(sliderRange(road, { max_duty: 100 }), { min: 40, max: 88, def: 40 }, 'cap 255 (100 %): the table');
  assert.deepEqual(sliderRange(road, { max_duty: 47 }), { min: 40, max: 47, def: 40 }, 'cap 120');
  assert.deepEqual(sliderRange(road, { max_duty: 20 }), { min: 40, max: 40, def: 40 }, 'below the table: the lowest (the plan refuses)');
});

test('tapeView: the last move with its terrain, both directions of the chosen terrain, a tape only for a move of that terrain', () => {
  const s = { table: TABLE, input: { terrain: 'road_paved' },
    corrections: { road_paved: { duties: [{ duty: 40, factor: 1.2, samples: 3 }], backward_duties: [] },
      orchard_row_1010: { duties: [], backward_duties: [{ duty: 90, factor: 0.9, samples: 2 }] } },
    result: { id: 3, terrain: 'road_paved', direction: 'backward', distance_m: 1.5, duty: 40, reason: 'planned', feedback_open: true } };
  let v = tapeView(s);
  assert.equal(v.last, '마지막 이동: 도로·포장 · 뒤로 1.5 m · 40 % · 계획 시간 끝');
  assert.deepEqual(v.rows, [['앞으로 줄자 배율', '40 % ×1.20 (3번)'], ['뒤로 줄자 배율', '기록 없음']], 'the terrain is the choice above the rows, not a row');
  assert.deepEqual([v.feedback.enabled, v.chip, v.note], [true, ['ok', '입력 가능'], null]);
  // another terrain chosen: its corrections are shown; the tape of the road move cannot be filed under it
  v = tapeView({ ...s, input: { terrain: 'orchard_row_1010' } });
  assert.deepEqual(v.rows, [['앞으로 줄자 배율', '기록 없음'], ['뒤로 줄자 배율', '90 % ×0.90 (2번)']]);
  assert.deepEqual([v.feedback.enabled, v.chip], [false, ['warn', '지형 다름']]);
  assert.equal(v.note, '마지막 이동은 도로·포장에서 한 것: 그 지형을 골라야 저장된다');
  assert.equal(v.feedback.title, v.note);
  v = tapeView({ ...s, result: { ...s.result, feedback_open: false } });
  assert.deepEqual([v.feedback.enabled, v.note], [false, null]);
  v = tapeView({ ...s, input: { terrain: 'orchard_row_1010' }, result: { ...s.result, feedback_open: false } });
  assert.equal(v.note, null, 'nothing to save: nothing to explain');
  v = tapeView({ ...s, result: { ...s.result, terrain: undefined } });
  assert.deepEqual([v.feedback.enabled, v.last], [true, '마지막 이동: 뒤로 1.5 m · 40 % · 계획 시간 끝'], 'a server reply without the terrain: as before');
  assert.equal(tapeView({ table: TABLE, input: { terrain: 'road_paved' } }).last, '이 화면에서 끝난 이동 없음');
});

// 2026-11-20, user: "보정/설정 탭의 거리 이동 줄자 보정에서 도로/포장 옵션만 있는데, 그 옵션을 바꿀 수 있게".
test('page: the tape part has its own terrain choice, one value with the 수동 조종 choice; reset and tape follow it', async () => {
  const p = page();
  await p.settle();
  const drive = p.els.steerDistTerrain, tape = p.els.steerDistTapeTerrain;
  const lastPlan = () => p.log.posts.filter(([route]) => route.endsWith('/plan')).at(-1)[1];
  const options = (select) => select.children.map((o) => [o.value, o.textContent]);
  assert.deepEqual(options(tape), [['road_paved', '도로·포장 (10/03)'], ['orchard_row_1010', '과수원 열 안 (10/10)']], 'every terrain of the table');
  assert.deepEqual(options(tape), options(drive));
  assert.deepEqual([drive.value, tape.value], ['road_paved', 'road_paved']);
  // chosen in the tape part: the 수동 조종 choice, the speed range and the plan follow
  tape.value = 'orchard_row_1010';
  tape.fire('change');
  await p.settle();
  assert.equal(drive.value, 'orchard_row_1010', 'one changes the other');
  assert.deepEqual([p.els.steerDistDuty.min, p.els.steerDistDuty.max], ['90', '90']);
  assert.deepEqual(lastPlan(), { terrain: 'orchard_row_1010', duty: 90, distance_m: 1 });
  // the reset clears the chosen terrain
  p.els.steerDistReset.fire('click');
  await p.settle();
  assert.deepEqual(p.log.posts.filter(([route]) => route.endsWith('/reset')).at(-1)[1], { terrain: 'orchard_row_1010', direction: 'forward' });
  assert.equal(p.els.steerDistTapeStatus.textContent, '과수원 열 안 앞으로 보정 초기화: 배율 1.00');
  // chosen in 수동 조종: the tape part follows
  drive.value = 'road_paved';
  drive.fire('change');
  await p.settle();
  assert.equal(tape.value, 'road_paved');
  assert.equal(p.els.steerDistTapeStatus.hidden, true, 'the answer of the other terrain is not kept');
  // a full move on the road, then its tape
  p.els.steerDistGo.fire('pointerdown', { pointerId: 2 });
  await p.settle();
  assert.equal(p.log.posts.find(([route]) => route.endsWith('/start'))[1].terrain, 'road_paved');
  p.state.finishOn = 'hold';
  await p.tickHold();
  await p.settle();
  p.els.steerDistGo.fire('pointerup', { pointerId: 2 });
  await p.settle();
  assert.equal(p.els.steerDistFeedback.disabled, false);
  assert.match(p.els.steerDistLast.textContent, /^마지막 이동: 도로·포장 · /);
  // another terrain chosen meanwhile: the tape of the road move is not filed under it (the button is off and says why)
  tape.value = 'orchard_row_1010';
  tape.fire('change');
  await p.settle();
  assert.deepEqual([p.els.steerDistFeedback.disabled, p.els.steerDistTapeState.textContent], [true, '지형 다름']);
  assert.equal(p.els.steerDistTapeStatus.textContent, '마지막 이동은 도로·포장에서 한 것: 그 지형을 골라야 저장된다');
  p.els.steerDistActual.value = '1.1';
  p.els.steerDistFeedback.fire('click');
  await p.settle();
  assert.ok(!p.routes().includes('feedback'), 'nothing is sent for the other terrain');
  // back on the terrain of the move: saved, and the corrections of that terrain show
  tape.value = 'road_paved';
  tape.fire('change');
  await p.settle();
  assert.equal(p.els.steerDistFeedback.disabled, false);
  p.els.steerDistFeedback.fire('click');
  await p.settle();
  assert.deepEqual(p.log.posts.find(([route]) => route.endsWith('/feedback'))[1], { move_id: 7, actual_m: 1.1 });
  assert.match(p.els.steerDistTapeStatus.textContent, /^저장: 도로·포장 앞으로 · 실제 1\.1 m/);
  const tapeRows = () => p.els.steerDistTapeRows.children.map((row) => row.children.map((c) => c.textContent));
  assert.deepEqual(tapeRows(), [['앞으로 줄자 배율', '40 % ×1.10 (1번)'], ['뒤로 줄자 배율', '기록 없음']]);
  tape.value = 'orchard_row_1010';
  tape.fire('change');
  await p.settle();
  assert.deepEqual(tapeRows(), [['앞으로 줄자 배율', '기록 없음'], ['뒤로 줄자 배율', '기록 없음']], 'the rows are those of the chosen terrain');
});

test('page: backward goes with plan and start, the distance follows its limit, the slider follows the bridge cap', async () => {
  const p = page();
  p.state.bridge = { refusal: null, max_pwm: 120, max_duty: 47 };
  await p.settle();
  assert.deepEqual([p.els.steerDistDuty.min, p.els.steerDistDuty.max], ['40', '47'], 'the bridge cap cuts the slider');
  assert.equal(p.els.steerDistGo.textContent, '앞으로 이동 (누르고 있기)');
  assert.equal(p.els.steerDistMetersLabel.textContent, '거리 m (0.2~3.0)');
  p.els.steerDistMeters.value = '2.5';
  const [forward, backward] = p.els.steerDistPanel.directionButtons;
  backward.fire('click');
  await p.settle();
  assert.equal(p.els.steerDistMeters.value, '2', 'cut to the 2 m of a backward move');
  assert.equal(p.els.steerDistMeters.max, '2');
  assert.equal(p.els.steerDistMetersLabel.textContent, '거리 m (0.2~2.0, 뒤로)');
  assert.equal(p.els.steerDistGo.textContent, '뒤로 이동 (누르고 있기)');
  assert.equal(backward['aria-pressed'], 'true');
  const lastPlan = () => p.log.posts.filter(([route]) => route.endsWith('/plan')).at(-1)[1];
  assert.deepEqual(lastPlan(), { terrain: 'road_paved', duty: 40, distance_m: 2, direction: 'backward' });
  p.els.steerDistGo.fire('pointerdown', { pointerId: 8 });
  await p.settle();
  const start = p.log.posts.find(([route]) => route.endsWith('/start'))[1];
  assert.equal(start.direction, 'backward', 'the direction goes with the start');
  forward.fire('click');
  assert.equal(p.log.posts.filter(([route]) => route.endsWith('/plan')).at(-1)[1].direction, 'backward', 'no direction change while held');
  // leaving the tab while held lets go
  p.win.dispatchEvent(new Event('ubts:rover-tab'));
  await p.settle();
  assert.ok(p.routes().includes('release'));
  forward.fire('click');
  await p.settle();
  assert.deepEqual(lastPlan(), { terrain: 'road_paved', duty: 40, distance_m: 2 }, 'forward again: the old body');
  p.state.bridge = { refusal: null, max_pwm: 255, max_duty: 100 };
  p.els.steerDistMeters.fire('input');
  await p.settle();
  assert.equal(p.els.steerDistDuty.max, '88', 'the cap raised to 100 %: the whole table');
});
