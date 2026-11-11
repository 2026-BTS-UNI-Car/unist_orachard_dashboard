// UI bug fixes (group A): S37 LiDAR ACK, S43 storage, S42 IMU canvas, S35 chart window,
// S27 camera rollback + S41 non-JSON replies, S07 list refresh keeps playback, B6 GPS rate.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource().replace(/\r\n/g, '\n');
const between = (begin, end) => { const a = source.indexOf(begin), b = source.indexOf(end, a); assert.ok(a >= 0 && b > a, begin); return source.slice(a, b); };

(async () => {
  // S37: a render exception still sends exactly one scan_ack.
  {
    const acks = [], logs = [];
    const state = {lidarMap: {range: 10, displayRange: 10, auto: false}, lidarPoints: {value: 900},
      lidarLive: {socket: {readyState: 1, send: v => acks.push(JSON.parse(v))}, messageCount: 0}, lidarScan: null};
    const context = {state, WebSocket: {OPEN: 1}, debugLog: (...a) => logs.push(a), renderLidarLiveState() {}, renderLiveSensorLinks() {},
      setScanResult() {}, maybeAutoSetLidarRange() {}, cleanLidarError: () => '', angleCoverage: () => 360, fmtNumber: String,
      renderLidarScan() { throw new Error('canvas lost'); }, routeSupportsLidarLive: () => true, Date, Math, Number, JSON, Array, Object};
    vm.createContext(context);
    vm.runInContext(between('function handleLidarLiveMessage(', 'function maybeAutoSetLidarRange('), context);
    vm.runInContext(between('function normalizeLidarExportPoint(', 'function normalizeLidarResult('), context);
    vm.runInContext(between('function normalizeLidarResult(', 'function renderLidarScan('), context);
    context.handleLidarLiveMessage(JSON.stringify({type: 'scan', sequence: 11, point_encoding: 'polar-v1', points: [[0, 1, 0], [90, 2, 0]]}));
    assert.deepEqual(acks, [{type: 'scan_ack', sequence: 11}]);
    assert.equal(state.lidarLive.renderErrors, 1);
  }
  // S43: the overview toggle works when storage throws.
  {
    const listeners = {}, toggled = [];
    const toggle = {checked: false, addEventListener: (k, f) => { listeners[k] = f; }};
    const context = {localStorage: {getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }},
      document: {getElementById: () => toggle, querySelector: () => ({classList: {toggle: (c, on) => toggled.push(on)}})},
      window: {dispatchEvent() {}, LiveMap: null, RoverGps: null}, Event: class { constructor(t) { this.type = t; } }};
    vm.createContext(context);
    vm.runInContext(between('const overviewToggle = document.getElementById', '\n}\n') + '\n}', context);
    toggle.checked = true; listeners.change();
    assert.deepEqual(toggled, [false, true]);
  }
  // S42: hidden page or zero-size canvas -> no resize, no drawing.
  {
    let contexts = 0;
    const canvas = rect => ({width: 0, height: 0, getBoundingClientRect: () => rect, getContext: () => { contexts++; return null; }});
    const context = {document: {hidden: false}, window: {devicePixelRatio: 1}, DPR_LIMIT: 2, Math};
    vm.createContext(context);
    vm.runInContext(between('function drawImuAttitude(', '\n}\n') + '\n}', context);
    const zero = canvas({width: 0, height: 0});
    context.drawImuAttitude(zero, 1, 2, 3);
    assert.equal(zero.width, 0); assert.equal(contexts, 0);
    context.document.hidden = true; const visible = canvas({width: 300, height: 200});
    context.drawImuAttitude(visible, 1, 2, 3); assert.equal(contexts, 0);
  }
  // S35: chart window label follows the telemetry interval.
  {
    const context = {MAX_POINTS: 90, Math, profile: 'auto'};
    context.liveNetworkProfile = () => context.profile;
    vm.createContext(context);
    vm.runInContext(between('function telemetryIntervalMs(', 'function pushHistory('), context);
    assert.equal(context.chartWindowLabel(), 'X 최근 270초');
    context.profile = 'tiny'; assert.equal(context.chartWindowLabel(), 'X 최근 900초');
    context.profile = 'detail'; assert.equal(context.chartWindowLabel(), 'X 최근 90초');
    assert.ok(!source.includes('X 최근 90초</small>'), 'fixed label removed');
  }
  // S41 + S27: an HTML 409/502 reply shows 'HTTP <status>' and the previous camera choice is restored.
  {
    const stored = {}, results = [];
    const state = {cameraModes: [{resolution: '1920x1080', fps: [30]}, {resolution: '1280x720', fps: [30]}],
      cameraLive: {resolution: '1280x720', fps: 30, settingsError: '', settingsInFlight: false}};
    const context = {state, CAMERA_RESOLUTION_KEY: 'r', CAMERA_FPS_KEY: 'f', Number, String, JSON,
      localStorage: {setItem: (k, v) => { stored[k] = v; }}, AbortController, setTimeout, clearTimeout,
      updateCameraControls() {}, setCameraResult: (kind, message) => results.push([kind, message]),
      applyCameraModeCatalog() {}, scheduleCameraReconnectForSettings() {}, cameraResolutionOptions: () => ['1920x1080', '1280x720'],
      fetch: async () => ({ok: false, status: 502, headers: {get: () => 'text/html'}, json: async () => { throw new SyntaxError("Unexpected token '<'"); }})};
    vm.createContext(context);
    vm.runInContext(between('function cameraFpsOptions(', 'function updateCameraControls('), context);
    await context.setCameraSettings({resolution: '1920x1080'});
    assert.equal(state.cameraLive.resolution, '1280x720');
    assert.equal(stored.r, '1280x720');
    assert.match(results.at(-1)[1], /HTTP 502/);
    assert.doesNotMatch(results.at(-1)[1], /Unexpected token/);
    context.fetch = async () => ({ok: false, status: 409, headers: {get: () => 'application/json'},
      json: async () => ({ok: false, error: 'camera_resolution_locked_while_recording'})});
    await context.setCameraSettings({resolution: '1920x1080'});
    assert.equal(state.cameraLive.resolution, '1280x720');
    assert.match(results.at(-1)[1], /녹화 중에는 해상도/);
  }
  // S07: list refresh re-selects only when the selected recording changed.
  {
    const selects = [];
    const item = {id: 'rec', duration_s: 10, timeline: {lidar_frames: 5}};
    const state = {recordings: {items: [item], selectedId: 'rec', selected: {id: 'rec'}, loading: false}};
    let reply = {recordings: [{timeline: {lidar_frames: 5}, duration_s: 10, id: 'rec'}]};
    const context = {state, JSON, Object, Array, renderRecordings() {}, refreshRecordingStatus: async () => {},
      getJson: async () => reply, selectRecording: async id => { selects.push(id); }};
    vm.createContext(context);
    vm.runInContext(between('async function loadRecordings(', 'async function selectRecording('), context);
    await context.loadRecordings();
    assert.deepEqual(selects, [], 'same recording (key order differs) keeps playback');
    reply = {recordings: [{id: 'rec', duration_s: 12, timeline: {lidar_frames: 9}}]};
    await context.loadRecordings();
    assert.deepEqual(selects, ['rec'], 'a changed recording is reloaded');
    state.recordings.selected = null; reply = {recordings: [{id: 'rec', duration_s: 12, timeline: {lidar_frames: 9}}]};
    await context.loadRecordings();
    assert.deepEqual(selects, ['rec', 'rec'], 'never-loaded detail is loaded');
  }
  // B6: GPS auto profile is 2 Hz (slow), 5 Hz (balanced) when the camera link measures fast.
  {
    const gps = fs.readFileSync(path.join(__dirname, '../static/gps-map.js'), 'utf8').replace(/\r\n/g, '\n');
    const block = gps.slice(gps.indexOf("  let profile = 'auto';"), gps.indexOf('  const ws = new WebSocket('));
    const pick = (stored, measured) => {
      const context = {localStorage: {getItem: () => stored}, window: {RoverLink: {cameraEffectiveProfile: () => measured}}};
      vm.createContext(context); vm.runInContext(block + '\nthis.result = profile;', context); return context.result;
    };
    assert.equal(pick(null, ''), 'slow');
    assert.equal(pick('auto', 'tiny'), 'slow');
    assert.equal(pick('auto', 'balanced'), 'balanced');
    assert.equal(pick('auto', 'detail'), 'balanced');
    assert.equal(pick('tiny', 'detail'), 'tiny', 'an explicit choice is kept');
  }
  console.log('UI bug fixes passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
