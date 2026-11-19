// The executor's local grid layer of the map screen (static/steer/local-grid.mjs, UI-09): bitmap and cell decoding
// (np.packbits order), the grid frame, the window fit and the panel words, with the real export of the simulation
// repository's LocalGrid (tests/fixtures/executor_status_front_steer.json, local_grid_map). No browser.
// Run: node tests/test_local_grid.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { SCHEMA, describe, gridCells, layout, screenOf, toGrid } from '../static/steer/local-grid.mjs';

const sample = JSON.parse(fs.readFileSync(new URL('./fixtures/executor_status_front_steer.json', import.meta.url), 'utf8'));
const grid = sample.local_grid_map;
assert.equal(grid.schema, SCHEMA);

// The real bitmap: as many set bits as the export's occupied count, every cell inside the shape.
const cells = gridCells(grid);
assert.equal(cells.length, grid.occupied);
assert.ok(cells.every(([i, j]) => i >= 0 && i < grid.shape[0] && j >= 0 && j < grid.shape[1]));

// Bit order: big end first, rows along the axis (i), columns across (j).
const tiny = { shape: [2, 4], encoding: 'bitmap', bits: Buffer.from([0b10000001]).toString('base64') };
assert.deepEqual(gridCells(tiny), [[0, 0], [1, 3]]);
assert.deepEqual(gridCells({ shape: [2, 4], encoding: 'cells', cells: [[1, 2], [5, 0], [0, -1]] }), [[1, 2]]);
assert.equal(gridCells({ ...grid, bits: '' }), null, 'a truncated bitmap carries no cells');
assert.equal(gridCells({ ...grid, bits: 'AAAA' }), null, 'too short for the shape');
assert.equal(gridCells({ ...grid, shape: [0, 5] }), null);

// The cells sit between the two fitted row lines' sides: their across position (v) brackets the centre line.
const [u0, v0] = grid.origin_uv;
const vs = cells.map(([, j]) => v0 + (j + 0.5) * grid.cell_m);
const centreV = toGrid(grid.axis_rad, ...grid.lines.centre[0])[1];
assert.ok(Math.min(...vs) < centreV && Math.max(...vs) > centreV, 'occupied cells on both sides of the centre line');
// The row lines run close to the grid axis (the last fit's axis is within a few degrees of the window's axis).
for (const name of ['left', 'right', 'centre']) {
  const [a, b] = grid.lines[name].map(([x, y]) => toGrid(grid.axis_rad, x, y));
  assert.ok(Math.abs(Math.atan2(b[1] - a[1], b[0] - a[0])) < 0.1, `${name} along the axis`);
  assert.ok(b[0] - a[0] > 5, `${name} points ahead`);
}
const leftV = toGrid(grid.axis_rad, ...grid.lines.left[0])[1], rightV = toGrid(grid.axis_rad, ...grid.lines.right[0])[1];
assert.ok(leftV > centreV && centreV > rightV, 'left of the row is +v');

// The window fits the canvas with one scale; the left row line is drawn above the right one, ahead is to the right.
const box = layout(grid, 720, 350);
assert.ok(box.length * box.scale <= 720 - 24 + 1e-6 && box.width * box.scale <= 350 - 24 + 1e-6);
const [la, lb] = grid.lines.left.map(([x, y]) => screenOf(grid, box, x, y));
const [ra] = grid.lines.right.map(([x, y]) => screenOf(grid, box, x, y));
assert.ok(la[1] < ra[1], 'left line above the right line');
assert.ok(lb[0] > la[0], 'ahead is to the right');
const [cx, cy] = screenOf(grid, box, u0 * Math.cos(grid.axis_rad) - v0 * Math.sin(grid.axis_rad),
                          u0 * Math.sin(grid.axis_rad) + v0 * Math.cos(grid.axis_rad));
assert.ok(Math.abs(cx - box.ox) < 1e-6 && Math.abs(cy - (box.oy + box.width * box.scale)) < 1e-6, 'window origin at the lower left');
const narrow = layout(grid, 340, 350);
assert.ok(narrow.length * narrow.scale <= 340 - 24 + 1e-6, 'a phone width fits too');

assert.deepEqual(describe(grid).slice(0, 2), ['ok', `갱신 ${grid.updates}`]);
assert.match(describe(grid)[2], /^셀 0.1 m · 200×100 · 점유 160 · 선 품질 1.00 · /);
assert.deepEqual(describe({ ...grid, age_s: 3.21 }).slice(0, 2), ['warn', '오래됨 3.2 s']);
assert.match(describe({ ...grid, lines: undefined, truncated: true, bits: '' })[2], /열 선 아직 없음 .*격자가 커서 셀을 받지 못함/);

console.log('PASS: local grid bitmap and cells, grid frame, window fit and panel words with the real executor export');
