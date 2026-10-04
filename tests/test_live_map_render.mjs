import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import * as model from '../static/sim-map-model.mjs';

// Exercise the production class with real drawing calls and controllable image
// completion order. No browser, network, simulation or timers are started.
const source = fs.readFileSync(new URL('../static/metric-map.mjs', import.meta.url), 'utf8');
const classSource = source.slice(source.indexOf('export class MetricMap {')).replace('export class','class');
function fixture(schematic = false, headingOffset = 0) {
  const images = [], observers = [], commands = [], events = {}, reads = [];
  const rect = {width:720, height:400};
  const ctx = new Proxy({}, {get(target, key) {
    return target[key] ?? ((...args) => commands.push([key, ...args]));
  }});
  const canvas = {width:0, height:0, getBoundingClientRect:() => rect,
    getContext:() => ctx, addEventListener:(name, fn) => {events[name] = fn;}, setPointerCapture() {}};
  const context = vm.createContext({...model, $:() => canvas, devicePixelRatio:1,
    document:{createElement:() => {let bitmap; return {getContext:() => ({
      drawImage(image) {bitmap=image;},
      getImageData() {
        reads.push(bitmap);
        if(bitmap.pixels)return {data:bitmap.pixels};
        const grid=bitmap.grid, data=new Uint8ClampedArray(grid.width*grid.height*4), hits=grid.occupied_bounds;
        for(let cell=0;cell<grid.width*grid.height;cell++){
          const x=grid.origin.x+(cell%grid.width+.5)*grid.resolution,y=grid.origin.y+(Math.floor(cell/grid.width)+.5)*grid.resolution;
          const value=hits&&x>=hits.minX&&x<hits.maxX&&y>=hits.minY&&y<hits.maxY?25:245;
          data.set([value,value,value,255],cell*4);
        }
        return {data};
      }
    })};}},
    Image:class {constructor() {images.push(this);} set src(value) {
      this.grid=map.pendingImage.snapshot.grid;this.naturalWidth=this.grid.width;this.naturalHeight=this.grid.height;
    }},
    ResizeObserver:class {constructor(fn) {this.notify = fn; observers.push(this);} observe() {}}});
  vm.runInContext(`${classSource}\nglobalThis.MetricMap = MetricMap;`, context);
  const map = new context.MetricMap('test', schematic, headingOffset), draws = [], original = map.draw;
  map.draw = function() {draws.push({snapshot:this.snapshot, bitmap:this.bitmap}); return original.call(this);};
  return {map, images, draws, observers, events, commands, rect, reads};
}
function snapshot(version, data = 'AAAA', overrides = {}) {
  return {version, scan_count:10, active:true, stamp:version,
    grid:{width:20, height:10, resolution:.1, origin:{x:-1, y:-1},
      encoding:'png', row_order:'min_y_first', image_base64:data,
      occupied_bounds:{minX:-.5,minY:-.5,maxX:.5,maxY:0}},
    pose:{x:version, y:0, yaw:0, stamp:version},
    path:[{x:0, y:0, stamp:0}, {x:version, y:0, stamp:version}], ...overrides};
}
function withoutPng(value) {
  const {image_base64, ...grid} = value.grid;
  return {...value, grid};
}
const values = value => JSON.parse(JSON.stringify(value));

// The real vehicle marker has a half-turn mounting/display offset. Its tip
// changes direction while world cells, path, pose and metric view stay native.
{
  const {map,images,commands}=fixture(false,Math.PI);
  const value=snapshot(1,'AAAA',{pose:{x:2,y:3,yaw:Math.PI/2,stamp:1}});
  map.set(value);images[0].onload();
  const angle=commands.filter(([name])=>name==='rotate').at(-1)[1];
  assert.ok(Math.abs(Math.cos(angle))<1e-12);
  assert.ok(Math.sin(angle)>0,'marker tip must face screen-down for sensor yaw +90 degrees');
  assert.equal(map.snapshot.pose.yaw,Math.PI/2);
  assert.equal(map.snapshot.grid,value.grid);assert.equal(map.snapshot.path,value.path);
}

// One complete draw per decoded generation, with no blank/path-only draw and
// no early bounds/autozoom update while a new image is still pending.
{
  const {map, images, draws, commands} = fixture();
  const first = snapshot(1); map.set(first);
  assert.equal(draws.length, 0); assert.equal(map.imageVersion, undefined);
  images[0].onload();
  assert.equal(draws.length, 1); assert.equal(map.snapshot, first);
  assert.equal(commands.filter(([name]) => name === 'drawImage').length, 1);
  map.zoom('in'); const view = map.view, bounds = map.bounds, before = draws.length;
  const second = snapshot(2, 'BBBB', {pose:{x:20, y:0, stamp:2}}); map.set(second);
  assert.equal(draws.length, before); assert.equal(map.snapshot, first);
  assert.equal(map.bitmap, images[0]); assert.equal(map.view, view); assert.equal(map.bounds, bounds);
  images[1].onload();
  assert.equal(draws.length, before + 1); assert.equal(map.snapshot, second);
  assert.equal(map.bitmap, images[1]); assert.equal(map.auto, false);
  assert.equal(map.view, view,'decoded map growth preserves the manually selected view');
  assert.equal(map.imageVersion, 2);
}

// Auto mode fits occupied objects plus the current rover, while manual zoom and
// drag preserve world center / pixels per metre across growing and shrinking
// rasters, moved origins, paused metadata, resized canvases and pending images.
{
  const {map,images,events,rect,observers} = fixture();
  const first=snapshot(1,'AAAA',{grid:{...snapshot(1).grid,width:1000,height:1000,origin:{x:-50,y:-50}},
    path:[{x:90,y:90,stamp:0}],pose:{x:0,y:0,stamp:1}});
  map.set(first);images[0].onload();
  assert.deepEqual(values(map.bounds),{minX:-1.5,minY:-1.5,maxX:1.5,maxY:1});
  const fitted=values(map.view);
  const freeGrowth={...first,version:2,grid:{...first.grid,width:1024,height:1024,origin:{x:-51,y:-51},image_base64:'BBBB'}};
  map.set(freeGrowth);images[1].onload();
  assert.deepEqual(values(map.view),fitted,'unknown/free canvas growth must not zoom out');
  map.zoom('in');
  events.pointerdown({clientX:10,clientY:10,pointerId:1});
  events.pointermove({clientX:60,clientY:30});events.pointerup();
  const manual=values(map.view);
  const moreObjects={...freeGrowth,version:3,grid:{...freeGrowth.grid,image_base64:'CCCC',
    occupied_bounds:{minX:-40,minY:-35,maxX:30,maxY:30}}};
  map.set(moreObjects);assert.deepEqual(values(map.view),manual);images[2].onload();
  assert.deepEqual(values(map.view),manual);assert.equal(map.auto,false);
  const shrunk=snapshot(4,'DDDD',{pose:{x:0,y:0,stamp:4},active:false});
  map.set(shrunk);images[3].onload();
  assert.deepEqual(values(map.view),manual);assert.equal(map.auto,false);
  map.set(withoutPng({...shrunk,pose:{x:30,y:20,stamp:5}}));
  assert.deepEqual(values(map.view),manual);
  rect.width=980;rect.height=600;observers[0].notify();
  assert.deepEqual(values(map.view),manual,'resize preserves metric scale and center in manual mode');
  map.zoom('fit');assert.equal(map.auto,true);
  assert.deepEqual(values(map.view),model.fittedView(map.bounds,980,600));
  assert.notDeepEqual(values(map.view),manual);
  const afterFit=values(map.view);rect.width=600;rect.height=300;observers[0].notify();
  assert.notEqual(map.view.scale,afterFit.scale,'auto-fit remains responsive to canvas size');
}

// Archived maps use the same object framing and preserve manual views during
// time scrubbing/regeneration. Selecting another archive explicitly clears it.
{
  const {map,images}=fixture();
  const record=snapshot(8,'AAAA',{pose:{x:40,y:0,stamp:30},recording_start_stamp:10,
    grid:{...snapshot(8).grid,width:1000,height:1000,origin:{x:-50,y:-50}},
    path:[{x:0,y:0,stamp:10,t_s:0},{x:2,y:0,stamp:20,t_s:10},{x:40,y:0,stamp:30,t_s:20}]});
  map.until=0;map.set(record);images[0].onload();
  assert.equal(map.bounds.maxX,1.5,'future route and final pose do not affect the current archive cursor');
  map.until=10;map.draw();assert.equal(map.bounds.maxX,3);
  map.zoom('in');const manual=values(map.view);
  map.until=20;map.draw();assert.deepEqual(values(map.view),manual);
  const regenerated={...record,version:9,grid:{...record.grid,image_base64:'BBBB',occupied_bounds:{minX:-20,minY:-20,maxX:20,maxY:20}}};
  map.set(regenerated);images[1].onload();assert.deepEqual(values(map.view),manual);
  map.set({});assert.equal(map.auto,true);
  map.set(snapshot(1,'CCCC'));images[2].onload();assert.equal(map.auto,true);
  assert.notDeepEqual(values(map.view),manual);
}

// Automatic framing both expands and contracts with nearby objects. Decode a
// generation only once even when metadata exists. A frame reset is distinct
// from ordinary geometry updates and returns the map to automatic framing.
{
  const {map,images,reads}=fixture();
  const first=snapshot(1,'AAAA',{coordinate_frame:'odom',pose:{x:0,y:0},
    grid:{...snapshot(1).grid,width:1000,height:1000,origin:{x:-50,y:-50}}});
  map.set(first);images[0].onload();const scale=map.view.scale;
  map.set({...first,version:2,grid:{...first.grid,image_base64:'BBBB',occupied_bounds:{minX:-20,minY:-10,maxX:20,maxY:10}}});
  images[1].onload();assert.ok(map.view.scale<scale);
  map.set({...first,version:3,grid:{...first.grid,image_base64:'CCCC'}});
  images[2].onload();assert.equal(map.view.scale,scale);assert.equal(reads.length,3);
  map.zoom('in');assert.equal(map.auto,false);
  map.set({...first,version:4,coordinate_frame:'new_odom',grid:{...first.grid,image_base64:'CCCC'}});
  assert.equal(map.auto,true);
  map.until=-1;map.set({...first,version:5,pose:{x:40,y:0},path:[{x:40,y:0,t_s:0,stamp:0}]});
  images[3].onload();assert.equal(map.bounds.maxX,1.5,'no archive cursor pose must not substitute the final pose');
}

// Old archives carry the verified palette but no bounds. Inspect their decoded
// pixels once; metadata-only updates and all user redraws reuse that extent.
{
  const {map,images,reads,observers}=fixture();
  const grid={width:3,height:2,resolution:.5,origin:{x:-2,y:4},encoding:'png',row_order:'min_y_first',image_base64:'AAAA'};
  const first=snapshot(1,'AAAA',{grid,pose:{x:-1,y:4.5,stamp:1},path:[]});
  map.set(first);
  Object.assign(images[0],{naturalWidth:3,naturalHeight:2,
    pixels:new Uint8ClampedArray([245,25,127,127,245,25].flatMap(value=>[value,value,value,255]))});
  images[0].onload();
  assert.deepEqual(values(map.occupiedCells.bounds),{minX:-1.5,minY:4,maxX:-.5,maxY:5});
  assert.deepEqual(values(map.bounds),{minX:-2.5,minY:3,maxX:.5,maxY:6});
  assert.equal(reads.length,1);
  map.set(withoutPng(first));map.zoom('in');observers[0].notify();map.draw();
  map.set({...first,version:2,active:false});assert.equal(reads.length,1);
  map.set({...first,version:3,grid:{...grid,image_base64:'BBBB'}});
  Object.assign(images[1],{naturalWidth:3,naturalHeight:2,
    pixels:new Uint8ClampedArray([245,245,127,127,245,127].flatMap(value=>[value,value,value,255]))});
  images[1].onload();assert.equal(reads.length,2);assert.equal(map.occupiedCells.bounds,null);
  map.zoom('fit');assert.deepEqual(values(map.bounds),{minX:-3,minY:2.5,maxX:1,maxY:6.5});
  const malformed={...first,version:4,grid:{...grid,image_base64:'CCCC'}};
  map.set(malformed);Object.assign(images[2],{naturalWidth:3,naturalHeight:2,pixels:new Uint8ClampedArray(24)});
  images[2].onload();assert.equal(map.occupiedCells,undefined);
  assert.deepEqual(values(map.bounds),{minX:-13,minY:-7.5,maxX:11,maxY:16.5},'unsupported palettes use a local window without claiming to identify objects');
  map.zoom('full');assert.deepEqual(values(map.bounds),{minX:-3,minY:3,maxX:.5,maxY:6});
}

// Nearby Auto and Full are explicit display choices. Distant cells and the
// complete path stay in the rendered source; neither is classified as noise.
{
  const {map,images,reads,commands}=fixture();
  const grid={width:80,height:40,resolution:1,origin:{x:-20.5,y:-20.5},encoding:'png',row_order:'min_y_first',
    image_base64:'AAAA',occupied_bounds:{minX:-.5,minY:-.5,maxX:40.5,maxY:.5}};
  const path=[{x:-100,y:-100,stamp:0},{x:0,y:0,stamp:1}];
  const first=snapshot(1,'AAAA',{grid,path,pose:{x:0,y:0,stamp:1}});
  map.set(first);
  const pixels=new Uint8ClampedArray(80*40*4).fill(245);for(let i=3;i<pixels.length;i+=4)pixels[i]=255;
  for(const column of [20,60])pixels.set([25,25,25,255],(20*80+column)*4);
  images[0].pixels=pixels;images[0].onload();
  assert.deepEqual(values(map.bounds),{minX:-1.5,minY:-1.5,maxX:1.5,maxY:1.5});
  assert.equal(map.occupiedCells.indices.length,2);assert.equal(map.snapshot.path,path);
  assert.equal(commands.filter(([name])=>name==='drawImage').at(-1)[4],grid.width*grid.resolution*map.view.scale,'draw the whole raster, not an altered crop');
  const localScale=map.view.scale;
  map.set(withoutPng({...first,pose:{x:40,y:0,stamp:2}}));
  assert.deepEqual(values(map.bounds),{minX:38.5,minY:-1.5,maxX:41.5,maxY:1.5});
  assert.equal(reads.length,1,'pose changes reuse the complete occupied index');
  map.zoom('in');const manual=values(map.view);
  map.set(withoutPng({...first,pose:{x:0,y:0,stamp:3}}));assert.deepEqual(values(map.view),manual);
  map.zoom('full');assert.equal(map.fitMode,'full');assert.equal(map.auto,true);
  assert.deepEqual(values(map.bounds),{minX:-21.5,minY:-21.5,maxX:60.5,maxY:20.5});
  assert.ok(map.view.scale<localScale);
  const grown={...first,version:2,grid:{...grid,width:100,image_base64:'BBBB'}};
  map.set(grown);images[1].onload();assert.equal(map.fitMode,'full');assert.equal(map.bounds.maxX,80.5);
  map.zoom('fit');assert.equal(map.fitMode,'nearby');assert.equal(map.auto,true);
  assert.ok(map.bounds.maxX<15);assert.equal(map.snapshot.grid.width,100);
}

// Equal byte lengths and repeated version numbers are not pixel identities.
// An older load or error must never overwrite a newer accepted generation.
{
  const {map, images, draws} = fixture();
  map.set(snapshot(1)); images[0].onload();
  map.set(snapshot(2, 'BBBB')); const old = images[1];
  const latest = snapshot(2, 'CCCC'); map.set(latest); const recent = images[2];
  const before = draws.length; old.onload(); old.onerror();
  assert.equal(draws.length, before); assert.equal(map.bitmap, images[0]);
  recent.onload(); old.onload();
  assert.equal(draws.length, before + 1); assert.equal(map.snapshot, latest);
  assert.equal(map.bitmap, recent);
  map.zoom('in'); assert.equal(map.auto,false);
  const restarted = snapshot(0, 'DDDD', {scan_count:3}); map.set(restarted);
  assert.equal(map.snapshot, latest); assert.equal(map.auto,false); images[3].onload();
  assert.equal(map.snapshot, restarted); assert.equal(map.imageVersion, 0);
  assert.equal(map.auto,true,'a restarted map epoch resets only when its pixels are accepted');
}

// A paused response reuses identical pixels. New poses on PNG-less responses
// update a matching accepted/pending generation, never an unrelated one.
{
  const {map, images, draws} = fixture();
  const first = snapshot(1); map.set(first); images[0].onload();
  const moved = withoutPng(snapshot(1, 'AAAA', {pose:{x:4, y:1, stamp:4}}));
  map.set(moved); assert.equal(map.snapshot, moved); assert.equal(images.length, 1);
  const paused = snapshot(2, 'AAAA', {active:false}); map.set(paused);
  assert.equal(map.snapshot, paused); assert.equal(images.length, 1);
  map.set(snapshot(3, 'BBBB')); const before = draws.length;
  const pendingPaused = withoutPng(snapshot(3, 'BBBB', {active:false, pose:{x:6,y:0,stamp:6}}));
  map.set(pendingPaused); map.set(withoutPng(paused));
  assert.equal(draws.length, before); assert.equal(map.snapshot, paused);
  images[1].onload();
  assert.equal(map.snapshot, pendingPaused); assert.equal(map.snapshot.active, false);
  assert.equal(draws.length, before + 1);
}

// Decode failure retains the complete previous view and requests full pixels
// again even if a restarted producer reused its version number.
{
  const {map, images, draws} = fixture();
  const first = snapshot(1); map.set(first); images[0].onload();
  const failed = snapshot(1, 'BBBB'); map.set(failed); const before = draws.length;
  images[1].onerror();
  assert.equal(draws.length, before); assert.equal(map.snapshot, first);
  assert.equal(map.bitmap, images[0]); assert.equal(map.imageVersion, undefined);
  map.set(withoutPng(first)); // A response already in flight cannot cancel retry.
  assert.equal(map.imageVersion, undefined); assert.equal(draws.length, before);
  map.set(failed); assert.equal(images.length, 3); images[2].onload();
  assert.equal(map.snapshot, failed); assert.equal(map.imageVersion, 1);
  map.set(withoutPng(snapshot(9, 'XXXX')));
  assert.equal(map.snapshot, failed); assert.equal(map.imageVersion, undefined);
}

// Reset and archive selection cancel in-flight work immediately. A later old
// completion cannot restore the cleared map; a new survey can start normally.
{
  const {map, images, draws} = fixture();
  map.set(snapshot(1)); images[0].onload(); map.set(snapshot(2, 'BBBB'));
  map.zoom('in');assert.equal(map.auto,false);
  const reset = snapshot(3, 'EEEE', {scan_count:0, path:[], pose:null, active:false});
  map.set(reset); const before = draws.length;
  assert.equal(map.snapshot, reset); assert.equal(map.bitmap, null); assert.equal(images.length, 2);
  assert.equal(map.auto,true);
  images[1].onload(); images[1].onerror(); assert.equal(draws.length, before);
  map.set(snapshot(4, 'FFFF')); images[2].onload(); assert.equal(map.bitmap, images[2]);
  map.set(snapshot(5, 'GGGG')); map.set({}); images[3].onload();
  assert.equal(map.bitmap, null); assert.equal(map.snapshot.grid, undefined);
}

// Explicit view changes still redraw the accepted map while another decode
// is pending. Schematic GPS views never wait for a bitmap.
{
  const {map, images, draws, observers, events} = fixture();
  const first = snapshot(1); map.set(first); images[0].onload(); map.set(snapshot(2, 'BBBB'));
  const before = draws.length;
  map.zoom('in'); observers[0].notify(); map.draw(); // existing theme callback
  events.pointerdown({clientX:10, clientY:10, pointerId:1});
  events.pointermove({clientX:15, clientY:13}); events.pointerup();
  assert.equal(draws.length, before + 4);
  assert.ok(draws.slice(before).every(draw => draw.snapshot === first && draw.bitmap === images[0]));
  const gps = fixture(true), value = snapshot(1);
  gps.map.set(value); assert.equal(gps.images.length, 0); assert.equal(gps.map.snapshot, value);
  assert.equal(gps.draws.length, 1);
}

// Schematic GPS keeps its orchard layout in automatic mode, but a changed pose
// or enlarged layout cannot overwrite the operator's selected zoom and pan.
{
  const {map,events,rect,observers}=fixture(true);
  const world={spawn:[0,0,0],row_y:[-2,2,6],row_x_range:[0,30]};
  map.set({pose:{x:0,y:0},world});
  assert.deepEqual(values(map.bounds),model.orchardLayout(world).bounds);
  map.zoom('in');events.pointerdown({clientX:0,clientY:0,pointerId:1});
  events.pointermove({clientX:20,clientY:10});events.pointerup();const manual=values(map.view);
  map.set({pose:{x:100,y:100},world:{...world,row_x_range:[0,200]}});
  rect.width=500;observers[0].notify();assert.deepEqual(values(map.view),manual);
  assert.equal(map.auto,false);map.zoom('fit');assert.equal(map.auto,true);
  assert.ok(map.bounds.maxX>=206&&map.bounds.maxY>=102);
}

// Archive submaps have unrelated local origins. Crossing an uncertain gap
// keeps the selected pixels/metre while recentering in the new local frame.
{
  const {map,images}=fixture();
  map.set(snapshot(1,'AAAA',{map_epoch:'archive',coordinate_frame:'isolated_0',pose:{x:4,y:2,yaw:0}}));images[0].onload();
  map.zoom('in');const scale=map.view.scale;
  map.commit(snapshot(20,'AAAA',{map_epoch:'archive',coordinate_frame:'isolated_1',pose:{x:0,y:0,yaw:0},preserve_view_on_seek:true}));
  assert.equal(map.auto,false);assert.equal(map.view.scale,scale);assert.equal(map.view.x,0);assert.equal(map.view.y,0);
  map.commit(snapshot(5,'AAAA',{map_epoch:'archive',coordinate_frame:'isolated_0',pose:{x:2,y:1,yaw:0},preserve_view_on_seek:true}));
  assert.equal(map.auto,false);assert.equal(map.view.scale,scale);
  map.commit(snapshot(1,'AAAA',{map_epoch:'different-recording',coordinate_frame:'isolated_0',preserve_view_on_seek:true}));
  assert.equal(map.auto,true,'a different recording still gets its own initial view');
}

console.log('PASS: hardware metric map coherent pixels, zoom preservation, nearby fit and fading path');
