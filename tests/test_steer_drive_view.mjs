// The steering panel's pure parts (static/steer/steer-drive.mjs): device texts, fault labels and the gauge and readout
// values from a controller. No browser, no network. Run: node tests/test_steer_drive_view.mjs
import assert from 'node:assert/strict';
import { DEVICE_TEXT, faultLabels, steerView } from '../static/steer/steer-drive.mjs';

const R = globalThis.UbtsSteerRules;
assert.ok(R && typeof R.SteerController === 'function', 'the module loads the rules');

// Every device state has a chip text; the not-present states explain why arm is impossible.
for (const state of ['present', 'none', 'unavailable', 'unknown']) assert.ok(DEVICE_TEXT[state][1], state);
assert.match(DEVICE_TEXT.none[2], /조향 장치 없음/);
assert.match(DEVICE_TEXT.unavailable[2], /연결 안 됨/);

// Faults: catalogue labels; an unknown code gets a short generic label and keeps its code.
const labels = faultLabels(['SOFTLIMIT', 'steering_not_following', 'BOOT', 'something_new']);
assert.deepEqual(labels.map((f) => f.level), ['warn', 'bad', 'info', 'bad']);
assert.equal(labels[1].label, '조향 추종 안 됨');
assert.deepEqual([labels[3].label, labels[3].code], ['기타 결함', 'something_new']);
assert.deepEqual(faultLabels(null), []);

// Gauge: left steering (positive mrad) is drawn left of the centre, right steering right of it; limits per side.
const now = 50000;
const status = {
  ok: true, armed: false, vehicle_profile: 'front_steer', steer_limit_mrad: { left: 400, right: 200 }, updated_at: 1,
  outputs: { D8: 102, D5: 0, D3: 102, D0: 0, D1: 255, D2: 255 },
  steering: { measured_mrad: -100, measured_permille: -228, target_permille: -228, age_ms: 20, mcu_ms: 1, seq: 1, faults: ['TTL'], saturated: false },
  steering_host: { stale: false },
};
const ctl = new R.SteerController({ now: () => now, transport: null });
ctl.onStatus(status, now, now);
let view = steerView(ctl, now);
assert.equal(view.device, 'present');
assert.equal(view.measuredPos, 75, 'right steering half way to the right limit: 75 %');
assert.equal(view.targetPos, null, 'nothing sent yet: no target marker');
assert.deepEqual(view.duty, { left: 102, right: 102, en: true });
assert.equal(view.armed, false);
assert.equal(view.deadlineMs, null);
assert.deepEqual(view.faults.map((f) => f.code), ['TTL']);
ctl.lease = 'L'; ctl.hostTarget = 200; ctl.inputDeadlineAt = now + 300;
view = steerView(ctl, now);
assert.equal(view.targetPos, 25, 'left steering half way to the left limit: 25 %');
assert.equal(Math.round(view.errorDeg * 10) / 10, Math.round(R.mradToDeg(300) * 10) / 10, 'error = target - measured');
assert.equal(view.deadlineMs, 300);
// A V2 bridge status (no steering block): no device, the default placeholder limits.
const v2 = new R.SteerController({ now: () => now, transport: null });
v2.onStatus({ ok: true, armed: false, state: 'disarmed', outputs: {} }, now, now);
view = steerView(v2, now);
assert.equal(view.device, 'none');
assert.deepEqual(view.limits, { left: 409, right: 409 });
assert.equal(view.measuredPos, null);

console.log('PASS: steering panel device texts, fault labels and gauge values');
