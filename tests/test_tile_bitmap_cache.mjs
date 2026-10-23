import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import * as model from '../static/sim-map-model.mjs';
import * as codec from '../static/grid-cell-codec.mjs';
import * as delta from '../static/live-map-delta.mjs';
const source=fs.readFileSync(new URL('../static/metric-map.mjs',import.meta.url),'utf8');
const classSource=source.slice(source.indexOf('export class MetricMap {')).replace('export class','class');
let allocations=0,draws=0;
const canvas={addEventListener(){},getBoundingClientRect:()=>({width:720,height:400})};
const context=vm.createContext({...model,...codec,...delta,$:()=>canvas,ResizeObserver:class{observe(){}},
  document:{createElement(){allocations++;return {getContext:()=>({
    createImageData:()=>({data:new Uint8ClampedArray(64*64*4)}),putImageData(){}})};}}});
vm.runInContext(`${classSource}\nthis.MetricMap=MetricMap`,context);
const map=new context.MetricMap('test');map.draw=()=>{draws++;};
const tile=(x,state)=>({x,y:0,encoding:'rle2-v1',cells:[state,4096]});
const packet=(version,tiles,extra={})=>({tile_epoch:'a:0',map_epoch:'a',version,tile_version:version,scan_count:version,
  grid:{resolution:.1,width:128,height:64,origin:{x:0,y:0}},tiles,path:[],tiles_reset:false,...extra});
await map.set(packet(1,[tile(0,2),tile(1,1)],{tiles_reset:true}));assert.equal(allocations,2);
const first=map.tileBitmaps.get('0,0').image;
map.auto=false;map.view={x:12,y:-3,scale:7};const manual=map.view;
await map.set(packet(2,[tile(0,2),tile(1,1)],{tiles_reset:true}));
assert.equal(allocations,2,'equal full seed reuses already decoded cells');
assert.equal(map.tileBitmaps.get('0,0').image,first);assert.equal(map.view,manual);
await map.set(packet(3,[tile(1,2)]));assert.equal(allocations,3);
assert.equal(map.tileBitmaps.get('0,0').image,first,'unchanged distant objects survive delta updates');
await map.set(packet(1,[tile(0,2)],{tiles_reset:true,preserve_view_on_seek:true}));
assert.equal(allocations,3);assert.equal(map.tileBitmaps.size,1,'backward seek still removes future tiles');
assert.equal(map.view,manual);assert.equal(draws,4,'no blank intermediate draw on a full replacement');
await map.set(packet(1,[tile(0,2)],{tile_epoch:'b:0',map_epoch:'b',tiles_reset:true}));
assert.equal(allocations,4,'new coordinate generation never reuses stale tile caches');
await map.set(packet(2,[{x:0,y:0,encoding:'rle2-v1',cells:[2,4096]}],{tile_epoch:'b:0',map_epoch:'b'}));
assert.equal(allocations,4);assert.equal(draws,6);
console.log('Cell bitmap cache, atomic full replacement, backward seek and manual zoom passed');
