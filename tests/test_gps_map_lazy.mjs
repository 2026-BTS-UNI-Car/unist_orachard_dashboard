// NET-03: the GPS map library and its label font are loaded when a map is wanted, not with the page.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../static/gps-map.js', import.meta.url), 'utf8');
assert.doesNotMatch(source, /^import[^\n]*maplibre/m, 'gps-map.js has no static import of the map library');
assert.match(source, /const MAP_LIBRARY_URL = "\/static\/vendor\/maplibre-gl\/maplibre-gl\.mjs";/);
assert.match(source, /const MAP_WORKER_URL = "\/static\/vendor\/maplibre-gl\/maplibre-gl-worker\.mjs";/);
assert.match(source, /if \(routeOn\(\)\) \{ watchGpsPanel\(\); live\.initWhenVisible\(\); \}\n  if \(active\(\)\) connect\(\);/, 'the live map waits until it is visible');

// mapLibrary(): one import, the style sheet and pmtiles reader added once, worker address and pmtiles protocol set
// once, and a failed load can be retried.
{
  const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
  for (const file of ['maplibre-gl.css', 'pmtiles.js', 'maplibre-gl.mjs']) assert.ok(!html.includes(file), `index.html does not load ${file}`);
  const code = source.slice(source.indexOf('const MAP_LIBRARY_URL'), source.indexOf('const byId'))
    .replace('import(MAP_LIBRARY_URL)', '__import(MAP_LIBRARY_URL)') + '\nglobalThis.mapLibrary = mapLibrary;';
  const imports = [], workers = [], protocols = [], added = [];
  let fail = true;
  const library = {setWorkerUrl: url => workers.push(url), addProtocol: (name, handler) => protocols.push([name, typeof handler])};
  const window = {};
  const head = {children: [], append(element) {
    this.children.push(element); added.push(element.tagName + ' ' + (element.href || element.src));
    // The script defines window.pmtiles when it runs; both load a moment later.
    setTimeout(() => { if (element.tagName === 'SCRIPT') window.pmtiles = {Protocol: class { tile() {} }}; element.onload(); }, 0);
  }};
  const document = {head,
    createElement: tag => ({tagName: tag.toUpperCase(), dataset: {}, remove() { head.children.splice(head.children.indexOf(this), 1); }}),
    querySelector: selector => selector === 'link[data-map-style]' ? head.children.find(e => e.tagName === 'LINK' && 'mapStyle' in e.dataset) ?? null : null};
  const context = {window, document, setTimeout,
    __import: url => { imports.push(url); if (fail) { fail = false; return Promise.reject(new Error('offline')); } return Promise.resolve(library); }};
  vm.createContext(context); vm.runInContext(code, context);
  await assert.rejects(context.mapLibrary(), /offline/);
  await new Promise(r => setTimeout(r, 5));
  const [a, b] = await Promise.all([context.mapLibrary(), context.mapLibrary()]);
  assert.equal(a, library); assert.equal(b, library);
  await context.mapLibrary();
  assert.deepEqual(imports, ['/static/vendor/maplibre-gl/maplibre-gl.mjs', '/static/vendor/maplibre-gl/maplibre-gl.mjs'], 'imported again only after the failure');
  assert.deepEqual(added, ['SCRIPT /static/vendor/pmtiles/pmtiles.js', 'LINK /static/vendor/maplibre-gl/maplibre-gl.css'],
    'the reader and the style sheet are added once, even though the first import failed');
  assert.deepEqual(workers, ['/static/vendor/maplibre-gl/maplibre-gl-worker.mjs']);
  assert.deepEqual(protocols, [['pmtiles', 'function']]);
}

// initWhenVisible(): nothing loads until the map box intersects the screen; then init() runs once.
{
  const code = source.slice(source.indexOf('class GpsMapView'), source.indexOf('const live = new GpsMapView')) + '\nglobalThis.View = GpsMapView;';
  const observers = [];
  const element = {addEventListener() {}, setAttribute() {}, classList: {toggle() {}}, textContent: ''};
  const context = {console, performance: {now: () => 0}, FollowModel: class { update() {} }, byId: () => element, text() {},
    IntersectionObserver: class { constructor(callback, options) { this.callback = callback; this.options = options; this.targets = []; observers.push(this); }
      observe(target) { this.targets.push(target); } disconnect() { this.disconnected = true; } }};
  vm.createContext(context); vm.runInContext(code, context);
  const view = new context.View('roverGps');
  let inits = 0; view.init = () => { inits++; };
  view.initWhenVisible(); view.initWhenVisible();
  assert.equal(observers.length, 1, 'one observer however often the route changes'); assert.equal(inits, 0);
  assert.equal(observers[0].options.rootMargin, '200px');
  observers[0].callback([{isIntersecting: false}]); assert.equal(inits, 0);
  observers[0].callback([{isIntersecting: true}]); assert.equal(inits, 1); assert.equal(observers[0].disconnected, true);
  // Without IntersectionObserver the map starts at once, as before.
  delete context.IntersectionObserver;
  const plain = new context.View('roverGps'); let plainInits = 0; plain.init = () => { plainInits++; };
  plain.initWhenVisible(); assert.equal(plainInits, 1);
}
console.log('gps map lazy loading tests passed');
