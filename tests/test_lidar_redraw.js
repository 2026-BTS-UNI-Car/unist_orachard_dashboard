// Actual dashboard scheduling/scan paths; deterministic fake RAF, no browser or sensors.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2] || path.join(__dirname, '../static/app.js'), 'utf8');
const between = (begin, end) => source.slice(source.indexOf(begin), source.indexOf(end));
const animation = between('function requestChartAnimation(', 'function normalizeBps(');

function fixture(simulation = true, route = '/rover-control') {
  const frames = [], draws = [], chartDraws = [], acknowledgements = [], events = [], listeners = {};
  let serial = 0, now = 0;
  const socket = {readyState: 1, send: value => {events.push('ack'); acknowledgements.push(JSON.parse(value));}};
  const state = {animationFrame: null, chartLidarRedrawRequested: false,
    chartShift: 1, chartShiftStartedAt: 0, chartShiftDurationMs: 1080,
    history: {power: [], fanTemp: [], rx: [], tx: []}, visualHistory: {}, cpuMode: 'combined',
    lidarMap: {range: 10, displayRange: 10, auto: false}, lidarPoints: {value: 3200},
    lidarLive: {socket, messageCount: 0}, lidarScan: {ok: true, points: []},
    lastTelemetryPoll: 0, tickInFlight: false, telemetryFailures: 0};
  const context = {state, SIMULATION: simulation, POLL_INTERVAL_MS: 1000,
    currentRoute: () => route, routeSupportsLidarLive: () => ['/rover-control', '/connection'].includes(route),
    requestAnimationFrame: callback => {frames.push(callback); return ++serial;},
    clampLidarRange: value => Math.max(.5, Math.min(30, Number(value) || 10)),
    chartColors: () => ({}), normalizeBps: values => values, drawCoreCharts() {},
    drawMultiLine: (id, series) => chartDraws.push({id, series}),
    drawLidarMap: result => {events.push('draw'); draws.push({result, range: state.lidarMap.displayRange});},
    Date: {now: () => 10000 + now}, performance: {now: () => now},
    window: {innerWidth: 1000, setTimeout() {}, clearTimeout() {},
      addEventListener: (kind, callback) => {listeners[kind] = callback;}},
    document: {addEventListener() {}, body: {classList: {contains: () => false}}},
    $: () => null, setMobileNav() {}, WebSocket: {OPEN: 1}, AbortController,
    liveNetworkProfile: () => 'local', getJson: async () => ({}),
    pushHistory() {state.chartShift = 1; state.chartShiftStartedAt = now;},
    renderTelemetry() {}, setConnection() {}, debugLog() {},
    setScanResult() {}, renderReadout() {}, updateLidarExport() {},
    renderLidarLiveState() {}, renderLiveSensorLinks() {}, fmtNumber: String,
    cleanLidarError: () => '', angleCoverage: () => 360};
  vm.createContext(context); vm.runInContext(animation, context);
  const frame = timestamp => {now = timestamp; const callback = frames.shift(); assert.ok(callback, 'RAF should be queued'); callback(timestamp);};
  return {context, state, frames, draws, chartDraws, acknowledgements, events, listeners, frame};
}

async function run() {
  // A real telemetry tick schedules1080ms chart interpolation. That unrelated
  // animation must not repaint a stationary rover LiDAR at browser RAF speed.
  const idle = fixture();
  vm.runInContext(between('async function tick()', 'function pushHistory('), idle.context);
  await idle.context.tick();
  for (let i = 1; i <= 60; i++) idle.frame(i * 1000 / 60);
  assert.equal(idle.draws.length, 0);
  assert.ok(idle.state.chartShift > 0, 'history interpolation is still running');

  // Explicit requests survive a previously queued history-only frame, and
  // are consumed once rather than recurring for the entire history animation.
  idle.context.requestChartAnimation();
  idle.context.requestChartAnimation({redrawLidar: false});
  assert.equal(idle.frames.length, 1);
  idle.frame(1010); assert.equal(idle.draws.length, 1);
  idle.frame(1020); assert.equal(idle.draws.length, 1);

  // Range interpolation retains every changed viewport, including the final
  // <.025m snap, even when no new scan arrives during the animation.
  const zoom = fixture(); zoom.state.lidarMap.range = 12;
  zoom.context.requestChartAnimation();
  for (let i = 1; i <= 40; i++) zoom.frame(i * 10);
  assert.ok(zoom.draws.length > 1);
  assert.equal(zoom.draws.at(-1).range, 12);
  const settled = zoom.draws.length; zoom.frame(410);
  assert.equal(zoom.draws.length, settled);

  // Existing route/theme callers retain the default redraw intent. The real
  // resize handler now explicitly requests one rather than relying on history.
  const resize = fixture();
  vm.runInContext(between('function initMobileNav()', 'function setMobileNav('), resize.context);
  resize.context.initMobileNav(); resize.listeners.resize(); resize.frame(1);
  assert.equal(resize.draws.length, 1);
  const theme = fixture();
  theme.context.document.documentElement = {dataset: {}};
  theme.context.localStorage = {setItem() {}}; theme.context.text = () => {};
  vm.runInContext(between('function applyTheme(', 'function initCpuMode('), theme.context);
  theme.context.applyTheme('light'); theme.frame(1);
  assert.equal(theme.draws.length, 1);

  // The real incoming scan handler still paints all3200 accepted measurements
  // before sending exactlyone ACK. No scan decimation/cadence change is hidden
  // inside the scheduling optimization.
  const scan = fixture();
  vm.runInContext(between('function handleLidarLiveMessage(', 'function maybeAutoSetLidarRange('), scan.context);
  vm.runInContext(between('function normalizeLidarExportPoint(', 'function normalizeLidarResult('), scan.context);
  vm.runInContext(between('function renderLidarScan(', 'function setScanResult('), scan.context);
  scan.context.maybeAutoSetLidarRange = () => {};
  const points = Array.from({length: 3200}, (_, index) => [index * 360 / 3200, 1 + index / 3200, 0]);
  scan.context.handleLidarLiveMessage(JSON.stringify({type: 'scan', sequence: 7,
    point_encoding: 'polar-v1', points, source_point_count: 3200, requested_max_points: 3200}));
  assert.equal(scan.draws.length, 1); assert.equal(scan.draws[0].result.points.length, 3200);
  assert.deepEqual(scan.events, ['draw', 'ack']);
  assert.deepEqual(scan.acknowledgements, [{type: 'scan_ack', sequence: 7}]);
  scan.context.requestChartAnimation({redrawLidar: false}); scan.frame(1);
  assert.equal(scan.draws.length, 1, 'same scan is not repainted by unrelated history');

  // Both runtime modes/routes share the same scan contract. History alone is
  // irrelevant in every live LiDAR view, but explicit changes still repaint.
  for (const [simulation, route] of [[false, '/rover-control'], [true, '/connection'], [false, '/connection']]) {
    const common = fixture(simulation, route);
    common.context.requestChartAnimation({redrawLidar: false}); common.frame(1); common.frame(2);
    assert.equal(common.draws.length, 0);
    common.context.requestChartAnimation(); common.frame(3);
    assert.equal(common.draws.length, 1);
    vm.runInContext(between('function initMobileNav()', 'function setMobileNav('), common.context);
    common.context.initMobileNav(); common.listeners.resize(); common.frame(4);
    assert.equal(common.draws.length, 2);
  }
  const system = fixture(true, '/system');
  system.context.requestChartAnimation({redrawLidar: false}); system.frame(1);
  assert.ok(system.chartDraws.length > 0); assert.equal(system.draws.length, 0);

  // A page without history charts must not even read CSS or touch/filter/map
  // the history arrays. Exercise real RAF scheduling in both runtime modes;
  // explicit scan/zoom invalidation remains independent of history preparation.
  for (const simulation of [false, true]) for (const route of ['/rover-control', '/connection', '/research', '/docs', '/unknown']) {
    const empty = fixture(simulation, route);
    const forbidden = new Proxy({}, {get(_target, key) {assert.fail(`history read on ${route}: ${String(key)}`);}});
    empty.state.history = forbidden; empty.state.visualHistory = forbidden;
    empty.context.chartColors = () => assert.fail(`CSS read on ${route}`);
    empty.context.normalizeBps = () => assert.fail(`series allocation on ${route}`);
    empty.context.requestChartAnimation({redrawLidar: false}); empty.frame(1); empty.frame(2);
    assert.equal(empty.chartDraws.length, 0); assert.equal(empty.draws.length, 0);
    empty.context.requestChartAnimation(); empty.frame(3);
    const lidar = ['/rover-control', '/connection'].includes(route);
    assert.equal(empty.draws.length, lidar ? 1 : 0);
    empty.state.lidarMap.range = 12; empty.frame(4);
    assert.equal(empty.draws.length, lidar ? 2 : 0);
  }

  // All real chart routes retain their series, normalization and core-chart
  // mode. Changing route during an already queued RAF uses the new route.
  const plots = fixture(); let route = '/', coreDraws = 0;
  plots.context.currentRoute = () => route;
  plots.context.routeSupportsLidarLive = () => ['/rover-control', '/connection'].includes(route);
  plots.context.drawCoreCharts = () => {coreDraws += 1;};
  plots.context.chartColors = () => ({good:'green', cyan:'cyan', blue:'blue', rose:'rose', amber:'amber'});
  plots.state.history = {power:[null, 15], fanTemp:[null, 95], rx:[0, 20], tx:[0, 40]};
  plots.state.visualHistory = Object.fromEntries(['cpu','memory','swap','power','rx','tx','dockerCpu','dockerMemory','fanTemp'].map(key => [key, [null, 10]]));
  vm.runInContext(between('function normalizeBps(', 'function normalizeSeries('), plots.context);
  const expected = {
    '/':['chartOverviewSystem','chartOverviewNetwork'],
    '/system':['chartCpu','chartMemory','chartSwap','chartSystemNetwork','chartPower'],
    '/network':['chartNetwork'], '/docker':['chartDockerCpu','chartDockerMem'], '/fan':['chartFanTemp'],
  };
  for (const [next, ids] of Object.entries(expected)) {
    route = next; plots.chartDraws.length = 0; plots.context.drawCharts({redrawLidar:false});
    assert.deepEqual(plots.chartDraws.map(draw => draw.id), ids);
  }
  route = '/network'; plots.chartDraws.length = 0; plots.context.drawCharts();
  assert.deepEqual(Array.from(plots.chartDraws[0].series[0].values), [null, 50]);
  assert.deepEqual(Array.from(plots.chartDraws[0].series[1].values), [null, 25]);
  route = '/system'; plots.state.cpuMode = 'separate'; plots.chartDraws.length = 0;
  plots.context.drawCharts(); assert.equal(coreDraws, 1);
  assert.deepEqual(plots.chartDraws.map(draw => draw.id), expected['/system'].slice(1));
  assert.equal(plots.chartDraws.at(-1).series[0].max, 15);
  route = '/fan'; plots.chartDraws.length = 0; plots.context.drawCharts();
  assert.equal(plots.chartDraws[0].series[0].max, 95);
  route = '/rover-control'; plots.context.requestChartAnimation({redrawLidar:false});
  route = '/network'; plots.chartDraws.length = 0; plots.frame(1);
  assert.deepEqual(plots.chartDraws.map(draw => draw.id), ['chartNetwork']);
  route = '/connection'; plots.context.requestChartAnimation(); plots.chartDraws.length = 0; plots.frame(2);
  assert.equal(plots.chartDraws.length, 0); assert.equal(plots.draws.length, 1);
  console.log('PASS: chartless routes skip CSS/history work; all plots/core mode, route changes, scan3200/ACK, final zoom and resize/theme preserved');
}
run().catch(error => {console.error(error); process.exitCode = 1;});
