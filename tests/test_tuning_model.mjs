// The tuning screen's model (static/steer/tuning-model.mjs, UI-12): groups, the state line, the checks before a change
// is sent (range, locked, unchanged, not a number) and the change log rows. No browser. Run: node tests/test_tuning_model.mjs
import assert from 'node:assert/strict';
import { check, clock, describeChanges, fmt, groups, logRows, screenState } from '../static/steer/tuning-model.mjs';

const param = (name, group, value, extra = {}) => ({ name, group, label: name, unit: 'm', min: 1, max: 4, step: 0.1, value,
  default: 2, source: '자리값', editable: group === 'basic', editable_when_stopped: group === 'basic', ...extra });
const view = { configured: true, reachable: true, applicable: true, run_state: 'idle', tuning: { revision: 3 },
  params: [param('lookahead_m', 'basic', 2), param('lateral_gain', 'basic', null), param('corridor_lost_s', 'advanced', 1.5),
           param('operator_timeout_s', 'safety', 0.5)] };

assert.deepEqual(groups(view).map((g) => [g.group, g.params.length]), [['basic', 2], ['advanced', 1], ['safety', 1]]);
assert.deepEqual(groups(null).map((g) => g.params.length), [0, 0, 0]);

assert.deepEqual(screenState(view), ['ok', '적용 가능']);
assert.deepEqual(screenState({ ...view, applicable: false, run_state: 'running' }), ['warn', '적용 불가 · running']);
assert.deepEqual(screenState({ configured: false }), ['', '실행기 설정 없음']);
assert.deepEqual(screenState({ configured: true, reachable: false }), ['bad', '실행기 연결 안 됨']);

// Empty inputs keep the value; an equal value is no change; out of range and not-a-number are refused with a reason.
assert.deepEqual(check(view, { lookahead_m: '', lateral_gain: '  ' }), { changes: {}, problems: [] });
assert.deepEqual(check(view, { lookahead_m: '2.0' }).changes, {}, 'same as now');
assert.deepEqual(check(view, { lookahead_m: '2.5', lateral_gain: '1' }).changes, { lookahead_m: 2.5, lateral_gain: 1 });
let result = check(view, { lookahead_m: '9', lateral_gain: 'abc' });
assert.deepEqual(result.changes, {});
assert.deepEqual(result.problems.map((p) => [p.name, p.text]), [['lookahead_m', '범위 밖(1.0 ~ 4.0 m)'], ['lateral_gain', '수가 아님']]);
result = check(view, { operator_timeout_s: '1.0' });
assert.deepEqual(result.problems.map((p) => p.text), ['바꿀 수 없는 값'], 'locked safety value');
result = check({ ...view, params: view.params.map((p) => ({ ...p, editable: false })) }, { lookahead_m: '3' });
assert.equal(result.problems.length, 1, 'not editable while moving');

assert.deepEqual(describeChanges(view, { lookahead_m: 2.5 }), ['lookahead_m 2.0 → 2.5 m']);
assert.equal(fmt(null, 0.1), '--');
assert.equal(fmt(24, 1), '24');
assert.equal(fmt(0.4, 0.05), '0.40');

const at = new Date(2026, 10, 13, 5, 20, 25).getTime() / 1000;
assert.equal(clock(new Date(at * 1000)), '11-13 05:20:25');
const rows = logRows([
  { at, by: 'abcd1234', ok: true, changes: { lookahead_m: [2, 2.5] }, revision_before: 3, revision_after: 4 },
  { at, by: 'abcd1234', ok: false, error: 'out_of_range', changes: { lookahead_m: [2, 9] }, revision_before: 4, revision_after: 4 },
  { at: null, changes: {} },
], view);
assert.deepEqual(rows[0], ['11-13 05:20:25', 'lookahead_m 2.0 → 2.5', '적용', '3 → 4', 'abcd1234']);
assert.equal(rows[1][2], '거절 · 범위 밖');
assert.deepEqual(rows[2], ['--', '--', '거절 · --', '-- → --', '--']);
assert.deepEqual(logRows(null, view), []);

console.log('PASS: tuning groups, state line, change checks and the change log rows');
