// The autonomy section's model (static/steer/auto-model.mjs, UI-08): the button it offers in each run state, when the
// heartbeat goes, the "끊김" rule and the Korean labels. No browser. Run: node tests/test_auto_model.mjs
import assert from 'node:assert/strict';
import {
  HEARTBEAT_MS, STALE_MS, GO_BLOCKER, STOP_REASON, SCALE_HOLD, autoView, heartbeatWanted, newSession, prepareRun, primaryAction,
  runActive, scaleLine, scaleOffer, scaleText,
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
assert.equal(rows['정지 이유'], `${STOP_REASON.OPERATOR_LINK_LOST} · 화면 연결 끊김`);
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

// Contract revision 3: an operator STOP ends the run as OPERATOR_OR_BACKSTOP / operator_stop.
view = autoView(reply({ run_state: 'latched', stop_reason: 'OPERATOR_OR_BACKSTOP', stop_detail: 'operator_stop' }), now);
assert.equal(Object.fromEntries(view.rows)['정지 이유'], '운영자·한도 정지 · 운영자 STOP');
assert.equal(Object.fromEntries(autoView(reply({ run_state: 'ended', stop_reason: 'ROW_END', stop_detail: 'new_detail_x' }), now).rows)['정지 이유'],
             '정상 열 끝 · new_detail_x', 'an unknown detail is shown as sent');
// Revision 3: the bridge took the lease back while armed and waiting -> ready, lease_lost + arm_first; Arm again.
const lost = { run_state: 'ready', mode: 'MOTOR', go_ready: false, go_blockers: ['lease_lost', 'arm_first'], stop_reason: 'CONTROL_AUTHORITY',
               stop_detail: 'lease_lost', lease_lost: { where: 'armed_wait', bridge_reason: 'steering_status_stale' } };
assert.deepEqual(primaryAction(reply(lost), now), { text: 'Arm 다시 (브리지가 권한을 거둠)', action: 'arm', enabled: true });
view = autoView(reply(lost), now);
assert.deepEqual(view.notice, ['warn', '브리지가 권한을 거뒀다: Arm을 다시 누른다. 조향 브리지 연결이 끊겨 다시 Arm이 필요함.']);
assert.equal(Object.fromEntries(view.rows)['정지 이유'], '조향·구동 권한 부족 · 브리지가 권한을 거둠');
assert.equal(GO_BLOCKER.lease_lost, '조향 브리지 연결이 끊겨 다시 Arm이 필요함');
assert.equal(primaryAction(reply({ ...lost, lease_lost: undefined }), now).action, 'arm', 'the blocker alone is enough');
assert.equal(primaryAction(reply({ run_state: 'ready', mode: 'MOTOR' }), now).text, 'Arm (모터 권한)', 'a plain ready run');

// Session ids: 24 hex characters (contract: 4 to 64), different every time.
const a = newSession(), b = newSession();
assert.match(a, /^[0-9a-f]{24}$/);
assert.notEqual(a, b);

// "준비" waits for a status read after the start reply before it sends Arm (the race seen in the SIM stack test:
// the start reply's status and a poll already in flight still said idle, so Arm was never sent).
async function prepareCase({ startOk = true, startStatus = { run_state: 'idle', mode: 'MOTOR' }, statuses = [], armOk = true, waitMs = 3000 }) {
  const sent = [], reads = [];
  let clock = 0;
  const result = await prepareRun({
    rowLen: 12.5, waitMs, stepMs: 150, now: () => clock, sleep: async (ms) => { clock += ms; },
    send: async (action, body) => { sent.push([action, body]); return { ok: action === 'start' ? startOk : armOk, reply: action === 'start' ? { ok: true, status: startStatus } : {} }; },
    readStatus: async () => { const next = statuses.length ? statuses.shift() : statuses.last; reads.push(next); return next; },
  });
  return { result, sent: sent.map(([action]) => action), body: sent[0]?.[1], reads: reads.length, clock };
}
const fresh = (status) => ({ ok: true, status });
await (async () => {
  // MOTOR: the start reply and the first read are still the old state; the second read says ready -> Arm.
  let r = await prepareCase({ statuses: [fresh({ run_state: 'idle', mode: 'MOTOR' }), fresh({ run_state: 'ready', mode: 'MOTOR' })] });
  assert.deepEqual([r.result, r.sent, r.reads], [{ ok: true, step: 'arm', runState: 'ready' }, ['start', 'arm'], 2]);
  assert.deepEqual(r.body, { row_len_m: 12.5 });
  // A failed read (null) is "not yet", not an error.
  r = await prepareCase({ statuses: [null, { ok: false }, fresh({ run_state: 'ready' })] });
  assert.deepEqual([r.result.step, r.sent], ['arm', ['start', 'arm']]);
  // The start reply already says ready: Arm at once, no extra read.
  r = await prepareCase({ startStatus: { run_state: 'ready', mode: 'MOTOR' } });
  assert.deepEqual([r.result.step, r.sent, r.reads], ['arm', ['start', 'arm'], 0]);
  // SHADOW: start arms by itself; no Arm is sent.
  r = await prepareCase({ statuses: [fresh({ run_state: 'armed', mode: 'SHADOW' })] });
  assert.deepEqual([r.result, r.sent], [{ ok: true, step: 'armed', runState: 'armed' }, ['start']]);
  // Never ready: give up after the wait with the last state; no Arm.
  r = await prepareCase({ statuses: Object.assign([], { last: fresh({ run_state: 'idle' }) }), waitMs: 900 });
  assert.deepEqual([r.result, r.sent], [{ ok: false, step: 'wait', runState: 'idle' }, ['start']]);
  assert.equal(r.reads, 6, '900 ms in 150 ms steps');
  // Start refused, or the executor latched: no Arm.
  r = await prepareCase({ startOk: false });
  assert.deepEqual([r.result, r.sent, r.reads], [{ ok: false, step: 'start', runState: null }, ['start'], 0]);
  r = await prepareCase({ statuses: [fresh({ run_state: 'latched' })] });
  assert.deepEqual([r.result.step, r.sent], ['latched', ['start']]);
  // Arm refused: reported, not retried.
  r = await prepareCase({ statuses: [fresh({ run_state: 'ready' })], armOk: false });
  assert.deepEqual([r.result, r.sent], [{ ok: false, step: 'arm', runState: 'ready' }, ['start', 'arm']]);
})();

// PW-10 (contract revision 6): the online scale row and the question after a run.
const scale = (extra = {}) => ({ enabled: true, run_id: 3, value: 0.8512, window_s: 20, target_s: 20, min_s: 10, frozen: false,
  hold: null, mismatch: false, speed_scale: 1, prior_scale: 1, offer: null, ...extra });
assert.equal(scaleText(scale()), '0.851 (최근 20/20 s)');
assert.equal(scaleText(scale({ value: null, window_s: 3.4 })), '모으는 중 3/10 s');
assert.equal(scaleText(scale({ value: null, window_s: 3.4, hold: 'lidar_unmatched' })), `모으는 중 3/10 s · 멈춤: ${SCALE_HOLD.lidar_unmatched}`);
assert.equal(scaleText(scale({ frozen: true, hold: 'lidar_stale' })), `0.851 (최근 20/20 s) · 멈춤: ${SCALE_HOLD.lidar_stale}`);
assert.equal(scaleText(scale({ frozen: true, hold: 'something_new', mismatch: true })), '0.851 (최근 20/20 s) · 멈춤: 기타(something_new) · 표 어긋남');
assert.equal(scaleText(scale({ enabled: false })), '꺼짐');
assert.equal(scaleLine(reply({ run_state: 'running', online_scale: scale({ frozen: true, hold: 'lidar_unmatched' }) }), now),
             `0.851 (최근 20/20 s) · 멈춤: ${SCALE_HOLD.lidar_unmatched}`);
assert.equal(scaleLine(reply({ run_state: 'idle' }), now), null, 'no block (differential or monitor-less executor): no line');
assert.equal(scaleLine(reply({ run_state: 'running', online_scale: scale() }), now + STALE_MS + 1), null, 'stale: hidden');
assert.equal(scaleLine({ ok: false, configured: false }, now), null);
assert.ok(!Object.fromEntries(autoView(reply({ run_state: 'running', online_scale: scale() }), now).rows)['온라인 배율'],
          'the line is outside the folded details, not a details row');

const offer = { run_id: 3, value: 0.8512, window_s: 20, end_reason: 'row_end', speed_scale_now: 1, speed_scale_new: 0.851, ok: true, why_not: null };
let ask = scaleOffer(reply({ run_state: 'ended', online_scale: scale({ offer }) }));
assert.deepEqual([ask.runId, ask.canAccept, ask.from, ask.to], [3, true, 1, 0.851]);
assert.match(ask.text, /run 3 끝: 온라인 배율 0\.851 \(최근 20 s\)\. 전체 배율\(k_field\)을 1\.000 → 0\.851로 저장할까요\?/);
ask = scaleOffer(reply({ run_state: 'idle', online_scale: scale({ offer: { ...offer, value: null, ok: false, why_not: 'no_scale' } }) }));
assert.equal(ask.canAccept, false);
assert.match(ask.text, /온라인 배율 없음\. 전체 배율로 저장할 수 없다: 배율이 아직 없다/);
assert.equal(scaleOffer(reply({ run_state: 'running', online_scale: scale({ offer }) })), null, 'never while a run drives');
assert.equal(scaleOffer(reply({ run_state: 'ended', online_scale: scale() })), null);
assert.equal(scaleOffer(reply({ run_state: 'ended' })), null);
assert.equal(scaleOffer(null), null);

console.log('PASS: autonomy button per run state, heartbeat states, stale rule, labels, session ids, the prepare sequence, the online scale row and its question');
