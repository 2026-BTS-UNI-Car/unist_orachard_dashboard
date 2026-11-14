// UI-16: the parts of Rover Control that ask the server on a timer do so only while they are wanted:
// the live occupancy grid map and the executor's local grid while the map part is on screen, the field tuning
// values while their collapsed part is open, the potentiometer rows from the steering panel's own status reply.
// Fake DOM, observers and requests only.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class FakeObserver {
  static all = [];
  constructor(callback) { this.callback = callback; this.targets = []; FakeObserver.all.push(this); }
  observe(target) { this.targets.push(target); }
  unobserve() {}
  report(visible) { this.callback(this.targets.map((target) => ({ target, isIntersecting: visible }))); }
}
const settle = () => new Promise((resolve) => setImmediate(resolve));

// ---- Live occupancy grid map (static/live-map.js) ----
{
  FakeObserver.all = [];
  const elements = new Map();
  const element = (id) => elements.get(id) || elements.set(id, { id, textContent: '', value: 0, disabled: false, open: false,
    addEventListener() {}, classList: { toggle() {} }, setAttribute() {} }).get(id);
  const requests = [];
  class MetricMap { constructor() { this.snapshot = {}; } set(s) { this.snapshot = s; } draw() {} zoom() {} }
  const sandbox = { MetricMap, ArchiveSegmentGallery: class { reset() {} update() {} }, decodeArchiveWindow: async (v) => v, renderJobs() {},
    LIVE_MAP_TIMEOUT_MS: 3000, livePollDelay: () => 1000, livePathQuery: () => ({}), acceptLiveResponse: () => false,
    console, URLSearchParams, AbortController, AbortSignal, IntersectionObserver: FakeObserver, performance, Date,
    location: { pathname: '/rover-control' },
    document: { hidden: false, getElementById: element, querySelectorAll: () => [], addEventListener() {}, activeElement: null },
    window: { addEventListener() {} }, setInterval() {}, setTimeout: () => 0, clearTimeout() {},
    fetch: (url) => { requests.push(url); return Promise.resolve({ ok: true, json: async () => ({ ok: true, speed_percent: 50 }) }); } };
  const source = fs.readFileSync(new URL('../static/live-map.js', import.meta.url), 'utf8').replace(/^import .*;\r?\n/gm, '');
  vm.runInNewContext(source, sandbox);
  const watch = FakeObserver.all.find((o) => o.targets.includes(element('liveMapPanel')));
  assert.ok(watch, 'the live map part is watched');
  await settle();
  assert.equal(requests.filter((url) => url.startsWith('/api/live/map')).length, 0, 'no map request before the first report');
  watch.report(true); await settle();
  assert.equal(requests.filter((url) => url.startsWith('/api/live/map')).length, 1, 'on screen: the map is asked for');
  watch.report(false); await settle();
  sandbox.window.LiveMap.routeChanged(); await settle();
  assert.equal(requests.filter((url) => url.startsWith('/api/live/map')).length, 1, 'off screen: no new map request');
  // The recording map follows its player, wherever the live map is.
  sandbox.window.LiveMap.selectArchive('rec');
  await settle();
  assert.ok(requests.some((url) => url.includes('/rec/')), 'the recording map is asked for while the live map is off screen');
  // Another page: nothing.
  const before = requests.length;
  sandbox.location.pathname = '/connection';
  watch.report(true); await settle();
  assert.equal(requests.length, before, 'off Rover Control nothing is asked for');
}

// ---- Steering modules: a small DOM ----
class El {
  constructor(id = '') { this.id = id; this.children = []; this.listeners = {}; this.dataset = {}; this.style = {}; this.hidden = false;
    this.textContent = ''; this.className = ''; this.value = ''; this.open = false; this.classList = { toggle() {}, add() {}, remove() {} }; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatch(type) { (this.listeners[type] || []).forEach((fn) => fn({ type })); }
  replaceChildren(...nodes) { this.children = nodes; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(name, value) { this[name] = value; }
  getBoundingClientRect() { return { width: 0, height: 0 }; }
  querySelectorAll() { return []; }
}
const registry = new Map();
const byId = (id) => registry.get(id) || registry.set(id, new El(id)).get(id);
globalThis.document = { hidden: false, getElementById: byId, createElement: () => new El(), querySelector: () => null, querySelectorAll: () => [],
  addEventListener() {}, documentElement: {} };
globalThis.window = { confirm: () => false, devicePixelRatio: 1 };
globalThis.IntersectionObserver = FakeObserver;

function pageApi(route = '/rover-control') {
  const calls = [];
  return { calls, route, currentRoute() { return this.route; },
    getJson: (url) => { calls.push(url); return Promise.reject(new Error('offline')); } };
}

// Executor's local grid: asked for only while the live map part is on screen.
{
  FakeObserver.all = [];
  byId('localGridPanel'); byId('localGridCanvas'); const mapPart = byId('liveMapPanel');
  const { initLocalGrid } = await import('../static/steer/local-grid.mjs');
  const page = pageApi();
  const screen = initLocalGrid(page);
  await settle();
  assert.deepEqual(page.calls, [], 'no executor request before the map part is reported');
  const watch = FakeObserver.all.find((o) => o.targets.includes(mapPart));
  watch.report(true); await settle();
  assert.deepEqual(page.calls, ['/api/executor/status?grid=1'], 'on screen: asked for');
  watch.report(false); screen.routeChanged(); await settle();
  assert.equal(page.calls.length, 1, 'off screen: the timer stops');
}

// Field tuning values: asked for only while the collapsed part is open.
{
  const details = byId('tuningDetails'); byId('tuneBasic');
  const { initTuningPage } = await import('../static/steer/tuning-page.mjs');
  const page = pageApi();
  initTuningPage(page);
  await settle();
  assert.deepEqual(page.calls, [], 'closed: nothing is asked for');
  details.open = true; details.dispatch('toggle'); await settle();
  assert.deepEqual(page.calls.sort(), ['/api/executor/tuning', '/api/executor/tuning/log'], 'opened: the bundle and the log');
  details.open = false; details.dispatch('toggle'); await settle();
  page.route = '/rover-control';
  assert.equal(page.calls.length, 2, 'closed again: no more requests');
}

// Potentiometer rows: the steering panel's fresh status reply, no request of their own.
{
  globalThis.UbtsSteerRules = { mradToDeg: (mrad) => mrad * 0.0572958 };
  byId('steerPotDetails'); byId('steerPotState');
  const { initSteerSensor } = await import('../static/steer/steer-sensor.mjs');
  const page = pageApi();
  let shared = { reply: { steering: { measured_mrad: 50, measured_permille: 100, raw: 512, calibrated: true, age_ms: 20, faults: [] } }, at: Date.now() };
  page.driveStatus = () => shared;
  const screen = initSteerSensor(page);
  await settle();
  assert.deepEqual(page.calls, [], 'a fresh steering panel reply is shown without a request');
  assert.equal(byId('steerPotState').textContent, '정상');
  // The steering panel's reply got old: leaving and opening the page again polls at once.
  shared = { ...shared, at: Date.now() - 5000 };
  page.route = '/connection'; screen.routeChanged();
  page.route = '/rover-control'; screen.routeChanged(); await settle();
  assert.deepEqual(page.calls, ['/api/rover/status'], 'an old shared reply: the rows ask themselves');
  page.route = '/connection'; screen.routeChanged();
}
console.log('PASS: live map and local grid only while the map part is on screen; tuning only while open; potentiometer rows share the steering reply');
