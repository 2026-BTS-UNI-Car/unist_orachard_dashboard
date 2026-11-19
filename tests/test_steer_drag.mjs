// Steering of the ESP32-S3 'ABS-HOLD' bridge on the Rover Control page (2026-11-16): A/D to the full limit and back to
// the centre, the drag control (precise angle while dragging, 0 when released by pointer up / cancel / lost capture,
// touch end, blur or a hidden page), keys and drag never fighting, STOP winning, the bridge-given status age limit and
// the page taking the steering panel when the bridge says front_steer. No browser, no network.
// Run: node --test tests/test_steer_drag.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { resetScreen, adRateText, armBlockText, bindSteerDrag, boardNotReady, dragPosition, dragText, driveCapText, faultLabels, steerView, HELP_TEXT } from '../static/steer/steer-drive.mjs';

const R = globalThis.UbtsSteerRules;
const here = path.dirname(fileURLToPath(import.meta.url));
const LIMIT = 349;                                     // 20 command degrees, scripts/esp32_abs_hold.LIMIT_MRAD
const flush = () => new Promise((resolve) => setImmediate(resolve));

function esp32Status(over = {}) {
  return {
    ok: true, armed: false, state: 'disarmed', vehicle_profile: 'front_steer', esp_kind: 'esp32_abs_hold',
    steer_limit_mrad: { left: LIMIT, right: LIMIT }, updated_at: 1,
    steer_control: { kind: 'esp32_abs_hold', keys: 'full_limit', ramp: false, angle_quantum_deg: 1, angle_min_interval_ms: 500 },
    outputs: { D8: 0, D5: 0, D3: 0, D0: 0, D1: 0, D2: 0 },
    steering: { measured_mrad: 0, measured_permille: 0, age_ms: 40, age_limit_ms: 300, mcu_ms: null, seq: 1, faults: [], saturated: false },
    steering_host: { stale: false },
    ...over,
  };
}

// A bridge that answers like SteerControl (lease, one-use ticket, seq + 1) and records every drive.
function rig(statusOver = {}) {
  let t = 100000;
  const clock = { now: () => t, add: (ms) => { t += ms; } };
  const bridge = { lease: null, ticket: null, seq: 0, n: 0, drives: [], stops: [], seqStatus: 1 };
  const status = () => ({ ...esp32Status(statusOver), armed: Boolean(bridge.lease),
    steering: { ...esp32Status(statusOver).steering, seq: (bridge.seqStatus += 1) } });
  const transport = {
    status: async () => status(),
    post: async (route, body) => {
      const op = route.split('/').pop();
      if (op === 'stop') { bridge.stops.push(body.reason); bridge.lease = null; return { ok: true, state: 'stop_requested' }; }
      if (op === 'arm') { bridge.lease = 'L'; bridge.seq = 0; }
      else {
        if (body.lease !== bridge.lease || body.ticket !== bridge.ticket || body.seq !== bridge.seq + 1) return { ok: false, error: 'invalid_sequence' };
        bridge.seq = body.seq;
        if (op === 'command') bridge.drives.push({ at: t, throttle: body.throttle, steer: body.steer_mrad });
      }
      bridge.n += 1;
      bridge.ticket = `T${bridge.n}`;
      return { ok: true, lease: bridge.lease, ticket: bridge.ticket, seq: bridge.seq, status: status() };
    },
  };
  const ctl = new R.SteerController({ now: clock.now, transport, sleep: async () => {} });
  async function step(ms = 50) {
    clock.add(ms);
    await ctl.pollStatus();
    await ctl.tick();
    await flush();
  }
  async function arm() {
    await ctl.pollStatus();
    await ctl.arm();
    await flush();
    assert.ok(ctl.lease, 'armed');
  }
  const lastSteer = () => bridge.drives.at(-1)?.steer;
  return { ctl, bridge, clock, step, arm, lastSteer };
}

test('A/D go to the full limit at once and back to the centre on release', async () => {
  const r = rig();
  await r.arm();
  assert.equal(r.ctl.absoluteMode(), true);
  r.ctl.keyDown('a');
  await r.step();
  assert.equal(r.lastSteer(), LIMIT, 'left is positive steer_mrad: the full left limit, no ramp, no 5 deg step');
  r.ctl.keyUp('a');
  await r.step();
  assert.equal(r.lastSteer(), 0, 'released: the centre at once');
  r.ctl.keyDown('d');
  await r.step();
  assert.equal(r.lastSteer(), -LIMIT);
  r.ctl.setSteerHold(true);
  r.ctl.keyUp('d');
  await r.step();
  await r.step(150);
  assert.equal(r.lastSteer(), -LIMIT, "'각 유지' keeps the target (the firmware holds it)");
  r.ctl.setCentre(true);
  await r.step();
  assert.equal(r.lastSteer(), 0);
  r.ctl.setCentre(false);
  await r.step(150);
  assert.equal(r.lastSteer(), 0, 'C released: the target stays at the centre (no freeze at a measured angle)');
});

test('drag: a precise angle, rate limited, and 0 when released', async () => {
  const r = rig();
  await r.arm();
  assert.equal(r.ctl.dragStart(12.3), true);
  assert.equal(r.ctl.drag.deg, 12.5, 'drag values in 0.5 deg steps');
  await r.step();
  assert.equal(r.lastSteer(), Math.round(R.degToMrad(12.5)));
  const changes = [];
  let last = r.ctl.hostTarget;
  for (let i = 0; i < 20; i += 1) {                     // a fast drag to the right: a new value every 50 ms
    r.ctl.dragMove(12 - i);
    await r.step(50);
    if (r.ctl.hostTarget !== last) { changes.push(r.clock.now()); last = r.ctl.hostTarget; }
  }
  const gaps = changes.slice(1).map((at, i) => at - changes[i]);
  assert.ok(changes.length >= 3, 'the target follows the drag');
  assert.ok(gaps.every((gap) => gap >= R.DEFAULTS.dragPeriodMs), `at most one new target per ${R.DEFAULTS.dragPeriodMs} ms: ${gaps}`);
  await r.step(300);
  assert.equal(r.lastSteer(), Math.round(R.degToMrad(-7)), 'the newest drag value is sent');
  r.ctl.dragEnd('pointerup');
  await r.step();
  assert.equal(r.lastSteer(), 0, 'released: back to 0 at once');
  assert.equal(r.ctl.drag.active, false);
  r.ctl.dragStart(30);
  assert.equal(r.ctl.drag.deg, R.mradToDeg(LIMIT), 'clamped to the status limit');
  r.ctl.blur();
  await r.step();
  assert.equal(r.lastSteer(), 0, 'blur ends the drag: 0');
});

const SMOOTH_CONTROL = { kind: 'esp32_abs_hold', keys: 'full_limit', ramp: false, angle_quantum_deg: 0.25,
  angle_min_interval_ms: 200, angle_max_jump_deg: 40, stage_max_counts: null, smooth: true };

test('SMOOTH firmware: A/D stay one full-limit target; the drag goes in 0.25 deg steps at most every 50 ms', async () => {
  const r = rig({ steer_control: SMOOTH_CONTROL });
  await r.arm();
  assert.equal(r.ctl.smoothMode(), true);
  assert.equal(r.ctl.dragStepDeg(), 0.25);
  assert.equal(r.ctl.dragPeriodMs(), 50);
  r.ctl.keyDown('a');
  for (let i = 0; i < 10; i += 1) await r.step(100);    // A held for a second: the same target every time
  assert.deepEqual([...new Set(r.bridge.drives.map((d) => d.steer))], [LIMIT], 'one full-limit target, no page ramp');
  r.ctl.keyUp('a');
  await r.step();
  assert.equal(r.lastSteer(), 0, 'released: the centre at once');
  assert.equal(r.ctl.dragStart(12.3), true);
  assert.equal(r.ctl.drag.deg, 12.25, '0.25 deg steps');
  await r.step();
  assert.equal(r.lastSteer(), Math.round(R.degToMrad(12.25)));
  const changes = [];
  let last = r.ctl.hostTarget;
  for (let i = 0; i < 40; i += 1) {                       // a slow drag: 0.1 deg per 25 ms
    r.ctl.dragMove(12.25 - 0.1 * i);
    await r.step(25);
    if (r.ctl.hostTarget !== last) { changes.push(r.clock.now()); last = r.ctl.hostTarget; }
  }
  const gaps = changes.slice(1).map((at, i) => at - changes[i]);
  assert.ok(changes.length >= 8, `the target follows the drag in fine steps: ${changes.length}`);
  assert.ok(gaps.every((gap) => gap >= 50), `at most one new target per 50 ms: ${gaps}`);
  for (const d of r.bridge.drives.slice(-5)) {
    const deg = R.mradToDeg(d.steer);
    // integer mrad on the wire (0.057 deg); the bridge rounds back to the 0.25 deg grid
    assert.ok(Math.abs(deg - 0.25 * Math.round(deg / 0.25)) < 0.03, `0.25 deg targets: ${deg}`);
  }
  r.ctl.dragEnd('pointerup');
  await r.step();
  assert.equal(r.lastSteer(), 0);
  const view = steerView(r.ctl, r.clock.now());
  assert.equal(view.smooth, true);
  assert.equal(view.dragStepDeg, 0.25);
  assert.equal(dragText(12.25), '왼쪽 12.3°', 'shown with one decimal');
  assert.match(HELP_TEXT.smooth, /끊김 없이/);
});

test('without steer_control.smooth the drag keeps 0.5 deg / 250 ms (older firmware, SM OFF)', async () => {
  const r = rig({ steer_control: { ...SMOOTH_CONTROL, smooth: false } });
  await r.arm();
  assert.equal(r.ctl.smoothMode(), false);
  assert.equal(r.ctl.dragStepDeg(), 0.5);
  assert.equal(r.ctl.dragPeriodMs(), 250);
});

test('W/S are not disabled by the page; a drive cap of 0 is shown and said', async () => {
  const r = rig({ drive: { max_pwm: 0 } });
  await r.arm();
  assert.equal(r.ctl.driveCapPwm(), 0);
  r.ctl.keyDown('w');
  await r.step();
  assert.equal(r.bridge.drives.at(-1).throttle, r.ctl.speedCapPct, 'W is sent as always: the bridge applies its cap');
  assert.match(r.ctl.message.text, /구동 상한 0/);
  r.ctl.keyUp('w');
  assert.equal(driveCapText(0), '구동 상한 0 (브리지 설치 값): W/S 꺼짐');
  assert.equal(driveCapText(120), 'MCU 보고 · 브리지 상한 PWM 120');
  assert.equal(driveCapText(undefined), 'MCU 보고 · 속도 아님');
  const s = rig({ drive: { max_pwm: 255 } });
  await s.arm();
  s.ctl.keyDown('s');
  await s.step();
  assert.equal(s.bridge.drives.at(-1).throttle, -s.ctl.speedCapPct);
  assert.doesNotMatch(s.ctl.message.text, /구동 상한 0/);
  assert.equal(steerView(s.ctl, s.clock.now()).driveCapPwm, 255);
});

test('keys and drag never fight; STOP always wins', async () => {
  const r = rig();
  await r.arm();
  r.ctl.dragStart(-10);
  r.ctl.keyDown('a');
  r.ctl.setCentre(true);
  assert.equal(r.ctl.keys.a, false, 'A while dragging is ignored');
  assert.equal(r.ctl.centreHeld, false, 'C while dragging is ignored');
  await r.step();
  assert.equal(r.lastSteer(), Math.round(R.degToMrad(-10)));
  r.ctl.dragEnd('pointerup');
  r.ctl.keyDown('d');
  assert.equal(r.ctl.dragStart(5), false, 'no drag while a steering key is held');
  await r.step();
  assert.equal(r.lastSteer(), -LIMIT);
  r.ctl.keyUp('d');
  r.ctl.keyDown('w');                                     // drive and drag together are fine
  assert.equal(r.ctl.dragStart(8), true);
  await r.step();
  assert.equal(r.bridge.drives.at(-1).throttle, r.ctl.speedCapPct);
  const sent = r.bridge.drives.length;
  r.ctl.stop('operator_stop');
  await flush();
  assert.equal(r.ctl.lease, null);
  assert.equal(r.ctl.drag.active, false, 'STOP clears the drag');
  assert.deepEqual(r.bridge.stops, ['operator_stop']);
  r.ctl.dragMove(15);
  await r.step();
  await r.step();
  assert.equal(r.bridge.drives.length, sent, 'nothing is sent after STOP');
  assert.equal(r.ctl.dragStart(3), false, 'no drag without a lease');
});

test('ramp bridges (V3) keep their rules; the drag goes through the ramp', async () => {
  const r = rig({ steer_control: undefined, steer_limit_mrad: { left: 409, right: 409 } });
  await r.arm();
  assert.equal(r.ctl.absoluteMode(), false);
  r.ctl.dragStart(10);
  await r.step(100);
  await r.step(100);
  const first = r.ctl.hostTarget;
  assert.ok(first > 0 && first < R.degToMrad(10), `ramped, not a jump: ${first}`);
});

test('the status age limit comes from the bridge when it gives one', () => {
  const ctl = new R.SteerController({ now: () => 1000, transport: null });
  ctl.onStatus(esp32Status({ steering: { ...esp32Status().steering, age_ms: 250 } }), 1000, 1000);
  assert.equal(ctl.ageLimitMs(), 300);
  assert.equal(ctl.fresh(1000), true, 'a 250 ms old STATUS line is fresh for a 10 Hz bridge');
  ctl.onStatus(esp32Status({ steering: { ...esp32Status().steering, age_ms: 250, age_limit_ms: undefined } }), 1001, 1001);
  assert.equal(ctl.fresh(1001), false, 'without it the 150 ms rule stays');
  ctl.onStatus(esp32Status({ steering: { ...esp32Status().steering, age_ms: 600, age_limit_ms: 5000 } }), 1002, 1002);
  assert.equal(ctl.ageLimitMs(), R.DEFAULTS.ageLimitMaxMs, 'a bridge cannot widen it past 500 ms');
  assert.equal(ctl.fresh(1002), false);
});

test('labels, positions and help text', () => {
  assert.deepEqual(faultLabels(['pot_invalid', 'steer_hold_cancelled', 'mcu_reset_detected']).map((f) => [f.label, f.level]),
    [['가변저항 값 무효(배선 확인)', 'bad'], ['조향 유지 취소(펌웨어)', 'warn'], ['MCU 리셋 감지', 'bad']]);
  assert.equal(dragText(0), '0°');
  assert.equal(dragText(12.5), '왼쪽 12.5°');
  assert.equal(dragText(-3), '오른쪽 3.0°');
  const ctl = new R.SteerController({ now: () => 1, transport: null });
  ctl.onStatus(esp32Status(), 1, 1);
  assert.equal(dragPosition(ctl, R.mradToDeg(LIMIT)), 0, 'full left at the left edge');
  assert.equal(dragPosition(ctl, -R.mradToDeg(LIMIT)), 100);
  assert.equal(dragPosition(ctl, 0), 50);
  const view = steerView(ctl, 1);
  assert.equal(view.absolute, true);
  assert.equal(view.dragPos, 50);
  assert.match(HELP_TEXT.absolute, /A\/D 조향\(끝까지\)/);
  for (const text of Object.values(HELP_TEXT)) assert.ok(text.length <= 45, `one short line: ${text}`);
});

// A fake element: enough of EventTarget, layout and pointer capture for bindSteerDrag.
class FakeElement extends EventTarget {
  constructor() { super(); this.captured = new Set(); }
  getBoundingClientRect() { return { left: 100, width: 400, top: 0, height: 56 }; }
  setPointerCapture(id) { this.captured.add(id); }
  releasePointerCapture(id) { this.captured.delete(id); }
}
function pointer(type, pointerId, clientX) {
  const event = new Event(type, { cancelable: true });
  Object.assign(event, { pointerId, clientX });
  return event;
}

test('the drag element: pointer, touch and the page end the drag, each back to 0', async () => {
  for (const ending of ['pointerup', 'pointercancel', 'lostpointercapture', 'touchend', 'touchcancel', 'blur', 'page_hidden']) {
    const r = rig();
    await r.arm();
    const el = new FakeElement();
    const drag = bindSteerDrag(el, r.ctl);
    el.dispatchEvent(pointer('pointerdown', 7, 200));    // a quarter from the left: half way to the left limit
    assert.equal(r.ctl.drag.active, true, ending);
    assert.ok(el.captured.has(7));
    assert.equal(r.ctl.drag.deg, 10, 'left of the centre is left steering (positive)');
    el.dispatchEvent(pointer('pointermove', 9, 500));    // another finger: ignored
    assert.equal(r.ctl.drag.deg, 10);
    el.dispatchEvent(pointer('pointermove', 7, 500));    // the right edge: the right limit
    assert.equal(r.ctl.drag.deg, -R.mradToDeg(LIMIT));
    await r.step(300);
    assert.equal(r.lastSteer(), -LIMIT);
    if (ending === 'blur' || ending === 'page_hidden') drag.end(ending);
    else if (ending.startsWith('touch')) el.dispatchEvent(new Event(ending));
    else el.dispatchEvent(pointer(ending, 7, 500));
    assert.equal(r.ctl.drag.active, false, ending);
    assert.equal(drag.held(), false);
    await r.step();
    assert.equal(r.lastSteer(), 0, `${ending}: back to 0`);
  }
  const r = rig();
  await r.ctl.pollStatus();
  const el = new FakeElement();
  bindSteerDrag(el, r.ctl);
  el.dispatchEvent(pointer('pointerdown', 1, 150));
  assert.equal(r.ctl.drag.active, false, 'not armed: no drag');
  assert.equal(el.captured.size, 0);
});

test('a differential page takes the steering panel when the bridge says front_steer', () => {
  const source = readFileSync(path.join(here, '..', 'static', 'app', '14-steer.js'), 'utf8').replace(/\bimport\(url\)/, 'fakeImport(url)');
  const fetched = [];
  const dataset = {};
  const context = {
    document: { documentElement: { dataset } }, window: { addEventListener() {} }, IntersectionObserver: undefined, console,
    $: () => null, currentRoute: () => '/rover-control', getJson() {}, postJson() {}, controlRequest() {}, renderReadout() {}, chartColors() {},
    fakeImport: (url) => { fetched.push(url.split('/').pop()); return Promise.resolve(new Proxy({}, { get: () => () => ({ routeChanged() {} }) })); },
  };
  vm.createContext(context);
  vm.runInContext(`${source}\nthis.adoptBridgeVehicle = adoptBridgeVehicle;`, context);
  const before = fetched.length;
  assert.equal(context.adoptBridgeVehicle(undefined), false, 'a V2 bridge: nothing changes');
  assert.equal(context.adoptBridgeVehicle('differential'), false);
  assert.equal(dataset.vehicleKind, undefined);
  assert.equal(context.adoptBridgeVehicle({ vehicle_profile: 'front_steer', esp_kind: 'esp32_abs_hold' }), true);
  assert.equal(dataset.vehicleKind, 'front_steer');
  assert.deepEqual(fetched.slice(before).filter((name) => name.startsWith('steer') || name === 'auto-panel.mjs'),
    ['steer-drive.mjs', 'auto-panel.mjs', 'steer-sensor.mjs']);
  assert.equal(context.adoptBridgeVehicle('front_steer'), false, 'once');
});

test('a front-steer bridge is remembered: the next load takes the steering panel at once (2026-11-18 battery swap)', () => {
  const source = readFileSync(path.join(here, '..', 'static', 'app', '14-steer.js'), 'utf8').replace(/\bimport\(url\)/, 'fakeImport(url)');
  const make = (stored) => {
    const store = new Map(stored ? [['ubts-vehicle-kind-seen', stored]] : []);
    const dataset = {};
    const fetched = [];
    const context = {
      document: { documentElement: { dataset } }, window: { addEventListener() {} }, IntersectionObserver: undefined, console,
      localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v) },
      $: () => null, currentRoute: () => '/rover-control', getJson() {}, postJson() {}, controlRequest() {}, renderReadout() {}, chartColors() {},
      fakeImport: (url) => { fetched.push(url.split('/').pop()); return Promise.resolve(new Proxy({}, { get: () => () => ({ routeChanged() {} }) })); },
    };
    vm.createContext(context);
    vm.runInContext(`${source}
this.adoptBridgeVehicle = adoptBridgeVehicle;`, context);
    return { context, dataset, store, fetched };
  };
  const first = make(null);
  assert.equal(first.dataset.vehicleKind, undefined, 'nothing known yet: the page as served');
  assert.equal(first.context.adoptBridgeVehicle({ vehicle_profile: 'front_steer' }), true);
  assert.equal(first.store.get('ubts-vehicle-kind-seen'), 'front_steer');
  const next = make('front_steer');
  assert.equal(next.dataset.vehicleKind, 'front_steer', 'a later load (bridge or board still restarting) never shows the differential pad');
  assert.equal(next.dataset.vehicleKindFrom, 'remembered');
  assert.ok(next.fetched.includes('steer-drive.mjs'));
  assert.equal(next.context.adoptBridgeVehicle(undefined), false, 'a status without a profile changes nothing');
  assert.equal(next.dataset.vehicleKind, 'front_steer');
});

test('board not ready: the reason and a clear text (pot_invalid = wiring)', () => {
  assert.equal(boardNotReady({ steering_host: { not_ready: 'pot_invalid' } }), 'pot_invalid');
  assert.equal(boardNotReady({ steering_host: { not_ready: null } }), null);
  assert.equal(boardNotReady(null), null);
  assert.match(armBlockText('pot_invalid'), /가변저항 값 무효\(배선 확인\)/);
  assert.match(armBlockText('device_unavailable'), /보드 다시 연결 중/);
  assert.match(R.messageFor('pot_invalid'), /배선 확인/);
});

test('the differential Arm on a front-steer bridge adopts the steering panel and says so in Korean', () => {
  const source = readFileSync(path.join(here, '..', 'static', 'app', '10-rover-control.js'), 'utf8');
  assert.match(source, /!result\.ok && result\.error === "steer_profile_required"/);
  assert.doesNotMatch(source, /vehicle_profile/, 'the old screen still never sends (or reads) a vehicle profile');
  assert.match(source, /adoptBridgeVehicle\("front_steer"\)/);
  assert.match(source, /throw new Error\(result\.message \|\| result\.error\)/);
});

test('A/D steering speed: the target ramps at the chosen rate while held; release goes to the centre at once', async () => {
  const r = rig();
  await r.arm();
  assert.equal(r.ctl.adRateDegS, 20, 'default: the full limit at once (as before)');
  assert.equal(r.ctl.setAdRate(5), true);
  assert.equal(r.ctl.setAdRate(99) && r.ctl.adRateDegS, 20, 'bounded 3..20');
  r.ctl.setAdRate(5);
  r.ctl.keyDown('a');
  for (let i = 0; i < 20; i += 1) await r.step(50);           // A held for 1 s at 5 deg/s
  const deg = R.mradToDeg(r.lastSteer());
  assert.ok(deg > 4 && deg < 6, `about 5 deg after 1 s: ${deg}`);
  r.ctl.keyUp('a');
  await r.step();
  assert.equal(r.lastSteer(), 0, 'release: the centre at once (unchanged)');
  r.ctl.setAdRate(20);
  r.ctl.keyDown('d');
  await r.step();
  assert.equal(r.lastSteer(), -LIMIT, 'the top of the range: the full limit at once');
  assert.equal(adRateText(20, 20), '최대(바퀴 속도)');
  assert.equal(adRateText(7, 20), '7 °/s');
});

test('화면 초기화: STOP first, forget the session, clear the stored state, load again; nothing without a yes', async () => {
  const calls = [];
  const store = () => { const m = new Map([['ubts-steer-ad-rate', '7'], ['ubts-vehicle-kind-seen', 'front_steer']]); return { m, clear: () => m.clear() }; };
  const local = store(), session = store();
  const ctl = { lease: 'L', stop: async (why) => { calls.push(['stop', why]); } };
  const page = { controlRequest: async (route) => { calls.push(['post', route]); return { ok: true }; } };
  let reloaded = 0;
  assert.equal(await resetScreen(ctl, page, { confirm: () => false, localStorage: local, sessionStorage: session, reload: () => { reloaded += 1; } }), false);
  assert.deepEqual(calls, []);
  assert.equal(local.m.size, 2);
  assert.equal(await resetScreen(ctl, page, { confirm: () => true, localStorage: local, sessionStorage: session, reload: () => { reloaded += 1; } }), true);
  assert.deepEqual(calls, [['stop', 'screen_reset'], ['post', '/api/session/reset']]);
  assert.equal(local.m.size + session.m.size, 0);
  assert.equal(reloaded, 1);
  const html = readFileSync(path.join(here, '..', 'static', 'index.html'), 'utf8');
  assert.match(html, /id="screenResetButton"[^>]*>화면 초기화<\/button>/);
});
