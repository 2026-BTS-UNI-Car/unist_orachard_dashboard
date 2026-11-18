// The 자율주행 tab of Rover Control (UI-08; 2026-11-19 tab, GPS choice, run recording and monitoring view): one press to
// start, STOP, the operator heartbeat while the screen is visible, and the state from GET /api/executor/status
// (docs/STEERING_SERVER_KO.md 6). Loaded by static/app/14-steer.js on a front-steer dashboard. Requests use the page's
// postJson/getJson. The page never sees the executor token.
// - GPS per run (user: "gps를 자율주행에 쓸지 안쓸지를 이제 결정해서 자율주행을 굴릴 수 있으면"): the choice goes with 준비
//   (POST /api/executor/start gnss_mode; '' = the executor's setting, the field is not sent). It is kept per browser. A
//   refusal of the executor (an older one, RTK not fixed) is shown in its words.
// - Recording (user: "자율주행 탭을 새로 만들거면 거기에도 녹화버튼 넣어줘. 아니면 아예 자율주행시에는 반드시 모든 센서가
//   녹화되도록"): a record button of its own (every sensor, tag autonomy), and, with '출발하면 모든 센서 기록' on (default, per
//   browser), a recording of every sensor starts right before 자율 주행 시작; without it the go is not sent. A recording
//   that is already running is kept. The one this panel started stops when the run ends or is stopped (ended, latched,
//   idle); a recording somebody started by hand is never stopped here.
// - Monitoring view: the tiles of auto-model.mjs monitorView (executor status of this poll, the steering panel's bridge
//   status: no request of its own) and status.metrics. The executor's local grid picture is static/steer/local-grid.mjs.
// - The heartbeat keeps going in every tab of Rover Control (STOP stays in the status bar); leaving the page or hiding
//   it stops it, and then the executor stops the vehicle by itself.
import { GNSS_MODES, HEARTBEAT_MS, RUN_RECORD_SENSORS, START_ERROR, STATUS_MS, autoView, heartbeatWanted, metricsView, modeChip,
         monitorView, newSession, prepareRun, primaryAction, recordAfterRun, recordBeforeGo, runActive, scaleLine, scaleOffer } from './auto-model.mjs';
import { ERROR_TEXT } from './tuning-model.mjs';

const HTTP_TEXT = { 403: '대시보드 세션 없음: 화면을 새로 고친다', 409: '실행기가 거절했다', 502: '실행기 연결 안 됨',
                    503: '실행기 설정 없음', 400: '요청 값 오류', 413: '요청이 너무 큼' };
const GNSS_KEY = 'ubts-auto-gnss-mode-v1';
const RECORD_KEY = 'ubts-auto-record-runs-v1';
const SHARED_BRIDGE_MS = 2000;
const BADGE_STATES = new Set(['armed', 'running', 'paused']);

export function initAutoPanel(page) {
  const $ = (id) => document.getElementById(id);
  const root = $('autoPanel');
  if (!root) return null;
  const session = newSession();
  let reply = null, pollTimer = null, beatTimer = null, polling = false, beating = false, busy = false, lastError = '';
  let autoRecordId = null, recordBusy = false, recordNote = '';
  const store = (() => { try { return globalThis.localStorage || null; } catch (_) { return null; } })();
  const read = (key) => { try { return store?.getItem(key) ?? null; } catch (_) { return null; } };
  const write = (key, value) => { try { store?.setItem(key, value); } catch (_) { /* this load only */ } };

  const gnssSelect = $('autoGnssMode');
  if (gnssSelect) {
    const kept = read(GNSS_KEY);
    if (kept !== null && (kept === '' || GNSS_MODES.includes(kept))) gnssSelect.value = kept;
    gnssSelect.addEventListener('change', () => write(GNSS_KEY, gnssSelect.value));
  }
  const gnssChoice = () => (gnssSelect && GNSS_MODES.includes(gnssSelect.value) ? gnssSelect.value : '');
  const recordBox = $('autoRecordRuns');
  if (recordBox) {
    const kept = read(RECORD_KEY);
    if (kept !== null) recordBox.checked = kept !== '0';
    recordBox.addEventListener('change', () => { write(RECORD_KEY, recordBox.checked ? '1' : '0'); draw(); });
  }
  const recordWanted = () => (recordBox ? recordBox.checked : true);
  const recording = () => (page.recording ? page.recording.view() : null);

  const active = () => page.currentRoute() === '/rover-control' && !document.hidden;

  async function poll() {
    if (polling) return;
    polling = true;
    try { reply = await page.getJson('/api/executor/status'); } catch (_) { reply = reply ? { ...reply } : null; }
    finally { polling = false; }
    afterRun();
    draw();
  }
  async function beat() {
    if (beating || !active() || !heartbeatWanted(reply)) return;
    beating = true;
    try {
      await (page.controlRequest || page.postJson)('/api/executor/heartbeat', { session });
      if (lastError.startsWith('연결 신호')) lastError = '';
    } catch (err) {
      lastError = `연결 신호 거절: ${httpText(err)}`;
    } finally { beating = false; }
  }
  // The executor's own refusal when the reply carried one (err.body.error), else the HTTP status in words.
  function httpText(err) {
    const code = err && err.body && typeof err.body.error === 'string' ? err.body.error : null;
    if (code) return START_ERROR[code] || ERROR_TEXT[code] || code;
    const status = Number(err && err.status) || Number(String(err && err.message || '').split(': ').pop());
    return HTTP_TEXT[status] || String(err && err.message || err);
  }
  function start() {
    if (!active()) return;
    if (!pollTimer) { pollTimer = setInterval(poll, STATUS_MS); poll(); }
    if (!beatTimer) beatTimer = setInterval(beat, HEARTBEAT_MS);
  }
  function stop() {
    clearInterval(pollTimer); pollTimer = null;
    clearInterval(beatTimer); beatTimer = null;    // no heartbeat: the executor stops the vehicle by itself
  }

  const ACTION_TEXT = { start: '준비', arm: 'Arm', go: '출발', stop: 'STOP', ack: '래치 확인', end: 'run 끝내기' };
  async function request(action, body = {}) {
    // Start, STOP and the other actions use the page's control transport (NET-05) where there is one.
    try { return { ok: true, reply: await (page.controlRequest || page.postJson)(`/api/executor/${action}`, body) }; }
    catch (err) { lastError = `${ACTION_TEXT[action] || action} 거절: ${httpText(err)}`; return { ok: false, reply: null }; }
  }
  const send = async (action, body = {}) => (await request(action, body)).ok;
  // A status read that is not skipped by a poll in flight (the prepare step needs one sent after the start reply).
  async function readStatus() {
    try { reply = await page.getJson('/api/executor/status'); } catch (_) { return null; }
    draw();
    return reply;
  }
  // Right before a go: a recording of every sensor (when wanted), or no go.
  async function recordForGo() {
    const decision = recordBeforeGo(recording(), recordWanted());
    if (decision === 'skip' || decision === 'keep') { recordNote = decision === 'keep' ? '이미 기록 중: 그 기록을 그대로 쓴다' : ''; return true; }
    if (decision === 'unknown' || !page.recording) {
      lastError = '기록 상태를 몰라 출발하지 않음 (기록 없이 가려면 "출발하면 모든 센서 기록"을 끈다)';
      return false;
    }
    const answer = await page.recording.start({ sensors: RUN_RECORD_SENSORS, tag: 'autonomy' });
    if (!answer || answer.ok === false) {
      lastError = `기록을 시작하지 못해 출발하지 않음: ${answer?.error || '이유 모름'}`;
      return false;
    }
    // '?' until the recorder names it (the next status read)
    autoRecordId = answer.recording_id || answer.status?.recording_id || recording()?.id || '?';
    recordNote = '출발 전에 모든 센서 기록을 시작했다';
    return true;
  }
  function afterRun() {
    const rec = recording();
    if (autoRecordId === '?' && rec?.active && rec.id) autoRecordId = rec.id;
    if (autoRecordId && rec?.known && !rec.active) { autoRecordId = null; return; }     // stopped elsewhere
    if (!recordAfterRun(reply, autoRecordId, rec) || recordBusy) return;
    recordBusy = true;
    Promise.resolve(page.recording.stop()).then((answer) => {
      recordNote = answer && answer.ok !== false ? 'run 이 끝나 자동 기록을 멈췄다' : `자동 기록을 멈추지 못함: ${answer?.error || '이유 모름'}`;
      if (answer && answer.ok !== false) autoRecordId = null;
    }).catch(() => { recordNote = '자동 기록을 멈추지 못함'; }).finally(() => { recordBusy = false; draw(); });
  }
  async function primary() {
    const { action } = primaryAction(reply);
    if (!action || busy) return;
    busy = true; lastError = ''; draw();
    try {
      if (action === 'prepare') {
        const raw = $('autoRowLen')?.value.trim();
        const rowLen = raw ? Number(raw) : null;
        if (raw && !(rowLen > 0)) { lastError = '열 길이는 0보다 큰 수이거나 비워 둔다'; return; }
        const result = await prepareRun({ send: request, readStatus, rowLen, gnssMode: gnssChoice() || null,
                                          sleep: (ms) => new Promise((done) => setTimeout(done, ms)) });
        if (!result.ok && result.step === 'wait') lastError = `준비: 실행기가 run을 만들었다고 알리지 않았다(상태 ${result.runState || '모름'})`;
        if (!result.ok && result.step === 'latched') lastError = '준비: 실행기가 래치 상태다. 래치 확인부터';
      } else if (action === 'go') {
        if (!(await recordForGo())) return;
        await beat();                                   // the link is fresh before the go (go_blockers: operator link)
        await send('go', { session });
      } else {
        await send(action);
      }
    } finally {
      busy = false;
      await poll();
    }
  }
  // The tab's own record button: every sensor, tag autonomy; a recording this button started is the operator's.
  async function recordButton() {
    const rec = recording();
    if (!page.recording || !rec || !rec.known || recordBusy) return;
    recordBusy = true; draw();
    try {
      const answer = rec.active ? await page.recording.stop() : await page.recording.start({ sensors: RUN_RECORD_SENSORS, tag: 'autonomy' });
      if (rec.active && answer && answer.ok !== false) autoRecordId = null;
      recordNote = answer && answer.ok === false ? `기록 ${rec.active ? '중지' : '시작'} 거절: ${answer.error || '이유 모름'}` : '';
    } finally { recordBusy = false; draw(); }
  }
  // Every STOP of the page also stops a run (their own handlers keep sending the bridge STOP).
  function stopRun() {
    if (!reply || reply.configured === false) return;
    if (runActive(reply) || !reply.ok) send('stop').then(poll);   // a run, or a state not known: the executor latches
  }
  // PW-10: the answer to the online-scale offer after a run (POST /api/executor/params; the reply body is kept on a
  // refusal so the executor's reason shows). The status poll above carries the offer: no polling of its own.
  async function answerScale(answer) {
    const offer = scaleOffer(reply);
    if (!offer || busy) return;
    if (answer === 'accept' && !window.confirm(`${offer.text}\n적용하면 실행기의 조정 값이 바뀐다(조정 화면의 기록에 남는다). 파일 저장은 조정 화면에서.`)) return;
    busy = true; lastError = ''; draw();
    try {
      const res = await fetch('/api/executor/params', { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' },
                                                        body: JSON.stringify({ online_scale: answer, run_id: offer.runId }) });
      let body = null;
      try { body = await res.json(); } catch (_) { body = null; }
      if (!res.ok || !body?.ok) lastError = `배율 ${answer === 'accept' ? '저장' : '닫기'} 거절: ${ERROR_TEXT[body?.error] || body?.error || `HTTP ${res.status}`}`;
    } catch (_) {
      lastError = `배율 ${answer === 'accept' ? '저장' : '닫기'}: ${ERROR_TEXT.executor_unreachable}`;
    } finally {
      busy = false;
      await poll();
    }
  }
  $('autoScaleAccept')?.addEventListener('click', () => answerScale('accept'));
  $('autoScaleDismiss')?.addEventListener('click', () => answerScale('dismiss'));
  $('autoPrimary')?.addEventListener('click', primary);
  $('autoEnd')?.addEventListener('click', () => { if (!busy) send('end').then(poll); });
  $('autoRecordButton')?.addEventListener('click', recordButton);
  for (const id of ['frameStopButton', 'roverStopButton', 'autoStopButton']) $(id)?.addEventListener('click', stopRun);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && page.currentRoute() === '/rover-control') stopRun();
  });
  document.addEventListener('visibilitychange', () => (document.hidden ? stop() : start()));

  function setText(el, value) { if (el && el.textContent !== value) el.textContent = value; }
  function chip(el, level, value) {
    if (!el) return;
    const name = `status-chip${level ? ` ${level}` : ''}`;
    if (el.className !== name) el.className = name;
    setText(el, value);
  }
  // Tiles [label, value, sub, level] into a <dl class="monitor-grid"> (or a detail list).
  function tiles(list, rows, isStale = false) {
    if (!list) return;
    list.classList?.toggle('stale', isStale);
    const key = JSON.stringify(rows);
    if (list.dataset.key === key) return;
    list.dataset.key = key;
    list.replaceChildren(...rows.map(([name, value, sub, level]) => {
      const row = document.createElement('div');
      if (level) row.dataset.level = level;
      const dt = document.createElement('dt'); dt.textContent = name;
      const dd = document.createElement('dd'); dd.textContent = value;
      row.append(dt, dd);
      if (sub) { const small = document.createElement('small'); small.textContent = sub; row.append(small); }
      return row;
    }));
  }
  function drawRecording() {
    const rec = recording();
    const button = $('autoRecordButton');
    if (button) {
      button.disabled = recordBusy || !rec || !rec.known;
      setText(button, recordBusy ? '보내는 중…' : rec?.active ? '기록 중지' : '기록 시작 (모든 센서)');
    }
    const state = $('autoRecordState');
    if (state) {
      if (!rec || !rec.known) chip(state, '', '기록 확인 중');
      else if (rec.active) chip(state, 'ok', autoRecordId && rec.id === autoRecordId ? '기록 중 · run 자동' : '기록 중');
      else chip(state, recordWanted() ? '' : 'warn', recordWanted() ? '출발 때 자동 기록' : '자동 기록 꺼짐');
    }
    return rec;
  }
  function draw() {
    const view = autoView(reply);
    chip($('autoState'), view.chip[0], view.chip[1]);
    const mode = $('autoMode');
    const modeView = modeChip(reply);
    if (mode) {
      mode.hidden = !modeView;
      if (modeView) chip(mode, modeView[0], modeView[1]);
    }
    const button = $('autoPrimary');
    const action = primaryAction(reply);
    if (button) { setText(button, busy ? '보내는 중…' : action.text); button.disabled = busy || !action.enabled; }
    const end = $('autoEnd');
    if (end) end.disabled = busy || !runActive(reply) || reply.status.run_state === 'running';
    if (gnssSelect) gnssSelect.disabled = busy || runActive(reply);
    const line = $('autoScale');
    if (line) {
      const text = scaleLine(reply);
      line.hidden = text === null;
      const value = $('autoScaleValue');
      if (value && text !== null) setText(value, text);
    }
    const box = $('autoScaleOffer');
    if (box) {
      const offer = view.stale ? null : scaleOffer(reply);
      box.hidden = !offer;
      if (offer) {
        setText($('autoScaleText'), offer.text);
        const accept = $('autoScaleAccept');
        if (accept) { accept.hidden = !offer.canAccept; accept.disabled = busy; }
        const dismiss = $('autoScaleDismiss');
        if (dismiss) { dismiss.disabled = busy; setText(dismiss, offer.canAccept ? '저장 안 함' : '닫기'); }
      }
    }
    const notice = $('autoNotice');
    const [kind, text] = lastError ? ['bad', lastError] : (view.notice || (recordNote ? ['testing', recordNote] : ['', '']));
    if (notice) { notice.hidden = !text; notice.className = `test-result ${kind}`.trim(); setText(notice, text); }
    tiles($('autoDetails'), view.rows.map(([name, value]) => [name, value, '', '']), view.stale);
    // The tab badge: a run that holds the vehicle shows in the tab row from every tab.
    const badge = $('roverTabAutoBadge');
    if (badge) {
      const s = reply && reply.ok && reply.status ? reply.status : null;
      const on = Boolean(s && !view.stale && BADGE_STATES.has(s.run_state));
      badge.hidden = !on;
      if (on) setText(badge, view.chip[1]);
    }
    // Monitoring view.
    const rec = drawRecording();
    const shared = page.driveStatus?.();
    const bridge = shared && Date.now() - shared.at <= SHARED_BRIDGE_MS ? shared.reply : null;
    const monitor = monitorView(reply, bridge, rec ? { ...rec, auto: Boolean(autoRecordId && rec.id === autoRecordId) } : null, gnssChoice());
    tiles($('autoMonitorGrid'), monitor.tiles, monitor.stale);
    const monitorState = $('autoMonitorState');
    if (monitorState) {
      const tag = `tag ${monitor.stale ? 'bad' : view.chip[0]}`.trim();
      if (monitorState.className !== tag) monitorState.className = tag;
      setText(monitorState, monitor.stale ? '끊김' : view.chip[1]);
    }
    const metrics = metricsView(reply);
    const block = $('autoMetricsBlock');
    if (block) block.hidden = !metrics;
    if (metrics) { tiles($('autoMetricsGrid'), metrics.tiles, monitor.stale); setText($('autoMetricsSource'), metrics.source); }
  }

  function routeChanged() { if (active()) start(); else stop(); draw(); }
  routeChanged();
  return { routeChanged, draw, session: () => session, state: () => reply, autoRecording: () => autoRecordId };
}
