// The steering potentiometer card of the sensors screen (static/steer/steer-sensor.mjs): the rows and the chip from
// GET /api/rover/status (steering bridge status). No browser. Run: node tests/test_steer_sensor.mjs
import assert from 'node:assert/strict';
import { STATUS_MS, firmwareText, potView } from '../static/steer/steer-sensor.mjs';

assert.equal(STATUS_MS, 500);
const status = (steering, host = { stale: false }) => ({ ok: true, steering, steering_host: host });

let [level, text, rows] = potView(status({ measured_mrad: -152, measured_permille: -347, raw: 401, calibrated: true, age_ms: 20, faults: [] }));
assert.deepEqual([level, text], ['ok', '정상']);
assert.deepEqual(Object.fromEntries(rows), {
  '측정 각도': '−8.7° (-152 mrad)', '측정 위치': '-347 ‰ · 왼쪽이 +', '가변저항 원값': '401', '보정': '있음',
  '조향 펌웨어': '--', '마지막 상태': '20 ms 전', '결함': '없음',
});
// The firmware row (2026-11-17): SMOOTH or PULSE from steer_control, so the operator sees how the wheels will move.
const withControl = (steer_control) => ({ ...status({ faults: [] }), steer_control });
assert.equal(firmwareText(withControl({ keys: 'full_limit', smooth: true })), 'SMOOTH · 한 번에 끊김 없이');
assert.equal(firmwareText(withControl({ keys: 'full_limit', smooth: false })), 'PULSE · 끊어서 이동');
assert.equal(firmwareText(withControl({ keys: 'full_limit' })), '끝 목표 · SMOOTH 보고 없음');
assert.equal(firmwareText(withControl({ keys: 'ramp' })), '단계 조향');
assert.equal(firmwareText(status({})), '--');
assert.equal(firmwareText(null), '--');
assert.equal(Object.fromEntries(potView(withControl({ keys: 'full_limit', smooth: true }))[2])['조향 펌웨어'], 'SMOOTH · 한 번에 끊김 없이');
[level, text, rows] = potView(status({ measured_mrad: 100, faults: ['SOFTLIMIT', 'POT_NOISE', 'mock_x'] }));
assert.deepEqual([level, text], ['bad', '가변저항 잡음'], 'a potentiometer fault comes first');
assert.equal(Object.fromEntries(rows)['결함'], '조향 한계 밖, 가변저항 잡음, 기타 결함');
assert.equal(Object.fromEntries(rows)['측정 각도'], '5.7° (100 mrad)');
assert.deepEqual(potView(status({ faults: ['SOFTLIMIT'] })).slice(0, 2), ['warn', '결함 있음']);
assert.deepEqual(potView(status({ faults: [], age_ms: 900 }, { stale: true })).slice(0, 2), ['bad', '상태 오래됨']);
assert.equal(Object.fromEntries(potView(status({ age_ms: 900 }, { stale: true }))[2])['마지막 상태'], '900 ms 전 · 오래됨');
assert.deepEqual(potView(status({ calibrated: false, faults: ['NOCAL'] })).slice(0, 2), ['bad', '보정 없음']);
assert.deepEqual(potView({ ok: true }), ['warn', '조향 장치 없음', []], 'a differential bridge has no steering block');
assert.deepEqual(potView(null), ['bad', '연결 안 됨', []]);
assert.equal(Object.fromEntries(potView(status({}))[2])['측정 각도'], '--');

console.log('PASS: steering potentiometer card rows and chip');
