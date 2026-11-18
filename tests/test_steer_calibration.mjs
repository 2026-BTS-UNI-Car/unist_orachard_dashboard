// The steering calibration section of Rover Control (static/steer/steer-calibration.mjs, 2026-11-16): the view of a
// status 'calibration' block (rows, chip, which buttons are usable and why), the apply proposal with the margins, and
// the page binding with a small fake DOM: confirm before every action that moves the wheels or changes the stored
// marks, the request bodies, STOP, a hidden page stopping a running scan. No browser, no network.
// Run: node --test tests/test_steer_calibration.mjs
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ACTIONS, calView, initSteerCalibration, proposal } from '../static/steer/steer-calibration.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const flush = () => new Promise((resolve) => setImmediate(resolve));

function block(over = {}) {
  return {
    supported: true, blocker: null, state: 'idle', job: null, last: null,
    found: { left: null, right: null }, samples: [],
    cal: { right: 1681, center: 1900, left: 2180, max_command: 20, store: 'DEFAULT', guard_right: 1300, guard_left: 2500 },
    pot: 1903, live_pot: null,
    proposal: { marks: { right: 1681, center: 1900, left: 2180 }, sides: [], problem: 'calibration_nothing_found', margin_counts: 30 },
    defaults: { right: 1681, center: 1900, left: 2180 },
    guards: { right_min: 1300, left_max: 2500, center_min: 1800, center_max: 2000, min_span: 150 },
    margin_range: [10, 120], default_margin_counts: 30, max_jump_deg: 20,
    ...over,
  };
}
const reply = (over) => ({ ok: true, armed: false, calibration: block(over) });
const found = (side, pot, usable = true, reason = 'REBOUND') => ({
  side, pot, reason, start: 1902, pulses: 11, drive_ms: 1100, time_ms: 9100, usable, age_s: 12,
});
const rows = (view) => Object.fromEntries(view.rows);
const enabled = (view) => ACTIONS.filter((a) => view.buttons[a].enabled);

test('no bridge, no calibration block, firmware without CAL_RUNTIME: every button off with the reason', () => {
  let v = calView(null);
  assert.deepEqual(enabled(v), []);
  assert.match(v.status.text, /연결 안 됨/);
  v = calView({ ok: true, armed: false });
  assert.equal(v.chip, '보정 없음');
  assert.deepEqual(enabled(v), []);
  v = calView(reply({ supported: false, blocker: 'calibration_needs_firmware_update' }));
  assert.equal(v.chip, '펌웨어 업데이트 필요');
  assert.deepEqual(enabled(v), []);
  assert.match(v.buttons.find_left.title, /펌웨어를 한 번 업데이트/);
  assert.match(v.status.text, /받침/);
});

test('armed or moving: refused with the reason', () => {
  for (const code of ['calibration_needs_disarmed', 'calibration_needs_standstill']) {
    const v = calView(reply({ blocker: code }));
    assert.deepEqual(enabled(v), [], code);
    assert.equal(v.level, 'warn');
    assert.equal(v.chip, '지금은 안 됨');
  }
  assert.match(calView(reply({ blocker: 'calibration_needs_disarmed' })).status.text, /조종 활성화를 끈/);
});

test('idle: centre, both finds and default usable; apply needs a usable end', () => {
  const v = calView(reply());
  assert.deepEqual(enabled(v), ['set_center', 'trim_left', 'trim_right', 'find_left', 'find_right', 'default']);
  assert.match(v.buttons.apply.title, /먼저 끝 찾기/);
  assert.equal(rows(v)['지금 보정'], '오른쪽 1681 · 가운데 1900 · 왼쪽 2180 (기본값)');
  assert.equal(rows(v)['가변저항'], '1903');
  assert.equal(rows(v)['왼쪽 끝 (찾은 값)'], '--');
  assert.equal(v.chip, '보정 기본값');
  const off = calView(reply({ pot: 2150 }));
  assert.equal(off.buttons.set_center.enabled, false, 'the wheels are not near the centre');
  assert.match(off.buttons.set_center.title, /가운데 범위/);
});

test('apply proposal: found ends moved inward by the margins, checked against the hard guards', () => {
  const cal = block({ found: { left: found('left', 2320), right: found('right', 1460, true, 'NO_PROGRESS') } });
  let p = proposal(cal, { left: 30, right: 30 });
  assert.deepEqual([p.marks, p.sides, p.problem], [{ right: 1490, center: 1900, left: 2290 }, ['left', 'right'], null]);
  p = proposal(cal, { left: 40, right: 20 });
  assert.deepEqual(p.marks, { right: 1480, center: 1900, left: 2280 });
  assert.equal(proposal(cal, { left: 5, right: 30 }).problem, 'calibration_margin_invalid');
  assert.equal(proposal(cal, { left: NaN, right: 30 }).problem, 'calibration_margin_invalid');
  const near = block({ found: { left: found('left', 2060), right: null } });
  assert.equal(proposal(near, { left: 30, right: 30 }).problem, 'calibration_left_span_small');
  const wide = block({ found: { left: found('left', 2560), right: null } });
  assert.equal(proposal(wide, { left: 30, right: 30 }).problem, 'calibration_left_above_guard');
  const unusable = block({ found: { left: found('left', 2300, false, 'ABORTED_INPUT'), right: null } });
  assert.equal(proposal(unusable, { left: 30, right: 30 }).problem, 'calibration_nothing_found');
  const v = calView({ ok: true, calibration: cal }, { left: 30, right: 30 });
  assert.equal(v.buttons.apply.enabled, true);
  assert.equal(rows(v)['적용하면'], '오른쪽 1490 · 가운데 1900 · 왼쪽 2290 (왼쪽·오른쪽 바뀜)');
  assert.match(rows(v)['왼쪽 끝 (찾은 값)'], /^2320 \(되밀림에서 멈춤 · 11 펄스 · 9\.1 s · 12 s 전\)$/);
  assert.equal(calView({ ok: true, calibration: cal }, { left: 200, right: 30 }).buttons.apply.enabled, false);
  assert.match(rows(calView({ ok: true, calibration: unusable }))['왼쪽 끝 (찾은 값)'], /쓸 수 없음/);
});

test('SMOOTH firmware r2: the guards of the status (2600, centre 1850..2150), the 2026-11-16 values pass, no staging', () => {
  // 2026-11-16 lifted-wheel field values: straight ahead about pot 2029, left end found 2543..2551
  const smooth = {
    cal: { right: 1475, center: 2029, left: 2513, max_command: 20, store: 'NVS', guard_right: 1300, guard_left: 2600 },
    guards: { right_min: 1300, left_max: 2600, center_min: 1850, center_max: 2150, min_span: 150, source: 'cal_line' },
    pot: 2029, max_jump_deg: 40, smooth: true,
  };
  const v = calView(reply(smooth));
  assert.equal(v.buttons.set_center.enabled, true, 'pot 2029 is inside 1850..2150');
  assert.equal(rows(v)['하드 한계'], '오른쪽 ≥ 1300 · 왼쪽 ≤ 2600 · 가운데 1850–2150 · 폭 ≥ 150');
  assert.match(rows(v)['한 목표 최대'], /나누지 않음/);
  const cal = block({ ...smooth, found: { left: found('left', 2551, true, 'NO_PROGRESS'), right: null } });
  const p = proposal(cal, { left: 30, right: 30 });
  assert.deepEqual([p.marks, p.problem], [{ right: 1475, center: 2029, left: 2521 }, null]);
  assert.equal(proposal(block({ ...smooth, found: { left: found('left', 2640), right: null } }), { left: 30, right: 30 }).problem,
    'calibration_left_above_guard');
  const old = calView(reply());
  assert.match(rows(old)['한 목표 최대'], /나눠 간다/, 'the pulse-hold firmware still stages');
  const bare = calView(reply({ guards: undefined, pot: 2029 }));
  assert.equal(rows(bare)['하드 한계'], '오른쪽 ≥ 1300 · 왼쪽 ≤ 2600 · 가운데 1850–2150 · 폭 ≥ 150', 'fallbacks = SMOOTH r2');
});

test('a running scan: progress, live potentiometer, every action off', () => {
  const v = calView(reply({
    state: 'running', blocker: 'calibration_busy', live_pot: 2101,
    job: { action: 'find_left', id: 3, phase: 'waiting', step: 'CAL FIND LEFT', elapsed_s: 4.5 },
    samples: [{ side: 'left', n: 3, before: 2063, peak: 2101, settled: 2101, progress: 38, rebound: 0, drive_ms: 300 }],
  }));
  assert.equal(v.running, true);
  assert.equal(v.chip, '보정 중');
  assert.deepEqual(enabled(v), []);
  assert.equal(v.status.kind, 'testing');
  assert.match(v.status.text, /왼쪽 끝 찾기 중 · 4\.5 s · 펄스 3 · 정착 2101 · 진척 \+38 · 되밀림 0 · STOP 으로 멈춤/);
  assert.equal(rows(v)['가변저항(정착 값)'], '2101');
  assert.match(v.buttons.apply.title, /진행 중/);
});

test('results of the last action', () => {
  let v = calView(reply({ last: { action: 'apply', ok: true, cal_after: { right: 1490, center: 1900, left: 2290, store: 'NVS' } } }));
  assert.deepEqual([v.status.kind, v.status.text], ['ok', '적용 끝: 오른쪽 1490 · 가운데 1900 · 왼쪽 2290 저장']);
  v = calView(reply({ last: { action: 'find_right', ok: false, error: 'calibration_end_not_found', detail: 'NO_PROGRESS' } }));
  assert.deepEqual([v.status.kind, v.status.text], ['bad', '오른쪽 끝 찾기 실패: 끝을 찾지 못함 (진척 없음)']);
  v = calView(reply({ last: { action: 'find_left', ok: false, error: 'calibration_stopped' } }));
  assert.equal(v.status.text, '왼쪽 끝 찾기 실패: STOP 으로 멈춤');
  v = calView(reply({ cal: { right: 1490, center: 1900, left: 2180, store: 'UNSAVED' },
    last: { action: 'apply', ok: false, error: 'calibration_no_answer', detail: 'CAL SAVE' } }));
  assert.match(v.status.text, /저장 안 된 값이 쓰이는 중/);
  assert.match(rows(v)['지금 보정'], /\(저장 안 됨\)$/);
});

// ---- page binding with a fake DOM --------------------------------------------------------------------------------
class El {
  constructor(id = null, tag = 'div') {
    Object.assign(this, { id, tagName: tag.toUpperCase(), textContent: '', className: '', disabled: false, hidden: false,
      title: '', value: '', open: false, dataset: {}, children: [], listeners: {}, buttons: [] });
  }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  fire(name, event = {}) { for (const fn of this.listeners[name] || []) fn(event); }
  replaceChildren(...items) { this.children = items; }
  append(...items) { this.children.push(...items); }
  querySelectorAll() { return this.buttons; }
}

const opened = [];
after(() => { for (const close of opened) close(); });   // also when a test failed half way

function page(statusReply) {
  const els = {};
  for (const id of ['steerCalPanel', 'steerCalDetails', 'steerCalState', 'steerCalRows', 'steerCalStatus', 'steerCalStop',
    'steerCalSamplesWrap', 'steerCalSamples', 'steerCalMarginLeft', 'steerCalMarginRight']) els[id] = new El(id);
  els.steerCalMarginLeft.value = '30';
  els.steerCalMarginRight.value = '30';
  els.steerCalDetails.open = true;
  for (const action of ACTIONS) {
    const button = new El(null, 'button');
    button.dataset.steerCal = action;
    els.steerCalPanel.buttons.push(button);
  }
  const docListeners = {};
  const doc = {
    hidden: false, getElementById: (id) => els[id] || null, createElement: (tag) => new El(null, tag),
    addEventListener: (name, fn) => { (docListeners[name] ||= []).push(fn); },
  };
  const log = { posts: [], controls: [], gets: 0, confirms: [] };
  const state = { reply: statusReply, answer: { ok: true, state: 'calibration_started' }, confirm: true };
  const api = {
    currentRoute: () => '/rover-control',
    getJson: async () => { log.gets += 1; return state.reply; },
    postJson: async (route, body) => { log.posts.push([route, body]); return state.answer; },
    controlRequest: async (route, body) => { log.controls.push([route, body]); return { ok: true, state: 'stop_requested' }; },
    driveStatus: () => null,
  };
  const screen = initSteerCalibration(api, { document: doc, confirm: (text) => { log.confirms.push(text); return state.confirm; } });
  const button = (action) => els.steerCalPanel.buttons.find((b) => b.dataset.steerCal === action);
  const hide = () => { doc.hidden = true; for (const fn of docListeners.visibilitychange || []) fn(); };
  // the section polls every 500 ms while open: folding it stops the timer (and lets the test process end)
  const close = () => { els.steerCalDetails.open = false; screen.routeChanged(); };
  opened.push(close);
  return { els, doc, log, state, screen, button, hide, close };
}

test('page: buttons follow the status; apply and default ask first and send their bodies', async () => {
  const p = page(reply({ found: { left: found('left', 2320), right: found('right', 1460) } }));
  await flush();
  assert.ok(p.log.gets >= 1);
  assert.equal(p.button('apply').disabled, false);
  assert.equal(p.button('find_left').disabled, false);
  assert.equal(p.els.steerCalState.textContent, '보정 기본값');
  assert.equal(p.els.steerCalRows.children.length, 7);
  p.state.confirm = false;
  p.button('apply').fire('click');
  await flush();
  assert.equal(p.log.posts.length, 0, 'no request when the dialog is declined');
  assert.match(p.log.confirms[0], /새 값: 오른쪽 1490 · 가운데 1900 · 왼쪽 2290/);
  p.state.confirm = true;
  p.els.steerCalMarginLeft.value = '40';
  p.els.steerCalMarginLeft.fire('input');
  p.button('apply').fire('click');
  await flush();
  assert.deepEqual(p.log.posts.at(-1), ['/api/rover/calibrate', { action: 'apply', left_margin_counts: 40, right_margin_counts: 30 }]);
  p.button('default').fire('click');
  await flush();
  assert.match(p.log.confirms.at(-1), /기본값\(오른쪽 1681 · 가운데 1900 · 왼쪽 2180\)/);
  assert.deepEqual(p.log.posts.at(-1), ['/api/rover/calibrate', { action: 'default' }]);
  p.button('find_right').fire('click');
  await flush();
  assert.match(p.log.confirms.at(-1), /오른쪽 끝까지 천천히/);
  assert.deepEqual(p.log.posts.at(-1), ['/api/rover/calibrate', { action: 'find_right' }]);
  p.button('set_center').fire('click');
  await flush();
  assert.match(p.log.confirms.at(-1), /가변저항 1903/);
  assert.deepEqual(p.log.posts.at(-1), ['/api/rover/calibrate', { action: 'set_center' }]);
  assert.equal(p.log.confirms.length, 5, 'every action asked first (one declined, four sent)');
  p.close();
});

test('page: a refusal is shown; disabled buttons send nothing; STOP always goes', async () => {
  const p = page(reply({ blocker: 'calibration_needs_disarmed' }));
  await flush();
  assert.equal(p.button('find_left').disabled, true);
  assert.match(p.els.steerCalStatus.textContent, /조종 활성화를 끈/);
  p.button('find_left').fire('click');
  await flush();
  assert.equal(p.log.posts.length, 0);
  assert.equal(p.log.confirms.length, 0);
  p.els.steerCalStop.fire('click');
  await flush();
  assert.deepEqual(p.log.controls, [['/api/rover/stop', { reason: 'calibration_stop' }]]);
  p.state.reply = reply();
  await p.screen.poll();
  p.state.answer = { ok: false, error: 'calibration_needs_firmware_update' };
  p.button('find_left').fire('click');
  await flush();
  await flush();
  assert.match(p.els.steerCalStatus.textContent, /왼쪽 끝 찾기 거절: 펌웨어에 실시간 보정이 없음/);
  p.close();
});

test('page: hiding the page while a scan runs sends STOP', async () => {
  const p = page(reply({ state: 'running', blocker: 'calibration_busy',
    job: { action: 'find_left', id: 1, phase: 'waiting', step: 'CAL FIND LEFT', elapsed_s: 2 } }));
  await flush();
  assert.equal(p.els.steerCalState.textContent, '보정 중');
  p.hide();
  await flush();
  assert.deepEqual(p.log.controls, [['/api/rover/stop', { reason: 'page_hidden' }]]);
  p.close();
});

test('markup: the folded section is inside Rover Control with the seven buttons, no new menu', () => {
  const html = readFileSync(path.join(here, '..', 'static', 'index.html'), 'utf8');
  const start = html.indexOf('id="steerCalPanel"');
  // Right after the potentiometer rows; the autonomy section is above both since 2026-11-17 (docs/SCREENS_KO.md 2).
  assert.ok(start > html.indexOf('id="steerPotPanel"') && start < html.indexOf('id="steerDistPanel"'), 'next to the potentiometer rows');
  assert.ok(html.indexOf('id="autoPanel"') < html.indexOf('id="steerPotPanel"'), 'the autonomy section comes first');
  const section = html.slice(start, html.indexOf('</section>', start));
  assert.match(section, /<details id="steerCalDetails">/);
  assert.match(html.slice(html.lastIndexOf('<section', start), start), /data-vehicle="front_steer"/);
  const labels = [...section.matchAll(/data-steer-cal="([a-z_]+)"[^>]*>([^<]+)</g)].map((m) => [m[1], m[2]]);
  assert.deepEqual(labels, [['set_center', '지금 위치를 가운데로'], ['trim_left', '영점 ← 왼쪽 0.2°'],
    ['trim_right', '영점 → 오른쪽 0.2°'], ['find_left', '왼쪽 끝 찾기'], ['find_right', '오른쪽 끝 찾기'],
    ['apply', '적용'], ['default', '기본값으로']]);
  assert.match(section, /<h3>조향 보정<\/h3>/);
  assert.ok(!/nav-item|data-route=/.test(section), 'no navigation entry');
  const raw = readFileSync(path.join(here, '..', 'static', 'steer', 'steer-calibration.mjs'));
  assert.ok(!raw.includes(Buffer.from('\r\n')) && raw[0] !== 0xef, 'UTF-8 LF without BOM');
});
