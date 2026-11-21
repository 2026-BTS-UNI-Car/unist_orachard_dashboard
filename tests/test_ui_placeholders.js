// Small correctness guards: no made-up values before the server answers, no CSS
// variable or element id that does not exist, no wait cursor on disabled buttons.
// Reads the shipped files and runs the real camera-settings functions in a fake page.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const staticDir = path.join(__dirname, '../static');
const read = name => fs.readFileSync(path.join(staticDir, name), 'utf8').replace(/\r\n/g, '\n');
const source = require('./helpers/app_source').readAppSource().replace(/\r\n/g, '\n'), html = read('index.html');
const between = (begin, end) => { const a = source.indexOf(begin), b = source.indexOf(end, a); assert.ok(a >= 0 && b > a, begin); return source.slice(a, b); };

(async () => {
  // The page as delivered shows "--" where a value has to come from the server.
  for (const id of ['cameraResolutionValue', 'cameraFpsValue', 'roverCameraResolutionValue', 'roverCameraFpsValue']) {
    const match = html.match(new RegExp(`id="${id}"[^>]*>([^<]*)<`));
    assert.ok(match, id);
    assert.equal(match[1], '--', `${id} must not start with an invented value`);
  }

  // Camera labels: "--" until the server reports or accepts the capture settings.
  {
    const labels = {}, selects = {resolution: {value: ''}, fps: {value: ''}};
    const state = {cameraModes: [{resolution: '1920x1080', fps: [30]}, {resolution: '1280x720', fps: [30, 60]}],
      cameraLive: {resolution: '1280x720', fps: 60, settingsKnown: false, settingsInFlight: false, settingsRevision: 0}};
    let clock = 10000, reply = null;
    const context = {state, CAMERA_RESOLUTION_KEY: 'r', CAMERA_FPS_KEY: 'f', AbortController, Number, Math, Object, String, JSON, Boolean,
      Date: {now: () => clock}, setTimeout, clearTimeout, localStorage: {setItem() {}},
      text: (id, value) => { labels[id] = value; }, syncSelectOptions() {},
      document: {activeElement: null, querySelectorAll: selector => selector.endsWith('-select]') ?
        [selector.includes('resolution') ? selects.resolution : selects.fps] : []},
      normalizeCameraModes: modes => modes, scheduleCameraReconnectForSettings() {}, setCameraResult() {},
      fetch: async () => reply};
    vm.createContext(context);
    vm.runInContext(between('function applyCameraModeCatalog(', 'function syncSelectOptions('), context);
    const shown = () => [labels.cameraResolutionValue, labels.cameraFpsValue];
    context.updateCameraControls();
    assert.deepEqual(shown(), ['--', '--'], 'the stored or default selection is only a request');
    assert.equal(selects.resolution.value, '1280x720', 'the selectors still hold the request');
    // Telemetry without usable settings (camera driver not running, unknown mode) changes nothing.
    context.applyCameraStatus({}, {}); context.applyCameraStatus({settings: {resolution: '999x999', fps: 30}});
    assert.deepEqual(shown(), ['--', '--']); assert.equal(state.cameraLive.settingsKnown, false);
    // A rejected change stays unknown.
    reply = {ok: false, status: 409, headers: {get: () => 'application/json'}, json: async () => ({ok: false, error: 'camera_resolution_locked_while_recording'})};
    await context.setCameraSettings({resolution: '1920x1080'});
    assert.deepEqual(shown(), ['--', '--']); assert.equal(state.cameraLive.resolution, '1280x720');
    // The first server report shows the values, even when they equal the request.
    clock += 5000; state.telemetryCameraSettingsRevision = state.cameraLive.settingsRevision;
    context.applyCameraStatus({settings: {resolution: '1280x720', fps: 60}});
    assert.deepEqual(shown(), ['1280x720', '60 fps']); assert.equal(state.cameraLive.settingsKnown, true);
    // An accepted change is a fact as well.
    state.cameraLive.settingsKnown = false; context.updateCameraControls(); assert.deepEqual(shown(), ['--', '--']);
    reply = {ok: true, status: 200, headers: {get: () => 'application/json'}, json: async () => ({ok: true})};
    await context.setCameraSettings({resolution: '1920x1080'});
    assert.deepEqual(shown(), ['1920x1080', '30 fps']);
  }

  // Every CSS custom property that is used is defined in a style sheet or set from script.
  {
    // The page style sheets and the design system sheets in static/css (tokens, components, frame).
    const cssFiles = [...fs.readdirSync(staticDir).filter(name => name.endsWith('.css')),
      ...fs.readdirSync(path.join(staticDir, 'css')).filter(name => name.endsWith('.css')).map(name => `css/${name}`)];
    const css = cssFiles.map(read).join('\n');
    const scripts = fs.readdirSync(staticDir).filter(name => /\.(js|mjs)$/.test(name)).map(read).join('\n');
    const defined = new Set([...css.matchAll(/(--[a-z0-9-]+)\s*:/g)].map(match => match[1]));
    for (const match of scripts.matchAll(/setProperty\(\s*['"](--[a-z0-9-]+)['"]/g)) defined.add(match[1]);
    const used = new Set([...(css + html).matchAll(/var\(\s*(--[a-z0-9-]+)/g)].map(match => match[1]));
    assert.ok(used.size > 20, 'custom properties found');
    assert.deepEqual([...used].filter(name => !defined.has(name)), [], 'undefined CSS custom property');
    const disabled = css.match(/\.action-button:disabled\s*\{[^}]*\}/);
    assert.ok(disabled); assert.doesNotMatch(disabled[0], /cursor:\s*wait/, 'a disabled button is not busy');
  }

  // Every element id that app.js looks up exists in the page. roverRecorderDetail is
  // created by renderStatusRows from the row list of the rover page.
  {
    const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]));
    const created = new Set(['roverRecorderDetail']);
    const lookups = new Set([...source.matchAll(/(?:\$|text|getElementById|renderReadout|renderStatusRows|syncItems)\(\s*["']([A-Za-z][\w-]*)["']/g)].map(match => match[1]));
    assert.ok(lookups.size > 150, 'id lookups found');
    assert.deepEqual([...lookups].filter(id => !ids.has(id) && !created.has(id)).sort(), [], 'app.js refers to an element that is not in index.html');
  }

  // S33: the container log box is refreshed every 2.5 s; unchanged text is not rewritten
  // (a rewrite replaces the text node and drops the reader's selection).
  {
    let writes = 0, content = '로그 대기 중';
    const logs = {get textContent() { return content; }, set textContent(value) { content = String(value); writes += 1; }};
    const context = {$: id => id === 'containerLogs' ? logs : id === 'containerDetail' ? {} : null, text() {},
      renderContainerActions() {}, containerDetailCell: () => '', containerStateLabel: () => '', formatShortDate: () => '',
      fmtPercent: () => '', fmtBytes: () => '', restartPolicyLabel: () => '', renderContainerDetailError() { throw new Error('unexpected'); }};
    vm.createContext(context);
    vm.runInContext(between('function renderContainerDetail(', 'function renderContainerActions('), context);
    const detail = {ok: true, name: 'c', stats: {}, state: {}};
    context.renderContainerDetail(detail, {ok: true, lines: ['a', 'b']});
    context.renderContainerDetail(detail, {ok: true, lines: ['a', 'b']});
    assert.equal(content, 'a\nb'); assert.equal(writes, 1, 'same log text is written once');
    context.renderContainerDetail(detail, {ok: true, lines: ['a', 'b', 'c']});
    assert.equal(content, 'a\nb\nc'); assert.equal(writes, 2);
    context.renderContainerDetail(detail, {ok: false, error: 'gone'});
    assert.match(content, /로그를 읽지 못했습니다. gone/);
  }
  console.log('PASS: placeholders until the server answers, defined CSS variables, existing element ids, log box rewrite');
})().catch(error => { console.error(error); process.exitCode = 1; });
