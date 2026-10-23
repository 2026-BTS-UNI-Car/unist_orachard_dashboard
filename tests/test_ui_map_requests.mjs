import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const elements = new Map(), timers = new Map(); let serial = 0, calls = 0;
const element = id => {if (!elements.has(id)) elements.set(id, {addEventListener() {}, disabled: false, textContent: ''}); return elements.get(id);};
class MetricMap {constructor() {this.snapshot = {}; this.tileEpoch = null;} set(v) {this.snapshot = v; return Promise.resolve();} draw() {} zoom() {}}
const context = vm.createContext({MetricMap, ArchiveMapBuffer: class {}, ArchiveSegmentGallery: class {reset() {}},
  decodeArchiveWindow: async v => v, renderJobs() {}, LIVE_MAP_TIMEOUT_MS: 3000, livePollDelay: () => 1000,
  livePathQuery: () => ({}), acceptLiveResponse: () => true, AbortController, URLSearchParams, performance, Date,
  // Deliberately no AbortSignal.any or AbortSignal.timeout (old mobile browser).
  location: {pathname: '/other'}, document: {hidden: false, getElementById: element, querySelectorAll: () => [], addEventListener() {}},
  window: {addEventListener() {}}, setInterval() {},
  setTimeout: (fn, delay) => {const id = ++serial; timers.set(id, {fn, delay}); return id;}, clearTimeout: id => timers.delete(id)});
const source = fs.readFileSync(new URL('../static/live-map.js', import.meta.url), 'utf8').replace(/^import .*;\r?\n/gm, '');
vm.runInContext(source, context);
const run = code => vm.runInContext(code, context);
const good = value => ({ok: true, status: 200, headers: {get: () => 'application/json'}, json: async () => value});
context.fetch = async () => {calls++; return good({ok: true, map_epoch: 'a', grid: {}, scan_count: 1});};
await run("selectArchive('r')");
run("archiveMappingGeneration='g';archiveMapEpoch='a';archiveWaitReason='old wait'");
const before = calls; await run("selectArchive('r')");
assert.equal(calls, before); assert.equal(run('archiveMappingGeneration'), 'g'); assert.equal(run('archiveMapEpoch'), 'a');
await run("selectArchive('s')"); assert.equal(run('archiveWaitReason'), '');
assert.equal(timers.size, 0, 'completed requests release their timers');
let listeners = 0, removed = 0;
context.external = {aborted: false, addEventListener() {listeners++;}, removeEventListener() {removed++;}};
await run("request('/test', undefined, external)"); assert.equal(listeners, 1); assert.equal(removed, 1); assert.equal(timers.size, 0);
context.fetch = async () => ({ok: false, status: 502, headers: {get: () => 'text/html'}, json: async () => {throw new SyntaxError('html');}});
await assert.rejects(run("request('/test')"), /HTTP 502/); assert.equal(timers.size, 0);
const bodyAbort = new AbortController(); context.bodyAbortSignal = bodyAbort.signal;
let bodyStarted; const bodyReady = new Promise(resolve => {bodyStarted = resolve;});
context.fetch = async (_, options) => ({ok: true, status: 200, headers: {get: () => 'application/json'},
  json: () => new Promise((_, reject) => {
    options.signal.addEventListener('abort', () => reject(Object.assign(new Error('body aborted'), {name: 'AbortError'})), {once: true});
    bodyStarted();
  })});
const bodyPending = run("request('/test', undefined, bodyAbortSignal)");
await bodyReady; bodyAbort.abort();
await assert.rejects(bodyPending, error => error.name === 'AbortError' && !/HTTP 200/.test(error.message));
assert.equal(timers.size, 0, 'body cancellation releases the deadline and remains silent to archive retry');
context.fetch = async (_, options) => new Promise((_, reject) => options.signal.addEventListener('abort', () =>
  reject(Object.assign(new Error('aborted'), {name: 'AbortError'})), {once: true}));
const timeout = run("request('/map-window?x=1')");
assert.equal([...timers.values()][0].delay, 30000); [...timers.values()][0].fn();
await assert.rejects(timeout, error => error.name === 'TimeoutError' && /시간 초과/.test(error.message)); assert.equal(timers.size, 0);
context.fetch = async () => good({ok: true});
await run("acceptArchiveMap({grid:{},scan_count:1,mapping_job:{stage:'waiting_live_priority'}})");
assert.match(run('archiveWaitReason'), /실시간 지도 우선/);
await run("acceptArchiveMap({grid:{},scan_count:2,mapping_job:{stage:'localizing'}})"); assert.equal(run('archiveWaitReason'), '');
run("archivePlaying=true;cursorMapping=true;immutableCursorMap=false;archiveTimeS=3");
context.fetch = async () => good({ok: true, waiting_autonomy_priority: true,
  map_snapshot: {grid: {}, scan_count: 2, frame_t_s: 3, playback_driven: true}});
await run('pushPlayback(true)');
assert.match(run('archiveWaitReason'), /자율주행 우선/);
assert.match(element('liveArchiveStatus').textContent, /자율주행 우선/, 'inline retained map cannot hide the current priority wait');
console.log('Same-record epoch, legacy abort/timeout cleanup, HTTP error, job wait/reset and inline priority label: PASS');
