// The frames of the moving picture (static/steer/local-grid.mjs with static/steer/minimap.mjs, 2026-11-20): frames only
// while a run is open, the block is on screen and the page is visible; the pose from the autonomy panel's status read
// (no request of its own; the ?grid=1 read stays at once a second); the car's arrow at the same place in every frame
// while the ground moves evenly; at most 30 pictures a second; a late sample holds the picture; after the run the last
// picture stays; reduced motion draws once per sample. Fake DOM, clock, frames and requests; no browser.
// Run: node tests/test_minimap_frames.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';

const fixture = JSON.parse(fs.readFileSync(new URL('./fixtures/executor_status_front_steer.json', import.meta.url), 'utf8'));
const GRID = fixture.local_grid_map;
const settle = async () => { for (let n = 0; n < 4; n += 1) await new Promise((resolve) => setImmediate(resolve)); };

// ---- a clock, frames and timers the test moves by hand ----
let nowMs = 10_000;
Object.defineProperty(globalThis, 'performance', { value: { now: () => nowMs }, configurable: true, writable: true });
let pendingFrame = null, frameAsks = 0;
globalThis.requestAnimationFrame = (fn) => { pendingFrame = fn; frameAsks += 1; return frameAsks; };
const timers = new Set();
globalThis.setInterval = (fn) => { timers.add(fn); return fn; };
globalThis.clearInterval = (fn) => { timers.delete(fn); };

class FakeObserver {
  static all = [];
  constructor(callback) { this.callback = callback; this.targets = []; FakeObserver.all.push(this); }
  observe(target) { this.targets.push(target); }
  report(visible) { this.callback(this.targets.map((target) => ({ target, isIntersecting: visible }))); }
}
globalThis.IntersectionObserver = FakeObserver;
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' });
let reducedMotion = false;
globalThis.matchMedia = () => ({ get matches() { return reducedMotion; } });

function recorder() {
  const calls = [];
  return new Proxy({}, {
    get(target, name) { if (name === 'calls') return calls; if (name in target) return target[name]; return (...args) => { calls.push([name, ...args]); }; },
    set(target, name, value) { target[name] = value; calls.push(['set', name, value]); return true; },
  });
}
class El {
  constructor(id = '') { this.id = id; this.hidden = false; this.textContent = ''; this.className = ''; this.width = 0; this.height = 0; this.ctx = recorder(); }
  getBoundingClientRect() { return this.id === 'localGridCanvas' ? { width: 358, height: 440 } : { width: 0, height: 0 }; }
  getContext() { return this.ctx; }
}
function world() {
  const registry = new Map();
  const byId = (id) => registry.get(id) || registry.set(id, new El(id)).get(id);
  const listeners = {};
  globalThis.document = { hidden: false, getElementById: byId, createElement: () => new El(), documentElement: {},
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); }, fire(type) { (listeners[type] || []).forEach((fn) => fn()); } };
  globalThis.window = { devicePixelRatio: 1 };
  for (const id of ['localGridPanel', 'localGridCanvas', 'autoGridBlock', 'localGridState', 'localGridStatus']) byId(id);
  byId('localGridPanel').hidden = true;
  FakeObserver.all = [];
  timers.clear(); pendingFrame = null;
  return byId;
}

// The car goes along the fitted row at 0.4 m/s from the grid's own base pose.
const AXIS = Math.atan2(GRID.lines.centre[1][1] - GRID.lines.centre[0][1], GRID.lines.centre[1][0] - GRID.lines.centre[0][0]);
const SPEED = 0.4;
const poseAtMs = (ms) => [GRID.base_pose[0] + Math.cos(AXIS) * SPEED * ms / 1000, GRID.base_pose[1] + Math.sin(AXIS) * SPEED * ms / 1000, AXIS];

function rig({ shared = true } = {}) {
  const byId = world();
  const state = { runState: 'running', t0: nowMs, calls: [], shared: null, sharedEvery: 500 };
  const sample = () => poseAtMs(nowMs - state.t0).map((v) => Math.round(v * 10000) / 10000);
  const page = {
    currentRoute: () => '/rover-control',
    getJson: async (url) => {
      state.calls.push(url);
      const pose = sample();
      return { ok: true, configured: true, status: { run_state: state.runState, run: 7, pose, local_grid_map: GRID,
        single_row_geometry: { base: pose, path: [[pose[0] - 1, pose[1]], [pose[0], pose[1]]] } } };
    },
  };
  if (shared) page.executorStatus = () => state.shared;
  return { byId, state, page, sample };
}
// Moves the clock by `ms` in frames of `frameMs`; the autonomy panel's read every 500 ms, the grid read every second.
async function run(r, screen, ms, frameMs = 1000 / 60, { samples = true } = {}) {
  const { state } = r;
  const end = nowMs + ms;
  while (nowMs < end - 1e-6) {
    nowMs += frameMs;
    const since = nowMs - state.t0;
    if (samples && r.page.executorStatus && Math.floor(since / state.sharedEvery) !== Math.floor((since - frameMs) / state.sharedEvery)) {
      state.shared = { reply: { ok: true, status: { run_state: state.runState, pose: r.sample() } }, at: nowMs };
    }
    if (Math.floor(since / 1000) !== Math.floor((since - frameMs) / 1000)) { for (const fn of [...timers]) fn(); await settle(); }
    const fn = pendingFrame; pendingFrame = null;
    if (fn) fn(nowMs);
  }
}
// What the canvas got: one entry per picture { f (the cells' shift down the screen), arrow (the polygon of the car) }.
function pictures(canvas, from = 0) {
  const calls = canvas.ctx.calls.slice(from), out = [];
  let current = null;
  for (let n = 0; n < calls.length; n += 1) {
    const call = calls[n];
    if (call[0] === 'clearRect') { current = { f: null, e: null, arrow: [], poly: null }; out.push(current); continue; }
    if (!current) continue;
    if (call[0] === 'transform') { current.e = call[5]; current.f = call[6]; }
    if (call[0] === 'closePath') current.arrow = current.poly;          // only the car's arrow is a closed path
    if (call[0] === 'beginPath') current.poly = [];
    if ((call[0] === 'moveTo' || call[0] === 'lineTo') && current.poly) current.poly.push([call[1], call[2]]);
  }
  return out;
}

const { initLocalGrid, POLL_MS } = await import('../static/steer/local-grid.mjs');
const { viewBox, FRAME_MS } = await import('../static/steer/minimap.mjs');
const BOX = viewBox(358, 440);

// ---- a running run with the autonomy panel's reads ----
{
  const r = rig();
  const screen = initLocalGrid(r.page);
  await settle();
  assert.deepEqual(r.state.calls, [], 'nothing is asked before the block is on screen');
  assert.equal(pendingFrame, null, 'and no frame');
  FakeObserver.all[0].report(true); await settle();
  assert.deepEqual(r.state.calls, ['/api/executor/status?grid=1'], 'on screen: the grid read');
  assert.equal(r.byId('localGridPanel').hidden, false);
  assert.ok(pendingFrame, 'a run is open: the frames start');
  const canvas = r.byId('localGridCanvas');
  assert.deepEqual([canvas.width, canvas.height], [358, 440], 'the buffer is the size on screen');

  await run(r, screen, 2000);                         // warm up: the glide settles
  const mark = canvas.ctx.calls.length, asked = r.state.calls.length, t0 = nowMs;
  await run(r, screen, 4000);
  const shots = pictures(canvas, mark);
  assert.equal(r.state.calls.length - asked, 4000 / POLL_MS, 'four seconds: four grid reads and no other request');
  assert.ok(r.state.calls.every((url) => url === '/api/executor/status?grid=1'));
  const wanted = 4000 / FRAME_MS;
  assert.ok(shots.length <= wanted + 1 && shots.length >= wanted * 0.9, `at most 30 pictures a second at 60 frames a second (${shots.length} in 4 s)`);
  // the car: the same four points in every picture, at the fixed place
  const arrow = shots[0].arrow;
  assert.equal(arrow.length, 4);
  assert.ok(shots.every((shot) => JSON.stringify(shot.arrow) === JSON.stringify(arrow)), 'the arrow is the same polygon in every picture');
  assert.ok(Math.abs(arrow[0][0] - BOX.cx) < 0.01 && arrow[0][1] < BOX.cy && arrow[0][1] > BOX.cy - 17, 'its tip straight up from (cx, cy)');
  // the ground: down the screen by the same amount in every picture, the true speed
  const steps = shots.slice(1).map((shot, n) => shot.f - shots[n].f);
  const perPicture = SPEED * (2 * 1000 / 60) / 1000 * BOX.scale;
  assert.ok(steps.every((d) => d > 0), 'the ground never stands still or goes back between two pictures');
  assert.ok(steps.every((d) => Math.abs(d - perPicture) < perPicture * 0.05), `evenly: ${perPicture.toFixed(3)} px a picture (min ${Math.min(...steps).toFixed(3)}, max ${Math.max(...steps).toFixed(3)})`);
  assert.ok(shots.every((shot) => Math.abs(shot.e - shots[0].e) < 0.05), 'and not sideways');
  const total = shots[shots.length - 1].f - shots[0].f;
  assert.ok(Math.abs(total - SPEED * (nowMs - t0 - 2 * 1000 / 60) / 1000 * BOX.scale) < 1.5, 'over four seconds the distance the car went');
  const view = screen.view();
  const truth = poseAtMs(nowMs - r.state.t0);
  const behind = (truth[0] - view.pose[0]) * Math.cos(AXIS) + (truth[1] - view.pose[1]) * Math.sin(AXIS);
  assert.ok(behind > 0 && behind < SPEED * 1.3, `the picture is behind the car by less than about a second (${behind.toFixed(3)} m), never ahead`);

  // a late sample: the picture waits at the last one, nothing is extrapolated
  const held = canvas.ctx.calls.length;
  r.page.getJson = async (url) => { r.state.calls.push(url); throw new Error('offline'); };
  await run(r, screen, 700, 1000 / 60, { samples: false });
  const coast = pictures(canvas, held);
  const before = screen.view().pose;
  const drawn = canvas.ctx.calls.length;
  await run(r, screen, 1500, 1000 / 60, { samples: false });
  assert.equal(canvas.ctx.calls.length, drawn, 'no new picture while no sample comes');
  assert.deepEqual(screen.view().pose, before, 'the car waits at the last sample');
  assert.ok(coast.length > 0 && coast.every((shot) => JSON.stringify(shot.arrow) === JSON.stringify(arrow)));
  assert.ok(screen.view().frames, 'the frames go on (they draw nothing) while the run is open');

  // hidden page: the frames stop; visible again: they start again
  document.hidden = true; document.fire('visibilitychange');
  await run(r, screen, 200, 1000 / 60, { samples: false });
  assert.equal(pendingFrame, null, 'a hidden page asks for no frame');
  assert.equal(screen.view().frames, false);
  document.hidden = false; document.fire('visibilitychange');
  assert.ok(pendingFrame, 'visible again: the frames start');
  // off screen (another tab of the page, scrolled away): the same
  FakeObserver.all[0].report(false);
  await run(r, screen, 200, 1000 / 60, { samples: false });
  assert.equal(pendingFrame, null, 'off screen: no frame');
  FakeObserver.all[0].report(true); await settle();
  assert.ok(pendingFrame);

  // the run ends: the car goes to its last sample, the frames stop, the picture stays
  r.state.runState = 'latched';
  r.state.shared = { reply: { ok: true, status: { run_state: 'latched', pose: r.sample() } }, at: nowMs };
  const last = screen.view().pose;
  await run(r, screen, 100, 1000 / 60, { samples: false });
  assert.equal(pendingFrame, null, 'after the run no frame is asked for');
  assert.equal(screen.view().open, false);
  assert.deepEqual(screen.view().pose, last, 'the picture of the run stays as it was');
  assert.equal(r.byId('localGridPanel').hidden, false);
}

// ---- without the autonomy panel: the grid read's own pose, once a second, the same glide ----
{
  const r = rig({ shared: false });
  const screen = initLocalGrid(r.page);
  FakeObserver.all[0].report(true); await settle();
  const canvas = r.byId('localGridCanvas');
  await run(r, screen, 4000);
  const mark = canvas.ctx.calls.length;
  await run(r, screen, 3000);
  const shots = pictures(canvas, mark);
  const steps = shots.slice(1).map((shot, n) => shot.f - shots[n].f);
  assert.ok(shots.length > 80 && steps.every((d) => d > 0), 'the ground moves in every picture with samples a second apart');
  assert.ok(r.state.calls.every((url) => url === '/api/executor/status?grid=1') && r.state.calls.length <= 9, 'still one read a second');
  // an idle executor: one picture, no frames
  const idle = rig({ shared: false });
  idle.state.runState = 'idle';
  const still = initLocalGrid(idle.page);
  FakeObserver.all[0].report(true); await settle();
  assert.equal(pendingFrame, null, 'no run is open: no frame');
  assert.equal(pictures(idle.byId('localGridCanvas')).length, 1, 'the picture is drawn once');
  assert.equal(still.view().open, false);
  await run(idle, still, 2100);
  assert.equal(pictures(idle.byId('localGridCanvas')).length, 1, 'and kept');
  assert.equal(idle.byId('localGridState').textContent, '멈춤');
}

// ---- reduced motion: one picture per sample, no glide ----
{
  reducedMotion = true;
  const r = rig();
  const screen = initLocalGrid(r.page);
  FakeObserver.all[0].report(true); await settle();
  const canvas = r.byId('localGridCanvas');
  await run(r, screen, 1000);
  const mark = canvas.ctx.calls.length;
  await run(r, screen, 3000);
  const shots = pictures(canvas, mark);
  // six samples of the autonomy panel and three grid reads (the lines and the path of the same pose may be new)
  assert.ok(shots.length >= 6 && shots.length <= 9, `one picture per sample or grid read (${shots.length} in 3 s)`);
  const steps = shots.slice(1).map((shot, n) => shot.f - shots[n].f).filter((d) => d > 1e-6);
  assert.ok(steps.length >= 5 && steps.every((d) => Math.abs(d - SPEED * 0.5 * BOX.scale) < 0.4), 'the ground jumps one sample at a time');
  reducedMotion = false;
}

console.log('PASS: minimap frames only while a run is open and shown, the car fixed and the ground even, late sample held, reduced motion');
