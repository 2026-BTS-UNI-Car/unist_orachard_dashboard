// The executor's local grid picture of a recorded autonomy run (static/steer/recorded-grid.mjs, 2026-11-20): a recorded
// LiDAR frame becomes cells with the executor pose of its own moment (the executor's mount: beam angle + 179.08 deg,
// the LiDAR 0.1185 m ahead of the base, not mirrored), the cells of a playback time come only from frames at or before
// it (a scrub backwards keeps nothing later), a cell needs two frames, another run is not counted, and the scene of a
// time (car between two samples, the lines of the state at or before). Synthetic data only; no browser, no recording.
// Run: node --test tests/test_recorded_grid.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  CELL_M, HISTORY_S, KEEP_SCANS, LIDAR_FRAME, LIDAR_X_IN_BASE_M, LIDAR_YAW_OFFSET_DEG, MAX_RANGE_M, MAX_SCANS, MIN_HITS, POSE_GAP_S, POSE_HOLD_S,
  REACH_M, SCAN_LATENCY_S, WINDOW_BEHIND_M, WINDOW_LENGTH_M, WINDOW_STEP_M, WINDOW_WIDTH_M, RecordedGrid, compactScan, eachCell, gridWindow, poseTrack,
  rebuildCells, recordedPainter, scanCells, sceneAt, trackPose,
} from '../static/steer/recorded-grid.mjs';
import { AHEAD_M, BEHIND_M, TRAIL_M } from '../static/steer/minimap.mjs';

const near = (a, b, eps = 1e-9) => Math.abs(a - b) <= eps;
const deg = (rad) => rad * 180 / Math.PI;
// the recorded angle of a beam that leaves the car at `bearing` [rad] from its travel axis (the inverse of the mount)
const beamAngle = (bearing) => Math.round((((deg(bearing) - LIDAR_YAW_OFFSET_DEG) % 360) + 360) % 360 * 100) / 100;
const frameOf = (t, returns) => ({ t_s: t, coordinate_frame: LIDAR_FRAME, points: returns.map(([bearing, range]) => ({ angle_deg: beamAngle(bearing), distance_m: range })) });
const stateOf = (t, x, y, yawDeg, extra = {}) => ({ t_s: t, state: { schema: 'ubts.row_executor.state.v1', run_id: 1, pose: { x, y, yaw_deg: yawDeg }, ...extra } });
const cellsOf = (built) => built.cells.map(([i, j]) => [i + Math.round(built.grid.origin_uv[0] / CELL_M), j + Math.round(built.grid.origin_uv[1] / CELL_M)]);

test('the numbers are the executor configuration of the 2026-11-20 field day', () => {
  assert.deepEqual([LIDAR_YAW_OFFSET_DEG, LIDAR_X_IN_BASE_M, SCAN_LATENCY_S, MAX_RANGE_M, CELL_M], [179.08, 0.1185, 0.08, 12, 0.1]);
  assert.deepEqual([WINDOW_LENGTH_M, WINDOW_WIDTH_M, WINDOW_BEHIND_M, WINDOW_STEP_M], [20, 10, 6, 1], "the executor's grid window");
  assert.equal(MIN_HITS, 2);
  assert.ok(WINDOW_LENGTH_M - WINDOW_BEHIND_M - WINDOW_STEP_M >= AHEAD_M && WINDOW_BEHIND_M >= BEHIND_M, 'the window covers what the picture shows ahead and behind');
  assert.ok(REACH_M >= MAX_RANGE_M + WINDOW_BEHIND_M, 'a frame that can reach the rear edge of the window is counted');
});

test('a recorded frame: the mount turns the beam angle, lists and objects read the same, bad returns are dropped', () => {
  // the travel axis is at -179.08 deg of the scan: that beam points straight ahead, a quarter turn more points left
  const ahead = compactScan({ t_s: 3.5, coordinate_frame: LIDAR_FRAME, points: [{ angle_deg: 180.92, distance_m: 5 }, { angle_deg: 270.92, distance_m: 2 }] });
  assert.equal(ahead.t, 3.5);
  assert.deepEqual([...ahead.xy], [5000, 0, 0, 2000], 'x ahead and y to the left, in millimetres');
  assert.ok(ahead.xy instanceof Int16Array);
  // the timeline answer sends lists with the names of the values (both orders of the two playback profiles)
  const full = compactScan({ t_s: 1, coordinate_frame: LIDAR_FRAME, points: [[0, 0, 5, 32, 180.92], [0, 0, 2, 32, 270.92]] }, ['x_m', 'y_m', 'distance_m', 'quality', 'angle_deg']);
  const polar = compactScan({ t_s: 1, coordinate_frame: LIDAR_FRAME, points: [[180.92, 5, 32], [270.92, 2, 32]] }, ['angle_deg', 'distance_m', 'quality']);
  assert.deepEqual([...full.xy], [...ahead.xy]);
  assert.deepEqual([...polar.xy], [...ahead.xy]);
  // not mirrored: a beam at a larger scan angle is farther to the left (counter-clockwise), as the executor reads it
  const [x, y] = compactScan(frameOf(0, [[0.3, 4]])).xy;
  assert.ok(near(x, 4000 * Math.cos(0.3), 1.5) && near(y, 4000 * Math.sin(0.3), 1.5) && y > 0);
  // returns the executor's grid does not take: nearer than its floor, beyond its range, not a number
  const kept = compactScan({ t_s: 2, coordinate_frame: LIDAR_FRAME, points: [{ angle_deg: 10, distance_m: 0.03 }, { angle_deg: 10, distance_m: MAX_RANGE_M + 0.001 },
    { angle_deg: 10, distance_m: NaN }, { angle_deg: null, distance_m: 3 }, { distance_m: 3 }, null, { angle_deg: 10, distance_m: MAX_RANGE_M }, { angle_deg: 10, distance_m: 0.05 }] });
  assert.equal(kept.xy.length, 4, 'the two returns inside [0.05 m, 12 m] stay');
  // a frame of another convention is not placed (its angles are not known to be beam angles); the answer can name the convention
  assert.equal(compactScan({ t_s: 1, coordinate_frame: 'legacy_archive_recorder_xy_v0', points: [{ angle_deg: 0, distance_m: 1 }] }), null);
  assert.equal(compactScan({ t_s: 1, points: [{ angle_deg: 0, distance_m: 1 }] }), null);
  assert.equal(compactScan({ t_s: 1, points: [{ angle_deg: 0, distance_m: 1 }] }, null, LIDAR_FRAME).xy.length, 2);
  assert.equal(compactScan({ coordinate_frame: LIDAR_FRAME, points: [] }), null, 'no time');
  assert.equal(compactScan({ t_s: 1, coordinate_frame: LIDAR_FRAME }), null, 'no points');
});

test('scan to cells: the pose of the moment places it, the LiDAR sits ahead of the base, cells are 0.1 m on the odometry axes', () => {
  const scan = compactScan(frameOf(0, [[0, 5], [Math.PI / 2, 2.05]]));
  // heading +x at the origin: 5 m ahead of the LiDAR is x = 5.1185; 2.05 m to its left is y = 2.05 at x = 0.1185
  assert.deepEqual(scanCells(scan, [0, 0, 0]), [[51, 0], [1, 20]]);
  // the same scan from a car at (10, 2) heading +y: ahead is +y, left is -x
  assert.deepEqual(scanCells(scan, [10, 2, Math.PI / 2]), [[100, 71], [79, 21]]);
  // heading -x: the lever and the returns turn with the car
  assert.deepEqual(scanCells(scan, [10, 2, Math.PI]), [[48, 20], [98, -1]]);
  // every point is visited once, a cell is listed once
  const twice = compactScan(frameOf(0, [[0, 5], [0, 5.01], [0, 5.02]]));
  let visits = 0;
  eachCell(twice, [0, 0, 0], () => { visits += 1; });
  assert.equal(visits, 3);
  assert.deepEqual(scanCells(twice, [0, 0, 0]), [[51, 0]]);
});

test('the pose of a time: straight between two samples, angles the short way round, held only for a moment', () => {
  const track = poseTrack([stateOf(0, 0, 0, 170), stateOf(1, 1, 0.5, -170), stateOf(2, 2, 1, -170), { t_s: 2.5, state: { run_id: 1 } }, { t_s: 2.6 }, stateOf(2, 9, 9, 0)]);
  assert.deepEqual(track.t, [0, 1, 2], 'states without a pose and a time that does not go on are left out');
  const half = trackPose(track, 0.5);
  assert.ok(near(half.pose[0], 0.5) && near(half.pose[1], 0.25));
  assert.ok(near(Math.abs(deg(half.pose[2])), 180, 1e-6), 'from 170 to -170 deg through 180, not through 0');
  assert.deepEqual([half.run, half.index], [1, 0]);
  assert.deepEqual(trackPose(track, 1).pose.map((v) => Math.round(v * 1e6) / 1e6), [1, 0.5, Math.round(-170 * Math.PI / 180 * 1e6) / 1e6], 'a sample itself');
  // after the last sample and before the first one: that sample, for POSE_HOLD_S only
  assert.deepEqual(trackPose(track, 2 + POSE_HOLD_S).pose.slice(0, 2), [2, 1]);
  assert.equal(trackPose(track, 2 + POSE_HOLD_S + 0.01), null);
  assert.deepEqual(trackPose(track, -POSE_HOLD_S).pose.slice(0, 2), [0, 0]);
  assert.equal(trackPose(track, -POSE_HOLD_S - 0.01), null);
  assert.deepEqual(trackPose(track, 60, Infinity).pose.slice(0, 2), [2, 1], 'the car of the picture stays at its last state');
  assert.equal(trackPose(poseTrack([]), 1), null);
  assert.equal(trackPose(track, NaN), null);
});

test('the pose of a time: nothing is joined across a gap, another run or a jump', () => {
  // a telemetry window that is not loaded leaves a gap: no pose is made up inside it
  const gap = poseTrack([stateOf(0, 0, 0, 0), stateOf(1, 0.4, 0, 0), stateOf(1 + POSE_GAP_S + 0.1, 1.2, 0, 0), stateOf(5, 1.5, 0, 0)]);
  assert.equal(trackPose(gap, 2.5), null);
  assert.deepEqual(trackPose(gap, 1.3).pose.slice(0, 2), [0.4, 0], 'just after the sample before the gap: held');
  assert.ok(near(trackPose(gap, 4.3).pose[0], 1.2 + 0.3 * (4.3 - 3.6) / 1.4, 1e-9), 'after the gap the samples join again');
  // a new run starts a new odometry frame: its first sample is not blended with the old run's last one
  const runs = poseTrack([stateOf(0, 30, 1, 0), { ...stateOf(1, 0.1, 0, 0), state: { run_id: 2, pose: { x: 0.1, y: 0, yaw_deg: 0 } } }, { t_s: 2, state: { run_id: 2, pose: { x: 0.5, y: 0, yaw_deg: 0 } } }]);
  assert.deepEqual([trackPose(runs, 0.4).pose[0], trackPose(runs, 0.4).run], [30, 1]);
  assert.equal(trackPose(runs, 0.9), null, 'between the runs, past the hold: no pose');
  assert.ok(near(trackPose(runs, 1.5).pose[0], 0.3) && trackPose(runs, 1.5).run === 2);
  // the same run id with a jump of the pose (an executor that was started again): not joined either
  const jump = poseTrack([stateOf(0, 0, 0, 0), stateOf(1, 5, 0, 0)]);
  assert.deepEqual(trackPose(jump, 0.4).pose.slice(0, 2), [0, 0]);
});

test('the window: 20 m by 10 m of 0.1 m cells around the car, 6 to 7 m behind it, moved in 1 m steps', () => {
  for (const pose of [[0, 0, 0], [17.69, -0.67, -0.02], [3.999, 4.449, 0.3], [-2.31, -7.75, 0]]) {
    const win = gridWindow(pose);
    assert.deepEqual([win.ni, win.nj], [200, 100]);
    assert.ok(win.i0 % 10 === 0 && win.j0 % 10 === 0, 'the corner is on a whole metre');
    const behind = pose[0] - win.i0 * CELL_M, right = pose[1] - win.j0 * CELL_M;
    assert.ok(behind >= WINDOW_BEHIND_M - 1e-9 && behind < WINDOW_BEHIND_M + WINDOW_STEP_M + CELL_M, `${behind} m behind the car`);
    assert.ok(Math.abs(right - WINDOW_WIDTH_M / 2) <= WINDOW_STEP_M / 2 + CELL_M, `${right} m to the right of the car`);
  }
  assert.deepEqual(gridWindow([17.05, 0.2, 0]), gridWindow([17.95, -0.2, 0]), 'the window stays while the car moves less than a step');
  assert.notDeepEqual(gridWindow([17.95, 0, 0]), gridWindow([18.05, 0, 0]));
});

// ---- a synthetic row: two straight tree lines, a car that weaves a little along the row ----
const LEFT_Y = 2.03, RIGHT_Y = -1.87;        // the two lines (inside the cells j = 20 and j = -19)
const SPEED = 0.4;
const truePose = (t) => {
  const y = 0.2 * Math.sin(0.3 * t);
  return [SPEED * t, y, Math.atan2(0.2 * 0.3 * Math.cos(0.3 * t), SPEED)];
};
const rowStates = (until, step = 0.25) => {
  const out = [];
  for (let t = 0; t <= until + 1e-9; t += step) { const p = truePose(t); out.push(stateOf(Math.round(t * 1000) / 1000, p[0], p[1], deg(p[2]))); }
  return out;
};
// What the recorder writes for the scan it receives at `t`: taken SCAN_LATENCY_S earlier, every beam that meets a line.
function rowFrame(t, beams = 810) {
  const [x, y, yaw] = truePose(t - SCAN_LATENCY_S);
  const lx = x + LIDAR_X_IN_BASE_M * Math.cos(yaw), ly = y + LIDAR_X_IN_BASE_M * Math.sin(yaw);
  const points = [];
  for (let n = 0; n < beams; n += 1) {
    const angle = Math.round((n * 360 / beams) * 100) / 100;                    // the recorded scan angle [deg]
    const direction = (angle + LIDAR_YAW_OFFSET_DEG) * Math.PI / 180 + yaw;     // in the odometry frame
    const s = Math.sin(direction);
    if (Math.abs(s) < 1e-6) continue;
    const range = ((s > 0 ? LEFT_Y : RIGHT_Y) - ly) / s;
    if (range > 0.05 && range <= 30) points.push({ angle_deg: angle, distance_m: Math.round(range * 1000) / 1000, quality: 32 });
  }
  return { t_s: t, coordinate_frame: LIDAR_FRAME, points };
}
const rowFrames = (until, step = 0.55) => { const out = []; for (let t = 0.3; t <= until; t += step) out.push(rowFrame(Math.round(t * 1000) / 1000)); return out; };

test('a recorded row: the cells of a playback time lie on the two tree lines and nowhere else', () => {
  const grid = new RecordedGrid();
  grid.states(rowStates(40), 'row');
  assert.equal(grid.ingest(rowFrames(40)), 73);
  for (const at of [6, 20, 39.9]) {
    const picture = grid.picture(at);
    const cells = cellsOf(picture);
    const left = cells.filter(([, j]) => j === 20), right = cells.filter(([, j]) => j === -19);
    assert.equal(left.length + right.length, cells.length, `t ${at}: every cell is on a line (j = 20 or j = -19), none beside them`);
    assert.ok(left.length > 80 && right.length > 80, `t ${at}: ${left.length} and ${right.length} cells along the two lines`);
    // a car that weaves: without the pose of each frame's own moment the lines would smear over neighbouring cells
    const x = truePose(at)[0];
    assert.ok(Math.max(...cells.map(([i]) => i)) <= Math.floor((x + LIDAR_X_IN_BASE_M + MAX_RANGE_M) / CELL_M), 'nothing beyond the range of the last frame');
    assert.ok(Math.min(...cells.map(([i]) => i)) >= Math.round(picture.grid.origin_uv[0] / CELL_M), 'nothing behind the window');
    assert.deepEqual([picture.grid.cell_m, picture.grid.axis_rad, picture.grid.shape], [0.1, 0, [200, 100]]);
  }
});

test('cells limited to the playback time: later frames never count, a scrub backwards keeps nothing', () => {
  const states = rowStates(40), frames = rowFrames(40);
  const all = new RecordedGrid();
  all.states(states, 'row'); all.ingest(frames);
  for (const at of [3, 12.4, 25]) {
    const early = new RecordedGrid();
    early.states(states, 'row'); early.ingest(frames.filter((frame) => frame.t_s <= at));
    assert.deepEqual(all.picture(at).cells, early.picture(at).cells, `t ${at}: the same cells with and without the later frames`);
    assert.equal(all.picture(at).scans, frames.filter((frame) => frame.t_s <= at).length, 'every frame up to that time, none after it');
  }
  // forwards to 30 s, back to 8 s: the picture of 8 s is the one a fresh player makes
  const fresh = new RecordedGrid();
  fresh.states(states, 'row'); fresh.ingest(frames);
  const first = fresh.picture(8).cells;
  all.picture(30);
  assert.deepEqual(all.picture(8).cells, first);
  const reach = Math.floor((truePose(8)[0] + LIDAR_X_IN_BASE_M + MAX_RANGE_M) / CELL_M);
  assert.ok(cellsOf(all.picture(8)).every(([i]) => i <= reach), 'no cell the car had not seen yet at 8 s');
  assert.ok(cellsOf(all.picture(30)).some(([i]) => i > reach), 'while the picture of 30 s has them');
  // a frame exactly at the playback time counts, one a millisecond later does not
  const edge = new RecordedGrid();
  edge.states(states, 'row'); edge.ingest(frames);
  const t = frames[10].t_s;
  assert.equal(edge.picture(t).scans, 11);
  assert.equal(edge.picture(t - 0.001).scans, 10);
});

test('a cell needs two frames; a frame of another run or out of reach is not counted; at most MAX_SCANS frames', () => {
  const still = (until, run = 1, x = 0) => { const out = []; for (let t = 0; t <= until; t += 0.5) out.push({ t_s: t, state: { run_id: run, pose: { x, y: 0, yaw_deg: 0 } } }); return out; };
  // a post seen in every frame, a passer-by seen in one frame only (by two beams of that frame, in one cell)
  const scans = [compactScan(frameOf(1, [[0, 3], [1, 4], [1, 4.01]])), compactScan(frameOf(2, [[0, 3]])), compactScan(frameOf(3, [[0, 3]]))];
  assert.deepEqual(scanCells(scans[0], [0, 0, 0]), [[31, 0], [22, 33]]);
  const built = rebuildCells(scans, poseTrack(still(5)), 4);
  assert.deepEqual(cellsOf(built), [[31, 0]], 'the post; two beams of one frame are one hit, not two');
  assert.equal(built.scans, 3);
  assert.deepEqual(cellsOf(rebuildCells(scans, poseTrack(still(5)), 1.5)), [], 'one frame so far: nothing yet');
  assert.equal(rebuildCells(scans, poseTrack(still(5)), -1), null, 'no state at or before that time: no picture');
  // a second run starts at 10 s with a new odometry frame: the frames of the first run are left out
  const track = poseTrack([...still(5, 1), ...still(8, 2).map((frame) => ({ ...frame, t_s: frame.t_s + 10 }))]);
  const two = [...scans, compactScan(frameOf(11, [[Math.PI / 2, 2.05]])), compactScan(frameOf(12, [[Math.PI / 2, 2.05]]))];
  assert.deepEqual(cellsOf(rebuildCells(two, track, 13)), [[1, 20]], 'only what the second run saw');
  assert.equal(rebuildCells(two, track, 13).scans, 2);
  assert.deepEqual(cellsOf(rebuildCells(two, track, 4)), [[31, 0]], 'and back in the first run only its own');
  // a frame whose moment has no pose (its states are not loaded) is not placed
  const lone = [compactScan(frameOf(1, [[0, 3]])), compactScan(frameOf(2, [[0, 3]])), compactScan(frameOf(30, [[0, 3]])), compactScan(frameOf(31, [[0, 3]]))];
  const holes = poseTrack([...still(5), ...still(2).map((frame) => ({ ...frame, t_s: frame.t_s + 40 }))]);
  assert.equal(rebuildCells(lone.slice(2), holes, 41).scans, 0, 'the frames of 30 s and 31 s fall in a gap of the states');
  assert.equal(rebuildCells(lone, holes, 41).scans, 2, 'the two frames of the same run whose poses are known still count');
  // out of reach: the car is 30 m on, the frames of the start cannot touch its window
  const far = poseTrack([...still(5), { t_s: 5.5, state: { run_id: 1, pose: { x: 1.5, y: 0, yaw_deg: 0 } } },
    ...Array.from({ length: 30 }, (_, k) => ({ t_s: 6 + k, state: { run_id: 1, pose: { x: 3 + k, y: 0, yaw_deg: 0 } } }))]);
  assert.equal(rebuildCells(scans, far, 35).scans, 0);
  assert.ok(Math.hypot(trackPose(far, 35, Infinity).pose[0], 0) > REACH_M);
  // standing for a long time: the newest MAX_SCANS frames
  const many = Array.from({ length: MAX_SCANS + 40 }, (_, k) => compactScan(frameOf(k * 0.5, [[0, 3]])));
  assert.equal(rebuildCells(many, poseTrack(still(200)), 150).scans, MAX_SCANS);
});

test('the scene of a time: the car between two samples, the lines and values of the state at or before', () => {
  const line = (y, w) => ({ odom: [[-2, y], [8, y]], base: [[-2, y], [8, y]], axis_deg: 0, width_m: w });
  const frames = [
    stateOf(10, 4, 0.1, 0, { centreline: line(0, 3.8), grid_centreline: line(0.05, 4), lookahead: { base: [3, -0.1], odom: [7, 0] }, lat_m: 0.1 }),
    stateOf(10.25, 4.1, 0.14, 4, { centreline: line(0.02, 3.8), grid_centreline: line(0.05, 4), lookahead: { base: [3, -0.2], odom: [7.1, 0] }, lat_m: 0.12 }),
    stateOf(10.5, 4.2, 0.16, 4, { lat_m: 0.14 }),
  ];
  const earlier = Array.from({ length: 40 }, (_, k) => stateOf(k * 0.25, k * 0.1, 0.1, 0));
  const all = [...earlier, ...frames], track = poseTrack(all);
  const scene = sceneAt(all, track, 10.125);
  assert.equal(scene.frame, frames[0], 'the values are those of the state at or before the playback time');
  assert.ok(near(scene.pose[0], 4.05) && near(scene.pose[1], 0.12) && near(deg(scene.pose[2]), 2, 1e-9), 'the car is half way between the two samples');
  assert.equal(scene.run, 1);
  // the estimator's centre line over the whole picture, the grid's line moved half its width to each side
  assert.ok(scene.centre.every(([, y]) => near(y, 0)) && scene.centre[0][0] < 4.05 - BEHIND_M && scene.centre[1][0] > 4.05 + AHEAD_M);
  assert.ok(scene.rows.left.every(([, y]) => near(y, 2.05)) && scene.rows.right.every(([, y]) => near(y, -1.95)), 'left is the +y side');
  assert.ok(near(scene.rows.left[0][0], 4.05 - 4) && near(scene.rows.left[1][0], 4.05 + 10), "as long as the executor's own row lines");
  assert.ok(near(scene.axis, 0, 1e-9), 'up is along the centre line');
  // the lookahead point rides with the car: its place in the car's frame on the pose of the playback time
  const c = Math.cos(scene.pose[2]), s = Math.sin(scene.pose[2]);
  assert.ok(near(scene.lookahead[0], scene.pose[0] + 3 * c + 0.1 * s) && near(scene.lookahead[1], scene.pose[1] + 3 * s - 0.1 * c));
  // the tail starts at the car and holds only where it has been
  assert.deepEqual(scene.tail[0], [scene.pose[0], scene.pose[1], 0]);
  assert.ok(scene.tail.length > 20 && scene.tail.every(([x]) => x <= scene.pose[0] + 1e-9));
  assert.ok(scene.tail[scene.tail.length - 1][2] <= TRAIL_M + 1e-9);
  // without motion (reduced motion): the car of the state at or before
  assert.deepEqual(sceneAt(all, track, 10.125, false).pose.slice(0, 2), [4, 0.1]);
  // a state without lines: the car and its tail, nothing made up; the axis is the heading
  const bare = sceneAt(all, track, 10.6);
  assert.deepEqual([bare.centre, bare.rows.left, bare.rows.right, bare.lookahead], [null, null, null, null]);
  assert.ok(near(deg(bare.axis), 4, 1e-9));
  // before the first state, and with states that carry no pose: no picture
  assert.equal(sceneAt(all, track, -0.5), null);
  assert.equal(sceneAt([{ t_s: 1, state: { run_id: 1 } }], poseTrack([{ t_s: 1, state: { run_id: 1 } }]), 2), null);
  assert.equal(sceneAt([], poseTrack([]), 2), null);
});

test('the store: windows held and missing, a bounded number of frames, cells counted again only when something changed', () => {
  const grid = new RecordedGrid();
  grid.states(rowStates(300), 'row');
  const frames = rowFrames(290);
  const windowOf = (start) => frames.filter((frame) => frame.t_s >= start && frame.t_s < start + 4);
  assert.equal(grid.ingest(windowOf(60), { startS: 60, endS: 64 }), windowOf(60).length);
  assert.equal(grid.ingest(windowOf(60), { startS: 60, endS: 64 }), 0, 'the same frames again are not held twice');
  assert.ok(grid.covered(60) && !grid.covered(56));
  // the windows of the HISTORY_S before a time that are not held, the newest first; the window of the time itself is the player's
  assert.equal(HISTORY_S, 24);
  assert.deepEqual(grid.missing(61.3, 4), [56, 52, 48, 44, 40, 36]);
  grid.ingest(windowOf(52), { startS: 52, endS: 56 });
  grid.ingest([], { startS: 44, endS: 48 });                        // a window without frames is held too
  assert.deepEqual(grid.missing(61.3, 4), [56, 48, 40, 36]);
  assert.deepEqual(grid.missing(5, 4), [0]);
  assert.deepEqual(grid.missing(2, 4), [], 'at the start there is nothing before');
  assert.deepEqual(grid.missing(NaN, 4), []);
  // lists with field names, as the timeline answer sends them
  const listed = { t_s: 70.1, points: [[180.92, 5, 32]] };
  assert.equal(grid.ingest([listed], { fields: ['angle_deg', 'distance_m', 'quality'], frame: LIDAR_FRAME }), 1);
  assert.deepEqual([...grid.scans.find((scan) => scan.t === 70.1).xy], [5000, 0]);
  assert.ok(grid.scans.every((scan, k) => k === 0 || scan.t > grid.scans[k - 1].t), 'held in the order of time');

  // the same picture twice: the cells are not counted again; another frame in play or another window: they are
  const a = grid.picture(62), b = grid.picture(62.2);
  assert.equal(a.cells, b.cells);
  assert.notEqual(a.pose[0], b.pose[0], 'while the car moves on');
  const next = grid.scans.find((scan) => scan.t > 62.2).t;
  assert.notEqual(grid.picture(next).cells, a.cells);
  const version = grid.version;
  grid.states(rowStates(300), 'row');
  assert.equal(grid.version, version, 'the same list of states: the track stays');
  grid.states(rowStates(300), 'row+1');
  assert.equal(grid.version, version + 1);

  // more frames than KEEP_SCANS: the ones farthest in time from the playback time go, and their windows with them
  const full = new RecordedGrid();
  full.states(rowStates(300), 'row');
  for (let start = 0; start < 288; start += 4) full.ingest(windowOf(start), { startS: start, endS: start + 4 });
  assert.ok(full.scans.length > KEEP_SCANS);
  const total = full.scans.length;
  const picture = full.picture(280);
  assert.equal(full.scans.length, KEEP_SCANS);
  assert.equal(full.scans.at(-1).t, frames.filter((frame) => frame.t_s < 288).at(-1).t_s, 'the newest stay');
  assert.ok(full.scans[0].t > 280 - 250 && !full.covered(0) && !full.covered(Math.floor(full.scans[0].t / 4) * 4 - 4) && full.covered(276), `${total - KEEP_SCANS} old frames went`);
  assert.ok(picture.cells.length > 100 && picture.scans <= MAX_SCANS);
  full.reset();
  assert.deepEqual([full.scans.length, full.windows.size, full.frames.length, full.picture(280)], [0, 0, 0, null]);
});

test('the painter: one drawing a picture, the same picture is not drawn twice, no picture empties the canvas', () => {
  const calls = [];
  const context = () => new Proxy({}, { get: (target, name) => (name in target ? target[name] : (...args) => { calls.push([name, ...args]); }), set: (target, name, value) => { target[name] = value; return true; } });
  let sheets = 0;
  let measured = 0;
  const canvas = { width: 0, height: 0, ctx: context(), getContext() { return this.ctx; }, getBoundingClientRect: () => { measured += 1; return { width: 358, height: 440 }; } };
  const saved = { document: globalThis.document, window: globalThis.window };
  globalThis.document = { createElement: () => { sheets += 1; return { width: 0, height: 0, ctx: context(), getContext() { return this.ctx; } }; } };
  globalThis.window = { devicePixelRatio: 3 };
  try {
    const grid = new RecordedGrid();
    grid.states(rowStates(40), 'row'); grid.ingest(rowFrames(40));
    const painter = recordedPainter(canvas, (name, fallback) => fallback, 1.5);
    const count = (name) => calls.filter(([call]) => call === name).length;
    assert.equal(painter.paint(grid.picture(20)), true);
    assert.deepEqual([canvas.width, canvas.height], [537, 660], 'the buffer: the box times the capped pixel ratio');
    assert.equal(count('drawImage'), 1, 'the cells as one sheet');
    assert.equal(painter.paint(grid.picture(20)), false, 'the same picture again: nothing to do');
    assert.equal(painter.paint(grid.picture(20.05)), true, 'the car moved on');
    assert.equal(painter.paint(grid.picture(31)), true);
    assert.equal(sheets, 1, 'one sheet for all pictures');
    assert.equal(count('drawImage'), 3);
    const before = count('clearRect');
    assert.equal(painter.paint(null), true);
    assert.equal(count('clearRect'), before + 1);
    assert.equal(painter.paint(null), false);
    painter.forget();
    assert.equal(painter.paint(null), true, 'after forget() the next picture is drawn whatever it is');
    // once an observer has told the size of the box it is not measured again (a measurement in the middle of the
    // player's frame makes the page lay itself out an extra time); a new size is a new picture
    const before2 = measured;
    painter.resized(324, 440);
    assert.equal(painter.paint(grid.picture(20)), true);
    assert.deepEqual([canvas.width, canvas.height], [486, 660]);
    assert.equal(painter.paint(grid.picture(21)), true);
    assert.equal(measured, before2, 'no measurement');
    painter.resized(0, 0);
    assert.equal(painter.paint(grid.picture(22)), false, 'a box without a size (a hidden part): nothing is drawn');
  } finally {
    globalThis.document = saved.document; globalThis.window = saved.window;
  }
});
