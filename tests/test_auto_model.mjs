// The autonomy section's model (static/steer/auto-model.mjs, UI-08): the button it offers in each run state, when the
// heartbeat goes, the "끊김" rule and the Korean labels. No browser. Run: node tests/test_auto_model.mjs
import assert from 'node:assert/strict';
import {
  HEARTBEAT_MS, STALE_MS, GO_BLOCKER, STOP_REASON, autoView, heartbeatWanted, newSession, primaryAction, runActive,
} from '../static/steer/auto-model.mjs';

const now = 1_800_000_000_000;
const reply = (status, extra = {}) => ({ ok: true, configured: true, reachable: true, received_at: now / 1000, status, ...extra });

assert.ok(HEARTBEAT_MS <= 250, 'contract: heartbeat at most every 250 ms');
assert.equal(HEARTBEAT_MS, 100, 'contract revision 1: every 100 ms');
assert.equal(STALE_MS, 1500, 'UI-08: 1.5 s');

// The button follows the run state; one press starts (go), never a hold.
const steps = [
  [{ run_state: 'idle', mode: 'MOTOR' }, 'prepare', true],
  [{ run_state: 'ended', mode: 'SHADOW' }, 'prepare', true],
  [{ run_state: 'ready', mode: 'MOTOR' }, 'arm', true],
  [{ run_state: 'armed', go_ready: true }, 'go', true],
  [{ run_state: 'armed', go_ready: false, go_blockers: ['inputs_not_fresh'] }, 'go', false],
  [{ run_state: 'armed' }, 'go', true],                                   // an executor without go_ready
  [{ run_state: 'running' }, null, false],
  [{ run_state: 'paused', go_ready: true }, 'go', true],
  [{ run_state: 'latched' }, 'ack', true],
  [{ run_state: 'flying' }, null, false],
];
for (const [status, action, enabled] of steps) {
  const button = primaryAction(reply(status), now);
  assert.deepEqual([button.action, button.enabled], [action, enabled], JSON.stringify(status));
  assert.ok(button.text);
}
assert.equal(primaryAction({ ok: false, configured: false, received_at: now / 1000 }, now).text, '실행기 설정 없음');
assert.equal(primaryAction({ ok: false, configured: true, error: 'executor_unreachable' }, now).text, '실행기 연결 안 됨');
assert.equal(primaryAction(null, now).enabled, false);
assert.equal(primaryAction(reply({ run_state: 'armed', go_ready: true }), now + 1600).enabled, false, 'stale: no start');

// Heartbeat only with a fresh status in armed / running / paused; never without an executor.
for (const state of ['armed', 'running', 'paused']) assert.ok(heartbeatWanted(reply({ run_state: state }), now), state);
for (const state of ['idle', 'ready', 'ended', 'latched']) assert.ok(!heartbeatWanted(reply({ run_state: state }), now), state);
assert.ok(!heartbeatWanted(reply({ run_state: 'running' }), now + STALE_MS + 1), 'stale status: no heartbeat');
assert.ok(!heartbeatWanted({ ok: false, configured: false }, now));
assert.ok(runActive(reply({ run_state: 'latched' })) && !runActive(reply({ run_state: 'idle' })));
// An executor set to hold_to_run (contract rev. 2): no go and no heartbeat from this screen.
assert.equal(primaryAction(reply({ run_state: 'armed', start_mode: 'hold_to_run', go_ready: true }), now).enabled, false);
assert.ok(!heartbeatWanted(reply({ run_state: 'running', start_mode: 'hold_to_run' }), now));
assert.equal(primaryAction(reply({ run_state: 'idle', start_mode: 'hold_to_run' }), now).action, 'prepare');

// The view: stop reasons, blockers and links in Korean; unknown codes are shown, not hidden.
let view = autoView(reply({ run_state: 'paused', stop_reason: 'OPERATOR_LINK_LOST', stop_detail: 'operator_link_lost',
  go_blockers: ['operator_link_not_fresh', 'something_new'], operator_link: { fresh: false, age_s: 2, timeout_s: 0.5 },
  single_row: { lateral_error_m: -0.123, heading_error_deg: 3.21, distance_m: 12.34, speed_m_s: 0 },
  speed: { target_mps: 0.4, limit_mps: 0.2, limit_reason: 'row_end' }, gnss: { state: 'no_fix', h_acc_m: 6.2 } }), now);
assert.deepEqual(view.chip, ['warn', '일시정지']);
assert.equal(view.stale, false);
assert.match(view.notice[1], new RegExp(GO_BLOCKER.operator_link_not_fresh));
assert.match(view.notice[1], /기타\(something_new\)/);
const rows = Object.fromEntries(view.rows);
assert.equal(rows['정지 이유'], `${STOP_REASON.OPERATOR_LINK_LOST} · operator_link_lost`);
assert.equal(rows['횡 · 방향 오차'], '-0.12 m · 3.2°');
assert.equal(rows['속도 제한'], '0.20 m/s · 열 끝 감속');
assert.equal(rows['GNSS'], '미확정 · ±6.2 m');
assert.equal(rows['화면 연결'], '끊김');
assert.equal(rows['진행 거리'], '12.3 m');
view = autoView(reply({ run_state: 'running', operator_link: { fresh: true, age_s: 0.05, timeout_s: 0.5 } }), now);
assert.match(view.notice[1], /0\.5초 뒤 차가 선다/);
view = autoView(reply({ run_state: 'running' }), now + 2000);
assert.deepEqual(view.chip, ['bad', '끊김']);
assert.equal(view.stale, true, 'values are dimmed');
assert.match(autoView({ ok: false, configured: false }, now).notice[1], /설정 없음/);
assert.match(autoView({ ok: false, configured: true, error: 'executor_unreachable' }, now).notice[1], /연결 안 됨/);
assert.equal(autoView(reply({ run_state: 'idle' }), now).rows.find(([name]) => name === 'GNSS')[1], '정보 없음');

// Session ids: 24 hex characters (contract: 4 to 64), different every time.
const a = newSession(), b = newSession();
assert.match(a, /^[0-9a-f]{24}$/);
assert.notEqual(a, b);

console.log('PASS: autonomy button per run state, heartbeat states, stale rule, labels and session ids');
