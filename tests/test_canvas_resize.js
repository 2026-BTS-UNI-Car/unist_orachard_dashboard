// Canvas boxes: charts, LiDAR and IMU canvases follow their own box (ResizeObserver),
// and the LiDAR waiting text keeps clear of the origin mark and the ring labels.
// Fake DOM only: no browser, no sensors.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource().replace(/\r\n/g, '\n');
const between = (begin, end) => { const a = source.indexOf(begin), b = source.indexOf(end, a); assert.ok(a >= 0 && b > a, begin); return source.slice(a, b); };
const resizeCode = between('const canvasResize = {', 'function drawGrid(');

function fixture({observer = true, ids = ['chartCpu', 'lidarScanCanvas', 'roverLidarCanvas', 'roverArchiveLidarCanvas', 'roverImuCanvas', 'roverArchiveImuCanvas']} = {}) {
  const classOf = id => id.includes('Imu') ? 'imu-attitude-canvas' : id.includes('Lidar') ? 'lidar-map' : 'resource-chart';
  const canvases = new Map(ids.map(id => [id, {id, classList: {contains: name => name === classOf(id)}}]));
  const calls = {chart: [], frames: [], prepared: [], archiveFrames: 0, imu: [], selectors: []};
  const observers = [];
  class FakeResizeObserver {
    constructor(callback) { this.callback = callback; this.targets = []; observers.push(this); }
    observe(target) { this.targets.push(target); }
  }
  const state = {archivePlayback: {lidarPainted: false, lastLidarRenderKey: 'frame-7:25'}, recordings: {timeline: null}};
  const context = {
    state, $: id => canvases.get(id) || null,
    document: {querySelectorAll: selector => { calls.selectors.push(selector); return [...canvases.values()]; }},
    requestChartAnimation: options => calls.chart.push(options.redrawLidar),
    requestAnimationFrame: callback => { calls.frames.push(callback); return calls.frames.length; },
    prepareCanvas: canvas => { calls.prepared.push(canvas.id); return null; },
    renderArchiveFrame: () => { calls.archiveFrames += 1; calls.keyAtRender = state.archivePlayback.lastLidarRenderKey; },
    imuDisplaySample: () => ({euler_deg: {roll: 1.5, pitch: -2, yaw: 30}}),
    drawImuAttitude: (canvas, roll, pitch, yaw) => calls.imu.push([canvas.id, roll, pitch, yaw]),
  };
  if (observer) context.ResizeObserver = FakeResizeObserver;
  vm.createContext(context);
  vm.runInContext(resizeCode, context);
  const entry = (id, width = 300, height = 200) => ({target: canvases.get(id), contentRect: {width, height}});
  const frame = () => { const callback = calls.frames.shift(); assert.ok(callback, 'a frame should be queued'); callback(0); };
  return {context, state, canvases, calls, observers, entry, frame};
}

// Every chart, LiDAR and IMU canvas of the page is observed.
{
  const f = fixture();
  f.context.initCanvasResize();
  assert.equal(f.observers.length, 1);
  assert.deepEqual(f.observers[0].targets.map(canvas => canvas.id), [...f.canvases.keys()]);
  for (const name of ['canvas.resource-chart', 'canvas.lidar-map', 'canvas.imu-attitude-canvas']) assert.ok(f.calls.selectors[0].includes(name), name);
  // Canvases created later (per-core charts) are added through the same observer.
  const late = {id: '', classList: {contains: name => name === 'resource-chart'}};
  f.context.observeCanvasResize(late); f.context.observeCanvasResize(null);
  assert.equal(f.observers[0].targets.at(-1), late);
}
// A browser without ResizeObserver keeps the old behaviour (window resize, redraw on data).
{
  const f = fixture({observer: false});
  f.context.initCanvasResize(); f.context.observeCanvasResize(f.canvases.get('chartCpu'));
  assert.equal(f.observers.length, 0); assert.equal(f.calls.chart.length, 0);
}
// Hidden pages and closed panels report an empty box: nothing is drawn or scheduled.
{
  const f = fixture(); f.context.initCanvasResize();
  f.context.handleCanvasResize([f.entry('chartCpu', 0, 0), f.entry('roverLidarCanvas', 300, 0), f.entry('roverImuCanvas', 0, 220), f.entry('roverArchiveLidarCanvas', 0, 0)]);
  assert.equal(f.calls.chart.length, 0); assert.equal(f.calls.frames.length, 0);
}
// Charts and the live LiDAR canvases use the existing chart redraw request.
{
  const f = fixture(); f.context.initCanvasResize();
  f.context.handleCanvasResize([f.entry('chartCpu')]);
  assert.deepEqual(f.calls.chart, [false], 'a chart alone does not repaint the LiDAR scan');
  f.context.handleCanvasResize([f.entry('chartCpu'), f.entry('roverLidarCanvas'), f.entry('lidarScanCanvas')]);
  assert.equal(f.calls.chart.at(-1), true, 'a LiDAR canvas in the batch repaints the scan');
  assert.equal(f.calls.chart.length, 2, 'one request per batch'); assert.equal(f.calls.frames.length, 0);
}
// The live IMU canvas is repainted from the last sample, once per frame.
{
  const f = fixture(); f.context.initCanvasResize();
  f.context.handleCanvasResize([f.entry('roverImuCanvas')]); f.context.handleCanvasResize([f.entry('roverImuCanvas', 320, 220)]);
  assert.equal(f.calls.frames.length, 1, 'coalesced into one frame'); assert.equal(f.calls.imu.length, 0);
  f.frame();
  assert.deepEqual(f.calls.imu, [['roverImuCanvas', 1.5, -2, 30]]);
  assert.equal(f.calls.archiveFrames, 0); assert.equal(f.calls.chart.length, 0);
}
// Archive LiDAR canvas that was never painted: stays blank, only its buffer follows the box.
{
  const f = fixture(); f.context.initCanvasResize();
  f.context.handleCanvasResize([f.entry('roverArchiveLidarCanvas'), f.entry('roverArchiveImuCanvas')]); f.frame();
  assert.deepEqual(f.calls.prepared, ['roverArchiveLidarCanvas']);
  assert.equal(f.calls.archiveFrames, 0, 'no recording timeline: the archive frame is not rendered');
  assert.equal(f.state.archivePlayback.lastLidarRenderKey, 'frame-7:25');
}
// Painted archive LiDAR canvas: the current frame is repainted through the normal render path, once.
{
  const f = fixture(); f.context.initCanvasResize();
  f.state.archivePlayback.lidarPainted = true; f.state.recordings.timeline = {};
  f.context.handleCanvasResize([f.entry('roverArchiveLidarCanvas'), f.entry('roverArchiveImuCanvas')]); f.frame();
  assert.equal(f.calls.archiveFrames, 1); assert.equal(f.calls.keyAtRender, '', 'the frame key is cleared before rendering');
  assert.deepEqual(f.calls.prepared, []);
  // The archive IMU canvas alone repaints without forcing the LiDAR frame.
  f.state.archivePlayback.lastLidarRenderKey = 'frame-8:25';
  f.context.handleCanvasResize([f.entry('roverArchiveImuCanvas')]); f.frame();
  assert.equal(f.calls.archiveFrames, 2); assert.equal(f.calls.keyAtRender, 'frame-8:25');
}

// Wiring in app.js: started before the first route render, per-core charts registered,
// the real window resize listener kept, no synthetic resize event left.
assert.ok(source.indexOf('\ninitCanvasResize();\n') > 0 && source.indexOf('\ninitCanvasResize();\n') < source.indexOf('\nrenderRoute();\n'));
assert.match(between('function createCoreChart(', 'function updateCoreChart('), /observeCanvasResize\(article\.querySelector\("canvas"\)\);/);
assert.match(between('function initMobileNav()', 'function setMobileNav('), /window\.addEventListener\("resize"/);
assert.doesNotMatch(source, /dispatchEvent\(new Event\(['"]resize['"]\)\)/);

// drawLidarMapOnCanvas reports whether it painted; the waiting text goes to the top-left corner.
{
  const texts = [];
  const ctx = new Proxy({}, {get: (target, key) => {
    if (key === 'fillText') return (label, x, y) => texts.push({label, x, y, align: target.textAlign});
    if (key === 'measureText') return label => ({width: String(label).length * 10});
    if (key === 'createRadialGradient') return () => ({addColorStop() {}});
    return key in target ? target[key] : () => {};
  }, set: (target, key, value) => { target[key] = value; return true; }});
  let prepared = null;
  const context = {prepareCanvas: () => prepared, chartColors: () => ({cyan: '#0ff'}), cssVar: (_, fallback) => fallback,
    clampLidarRange: value => value, lidarDrawRange: () => 25, lidarGridStep: () => 5, drawLidarGrid() {}, fmtNumber: String,
    angleCoverage: () => 360, Math, Number, String};
  vm.createContext(context);
  vm.runInContext(between('function drawLidarMapOnCanvas(', 'function lidarRangeFor(') + between('function drawLidarEmpty(', 'function escapeHtml('), context);
  assert.equal(context.drawLidarMapOnCanvas(null, null), false);
  assert.equal(context.drawLidarMapOnCanvas({}, null), false, 'empty box: not painted');
  prepared = {ctx, w: 330, h: 330};
  assert.equal(context.drawLidarMapOnCanvas({}, null), true);
  const waiting = texts.at(-1);
  assert.equal(waiting.label, '스캔 대기'); assert.equal(waiting.align, 'left');
  assert.ok(waiting.x < 330 / 4 && waiting.y < 330 / 4, 'top-left corner, away from the origin mark and ring labels');
  texts.length = 0;
  assert.equal(context.drawLidarMapOnCanvas({}, {ok: false, message: 'ROS2 /scan topic is not available on this bridge yet'}), true);
  const long = texts.at(-1).label;
  assert.ok(long.endsWith('…') && long.length < 30, 'a long message is cut');
  assert.ok(texts.at(-1).x + long.length * 10 <= 330 / 2 - 24, 'the text ends before the 180° label at the top centre');
  texts.length = 0;
  assert.equal(context.drawLidarMapOnCanvas({}, {ok: true, points: [{x_m: 1, y_m: 1, quality: 10}], metrics: {coverage_deg: 1}}), true);
  assert.ok(texts.some(item => /1 points/.test(item.label)));
}
console.log('PASS: canvas boxes observed (charts, LiDAR, IMU, per-core), empty boxes skipped, archive canvas blank/painted paths, no synthetic resize, LiDAR waiting text placement');
