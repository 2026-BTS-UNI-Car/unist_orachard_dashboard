// Headless Chrome driver of the slow-link bench (raw DevTools protocol, no extra packages).
//
// One run has up to three parts, all on the driving screen:
//   cold load  - first open with an empty cache: requests, bytes, time until the page can be driven
//   warm load  - the same address opened again with the cache filled
//   live       - streams and polling for a set time; optionally the manual-drive exercise, the live map, a hidden tab
//
// The manual-drive exercise uses the page's own controls: the activation button, a held W key, the STOP button.
// The requests it causes go to the dashboard under test, whose control socket is the bench's recording endpoint.
//
// usage: node browser_runner.mjs <config.json>
import {spawn} from 'node:child_process';
import {mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync, mkdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as sleep} from 'node:timers/promises';

const cfg = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const HERE = dirname(fileURLToPath(import.meta.url));
const INSTRUMENT = readFileSync(join(HERE, 'page_instrument.js'), 'utf8');
const CHROME_CANDIDATES = [cfg.chrome, process.env.SLOW_LINK_CHROME, 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].filter(Boolean);
const CHROME = CHROME_CANDIDATES.find(path => existsSync(path));
if (!CHROME) throw Error('Chrome not found; set "chrome" in the config or SLOW_LINK_CHROME');
mkdirSync(cfg.out, {recursive: true});
const P = cfg.phases ?? {};
const ROUTE = cfg.route ?? '/rover-control';

// ---------------------------------------------------------------- clock check (server minus client, best of 15)
async function clockOffset(url = cfg.clock_url) {
  if (!url) return null;
  let best = null;
  for (let i = 0; i < 15; i++) {
    const t0 = Date.now(); const r = await (await fetch(url)).json(); const t1 = Date.now();
    const rtt = t1 - t0, off = r.t * 1000 - (t0 + t1) / 2;
    if (!best || rtt < best.rtt) best = {rtt, off};
    await sleep(30);
  }
  return {offset_ms: Math.round(best.off * 10) / 10, rtt_ms: best.rtt};
}

// ---------------------------------------------------------------- browser and protocol plumbing
const port = 9600 + Math.floor(Math.random() * 300);
const profile = mkdtempSync(join(tmpdir(), 'slow-link-chrome-'));
const W0 = cfg.clients?.[0]?.width ?? 1366, H0 = cfg.clients?.[0]?.height ?? 900;
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, '--remote-debugging-address=127.0.0.1', `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-background-networking', '--disable-component-update',
  '--disable-sync', '--disable-default-apps', '--no-pings', '--metrics-recording-only',
  // Nothing but 127.0.0.1 can be resolved: the page under test and the browser itself stay local.
  '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
  '--enable-precise-memory-info', '--autoplay-policy=no-user-gesture-required', `--window-size=${W0},${H0}`, ...(cfg.chrome_args ?? []), 'about:blank'],
  {stdio: 'ignore'});
let version = null;
for (let i = 0; i < 150 && !version; i++) { try { version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); } catch { await sleep(100); } }
if (!version) { chrome.kill(); throw Error('Chrome did not open its debugging port'); }
const ws = new WebSocket(version.webSocketDebuggerUrl);
await new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = bad; });
// Every command has an answer deadline. A tab whose renderer crashed or that was closed fails its waiting and
// later commands at once (with the reason), so a lost tab ends the run with an error instead of hanging it.
let nextId = 1; const pending = new Map(); const listeners = []; const lostSessions = new Map();
const COMMAND_TIMEOUT_MS = (cfg.command_timeout_s ?? 60) * 1000;
function loseSession(sessionId, reason) {
  if (!sessionId || lostSessions.has(sessionId)) return;
  lostSessions.set(sessionId, {reason, t: Date.now()});
  for (const [id, entry] of pending) if (entry.sessionId === sessionId) { pending.delete(id); clearTimeout(entry.timer); entry.bad(Error(`tab lost: ${reason}`)); }
}
ws.onmessage = e => {
  // WebSocket frame events carry the whole frame (camera pictures); they are not needed and not parsed.
  if (e.data.length > 2048 && e.data.startsWith('{"method":"Network.webSocketFrame')) return;
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { const entry = pending.get(m.id); pending.delete(m.id); clearTimeout(entry.timer); entry.done(m); return; }
  if (m.method === 'Inspector.targetCrashed') loseSession(m.sessionId, 'renderer crashed');
  if (m.method === 'Inspector.detached') loseSession(m.sessionId, `inspector detached (${m.params?.reason ?? '?'})`);
  if (m.method === 'Target.detachedFromTarget') loseSession(m.params?.sessionId, 'tab closed or detached');
  for (const l of listeners) l(m);
};
const send = (method, params = {}, sessionId, timeoutMs = COMMAND_TIMEOUT_MS) => new Promise((ok, bad) => {
  if (sessionId && lostSessions.has(sessionId)) { bad(Error(`tab lost: ${lostSessions.get(sessionId).reason}`)); return; }
  const id = nextId++;
  const timer = setTimeout(() => { if (pending.delete(id)) bad(Error(`${method}: no answer within ${timeoutMs} ms`)); }, timeoutMs);
  pending.set(id, {sessionId, timer, bad, done: m => m.error ? bad(Error(method + ': ' + m.error.message)) : ok(m.result)});
  ws.send(JSON.stringify(sessionId ? {id, method, params, sessionId} : {id, method, params}));
});

const report = {config: cfg, chrome: version.Browser, started_utc: new Date().toISOString(), clients: [], notes: [], phase_marks: []};
report.clock = await clockOffset();
const mark = name => report.phase_marks.push({t: Date.now(), mark: name});

class Client {
  constructor(index, spec) { this.index = index; this.spec = spec; this.samples = []; this.metrics = []; this.console = []; this.loads = {}; }
  async open() {
    const s = this.spec;
    this.ctx = (await send('Target.createBrowserContext', {disposeOnDetach: true})).browserContextId;
    this.targetId = (await send('Target.createTarget', {url: 'about:blank', browserContextId: this.ctx, newWindow: true,
      width: s.width ?? 1366, height: s.height ?? 900})).targetId;
    this.session = (await send('Target.attachToTarget', {targetId: this.targetId, flatten: true})).sessionId;
    listeners.push(m => {
      if (m.sessionId !== this.session) return;
      if (m.method === 'Runtime.exceptionThrown' && this.console.length < 300)
        this.console.push([Date.now(), 'exception', (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text || '').slice(0, 300)]);
      if (m.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(m.params.type) && this.console.length < 300)
        this.console.push([Date.now(), m.params.type, (m.params.args || []).map(a => a.value ?? a.description ?? '').join(' ').slice(0, 300)]);
      if (m.method === 'Log.entryAdded' && ['error', 'warning'].includes(m.params.entry.level) && this.console.length < 300)
        this.console.push([Date.now(), 'log:' + m.params.entry.level, `${m.params.entry.text} ${m.params.entry.url || ''}`.slice(0, 300)]);
    });
    await this.cmd('Page.enable'); await this.cmd('Runtime.enable'); await this.cmd('Performance.enable'); await this.cmd('Log.enable');
    await this.cmd('Inspector.enable');
    await this.cmd('Page.addScriptToEvaluateOnNewDocument', {source: INSTRUMENT});
    await this.cmd('Emulation.setDeviceMetricsOverride', {width: s.width ?? 1366, height: s.height ?? 900, deviceScaleFactor: s.mobile ? 2 : 1, mobile: s.mobile ?? false});
    if (s.mobile) await this.cmd('Emulation.setTouchEmulationEnabled', {enabled: true, maxTouchPoints: 5});
    if (s.network_profile) await this.cmd('Page.addScriptToEvaluateOnNewDocument', {source: `try{localStorage.setItem('ubts-live-network-profile',${JSON.stringify(s.network_profile)})}catch(e){}`});
  }
  cmd(method, params = {}) { return send(method, params, this.session); }
  checkAlive() { const lost = lostSessions.get(this.session); if (lost) throw Error(`tab ${this.index} lost: ${lost.reason}`); }
  async ev(expr) {
    const r = await this.cmd('Runtime.evaluate', {expression: expr, returnByValue: true, awaitPromise: true});
    if (r.exceptionDetails) throw Error('eval: ' + (r.exceptionDetails.exception?.description ?? r.exceptionDetails.text).slice(0, 300));
    return r.result?.value;
  }
  async drain(final = false) {
    const d = await this.ev(`window.__slowlink ? window.__slowlink.drain(${final}) : null`);
    const pm = await this.cmd('Performance.getMetrics');
    const m = Object.fromEntries(pm.metrics.map(x => [x.name, x.value]));
    this.samples.push(d); this.metrics.push({t: Date.now(), ...m});
    return d;
  }
  async shot(name) {
    writeFileSync(join(cfg.out, name), Buffer.from((await this.cmd('Page.captureScreenshot', {format: 'png'})).data, 'base64'));
  }
  async key(type, letter) {
    const code = letter.toUpperCase().charCodeAt(0);
    await this.cmd('Input.dispatchKeyEvent', {type, key: letter, code: 'Key' + letter.toUpperCase(), windowsVirtualKeyCode: code, nativeVirtualKeyCode: code,
      ...(type === 'keyDown' ? {text: letter} : {})});
  }
  async hide() {
    // A second tab in the same window becomes the active one; the dashboard tab turns hidden.
    this.blankTarget = (await send('Target.createTarget', {url: 'about:blank', browserContextId: this.ctx})).targetId;
    await send('Target.activateTarget', {targetId: this.blankTarget});
    await sleep(500);
    return this.ev('document.visibilityState');
  }

  // Open the route and wait until the page can be driven, has fired its load event and no page file is still on
  // its way. A page file that never reports completion does not hold the run: after `load_settle_limit_s` from
  // "usable and loaded" the load part ends anyway and the files still open are listed. The request list comes from
  // the browser's own network events, which are switched on for this part only.
  async load(name) {
    const limit = (P.load_timeout_s ?? 600) * 1000, quiet = (P.load_quiet_s ?? 1.5) * 1000, settleLimit = (P.load_settle_limit_s ?? 120) * 1000;
    const requests = new Map();
    const pathOf = url => { try { return new URL(url).pathname; } catch { return url.slice(0, 80); } };
    const onEvent = m => {
      if (m.sessionId !== this.session || !m.method?.startsWith('Network.')) return;
      const p = m.params, row = requests.get(p.requestId);
      if (m.method === 'Network.requestWillBeSent') {
        // blob: and data: addresses (the camera pictures are shown through blob: addresses) are not network traffic.
        if (!/^https?:/.test(p.request.url)) return;
        requests.set(p.requestId, {path: pathOf(p.request.url), method: p.request.method, type: p.type ?? null, start: Math.round(p.wallTime * 1000), mono: p.timestamp,
          status: null, wire_status: null, from_cache: false, transfer: 0, decoded: 0, end: null, failed: null, priority: p.request.initialPriority ?? null,
          response_seen: false, headers_at: null});
      } else if (!row) {
        return;
      } else if (m.method === 'Network.responseReceived') {
        row.response_seen = true;
        row.status = p.response.status; row.from_cache = row.from_cache || !!p.response.fromDiskCache || !!p.response.fromPrefetchCache;
        row.protocol = p.response.protocol ?? null; row.reused_connection = p.response.connectionReused ?? null;
        const t = p.response.timing;
        if (t) { row.send_start_ms = Math.round(t.sendStart * 10) / 10; row.queued_ms = Math.round((t.requestTime - row.mono) * 10000) / 10; }
      } else if (m.method === 'Network.responseReceivedExtraInfo') {
        row.wire_status = p.statusCode; row.headers_at ??= Date.now();
      } else if (m.method === 'Network.requestServedFromCache') {
        row.from_cache = true;
      } else if (m.method === 'Network.dataReceived') {
        row.decoded += p.dataLength;
      } else if (m.method === 'Network.loadingFinished') {
        row.transfer = p.encodedDataLength; row.end = Math.round(row.start + (p.timestamp - row.mono) * 1000);
      } else if (m.method === 'Network.loadingFailed') {
        row.failed = p.errorText || (p.canceled ? 'canceled' : 'failed'); row.end = Math.round(row.start + (p.timestamp - row.mono) * 1000);
      }
    };
    listeners.push(onEvent);
    await this.cmd('Network.enable');
    const navAt = Date.now();
    mark(`c${this.index}_${name}_navigate`);
    const nav = await this.cmd('Page.navigate', {url: `${this.spec.origin}${ROUTE}`});
    let state = null, doneAt = null, readyAt = null, calmSince = null, stillOpen = null;
    while (Date.now() - navAt < limit) {
      await sleep(100);
      this.checkAlive();
      try {
        state = await this.ev(`(()=>{const u=window.__slowlink;return u&&location.pathname===${JSON.stringify(ROUTE)}?{ready:document.readyState,m:u.milestones}:null})()`);
      } catch { state = null; }
      const ready = state && state.ready === 'complete' && state.m.drive_ready && state.m.first_telemetry;
      if (ready && !readyAt) readyAt = Date.now();
      // A web worker's own script: its headers are reported here but its completion only to the worker, so it would hold
      // the wait for ever. It stops holding it 2 s after its headers and is flagged. (Large files on a slow link also get
      // their page-side response event late, at the end, so "no response yet" alone does not mean "elsewhere".)
      for (const r of requests.values())
        if (r.end === null && !r.response_seen && r.headers_at && Date.now() - r.headers_at > 2000 && r.type === 'Script' && /worker\.m?js$/.test(r.path)) r.handed_off = true;
      const pending = [...requests.values()].filter(r => r.start >= navAt - 50 && r.end === null && !r.handed_off && !r.path.startsWith('/api/'));
      if (ready && pending.length === 0) { calmSince ??= Date.now(); if (Date.now() - calmSince >= quiet) { doneAt = calmSince; break; } }
      else calmSince = null;
      if (readyAt && Date.now() - readyAt >= settleLimit) {
        doneAt = Date.now();
        stillOpen = pending.map(r => ({path: r.path, type: r.type, method: r.method, start_ms: r.start - navAt, received_bytes: r.decoded, status: r.status}));
        break;
      }
    }
    mark(`c${this.index}_${name}_done`);
    await this.cmd('Network.disable').catch(() => {});
    listeners.splice(listeners.indexOf(onEvent), 1);
    let taken = null;
    try { taken = await this.ev('window.__slowlink ? window.__slowlink.takeLoad() : null'); } catch (e) { report.notes.push(`takeLoad ${name}: ${e.message}`); }
    const rows = [...requests.values()].filter(r => r.start >= navAt - 50).map(({mono, ...row}) => row);
    this.loads[name] = {nav_at: navAt, ready_at: readyAt, done_at: doneAt, timed_out: !doneAt, settled: Boolean(doneAt) && stillOpen === null,
      still_open: stillOpen, navigate_error: nav.errorText ?? null, last_state: state, requests: rows, ...(taken ?? {})};
    return this.loads[name];
  }
}

// ---------------------------------------------------------------- live phase helpers
async function waitFor(client, expr, timeoutMs = 20000, stepMs = 250) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) { try { const v = await client.ev(expr); if (v) return v; } catch {} await sleep(stepMs); }
  return null;
}
async function startLiveMap(client) {
  // Display mapping only. Wait until the page knows the map epoch, click, check that it reports active.
  for (let attempt = 1; attempt <= 3; attempt++) {
    const ready = await waitFor(client, `(()=>{const b=document.getElementById('liveMapStart');const m=window.__slowlink.maps.liveMapCanvas;return b && !b.disabled && m && m.snapshot && m.snapshot.map_epoch;})()`, 60000);
    if (!ready) return {ok: false, attempt, reason: 'start_button_or_epoch_unavailable'};
    await client.ev(`document.getElementById('liveMapStart').click()`);
    const active = await waitFor(client, `(()=>{const m=window.__slowlink.maps.liveMapCanvas;return m && m.snapshot && m.snapshot.active===true;})()`, 20000, 300);
    if (active) return {ok: true, attempt, state: await client.ev(`document.getElementById('liveMapState')?.textContent`)};
    report.notes.push(`live map start attempt ${attempt}: ` + await client.ev(`document.getElementById('liveMapStatus')?.textContent`));
  }
  return {ok: false, reason: 'not_active_after_3_attempts'};
}

// Manual drive the way an operator does it: activate, hold forward, let go, STOP. Repeats until `until`.
async function driveCycles(client, until) {
  const d = P.drive, log = [];
  const leased = `(()=>{try{return !!state.roverControl.lease}catch(e){return false}})()`;
  await client.cmd('Page.bringToFront').catch(() => {});
  await client.cmd('Emulation.setFocusEmulationEnabled', {enabled: true}).catch(() => {});
  const total = (d.hold_s + d.idle_s + d.after_stop_s) * 1000 + 1500;
  for (let cycle = 0; Date.now() + total < until; cycle++) {
    const row = {cycle, t_arm_click: Date.now()};
    await client.ev(`document.getElementById('roverArmButton').click()`);
    const armed = await waitFor(client, leased, (d.arm_timeout_s ?? 8) * 1000, 40);
    row.armed_at = armed ? Date.now() : null;
    if (!armed) {
      row.arm_status = await client.ev(`document.getElementById('roverCommandStatus')?.textContent`).catch(() => null);
    } else {
      row.t_keydown = Date.now();
      await client.key('keyDown', 'w');
      await sleep(d.hold_s * 1000);
      row.lease_at_hold_end = await client.ev(leased);
      row.status_at_hold_end = await client.ev(`document.getElementById('roverCommandStatus')?.textContent`).catch(() => null);
      row.t_keyup = Date.now();
      await client.key('keyUp', 'w');
      await sleep(d.idle_s * 1000);
      row.lease_before_stop = await client.ev(leased);
    }
    row.t_stop_click = Date.now();
    await client.ev(`document.getElementById('roverStopButton').click()`);
    await sleep(d.after_stop_s * 1000);
    row.status_after_stop = await client.ev(`document.getElementById('roverCommandStatus')?.textContent`).catch(() => null);
    log.push(row);
  }
  return log;
}

async function runLive(clients) {
  const dur = (P.live_s ?? 120) * 1000, every = (P.sample_every_s ?? 5) * 1000;
  await sleep((P.settle_s ?? 5) * 1000);
  if (P.start_live_map) report.live_map_start = await startLiveMap(clients[0]);
  for (const c of clients) await c.drain().catch(e => report.notes.push(`drain c${c.index}: ${e.message}`));
  const t0 = Date.now();
  mark('measure_start');
  for (const c of clients) if (c.spec.hidden_after_s !== undefined && c.spec.hidden_after_s !== null) c.hideAt = t0 + c.spec.hidden_after_s * 1000;
  const driving = P.drive ? driveCycles(clients[0], t0 + dur).catch(e => { report.notes.push('drive: ' + e.message); return null; }) : null;
  let next = t0 + every;
  while (Date.now() < t0 + dur) {
    for (const c of clients) if (c.hideAt && Date.now() >= c.hideAt && !c.hidden) { c.hidden = await c.hide(); mark(`client${c.index}_hidden:${c.hidden}`); }
    await sleep(Math.max(0, Math.min(next, t0 + dur) - Date.now()));
    for (const c of clients) await c.drain().catch(e => report.notes.push(`drain c${c.index}: ${e.message}`));
    for (const c of clients) c.checkAlive();
    next += every;
  }
  mark('measure_end');
  if (driving) report.drive_log = await driving;
  await sleep(1500);  // answers still on their way
  for (const c of clients) {
    await c.drain(true).catch(e => report.notes.push(`final drain c${c.index}: ${e.message}`));
    c.final = await c.ev(`(()=>({route:location.pathname, vis:document.visibilityState, focus:document.hasFocus(),
      live:{lidar:state.lidarLive.connected, camera:state.cameraLive.connected, imu:state.imuLive.connected,
            cameraProfile:state.cameraLive.effectiveProfile, cameraDelivery:state.cameraLive.deliveryState, cameraAck:state.cameraLive.ackLatencyMs},
      networkProfile:(()=>{try{return localStorage.getItem('ubts-live-network-profile')}catch(e){return null}})(),
      mapState:document.getElementById('liveMapState')?.textContent, mapStatus:document.getElementById('liveMapStatus')?.textContent,
      driveStatus:document.getElementById('roverCommandStatus')?.textContent,
      gpsAge:document.getElementById('roverGpsAge')?.textContent, gpsMapStatus:document.getElementById('roverGpsMapStatus')?.textContent,
      cameraCaption:document.getElementById('roverCameraCaption')?.textContent}))()`).catch(e => ({error: e.message}));
    if (cfg.screenshot && !c.hidden) { await c.shot(`live_c${c.index}.png`).catch(e => report.notes.push('screenshot: ' + e.message)); }
  }
}

// ---------------------------------------------------------------- main
const clients = [];
try {
  for (const [i, spec] of (cfg.clients ?? []).entries()) { const c = new Client(i, spec); await c.open(); clients.push(c); }
  if (P.cold_load !== false) for (const c of clients) await c.load('cold');
  else for (const c of clients) { mark(`c${c.index}_plain_navigate`); await c.cmd('Page.navigate', {url: `${c.spec.origin}${ROUTE}`}); }
  if (P.warm_load) for (const c of clients) await c.load('warm');
  if ((P.live_s ?? 0) > 0) await runLive(clients);
  report.clock_end = await clockOffset();
} catch (e) { report.error = String(e.stack ?? e.message).slice(0, 1200); }
finally {
  report.lost_tabs = clients.filter(c => lostSessions.has(c.session)).map(c => ({index: c.index, ...lostSessions.get(c.session)}));
  for (const c of clients)
    report.clients.push({index: c.index, spec: c.spec, hidden: c.hidden ?? null, final: c.final ?? null, loads: c.loads, samples: c.samples, metrics: c.metrics, console: c.console});
  try { ws.close(); } catch {}
  chrome.kill(); await sleep(700);
  try { rmSync(profile, {recursive: true, force: true}); } catch {}
}
writeFileSync(join(cfg.out, 'RAW.json'), JSON.stringify(report) + '\n');
console.log(JSON.stringify({label: cfg.label, error: report.error ?? null, clients: report.clients.length, notes: report.notes.length}));
