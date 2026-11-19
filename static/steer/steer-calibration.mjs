// Steering calibration part of Rover Control (2026-11-16, ESP32-S3 bridge op 'calibrate', firmware token
// CAL_RUNTIME=1; docs/esp_rover_control.md 1-4). Since 2026-11-19 a part of the 보정·설정 tab (before: a folded section
// of the steering panel; a page with the folded <details id="steerCalDetails"> still works): current CAL marks and the measured
// potentiometer, the buttons 지금 위치를 가운데로 / 왼쪽 끝 찾기 / 오른쪽 끝 찾기 / 적용 (with the margins) / 기본값으로,
// the progress of a running scan and the results. Every action is refused by the bridge while armed or moving and
// needs the firmware update; the buttons show the same reason. Actions that move the wheels or change the stored
// marks ask first (window.confirm). STOP (this section's button, the page's STOP buttons, Esc) ends any action; a page
// that is hidden or left while a scan runs sends STOP too. Loaded by static/app/14-steer.js when the section is
// opened on a front-steer dashboard. Requests: POST /api/rover/calibrate {action, left_margin_counts,
// right_margin_counts}; the status comes from GET /api/rover/status ('calibration' block), shared with the steering
// panel when its reply is fresh. The hard guards come from the status (the CAL line's GUARD; SMOOTH firmware r2: right
// >= 1300, left <= 2600, centre 1850..2150); the fallbacks below are only for a status without them.

export const STATUS_MS = 500;
export const SHARED_MAX_AGE_MS = 1000;
export const NOTICE_MS = 8000;           // the answer of this page's own request stays this long
export const ACTIONS = ['set_center', 'trim_left', 'trim_right', 'find_left', 'find_right', 'apply', 'default'];
// Fine zero (2026-11-18): one press moves CAL CENTER by TRIM_COUNTS (about 0.2 deg); + = left (the POT rises to the left).
export const TRIM_COUNTS = 5;
const num = (value) => (typeof value === 'number' && isFinite(value) ? value : null);

export const REASON_TEXT = {
  REBOUND: '되밀림', NO_PROGRESS: '진척 없음', GUARD: '하드 한계', ABORTED_INPUT: '중단(STOP·입력)',
  INVALID_POT: '가변저항 무효', UNSETTLED: '값이 멈추지 않음', BUDGET: '펄스 예산 끝', TIME: '시간 예산 끝',
};
export const STORE_TEXT = { DEFAULT: '기본값', NVS: '저장됨', UNSAVED: '저장 안 됨' };
export const ACTION_TEXT = {
  set_center: '가운데 저장', trim_left: '영점 왼쪽으로', trim_right: '영점 오른쪽으로', trim_center: '영점 미세 조정', find_left: '왼쪽 끝 찾기', find_right: '오른쪽 끝 찾기', apply: '적용', default: '기본값으로',
  read: '읽기',
};
// Refusals and job errors (scripts/esp32_abs_hold.py, scripts/esp32_steer_bridge.py) as the operator reads them.
export const CAL_MESSAGES = {
  calibration_needs_firmware_update: '펌웨어에 실시간 보정이 없음: 펌웨어를 한 번 업데이트해야 한다(차를 받침에 올리고)',
  calibration_needs_disarmed: '조종 활성화 중에는 보정할 수 없음: 조종 활성화를 끈 뒤',
  calibration_needs_standstill: '움직이는 중에는 보정할 수 없음: 차가 선 뒤',
  calibration_busy: '보정 동작이 이미 진행 중',
  calibrating: '조향 보정 중',
  calibration_stopped: 'STOP 으로 멈춤',
  calibration_silent: '보드 응답이 끊겨 STOP 을 보냄',
  calibration_timeout: '시간이 넘어 STOP 을 보냄',
  calibration_refused: '펌웨어가 거절함',
  calibration_end_not_found: '끝을 찾지 못함',
  calibration_readback_mismatch: '다시 읽은 보정 값이 보낸 값과 다름',
  calibration_not_saved: '저장 확인이 없음',
  calibration_no_answer: '펌웨어 응답 없음',
  calibration_interrupted: '보드가 다시 켜져 중단됨',
  calibration_link_closed: '연결이 끊겨 중단됨',
  calibration_stop_unconfirmed: 'STOP 확인이 없어 시작하지 않음',
  calibration_nothing_found: '적용할 끝 값이 없음: 먼저 끝 찾기',
  calibration_margin_invalid: '여유는 10~120 count 정수',
  calibration_right_below_guard: '오른쪽이 하드 한계 밖(아래 하드 한계 줄)',
  calibration_left_above_guard: '왼쪽이 하드 한계 밖(아래 하드 한계 줄)',
  calibration_center_outside: '가운데가 허용 범위 밖(아래 하드 한계 줄)',
  calibration_pulse_invalid: '끝 찾기 펄스는 40~100 ms 정수',
  calibration_pulse_needs_firmware_update: '끝 찾기 펄스 길이 지정은 SMOOTH 펌웨어부터',
  calibration_right_span_small: '오른쪽 폭이 150 count 보다 작음',
  calibration_left_span_small: '왼쪽 폭이 150 count 보다 작음',
  invalid_calibration_action: '알 수 없는 보정 동작',
  esp32_not_ready: 'ESP32 준비 중',
  device_unavailable: '장치 연결 끊김',
  firmware_mismatch: '펌웨어 이름이 ABS-HOLD 가 아님',
  steering_status_stale: '조향 상태 줄 끊김',
  pot_invalid: '가변저항 값 무효(배선 확인)',
  stop_unconfirmed: 'STOP 확인 실패',
  operator_required: '조종자 세션 없음',
  motor_bridge_unavailable: '브리지에 연결할 수 없음',
  control_busy_stopped: '브리지가 바쁨: 정지',
  request_expired: '요청이 큐에서 만료됨',
  stop_barrier: '다른 STOP 이 먼저 처리됨',
  http_failed: 'HTTP 요청 실패',
};
export const calMessage = (code) => (code ? (CAL_MESSAGES[code] || String(code)) : '');

const marksText = (m) => (m ? `오른쪽 ${m.right} · 가운데 ${m.center} · 왼쪽 ${m.left}` : '--');
// The rows next to the centre buttons; the others (found ends, what 'apply' would set, the limits) are shown with the
// folded end finding (#steerCalMore, 2026-11-20).
export const MAIN_ROWS = ['지금 보정', '가변저항', '가변저항(정착 값)'];

function foundText(found) {
  if (!found) return '--';
  const age = num(found.age_s);
  const ago = age === null ? '' : age < 90 ? ` · ${Math.round(age)} s 전` : ` · ${Math.round(age / 60)} 분 전`;
  const how = `${REASON_TEXT[found.reason] || found.reason}에서 멈춤 · ${found.pulses} 펄스 · ${(found.time_ms / 1000).toFixed(1)} s`;
  return `${found.pot} (${how}${ago})${found.usable ? '' : ' · 쓸 수 없음'}`;
}

export const FALLBACK_GUARDS = { right_min: 1300, left_max: 2600, center_min: 1850, center_max: 2150, min_span: 150 };

// The marks 'apply' would set with these margins (as scripts/esp32_abs_hold.cal_proposal), or a problem code.
export function proposal(cal, margins) {
  if (!cal || !cal.cal) return { marks: null, sides: [], problem: 'calibration_nothing_found' };
  const base = cal.cal;
  const marks = { right: base.right, center: base.center, left: base.left };
  const sides = [];
  for (const side of ['left', 'right']) {
    const found = cal.found && cal.found[side];
    if (found && found.usable) {
      const margin = margins[side];
      marks[side] = side === 'left' ? found.pot - margin : found.pot + margin;
      sides.push(side);
    }
  }
  const [lo, hi] = Array.isArray(cal.margin_range) ? cal.margin_range : [10, 120];
  if (!sides.length) return { marks, sides, problem: 'calibration_nothing_found' };
  for (const side of sides) {
    const margin = margins[side];
    if (!Number.isInteger(margin) || margin < lo || margin > hi) return { marks, sides, problem: 'calibration_margin_invalid' };
  }
  const g = cal.guards || FALLBACK_GUARDS;
  let problem = null;
  if (marks.right < g.right_min) problem = 'calibration_right_below_guard';
  else if (marks.left > g.left_max) problem = 'calibration_left_above_guard';
  else if (marks.center < g.center_min || marks.center > g.center_max) problem = 'calibration_center_outside';
  else if (marks.center - marks.right < g.min_span) problem = 'calibration_right_span_small';
  else if (marks.left - marks.center < g.min_span) problem = 'calibration_left_span_small';
  return { marks, sides, problem };
}

// Everything the section shows for one status reply (pure: tested without a page). margins: {left, right} (ints).
// notice: the answer of this page's last request {kind, text} (shown until the next status says more).
export function calView(reply, margins = { left: 30, right: 30 }, notice = null) {
  const off = (reason, level = 'bad', chip) => ({
    level, chip: chip || reason, rows: [], samples: [], running: false, blocker: reason,
    buttons: Object.fromEntries(ACTIONS.map((a) => [a, { enabled: false, title: reason }])),
    status: { kind: level, text: reason }, apply: null,
  });
  if (!reply) return off('연결 안 됨: 조종 브리지에 닿지 않는다');
  const cal = reply.calibration;
  if (!cal || typeof cal !== 'object') return off('이 조종 브리지는 조향 보정을 모름(ESP32-S3 브리지가 아님)', 'warn', '보정 없음');
  const running = cal.state === 'running' && cal.job;
  const blockerCode = cal.blocker || null;
  const blocker = running ? null : calMessage(blockerCode);
  const store = cal.cal ? (STORE_TEXT[cal.cal.store] || cal.cal.store || '') : '';
  const pot = running && num(cal.live_pot) !== null ? cal.live_pot : cal.pot;
  const plan = proposal(cal, margins);
  const g = { ...FALLBACK_GUARDS, ...(cal.guards || {}) };
  const rows = [
    ['지금 보정', cal.cal ? `${marksText(cal.cal)}${store ? ` (${store})` : ''}` : '--'],
    [running ? '가변저항(정착 값)' : '가변저항', num(pot) === null ? '--' : String(pot)],
    ['왼쪽 끝 (찾은 값)', foundText(cal.found && cal.found.left)],
    ['오른쪽 끝 (찾은 값)', foundText(cal.found && cal.found.right)],
    ['적용하면', plan.problem ? calMessage(plan.problem) : `${marksText(plan.marks)} (${plan.sides.map((s) => (s === 'left' ? '왼쪽' : '오른쪽')).join('·')} 바뀜)`],
    ['하드 한계', `오른쪽 ≥ ${g.right_min} · 왼쪽 ≤ ${g.left_max} · 가운데 ${g.center_min}–${g.center_max} · 폭 ≥ ${g.min_span}`],
    ['한 목표 최대', cal.smooth === true ? '나누지 않음 (SMOOTH 펌웨어: 한 목표를 끊김 없이 한 번에 간다)'
      : num(cal.max_jump_deg) === null ? '--' : `${cal.max_jump_deg}° (보정 구동 1 s 예산 때문에 큰 각은 나눠 간다)`],
  ];
  const enabled = !running && !blockerCode;
  const buttons = {};
  for (const action of ACTIONS) {
    let why = running ? `${ACTION_TEXT[cal.job.action] || cal.job.action} 진행 중` : blocker;
    if (!why && action === 'apply' && plan.problem) why = calMessage(plan.problem);
    if (!why && action === 'set_center' && cal.cal && num(cal.pot) !== null
        && (cal.pot < g.center_min || cal.pot > g.center_max)) {
      why = `지금 위치 ${cal.pot} 는 가운데 범위(${g.center_min}–${g.center_max}) 밖`;
    }
    buttons[action] = { enabled: enabled && !why, title: why || ACTION_TEXT[action] };
  }
  let level = 'ok', chip = cal.cal ? `보정 ${store || '있음'}` : '보정 가능';
  let status = { kind: '', text: '보정할 수 있음 · 찾은 끝은 적용을 눌러야 쓰인다' };
  const samples = Array.isArray(cal.samples) ? cal.samples : [];
  if (running) {
    const job = cal.job;
    const last = samples.length ? samples[samples.length - 1] : null;
    const side = job.action === 'find_left' ? 'left' : job.action === 'find_right' ? 'right' : null;
    level = 'warn';
    chip = '보정 중';
    let text = `${ACTION_TEXT[job.action] || job.action} 중 · ${job.elapsed_s} s`;
    if (side && last && last.side === side) {
      text += ` · 펄스 ${last.n} · 정착 ${last.settled} · 진척 ${last.progress >= 0 ? '+' : ''}${last.progress} · 되밀림 ${last.rebound}`;
    } else if (side) text += ' · 첫 표본 기다림(약 1.4 s)';
    status = { kind: 'testing', text: `${text} · STOP 으로 멈춤` };
  } else if (blockerCode) {
    level = blockerCode === 'calibration_needs_firmware_update' ? 'warn' : 'bad';
    if (['calibration_needs_disarmed', 'calibration_needs_standstill'].includes(blockerCode)) level = 'warn';
    chip = blockerCode === 'calibration_needs_firmware_update' ? '펌웨어 업데이트 필요' : '지금은 안 됨';
    status = { kind: level, text: blocker };
  } else if (cal.last) {
    const last = cal.last;
    const name = ACTION_TEXT[last.action] || last.action;
    if (last.ok) {
      let text = `${name} 끝`;
      if (['apply', 'default', 'set_center', 'trim_center'].includes(last.action)) text += `: ${marksText(last.cal_after)} 저장`;
      if (last.action === 'find_left' || last.action === 'find_right') text += ': 적용 전(적용을 눌러야 쓰인다)';
      status = { kind: 'ok', text };
    } else {
      const reason = last.error === 'calibration_end_not_found' && last.detail ? ` (${REASON_TEXT[last.detail] || last.detail})`
        : last.detail ? ` (${last.detail})` : '';
      status = { kind: 'bad', text: `${name} 실패: ${calMessage(last.error)}${reason}` };
      if (cal.cal && cal.cal.store === 'UNSAVED') status.text += ' · 저장 안 된 값이 쓰이는 중: 다시 적용하거나 기본값으로';
    }
  }
  if (notice && !running) status = notice;
  return { level, chip, rows, samples, running: Boolean(running), blocker: blockerCode, buttons, status, apply: plan };
}

export function initSteerCalibration(page, options = {}) {
  const doc = options.document || document;
  const confirmFn = options.confirm || ((text) => globalThis.confirm(text));
  const now = options.now || (() => Date.now());
  const $ = (id) => doc.getElementById(id);
  const root = $('steerCalPanel');
  const details = $('steerCalDetails');
  if (!root) return null;
  let timer = null, busy = false, posting = false, reply = null, notice = null, view = null;
  // Asked only while the part is on screen: its tab is shown (and a folded part of an older page is open).
  const active = () => page.currentRoute() === '/rover-control' && !doc.hidden && (!details || details.open)
    && (page.shown ? page.shown('steerCalPanel') : true);
  const marginInputs = { left: $('steerCalMarginLeft'), right: $('steerCalMarginRight') };
  const margins = () => {
    const read = (el) => { const v = Number(el?.value); return Number.isFinite(v) ? v : NaN; };
    return { left: read(marginInputs.left), right: read(marginInputs.right) };
  };

  async function poll() {
    if (busy) return;
    const shared = page.driveStatus?.();
    if (shared && now() - shared.at <= SHARED_MAX_AGE_MS) { reply = shared.reply; draw(); return; }
    busy = true;
    try { reply = await page.getJson('/api/rover/status'); } catch (_) { reply = null; }
    finally { busy = false; }
    draw();
  }

  function setText(el, value) { if (el && el.textContent !== value) el.textContent = value; }
  function draw() {
    const fresh = notice && now() - notice.at < NOTICE_MS ? notice : null;
    view = calView(reply, margins(), fresh);
    const chip = $('steerCalState');
    if (chip) { chip.className = `status-chip ${view.level}`; setText(chip, view.chip); }
    // With the folded end finding on the page its rows go there; a page without it shows every row in one list.
    const more = $('steerCalMore');
    const lists = more ? [[$('steerCalRows'), view.rows.filter(([name]) => MAIN_ROWS.includes(name))],
                          [more, view.rows.filter(([name]) => !MAIN_ROWS.includes(name))]] : [[$('steerCalRows'), view.rows]];
    for (const [list, rows] of lists) {
      if (!list) continue;
      const key = JSON.stringify(rows);
      if (list.dataset.key === key) continue;
      list.dataset.key = key;
      list.replaceChildren(...rows.map(([name, value]) => {
        const row = doc.createElement('div');
        const dt = doc.createElement('dt'); dt.textContent = name;
        const dd = doc.createElement('dd'); dd.textContent = value;
        row.append(dt, dd);
        return row;
      }));
    }
    root.querySelectorAll('[data-steer-cal]').forEach((button) => {
      const state = view.buttons[button.dataset.steerCal];
      button.disabled = posting || !state || !state.enabled;
      button.title = state ? state.title : '';
    });
    const status = $('steerCalStatus');
    if (status) { status.className = `test-result ${view.status.kind || ''}`.trim(); setText(status, view.status.text); }
    const wrap = $('steerCalSamplesWrap');
    const body = $('steerCalSamples');
    if (wrap && body) {
      wrap.hidden = view.samples.length === 0;
      const key = JSON.stringify(view.samples);
      if (body.dataset.key !== key) {
        body.dataset.key = key;
        body.replaceChildren(...view.samples.slice().reverse().map((s) => {
          const tr = doc.createElement('tr');
          for (const value of [s.n, s.side === 'left' ? '왼쪽' : '오른쪽', s.settled, s.progress, s.rebound]) {
            const td = doc.createElement('td'); td.textContent = String(value); tr.append(td);
          }
          return tr;
        }));
      }
    }
  }

  function question(action) {
    const cal = reply?.calibration;
    const current = marksText(cal?.cal);
    if (action === 'apply') {
      const plan = view?.apply;
      return `조향 보정 값을 바꾸고 보드에 저장합니다.\n지금: ${current}\n새 값: ${marksText(plan?.marks)}\n`
        + '조향 명령 ±20 이 새 값(찾은 끝에서 여유만큼 안쪽)이 됩니다. 계속할까요?';
    }
    if (action === 'default') {
      const d = cal?.defaults || { right: 1681, center: 1900, left: 2180 };
      return `조향 보정 값을 기본값(${marksText(d)})으로 되돌리고 저장된 값을 지웁니다.\n지금: ${current}\n계속할까요?`;
    }
    if (action === 'set_center') {
      return `지금 바퀴 위치(가변저항 ${cal?.pot ?? '--'})를 가운데로 저장합니다. 앞바퀴가 똑바로 섰는지 확인했나요?`;
    }
    const side = action === 'find_left' ? '왼쪽' : '오른쪽';
    return `앞바퀴를 ${side} 끝까지 천천히 돌려 끝을 찾습니다(조향 모터만, 구동 모터는 쓰지 않음, 최대 약 50 s).\n`
      + '바퀴 주변에 사람·손·물건이 없나요? 멈추려면 STOP.';
  }

  async function run(action) {
    if (posting || !view?.buttons[action]?.enabled) return;
    const trim = action === 'trim_left' ? TRIM_COUNTS : action === 'trim_right' ? -TRIM_COUNTS : 0;
    if (!trim && !confirmFn(question(action))) return;   // a trim moves no wheel: no question
    const body = trim ? { action: 'trim_center', delta_counts: trim } : { action };
    if (action === 'apply') { const m = margins(); body.left_margin_counts = m.left; body.right_margin_counts = m.right; }
    posting = true;
    notice = { kind: 'testing', text: `${ACTION_TEXT[action]} 요청 중…`, at: now() };
    draw();
    try {
      const answer = await page.postJson('/api/rover/calibrate', body);
      notice = answer && answer.ok ? null : { kind: 'bad', text: `${ACTION_TEXT[action]} 거절: ${calMessage(answer?.error || 'bad_reply')}`, at: now() };
    } catch (_) {
      notice = { kind: 'bad', text: `${ACTION_TEXT[action]} 거절: ${calMessage('http_failed')}`, at: now() };
    } finally {
      posting = false;
    }
    await poll();
  }

  function stopNow(reason) {
    notice = null;
    return Promise.resolve((page.controlRequest || page.postJson)('/api/rover/stop', { reason })).catch(() => null).then(() => poll());
  }

  root.querySelectorAll('[data-steer-cal]').forEach((button) => {
    button.addEventListener('click', () => run(button.dataset.steerCal));
  });
  $('steerCalStop')?.addEventListener('click', () => stopNow('calibration_stop'));
  for (const el of Object.values(marginInputs)) el?.addEventListener('input', () => { notice = null; draw(); });
  details?.addEventListener('toggle', () => routeChanged());
  doc.addEventListener('visibilitychange', () => {
    // A scan moves the wheels without an armed operator: a hidden page stops it, as it stops driving.
    if (doc.hidden && view?.running) stopNow('page_hidden');
    routeChanged();
  });
  globalThis.addEventListener?.('pagehide', () => {
    if (!view?.running) return;
    try {
      navigator.sendBeacon('/api/rover/stop', new Blob([JSON.stringify({ reason: 'pagehide' })], { type: 'application/json' }));
    } catch (_) { /* the scan ends by itself within its budget */ }
  });
  function routeChanged() {
    if (active()) { if (!timer) { timer = setInterval(poll, STATUS_MS); poll(); } }
    else {
      clearInterval(timer); timer = null;
      // A scan moves the wheels: leaving the page or its tab stops it, as a hidden page does.
      if (view?.running && (page.currentRoute() !== '/rover-control' || (page.shown && !page.shown('steerCalPanel')))) stopNow('page_inactive');
    }
  }
  routeChanged();
  return { routeChanged, draw, poll, run, view: () => view };
}
