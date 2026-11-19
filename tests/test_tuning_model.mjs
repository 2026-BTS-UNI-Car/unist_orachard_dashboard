// The tuning screen's model (static/steer/tuning-model.mjs, UI-12): groups, the state line, why it can not change,
// the checks before a change is sent (the executor's ranges, locked, unchanged, not a number), preset names, reply
// words and the change log rows. The view is built from the real executor status sample
// (tests/fixtures/executor_status_front_steer.json) the way dashboard_server/steer_tuning.py builds it.
// No browser. Run: node tests/test_tuning_model.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { check, clock, describeChanges, fmt, groups, logRows, presetLine, presetNameOk, replyText, screenState, whyNot,
         withUnit } from '../static/steer/tuning-model.mjs';

const sample = JSON.parse(fs.readFileSync(new URL('./fixtures/executor_status_front_steer.json', import.meta.url), 'utf8'));
const t = sample.tuning;
const view = {
  configured: true, reachable: true, available: true, applicable: true, why_not: null, run_state: 'idle',
  tuning: { preset: t.preset, revision: t.revision, modified: t.modified, presets: t.presets, writable: t.writable, unsaved: t.unsaved },
  params: [
    ...t.basic.map((row) => ({ name: row.name, group: 'basic', label: row.name, unit: row.unit, min: row.low, max: row.high,
                               step: 0.01, value: row.value, editable: true, editable_when_stopped: true })),
    ...['advanced', 'locked'].flatMap((group) => Object.entries(t.values[group]).map(([name, value]) => ({
      name, group, label: name, unit: '', min: null, max: null, step: null, value, editable: false, editable_when_stopped: false }))),
  ],
};

assert.deepEqual(groups(view).map((g) => [g.group, g.params.length]),
                 [['basic', 8], ['advanced', Object.keys(t.values.advanced).length], ['locked', Object.keys(t.values.locked).length]]);
assert.deepEqual(groups(null).map((g) => g.params.length), [0, 0, 0]);

assert.deepEqual(screenState(view), ['ok', '변경 가능']);
assert.deepEqual(screenState({ ...view, applicable: false }), ['warn', '지금은 변경 불가']);
assert.deepEqual(screenState({ ...view, available: false, applicable: false }), ['warn', '조정 값 없음']);
assert.deepEqual(screenState({ configured: false }), ['', '실행기 설정 없음']);
assert.deepEqual(screenState({ configured: true, reachable: false }), ['bad', '실행기 연결 안 됨']);
assert.equal(whyNot(view), '');
assert.equal(whyNot({ ...view, applicable: false, why_not: 'tuning_locked_while_armed', run_state: 'armed' }),
  '실행기가 조종 권한을 쥐고 있어 바꿀 수 없습니다. 주행을 끝내고 차가 멈춘 뒤에 바꾸세요. 지금 실행기 상태는 출발 대기입니다.');
assert.equal(whyNot({ ...view, applicable: false, why_not: 'new_reason_x', run_state: 'new_state_x' }),
  '알려 온 이유는 new_reason_x입니다. 주행을 끝내고 차가 멈춘 뒤에 바꾸세요.', 'an unknown reason is named in a sentence; an unknown state is left out');
assert.equal(whyNot({ configured: true, reachable: false, error: 'executor_unreachable' }), '실행기에서 조정 값을 읽을 수 없습니다. 실행기에 연결하지 못했습니다.');
assert.equal(whyNot({ configured: true, reachable: false }), '실행기에서 조정 값을 읽을 수 없습니다. 실행기가 응답하지 않습니다.');
assert.match(whyNot({ ...view, applicable: false, why_not: 'tuning_locked_vehicle_moving' }), /차가 움직이고 있어/);
assert.match(whyNot({ ...view, applicable: false, why_not: 'something_new' }), /^알려 온 이유는 something_new입니다\./, 'an unknown reason is shown, named in a sentence');
assert.equal(presetLine(view), '프리셋 default · 판 0');
assert.equal(presetLine({ ...view, tuning: { ...view.tuning, preset: 'default*', revision: 2, unsaved: true } }), '프리셋 default* · 판 2 · 파일에 저장 전');
assert.equal(presetLine({ available: false }), '판 --');

// Empty inputs keep the value; an equal value is no change; out of range and not-a-number are refused with a reason.
assert.deepEqual(check(view, { k_d: '', target_speed_m_s: '  ' }), { changes: {}, problems: [] });
assert.deepEqual(check(view, { target_speed_m_s: '0.40' }).changes, {}, 'same as now');
assert.deepEqual(check(view, { target_speed_m_s: '0.35', k_psi: '1.2' }).changes, { target_speed_m_s: 0.35, k_psi: 1.2 });
let result = check(view, { target_speed_m_s: '0.6', k_d: 'abc' });
assert.deepEqual(result.changes, {});
assert.deepEqual(result.problems.map((p) => [p.name, p.text]), [['target_speed_m_s', '값이 범위 0.20 ~ 0.50 m/s 밖입니다.'], ['k_d', '값이 숫자가 아닙니다.']]);
assert.deepEqual(check(view, { k_d: '3.5' }).problems.map((p) => p.text), ['값이 범위 0.00 ~ 3.00 밖입니다.'], 'no unit: no trailing space');
result = check(view, { max_speed_m_s: '0.3' });
assert.deepEqual(result.problems.map((p) => p.text), ['값은 바꿀 수 없습니다.'], 'locked value');
result = check({ ...view, params: view.params.map((p) => ({ ...p, editable: false })) }, { k_d: '2' });
assert.equal(result.problems.length, 1, 'not editable while the executor says no');

assert.deepEqual(describeChanges(view, { target_speed_m_s: 0.35 }), ['target_speed_m_s 0.40 → 0.35 m/s']);
assert.deepEqual(describeChanges(view, { k_d: 1.2 }), ['k_d 1.00 → 1.20']);
assert.equal(fmt(null, 0.1), '--');
assert.equal(fmt(24, 1), '24');
assert.equal(fmt(0.4, 0.05), '0.40');
assert.equal(fmt(t.values.advanced.speed_table), '25→0.239, 80→0.709, 90→0.715, 100→0.899');
assert.equal(fmt(70), '70', 'an integer value without a step');
assert.equal(withUnit('--', 'm'), '--');

assert.ok(presetNameOk('wet_0.3') && presetNameOk('A-b'));
for (const bad of ['', 'default', 'has space', 'x'.repeat(41), '젖음']) assert.equal(presetNameOk(bad), false, bad);

assert.deepEqual(replyText(view, 200, { ok: true, action: 'set', changes: [{ key: 'k_d', old: 1, new: 1.2 }], tuning: { revision: 2 } }),
                 ['ok', '값을 적용했습니다. 판 2 · k_d 1.00 → 1.20']);
assert.deepEqual(replyText(view, 200, { ok: true, action: 'load_preset', changes: [], tuning: { revision: 3 } }), ['ok', '프리셋 값을 적용했습니다. 판 3']);
assert.deepEqual(replyText(view, 400, { ok: false, error: 'invalid_params', problems: ['k_d: breaks a rule', 'x'] }),
                 ['bad', '요청이 거절됐습니다. 실행기가 값을 받지 않았습니다. k_d: breaks a rule / x']);
assert.deepEqual(replyText(view, 409, { ok: false, error: 'tuning_locked_while_running' }), ['bad', '요청이 거절됐습니다. 주행이 끝나지 않아 바꿀 수 없습니다.']);
assert.deepEqual(replyText(view, 502, null), ['bad', '요청이 거절됐습니다. 서버가 HTTP 502 오류로 답했습니다.']);

const at = new Date(2026, 10, 13, 5, 20, 25).getTime() / 1000;
assert.equal(clock(new Date(at * 1000)), '11-13 05:20:25');
const rows = logRows([
  { at, by: 'abcd1234', action: 'set', ok: true, changes: { k_d: [1, 1.2] }, revision_before: 3, revision_after: 4 },
  { at, by: 'abcd1234', action: 'set', ok: false, error: 'out_of_range', changes: { k_d: [1, 9] }, revision_before: 4, revision_after: 4 },
  { at, by: 'abcd1234', action: 'load_preset', target: 'slow_030', ok: true, changes: { target_speed_m_s: [0.4, 0.3] }, revision_before: 4, revision_after: 5 },
  { at, by: 'abcd1234', action: 'save', ok: false, error: 'tuning_not_writable', changes: {}, revision_before: 5, revision_after: 5 },
  { at: null, changes: {} },
], view);
assert.deepEqual(rows[0], ['11-13 05:20:25', 'k_d 1.00 → 1.20', '적용', '3 → 4', 'abcd1234']);
assert.equal(rows[1][2], '거절 · 범위를 벗어난 값입니다.');
assert.equal(rows[2][1], '프리셋 불러옴 slow_030, target_speed_m_s 0.40 → 0.30');
assert.deepEqual(rows[3].slice(1, 3), ['파일에 저장', '거절 · 실행기가 기본 파일을 쓰고 있어 저장할 수 없습니다.']);
assert.deepEqual(rows[4], ['--', '--', '거절 · --', '-- → --', '--']);
assert.deepEqual(logRows(null, view), []);

console.log('PASS: tuning bundle from the executor sample, state line, checks, presets, replies and the change log rows');
