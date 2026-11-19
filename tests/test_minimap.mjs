// The moving picture of the monitoring view (static/steer/minimap.mjs, 2026-11-20): the car at one fixed place of the
// canvas, the row axis up, one fixed scale, the pose glide between samples (monotone, the yaw the short way round, held
// at a late sample), the eased axis, the cells' transform, the tail, the centre line and the lookahead point. Pure
// functions and a recording 2D context; no browser. Run: node --test tests/test_minimap.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import {
  AHEAD_M, AXIS_TAU_S, BEHIND_M, FRAME_MS, GLIDE_STRETCH, HALF_M, JUMP_M, SCALE_BAR_M, TRAIL_M, drawMinimap, easeAxis, follow, fromCar,
  gridTransform, lookahead, poseAt, rowAxis, spanLine, tail, toScreen, viewBox, wrap,
} from '../static/steer/minimap.mjs';
import { gridCells } from '../static/steer/local-grid.mjs';
import { sceneFromStatus } from '../static/steer/run-view.mjs';

const near = (a, b, eps = 1e-9) => Math.abs(a - b) <= eps;
const fixture = JSON.parse(fs.readFileSync(new URL('./fixtures/executor_status_front_steer.json', import.meta.url), 'utf8'));

test('the frame is fixed: the car in the middle, 5/17 above the lower edge, one scale that the data never changes', () => {
  for (const [w, h] of [[358, 440], [480, 440], [720, 440]]) {
    const box = viewBox(w, h);
    assert.equal(box.cx, w / 2);
    assert.ok(near(box.cy, h * AHEAD_M / (AHEAD_M + BEHIND_M)), 'the place of the car depends on the canvas only');
    assert.ok(near((h - box.cy) / h, BEHIND_M / (AHEAD_M + BEHIND_M)) && (h - box.cy) / h > 0.27 && (h - box.cy) / h < 0.32, 'about 30 % from the bottom');
    assert.ok(box.cy / box.scale >= AHEAD_M - 1e-9, `${AHEAD_M} m ahead are in the picture`);
    assert.ok((h - box.cy) / box.scale >= BEHIND_M - 1e-9, `${BEHIND_M} m behind`);
    assert.ok(box.cx / box.scale >= HALF_M - 1e-9, `${HALF_M} m to each side`);
  }
  assert.ok(viewBox(358, 440).scale > 20, 'a phone width: more than 20 pixels a metre (a 0.1 m cell is 2 pixels)');
  assert.equal(viewBox(480, 440).scale, viewBox(720, 440).scale, 'a wider canvas shows more to the sides, the scale stays');
});

test('the car lands on its fixed place whatever its pose and the axis; ahead is up, the left of the row is left', () => {
  const box = viewBox(480, 440);
  for (const pose of [[0, 0, 0], [8.36, 1.48, 0.15], [-3, 40, 2.9], [120.5, -7.25, -3.1]]) {
    for (const axis of [0, 0.166, -1.2, 3.1]) {
      const [sx, sy] = toScreen(box, pose, axis, pose[0], pose[1]);
      assert.ok(near(sx, box.cx, 1e-6) && near(sy, box.cy, 1e-6), 'the car is at (cx, cy)');
      const ahead = toScreen(box, pose, axis, pose[0] + Math.cos(axis), pose[1] + Math.sin(axis));
      assert.ok(near(ahead[0], box.cx, 1e-6) && near(ahead[1], box.cy - box.scale, 1e-6), '1 m along the axis is 1 m up');
      const left = toScreen(box, pose, axis, pose[0] - Math.sin(axis), pose[1] + Math.cos(axis));
      assert.ok(near(left[0], box.cx - box.scale, 1e-6) && near(left[1], box.cy, 1e-6), '1 m to the left of the axis is 1 m left');
    }
  }
});

test('the glide: the first sample at once, then from the pose drawn now to the new sample, held there', () => {
  let glide = follow(null, [0, 0, 0], 1000);
  assert.deepEqual(poseAt(glide, 1000), [0, 0, 0], 'the first sample is shown at once');
  assert.deepEqual(poseAt(glide, 5000), [0, 0, 0]);
  glide = follow(glide, [0.2, 0, 0], 1500);
  assert.equal(glide.dur, 500 * GLIDE_STRETCH, 'as long as the samples were apart, stretched');
  let last = -1;
  for (let t = 1500; t <= 1500 + glide.dur; t += 10) {
    const x = poseAt(glide, t)[0];
    assert.ok(x >= last && x <= 0.2 + 1e-12, 'between two samples the value goes on monotonically and never past the sample');
    last = x;
  }
  assert.ok(near(poseAt(glide, 1500 + glide.dur / 2)[0], 0.1), 'half way at half the time');
  // a late sample: the car waits at the last one (nothing is extrapolated)
  for (const t of [1500 + glide.dur, 2500, 9000]) assert.deepEqual(poseAt(glide, t), [0.2, 0, 0]);
  // the next sample continues from what is drawn at that moment
  const next = follow(glide, [0.4, 0.02, 0.01], 1790);
  assert.deepEqual(poseAt(next, 1790), poseAt(glide, 1790), 'no step when a sample arrives');
  assert.equal(next.dur, (1790 - 1500) * GLIDE_STRETCH, 'the interval is measured between the arrivals, not between the frames');
  assert.ok(poseAt(next, 1800)[0] > poseAt(next, 1790)[0] && poseAt(next, 1800)[0] - poseAt(glide, 1790)[0] < 0.01, 'a frame 10 ms later is on the new glide');
});

test('the glide: an even flow at regular samples and no halt for a sample that is a little late', () => {
  const frames = (arrivals, step = 0.2) => {
    let glide = null, k = 0;
    const xs = [];
    for (let t = 0; t <= arrivals[arrivals.length - 1] + 400; t += FRAME_MS) {
      while (k < arrivals.length && arrivals[k] <= t) { glide = follow(glide, [step * k, 0, 0], arrivals[k]); k += 1; }
      if (glide) xs.push([t, poseAt(glide, t)[0], step * (k - 1)]);
    }
    return xs;
  };
  const regular = frames(Array.from({ length: 16 }, (_, n) => n * 500));
  const steady = regular.filter(([t]) => t > 4000 && t < 7400);
  const steps = steady.slice(1).map(([, x], n) => x - steady[n][1]);
  const perFrame = 0.2 * FRAME_MS / 500;
  assert.ok(steps.every((d) => Math.abs(d - perFrame) < perFrame * 0.001), 'every frame moves the ground by the same amount (the true speed)');
  assert.ok(regular.every(([, x, newest]) => x <= newest + 1e-9), 'never ahead of the newest sample');
  const lag = steady.map(([, x, newest]) => newest - x);
  assert.ok(Math.max(...lag) <= 0.2 * GLIDE_STRETCH + 1e-6, 'about one sample behind at most');
  // every fourth sample 90 ms late (less than the stretch covers): the ground never stands still in between
  const late = frames(Array.from({ length: 16 }, (_, n) => n * 500 + (n % 4 === 3 ? 90 : 0)));
  const moving = late.filter(([t]) => t > 2000 && t < 7400);
  assert.ok(moving.slice(1).every(([, x], n) => x - moving[n][1] > 0), 'a sample 90 ms late does not halt the picture');
});

test('the glide: the yaw goes the short way round, a jump and reduced motion do not glide', () => {
  let glide = follow(null, [0, 0, 3.1], 0);
  glide = follow(glide, [0.1, 0, -3.1], 500);
  const mid = poseAt(glide, 500 + glide.dur / 2)[2];
  assert.ok(Math.abs(Math.abs(mid) - Math.PI) < 0.05, `through pi, not through 0 (${mid})`);
  let turn = -1;
  for (let t = 500; t <= 500 + glide.dur; t += 20) {
    const done = wrap(poseAt(glide, t)[2] - 3.1);
    assert.ok(done >= turn - 1e-12 && done <= wrap(-3.1 - 3.1) + 1e-9, 'the turn is monotone and 0.083 rad at most');
    turn = done;
  }
  assert.ok(near(wrap(Math.PI), -Math.PI) && near(wrap(-Math.PI), -Math.PI) && near(wrap(7), 7 - 2 * Math.PI) && near(wrap(0.3), 0.3));
  // a new run: the odometry starts again somewhere else
  const jump = follow(glide, [0.1 + JUMP_M + 0.5, 0, 0], 1000);
  assert.deepEqual(poseAt(jump, 1000), [0.1 + JUMP_M + 0.5, 0, 0], 'a jump is shown at once');
  // reduced motion: one picture per sample
  const calm = follow(glide, [0.3, 0, -3.1], 1000, false);
  assert.deepEqual(poseAt(calm, 1000), [0.3, 0, -3.1]);
  assert.equal(poseAt(null, 0), null);
});

test('the axis: the fitted centre line, else the band, else the heading; eased the short way round', () => {
  const base = [8.36, 1.48, 0.15];
  const centre = [[4.4, 0.92], [18.2, 3.23]], reference = [[6.4, 1.2], [16.2, 2.83]];
  const fitted = Math.atan2(3.23 - 0.92, 18.2 - 4.4);
  assert.ok(near(rowAxis({ pose: base, centre, reference }), fitted), 'the grid fit comes first');
  assert.ok(near(rowAxis({ pose: base, centre: [centre[1], centre[0]], reference }), fitted), 'a line given backwards points the way the car goes');
  assert.ok(near(rowAxis({ pose: base, centre: null, reference }), Math.atan2(2.83 - 1.2, 16.2 - 6.4)), 'then the estimator band');
  assert.equal(rowAxis({ pose: base, centre: null, reference: null }), 0.15, 'then the heading of the car');
  assert.equal(rowAxis({ pose: null }), null);
  assert.equal(easeAxis(null, 0.3, 33), 0.3, 'the first axis is taken at once');
  const one = easeAxis(0, 0.2, AXIS_TAU_S * 1000);
  assert.ok(near(one, 0.2 * (1 - Math.exp(-1)), 1e-9), '63 % of the way after one time constant');
  let axis = 0;
  for (let n = 0; n < 15; n += 1) { const next = easeAxis(axis, 0.2, FRAME_MS); assert.ok(next > axis && next < 0.2); axis = next; }
  assert.ok(axis > 0.2 * 0.6 && axis < 0.2 * 0.66, 'half a second of frames: the same 63 %');
  assert.ok(easeAxis(3.1, -3.1, 100) > 3.1 || easeAxis(3.1, -3.1, 100) < -3.1, 'across pi the short way');
  assert.ok(near(easeAxis(0.1, 0.1, 33), 0.1, 1e-12), 'at the target it stays');
});

test('the cells: the picture of the grid lands where its cells are in the odometry', () => {
  const grid = fixture.local_grid_map;
  const cells = gridCells(grid);
  const box = viewBox(480, 440), pose = grid.base_pose, axis = rowAxis(sceneFromStatus({ local_grid_map: grid }));
  const c = Math.cos(grid.axis_rad), s = Math.sin(grid.axis_rad);
  for (const k of [1, 4]) {
    const [a, b, cc, d, e, f] = gridTransform(box, pose, axis, grid, k);
    for (const [i, j] of [cells[0], cells[cells.length - 1], [0, 0], [grid.shape[0], grid.shape[1]]]) {
      const u = grid.origin_uv[0] + i * grid.cell_m, v = grid.origin_uv[1] + j * grid.cell_m;
      const want = toScreen(box, pose, axis, u * c - v * s, u * s + v * c);
      const got = [a * i * k + cc * j * k + e, b * i * k + d * j * k + f];
      assert.ok(near(got[0], want[0], 1e-6) && near(got[1], want[1], 1e-6), `cell corner ${i}, ${j} at ${k} px a cell`);
    }
    assert.ok(near(Math.hypot(a, b) * k, grid.cell_m * box.scale, 1e-9), 'a cell is cell_m * scale pixels');
  }
  assert.equal(gridTransform(box, pose, axis, { ...grid, origin_uv: undefined }), null);
  // the two fitted rows are left and right of the car, the fit runs up the picture
  const scene = sceneFromStatus({ local_grid_map: grid });
  const [la, lb] = scene.rows.left.map(([x, y]) => toScreen(box, pose, axis, x, y));
  const [ra] = scene.rows.right.map(([x, y]) => toScreen(box, pose, axis, x, y));
  assert.ok(la[0] < box.cx && ra[0] > box.cx, 'the left row is left of the car, the right row right');
  assert.ok(lb[1] < la[1] && Math.abs(lb[0] - la[0]) < 1, 'the rows run straight up');
});

test('the tail: from the car backwards, the last 15 m, nothing in front of the car', () => {
  const path = Array.from({ length: 120 }, (_, n) => [n * 0.2, 0.01 * Math.sin(n / 7)]);      // 0 .. 23.8 m
  const pose = [23.5, 0, 0];                                                                    // drawn a little behind the path's end
  const out = tail(path, pose);
  assert.deepEqual(out[0], [23.5, 0, 0], 'the tail starts at the car');
  assert.ok(out.slice(1).every(([x]) => x <= 23.5), 'the two path points the drawn car has not reached are left out');
  assert.ok(out.slice(1).every((p, n) => p[2] > out[n][2]), 'the distance along the tail grows');
  assert.ok(near(out[out.length - 1][2], TRAIL_M, 1e-9), `cut at ${TRAIL_M} m`);
  assert.ok(out[out.length - 1][0] > 23.5 - TRAIL_M - 0.01 && out[out.length - 1][0] < 23.5 - TRAIL_M + 0.2);
  assert.deepEqual(tail([], pose), [[23.5, 0, 0]]);
  assert.deepEqual(tail([[0, 0], [1, 0]], [1.5, 0, 0]).map((p) => p[2]), [0, 0.5, 1.5], 'a short path is shown whole');
});

test('the centre line: over the whole picture, left of the car when the car is right of it', () => {
  const box = viewBox(480, 440);
  const reference = [[0, 0.3], [10, 0.3]];                 // the band's centre line, 0.3 m to the left of the car's track
  const pose = [5, 0, 0];
  const line = spanLine(reference, pose);
  const [a, b] = line.map(([x, y]) => toScreen(box, pose, 0, x, y));
  assert.ok(a[1] > box.h && b[1] < 0, 'from below the lower edge to above the upper one');
  assert.ok(near(a[0], box.cx - 0.3 * box.scale, 1e-6) && near(b[0], a[0], 1e-6), 'a lateral error of 0.3 m to the right: the line 0.3 m left of the car');
  const other = spanLine([reference[1], reference[0]], pose).map(([x, y]) => toScreen(box, pose, 0, x, y));
  assert.ok(near(other[0][1], a[1], 1e-6) && near(other[1][1], b[1], 1e-6), 'whichever way the two points are given');
  assert.equal(spanLine(null, pose), null);
  assert.equal(spanLine([[1, 1], [1, 1]], pose), null);
});

test('the lookahead point: the angle of the status at the distance of the controller rule', () => {
  const tuning = { values: { basic: { lookahead_base_m: 1.5, lookahead_per_speed_s: 1.25, lookahead_max_m: 3.0 }, advanced: { min_speed_m_s: 0.2 } } };
  const status = { run_state: 'running', single_row: { lookahead_error_deg: 3.07, speed_m_s: 0.4 }, tuning };
  const [ahead, left] = lookahead(status);
  assert.ok(near(ahead, 2.0) && near(left, 2.0 * Math.tan(3.07 * Math.PI / 180)), 'base + per_speed * speed, to the left for a positive angle');
  assert.ok(near(lookahead({ ...status, single_row: { lookahead_error_deg: -2, speed_m_s: 0.05 } })[0], 1.75), 'below the minimum speed the minimum counts');
  assert.ok(near(lookahead({ ...status, single_row: { lookahead_error_deg: 0, speed_m_s: 2 } })[0], 3.0), 'never beyond the maximum');
  for (const bad of [{ ...status, run_state: 'paused' }, { ...status, single_row: { speed_m_s: 0.4 } }, { ...status, tuning: {} }, { run_state: 'running' }, null]) {
    assert.equal(lookahead(bad), null, 'nothing is drawn without the values or while the run does not drive');
  }
  const [x, y] = fromCar([1, 2, Math.PI / 2], 2, 0.5);
  assert.ok(near(x, 0.5) && near(y, 4), 'the point in the odometry: ahead along the heading, left of it');
});

// A 2D context that records what is drawn.
function recorder() {
  const calls = [];
  const state = { matrix: null };
  const ctx = new Proxy({}, {
    get(target, name) {
      if (name === 'calls') return calls;
      if (name in target) return target[name];
      return (...args) => { calls.push([name, ...args]); if (name === 'transform') state.matrix = args; };
    },
    set(target, name, value) { target[name] = value; calls.push(['set', name, value]); return true; },
  });
  return ctx;
}
const COLOURS = { ground: 'ground', cells: 'cells', rows: 'rows', centre: 'centre', path: 'path', target: 'target', vehicle: 'vehicle' };
// The polygon filled with the vehicle colour: the points of the arrow.
function arrowOf(calls) {
  const fill = calls.findIndex((c) => c[0] === 'set' && c[1] === 'fillStyle' && c[2] === 'vehicle');
  const points = [];
  for (const call of calls.slice(fill)) {
    if (call[0] === 'moveTo' || call[0] === 'lineTo') points.push([call[1], call[2]]);
    if (call[0] === 'closePath') break;
  }
  return points;
}

test('the drawing: the same arrow at the same place while the ground moves; the arrow turns by the heading error only', () => {
  const grid = fixture.local_grid_map;
  const cells = gridCells(grid);
  const box = viewBox(480, 440);
  const scene = sceneFromStatus({ local_grid_map: grid });
  const axis = rowAxis(scene);
  const draw = (pose, extra = {}) => {
    const ctx = recorder();
    drawMinimap(ctx, box, { pose, axis, grid, cells, rows: scene.rows, centre: scene.centre, tail: tail([[0, 0], [1, 0.1]], pose), ...extra }, COLOURS);
    return ctx.calls;
  };
  const along = (pose, d) => [pose[0] + Math.cos(axis) * d, pose[1] + Math.sin(axis) * d, axis];
  const start = [grid.base_pose[0], grid.base_pose[1], axis];
  const a = draw(start), b = draw(along(start, 0.4)), c = draw(along(start, 0.8));
  const arrow = arrowOf(a);
  assert.equal(arrow.length, 4, 'an arrow of four points');
  assert.deepEqual(arrowOf(b), arrow, 'the arrow does not move when the car does');
  assert.deepEqual(arrowOf(c), arrow);
  assert.ok(near(arrow[0][0], box.cx, 1e-9) && arrow[0][1] < box.cy, 'heading along the row: the tip straight up from the fixed place');
  assert.ok(arrow.every(([x, y]) => Math.hypot(x - box.cx, y - box.cy) <= 17), 'around (cx, cy)');
  // the ground moves down by the same amount for the same distance
  const shift = (calls) => calls.find((call) => call[0] === 'transform');
  const [fa, fb, fc] = [shift(a), shift(b), shift(c)].map((t) => t[6]);
  assert.ok(near(fb - fa, 0.4 * box.scale, 1e-6) && near(fc - fb, 0.4 * box.scale, 1e-6), 'the cells move down 0.4 m a step');
  assert.ok(near(shift(b)[5], shift(a)[5], 1e-6), 'and not sideways');
  assert.equal(a.filter((call) => call[0] === 'fillRect').length, cells.length, 'every occupied cell');
  // a heading error to the left turns the arrow to the left, the place stays
  const turned = arrowOf(draw([start[0], start[1], axis + 0.2]));
  assert.ok(turned[0][0] < box.cx - 2 && turned[0][1] < box.cy, 'the tip points up and to the left');
  const centreOf = (points) => [(points[0][0] + points[2][0]) / 2, (points[0][1] + points[2][1]) / 2];
  assert.ok(Math.hypot(centreOf(turned)[0] - box.cx, centreOf(turned)[1] - box.cy) < 6, 'still at the fixed place');
  // the picture of the cells is drawn with the transform of its own pixel size; the scale bar says its length
  const sheet = { width: grid.shape[0] * 4, height: grid.shape[1] * 4 };
  const withImage = draw(start, { image: sheet, imageK: 4, cells: null });
  assert.deepEqual(withImage.find((call) => call[0] === 'drawImage'), ['drawImage', sheet, 0, 0]);
  assert.ok(near(Math.hypot(shift(withImage)[1], shift(withImage)[2]) * 4, grid.cell_m * box.scale, 1e-9));
  assert.equal(withImage.filter((call) => call[0] === 'fillRect').length, 0);
  assert.deepEqual(a.find((call) => call[0] === 'fillText').slice(1, 2), [`${SCALE_BAR_M} m`]);
  // the lookahead point: one small dot; without a grid only the ground lines, the tail and the car
  const dot = draw(start, { lookahead: fromCar(start, 2, 0) }).filter((call) => call[0] === 'arc');
  assert.equal(dot.length, 1);
  assert.ok(near(dot[0][1], box.cx, 1e-6) && near(dot[0][2], box.cy - 2 * box.scale, 1e-6) && dot[0][3] <= 4, 'a small dot 2 m ahead');
  const bare = recorder();
  drawMinimap(bare, box, { pose: start, axis, grid: null, cells: null, rows: null, centre: null, tail: tail([[0, 0], [1, 0.1]], start), lookahead: null }, COLOURS);
  assert.equal(bare.calls.filter((call) => call[0] === 'transform' || call[0] === 'fillRect' || call[0] === 'arc').length, 0);
  assert.deepEqual(arrowOf(bare.calls), arrow, 'path and car only: the same arrow at the same place');
});
