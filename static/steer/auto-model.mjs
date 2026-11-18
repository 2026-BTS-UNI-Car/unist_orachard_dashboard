// What the autonomy section shows and which button it offers, from GET /api/executor/status (UI-08,
// docs/STEERING_SERVER_KO.md 6, contract v0 with revisions 1 and 2). Pure functions: tested without a page.

export const HEARTBEAT_MS = 100;          // contract revision 1: every 100 ms (at most 250 ms)
export const STATUS_MS = 500;             // placeholder (STEERING_SERVER_KO.md 6)
export const STALE_MS = 1500;             // UI-08: no status for 1.5 s -> "끊김", values dimmed
export const HEARTBEAT_STATES = new Set(['armed', 'running', 'paused']);
export const ACTIVE_RUN_STATES = new Set(['ready', 'armed', 'running', 'paused', 'latched']);

export const RUN_STATE = {
  idle: ['', '대기'], ready: ['info', '준비됨 · Arm 전'], armed: ['info', '출발 대기'], running: ['ok', '주행 중'],
  paused: ['warn', '일시정지'], ended: ['', '끝남'], latched: ['bad', '정지 래치'],
};
export const STOP_REASON = {
  ROW_END: '정상 열 끝', OBSTACLE: '열 안 장애물', END_STRUCTURE: '열 끝 구조물(벽·울타리)',
  PERCEPTION_OR_LOCALISATION: '센서·위치 추정 부족', CONTROL_AUTHORITY: '조향·구동 권한 부족',
  OPERATOR_OR_BACKSTOP: '운영자·한도 정지', OPERATOR_LINK_LOST: '화면 연결 끊김(화면 신호 없음)',
  SYSTEM_FAULT: '시스템 오류', UNCLASSIFIED: '분류 안 됨',
};
export const GO_BLOCKER = {
  session_conflict: '다른 화면이 출발 권한을 가짐', operator_link_not_fresh: '이 화면의 연결 신호가 아직 없음',
  no_run: 'run 없음: 먼저 준비', already_running: '이미 주행 중', latched: '래치 확인이 먼저',
  arm_first: 'Arm이 먼저', inputs_not_fresh: '센서 입력이 오래됨', steering_feedback: '조향 상태가 없거나 오래됨',
  vehicle_moving: '차가 아직 움직임', corridor_not_acquired: '통로를 아직 못 찾음',
  lease_lost: '조향 브리지 연결이 끊겨 다시 Arm이 필요함',                  // contract revision 3
};
// stop_detail (the executor's own reason string) -> short words; an unknown one is shown as sent.
export const STOP_DETAIL = {
  operator_stop: '운영자 STOP', operator_link_lost: '화면 연결 끊김', row_end: '열 끝', lease_lost: '브리지가 권한을 거둠',
};
export const LEASE_LOST_TEXT = '브리지가 권한을 거뒀다: Arm을 다시 누른다';
export const GNSS_STATE = {
  none: '수신 없음', no_fix: '미확정', fix: '확정', rtk: 'RTK', stale: '오래됨', unused: '쓰지 않음', off: '꺼짐',
  absent: '수신 없음', waiting: '기다림', ok: '정상', accepted: '받아들임', rejected: '거부', fence_tripped: '범위 벗어남',
  fixed: 'RTK FIXED', float: 'RTK FLOAT',
};
// The run's GPS mode (2026-11-19, tools/row_executor/config.py gnss_mode): the choice of the autonomy start.
export const GNSS_MODES = ['off', 'log_only', 'geofence', 'weak_constraint'];
export const GNSS_MODE_TEXT = {
  off: 'GPS 안 씀', log_only: 'GPS 기록만', geofence: 'GPS 범위 감시', weak_constraint: 'RTK 약한 보정',
};
// Refusals of a start (the executor's own codes, and the dashboard's 400) as the operator reads them.
export const START_ERROR = {
  invalid_gnss_mode: 'GPS 방식 값이 틀림', invalid_row_len: '열 길이는 0보다 크고 1000 m 이하',
  unknown_parameter: '이 실행기는 GPS 방식 선택을 모름(실행기 업데이트 필요)',
  gnss_mode_not_supported: '이 실행기는 GPS 방식 선택을 모름(실행기 업데이트 필요)',
  gnss_rtk_not_fixed: 'RTK FIXED 가 아님: RTK 약한 보정을 쓸 수 없다', gnss_needs_rtk_fixed: 'RTK FIXED 가 아님: RTK 약한 보정을 쓸 수 없다',
  gnss_not_available: 'GPS 수신이 없음', gnss_mode_needs_front_steer: '이 방식은 앞바퀴 조향 실행기에서만',
};
export const LIMIT_REASON = {
  target: '목표 속도', corridor: '통로 신뢰도', lateral_error: '횡 오차', heading_error: '방향 오차',
  row_end: '열 끝 감속', steering: '조향 권한', obstacle: '앞 장애물', approach: '열 끝 감속',
};
// PW-10 (contract revision 6): why the online scale is held, and why its offer can not be taken.
export const SCALE_HOLD = {
  lidar_stale: 'LiDAR 스캔 없음', lidar_unmatched: 'LiDAR 속도 품질 미달(스캔 정합 실패)', not_driving: '주행 중 아님',
  no_status: '구동 상태 없음', monitor_off: '감시 꺼짐',
};
export const SCALE_WHY_NOT = {
  no_scale: '배율이 아직 없다(주행 10초 미만)', no_forward_motion: 'LiDAR가 앞으로 가는 것을 못 봤다',
  scale_set_in_config: '실행기 설정이 배율을 따로 정했다', out_of_range: '제안 값이 범위 밖', no_change: '지금 값과 같다',
  no_field_tuning: '현장 조정 묶음 없음',
};

const num = (value) => (typeof value === 'number' && isFinite(value) ? value : null);
const fixed = (value, digits, unit) => (num(value) === null ? '--' : `${value.toFixed(digits)}${unit}`);
const label = (table, code) => (code === null || code === undefined || code === '' ? null
  : (table[code] || `기타(${String(code)})`));

// The button of the section: [text, action, enabled]. action: prepare | arm | go | ack | null.
export function primaryAction(reply, nowMs = Date.now()) {
  if (!reply) return { text: '확인 중', action: null, enabled: false };
  if (reply.configured === false) return { text: '실행기 설정 없음', action: null, enabled: false };
  if (!reply.ok || !reply.status) return { text: '실행기 연결 안 됨', action: null, enabled: false };
  if (stale(reply, nowMs)) return { text: '실행기 응답 끊김', action: null, enabled: false };
  const s = reply.status;
  const ready = s.go_ready === undefined || s.go_ready === null ? true : s.go_ready === true;
  // contract rev. 2: an executor set to hold_to_run refuses go and heartbeat (409 start_mode_hold_to_run)
  if (s.start_mode === 'hold_to_run' && (s.run_state === 'armed' || s.run_state === 'paused' || s.run_state === 'running')) {
    return { text: '누르는 동안만 방식: 이 화면에서 출발 못 함', action: null, enabled: false };
  }
  switch (s.run_state) {
    case 'idle': case 'ended': case undefined: case null:
      return { text: s.mode === 'MOTOR' ? '준비 (run 만들기 · Arm)' : '준비 (run 만들기)', action: 'prepare', enabled: true };
    case 'ready': return leaseLost(s) ? { text: 'Arm 다시 (브리지가 권한을 거둠)', action: 'arm', enabled: true }
      : { text: 'Arm (모터 권한)', action: 'arm', enabled: true };
    case 'armed': return { text: '자율 주행 시작', action: 'go', enabled: ready };
    case 'running': return { text: '주행 중', action: null, enabled: false };
    case 'paused': return { text: '다시 출발', action: 'go', enabled: ready };
    case 'latched': return { text: '래치 확인', action: 'ack', enabled: true };
    default: return { text: `상태 ${String(s.run_state)}`, action: null, enabled: false };
  }
}

// "준비" in one press (contract v0): start, then Arm when the executor says the run is ready. The start reply is not
// enough to decide: the status it carries can come from before the start, and a status poll already in flight when
// the start was sent can answer with the old state. So: read the status again after the start reply until it says
// ready (MOTOR: send arm) or armed (SHADOW: start arms by itself), at most waitMs. Only a run state that the start
// itself makes (ready, armed) counts; idle or ended means "not yet". Returns { ok, step, runState }.
export const PREPARE_WAIT_MS = 3000;
export const PREPARE_STEP_MS = 150;
// gnssMode (2026-11-19): the run's GPS mode (GNSS_MODES); null = the executor's configured mode (the field is not sent).
export async function prepareRun({ send, readStatus, sleep, rowLen = null, gnssMode = null, waitMs = PREPARE_WAIT_MS,
                                   stepMs = PREPARE_STEP_MS, now = () => Date.now() }) {
  const body = { row_len_m: rowLen };
  if (gnssMode) body.gnss_mode = gnssMode;
  const started = await send('start', body);
  if (!started.ok) return { ok: false, step: 'start', runState: null };
  const t0 = now();
  let status = started.reply && typeof started.reply.status === 'object' ? started.reply.status : null;
  for (;;) {
    const runState = status ? status.run_state : null;
    if (runState === 'armed') return { ok: true, step: 'armed', runState };
    if (runState === 'ready') {
      const armed = await send('arm', {});
      return { ok: armed.ok, step: 'arm', runState };
    }
    if (runState === 'latched') return { ok: false, step: 'latched', runState };
    if (now() - t0 >= waitMs) return { ok: false, step: 'wait', runState };
    await sleep(stepMs);
    const fresh = await readStatus();
    status = fresh && fresh.ok && fresh.status ? fresh.status : null;
  }
}

// Contract revision 3: the bridge took the lease back while the run waited for its first go; the run stays open.
export function leaseLost(s) {
  return Boolean(s && s.run_state === 'ready' && ((s.lease_lost && typeof s.lease_lost === 'object')
    || (Array.isArray(s.go_blockers) && s.go_blockers.includes('lease_lost'))));
}

export function stale(reply, nowMs = Date.now()) {
  const at = reply && num(reply.received_at);
  return at === null || nowMs - at * 1000 > STALE_MS;
}

export function heartbeatWanted(reply, nowMs = Date.now()) {
  return Boolean(reply && reply.ok && reply.status && !stale(reply, nowMs) && HEARTBEAT_STATES.has(reply.status.run_state)
    && reply.status.start_mode !== 'hold_to_run');
}

export function runActive(reply) {
  return Boolean(reply && reply.ok && reply.status && ACTIVE_RUN_STATES.has(reply.status.run_state));
}

// The chip and the rows of the section.
export function autoView(reply, nowMs = Date.now()) {
  if (!reply) return { chip: ['', '확인 중'], notice: ['testing', '자율 실행기 상태를 확인하고 있다.'], rows: [], stale: true };
  if (reply.configured === false) {
    return { chip: ['', '설정 없음'], rows: [], stale: true,
             notice: ['warn', '자율 실행기 설정 없음: 이 대시보드에는 UBTS_EXECUTOR_URL이 없다. 수동 조종과 STOP은 그대로 쓴다.'] };
  }
  if (!reply.ok || !reply.status) {
    const why = reply.error === 'executor_token_rejected' || reply.error === 'executor_token_missing'
      ? '토큰이 맞지 않음(실행기가 다시 시작됨)' : '실행기에 닿지 않음';
    return { chip: ['bad', '연결 안 됨'], rows: [], stale: true,
             notice: ['bad', `자율 실행기 연결 안 됨: ${why}. 주행 중이었다면 차는 스스로 선다. STOP은 브리지로도 보낸다.`] };
  }
  const s = reply.status;
  const isStale = stale(reply, nowMs);
  const [level, text] = RUN_STATE[s.run_state] || ['', String(s.run_state || '알 수 없음')];
  const sr = s.single_row || {};
  const link = s.operator_link || {};
  const speed = s.speed || {};
  const gnss = s.gnss || {};
  const category = s.stop_reason || sr.stop_category || null;
  const stopText = label(STOP_REASON, category);
  const rows = [
    ['모드', s.mode === 'SHADOW' ? 'SHADOW · 차를 몰지 않음' : s.mode === 'MOTOR' ? 'MOTOR · 차를 몬다' : (s.mode || '--')],
    ['정지 이유', stopText ? (s.stop_detail ? `${stopText} · ${STOP_DETAIL[s.stop_detail] || s.stop_detail}` : stopText) : '--'],
    ['횡 · 방향 오차', `${fixed(sr.lateral_error_m, 2, ' m')} · ${fixed(sr.heading_error_deg, 1, '°')}`],
    ['속도', `${fixed(sr.speed_m_s, 2, '')} / 목표 ${fixed(num(speed.target_mps) ?? num(sr.target_speed_m_s), 2, '')} m/s`],
    ['속도 제한', num(speed.limit_mps) === null ? '--'
      : `${fixed(speed.limit_mps, 2, ' m/s')} · ${label(LIMIT_REASON, speed.limit_reason) || '--'}`],
    ['진행 거리', fixed(sr.distance_m, 1, ' m')],
    ['GNSS', gnss.state || gnss.mode ? [gnss.mode ? GNSS_MODE_TEXT[gnss.mode] || gnss.mode : null, gnss.state ? label(GNSS_STATE, gnss.state) : null,
      num(num(gnss.hacc_m) ?? gnss.h_acc_m) !== null ? `±${(num(gnss.hacc_m) ?? gnss.h_acc_m).toFixed(1)} m` : null].filter(Boolean).join(' · ') : '정보 없음'],
    ['화면 연결', link.fresh === true ? `연결됨 · ${fixed(link.age_s, 2, ' s')}`
      : link.fresh === false ? '끊김' : '--'],
  ];
  let notice = null;
  if (isStale) notice = ['bad', '실행기 상태가 1.5초 넘게 오지 않는다: 값이 오래됐다. 주행 중이었다면 차는 스스로 선다.'];
  else if (leaseLost(s)) notice = ['warn', `${LEASE_LOST_TEXT}. ${GO_BLOCKER.lease_lost}.`];
  else if (Array.isArray(s.go_blockers) && s.go_blockers.length && (s.run_state === 'armed' || s.run_state === 'paused')) {
    notice = ['warn', `출발 조건: ${s.go_blockers.map((code) => label(GO_BLOCKER, code)).join(', ')}`];
  } else if (s.run_state === 'running') {
    const timeout = num(link.timeout_s);
    notice = ['ok', `주행 중: 이 화면을 열어 두어야 한다. 탭을 숨기거나 화면을 떠나면 ${timeout === null ? '0.5' : timeout.toFixed(1)}초 뒤 차가 선다.`];
  } else if (s.run_state === 'paused' && category === 'OPERATOR_LINK_LOST') {
    notice = ['warn', '화면 연결이 끊겨 멈췄다. 다시 가려면 다시 출발을 누른다(자동 재출발 없음).'];
  }
  return { chip: isStale ? ['bad', '끊김'] : [level, text], notice, rows, stale: isStale };
}

// The executor's motor mode as a chip next to the run state (2026-11-17): SHADOW runs never move the vehicle, so the
// operator sees it before pressing start, not only in the collapsed details. null while the status is missing or stale.
export function modeChip(reply, nowMs = Date.now()) {
  const s = reply && reply.ok && reply.status ? reply.status : null;
  if (!s || !s.mode || stale(reply, nowMs)) return null;
  if (s.mode === 'SHADOW') return ['', 'SHADOW · 모터 안 씀'];
  if (s.mode === 'MOTOR') return ['warn', 'MOTOR · 차를 몬다'];
  return ['', String(s.mode)];
}

// PW-10: "LiDAR 속도 ÷ 표 속도" of the last ~20 s of driving, or why there is none / why it does not change. The line
// of the autonomy section (shown only when the executor sends the block: a front-steer executor with its monitor).
export function scaleLine(reply, nowMs = Date.now()) {
  const s = reply && reply.ok && reply.status ? reply.status : null;
  if (!s || !s.online_scale || typeof s.online_scale !== 'object' || stale(reply, nowMs)) return null;
  return scaleText(s.online_scale);
}

export function scaleText(scale) {
  if (!scale || scale.enabled === false) return '꺼짐';
  const win = num(scale.window_s);
  const minimum = num(scale.min_s);
  const target = num(scale.target_s);
  let text;
  if (num(scale.value) === null) {
    text = `모으는 중 ${win === null ? '--' : win.toFixed(0)}/${minimum === null ? '10' : minimum.toFixed(0)} s`;
  } else {
    text = `${scale.value.toFixed(3)} (최근 ${win === null ? '--' : win.toFixed(0)}${target === null ? '' : `/${target.toFixed(0)}`} s)`;
  }
  if (scale.frozen === true || (num(scale.value) === null && scale.hold && scale.hold !== 'not_driving')) {
    text += ` · 멈춤: ${label(SCALE_HOLD, scale.hold) || '이유 모름'}`;
  }
  if (scale.mismatch === true) text += ' · 표 어긋남';
  return text;
}

// PW-10: the question after a run ended (null when there is nothing to ask). accept: the executor sets k_field.
export function scaleOffer(reply) {
  const s = reply && reply.ok && reply.status ? reply.status : null;
  const scale = s && s.online_scale;
  const offer = scale && typeof scale.offer === 'object' ? scale.offer : null;
  if (!offer || !Number.isInteger(offer.run_id) || HEARTBEAT_STATES.has(s.run_state)) return null;
  const value = num(offer.value);
  const win = num(offer.window_s);
  const measured = value === null ? '온라인 배율 없음' : `온라인 배율 ${value.toFixed(3)} (최근 ${win === null ? '--' : win.toFixed(0)} s)`;
  if (offer.ok !== true) {
    return { runId: offer.run_id, canAccept: false,
             text: `run ${offer.run_id} 끝: ${measured}. 전체 배율로 저장할 수 없다: ${label(SCALE_WHY_NOT, offer.why_not) || '이유 모름'}.` };
  }
  const now = num(offer.speed_scale_now);
  const next = num(offer.speed_scale_new);
  return { runId: offer.run_id, canAccept: true, from: now, to: next,
           text: `run ${offer.run_id} 끝: ${measured}. 전체 배율(k_field)을 ${fixed(now, 3, '')} → ${fixed(next, 3, '')}로 저장할까요?` };
}

// A random session id of the operator link (contract: 4 to 64 characters).
export function newSession(random = globalThis.crypto) {
  const bytes = new Uint8Array(12);
  random.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

// ---- Monitoring view of the 자율주행 tab (2026-11-19) ---------------------------------------------------------------
// User: "자율주행시에 occupancy grid map을 그리면서 하고있는거니? ... 모니터링이 쉽게 되도록". One tile per value: run state and
// reason, lateral / heading error, distance, speed, the steering command against the measured angle, the drive duty, the
// steering motor PWM (bridge status steering.pwm / pwm_peak, 16f9d5c), recording and GPS. The executor status is the
// autonomy panel's own poll; the bridge status is the steering panel's (shared, no request of its own).
const DEG = 180 / Math.PI / 1000;
const mradDeg = (mrad) => (num(mrad) === null ? null : mrad * DEG);
const signedDeg = (deg) => (deg === null ? '--' : `${Math.abs(deg) < 0.05 ? '' : deg > 0 ? '왼 ' : '오 '}${Math.abs(deg).toFixed(1)}°`);

// The GPS words of a run: [value, sub, level]. requested: the mode the page sent ('' = the executor's setting).
export function gnssTile(status, requested = '') {
  const g = status && typeof status.gnss === 'object' && status.gnss ? status.gnss : null;
  if (!g) return [requested ? GNSS_MODE_TEXT[requested] || requested : '--', '실행기가 GPS 상태를 보내지 않음', ''];
  const mode = typeof g.mode === 'string' ? g.mode : null;
  const value = mode ? GNSS_MODE_TEXT[mode] || mode : '방식 모름';
  const hacc = num(g.hacc_m) ?? num(g.h_acc_m);
  const counts = g.counts && typeof g.counts === 'object' ? g.counts : {};
  const accepted = num(g.accepted) ?? num(counts.accepted);
  const rejectedOf = Object.entries(counts).filter(([k]) => !['accepted', 'restarted', 'reanchored'].includes(k));
  const rejected = num(g.rejected) ?? (rejectedOf.length ? rejectedOf.reduce((a, [, v]) => a + (num(v) || 0), 0) : null);
  const state = g.rtk?.state || g.state || g.outcome || null;
  const parts = [state ? label(GNSS_STATE, state) : null, hacc === null ? null : `±${hacc.toFixed(2)} m`,
                 accepted === null ? null : `받음 ${accepted}${rejected === null ? '' : ` · 거부 ${rejected}`}`].filter(Boolean);
  let level = '';
  if (g.fence_tripped === true) { parts.unshift('범위 벗어남'); level = 'bad'; }
  if (requested && mode && requested !== mode) { parts.unshift(`요청한 ${GNSS_MODE_TEXT[requested] || requested}과 다름`); level = level || 'warn'; }
  return [value, parts.join(' · ') || '정보 없음', level];
}

// The tiles: [label, value, sub, level]. bridge: the bridge status reply of the steering panel (or null);
// recording: {known, active, tag, auto} (auto = started by this panel for the run).
export function monitorView(reply, bridge = null, recording = null, requestedGnss = '', nowMs = Date.now()) {
  const s = reply && reply.ok && reply.status ? reply.status : null;
  const isStale = stale(reply, nowMs);
  const tiles = [];
  if (!s) {
    tiles.push(['상태', reply && reply.configured === false ? '실행기 없음' : reply ? '연결 안 됨' : '확인 중', '', reply ? 'bad' : '']);
  } else {
    const [level, text] = RUN_STATE[s.run_state] || ['', String(s.run_state || '알 수 없음')];
    const sr = s.single_row || {};
    const stopText = label(STOP_REASON, s.stop_reason || sr.stop_category || null);
    const why = stopText ? `${stopText}${s.stop_detail ? ` · ${STOP_DETAIL[s.stop_detail] || s.stop_detail}` : ''}` : sr.phase ? `단계 ${sr.phase}` : '';
    tiles.push(['상태', isStale ? '끊김' : text, why, isStale ? 'bad' : level]);
    tiles.push(['횡 오차', fixed(num(sr.lateral_error_m) ?? num(s.lat), 2, ' m'), '+ = 왼쪽', '']);
    tiles.push(['방향 오차', fixed(num(sr.heading_error_deg) ?? num(s.head), 1, '°'), '', '']);
    const remaining = num(sr.end_remaining_m);
    tiles.push(['진행 거리', fixed(num(sr.distance_m) ?? num(s.dist), 1, ' m'), remaining === null ? '' : `남은 ${remaining.toFixed(1)} m`, '']);
    const speed = s.speed || {};
    const limit = num(speed.limit_mps) === null ? '' : ` · 제한 ${speed.limit_mps.toFixed(2)} (${label(LIMIT_REASON, speed.limit_reason) || '--'})`;
    tiles.push(['속도', fixed(num(sr.speed_m_s) ?? num(s.v), 2, ' m/s'), `목표 ${fixed(num(speed.target_mps) ?? num(sr.target_speed_m_s), 2, '')}${limit}`, '']);
  }
  // Steering command against the measured angle: the executor's own numbers, else the bridge's.
  const steering = bridge && typeof bridge.steering === 'object' && bridge.steering ? bridge.steering : {};
  const host = bridge && typeof bridge.steering_host === 'object' && bridge.steering_host ? bridge.steering_host : {};
  const cmd = mradDeg(num(s?.S) ?? num(s?.single_row?.steer_mrad) ?? num(host.target_mrad));
  const meas = mradDeg(num(s?.steer?.meas) ?? num(steering.measured_mrad));
  tiles.push(['조향 명령 / 측정', `${signedDeg(cmd)} / ${signedDeg(meas)}`, cmd !== null && meas !== null ? `차이 ${Math.abs(cmd - meas).toFixed(1)}°` : '', '']);
  const drive = bridge && typeof bridge.drive === 'object' && bridge.drive ? bridge.drive : {};
  const acked = [drive.left?.acked, drive.right?.acked].map(num);
  const throttle = num(s?.T) ?? num(s?.single_row?.throttle_pct);
  const board = acked.some((v) => v !== null) ? `보드 PWM ${acked.map((v) => (v === null ? '--' : v)).join(' / ')}` : '';
  tiles.push(['구동 duty', throttle === null ? '--' : `${throttle} %`, `${board}${num(drive.max_pwm) === null ? '' : `${board ? ' · ' : ''}상한 ${drive.max_pwm}`}`, '']);
  const pwm = num(steering.pwm), peak = num(steering.pwm_peak);
  tiles.push(['조향 모터 PWM', pwm === null ? '--' : String(pwm), peak === null ? (bridge ? 'TRACE 값 없음' : '조향 상태 없음') : `최근 최대 ${peak}`, '']);
  if (recording) {
    const rec = !recording.known ? ['확인 중', '', ''] : recording.active
      ? ['기록 중', recording.auto ? 'run 자동 기록(모든 센서)' : recording.tag === 'autonomy' ? '자율주행 기록' : '손으로 시작한 기록', 'ok']
      : ['기록 안 함', '', 'warn'];
    tiles.push(['기록', ...rec]);
  }
  tiles.push(['GPS', ...gnssTile(s, requestedGnss)]);
  return { tiles, stale: isStale };
}

// status.metrics: the executor's comparison of its reference centreline with the travelled path (names read
// defensively; grid_* = the same against the centreline of the local grid). Returns {tiles, source} or null.
const METRICS = [
  ['lat_rms_m', '횡 RMS', 'm', 3], ['lat_mean_m', '횡 평균', 'm', 3], ['lat_max_abs_m', '횡 최대', 'm', 3],
  ['within_0_05_pct', '±5 cm 안', '%', 1], ['within_0_10_pct', '±10 cm 안', '%', 1],
  ['head_rms_deg', '방향 RMS', '°', 2], ['head_max_abs_deg', '방향 최대', '°', 2],
];
export function metricsView(reply) {
  const m = reply && reply.ok && reply.status && reply.status.metrics && typeof reply.status.metrics === 'object' ? reply.status.metrics : null;
  if (!m) return null;
  const unit = (value, u, digits) => (num(value) === null ? '--' : `${value.toFixed(digits)}${u === '°' ? '°' : ` ${u}`}`);
  const tiles = [];
  for (const [key, name, u, digits] of METRICS) {
    const grid = m[`grid_${key}`];
    if (num(m[key]) === null && num(grid) === null) continue;
    tiles.push([name, unit(m[key], u, digits), num(grid) === null ? '' : `격자 중심선 ${unit(grid, u, digits)}`, '']);
  }
  const samples = num(m.samples), distance = num(m.distance_m);
  if (samples !== null || distance !== null) {
    tiles.push(['표본', samples === null ? '--' : String(samples), distance === null ? '' : `${distance.toFixed(1)} m 동안`, '']);
  }
  const known = new Set(['samples', 'distance_m', ...METRICS.flatMap(([k]) => [k, `grid_${k}`])]);
  for (const [key, value] of Object.entries(m).filter(([k, v]) => !known.has(k) && num(v) !== null).slice(0, 6)) {
    tiles.push([key, String(Math.round(value * 1000) / 1000), '', '']);
  }
  return tiles.length ? { tiles, source: typeof m.reference === 'string' ? m.reference : '기준 중심선 대 실제 경로' } : null;
}

// Recording of a run (2026-11-19, user: "자율주행시에는 반드시 모든 센서가 녹화되도록"). Before a go: start one (every sensor,
// tag autonomy) unless one is running; after the run ended or was stopped: stop only the one this panel started.
export const RUN_RECORD_SENSORS = ['lidar', 'camera', 'imu', 'gps', 'drive', 'steering', 'executor', 'tf'];
export const RUN_OVER_STATES = new Set(['ended', 'idle', 'latched']);
export function recordBeforeGo(recording, wanted) {
  if (!wanted) return 'skip';
  if (!recording || !recording.known) return 'unknown';
  return recording.active ? 'keep' : 'start';
}
export function recordAfterRun(reply, autoId, recording) {
  const s = reply && reply.ok && reply.status ? reply.status : null;
  if (!autoId || !s || !RUN_OVER_STATES.has(s.run_state)) return false;
  return Boolean(recording && recording.known && recording.active && recording.id === autoId);
}
