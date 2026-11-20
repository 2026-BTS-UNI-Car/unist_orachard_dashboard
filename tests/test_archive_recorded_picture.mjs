// The recorded executor part of the recording player (static/app/12-archive.js with static/steer/recorded-grid.mjs,
// 2026-11-20): shown only for a recording with executor states (or an autonomy recording before they arrive), the
// values of the state at or before the playback time, the moving picture drawn for the playback time, every loaded
// LiDAR window handed to the picture's store (also the ones that arrive before the module), the windows before a jump
// in time asked for one at a time, the newest first, and nothing left of the old recording after another is chosen.
// The real page functions run in a VM with a fake page; no browser, no request leaves the process, synthetic data.
// Run: node --test tests/test_archive_recorded_picture.mjs
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import * as recordedGrid from '../static/steer/recorded-grid.mjs';

const require = createRequire(import.meta.url);
const source = require('./helpers/app_source').readAppSource();
const slice = (start, end) => { const a = source.indexOf(start), b = source.indexOf(end, a + start.length); assert.ok(a >= 0 && b > a, start); return source.slice(a, b); };
const IMPORT = 'import("/static/steer/recorded-grid.mjs")';
const part = slice('const ARCHIVE_EXECUTOR_KEEP = 3000;', 'function renderRecordingDetail(');
assert.equal(part.split(IMPORT).length, 2, 'the picture module is loaded in one place, when it is first needed');
const code = slice('function recordingTag(item) {', '// A time in words') + slice('async function loadArchiveTimelineSegment(', 'function archiveCameraFrameSrc(')
  + part.replace(IMPORT, 'loadRecordedGrid()') + slice('function archiveMotorFrameAtOrBefore(', 'function archiveMotorPwmLabel(');

const settle = async () => { for (let n = 0; n < 6; n += 1) await new Promise((resolve) => setImmediate(resolve)); };
const { LIDAR_FRAME, LIDAR_YAW_OFFSET_DEG } = recordedGrid;
const ID = '20261120T000000Z_both';

// a row of two tree lines 2 m to each side, a car at 0.4 m/s along x; states four a second, a LiDAR frame every 0.55 s
const stateAt = (t, extra = {}) => ({ t_s: t, state: { schema: 'ubts.row_executor.state.v1', run_id: 3, pose: { x: 0.4 * t, y: 0, yaw_deg: 0 }, lat_m: 0.012, head_deg: -1.5,
  speed_mps: 0.4, dist_m: 0.4 * t, corridor_ok: true, width_m: 4, throttle_pct: 46, steer_cmd_mrad: 17.45, steer_meas_mrad: 0,
  centreline: { odom: [[0.4 * t - 2, 0], [0.4 * t + 8, 0]], width_m: 4 }, grid_centreline: { odom: [[0.4 * t - 2, 0], [0.4 * t + 8, 0]], width_m: 4 },
  lookahead: { base: [3, 0], odom: [0.4 * t + 3, 0] }, gnss: { mode: 'off', state: 'off' }, ...extra } });
const statesOf = (from, to) => { const out = []; for (let t = from; t < to - 1e-9; t += 0.25) out.push(stateAt(Math.round(t * 1000) / 1000)); return out; };
function lidarFrame(t) {
  const x = 0.4 * (t - 0.08) + 0.1185, points = [];
  for (let n = 0; n < 720; n += 1) {
    const angle = n / 2, s = Math.sin((angle + LIDAR_YAW_OFFSET_DEG) * Math.PI / 180);
    const range = Math.abs(s) > 1e-6 ? 2.03 / Math.abs(s) : Infinity;
    if (range <= 30) points.push([angle, Math.round(range * 1000) / 1000, 32]);
  }
  return { t_s: t, frame_id: 'laser', coordinate_frame: LIDAR_FRAME, points, x };
}
const FRAME_TIMES = Array.from({ length: 200 }, (_, k) => Math.round((0.3 + k * 0.55) * 1000) / 1000);
const windowAnswer = (start, duration) => ({ ok: true, timeline: { coordinate_frame: LIDAR_FRAME, segment: { start_s: start, end_s: start + duration },
  playback: { profile: 'dashboard-polar-v2', lidar_point_fields: ['angle_deg', 'distance_m', 'quality'] },
  lidar: { frames: FRAME_TIMES.filter((t) => t >= start && t < start + duration).map(lidarFrame) } } });

function rig({ tag = 'autonomy', lidarFrames = 200, holdModule = false } = {}) {
  const calls = [];
  const drawing = () => new Proxy({}, { get: (target, name) => (name in target ? target[name] : (...args) => { calls.push([name, ...args]); }), set: (target, name, value) => { target[name] = value; return true; } });
  const el = (extra = {}) => ({ hidden: false, textContent: '', innerHTML: '', dataset: {}, ...extra });
  const log = { timeline: [], telemetry: [], imports: 0, debug: [], timers: [], observers: [], sizes: [], measured: 0 };
  const elements = { roverArchiveAutoPanel: el({ hidden: true }), roverArchiveAutoTime: el(), roverArchiveAutoStatus: el(), roverArchiveAutoGrid: el(),
    roverArchiveAutoCanvas: { width: 0, height: 0, ctx: drawing(), getContext() { return this.ctx; }, getBoundingClientRect: () => { log.measured += 1; return { width: 358, height: 440 }; } } };
  let clock = 1000, gate = null, failWith = null, arrive = null;
  const module = holdModule ? new Promise((resolve) => { arrive = () => resolve(recordedGrid); }) : Promise.resolve(recordedGrid);
  const context = {
    state: { recordings: { selectedId: ID, items: [{ id: ID, tag }], timeline: { sync: { lidar_frames: lidarFrames } } },
      archivePlayback: { timeS: 0, segmentDurationS: 4, lastExecutorKey: '', cache: { executorFrames: [], lidarFrames: [] }, segmentCache: new Map(),
        loadingSegments: new Map(), loadingTelemetry: new Map(), telemetrySegments: new Map() } },
    $: (id) => elements[id] || null, text: (id, value) => { elements[id].textContent = value; },
    escapeHtml: (value) => String(value), fmtNumber: (value, digits) => Number(value).toFixed(digits),
    DPR_LIMIT: 1.5, ARCHIVE_SEGMENT_SECONDS: 4, ARCHIVE_PLAYBACK_PROFILE: 'dashboard-polar-v2',
    debugLog: (...args) => { log.debug.push(args); }, performance: { now: () => clock },
    setTimeout: (fn, ms) => { log.timers.push({ fn, ms }); return log.timers.length; }, clearTimeout: (id) => { if (id) log.timers[id - 1] = null; },
    getComputedStyle: () => ({ getPropertyValue: () => '' }), document: { documentElement: {} }, matchMedia: () => ({ matches: false }),
    URLSearchParams, AbortController, encodeURIComponent, Promise, console,
    IntersectionObserver: class { constructor(callback) { this.callback = callback; log.observers.push(this); } observe() {} },
    ResizeObserver: class { constructor(callback) { this.callback = callback; log.sizes.push(this); } observe() {} },
    currentRoute: () => '/rover-control', trimArchiveTimelineCache() {}, mergeArchiveTimelineSegment() {},
    renderArchiveFrame: () => context.renderArchiveExecutor(context.state.archivePlayback.cache.executorFrames, context.state.archivePlayback.timeS),
    // the server's answers: a LiDAR window of the recording, a telemetry window with its executor states
    getJson: async (url, options = {}) => {
      const query = new URLSearchParams(url.split('?')[1]);
      log.timeline.push(Number(query.get('start_s')));
      if (gate) await gate;
      if (options.signal?.aborted) { const error = new Error('aborted'); error.name = 'AbortError'; throw error; }
      if (failWith) throw failWith;
      return windowAnswer(Number(query.get('start_s')), Number(query.get('duration_s')));
    },
    loadArchiveTelemetrySegment: async (id, start) => {
      log.telemetry.push(start);
      context.mergeArchiveExecutorSegment({ frames: statesOf(start, start + 24) });
      context.state.archivePlayback.telemetrySegments.set(`${id}:${start}`, start);
    },
    loadRecordedGrid: () => { log.imports += 1; return module; },
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  const grid = () => vm.runInContext('archiveGrid', context);
  return { context, elements, calls, log, grid, playback: context.state.archivePlayback,
    tick(ms) { clock += ms; }, hold(promise) { gate = promise; }, fail(error) { failWith = error; }, arrive: () => arrive(),
    drawn: (name) => calls.filter(([call]) => call === name).length,
    render(t) { context.state.archivePlayback.timeS = t; context.renderArchiveExecutor(context.state.archivePlayback.cache.executorFrames, t); },
    async runTimer() { const k = log.timers.findIndex(Boolean); assert.ok(k >= 0, 'a timer waits'); const { fn } = log.timers[k]; log.timers[k] = null; await fn(); await settle(); },
    waiting: () => log.timers.filter(Boolean).map((timer) => timer.ms) };
}

// the module draws through the page's globals
const saved = { document: globalThis.document, window: globalThis.window };
globalThis.window = { devicePixelRatio: 1 };
globalThis.document = { createElement: () => ({ width: 0, height: 0, getContext: () => new Proxy({}, { get: () => () => {}, set: () => true }) }) };
test.after(() => { globalThis.document = saved.document; globalThis.window = saved.window; });

test('the page hands every loaded LiDAR window to the picture and empties the store with the cache', () => {
  assert.match(source, /mergeArchiveTimelineSegment\(payload\.timeline\);\s+if \(typeof archiveGridIngest === "function"\) archiveGridIngest\(payload\.timeline, start, duration\);/);
  assert.match(slice('function clearArchiveFrameCache() {', 'function prepareArchiveTimelineCache('), /if \(typeof archiveGridReset === "function"\) archiveGridReset\(\);/);
  assert.match(slice('function renderArchiveFrame(', 'function mergeArchiveMotorSegment('), /renderArchiveExecutor\(cache\.executorFrames \|\| \[\], timeS\)/, 'drawn with every frame of the player, for its playback time');
});

test('a recording without executor state: no part, no picture module, no frames kept, no error', async () => {
  const r = rig({ tag: 'manual' });
  r.render(3);
  await r.context.loadArchiveTimelineSegment(ID, 0, 4);
  r.render(3.5);
  await settle();
  assert.equal(r.elements.roverArchiveAutoPanel.hidden, true);
  assert.deepEqual([r.log.imports, r.grid().pending.length, r.grid().view, r.calls.length, r.waiting()], [0, 0, null, 0, []]);
  assert.deepEqual(r.log.debug, []);
  // no recording chosen
  r.context.state.recordings.selectedId = null;
  r.render(0);
  assert.equal(r.elements.roverArchiveAutoPanel.hidden, true);
});

test('an autonomy recording: the part shows at once, the frames that arrive before the module are not lost, the picture is drawn', async () => {
  const r = rig({ holdModule: true });
  r.render(0);
  assert.equal(r.elements.roverArchiveAutoPanel.hidden, false, 'before any state arrives');
  assert.equal(r.elements.roverArchiveAutoStatus.textContent, '이 기록에 실행기 상태가 없거나 서버가 아직 보내지 않았습니다.');
  assert.equal(r.log.imports, 1);
  // the first window and the states arrive while the module is on its way
  await r.context.loadArchiveTimelineSegment(ID, 0, 4);
  r.context.mergeArchiveExecutorSegment({ frames: statesOf(0, 24) });
  r.render(0.1);
  assert.deepEqual([r.grid().pending.length, r.grid().view, r.calls.length], [1, null, 0], 'kept for the module, nothing drawn yet');
  r.arrive();
  await settle();
  const { view } = r.grid();
  assert.ok(view, 'the module arrived');
  assert.deepEqual([r.log.imports, r.grid().pending.length], [1, 0], 'loaded once');
  assert.equal(view.grid.scans.length, FRAME_TIMES.filter((t) => t < 4).length, 'the frames of the window that came first');
  assert.ok(view.grid.covered(0));
  assert.ok(r.drawn('clearRect') >= 1, 'a picture was drawn when the module arrived');
  // playing on: each window the player loads goes to the store, the picture follows the playback time
  await r.context.loadArchiveTimelineSegment(ID, 4, 4);
  r.render(7.9);
  assert.equal(view.grid.scans.length, FRAME_TIMES.filter((t) => t < 8).length);
  assert.equal(r.drawn('drawImage'), 1, 'the cells as a sheet under the lines');
  const strokes = r.drawn('stroke');
  r.render(7.9);
  assert.equal(r.drawn('stroke'), strokes, 'the same time again draws nothing');
  r.render(7.95);
  assert.ok(r.drawn('stroke') > strokes, 'a later time draws the next picture');
  assert.deepEqual(r.waiting(), [], 'played from the start: nothing is asked for besides what the player loads');
  assert.deepEqual(r.log.timeline, [0, 4]);
});

test('the values of the moment stay: the state at or before the playback time, never a later one', async () => {
  const r = rig();
  r.context.mergeArchiveExecutorSegment({ frames: [stateAt(1, { lat_m: 0.111 }), stateAt(2, { lat_m: 0.222, dist_m: 12.34 }), { t_s: 3, state: { run_id: 3, lat_m: 0.333 } }] });
  r.render(0.5);
  assert.equal(r.elements.roverArchiveAutoStatus.textContent, '이 시각 이전에 기록된 실행기 상태가 없습니다.');
  assert.equal(r.elements.roverArchiveAutoTime.textContent, '--');
  assert.equal(r.elements.roverArchiveAutoGrid.innerHTML, '');
  r.render(2.9);
  assert.equal(r.elements.roverArchiveAutoTime.textContent, '2.0 s');
  assert.match(r.elements.roverArchiveAutoGrid.innerHTML, /<dt>횡 오차<\/dt><dd>0\.22 m<\/dd>/);
  assert.match(r.elements.roverArchiveAutoGrid.innerHTML, /<dt>거리<\/dt><dd>12\.3 m<\/dd>/);
  assert.match(r.elements.roverArchiveAutoGrid.innerHTML, /<dt>조향 명령 \/ 측정<\/dt><dd>1\.0° \/ 0\.0°<\/dd>/);
  assert.doesNotMatch(r.elements.roverArchiveAutoGrid.innerHTML, /0\.33 m|0\.11 m/);
  assert.equal(r.elements.roverArchiveAutoStatus.textContent, '재생 시각 이전에 기록된 마지막 실행기 상태입니다. 나무 줄은 기록된 LiDAR로 다시 그렸습니다.');
  // a state without a pose has values but no picture: the sentence about the picture is left out
  r.render(3.2);
  assert.match(r.elements.roverArchiveAutoGrid.innerHTML, /<dd>0\.33 m<\/dd>/);
  assert.equal(r.elements.roverArchiveAutoStatus.textContent, '재생 시각 이전에 기록된 마지막 실행기 상태입니다.');
  // a recording without LiDAR frames: the lines and the car only
  const bare = rig({ lidarFrames: 0 });
  bare.context.mergeArchiveExecutorSegment({ frames: [stateAt(1)] });
  bare.render(1.5);
  assert.equal(bare.elements.roverArchiveAutoStatus.textContent, '재생 시각 이전에 기록된 마지막 실행기 상태입니다.');
});

test('after a jump in time: the windows before it one at a time, the newest first, with the states of those seconds', async () => {
  const r = rig();
  r.context.mergeArchiveExecutorSegment({ frames: statesOf(48, 72) });        // the telemetry window of the new time
  r.render(0);
  await settle();
  await r.context.loadArchiveTimelineSegment(ID, 60, 4);                      // the player loads the window of the new time
  r.log.timeline.length = 0;
  r.render(61.3);
  assert.deepEqual(r.waiting(), [350], 'the jump rests before older frames are asked for');
  r.render(61.3); r.render(61.35);
  assert.deepEqual(r.waiting(), [350], 'one wait, however many pictures are drawn meanwhile');
  const before = r.grid().view.grid.picture(61.3);
  // one request at a time: the second is not sent before the first has answered
  let open;
  r.hold(new Promise((resolve) => { open = resolve; }));
  const running = r.runTimer();
  await settle();
  assert.deepEqual(r.log.timeline, [56]);
  r.render(61.4);
  assert.deepEqual(r.waiting(), [], 'no second round while one is at work');
  open(); r.hold(null);
  await running;
  assert.deepEqual(r.log.timeline, [56, 52, 48, 44, 40, 36], 'the newest window first, back to 24 s before the time');
  assert.deepEqual(r.log.telemetry, [24], 'the states of the seconds before the telemetry window of the time, asked for once');
  const { grid } = r.grid().view;
  assert.deepEqual(grid.missing(61.3, 4), []);
  r.render(61.4);
  const after = grid.picture(61.4);
  assert.ok(after.scans > before.scans + 30 && after.cells.length > before.cells.length, `${before.scans} frames before, ${after.scans} after`);
  assert.deepEqual(r.waiting(), [], 'nothing more to ask for');
  // back to an earlier time: only frames at or before it count again
  r.render(45);
  assert.equal(grid.picture(45).scans, FRAME_TIMES.filter((t) => t >= 36 && t <= 45).length);
});

test('the asking stops when the picture is off screen, another recording is chosen, or a load fails', async () => {
  const r = rig();
  r.context.mergeArchiveExecutorSegment({ frames: statesOf(48, 72) });
  r.render(0);
  await settle();
  await r.context.loadArchiveTimelineSegment(ID, 60, 4);
  r.log.timeline.length = 0;
  const observer = r.log.observers[0];
  assert.ok(observer, 'the part is watched');
  // the size of the picture's box comes from its observer: a new size is drawn at once, and the box is not measured for every picture
  const canvas = r.elements.roverArchiveAutoCanvas;
  r.render(61.2);
  assert.deepEqual([canvas.width, canvas.height], [358, 440]);
  r.log.sizes[0].callback([{ contentRect: { width: 324, height: 440 } }]);
  assert.deepEqual([canvas.width, canvas.height], [324, 440]);
  const measured = r.log.measured;
  r.render(61.25); r.render(61.3);
  assert.equal(r.log.measured, measured);
  // off screen: no drawing and no asking, also not by a wait that began while it was still on screen
  assert.deepEqual(r.waiting(), [350]);
  observer.callback([{ isIntersecting: false }]);
  await r.runTimer();
  assert.deepEqual(r.log.timeline, []);
  const strokes = r.drawn('stroke');
  r.render(61.35);
  assert.deepEqual([r.drawn('stroke'), r.waiting()], [strokes, []]);
  // on screen again: drawn at once for the current time, and the asking starts
  observer.callback([{ isIntersecting: true }]);
  assert.ok(r.drawn('stroke') > strokes);
  assert.deepEqual(r.waiting(), [350]);
  // a load that fails: no hammering, the next try after a while
  r.fail(new Error('timeline: 503'));
  await r.runTimer();
  assert.deepEqual(r.log.timeline, [56]);
  r.render(61.5);
  assert.deepEqual(r.waiting(), [], 'not again at once');
  r.tick(5001);
  r.render(61.6);
  assert.deepEqual(r.waiting(), [350]);
  // a jump elsewhere aborts the load (scheduleArchiveSeek): that is not a failure, the next picture asks again
  const aborted = new Error('aborted'); aborted.name = 'AbortError';
  r.fail(aborted);
  await r.runTimer();
  r.render(61.7);
  assert.deepEqual(r.waiting(), [350]);
  // another recording: nothing of the old one stays, the wait is dropped
  r.fail(null);
  r.context.archiveGridReset();
  assert.deepEqual(r.waiting(), []);
  const { grid } = r.grid().view;
  assert.deepEqual([grid.scans.length, grid.windows.size, grid.frames.length], [0, 0, 0]);
  // a round that is at work when another recording is chosen asks for nothing more
  r.playback.segmentCache.clear();
  await r.context.loadArchiveTimelineSegment(ID, 60, 4);
  r.context.mergeArchiveExecutorSegment({ frames: statesOf(48, 72) });
  r.render(61.3);
  let open;
  r.hold(new Promise((resolve) => { open = resolve; }));
  const running = r.runTimer();
  await settle();
  const count = r.log.timeline.length;
  r.context.state.recordings.selectedId = 'another';
  r.context.archiveGridReset();
  open(); r.hold(null);
  await running;
  assert.equal(r.log.timeline.length, count);
  assert.deepEqual([grid.scans.length, grid.windows.size], [0, 0], 'and its answer is not kept for the new recording');
});
