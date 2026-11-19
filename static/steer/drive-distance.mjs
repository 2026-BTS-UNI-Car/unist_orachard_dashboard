// Distance move part of Rover Control's steering panel (2026-11-17, user request: choose the terrain and send "1 m
// forward"; docs/esp_rover_control.md 1-6). A folded section of the 수동 조종 tab: direction, terrain, distance, the speed
// (2026-11-19, "거리 이동하는거 속도 조절 되게": a duty slider from the terrain's lowest to its highest table duty, 1 %
// steps, never above the bridge's drive cap that the plan reply names (bridge.max_duty); between table rows the server
// interpolates; default the lowest, the 11-17 page's first choice), the server's plan (drive time, expected distance
// and the grade of each part; the details folded), a button that runs only while it is held (deadman), and the result.
// Backward (2026-11-19, "특정거리 앞으로 가는거 뿐만 아니라 뒤로 가는 기능"): the same move with direction 'backward' (the
// server drives a negative throttle, at most 2 m: nothing looks behind the vehicle; the table was driven forward, so a
// backward plan is an assumption). The tape measurement that becomes the correction factor of that terrain, direction
// and duty is a part of the 보정·설정 tab (#steerDistTapePanel; a duty without one borrows the nearest measured duty of
// the same direction, the rows say which). The data are the October 2026 drive records of the vehicle BEFORE the steering
// unit (ESP8266); the steering vehicle has not been measured.
// Terrain in the tape part (2026-11-20, user: "줄자 보정에서 도로/포장 옵션만 있는데, 그 옵션을 바꿀 수 있게"): the tape part
// has its own terrain choice (#steerDistTapeTerrain). It is the same choice as the one of the 수동 조종 tab: changing
// either changes the other, the plan and the corrections shown. The reset clears the chosen terrain. A tape distance
// belongs to the terrain its move was planned with (the server files it there), so it can be saved only while that
// terrain is the chosen one; with another terrain chosen the part says which terrain the move was.
// While the button is held the server drives with this page's lease (dashboard_server/drive_distance.py): the page hands
// its lease chain over (controller.beginExternal), sends a hold heartbeat every HOLD_MS, and takes the chain back when
// the move ends (endExternal). Release, a hidden page, blur, a lost pointer or leaving the tab -> release request; the
// server also stops on a missing heartbeat (0.4 s) and the bridge on its own input TTL (0.75 s). STOP (this section's
// button, the page's STOP buttons, Esc) always wins. Loaded by static/app/14-steer.js when the section is opened (or the
// 보정·설정 tab shown) the first time.

export const HOLD_MS = 100;
export const PLAN_DEBOUNCE_MS = 250;
export const RELEASE_RETRIES = 3;
const num = (value) => (typeof value === 'number' && isFinite(value) ? value : null);

export const REASON_TEXT = {
  planned: '계획 시간 끝', released: '버튼을 놓음', heartbeat_lost: '화면 신호가 끊김', operator_stop: 'STOP',
  bridge_error: '브리지 오류', page_hidden: '화면이 가려짐', blur: '창이 포커스를 잃음', tab_changed: '탭을 바꿈',
};
export const DIRECTION_TEXT = { forward: '앞으로', backward: '뒤로' };
export const MESSAGES = {
  distance_terrain_unknown: '알 수 없는 지형',
  distance_duty_invalid: '속도 duty 가 정수가 아님',
  distance_duty_out_of_range: '이 지형 표 범위 밖의 속도 duty',
  distance_invalid: '거리가 숫자가 아님',
  distance_out_of_range: '거리는 0.2~3.0 m',
  distance_out_of_range_backward: '뒤로는 0.2~2.0 m (뒤는 감지하지 못한다)',
  distance_direction_invalid: '방향 값이 틀림',
  distance_too_short_for_duty: '이 속도로는 너무 짧다(출발 자료 밖): 속도를 낮추거나 거리를 늘린다',
  distance_too_long_for_duty: '이 속도로는 너무 길다: 속도를 높이거나 거리를 줄인다',
  distance_needs_steer_bridge: 'ESP32-S3 조향 브리지가 아님',
  distance_drive_cap_zero: '구동 상한 0 (브리지 설치 값): 거리 이동 불가',
  distance_drive_cap_unknown: '브리지가 구동 상한을 알리지 않음',
  distance_duty_above_cap: 'duty가 브리지 구동 상한보다 큼: 표의 속도가 나오지 않는다',
  distance_needs_arm: '먼저 조종 활성화',
  distance_needs_lease: '조종권 정보 없음: 다시 조종 활성화',
  distance_needs_manual: '자율 run 중에는 안 됨',
  distance_move_running: '거리 이동이 이미 진행 중',
  distance_move_unknown: '이 화면의 거리 이동이 아님',
  distance_feedback_needs_full_move: '계획 시간까지 간 이동만 보정에 쓴다',
  distance_feedback_done: '이 이동은 이미 저장함',
  distance_actual_invalid: '실제 거리는 0 m 초과 10 m 이하',
  distance_not_configured: '서버에 거리 이동이 설정되지 않음',
  motor_bridge_unavailable: '브리지에 연결할 수 없음',
  control_not_owned: '다른 화면이 조종 중',
  stale_input_rearm_required: '입력표 만료: 다시 조종 활성화',
  http_failed: 'HTTP 요청 실패',
};
export const message = (code) => (code ? (MESSAGES[code] || String(code)) : '');
const fixed = (value, digits) => (num(value) === null ? '--' : value.toFixed(digits));
// The rows shown under the controls; the others are in the folded '계획 자세히'.
export const MAIN_ROWS = ['계획 시간', '예상 거리', '보정 배율', '구동 상한'];

// The duty range of a terrain of the table view (the server sends it; older views: the rows' lowest and highest).
export function dutyRange(terrain) {
  const duties = (terrain?.duties || []).map((d) => d.duty);
  const min = num(terrain?.duty_min) ?? (duties.length ? Math.min(...duties) : null);
  const max = num(terrain?.duty_max) ?? (duties.length ? Math.max(...duties) : null);
  return { min, max, def: num(terrain?.default_duty) ?? min };
}

// The slider range: the terrain's table range, its top cut at the bridge's drive cap (bridge.max_duty of the plan
// reply) when that is known. A cap below the lowest table duty leaves the slider at that duty (the plan refuses it).
export function sliderRange(terrain, bridge) {
  const range = dutyRange(terrain);
  const cap = num(bridge?.max_duty);
  if (range.min === null || cap === null) return range;
  return { ...range, max: Math.max(range.min, Math.min(range.max, cap)) };
}

// The longest distance of a direction (the table view's limits; 3.0 m forward, 2.0 m backward).
export function distanceLimit(table, direction) {
  const limits = table?.limits || {};
  return direction === 'backward' ? num(limits.distance_max_backward_m) ?? 2.0 : num(limits.distance_max_m) ?? 3.0;
}

// The table speed at a duty, as the server plans it (dashboard_server/drive_distance.py speed_at): the row itself or
// the straight line between the two rows around it; null outside the table. For the slider's readout before the plan.
export function speedAt(terrain, duty) {
  const rows = [...(terrain?.duties || [])].sort((a, b) => a.duty - b.duty);
  const row = rows.find((r) => r.duty === duty);
  if (row) return row.speed_m_s;
  for (let i = 1; i < rows.length; i += 1) {
    const lo = rows[i - 1], hi = rows[i];
    if (lo.duty < duty && duty < hi.duty) return lo.speed_m_s + (hi.speed_m_s - lo.speed_m_s) * (duty - lo.duty) / (hi.duty - lo.duty);
  }
  return null;
}

// The correction row: this duty's own tape samples, another duty's borrowed (same direction), or none.
export function correctionText(plan) {
  const c = plan.correction || { factor: plan.factor, samples: 0, from_duty: null };
  const way = plan.direction === 'backward' ? '뒤로 ' : '';
  if (num(c.from_duty) === null || !c.samples) return `${fixed(num(c.factor) ?? 1, 2)} (${way}줄자 기록 없음)`;
  if (c.from_duty === plan.duty) return `${fixed(c.factor, 2)} (${way}줄자 ${c.samples}번)`;
  return `${fixed(c.factor, 2)} (${c.from_duty} %의 ${way}배율을 빌림)`;
}

// The tape corrections of one terrain and direction as a row text, or null.
function measuredText(corrections, terrain, direction) {
  const entry = corrections?.[terrain];
  const list = (direction === 'backward' ? entry?.backward_duties : entry?.duties) || [];
  return list.length ? list.map((d) => `${d.duty} % ×${fixed(d.factor, 2)} (${d.samples}번)`).join(' · ') : null;
}

// The rows and the button state for one moment (pure: tested without a page).
// s: {table, corrections, plan, bridge, planError, move, result, armed, external, fresh, notice, input, direction}
// input: {terrain, duty} as the controls show them now (the plan may still be for the previous duty).
export function distView(s) {
  const rows = [];
  const plan = s.plan;
  const direction = s.direction === 'backward' ? 'backward' : 'forward';
  const terrain = s.table?.terrains?.find((t) => t.key === plan?.terrain);
  if (plan && plan.speed) {
    const sp = plan.speed;
    rows.push(['계획 시간', `${fixed(plan.drive_s, 2)} s · duty ${plan.duty} % (PWM ${plan.pwm})`]);
    rows.push(['예상 거리', `${fixed(plan.expected_m, 2)} m · 등급 ${plan.grade}${plan.direction === 'backward' ? ' · 뒤로' : ''}`]);
    rows.push(['속도', sp.interpolated && Array.isArray(sp.between)
      ? `${fixed(sp.value, 3)} m/s [${sp.grade}: 표의 ${sp.between[0]} %·${sp.between[1]} % 사이 직선 보간, ${sp.reference}]`
      : `${fixed(sp.value, 3)} m/s [${sp.grade}, ${sp.reference}, 구간 ${sp.segments}]`]);
    rows.push(['출발 지연', `${fixed(plan.lag.value, 2)} s [${plan.lag.grade}${plan.lag.grade === '가정' ? ': 100 % 출발 값을 씀' : ': 100 % 출발 9건'}]`]);
    rows.push(['놓은 뒤 미끄러짐', `${fixed(plan.coast.value, 2)} m [${plan.coast.grade}]`]);
    rows.push(['이 속도로 갈 수 있는 거리', `${fixed(plan.min_distance_m, 2)}–${fixed(plan.max_distance_m, 2)} m`]);
    rows.push(['보정 배율', correctionText(plan)]);
    const measured = measuredText(s.corrections, plan.terrain, plan.direction);
    if (measured) rows.push(['줄자 기록 있는 속도', measured]);
  }
  if (terrain?.duties?.length) rows.push(['표에 있는 duty', `${terrain.duties.map((d) => d.duty).join(' · ')} % (그 사이는 직선 보간)`]);
  const cap = num(s.bridge?.max_pwm);
  const capDuty = num(s.bridge?.max_duty);
  rows.push(['구동 상한', cap === null ? '--' : cap === 0 ? 'PWM 0 (브리지 설치 값): 바퀴가 돌지 않음' : `PWM ${cap}${capDuty === null ? '' : ` (duty ${capDuty} %까지)`}`]);
  rows.push(['자료', `${terrain ? `${terrain.label} ${terrain.day} · ` : ''}2026-10 기록, 조향 장치 전 차(ESP8266). 지금 차는 재지 않음${direction === 'backward' ? '. 뒤로는 앞으로 잰 표를 그대로 씀(가정)' : ''}`]);

  // The slider's readout: the duty now, its table speed at once, the drive time once the plan is for this duty.
  const inTerrain = s.table?.terrains?.find((t) => t.key === s.input?.terrain);
  const duty = num(s.input?.duty);
  let speed = '--';
  if (inTerrain && duty !== null) {
    const v = speedAt(inTerrain, duty);
    const planned = plan && plan.speed && plan.terrain === inTerrain.key && plan.duty === duty;
    const time = planned ? (plan.problem ? '이 거리 안 됨' : `${fixed(plan.drive_s, 2)} s`) : s.planError ? '--' : '계산 중';
    speed = `${duty} % · ${v === null ? '--' : `${v.toFixed(2)} m/s`} · ${time}`;
  }

  const running = s.move && s.move.state === 'driving';
  let why = null;
  if (!s.table) why = '표를 읽는 중';
  else if (s.external || running) why = '거리 이동 중';
  else if (!s.armed) why = message('distance_needs_arm');
  else if (!s.fresh) why = '조향 상태가 오래됨';
  else if (s.planError) why = message(s.planError);
  else if (!plan) why = '계획 중';
  else if ((plan.direction || 'forward') !== direction) why = '계획 중';
  else if (plan.problem) why = message(plan.problem);
  else if (s.bridge?.refusal) why = message(s.bridge.refusal);
  const go = { enabled: !why, title: why || '누르고 있는 동안만 간다. 놓으면 바로 선다.',
               text: `${DIRECTION_TEXT[direction]} 이동 (누르고 있기)` };

  let status = { kind: '', text: '버튼을 누르고 있는 동안만 간다' };
  if (running) {
    status = { kind: 'testing', text: `${DIRECTION_TEXT[s.move.direction] || ''} 거리 이동 중 · ${fixed(s.move.elapsed_s, 1)} / ${fixed(s.move.planned_s, 2)} s · 놓으면 선다`.trim() };
  } else if (s.result) {
    const r = s.result;
    const expected = num(r.expected_for_elapsed_m) ?? r.expected_m;
    const reason = REASON_TEXT[r.reason] || r.reason || '';
    const kind = r.reason === 'planned' ? 'ok' : r.state === 'failed' || r.state === 'stopped' ? 'bad' : 'warn';
    let text = `끝 (${reason}): 계획 ${fixed(r.planned_s, 2)} s · 실제 ${fixed(r.elapsed_s, 2)} s · 예상 ${fixed(expected, 2)} m`;
    if (r.feedback_open) text += ' · 줄자 거리는 보정·설정 탭에서';
    status = { kind, text };
  }
  if (s.notice && !running) status = s.notice;
  const feedback = { enabled: Boolean(s.result && s.result.feedback_open && !running), title: s.result?.feedback_open ? '실제 간 거리(m)를 저장' : '계획 시간까지 간 이동 뒤에' };
  const chip = running ? ['warn', '이동 중'] : go.enabled ? ['ok', '준비'] : ['', '대기'];
  return { rows, go, feedback, status, chip, speed, direction,
           main: rows.filter(([name]) => MAIN_ROWS.includes(name)), more: rows.filter(([name]) => !MAIN_ROWS.includes(name)) };
}

// The 보정·설정 tape part: the last move of this page, the corrections of the chosen terrain in both directions. A tape
// distance can be saved only for a move of the chosen terrain (note says so when another one is chosen).
export function tapeView(s) {
  const terrain = s.input?.terrain;
  const labelOf = (key) => s.table?.terrains?.find((item) => item.key === key)?.label || key || '--';
  const r = s.result;
  const moved = r && typeof r.terrain === 'string' && r.terrain ? r.terrain : null;
  const last = r ? `마지막 이동: ${moved ? `${labelOf(moved)} · ` : ''}${DIRECTION_TEXT[r.direction] || '앞으로'} ${fixed(num(r.distance_m), 1)} m · ${num(r.duty) === null ? '--' : `${r.duty} %`} · ${REASON_TEXT[r.reason] || r.reason || ''}`
    : '이 화면에서 끝난 이동 없음';
  const rows = [];
  for (const direction of ['forward', 'backward']) rows.push([`${DIRECTION_TEXT[direction]} 줄자 배율`, measuredText(s.corrections, terrain, direction) || '기록 없음']);
  const open = Boolean(r && r.feedback_open && !(s.move && s.move.state === 'driving'));
  const other = open && Boolean(moved && terrain && moved !== terrain);
  const note = other ? `마지막 이동은 ${labelOf(moved)}에서 한 것: 그 지형을 골라야 저장된다` : null;
  const enabled = open && !other;
  return { last, rows, note, chip: enabled ? ['ok', '입력 가능'] : other ? ['warn', '지형 다름'] : ['', '대기'],
           feedback: { enabled, title: enabled ? '실제 간 거리(m)를 저장' : note || '계획 시간까지 간 이동 뒤에' } };
}

export function initDriveDistance(page, options = {}) {
  const doc = options.document || document;
  const win = options.window || globalThis;
  const now = options.now || (() => Date.now());
  const setTimer = options.setInterval || ((fn, ms) => setInterval(fn, ms));
  const clearTimer = options.clearInterval || ((id) => clearInterval(id));
  const confirmFn = options.confirm || ((text) => globalThis.confirm(text));
  const debounceMs = options.planDebounceMs ?? PLAN_DEBOUNCE_MS;
  const $ = (id) => doc.getElementById(id);
  const root = $('steerDistPanel');
  const details = $('steerDistDetails');
  if (!root || !details) return null;
  const ui = { terrain: $('steerDistTerrain'), meters: $('steerDistMeters'), duty: $('steerDistDuty'), go: $('steerDistGo'),
               actual: $('steerDistActual'), tapeTerrain: $('steerDistTapeTerrain') };
  // The terrain choice lives in two places (수동 조종, the tape part of 보정·설정): one value.
  const terrainSelects = [ui.terrain, ui.tapeTerrain].filter(Boolean);
  const terrainKey = () => (ui.terrain || ui.tapeTerrain)?.value;
  const terrainLabel = (key) => s.table?.terrains.find((t) => t.key === key)?.label || key || '';
  const directionButtons = [...(root.querySelectorAll?.('[data-dist-direction]') || [])];
  const post = (path, body) => (page.controlRequest || page.postJson)(path, body);
  const s = { table: null, corrections: {}, plan: null, bridge: null, planError: null, move: null, result: null,
              armed: false, external: false, fresh: false, notice: null, tapeNotice: null, direction: 'forward' };
  let planTimer = null, holdTimer = null, pointer = null, moveId = null, starting = false, loaded = false, view = null;
  let dutyTouched = false;
  // Wanted: the folded part is open, or the tape part is in the shown tab (보정·설정).
  const wanted = () => details.open || Boolean(page.shown && $('steerDistTapePanel') && page.shown('steerDistTapePanel'));

  const ctl = () => (page.driveController ? page.driveController() : null);
  function syncCtl() {
    const c = ctl();
    s.armed = Boolean(c && c.lease);
    s.external = Boolean(c && c.external);
    s.fresh = Boolean(c && c.fresh(now()));
  }

  function setText(el, value) { if (el && el.textContent !== value) el.textContent = value; }
  function list(el, rows) {
    if (!el) return;
    const key = JSON.stringify(rows);
    if (el.dataset.key === key) return;
    el.dataset.key = key;
    el.replaceChildren(...rows.map(([name, value]) => {
      const row = doc.createElement('div');
      const dt = doc.createElement('dt'); dt.textContent = name;
      const dd = doc.createElement('dd'); dd.textContent = value;
      row.append(dt, dd);
      return row;
    }));
  }
  function draw() {
    syncCtl();
    s.input = { terrain: terrainKey(), duty: Number(ui.duty?.value) };
    view = distView(s);
    setText($('steerDistSpeed'), view.speed);
    const chip = $('steerDistState');
    if (chip) { chip.className = `status-chip ${view.chip[0]}`.trim(); setText(chip, view.chip[1]); }
    list($('steerDistRows'), $('steerDistMore') ? view.main : view.rows);
    list($('steerDistMore'), view.more);
    if (ui.go) {
      ui.go.disabled = !view.go.enabled && pointer === null; ui.go.title = view.go.title; ui.go.classList.toggle('active', pointer !== null);
      if (pointer === null) setText(ui.go, view.go.text);
    }
    for (const button of directionButtons) {
      const on = button.dataset.distDirection === s.direction;
      button.classList.toggle('active', on);
      button.setAttribute?.('aria-pressed', String(on));
      button.disabled = pointer !== null || moveId !== null;
    }
    const limit = distanceLimit(s.table, s.direction);
    if (ui.meters && ui.meters.max !== String(limit)) ui.meters.max = String(limit);
    setText($('steerDistMetersLabel'), `거리 m (0.2~${limit.toFixed(1)}${s.direction === 'backward' ? ', 뒤로' : ''})`);
    const status = $('steerDistStatus');
    if (status) { status.className = `test-result ${view.status.kind || ''}`.trim(); setText(status, view.status.text); }
    const toTape = $('steerDistToTape');
    if (toTape) toTape.hidden = !view.feedback.enabled;
    // The tape part (보정·설정 tab).
    const tape = tapeView(s);
    setText($('steerDistLast'), tape.last);
    list($('steerDistTapeRows'), tape.rows);
    const tapeChip = $('steerDistTapeState');
    if (tapeChip) { tapeChip.className = `status-chip ${tape.chip[0]}`.trim(); setText(tapeChip, tape.chip[1]); }
    const fb = $('steerDistFeedback');
    if (fb) { fb.disabled = !tape.feedback.enabled; fb.title = tape.feedback.title; }
    const reset = $('steerDistReset');
    if (reset) setText(reset, `이 지형 ${DIRECTION_TEXT[s.direction]} 보정 초기화`);
    const tapeStatus = $('steerDistTapeStatus');
    const tapeNote = s.tapeNotice || (tape.note ? { kind: 'warn', text: tape.note } : null);
    if (tapeStatus) {
      tapeStatus.hidden = !tapeNote;
      if (tapeNote) { tapeStatus.className = `test-result ${tapeNote.kind || ''}`.trim(); setText(tapeStatus, tapeNote.text); }
    }
  }

  function fillTerrains() {
    if (!terrainSelects.length || !s.table) return;
    for (const select of terrainSelects) {
      select.replaceChildren(...s.table.terrains.map((t) => {
        const opt = doc.createElement('option');
        opt.value = t.key; opt.textContent = `${t.label} (${t.day.slice(5).replace('-', '/')})`;
        return opt;
      }));
    }
    syncTerrain(terrainSelects[0]);
    fillDuties();
  }
  // The other terrain choice follows the one that changed.
  function syncTerrain(from) {
    for (const select of terrainSelects) if (select !== from && select.value !== from.value) select.value = from.value;
  }
  // The speed slider spans the terrain's table duties up to the bridge cap; a duty still inside the new range stays,
  // otherwise the terrain's default (its lowest table duty, the first choice of the 2026-11-17 page).
  function fillDuties() {
    const terrain = s.table?.terrains.find((t) => t.key === terrainKey()) || s.table?.terrains[0];
    if (!ui.duty || !terrain) return;
    const range = sliderRange(terrain, s.bridge);
    if (range.min === null) return;
    const keep = dutyTouched ? Number(ui.duty.value) : NaN;
    ui.duty.min = String(range.min);
    ui.duty.max = String(range.max);
    ui.duty.step = '1';
    const def = Math.min(range.def, range.max);
    ui.duty.value = String(Number.isInteger(keep) && keep >= range.min && keep <= range.max ? keep : def);
  }
  const inputs = () => ({ terrain: terrainKey(), duty: Number(ui.duty?.value), distance_m: Number(ui.meters?.value),
                          ...(s.direction === 'backward' ? { direction: 'backward' } : {}) });

  async function load() {
    try {
      const reply = await page.getJson('/api/rover/distance');
      if (reply && reply.table) {
        s.table = reply.table; s.corrections = reply.corrections || {};
        if (!loaded) { loaded = true; fillTerrains(); }
      }
    } catch (_) { s.notice = { kind: 'bad', text: `표를 읽지 못함: ${message('http_failed')}` }; }
    replan();
  }
  async function replanNow() {
    planTimer = null;
    if (!s.table) return draw();
    try {
      const reply = await post('/api/rover/distance/plan', inputs());
      s.plan = reply?.plan?.speed ? reply.plan : null;
      s.planError = reply?.plan && !reply.plan.speed ? reply.plan.problem : null;
      const capBefore = num(s.bridge?.max_duty);
      s.bridge = reply?.bridge || null;
      if (num(s.bridge?.max_duty) !== capBefore) {           // the bridge cap moved: the slider follows it
        const before = ui.duty?.value;
        fillDuties();
        if (ui.duty && ui.duty.value !== before) { replan(); return; }
      }
    } catch (_) { s.plan = null; s.planError = 'http_failed'; }
    draw();
  }
  function replan() { clearTimeout(planTimer); planTimer = setTimeout(replanNow, debounceMs); draw(); }

  // ---- the held button ----------------------------------------------------------------------------------------
  function finish(reply) {
    const c = ctl();
    if (reply?.move) { s.move = reply.move; s.result = reply.move.state === 'driving' ? null : reply.move; }
    if (reply?.chain) {
      c?.endExternal(reply.chain);
    } else if (reply?.lease_lost) {
      c?.endExternal(null, 'distance_move_lost');
    } else {
      return false;
    }
    clearTimer(holdTimer); holdTimer = null;
    moveId = null;
    if (s.move && s.move.state !== 'driving') s.move = null;
    replan();
    return true;
  }
  async function hold() {
    if (moveId === null) return;
    const c = ctl();
    if (c && !c.external) { stopTimers('stopped'); return; }       // STOP or another stop dropped the lease
    try {
      const reply = await post('/api/rover/distance/hold', { move_id: moveId });
      if (reply && reply.ok) finish(reply);
    } catch (_) { /* the server stops on its own after 0.4 s without a heartbeat */ }
    draw();
  }
  async function start() {
    if (starting || moveId !== null) return;
    const c = ctl();
    if (!view?.go.enabled || !c) return;
    starting = true;
    s.notice = null; s.result = null; s.tapeNotice = null;
    let chain = null;
    for (let i = 0; i < 10 && !chain && pointer !== null; i += 1) {
      chain = c.beginExternal();
      if (!chain) await new Promise((resolve) => setTimeout(resolve, 30));   // a request of the page in flight
    }
    if (!chain) { starting = false; draw(); return; }
    let reply = null;
    try {
      reply = await post('/api/rover/distance/start', { ...inputs(), ...chain });
    } catch (_) { reply = { ok: false, error: 'http_failed' }; }
    starting = false;
    if (!reply || !reply.ok) {
      c.endExternal({ lease: chain.lease, ticket: chain.ticket, seq: chain.seq });
      s.notice = { kind: 'bad', text: `거리 이동 거절: ${message(reply?.error)}` };
      replan();
      return;
    }
    moveId = reply.move.id;
    s.move = reply.move;
    holdTimer = setTimer(hold, HOLD_MS);
    if (pointer === null) await release('released');                  // let go while the start was on its way
    draw();
  }
  async function release(why) {
    if (moveId === null) return;
    for (let i = 0; i < RELEASE_RETRIES; i += 1) {
      try {
        const reply = await post('/api/rover/distance/release', { move_id: moveId, reason: why });
        if (reply && reply.ok) {
          if (finish(reply)) return draw();
          if (reply.move && reply.move.state === 'driving') continue;
        }
      } catch (_) { /* try again */ }
    }
    stopTimers('release_failed');
  }
  function stopTimers(why) {
    clearTimer(holdTimer); holdTimer = null;
    const c = ctl();
    if (c && c.external) c.endExternal(null, 'distance_move_lost');
    if (why !== 'stopped' && moveId !== null) s.notice = { kind: 'bad', text: '거리 이동의 끝을 확인하지 못함: 다시 조종 활성화' };
    moveId = null; s.move = null;
    draw();
  }
  function letGo(why) {
    const id = pointer;
    pointer = null;
    if (id !== null && typeof id === 'number') { try { ui.go?.releasePointerCapture?.(id); } catch (_) { /* gone */ } }
    if (moveId !== null) release(why); else draw();
  }

  ui.go?.addEventListener('pointerdown', (event) => {
    event.preventDefault?.();
    if (pointer !== null) return;
    syncCtl(); view = distView(s);
    if (!view.go.enabled) { draw(); return; }
    pointer = event.pointerId;
    try { ui.go.setPointerCapture?.(event.pointerId); } catch (_) { /* its cancel ends it */ }
    start();
  });
  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    ui.go?.addEventListener(name, (event) => { if (event.pointerId === pointer) letGo('released'); });
  }
  ui.go?.addEventListener('contextmenu', (event) => event.preventDefault?.());
  $('steerDistStop')?.addEventListener('click', () => {
    pointer = null;
    ctl()?.abandon?.('operator_stop');
    Promise.resolve(post('/api/rover/stop', { reason: 'distance_stop' })).catch(() => null);
    stopTimers('stopped');
  });
  for (const button of directionButtons) {
    button.addEventListener('click', () => {
      const next = button.dataset.distDirection === 'backward' ? 'backward' : 'forward';
      if (next === s.direction || pointer !== null || moveId !== null) return;
      s.direction = next;
      s.plan = null; s.notice = null;
      const limit = distanceLimit(s.table, next);
      if (ui.meters && Number(ui.meters.value) > limit) ui.meters.value = String(limit);
      replan();
    });
  }
  for (const el of terrainSelects) {
    el.addEventListener('change', () => { syncTerrain(el); fillDuties(); s.notice = null; s.tapeNotice = null; replan(); });
  }
  for (const name of ['input', 'change']) ui.duty?.addEventListener(name, () => { dutyTouched = true; });
  for (const el of [ui.duty, ui.meters]) el?.addEventListener('input', () => { s.notice = null; replan(); });
  ui.duty?.addEventListener('change', () => { s.notice = null; replan(); });
  $('steerDistFeedback')?.addEventListener('click', async () => {
    const actual = Number(ui.actual?.value);
    s.input = { terrain: terrainKey(), duty: Number(ui.duty?.value) };
    if (!s.result || !tapeView(s).feedback.enabled) { draw(); return; }      // only a move of the chosen terrain
    try {
      const reply = await post('/api/rover/distance/feedback', { move_id: s.result.id, actual_m: actual });
      if (reply && reply.ok) {
        s.corrections = reply.corrections || s.corrections;
        s.result = { ...s.result, feedback_open: false };
        const c = reply.correction;
        const at = num(s.result.duty) === null ? '' : ` ${s.result.duty} %`;
        const way = DIRECTION_TEXT[s.result.direction] || '앞으로';
        const where = terrainLabel(s.result.terrain || terrainKey());
        s.tapeNotice = { kind: 'ok', text: `저장: ${where} ${way}${at} · 실제 ${actual} m / 모델 ${reply.sample.model_m} m · 배율 ${c ? `${fixed(c.factor, 2)} (줄자 ${c.samples}번)` : '--'}` };
      } else s.tapeNotice = { kind: 'bad', text: `저장 안 됨: ${message(reply?.error)}` };
    } catch (_) { s.tapeNotice = { kind: 'bad', text: `저장 안 됨: ${message('http_failed')}` }; }
    replan();
  });
  $('steerDistReset')?.addEventListener('click', async () => {
    const terrain = terrainKey();
    const way = DIRECTION_TEXT[s.direction];
    if (!terrain || !confirmFn(`${terrainLabel(terrain)}의 ${way} 줄자 보정 기록(모든 속도)을 지우고 배율을 1.00 으로 되돌립니다. 계속할까요?`)) return;
    try {
      const reply = await post('/api/rover/distance/reset', { terrain, direction: s.direction });
      if (reply && reply.ok) { s.corrections = reply.corrections || {}; s.tapeNotice = { kind: 'ok', text: `${terrainLabel(terrain)} ${way} 보정 초기화: 배율 1.00` }; }
      else s.tapeNotice = { kind: 'bad', text: `초기화 안 됨: ${message(reply?.error)}` };
    } catch (_) { s.tapeNotice = { kind: 'bad', text: `초기화 안 됨: ${message('http_failed')}` }; }
    replan();
  });
  win.addEventListener?.('blur', () => { if (pointer !== null) letGo('blur'); });
  win.addEventListener?.('ubts:rover-tab', () => { if (pointer !== null) letGo('tab_changed'); });
  doc.addEventListener?.('visibilitychange', () => { if (doc.hidden && pointer !== null) letGo('page_hidden'); });
  const wake = () => { if (wanted() && !loaded) load(); else if (wanted()) draw(); };
  details.addEventListener('toggle', wake);
  const redraw = setTimer(() => { if (wanted()) draw(); }, 500);
  if (wanted()) load();
  return { draw, load, view: () => view, state: s, stop: () => clearTimer(redraw), routeChanged: wake };
}
