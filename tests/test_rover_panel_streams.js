// UI-16: Rover Control holds the LiDAR, camera, IMU, GPS and map panels together again (the layout of before the
// redesign). A socket opens only while its picture is on screen (NET-08 for every stream): a start waits for the
// observer's first report, off screen for 1 s closes it (not a user stop), back on screen opens it again. The page
// addresses: the eight menu pages, the six-screen addresses as aliases. Fake observers and sockets only.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource();

const between = (begin, end) => {
  const a = source.indexOf(begin), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, begin);
  return source.slice(a, b);
};

// ---- LiDAR and IMU pictures ----
const panelCode = between('// Hidden LiDAR and IMU pictures (UI-16)', 'function toggleLidarLive(');

function fixture({ route = '/rover-control', observer = true } = {}) {
  const timers = new Map(); let next = 0; const calls = []; const observers = [];
  class FakeObserver {
    constructor(callback) { this.callback = callback; this.targets = []; observers.push(this); }
    observe(element) { this.targets.push(element); }
    unobserve(element) { this.targets.splice(this.targets.indexOf(element), 1); }
  }
  const boxes = {};
  const canvas = (id) => (boxes[id] = boxes[id] || { id: `${id}-box` }, { parentElement: boxes[id] });
  const live = () => ({ socket: null, enabled: false, userStopped: false, reconnectAttempt: 3 });
  const context = {
    document: { hidden: false },
    state: { lidarLive: live(), imuLive: live() },
    $: (id) => (['roverLidarCanvas', 'lidarScanCanvas', 'roverImuCanvas'].includes(id) ? canvas(id) : null),
    currentRoute: () => context.route,
    route,
    routeSupportsLidarLive: () => ['/connection', '/rover-control'].includes(context.route),
    routeSupportsImuLive: () => context.route === '/rover-control',
    startLidarLive: () => { calls.push('startLidar'); Object.assign(context.state.lidarLive, { socket: {}, enabled: true }); },
    startImuLive: () => { calls.push('startImu'); Object.assign(context.state.imuLive, { socket: {}, enabled: true }); },
    closeLidarLive: (message, options) => { calls.push(['closeLidar', options.userStopped]); Object.assign(context.state.lidarLive, { socket: null, enabled: false, userStopped: options.userStopped }); },
    closeImuLive: (message, options) => { calls.push(['closeImu', options.userStopped]); Object.assign(context.state.imuLive, { socket: null, enabled: false, userStopped: options.userStopped }); },
    CAMERA_PANEL_HIDDEN_MS: 1000,
    window: { setTimeout: (fn, ms) => { assert.equal(ms, 1000); const id = ++next; timers.set(id, fn); return id; }, clearTimeout: (id) => timers.delete(id) },
  };
  if (observer) context.IntersectionObserver = FakeObserver;
  vm.createContext(context);
  vm.runInContext(`${panelCode}\nthis.sensorPanels = sensorPanels;`, context);
  const report = (name, visible) => {
    const watch = observers.find((o) => o.targets.includes(context.sensorPanels[name].element));
    watch.callback([{ target: context.sensorPanels[name].element, isIntersecting: visible }]);
  };
  const fire = () => { const pending = [...timers.values()]; timers.clear(); pending.forEach((fn) => fn()); };
  return { context, calls, observers, report, fire, timers, boxes };
}

for (const name of ['lidar', 'imu']) {
  const start = name === 'lidar' ? 'startLidar' : 'startImu';
  const close = name === 'lidar' ? 'closeLidar' : 'closeImu';
  const f = fixture();
  const live = name === 'lidar' ? f.context.state.lidarLive : f.context.state.imuLive;
  assert.equal(f.context.sensorPanelDefersStart(name), true, `${name}: a start waits for the first report`);
  assert.deepEqual(f.calls, []);
  f.report(name, true);
  assert.deepEqual(f.calls, [start], `${name}: on screen opens it`);
  assert.equal(live.reconnectAttempt, 0, `${name}: a fresh start`);
  assert.equal(f.context.sensorPanelDefersStart(name), false, `${name}: on screen a start goes ahead`);
  f.report(name, false);
  assert.deepEqual(f.calls, [start], `${name}: nothing closes before 1 s`);
  f.report(name, true); f.fire();
  assert.deepEqual(f.calls, [start], `${name}: a short scroll away keeps it`);
  f.report(name, false); f.fire();
  assert.deepEqual(f.calls, [start, [close, false]], `${name}: off screen for 1 s closes it, not as a user stop`);
  f.report(name, true);
  assert.deepEqual(f.calls, [start, [close, false], start], `${name}: back on screen opens it again`);
  // A user stop is left alone.
  live.userStopped = true; live.socket = null; live.enabled = false;
  f.report(name, false); f.fire(); f.report(name, true);
  assert.deepEqual(f.calls, [start, [close, false], start], `${name}: a stream the user stopped stays stopped`);
  // A hidden tab does not reopen it (the hidden-page rule does that).
  const g = fixture();
  g.context.sensorPanelDefersStart(name);
  g.context.document.hidden = true; g.report(name, true);
  assert.deepEqual(g.calls, [], `${name}: no start while the tab is hidden`);
  assert.equal(g.context.sensorPanels[name].closed, true, `${name}: still waiting for the tab (resumeSensorStreamsForVisiblePage)`);
}
{
  // Without IntersectionObserver the sockets open as before.
  const f = fixture({ observer: false });
  assert.equal(f.context.sensorPanelDefersStart('lidar'), false);
  assert.equal(f.context.sensorPanelDefersStart('imu'), false);
}
{
  // Connection has the LiDAR picture of its own; going there watches that one instead.
  const f = fixture();
  f.context.watchSensorPanel('lidar');
  const first = f.context.sensorPanels.lidar.element;
  assert.equal(first, f.boxes.roverLidarCanvas);
  f.report('lidar', true);
  f.context.route = '/connection';
  f.context.watchSensorPanel('lidar');
  assert.equal(f.context.sensorPanels.lidar.element, f.boxes.lidarScanCanvas);
  assert.equal(f.context.sensorPanels.lidar.visible, null, 'the new picture is not reported yet');
  assert.ok(!f.observers[0].targets.includes(first) && f.observers[0].targets.includes(f.boxes.lidarScanCanvas), 'one watch, moved');
  // Connection has no IMU picture: nothing is watched for it.
  f.context.watchSensorPanel('imu');
  assert.equal(f.context.sensorPanels.imu.element, null);
}
// The starts ask the panel rule; the camera waits for its first report too.
assert.match(source, /clearLidarReconnectTimer\(\);\n  if \(sensorPanelDefersStart\("lidar"\)\) return;/);
assert.match(source, /state\.imuLive\.reconnectTimer = null;\n  \}\n  if \(sensorPanelDefersStart\("imu"\)\) return;/);
assert.match(source, /if \(cameraPanel\.visible === false \|\| \(cameraPanel\.observer && cameraPanel\.element && cameraPanel\.visible === null\)\) \{/);
// renderRoute moves every watch to the pictures of the page it opens (a socket kept across a page change follows them).
const route = between('function renderRoute() {', 'function initMobileNav(');
assert.match(route, /if \(routeSupportsLidarLive\(route\)\) \{\n    watchSensorPanel\("lidar"\);/);
assert.match(route, /if \(routeSupportsCameraLive\(route\)\) \{\n    watchCameraPanel\(\);/);
assert.match(route, /if \(routeSupportsImuLive\(route\)\) \{\n    watchSensorPanel\("imu"\);/);

// ---- Addresses ----
{
  const config = between('const ROUTES = ', 'const HISTORY_KEYS');
  const shell = between('function canonicalPath(', 'function focusRoutePart(');
  const context = { location: { pathname: '/' } };
  vm.createContext(context);
  vm.runInContext(`${config}\n${shell}\nthis.ROUTE_FOCUS = ROUTE_FOCUS;`, context);
  const at = (pathname) => { context.location.pathname = pathname; return context.currentRoute(); };
  const pages = { '/': '/', '/connection': '/connection', '/system': '/system', '/network': '/network', '/docker': '/docker',
    '/fan': '/fan', '/rover-control': '/rover-control', '/docs': '/docs', '/docs/lidar-connection': '/docs',
    // six-screen addresses and the removed research page
    '/drive': '/rover-control', '/map': '/rover-control', '/records': '/rover-control', '/tuning': '/rover-control',
    '/sensors': '/connection', '/system/resources': '/system', '/system/network': '/network', '/system/docker': '/docker',
    '/system/fan': '/fan', '/research': '/', '/map/': '/rover-control', '/unknown': '/' };
  for (const [pathname, page] of Object.entries(pages)) assert.equal(at(pathname), page, pathname);
  assert.deepEqual({ ...context.ROUTE_FOCUS }, { '/map': 'liveMapPanel', '/records': 'roverRecordPanel', '/tuning': 'tuningPanel' });
  const streams = (page) => [context.routeSupportsLidarLive(page), context.routeSupportsCameraLive(page), context.routeSupportsImuLive(page)];
  assert.deepEqual(streams('/rover-control'), [true, true, true], 'Rover Control: LiDAR, camera, IMU');
  assert.deepEqual(streams('/connection'), [true, true, false], 'Connection: its LiDAR map and camera, no IMU picture');
  for (const page of ['/', '/system', '/network', '/docker', '/fan', '/docs']) assert.deepEqual(streams(page), [false, false, false], page);
}
console.log('PASS: LiDAR and IMU sockets follow their pictures like the camera; starts wait for the first report; menu addresses and aliases');
