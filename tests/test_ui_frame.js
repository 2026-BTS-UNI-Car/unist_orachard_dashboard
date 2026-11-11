// Application frame: the status bar with the always-visible STOP, the drive
// state indicator and the recording indicator. Fake buttons and a fake
// transport only: never connects to hardware.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const staticDir = path.join(__dirname, '../static');
const read = name => fs.readFileSync(path.join(staticDir, name), 'utf8').replace(/\r\n/g, '\n');
const source = require('./helpers/app_source').readAppSource().replace(/\r\n/g, '\n'), html = read('index.html'), frameCss = read('css/frame.css');
const between = (begin, end) => { const a = source.indexOf(begin), b = source.indexOf(end, a); assert.ok(a >= 0 && b > a, begin); return source.slice(a, b); };
const flush = () => new Promise(resolve => setImmediate(resolve));
// Requests as plain text: objects made inside the vm context have another Object prototype.
const sent = calls => calls.map(call => `${call.p} ${JSON.stringify(call.payload)}`);
const STOP_REQUEST = '/api/rover/stop {"reason":"operator_stop"}';

// ---- Markup: one status bar, outside every page, so it is on all nine routes ----
const ROUTES = ['/', '/connection', '/system', '/network', '/docker', '/fan', '/research', '/rover-control', '/docs'];
{
  const bar = html.match(/<header class="statusbar" id="statusBar">[\s\S]*?<\/header>/);
  assert.ok(bar, 'status bar');
  const firstPage = html.indexOf('data-page=');
  assert.ok(html.indexOf(bar[0]) < html.indexOf('<main'), 'the status bar is outside <main>');
  assert.ok(html.indexOf(bar[0]) < firstPage, 'the status bar is not inside a page');
  // Every route is a page section of this one document (the server sends index.html for all of them).
  assert.deepEqual([...html.matchAll(/<section class="page[^"]*" data-page="([^"]+)">/g)].map(match => match[1]).sort(), [...ROUTES].sort());
  assert.match(source, /^const ROUTES = \["\/", "\/connection", "\/system", "\/network", "\/docker", "\/fan", "\/research", "\/rover-control", "\/docs"\];$/m);
  // The status bar holds the state items and, at the right end, STOP.
  const order = ['id="mobileMenuButton"', 'id="connectionState"', 'id="frameDriveState"', 'id="frameRecordState"', 'id="frameClock"', 'id="frameStopButton"'];
  const positions = order.map(marker => bar[0].indexOf(marker));
  assert.ok(positions.every(position => position >= 0), 'status bar items');
  assert.deepEqual([...positions].sort((a, b) => a - b), positions, 'STOP is the last item of the status bar');
  assert.match(bar[0], /<button class="stop-button" data-ui-check="stop" id="frameStopButton" type="button">STOP<\/button>/);
  assert.equal([...html.matchAll(/id="frameStopButton"/g)].length, 1);
  // The drive panel STOP and the pad STOP are still there.
  assert.match(html, /<button class="action-button danger" id="roverStopButton" type="button">STOP<\/button>/);
  assert.match(html, /<button data-rover-drive="stop" type="button">STOP<\/button>/);
  // Navigation: nine routes, drive first, Korean labels.
  const nav = html.match(/<nav class="nav-list">([\s\S]*?)<\/nav>/)[1];
  assert.deepEqual([...nav.matchAll(/<a data-route="([^"]+)" href="([^"]+)">([^<]+)<\/a>/g)].map(match => [match[1], match[3]]), [
    ['/rover-control', '주행'], ['/connection', '연결'], ['/', '개요'], ['/system', '시스템'], ['/network', '네트워크'],
    ['/docker', '컨테이너'], ['/fan', '팬'], ['/docs', '문서'], ['/research', '연구']]);
  assert.ok([...nav.matchAll(/data-route="([^"]+)" href="([^"]+)"/g)].every(match => match[1] === match[2]));
}

// ---- Style: the bar is fixed; STOP is at least 44px high and 88px wide ----
{
  const rule = selector => { const match = frameCss.match(new RegExp(`(?:^|\\n)${selector.replace(/[.]/g, '\\.')} \\{([^}]*)\\}`)); assert.ok(match, selector); return match[1]; };
  const bar = rule('.statusbar');
  assert.match(bar, /position: fixed;/); assert.match(bar, /top: var\(--banner-height\);/); assert.match(bar, /height: var\(--statusbar-height\);/);
  const stop = rule('.stop-button');
  assert.match(stop, /height: 44px;/); assert.match(stop, /min-width: 96px;/); assert.match(stop, /background: var\(--color-stop\);/);
  const phone = frameCss.slice(frameCss.indexOf('@media (max-width: 559px)'));
  assert.match(phone, /\.stop-button \{\s*min-width: 88px;\s*\}/, 'STOP keeps 88px on phones');
  assert.doesNotMatch(phone, /\.stop-button \{[^}]*(display: none|height)/);
  assert.match(read('css/tokens.css'), /--statusbar-height: 56px;/);
  // Phones show the states in two rows of two; the narrowest phones in one column, where only the clock gives way.
  assert.match(phone, /\.statusbar-items \{\s*display: grid;\s*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/);
  const narrowest = frameCss.slice(frameCss.indexOf('@media (max-width: 379px)'));
  assert.match(narrowest, /\.statusbar-items \{\s*grid-template-columns: minmax\(0, 1fr\);/);
  assert.deepEqual([...narrowest.matchAll(/([.#][\w-]+) \{\s*display: none;/g)].map(match => match[1]), ['.statusbar-clock']);
  // No rule anywhere hides the drive state, the recording state or STOP.
  assert.doesNotMatch(frameCss + read('styles.css'), /#frame(DriveState|RecordState|StopButton)[^{]*\{[^}]*display: none/);
  // The drawer and its backdrop start below the status bar, so STOP is never covered by the menu.
  const drawer = frameCss.slice(frameCss.indexOf('@media (max-width: 1023px)'), frameCss.indexOf('@media (max-width: 639px)'));
  assert.equal([...drawer.matchAll(/top: calc\(var\(--statusbar-height\) \+ var\(--banner-height\)\);/g)].length, 2);
}

// ---- STOP in the status bar: the same function, reason and request as the drive panel STOP ----
const stopCode = between('function hasActiveRoverInput() {', 'function roverCommandMessage(');
const frameCode = between('function initFrameStop() {', 'function renderFrameClock(');
const panelLine = '$("roverStopButton")?.addEventListener("click", () => stopRoverNow("operator_stop"));';
assert.ok(source.includes(`  ${panelLine}\n  initFrameStop();\n`), 'the status bar STOP is wired right after the drive panel STOP');
assert.match(frameCode, /function initFrameStop\(\) \{\n  \$\("frameStopButton"\)\?\.addEventListener\("click", \(\) => stopRoverNow\("operator_stop"\)\);\n\}/);

function setup(route = '/rover-control') {
  const c = {activeKeys: new Set(), speedKeys: new Set(), activeButton: '', speedPct: 30, lease: 'lease', ticket: 'one', seq: 0, epoch: 0};
  const calls = [], status = [];
  const element = () => ({className: '', textContent: '', handlers: {}, addEventListener(type, handler) { this.handlers[type] = handler; }});
  const nodes = {frameStopButton: element(), roverStopButton: element(), frameDriveState: element()};
  const context = {
    state: {roverControl: c}, document: {hidden: false}, currentRoute: () => route, SIMULATION: false,
    $: id => nodes[id] || null, setInterval: () => 1, clearInterval: () => {},
    renderRoverButtons() { context.renderFrameDriveState(); }, renderRoverMotionReadout() {},
    setRoverCommandStatus: (kind, message) => status.push([kind, message]),
    postJson: async (p, payload) => { calls.push({p, payload}); return {ok: true}; },
  };
  vm.createContext(context); vm.runInContext(stopCode + frameCode, context);
  vm.runInContext(panelLine, context); context.initFrameStop();
  return {context, c, calls, status, nodes};
}

(async () => {
  // One click on either STOP: the lease is cleared at once and one stop request goes out with the same body.
  const outcome = {};
  for (const id of ['roverStopButton', 'frameStopButton']) {
    const {c, calls, status, nodes} = setup();
    c.activeKeys.add('w');
    assert.deepEqual(Object.keys(nodes[id].handlers), ['click']);
    nodes[id].handlers.click();
    assert.equal(c.lease, null, `${id}: lease cleared before the network round trip`);
    assert.equal(calls.length, 1, `${id}: the request is posted synchronously`);
    await flush();
    assert.deepEqual(sent(calls), [STOP_REQUEST], id);
    assert.equal(c.activeKeys.size, 0); assert.equal(c.disarming, false);
    outcome[id] = JSON.stringify([calls, status, c.epoch]);
  }
  assert.equal(outcome.frameStopButton, outcome.roverStopButton, 'both STOP buttons take the same path');

  // The status bar STOP does not depend on the route: stopRoverNow() does not look at it.
  for (const route of ROUTES) {
    const {c, calls, nodes} = setup(route);
    nodes.frameStopButton.handlers.click(); await flush();
    assert.deepEqual(sent(calls), [STOP_REQUEST], route);
    assert.equal(c.lease, null, route);
  }
  // It also stops with no lease (another tab or device may be driving) and while a drive request is in flight.
  {
    const {c, calls, nodes} = setup('/system');
    c.lease = null; c.inFlight = true;
    nodes.frameStopButton.handlers.click(); await flush();
    assert.deepEqual(sent(calls), [STOP_REQUEST]);
  }

  // ---- Drive state indicator ----
  {
    const {context, c, nodes} = setup();
    const shown = () => [nodes.frameDriveState.className, nodes.frameDriveState.textContent];
    context.renderFrameDriveState();
    assert.deepEqual(shown(), ['status-chip warn', '조종 활성'], 'armed');
    c.lease = null; context.renderFrameDriveState();
    assert.deepEqual(shown(), ['status-chip', '조종 비활성'], 'disarmed');
    c.arming = true; context.renderFrameDriveState();
    assert.deepEqual(shown(), ['status-chip', '활성화 중']);
    c.arming = false; c.lease = 'lease'; context.renderFrameDriveState();
    assert.deepEqual(shown(), ['status-chip warn', '조종 활성']);
    // STOP: the indicator leaves "armed" in the same turn as the click.
    let answer;
    context.postJson = () => new Promise(resolve => { answer = resolve; });
    nodes.frameStopButton.handlers.click();
    assert.deepEqual(shown(), ['status-chip', '정지 요청 중']);
    answer({ok: true}); await flush();
    assert.deepEqual(shown(), ['status-chip', '조종 비활성']);
    // A collapsed drive panel changes nothing here: the indicator reads the control state only.
    assert.doesNotMatch(frameCode, /panelCollapsed|drive-collapsed/);
  }
  // The real status function marks a STOP that got no answer; the next good status clears it.
  {
    const c = {lease: null, disarming: true};
    const chip = {className: '', textContent: ''}, notice = {className: '', textContent: ''};
    const context = {state: {roverControl: c}, $: id => ({frameDriveState: chip, roverCommandStatus: notice})[id] || null};
    vm.createContext(context);
    vm.runInContext(between('function setRoverCommandStatus(', 'function renderRoverButtons(') + between('function renderFrameDriveState() {', 'function renderFrameClock('), context);
    context.setRoverCommandStatus('bad', '응답 없음');
    assert.equal(c.stopFailed, true); assert.equal(notice.className, 'test-result bad'); assert.equal(notice.textContent, '응답 없음');
    c.disarming = false; context.renderFrameDriveState();
    assert.deepEqual([chip.className, chip.textContent], ['status-chip bad', '정지 미확인']);
    context.setRoverCommandStatus('bad', '먼저 조종을 활성화하세요.');
    assert.equal(c.stopFailed, false, 'a refused pad press is not a failed STOP');
    assert.deepEqual([chip.className, chip.textContent], ['status-chip', '조종 비활성']);
    c.disarming = true; context.setRoverCommandStatus('bad', '응답 없음'); c.disarming = false;
    c.lease = 'lease'; context.setRoverCommandStatus('ok', '조종 활성화');
    assert.equal(c.stopFailed, false); assert.deepEqual([chip.className, chip.textContent], ['status-chip warn', '조종 활성']);
  }
  // renderRoverButtons() is where the arm button changes; it refreshes the indicator as well.
  assert.match(between('function renderRoverButtons() {', 'function initFrameStop() {'), /\n  renderFrameDriveState\(\);\n\}\n/);

  // ---- Recording indicator ----
  {
    const chip = {className: '', textContent: ''};
    let route = '/rover-control';
    const context = {state: {telemetry: null, telemetryFailures: 0}, currentRoute: () => route, $: id => id === 'frameRecordState' ? chip : null};
    vm.createContext(context);
    vm.runInContext(between('function renderFrameRecordState(', 'function recordingDurationSeconds('), context);
    const shown = (known, active) => { context.renderFrameRecordState(known, active); return [chip.className, chip.textContent]; };
    // Drive page: what the recorder panel decided (ordered reads, 5 s freshness gate).
    assert.deepEqual(shown(true, true), ['status-chip recording', '기록 중']);
    assert.deepEqual(shown(true, false), ['status-chip', '기록 대기']);
    assert.deepEqual(shown(false, false), ['status-chip', '기록 미확인']);
    context.state.telemetry = {services: {data_recorder: {ok: true, active: true}}};
    assert.deepEqual(shown(false, false), ['status-chip', '기록 미확인'], 'telemetry does not replace the recorder panel state on the drive page');
    // Other routes: the recorder is not polled there, the telemetry row is used.
    route = '/system';
    assert.deepEqual(shown(false, false), ['status-chip recording', '기록 중']);
    context.state.telemetry.services.data_recorder.active = false;
    assert.deepEqual(shown(true, true), ['status-chip', '기록 대기']);
    context.state.telemetryFailures = 3;
    assert.deepEqual(shown(true, true), ['status-chip', '기록 미확인'], 'nothing is claimed once telemetry has stopped');
    context.state.telemetryFailures = 0; context.state.telemetry = {services: {data_recorder: {ok: false, state: 'not_connected'}}};
    assert.deepEqual(shown(true, true), ['status-chip', '기록 미확인']);
    context.state.telemetry = null;
    assert.deepEqual(shown(true, true), ['status-chip', '기록 미확인']);
    assert.match(between('function renderRecordingStatus(', 'function renderFrameRecordState('), /\n  renderFrameRecordState\(known, active\);\n\}\n/);
  }

  // ---- Clock: its own one-second timer, placed before the recorder interval (the last "setInterval(() => {") ----
  assert.match(source, /\nrenderFrameClock\(\);\nsetInterval\(renderFrameClock, 1000\);\n/);
  assert.ok(source.indexOf('setInterval(renderFrameClock, 1000);') < source.lastIndexOf('setInterval(() => {'));
  {
    const shown = {};
    const context = {Date: class { getHours() { return 7; } getMinutes() { return 5; } getSeconds() { return 9; } }, String, text: (id, value) => { shown[id] = value; }};
    vm.createContext(context); vm.runInContext(between('function renderFrameClock() {', 'function renderRoverMotionReadout('), context);
    context.renderFrameClock();
    assert.deepEqual(shown, {frameClock: '07:05:09'});
  }

  // ---- Theme: light unless the reader chose dark; the switch is pressed when dark ----
  assert.match(html, /document\.documentElement\.dataset\.theme = localStorage\.getItem\("ubts-theme"\) \|\| "light";/);
  assert.match(between('function initTheme() {', 'function applyTheme('), /const theme = stored \|\| "light";/);
  {
    const toggle = {attributes: {}, setAttribute(name, value) { this.attributes[name] = value; }};
    const stored = {};
    const context = {document: {documentElement: {dataset: {}}}, localStorage: {setItem: (key, value) => { stored[key] = value; }},
      $: id => id === 'themeToggle' ? toggle : null, requestChartAnimation() {}, String};
    vm.createContext(context); vm.runInContext(between('function applyTheme(', 'function initCpuMode('), context);
    context.applyTheme('dark');
    assert.deepEqual([context.document.documentElement.dataset.theme, stored['ubts-theme'], toggle.attributes['aria-pressed']], ['dark', 'dark', 'true']);
    context.applyTheme('light');
    assert.deepEqual([context.document.documentElement.dataset.theme, stored['ubts-theme'], toggle.attributes['aria-pressed']], ['light', 'light', 'false']);
  }
  // ---- Page header: one compact row per page, titled like its menu item; no banner ----
  {
    const labels = Object.fromEntries([...html.matchAll(/<a data-route="([^"]+)" href="[^"]+">([^<]+)<\/a>/g)].map(match => [match[1], match[2]]));
    const pages = html.split(/<section class="page[^"]*" data-page="/).slice(1);
    assert.equal(pages.length, 9);
    for (const page of pages) {
      const route = page.slice(0, page.indexOf('"'));
      const heads = [...page.matchAll(/<header class="page-head">([\s\S]*?)<\/header>/g)];
      assert.equal(heads.length, 1, `${route}: one page header`);
      assert.ok(page.indexOf('<header class="page-head">') < 40, `${route}: the header is the first element of the page`);
      assert.deepEqual([...heads[0][1].matchAll(/<h1>([^<]*)<\/h1>/g)].map(match => match[1]), [labels[route]], `${route}: title is the menu label`);
      assert.doesNotMatch(heads[0][1], /eyebrow|hero-copy/, `${route}: no banner parts`);
      assert.ok((heads[0][1].match(/<p /g) || []).length <= 1, `${route}: at most one note line`);
    }
    assert.doesNotMatch(html, /hero-copy|class="kpi"|kpi-grid|network-focus-grid/, 'banner and card markup is gone');
    assert.match(html, /<span class="status-chip" id="roverModeLabel">실차<\/span>/);
    assert.match(html, /<div class="page-head-actions"><label class="field-inline">전송 품질 <select aria-label="전송 품질 선택" data-network-profile="">/);
  }
  // ---- Status strip: name, value, detail; a full row of cells ----
  {
    const strips = [...html.matchAll(/<section class="status-strip( cols-2)?">([\s\S]*?)<\/section>\n/g),
      ...html.matchAll(/<div class="status-strip( cols-1)">(.*)<\/div>\n/g)];
    assert.equal(strips.length, 6, 'overview, connection, network, docker, drive, and the one cell of the fan page');
    for (const strip of strips) {
      const items = [...strip[2].matchAll(/<div class="status-strip-item"><span[^>]*>[^<]+<\/span><strong id="[^"]+">--<\/strong><small[^>]*>[^<]*<\/small>(?:<\/div>)?/g)];
      const expected = strip[1] === ' cols-2' ? 2 : strip[1] === ' cols-1' ? 1 : 4;
      assert.equal(items.length, expected, strip[0].slice(0, 60));
      assert.equal((strip[2].match(/status-strip-item/g) || []).length, expected, 'every cell has name, value and detail');
    }
    assert.match(frameCss, /\.status-strip \{[^}]*grid-template-columns: repeat\(4, minmax\(0, 1fr\)\);/);
    assert.match(frameCss.slice(frameCss.indexOf('@media (max-width: 639px)')), /\.status-strip,\n  \.status-strip\.cols-2 \{\n    grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/, 'two by two on phones');
    // Values that hold a device path are shortened in the middle; the title keeps the whole value.
    assert.deepEqual([...html.matchAll(/data-shorten="(\d+)" id="([^"]+)"/g)].map(match => `${match[2]}:${match[1]}`),
      ['connectionLidarDevice:24', 'connectionCameraDevice:24', 'roverControlBase:24']);
    const element = shorten => ({dataset: shorten ? {shorten} : {}, textContent: '', attributes: {},
      getAttribute(name) { return this.attributes[name] ?? null; }, setAttribute(name, value) { this.attributes[name] = value; }, removeAttribute(name) { delete this.attributes[name]; }});
    const nodes = {path: element('24'), plain: element('')};
    const context = {$: id => nodes[id] || null, String, Number, Math};
    vm.createContext(context); vm.runInContext(between('function text(', 'function fmtPercent('), context);
    const long = '/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0';
    context.text('path', long);
    assert.equal(nodes.path.textContent, '/dev/serial/…-if00-port0'); assert.equal(nodes.path.textContent.length, 24);
    assert.equal(nodes.path.attributes.title, long, 'the full value stays available');
    context.text('path', '/dev/ubts-motor');
    assert.equal(nodes.path.textContent, '/dev/ubts-motor'); assert.equal('title' in nodes.path.attributes, false, 'no title when nothing was cut');
    context.text('plain', long); assert.equal(nodes.plain.textContent, long); assert.equal('title' in nodes.plain.attributes, false);
    context.text('missing', 'x');
  }
  console.log('PASS: status bar on every route, STOP in the bar takes the drive panel STOP path, drive state and recording indicators, clock, theme default, page headers, status strips');
})().catch(error => { console.error(error); process.exitCode = 1; });
