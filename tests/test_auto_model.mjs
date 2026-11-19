// The autonomy tab's model (static/steer/auto-model.mjs, UI-08; 2026-11-20 three buttons and one status sentence): which
// buttons are on in each run state, the status sentence, the steps of 준비, when the heartbeat goes, the "끊김" rule and
// the Korean labels. No browser. Run: node tests/test_auto_model.mjs
import assert from 'node:assert/strict';
import {
  HEARTBEAT_MS, STALE_MS, EXEC_ERROR, GO_BLOCKER, SCALE_WHY_NOT, START_ERROR, STOP_CLAUSE, STOP_DETAIL, STOP_REASON, SCALE_HOLD, autoButtons, autoView, goAllowed, heartbeatWanted, modeChip,
  newSession, openBlockers, prepareFailText, prepareSequence, speedChoice, speedLabel, speedNote, SPEED_KEY, runActive, scaleLine, scaleOffer, scaleText, statusLine, stopClause, stopWords,
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
assert.match(statusLine(reply({ run_state: 'armed', start_mode: 'hold_to_run' }), now)[1], /이 화면에서는 출발할 수 없습니다/);

// The status sentence: the state now and what to do next, in the operator's words.
const line = (status, at = now) => statusLine(reply(status), at);
assert.deepEqual(statusLine(null, now), ['', '자율주행 실행기를 확인하고 있습니다.']);
assert.deepEqual(statusLine({ ok: false, configured: false }, now), ['warn', '자율주행 실행기가 설정되어 있지 않아 자율주행을 쓸 수 없습니다.']);
assert.deepEqual(statusLine({ ok: false, configured: true, error: 'executor_unreachable' }, now), ['bad', '자율주행 실행기에 연결하지 못했습니다.']);
assert.deepEqual(statusLine({ ok: false, configured: true, error: 'executor_token_rejected' }, now), ['bad', '자율주행 실행기에 연결하지 못했습니다. 토큰이 맞지 않습니다.']);
assert.deepEqual(line({ run_state: 'running' }, now + 2000), ['bad', '자율주행 실행기의 응답이 끊겼습니다. 주행 중이었다면 차는 스스로 멈춥니다.']);
assert.deepEqual(line({ run_state: 'idle', mode: 'MOTOR' }), ['', '준비를 누르세요.']);
assert.deepEqual(line({ run_state: 'idle', mode: 'SHADOW' }), ['', '준비를 누르세요. 연습 모드라서 차는 움직이지 않습니다.'], 'a SHADOW executor says so before the start');
assert.deepEqual(line({ run_state: 'armed', go_ready: true, go_blockers: [], mode: 'SHADOW' }), ['ok', '출발할 수 있습니다. 연습 모드라서 차는 움직이지 않습니다.']);
assert.deepEqual(line({ run_state: 'armed', go_ready: false, go_blockers: ['corridor_not_acquired'] }), ['testing', '통로를 찾고 있습니다.']);
assert.deepEqual(line({ run_state: 'armed', go_ready: false, go_blockers: ['operator_link_not_fresh', 'corridor_not_acquired'] }), ['testing', '통로를 찾고 있습니다.']);
assert.deepEqual(line({ run_state: 'armed', go_ready: false, go_blockers: ['inputs_not_fresh', 'corridor_not_acquired', 'vehicle_moving'] }),
  ['testing', '센서 입력을 기다리고 있습니다. 통로를 찾고 있습니다.'], 'at most two sentences; every condition is in the folded rows');
assert.deepEqual(line({ run_state: 'armed', go_ready: false, go_blockers: ['inputs_not_fresh', 'something_new'] }),
  ['testing', `${GO_BLOCKER.inputs_not_fresh} 출발 조건이 아직 맞지 않습니다.`], 'an unknown code is a plain sentence here; the code itself is in the folded rows');
assert.deepEqual(line({ run_state: 'armed', go_ready: false }), ['testing', '출발 조건을 확인하고 있습니다.']);
assert.deepEqual(line({ run_state: 'armed', go_ready: true, go_blockers: [], mode: 'MOTOR' }), ['ok', '출발할 수 있습니다.']);
assert.deepEqual(line({ run_state: 'armed', go_ready: false, go_blockers: ['operator_link_not_fresh'] }), ['ok', '출발할 수 있습니다.']);
// Screen text is whole sentences: no telegraph endings, no dash or arrow chains, no developer words.
for (const text of [...Object.values(GO_BLOCKER), ...Object.values(START_ERROR), ...Object.values(EXEC_ERROR), ...Object.values(SCALE_WHY_NOT)]) {
  assert.match(text, /(니다|세요)\.$/, text);
  assert.doesNotMatch(text, /—|→|\brun\b|Arm|조종권|래치/, text);
}
assert.deepEqual(line({ run_state: 'running', single_row: { distance_m: 12.34, speed_m_s: 0.381 } }), ['ok', '주행 중 · 12.3 m · 0.38 m/s']);
assert.deepEqual(line({ run_state: 'running' }), ['ok', '주행 중']);
assert.deepEqual(line({ run_state: 'ended', stop_reason: 'ROW_END', stop_detail: 'row_end' }), ['ok', '열 끝에 도착해 멈췄습니다.']);
assert.deepEqual(line({ run_state: 'ended', stop_reason: 'OBSTACLE', stop_detail: 'obstacle_ahead' }), ['warn', '앞에 장애물이 있어 멈췄습니다. 다시 하려면 준비를 누르세요.']);
assert.deepEqual(line({ run_state: 'ended', stop_detail: 'operator_end' }), ['', '준비를 누르세요.'], 'a run the operator ended is no stop to explain');
assert.deepEqual(line({ run_state: 'ended' }), ['', '준비를 누르세요.']);
// Why it stopped stays in the sentence while the run is latched (and after the acknowledgement, until the next start).
const stopped = { stop_reason: 'OPERATOR_OR_BACKSTOP', stop_detail: 'operator_stop' };
assert.deepEqual(line({ run_state: 'latched', ...stopped }), ['bad', 'STOP을 눌러 멈췄습니다. 다시 하려면 준비를 누르세요.']);
assert.deepEqual(line({ run_state: 'ended', ...stopped }), ['warn', 'STOP을 눌러 멈췄습니다. 다시 하려면 준비를 누르세요.']);
assert.deepEqual(line({ run_state: 'latched', stop_reason: 'SYSTEM_FAULT', stop_detail: 'new_detail_x' }), ['bad', '시스템 오류로 멈췄습니다. 다시 하려면 준비를 누르세요.'],
  'an unknown detail: the category in words (the code itself is in the folded rows)');
assert.deepEqual(line({ run_state: 'latched', stop_reason: 'NEW_CATEGORY_X' }), ['bad', '알 수 없는 이유로 멈췄습니다. 다시 하려면 준비를 누르세요.'],
  'an unknown category: said so, its code is in the folded rows');
assert.deepEqual(line({ run_state: 'latched' }), ['bad', '멈췄습니다. 다시 하려면 준비를 누르세요.']);
assert.deepEqual(line({ run_state: 'paused', stop_reason: 'OPERATOR_LINK_LOST', stop_detail: 'operator_link_lost', go_ready: true }),
  ['warn', '화면 연결이 끊겨 잠시 멈췄습니다. 다시 출발을 누르세요.']);
assert.deepEqual(line({ run_state: 'paused', stop_reason: 'PERCEPTION_OR_LOCALISATION', stop_detail: 'stale_lidar', go_ready: false, go_blockers: ['inputs_not_fresh'] }),
  ['warn', `LiDAR 입력이 끊겨 잠시 멈췄습니다. ${GO_BLOCKER.inputs_not_fresh}`]);
assert.deepEqual(line({ run_state: 'paused', go_ready: true }), ['warn', '잠시 멈췄습니다. 다시 출발을 누르세요.']);
assert.deepEqual(line({ run_state: 'ready', mode: 'MOTOR' }), ['warn', '준비가 끝나지 않았습니다. 준비를 다시 누르세요.']);
assert.deepEqual(line({ run_state: 'something_else' }), ['', '실행기 상태를 알 수 없습니다.']);
// Every stop reason the tables know reads as one clause of the sentence; the tile keeps the short label.
for (const code of Object.keys(STOP_DETAIL)) assert.ok(STOP_CLAUSE[code], `clause of ${code}`);
for (const code of Object.keys(STOP_REASON).filter((c) => c !== 'UNCLASSIFIED')) assert.ok(STOP_CLAUSE[code], `clause of ${code}`);
assert.equal(stopClause({ stop_reason: 'ROW_END', stop_detail: 'new_detail_x' }), '열 끝에 도착해');
assert.equal(stopClause({ stop_detail: 'operator_stop' }), 'STOP을 눌러');
assert.equal(stopClause({}), null);
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
assert.equal(rows['정지 이유'], '화면 연결 끊김', 'the category and the detail in the same words are said once');
assert.equal(rows['실행기 코드'], 'paused · OPERATOR_LINK_LOST · operator_link_lost');
assert.equal(rows['출발 조건'], `${GO_BLOCKER.operator_link_not_fresh} [operator_link_not_fresh] 알 수 없는 조건입니다. [something_new]`,
             'unknown codes are shown, not hidden');
assert.equal(rows['횡 · 방향 오차'], '-0.12 m · 3.2°');
assert.equal(rows['속도 제한'], '0.20 m/s · 열 끝 감속');
assert.equal(rows['GPS'], '미확정 · ±6.2 m');
assert.equal(rows['화면 연결'], '끊김');
assert.equal(rows['진행 거리'], '12.3 m');
view = autoView(reply({ run_state: 'running' }), now + 2000);
assert.deepEqual(view.chip, ['bad', '끊김']);
assert.equal(view.stale, true, 'values are dimmed');
assert.deepEqual(autoView({ ok: false, configured: false }, now).chip, ['', '설정 없음']);
assert.deepEqual(autoView({ ok: false, configured: true, error: 'executor_unreachable' }, now).chip, ['bad', '연결 안 됨']);
assert.equal(autoView(reply({ run_state: 'idle' }), now).rows.find(([name]) => name === 'GPS')[1], '정보 없음');
assert.deepEqual(autoView(reply({ run_state: 'latched' }), now).chip, ['bad', '멈춤']);

// Contract revision 3: an operator STOP ends the run as OPERATOR_OR_BACKSTOP / operator_stop.
view = autoView(reply({ run_state: 'latched', stop_reason: 'OPERATOR_OR_BACKSTOP', stop_detail: 'operator_stop' }), now);
assert.equal(Object.fromEntries(view.rows)['정지 이유'], 'STOP 또는 거리 한도 · STOP 누름');
assert.equal(Object.fromEntries(autoView(reply({ run_state: 'ended', stop_reason: 'ROW_END', stop_detail: 'new_detail_x' }), now).rows)['정지 이유'],
             '열 끝 도착 · new_detail_x', 'an unknown detail is shown as sent');
// Revision 3: the bridge took the lease back while armed and waiting -> ready, lease_lost + arm_first. 준비 again.
const lost = { run_state: 'ready', mode: 'MOTOR', go_ready: false, go_blockers: ['lease_lost', 'arm_first'], stop_reason: 'CONTROL_AUTHORITY',
               stop_detail: 'lease_lost', lease_lost: { where: 'armed_wait', bridge_reason: 'steering_status_stale' } };
assert.deepEqual(autoButtons(reply(lost), now), { prepare: true, go: false, goText: '자율 주행 시작' });
assert.deepEqual(statusLine(reply(lost), now), ['warn', '브리지가 조종 권한을 거두었습니다. 준비를 다시 누르세요.']);
assert.equal(Object.fromEntries(autoView(reply(lost), now).rows)['정지 이유'], '조향·구동 응답 부족 · 조종 권한 거둠');
assert.equal(GO_BLOCKER.lease_lost, '브리지가 조종 권한을 거두었습니다. 준비를 다시 누르세요.');
assert.equal(statusLine(reply({ ...lost, lease_lost: undefined }), now)[1], '브리지가 조종 권한을 거두었습니다. 준비를 다시 누르세요.', 'the blocker alone is enough');

// Session ids: 24 hex characters (contract: 4 to 64), different every time.
const a = newSession(), b = newSession();
assert.match(a, /^[0-9a-f]{24}$/);
assert.notEqual(a, b);

// 준비 in one press. first: the executor's state when the button is pressed. After the start it waits for a status read
// before it sends Arm (the race seen in the SIM stack test: the start reply's status and a poll already in flight still
// said idle, so Arm was never sent).
async function prepareCase({ first = { run_state: 'idle', mode: 'MOTOR' }, startOk = true, startStatus = { run_state: 'idle', mode: 'MOTOR' },
                             statuses = [], armOk = true, waitMs = 3000, answers = {}, abortAfter = null, armShows = true }) {
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
    readStatus: async () => {
      // after an accepted Arm the executor's status says armed (armShows false: it keeps its old answer)
      const armedNow = armShows && armOk && sent.some(([action]) => action === 'arm');
      const next = armedNow ? { ok: true, status: { run_state: 'armed', mode: 'MOTOR' } } : queue.length ? queue.shift() : queue.last;
      reads.push(next); return next;
    },
  });
  return { result, sent: sent.map(([action]) => action), body: sent.find(([action]) => action === 'start')?.[1], reads: reads.length, clock };
}
const fresh = (status) => ({ ok: true, status });
await (async () => {
  // MOTOR: the start reply and the first read are still the old state; the second read says ready -> Arm.
  let r = await prepareCase({ statuses: [fresh({ run_state: 'idle', mode: 'MOTOR' }), fresh({ run_state: 'ready', mode: 'MOTOR' })] });
  assert.deepEqual([r.result, r.sent, r.reads], [{ ok: true, step: 'arm', runState: 'ready' }, ['start', 'arm'], 4],
    'one more read after the Arm: the step ends when the status shows armed');
  assert.deepEqual(r.body, { row_len_m: 12.5 });
  // The status keeps saying ready after the accepted Arm: the step waits ARM_SHOWN_MS (2 s in 150 ms steps), then ends ok.
  r = await prepareCase({ statuses: [fresh({ run_state: 'ready', mode: 'MOTOR' })], armShows: false });
  r.statusesLast = null;
  assert.deepEqual([r.result.ok, r.result.step, r.sent], [true, 'arm', ['start', 'arm']]);
  assert.ok(r.clock >= 2000 && r.clock < 2000 + 3 * 150, `waited about ARM_SHOWN_MS (${r.clock} ms)`);
  // A failed read (null) is "not yet", not an error.
  r = await prepareCase({ statuses: [null, { ok: false }, fresh({ run_state: 'ready' })] });
  assert.deepEqual([r.result.step, r.sent], ['arm', ['start', 'arm']]);
  // The start reply already says ready: Arm at once, no extra read.
  r = await prepareCase({ startStatus: { run_state: 'ready', mode: 'MOTOR' } });
  assert.deepEqual([r.result.step, r.sent, r.reads], ['arm', ['start', 'arm'], 2]);
  // SHADOW: start arms by itself; no Arm is sent.
  r = await prepareCase({ statuses: [fresh({ run_state: 'armed', mode: 'SHADOW' })] });
  assert.deepEqual([r.result, r.sent], [{ ok: true, step: 'armed', runState: 'armed' }, ['start']]);
  // Never ready: give up after the wait with the last state; no Arm.
  r = await prepareCase({ statuses: Object.assign([], { last: fresh({ run_state: 'idle' }) }), waitMs: 900 });
  assert.deepEqual([r.result, r.sent], [{ ok: false, step: 'wait', runState: 'idle' }, ['start']]);
  assert.equal(r.reads, 7, 'the first read, then 900 ms in 150 ms steps');
  // Start refused: no Arm; the refusal goes with the result.
  r = await prepareCase({ answers: { start: [{ ok: false, reply: null, error: 'gnss_rtk_not_fixed', text: START_ERROR.gnss_rtk_not_fixed }] } });
  assert.deepEqual([r.result, r.sent, r.reads], [{ ok: false, step: 'start', runState: 'idle', error: 'gnss_rtk_not_fixed', text: START_ERROR.gnss_rtk_not_fixed }, ['start'], 1]);
  assert.equal(prepareFailText(r.result), '준비하지 못했습니다. RTK FIXED 상태가 아니어서 RTK 보정을 쓸 수 없습니다.');
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
  assert.equal(prepareFailText(r.result, (code) => EXEC_ERROR[code] || code), '준비하지 못했습니다. 다른 화면에서 조종하고 있습니다.', 'what happened and why');
  assert.equal(prepareFailText(r.result, (code) => code), '준비하지 못했습니다. 알려 온 이유는 control_in_use입니다.', 'a code without words is named in a sentence');
  assert.equal(prepareFailText({ ok: false, step: 'start' }), '준비하지 못했습니다. 이유는 알 수 없습니다.');
  assert.equal(prepareFailText({ ok: false, step: 'running' }), '주행 중에는 준비할 수 없습니다. 먼저 멈추기를 누르세요.');
  assert.equal(prepareFailText({ ok: false, step: 'latched' }), '준비하지 못했습니다. 실행기가 다시 멈춤 상태가 됐습니다.');
  assert.equal(prepareFailText({ ok: false, step: 'wait', runState: 'ready' }), '시간 안에 준비를 끝내지 못했습니다. 실행기 상태는 자세히에서 확인하세요.');

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
  const stillArmed = () => ({ ok: false, reply: null, error: 'bridge_still_armed', text: EXEC_ERROR.bridge_still_armed });
  r = await prepareCase({ first: { run_state: 'paused' }, statuses: [fresh({ run_state: 'ended' })], startStatus: { run_state: 'ready' },
    answers: { start: [stillArmed(), stillArmed()] } });
  assert.deepEqual([r.result.ok, r.sent], [true, ['end', 'start', 'start', 'start', 'arm']]);
  // ... but not for ever: the whole press has a time limit, and the last refusal is the reason.
  r = await prepareCase({ first: { run_state: 'ended' }, statuses: Object.assign([], { last: fresh({ run_state: 'ended' }) }),
    answers: { start: Array.from({ length: 80 }, stillArmed) } });
  assert.deepEqual([r.result.ok, r.result.step, r.result.error], [false, 'wait', 'bridge_still_armed']);
  assert.ok(r.clock >= 12000 && r.clock < 13000, `gave up after ${r.clock} ms`);
  assert.equal(prepareFailText(r.result), '시간 안에 준비를 끝내지 못했습니다. 차가 아직 멈추는 중입니다.');
  // A start refused because of a latch the status did not show: acknowledged, then started.
  r = await prepareCase({ first: { run_state: 'ended' }, statuses: [fresh({ run_state: 'ended' })], startStatus: { run_state: 'armed' },
    answers: { start: [{ ok: false, error: 'acknowledge_latch_first' }] } });
  assert.deepEqual([r.result.ok, r.sent], [true, ['start', 'ack', 'start']]);
  // A latch that does not clear: two acknowledgements, then the failure names the step.
  r = await prepareCase({ first: { run_state: 'latched' }, statuses: Object.assign([], { last: fresh({ run_state: 'latched' }) }), waitMs: 300 });
  assert.deepEqual([r.result, r.sent], [{ ok: false, step: 'ack', runState: 'latched', error: 'still_latched' }, ['ack', 'ack']]);
  // An end the executor refuses is reported (no start on top of an open run).
  r = await prepareCase({ first: { run_state: 'armed' }, answers: { end: [{ ok: false, error: 'executor_unreachable', text: EXEC_ERROR.executor_unreachable }] } });
  assert.deepEqual([r.result.step, r.sent], ['end', ['end']]);
  assert.equal(prepareFailText(r.result), '준비하지 못했습니다. 실행기에 연결하지 못했습니다.');
  // Driving: nothing is sent. No status: nothing is sent.
  r = await prepareCase({ first: { run_state: 'running' } });
  assert.deepEqual([r.result.step, r.sent], ['running', []]);
  r = await prepareCase({ first: null });
  assert.deepEqual([r.result, r.sent], [{ ok: false, step: 'status', runState: null }, []]);
  assert.equal(prepareFailText(r.result), '준비하지 못했습니다. 실행기 상태를 읽지 못했습니다.');
  // 멈추기 while 준비 is on its way: nothing more is sent (no Arm after the start).
  r = await prepareCase({ statuses: [fresh({ run_state: 'ready' })], abortAfter: 1 });
  assert.deepEqual([r.result.step, r.sent], ['aborted', ['start']]);
  r = await prepareCase({ first: { run_state: 'latched' }, statuses: [fresh({ run_state: 'ended' })], abortAfter: 1 });
  assert.deepEqual([r.result.step, r.sent], ['aborted', ['ack']]);
  assert.equal(prepareFailText(r.result), '멈추기를 눌러 준비를 그만두었습니다.');
})();

// ---- The speed choice (2026-11-20, user: "자율주행 속도 설정은 따로 못 하나?") ------------------------------------------
// The executor's status as the page gets it (shape read from the SIM stack 2026-11-20: status.tuning of
// tools/row_executor/field_tuning.py State.status, reduced by dashboard_server/executor_proxy.py page_tuning).
{
  const tuningOf = ({ value = 0.4, editable = true, low = 0.2, high = 0.5, floor = 0.2, top = 0.5, margin = 0.05 } = {}) => ({
    preset: 'default', revision: 0, editable, not_editable_because: editable ? null : 'tuning_locked_while_running', writable: false,
    basic: [{ name: 'speed_scale', value: 1, low: 0.5, high: 1.3, unit: '' }, { name: 'target_speed_m_s', value, low, high, unit: 'm/s' }],
    values: { basic: { speed_scale: 1, target_speed_m_s: value }, advanced: { ...(floor === null ? {} : { min_speed_m_s: floor }) },
              locked: { ...(top === null ? {} : { max_speed_m_s: top }), ...(margin === null ? {} : { overspeed_margin_m_s: margin }) } } });
  const choice = (status) => speedChoice(reply(status), now);
  assert.equal(SPEED_KEY, 'target_speed_m_s');
  // the range in 0.05 m/s steps, without the step the executor's own rule refuses (min_speed_m_s < target)
  let c = choice({ run_state: 'idle', speed: { target_mps: 0.4 }, tuning: tuningOf() });
  assert.deepEqual(c, { value: 0.4, options: [0.25, 0.3, 0.35, 0.4, 0.45, 0.5], enabled: true, limit: 0.55, note: '' });
  assert.deepEqual(c.options.map(speedLabel), ['0.25 m/s', '0.30 m/s', '0.35 m/s', '0.40 m/s', '0.45 m/s', '0.50 m/s']);
  // the range is the executor's, not the page's
  assert.deepEqual(choice({ run_state: 'idle', tuning: tuningOf({ low: 0.1, high: 0.3, floor: 0.1, top: 0.6, value: 0.2 }) }).options, [0.15, 0.2, 0.25, 0.3]);
  assert.deepEqual(choice({ run_state: 'idle', tuning: tuningOf({ low: 0.2, high: 0.5, top: 0.4 }) }).options, [0.25, 0.3, 0.35, 0.4], 'not above max_speed_m_s');
  assert.deepEqual(choice({ run_state: 'idle', tuning: tuningOf({ floor: null, top: null, margin: null }) }).options, [0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5],
    'without the two rule values every step of the range is offered (the executor judges)');
  // the value now: also one that is not on a step (set in the field tuning part or on another screen)
  c = choice({ run_state: 'ended', tuning: tuningOf({ value: 0.38 }) });
  assert.deepEqual([c.value, c.options], [0.38, [0.25, 0.3, 0.35, 0.38, 0.4, 0.45, 0.5]]);
  // locked while a run is open: the executor says so (ready, armed, running, paused), and the tab locks its own three
  for (const state of ['ready', 'armed', 'running', 'paused']) {
    assert.equal(choice({ run_state: state, tuning: tuningOf({ editable: false }) }).enabled, false, state);
  }
  for (const state of ['armed', 'running', 'paused']) assert.equal(choice({ run_state: state, tuning: tuningOf({ editable: true }) }).enabled, false, `${state}: locked like the row length`);
  for (const state of ['idle', 'ended', 'latched']) assert.equal(choice({ run_state: state, tuning: tuningOf() }).enabled, true, state);
  assert.equal(choice({ run_state: 'ended', tuning: tuningOf({ editable: false }) }).enabled, false, 'at rest but the bridge is still armed or the vehicle moves');
  // no range (an executor without the bundle, a differential one): off, the value now only
  c = choice({ run_state: 'idle', speed: { target_mps: 0.4 }, tuning: { preset: null, revision: 0, editable: false } });
  assert.deepEqual(c, { value: 0.4, options: [0.4], enabled: false, limit: null, note: '' });
  assert.deepEqual(choice({ run_state: 'idle' }), { value: null, options: [], enabled: false, limit: null, note: '' });
  const off = { value: null, options: [], enabled: false, limit: null, note: '' };
  assert.deepEqual(speedChoice(null, now), off);
  assert.deepEqual(speedChoice({ ok: false, configured: false }, now), off);
  assert.deepEqual(speedChoice(reply({ run_state: 'idle', tuning: tuningOf() }), now + 2000), off, 'a stale status: nothing to choose from');
  // the one line of caution: a value within 0.10 m/s of the overspeed line (max + margin), with the executor's number
  const near = '과속 한도(0.55 m/s)에 가까워 주행 중 잠깐 느려질 때가 있습니다.';
  assert.equal(choice({ run_state: 'idle', tuning: tuningOf({ value: 0.45 }) }).note, near);
  assert.equal(choice({ run_state: 'running', tuning: tuningOf({ value: 0.5, editable: false }) }).note, near, 'also while driving with it');
  assert.equal(choice({ run_state: 'idle', tuning: tuningOf({ value: 0.44 }) }).note, '');
  assert.equal(choice({ run_state: 'idle', tuning: tuningOf({ value: 0.5, margin: null }) }).note, '', 'the line is not shown without the executor numbers');
  assert.equal(speedNote(0.45, 0.6), '', 'another overspeed line: the distance to it decides');
  assert.equal(speedNote(0.5, 0.6), '과속 한도(0.60 m/s)에 가까워 주행 중 잠깐 느려질 때가 있습니다.');
  assert.match(near, /니다\.$/);
}

// PW-10 (contract revision 6): the online scale row and the question after a run.
const scale = (extra = {}) => ({ enabled: true, run_id: 3, value: 0.8512, window_s: 20, target_s: 20, min_s: 10, frozen: false,
  hold: null, mismatch: false, speed_scale: 1, prior_scale: 1, offer: null, ...extra });
assert.equal(scaleText(scale()), '0.851 · 최근 20/20 s');
assert.equal(scaleText(scale({ value: null, window_s: 3.4 })), '모으는 중 3/10 s');
assert.equal(scaleText(scale({ value: null, window_s: 3.4, hold: 'lidar_unmatched' })), `모으는 중 3/10 s · 갱신 멈춤 · ${SCALE_HOLD.lidar_unmatched}`);
assert.equal(scaleText(scale({ frozen: true, hold: 'lidar_stale' })), `0.851 · 최근 20/20 s · 갱신 멈춤 · ${SCALE_HOLD.lidar_stale}`);
assert.equal(scaleText(scale({ frozen: true, hold: 'something_new', mismatch: true })), '0.851 · 최근 20/20 s · 갱신 멈춤 · 기타(something_new) · 속도 표와 어긋남');
assert.equal(scaleText(scale({ enabled: false })), '꺼짐');
assert.equal(scaleLine(reply({ run_state: 'running', online_scale: scale({ frozen: true, hold: 'lidar_unmatched' }) }), now),
             `0.851 · 최근 20/20 s · 갱신 멈춤 · ${SCALE_HOLD.lidar_unmatched}`);
assert.equal(scaleLine(reply({ run_state: 'idle' }), now), null, 'no block (differential or monitor-less executor): no line');
assert.equal(scaleLine(reply({ run_state: 'running', online_scale: scale() }), now + STALE_MS + 1), null, 'stale: hidden');
assert.equal(scaleLine({ ok: false, configured: false }, now), null);
assert.ok(!Object.fromEntries(autoView(reply({ run_state: 'running', online_scale: scale() }), now).rows)['온라인 배율'],
          'the line is outside the folded details, not a details row');

const offer = { run_id: 3, value: 0.8512, window_s: 20, end_reason: 'row_end', speed_scale_now: 1, speed_scale_new: 0.851, ok: true, why_not: null };
let ask = scaleOffer(reply({ run_state: 'ended', online_scale: scale({ offer }) }));
assert.deepEqual([ask.runId, ask.canAccept, ask.from, ask.to], [3, true, 1, 0.851]);
assert.equal(ask.text, '3번 주행에서 마지막 20초 동안 잰 온라인 배율은 0.851입니다. 지금 전체 배율은 1.000입니다. 0.851 값으로 바꿔 저장할까요?');
ask = scaleOffer(reply({ run_state: 'idle', online_scale: scale({ offer: { ...offer, value: null, ok: false, why_not: 'no_scale' } }) }));
assert.equal(ask.canAccept, false);
assert.equal(ask.text, '3번 주행에서는 온라인 배율을 재지 못했습니다. 전체 배율로 저장할 수 없습니다. 주행이 10초보다 짧아 배율이 아직 없습니다.');
assert.equal(scaleOffer(reply({ run_state: 'running', online_scale: scale({ offer }) })), null, 'never while a run drives');
assert.equal(scaleOffer(reply({ run_state: 'ended', online_scale: scale() })), null);
assert.equal(scaleOffer(reply({ run_state: 'ended' })), null);
assert.equal(scaleOffer(null), null);

// The motor mode chip (2026-11-17): SHADOW and MOTOR in the section head; nothing while the status is missing or stale.
assert.deepEqual(modeChip(reply({ run_state: 'idle', mode: 'SHADOW' }), now), ['', '연습 모드']);
assert.deepEqual(modeChip(reply({ run_state: 'running', mode: 'MOTOR' }), now), ['warn', '실제 주행 모드']);
assert.deepEqual(modeChip(reply({ run_state: 'idle', mode: 'mock_mode' }), now), ['', 'mock_mode']);
assert.equal(modeChip(reply({ run_state: 'idle' }), now), null, 'no mode reported');
assert.equal(modeChip(reply({ run_state: 'idle', mode: 'MOTOR' }), now + STALE_MS + 1), null, 'stale status');
assert.equal(modeChip({ ok: false, error: 'executor_unreachable' }, now), null);
assert.equal(modeChip(null, now), null);
// autoView keeps the mode row of the details as before.
assert.equal(Object.fromEntries(autoView(reply({ run_state: 'idle', mode: 'SHADOW' }), now).rows)['모드'], '연습 모드(차는 움직이지 않음) [SHADOW]');

console.log('PASS: three autonomy buttons per run state, the status sentence, heartbeat states, stale rule, labels, session ids, 준비 in one press (ack, end, start, Arm), the online scale row and its question');
