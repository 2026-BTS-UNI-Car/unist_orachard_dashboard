import assert from 'node:assert/strict';
import {decodeTileCells,cellPixels,tileOccupiedIndices,nearbyTileBounds} from '../static/grid-cell-codec.mjs';
const packed=Buffer.alloc(1024,0xe4).toString('base64');
const states=decodeTileCells({x:-2,y:-1,encoding:'packed2-v1',cells:packed});
assert.deepEqual([...states.slice(0,8)],[0,1,2,3,0,1,2,3]);
assert.equal(states.length,4096);
const runs=decodeTileCells({x:-1,y:0,encoding:'rle2-v1',cells:[2,1,1,1,0,4093,3,1]});
assert.deepEqual([...runs.slice(0,3)],[2,1,0]);assert.equal(runs[4095],3);
assert.deepEqual([...cellPixels(runs).slice(0,12)],[25,25,25,255,245,245,245,255,127,127,127,255]);
const occupied=tileOccupiedIndices(runs);assert.deepEqual([...occupied],[0]);
const tile={x:-1,y:0,occupied};
const bounds=nearbyTileBounds({pose:{x:0,y:0},grid:{resolution:.1}},new Map([['-1,0',tile]]));
assert.ok(Math.abs(bounds.minX+7.4)<1e-8);assert.equal(bounds.maxX,1);
for(const cells of [[0,4095],[0,4097],[4,4096],[0,0],[0,4096,1],[]]) {
  assert.throws(()=>decodeTileCells({x:0,y:0,encoding:'rle2-v1',cells}));
}
assert.throws(()=>decodeTileCells({x:.5,y:0,encoding:'rle2-v1',cells:[0,4096]}));
assert.throws(()=>decodeTileCells({x:0,y:0,encoding:'packed2-v1',cells:packed.slice(1)}));

// Browser-side raster creation and map revisions work without any Image API.
let rasterWrites=0;
const context={createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData:()=>rasterWrites++};
globalThis.document={getElementById:()=>({addEventListener(){}}),createElement:()=>({getContext:()=>context})};
globalThis.ResizeObserver=class {observe(){}};
globalThis.Image=class {constructor(){throw new Error('Cell packets must never decode an image');}};
const {MetricMap}=await import('../static/metric-map.mjs');
const map=new MetricMap('canvas');map.draw=()=>{};
const packet=(version,tiles,reset=false)=>({tiles,tile_size:64,tiles_reset:reset,tile_epoch:'a:0:1',
  view_epoch:'a:0',tile_version:version,map_epoch:'a',scan_count:version,pose:{x:0,y:0},
  grid:{width:200,height:200,resolution:.1,origin:{x:-10,y:-10},encoding:'tiles-cells-v1'},path:[{stamp:version}]});
const numeric={x:-1,y:0,encoding:'rle2-v1',cells:[2,1,0,4095]};
await map.set(packet(1,[numeric],true));assert.equal(map.tileBitmaps.size,1);assert.equal(rasterWrites,1);
map.view={x:3,y:2,scale:8};map.auto=false;
await map.set({...packet(2,[{...numeric,x:0}]),path_append:true});
assert.equal(map.tileBitmaps.size,2);assert.deepEqual(map.snapshot.path,[{stamp:1},{stamp:2}]);
assert.deepEqual(map.view,{x:3,y:2,scale:8});
map.snapshot.coordinate_frame='isolated_local_segment';
await map.set({...packet(3,[numeric],true),tile_epoch:'a:0:2',coordinate_frame:'fixed_ENU_GNSS_position_constrained'});
assert.equal(map.tileBitmaps.size,1);assert.deepEqual(map.view,{x:3,y:2,scale:8});
await map.set({...packet(4,[{...numeric,cells:[0,2]}]),path_append:true});
assert.equal(map.tileEpoch,null);assert.equal(map.snapshot.tile_version,3);assert.equal(map.tileBitmaps.size,1);
console.log('Numeric occupancy transport, local rendering, zoom preservation and malformed packet recovery passed');
