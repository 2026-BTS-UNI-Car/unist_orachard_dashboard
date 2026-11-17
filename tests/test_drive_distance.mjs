// The distance move section of Rover Control (static/steer/drive-distance.mjs, 2026-11-17): the view (rows with the
// data grades and the October 2026 source, why the button is off), and the page binding with a small fake DOM and a
// fake steering controller: the button runs only while held (pointer up, blur and a hidden page release it), the lease
// chain goes to the server and comes back, STOP drops it, the tape feedback and the reset. The steering controller's
// hand-over (beginExternal / endExternal) is checked against the real rules too. No browser, no network.
// Run: node --test tests/test_drive_distance.mjs
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { distView, initDriveDistance, message } from '../static/steer/drive-distance.mjs';
import '../static/steer/steer-control-rules.js';

const R = globalThis.UbtsSteerRules;
const flush = () => new Promise((resolve) => setImmediate(resolve));

const TABLE = {
  version: '202610', terrains: [
    { key: 'road_paved', label: '도로·포장', day: '2026-10-03', note: '', duties: [
      { duty: 40, speed_m_s: 0.373, grade: '참고', reference: 'LiDAR', segments: 1 },
      { duty: 88, speed_m_s: 0.771, grade: '측정', reference: 'LiDAR', segments: 5 }] },
    { key: 'orchard_row_1010', label: '과수원 열 안', day: '2026-10-10', note: '', duties: [
      { duty: 90, speed_m_s: 0.706, grade: '측정', reference: 'LiDAR', segments: 12 }] }],
  limits: { distance_default_m: 1.0 },
};
const PLAN = {
  ok: true, problem: null, terrain: 'road_paved', duty: 40, throttle: 40, pwm: 102, distance_m: 1.0, drive_s: 2.897,
  expected_m: 1.0, min_distance_m: 0.4, max_distance_m: 3.0, factor: 1.0, grade: '모델',
  speed: { value: 0.373, grade: '참고', reference: 'LiDAR', segments: 1 }, lag: { value: 0.61, grade: '가정' },
  coast: { value: 0.147, grade: '모델' },
};
const rows = (view) => Object.fromEntries(view.rows);

test('view: rows with grades and source, the button off with its reason', () => {
  const base = { table: TABLE, corrections: {}, plan: PLAN, bridge: { refusal: null, max_pwm: 255 }, armed: true, fresh: true };
  let v = distView(base);
  assert.equal(v.go.enabled, true);
  assert.equal(rows(v)['계획 시간'], '2.90 s · duty 40 % (PWM 102)');
  assert.equal(rows(v)['예상 거리'], '1.00 m · 등급 모델');
  assert.match(rows(v)['속도'], /0\.373 m\/s \[참고, LiDAR, 구간 1\]/);
  assert.match(rows(v)['출발 지연'], /가정: 100 % 출발 값을 씀/);
  assert.equal(rows(v)['보정 배율'], '1.00 (줄자 기록 없음)');
  assert.match(rows(v)['자료'], /2026-10 기록, 조향 장치 전 차\(ESP8266\)\. 지금 차는 재지 않음/);
  v = distView({ ...base, corrections: { road_paved: { factor: 1.12, samples: 3 } } });
  assert.match(rows(v)['보정 배율'], /^1\.12 \(줄자 3번/);
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
    'steerDistMeters', 'steerDistGo', 'steerDistActual', 'steerDistFeedback', 'steerDistReset'];
  for (const id of ids) els[id] = new El(id, id === 'steerDistGo' ? 'button' : 'div');
  els.steerDistTerrain = new El('steerDistTerrain', 'select');
  els.steerDistDuty = new El('steerDistDuty', 'select');
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
    if (route.endsWith('/plan')) return { ok: true, plan: { ...PLAN, terrain: body.terrain, duty: body.duty }, bridge: { refusal: null, max_pwm: 255 } };
    if (route.endsWith('/start')) { state.move = { id: 7, state: 'driving', elapsed_s: 0, planned_s: 2.897, terrain: body.terrain }; return { ok: true, move: state.move, plan: PLAN }; }
    if (route.endsWith('/hold')) {
      if (state.finishOn === 'hold') return { ok: true, move: { ...state.move, state: 'closed', reason: 'planned', elapsed_s: 2.9, expected_m: 1, feedback_open: true }, chain: { lease: 'L1', ticket: 'T40', seq: 40 } };
      return { ok: true, move: state.move };
    }
    if (route.endsWith('/release')) return { ok: true, move: { ...state.move, state: 'closed', reason: 'released', elapsed_s: 0.5, feedback_open: false }, chain: { lease: 'L1', ticket: 'T9', seq: 9 } };
    if (route.endsWith('/feedback')) return { ok: true, sample: { model_m: 1.0, ratio: 1.1 }, corrections: { road_paved: { factor: 1.1, samples: 1 } } };
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

test('page: the terrain fills the duties with data only; inputs re-plan', async () => {
  const p = page();
  await p.settle();
  assert.deepEqual(p.els.steerDistTerrain.children.map((o) => o.value), ['road_paved', 'orchard_row_1010']);
  assert.deepEqual(p.els.steerDistDuty.children.map((o) => o.value), ['40', '88']);
  p.els.steerDistTerrain.value = 'orchard_row_1010';
  p.els.steerDistTerrain.fire('change');
  await p.settle();
  assert.deepEqual(p.els.steerDistDuty.children.map((o) => o.value), ['90']);
  const plans = p.log.posts.filter(([route]) => route.endsWith('/plan'));
  assert.deepEqual(plans.at(-1)[1], { terrain: 'orchard_row_1010', duty: 90, distance_m: 1 });
  assert.equal(p.els.steerDistGo.disabled, false);
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
  assert.match(p.els.steerDistStatus.textContent, /보정 배율 1\.1/);
  p.els.steerDistReset.fire('click');
  await p.settle();
  assert.deepEqual(p.log.posts.find(([route]) => route.endsWith('/reset'))[1], { terrain: 'road_paved' });
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
