// Execute real dashboard functions with deterministic fetch/timer/DOM adapters.
// No rover bridge, motor output, service, browser or Docker is used here.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource().replace(/\r\n/g, '\n');
const between = (start, end) => {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, start);
  return source.slice(a, b);
};
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };

function recordingFixture() {
  const events = [], cache = {lidarFrames: [{t_s: 20}], cameraFrames: [], imuFrames: [], gpsFrames: []};
  const state = {recordings: {items: [{id: 'r', duration_s: 40}], selectedId: 'r', selected: {id: 'r'}, timeline: {duration_s: 40}},
    archivePlayback: {timeS: 20, durationS: 40, playing: true, cache}};
  const context = {state, Number, String, Math, Object, Array, JSON, encodeURIComponent,
    refreshRecordingStatus: async () => {}, renderRecordings() {},
    stopArchivePlayback: reset => {events.push(['stop', reset]); state.archivePlayback.timeS = 0; state.archivePlayback.playing = false;},
    clearArchiveFrameCache: () => events.push(['clear']), renderArchiveMotor() {}, renderArchiveFrame() {}, syncArchiveScrubberBounds() {},
    prepareArchiveTimelineCache: () => events.push(['prepare-cache']), configureArchiveNativeVideo: (_, options) => events.push(['video', options]),
    timelineShellFromRecording: item => item.timeline || null, scheduleArchiveSegmentsForTime: async t => events.push(['segment', t]),
    renderStatusRows: (...args) => events.push(['error', args]), debugLog() {}, $: () => null,
    getJson: async url => url === '/api/recordings' ? {recordings: [{id: 'r', duration_s: 50}]} : {id: 'r', timeline: {duration_s: 50}}};
  context.renderRecordingDetail = (detail, options) => context.setupArchivePlayback(detail, null, options);
  vm.createContext(context);
  vm.runInContext(between('async function loadRecordings(', 'function timelineShellFromRecording(') +
    between('function setupArchivePlayback(', 'function suspendLiveCameraForArchive('), context);
  return {context, state, events, cache};
}

function cameraFixture() {
  const state = {cameraModes: [{resolution: '1280x720', fps: [30]}, {resolution: '1920x1080', fps: [30]}],
    cameraLive: {resolution: '1280x720', fps: 30, settingsInFlight: false, settingsRevision: 0}};
  const timers = new Map(), stored = {}, results = [], resolution = {value: '1280x720'}, fps = {value: '30'};
  let clock = 10000, serial = 0;
  const context = {state, CAMERA_RESOLUTION_KEY: 'resolution', CAMERA_FPS_KEY: 'fps', AbortController,
    Date: {now: () => clock}, Number, Math, Object, String, JSON,
    setTimeout: (fn, delay) => {const id = ++serial; timers.set(id, {fn, delay}); return id;}, clearTimeout: id => timers.delete(id),
    localStorage: {setItem: (key, value) => {stored[key] = value;}}, text() {}, syncSelectOptions() {},
    document: {activeElement: resolution, querySelectorAll: selector => selector.endsWith('-select]') ?
      [selector.includes('resolution') ? resolution : fps] : []},
    normalizeCameraModes: modes => modes, scheduleCameraReconnectForSettings() {},
    setCameraResult: (...args) => results.push(args)};
  vm.createContext(context);
  vm.runInContext(between('function applyCameraModeCatalog(', 'function syncSelectOptions('), context);
  return {context, state, timers, stored, results, resolution, fps, advance: ms => {clock += ms;}};
}

(async () => {
  {
    const f = recordingFixture();
    await f.context.loadRecordings();
    assert.equal(f.state.archivePlayback.timeS, 20);
    assert.equal(f.state.archivePlayback.playing, true);
    assert.equal(f.state.archivePlayback.durationS, 50);
    assert.equal(f.state.archivePlayback.cache, f.cache);
    assert.deepEqual(f.events.map(event => event[0]), ['video']);
    assert.equal(f.events[0][1].preservePlayback, true);
    f.events.length = 0;
    await f.context.loadRecordings();
    assert.equal(f.events.length, 0, 'unchanged list must not fetch/reconfigure selected assets');
  }
  {
    const f = recordingFixture(), first = deferred(), second = deferred();
    let requests = 0;
    f.context.getJson = () => (++requests === 1 ? first : second).promise;
    const a = f.context.selectRecording('r', {preservePlayback: true});
    const b = f.context.selectRecording('r', {preservePlayback: true});
    second.resolve({id: 'r', label: 'latest', timeline: {duration_s: 55}}); await b;
    first.resolve({id: 'r', label: 'obsolete', timeline: {duration_s: 30}}); await a;
    assert.equal(f.state.recordings.selected.label, 'latest', 'older response for the same ID must not overwrite the latest detail');
    assert.equal(f.state.archivePlayback.durationS, 55); assert.equal(f.state.archivePlayback.timeS, 20);
  }
  {
    const f = recordingFixture();
    f.context.refreshRecordingStatus = async () => {throw new Error('offline');};
    await assert.rejects(f.context.loadRecordings(), /offline/);
    assert.equal(f.state.recordings.loading, false, 'a failed status refresh must release the list spinner');
  }
  {
    const f = cameraFixture(), pending = deferred(); let posts = 0;
    f.context.fetch = async () => {posts++; return pending.promise;};
    const change = f.context.setCameraSettings({resolution: '1920x1080'});
    assert.equal(f.resolution.disabled, true);
    await f.context.setCameraSettings({fps: 15});
    assert.equal(posts, 1, 'settings updates are single-flight');
    pending.resolve({ok: false, status: 409, headers: {get: () => 'application/json'},
      json: async () => ({ok: false, error: 'camera_resolution_locked_while_recording'})});
    await change;
    assert.equal(f.state.cameraLive.resolution, '1280x720'); assert.equal(f.stored.resolution, '1280x720');
    assert.equal(f.resolution.value, '1280x720', 'the focused selector must also roll back');
    assert.equal(f.resolution.disabled, false); assert.equal(f.timers.size, 0);
    assert.match(f.results.at(-1)[1], /녹화 중에는 해상도/);
    const old = {settings: {resolution: '1920x1080', fps: 30}};
    f.context.applyCameraStatus(old); assert.equal(f.state.cameraLive.resolution, '1280x720', 'settling window blocks telemetry');
    f.advance(7000); f.state.telemetryCameraSettingsRevision = 0;
    f.context.applyCameraStatus(old); assert.equal(f.state.cameraLive.resolution, '1280x720', 'even a very late pre-change response stays obsolete');
    f.context.applyCameraModeCatalog([{resolution: '1920x1080', fps: [30]}], 'driver');
    assert.equal(f.state.cameraModes.length, 2, 'obsolete mode catalog cannot undo the rollback either');
    f.state.telemetryCameraSettingsRevision = 1;
    f.context.applyCameraStatus(old); assert.equal(f.state.cameraLive.resolution, '1920x1080', 'fresh remote state may be adopted');
  }
  {
    const f = cameraFixture();
    f.context.fetch = (_, options) => new Promise((_, reject) => options.signal.addEventListener('abort', () =>
      reject(Object.assign(new Error('timeout'), {name: 'AbortError'})), {once: true}));
    const change = f.context.setCameraSettings({resolution: '1920x1080'});
    assert.equal([...f.timers.values()][0].delay, 8000); [...f.timers.values()][0].fn(); await change;
    assert.equal(f.state.cameraLive.settingsInFlight, false); assert.equal(f.resolution.disabled, false);
    assert.equal(f.state.cameraLive.resolution, '1280x720'); assert.equal(f.timers.size, 0);
  }
  console.log('UI metadata refresh, stale same-ID response, list failure, camera rollback/focus/timeout and telemetry revisions: PASS');
})().catch(error => {console.error(error); process.exitCode = 1;});
