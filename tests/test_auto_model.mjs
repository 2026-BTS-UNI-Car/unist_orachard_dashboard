// The autonomy tab's model (static/steer/auto-model.mjs, UI-08; 2026-11-20 three buttons and one status sentence): which
// buttons are on in each run state, the status sentence, the steps of 준비, when the heartbeat goes, the "끊김" rule and
// the Korean labels. No browser. Run: node tests/test_auto_model.mjs
import assert from 'node:assert/strict';
import {
  HEARTBEAT_MS, STALE_MS, GO_BLOCKER, STOP_REASON, SCALE_HOLD, autoButtons, autoView, goAllowed, heartbeatWanted, modeChip,
  newSession, openBlockers, prepareFailText, prepareSequence, runActive, scaleLine, scaleOffer, scaleText, statusLine, stopWords,
} from '../static/steer/auto-model.mjs';

const now = 1_800_000_000_000;
const reply = (status, extra = {}) => ({ ok: true, configured: true, reachable: true, received_at: now / 1000, status, ...extra });

assert.ok(HEARTBEAT_MS <= 250, 'contract: heartbeat at most every 250 ms');
assert.equal(HEARTBEAT_MS, 100, 'contract revision 1: every 100 ms');
assert.equal(STALE_MS, 1500, 'UI-08: 1.5 s');

// Three buttons that stay in place: [준비, 자율 주행 시작] per run state (멈추기 is always on). 준비 whenever the vehicle
// is not driving; the start only when the run is armed (or paused) and nothing but the operator link is in the way.
const steps = [
  [{ run_state: 'idle', mode: 'MOTOR' }, true, false],
  [{ run_state: 'ended', mode: 'SHADOW' }, true, false],
  [{ run_state: 'ready', mode: 'MOTOR' }, true, false],                    // Arm 전: 준비 again, no start
  [{ run_state: 'armed', go_ready: true, go_blockers: [] }, true, true],
  [{ run_state: 'armed', go_ready: false, go_blockers: ['inputs_not_fresh'] }, true, false],
  [{ run_state: 'armed', go_ready: false, go_blockers: ['corridor_not_acquired'] }, true, false],   // before the corridor: off
  [{ run_state: 'armed', go_ready: false, go_blockers: ['operator_link_not_fresh'] }, true, true],  // the press clears it
  [{ run_state: 'armed', go_ready: false, go_blockers: ['operator_link_not_fresh', 'corridor_not_acquired'] }, true, false],
  [{ run_state: 'armed', go_ready: false, go_blockers: [] }, true, false],  // not ready and no reason given: off
  [{ run_state: 'armed' }, true, true],                                    // an executor without go_ready
  [{ run_state: 'running' }, false, false],
  [{ run_state: 'paused', go_ready: true }, true, true],
  [{ run_state: 'paused', go_ready: false, go_blockers: ['vehicle_moving'] }, true, false],
  [{ run_state: 'latched' }, true, false],                                 // no button of its own: 준비 acknowledges
  [{ run_state: 'flying' }, true, false],
];
for (const [status, prepare, go] of steps) {
  const buttons = autoButtons(reply(status), now);
  assert.deepEqual([buttons.prepare, buttons.go], [prepare, go], JSON.stringify(status));
  assert.equal(buttons.goText, status.run_state === 'paused' ? '다시 출발' : '자율 주행 시작', 'the same button, 다시 출발 after a pause');
}
const off = { prepare: false, go: false, goText: '자율 주행 시작' };
assert.deepEqual(autoButtons({ ok: false, configured: false, received_at: now / 1000 }, now), off, 'no executor');
assert.deepEqual(autoButtons({ ok: false, configured: true, error: 'executor_unreachable' }, now), off);
assert.deepEqual(autoButtons(null, now), off);
assert.deepEqual(autoButtons(reply({ run_state: 'armed', go_ready: true }), now + 1600), off, 'stale: no 준비, no start');
assert.deepEqual(openBlockers({ go_blockers: ['operator_link_not_fresh', 'vehicle_moving'] }), ['vehicle_moving']);
assert.equal(openBlockers({}), null, 'an executor without the list');
assert.equal(goAllowed(null), false);

// Heartbeat only with a fresh status in armed / running / paused; never without an executor.
for (const state of ['armed', 'running', 'paused']) assert.ok(heartbeatWanted(reply({ run_state: state }), now), state);
for (const state of ['idle', 'ready', 'ended', 'latched']) assert.ok(!heartbeatWanted(reply({ run_state: state }), now), state);
assert.ok(!heartbeatWanted(reply({ run_state: 'running' }), now + STALE_MS + 1), 'stale status: no heartbeat');
assert.ok(!heartbeatWanted({ ok: false, configured: false }, now));
assert.ok(runActive(reply({ run_state: 'latched' })) && !runActive(reply({ run_state: 'idle' })));
// An executor set to hold_to_run (contract rev. 2): no go and no heartbeat from this screen.
assert.equal(autoButtons(reply({ run_state: 'armed', start_mode: 'hold_to_run', go_ready: true }), now).go, false);
assert.ok(!heartbeatWanted(reply({ run_state: 'running', start_mode: 'hold_to_run' }), now));
assert.equal(autoButtons(reply({ run_state: 'idle', start_mode: 'hold_to_run' }), now).prepare, true);
assert.match(statusLine(reply({ run_state: 'armed', start_mode: 'hold_to_run' }), now)[1], /이 화면에서 출발 못 함/);

// The status sentence: the state now and what to do next, in the operator's words.
const line = (status, at = now) => statusLine(reply(status), at);
assert.deepEqual(statusLine(null, now), ['', '실행기 확인 중']);
assert.deepEqual(statusLine({ ok: false, configured: false }, now), ['warn', '실행기 설정 없음: 자율주행을 쓸 수 없음']);
assert.deepEqual(statusLine({ ok: false, configured: true, error: 'executor_unreachable' }, now), ['bad', '실행기 연결 안 됨']);
assert.deepEqual(statusLine({ ok: false, configured: true, error: 'executor_token_rejected' }, now), ['bad', '실행기 연결 안 됨: 토큰이 맞지 않음']);
assert.deepEqual(line({ run_state: 'running' }, now + 2000), ['bad', '실행기 응답 끊김: 주행 중이었다면 차는 스스로 선다']);
assert.deepEqual(line({ run_state: 'idle', mode: 'MOTOR' }), ['', '준비를 누르세요']);
assert.deepEqual(line({ run_state: 'idle', mode: 'SHADOW' }), ['', '준비를 누르세요 · SHADOW(차는 안 움직임)'], 'a SHADOW executor says so before the start');
assert.deepEqual(line({ run_state: 'armed', go_ready: false, go_blockers: ['corridor_not_acquired'] }), ['testing', '통로 찾는 중']);
assert.deepEqual(line({ run_state: 'armed', go_ready: false, go_blockers: ['operator_link_not_fresh', 'corridor_not_acquired'] }), ['testing', '통로 찾는 중']);
assert.deepEqual(line({ run_state: 'armed', go_ready: false, go_blockers: ['inputs_not_fresh', 'something_new'] }),
  ['testing', `출발 대기: ${GO_BLOCKER.inputs_not_fresh}, 기타(something_new)`], 'unknown codes are shown, not hidden');
assert.deepEqual(line({ run_state: 'armed', go_ready: true, go_blockers: [], mode: 'MOTOR' }), ['ok', '출발할 수 있음']);
assert.deepEqual(line({ run_state: 'armed', go_ready: false, go_blockers: ['operator_link_not_fresh'] }), ['ok', '출발할 수 있음']);
assert.deepEqual(line({ run_state: 'running', single_row: { distance_m: 12.34, speed_m_s: 0.381 } }), ['ok', '주행 중 · 12.3 m · 0.38 m/s']);
assert.deepEqual(line({ run_state: 'running' }), ['ok', '주행 중']);
assert.deepEqual(line({ run_state: 'ended', stop_reason: 'ROW_END', stop_detail: 'row_end' }), ['ok', '멈춤: 열 끝 도착']);
assert.deepEqual(line({ run_state: 'ended', stop_reason: 'OBSTACLE', stop_detail: 'obstacle_ahead' }), ['warn', '멈춤: 앞 장애물 — 차를 다시 놓고 준비']);
assert.deepEqual(line({ run_state: 'ended', stop_detail: 'operator_end' }), ['', '준비를 누르세요'], 'a run the operator ended is no stop to explain');
assert.deepEqual(line({ run_state: 'ended' }), ['', '준비를 누르세요']);
// Why it stopped stays in the sentence while the run is latched (and after the acknowledgement, until the next start).
const stopped = { stop_reason: 'OPERATOR_OR_BACKSTOP', stop_detail: 'operator_stop' };
assert.deepEqual(line({ run_state: 'latched', ...stopped }), ['bad', '멈춤: 운영자 STOP — 차를 다시 놓고 준비']);
assert.deepEqual(line({ run_state: 'ended', ...stopped }), ['warn', '멈춤: 운영자 STOP — 차를 다시 놓고 준비']);
assert.deepEqual(line({ run_state: 'latched', stop_reason: 'SYSTEM_FAULT', stop_detail: 'new_detail_x' }), ['bad', '멈춤: 시스템 오류 — 차를 다시 놓고 준비'],
  'an unknown detail: the category in words (the code itself is in the folded rows)');
assert.deepEqual(line({ run_state: 'latched' }), ['bad', '멈춤 — 차를 다시 놓고 준비']);
assert.deepEqual(line({ run_state: 'paused', stop_reason: 'OPERATOR_LINK_LOST', stop_detail: 'operator_link_lost', go_ready: true }),
  ['warn', '일시정지: 화면 연결 끊김 — 다시 출발을 누르세요']);
assert.deepEqual(line({ run_state: 'paused', stop_reason: 'PERCEPTION_OR_LOCALISATION', stop_detail: 'stale_lidar', go_ready: false, go_blockers: ['inputs_not_fresh'] }),
  ['warn', `일시정지: LiDAR 입력 끊김 — 출발 대기: ${GO_BLOCKER.inputs_not_fresh}`]);
assert.deepEqual(line({ run_state: 'ready', mode: 'MOTOR' }), ['warn', 'Arm 전 — 준비를 다시 누르세요']);
assert.equal(stopWords({ stop_reason: 'ROW_END' }), '열 끝 도착');
assert.equal(stopWords({}), null);
assert.doesNotMatch(Object.values(GO_BLOCKER).join(' ') + line({ run_state: 'latched', ...stopped })[1], /래치/, 'no latch wording for the operator');

// The folded rows: stop reasons, start conditions and links in Korean with the executor's own codes beside them.
let view = autoView(reply({ run_state: 'paused', stop_reason: 'OPERATOR_LINK_LOST', stop_detail: 'operator_link_lost',
  go_blockers: ['operator_link_not_fresh', 'something_new'], operator_link: { fresh: false, age_s: 2, timeout_s: 0.5 },
  single_row: { lateral_error_m: -0.123, heading_error_deg: 3.21, distance_m: 12.34, speed_m_s: 0 },
  speed: { target_mps: 0.4, limit_mps: 0.2, limit_reason: 'row_end' }, gnss: { state: 'no_fix', h_acc_m: 6.2 } }), now);
assert.deepEqual(view.chip, ['warn', '일시정지']);
assert.equal(view.stale, false);
assert.equal(view.notice, undefined, 'the sentence is statusLine: the view carries no notice');
const rows = Object.fromEntries(view.rows);
assert.equal(rows['정지 이유'], `${STOP_REASON.OPERATOR_LINK_LOST} · 화면 연결 끊김`);
assert.equal(rows['실행기 코드'], 'paused · OPERATOR_LINK_LOST · operator_link_lost');
assert.equal(rows['출발 조건'], `${GO_BLOCKER.operator_link_not_fresh} [operator_link_not_fresh], 기타(something_new) [something_new]`);
assert.equal(rows['횡 · 방향 오차'], '-0.12 m · 3.2°');
assert.equal(rows['속도 제한'], '0.20 m/s · 열 끝 감속');
assert.equal(rows['GNSS'], '미확정 · ±6.2 m');
assert.equal(rows['화면 연결'], '끊김');
assert.equal(rows['진행 거리'], '12.3 m');
view = autoView(reply({ run_state: 'running' }), now + 2000);
assert.deepEqual(view.chip, ['bad', '끊김']);
assert.equal(view.stale, true, 'values are dimmed');
assert.deepEqual(autoView({ ok: false, configured: false }, now).chip, ['', '설정 없음']);
assert.deepEqual(autoView({ ok: false, configured: true, error: 'executor_unreachable' }, now).chip, ['bad', '연결 안 됨']);
assert.equal(autoView(reply({ run_state: 'idle' }), now).rows.find(([name]) => name === 'GNSS')[1], '정보 없음');
assert.deepEqual(autoView(reply({ run_state: 'latched' }), now).chip, ['bad', '멈춤']);

// Contract revision 3: an operator STOP ends the run as OPERATOR_OR_BACKSTOP / operator_stop.
view = autoView(reply({ run_state: 'latched', stop_reason: 'OPERATOR_OR_BACKSTOP', stop_detail: 'operator_stop' }), now);
assert.equal(Object.fromEntries(view.rows)['정지 이유'], '운영자·한도 정지 · 운영자 STOP');
assert.equal(Object.fromEntries(autoView(reply({ run_state: 'ended', stop_reason: 'ROW_END', stop_detail: 'new_detail_x' }), now).rows)['정지 이유'],
             '정상 열 끝 · new_detail_x', 'an unknown detail is shown as sent');
// Revision 3: the bridge took the lease back while armed and waiting -> ready, lease_lost + arm_first. 준비 again.
const lost = { run_state: 'ready', mode: 'MOTOR', go_ready: false, go_blockers: ['lease_lost', 'arm_first'], stop_reason: 'CONTROL_AUTHORITY',
               stop_detail: 'lease_lost', lease_lost: { where: 'armed_wait', bridge_reason: 'steering_status_stale' } };
assert.deepEqual(autoButtons(reply(lost), now), { prepare: true, go: false, goText: '자율 주행 시작' });
assert.deepEqual(statusLine(reply(lost), now), ['warn', '브리지가 권한을 거둠 — 준비를 다시 누르세요']);
assert.equal(Object.fromEntries(autoView(reply(lost), now).rows)['정지 이유'], '조향·구동 권한 부족 · 브리지가 권한을 거둠');
assert.equal(GO_BLOCKER.lease_lost, '조향 브리지 연결이 끊겨 다시 Arm이 필요함');
assert.equal(statusLine(reply({ ...lost, lease_lost: undefined }), now)[1], '브리지가 권한을 거둠 — 준비를 다시 누르세요', 'the blocker alone is enough');

// Session ids: 24 hex characters (contract: 4 to 64), different every time.
const a = newSession(), b = newSession();
assert.match(a, /^[0-9a-f]{24}$/);
assert.notEqual(a, b);

// 준비 in one press. first: the executor's state when the button is pressed. After the start it waits for a status read
// before it sends Arm (the race seen in the SIM stack test: the start reply's status and a poll already in flight still
// said idle, so Arm was never sent).
async function prepareCase({ first = { run_state: 'idle', mode: 'MOTOR' }, startOk = true, startStatus = { run_state: 'idle', mode: 'MOTOR' },
                             statuses = [], armOk = true, waitMs = 3000, answers = {}, abortAfter = null }) {
  const sent = [], reads = [];
  let clock = 0;
  const queue = [first === null ? null : { ok: true, status: first }, ...statuses];
  queue.last = statuses.last;
  const result = await prepareSequence({
    rowLen: 12.5, waitMs, stepMs: 150, now: () => clock, sleep: async (ms) => { clock += ms; },
    aborted: () => abortAfter !== null && sent.length >= abortAfter,
    send: async (action, body) => {
      sent.push([action, body]);
      const scripted = answers[action];
      if (Array.isArray(scripted) && scripted.length) return scripted.shift();
      return { ok: action === 'start' ? startOk : action === 'arm' ? armOk : true, reply: action === 'start' ? { ok: true, status: startStatus } : {} };
    },
    readStatus: async () => { const next = queue.length ? queue.shift() : queue.last; reads.push(next); return next; },
  });
  return { result, sent: sent.map(([action]) => action), body: sent.find(([action]) => action === 'start')?.[1], reads: reads.length, clock };
}
const fresh = (status) => ({ ok: true, status });
await (async () => {
  // MOTOR: the start reply and the first read are still the old state; the second read says ready -> Arm.
  let r = await prepareCase({ statuses: [fresh({ run_state: 'idle', mode: 'MOTOR' }), fresh({ run_state: 'ready', mode: 'MOTOR' })] });
  assert.deepEqual([r.result, r.sent, r.reads], [{ ok: true, step: 'arm', runState: 'ready' }, ['start', 'arm'], 3]);
  assert.deepEqual(r.body, { row_len_m: 12.5 });
  // A failed read (null) is "not yet", not an error.
  r = await prepareCase({ statuses: [null, { ok: false }, fresh({ run_state: 'ready' })] });
  assert.deepEqual([r.result.step, r.sent], ['arm', ['start', 'arm']]);
  // The start reply already says ready: Arm at once, no extra read.
  r = await prepareCase({ startStatus: { run_state: 'ready', mode: 'MOTOR' } });
  assert.deepEqual([r.result.step, r.sent, r.reads], ['arm', ['start', 'arm'], 1]);
  // SHADOW: start arms by itself; no Arm is sent.
  r = await prepareCase({ statuses: [fresh({ run_state: 'armed', mode: 'SHADOW' })] });
  assert.deepEqual([r.result, r.sent], [{ ok: true, step: 'armed', runState: 'armed' }, ['start']]);
  // Never ready: give up after the wait with the last state; no Arm.
  r = await prepareCase({ statuses: Object.assign([], { last: fresh({ run_state: 'idle' }) }), waitMs: 900 });
  assert.deepEqual([r.result, r.sent], [{ ok: false, step: 'wait', runState: 'idle' }, ['start']]);
  assert.equal(r.reads, 7, 'the first read, then 900 ms in 150 ms steps');
  // Start refused: no Arm; the refusal goes with the result.
  r = await prepareCase({ answers: { start: [{ ok: false, reply: null, error: 'gnss_rtk_not_fixed', text: 'RTK FIXED 가 아님' }] } });
  assert.deepEqual([r.result, r.sent, r.reads], [{ ok: false, step: 'start', runState: 'idle', error: 'gnss_rtk_not_fixed', text: 'RTK FIXED 가 아님' }, ['start'], 1]);
  assert.equal(prepareFailText(r.result), '준비 실패(새 run): RTK FIXED 가 아님');
  r = await prepareCase({ startOk: false });
  assert.deepEqual([r.result, r.sent], [{ ok: false, step: 'start', runState: 'idle' }, ['start']]);
  // The executor latched right after the start: no Arm.
  r = await prepareCase({ statuses: [fresh({ run_state: 'latched' })] });
  assert.deepEqual([r.result.step, r.sent], ['latched', ['start']]);
  // Arm refused: reported, not retried.
  r = await prepareCase({ statuses: [fresh({ run_state: 'ready' })], armOk: false });
  assert.deepEqual([r.result, r.sent], [{ ok: false, step: 'arm', runState: 'ready' }, ['start', 'arm']]);
  r = await prepareCase({ statuses: [fresh({ run_state: 'ready' })], answers: { arm: [{ ok: false, error: 'control_in_use' }] } });
  assert.deepEqual(r.result, { ok: false, step: 'arm', runState: 'ready', error: 'control_in_use' });
  assert.equal(prepareFailText(r.result, (code) => `<${code}>`), '준비 실패(Arm): <control_in_use>', 'where and why in one sentence');

  // 2026-11-20: a latch is acknowledged by 준비 itself (no button of its own), then the new run and Arm.
  r = await prepareCase({ first: { run_state: 'latched', stop_reason: 'OPERATOR_OR_BACKSTOP' },
    statuses: [fresh({ run_state: 'ended' }), fresh({ run_state: 'ready' })] });
  assert.deepEqual([r.result, r.sent], [{ ok: true, step: 'arm', runState: 'ready' }, ['ack', 'start', 'arm']]);
  // A run that is still open (armed and waiting, paused, or ready without Arm) is ended first.
  for (const state of ['armed', 'paused', 'ready']) {
    r = await prepareCase({ first: { run_state: state }, statuses: [fresh({ run_state: 'ended' }), fresh({ run_state: 'ready' })] });
    assert.deepEqual([r.result.ok, r.sent], [true, ['end', 'start', 'arm']], state);
  }
  // Latched with the run ended by the latch, then an old state for one read: ack, wait, start.
  r = await prepareCase({ first: { run_state: 'latched' }, statuses: [fresh({ run_state: 'latched' }), fresh({ run_state: 'ended' })],
    startStatus: { run_state: 'ready' } });
  assert.deepEqual(r.sent, ['ack', 'start', 'arm']);
  // The end of an open run leaves the bridge latched: end, ack, start, Arm.
  r = await prepareCase({ first: { run_state: 'ready', go_blockers: ['lease_lost', 'arm_first'] },
    statuses: [fresh({ run_state: 'latched' }), fresh({ run_state: 'ended' })], startStatus: { run_state: 'ready' } });
  assert.deepEqual([r.result.ok, r.sent], [true, ['end', 'ack', 'start', 'arm']]);
  // After an end the bridge is still armed until the vehicle stands: the start is sent again, not reported.
  const stillArmed = () => ({ ok: false, reply: null, error: 'bridge_still_armed', text: '차가 아직 서는 중' });
  r = await prepareCase({ first: { run_state: 'paused' }, statuses: [fresh({ run_state: 'ended' })], startStatus: { run_state: 'ready' },
    answers: { start: [stillArmed(), stillArmed()] } });
  assert.deepEqual([r.result.ok, r.sent], [true, ['end', 'start', 'start', 'start', 'arm']]);
  // ... but not for ever: the whole press has a time limit, and the last refusal is the reason.
  r = await prepareCase({ first: { run_state: 'ended' }, statuses: Object.assign([], { last: fresh({ run_state: 'ended' }) }),
    answers: { start: Array.from({ length: 80 }, stillArmed) } });
  assert.deepEqual([r.result.ok, r.result.step, r.result.error], [false, 'wait', 'bridge_still_armed']);
  assert.ok(r.clock >= 12000 && r.clock < 13000, `gave up after ${r.clock} ms`);
  assert.equal(prepareFailText(r.result), '준비 실패(시간 초과): 차가 아직 서는 중');
  // A start refused because of a latch the status did not show: acknowledged, then started.
  r = await prepareCase({ first: { run_state: 'ended' }, statuses: [fresh({ run_state: 'ended' })], startStatus: { run_state: 'armed' },
    answers: { start: [{ ok: false, error: 'acknowledge_latch_first' }] } });
  assert.deepEqual([r.result.ok, r.sent], [true, ['start', 'ack', 'start']]);
  // A latch that does not clear: two acknowledgements, then the failure names the step.
  r = await prepareCase({ first: { run_state: 'latched' }, statuses: Object.assign([], { last: fresh({ run_state: 'latched' }) }), waitMs: 300 });
  assert.deepEqual([r.result, r.sent], [{ ok: false, step: 'ack', runState: 'latched', error: 'still_latched' }, ['ack', 'ack']]);
  // An end the executor refuses is reported (no start on top of an open run).
  r = await prepareCase({ first: { run_state: 'armed' }, answers: { end: [{ ok: false, error: 'executor_unreachable', text: '실행기 연결 안 됨' }] } });
  assert.deepEqual([r.result.step, r.sent], ['end', ['end']]);
  assert.equal(prepareFailText(r.result), '준비 실패(열린 run 끝내기): 실행기 연결 안 됨');
  // Driving: nothing is sent. No status: nothing is sent.
  r = await prepareCase({ first: { run_state: 'running' } });
  assert.deepEqual([r.result.step, r.sent], ['running', []]);
  r = await prepareCase({ first: null });
  assert.deepEqual([r.result, r.sent], [{ ok: false, step: 'status', runState: null }, []]);
  assert.equal(prepareFailText(r.result), '준비 실패: 실행기 상태를 읽지 못함');
  // 멈추기 while 준비 is on its way: nothing more is sent (no Arm after the start).
  r = await prepareCase({ statuses: [fresh({ run_state: 'ready' })], abortAfter: 1 });
  assert.deepEqual([r.result.step, r.sent], ['aborted', ['start']]);
  r = await prepareCase({ first: { run_state: 'latched' }, statuses: [fresh({ run_state: 'ended' })], abortAfter: 1 });
  assert.deepEqual([r.result.step, r.sent], ['aborted', ['ack']]);
  assert.equal(prepareFailText(r.result), '멈추기를 눌러 준비를 그만둠');
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

// The motor mode chip (2026-11-17): SHADOW and MOTOR in the section head; nothing while the status is missing or stale.
assert.deepEqual(modeChip(reply({ run_state: 'idle', mode: 'SHADOW' }), now), ['', 'SHADOW · 모터 안 씀']);
assert.deepEqual(modeChip(reply({ run_state: 'running', mode: 'MOTOR' }), now), ['warn', 'MOTOR · 차를 몬다']);
assert.deepEqual(modeChip(reply({ run_state: 'idle', mode: 'mock_mode' }), now), ['', 'mock_mode']);
assert.equal(modeChip(reply({ run_state: 'idle' }), now), null, 'no mode reported');
assert.equal(modeChip(reply({ run_state: 'idle', mode: 'MOTOR' }), now + STALE_MS + 1), null, 'stale status');
assert.equal(modeChip({ ok: false, error: 'executor_unreachable' }, now), null);
assert.equal(modeChip(null, now), null);
// autoView keeps the mode row of the details as before.
assert.equal(Object.fromEntries(autoView(reply({ run_state: 'idle', mode: 'SHADOW' }), now).rows)['모드'], 'SHADOW · 차를 몰지 않음');

console.log('PASS: three autonomy buttons per run state, the status sentence, heartbeat states, stale rule, labels, session ids, 준비 in one press (ack, end, start, Arm), the online scale row and its question');
