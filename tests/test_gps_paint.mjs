import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import * as model from '../static/gps-map-model.mjs';

const original=readFileSync(new URL('../static/gps-map.js',import.meta.url),'utf8');
const source=original.replace(/^import .*;\r?\n/gm,'')+'\nwindow.__testViews={GpsMapView,live,archive};';
const fix=(latitude=35,longitude=129,extra={})=>({valid:true,latitude,longitude,h_acc_m:.1,
 speed_mps:1,course_deg:30,received_at:100,fix_type:'autonomous',...extra});

function harness(){
 let nextId=0;const raf=new Map(),timers=new Map(),elements=new Map();
 const node=id=>{
  if(elements.has(id))return elements.get(id);
  const listeners=new Map(),classes=new Map(),styles=new Map();
  const element={id,hidden:false,textContent:'',dataset:{},attributes:new Map(),listeners,classes,styles,
   classList:{toggle(name,value){classes.set(name,!!value);}},
   style:{setProperty(name,value){styles.set(name,value);}},
   setAttribute(name,value){this.attributes.set(name,value);},
   addEventListener(name,callback){listeners.set(name,callback);},
   dispatch(name){listeners.get(name)?.({});}};
  elements.set(id,element);return element;
 };
 class FakeMap{
  constructor(options={}){this.options=options;this.jumps=[];this.sources=new Map();this.handlers=new Map();this.layers=[];}
  getSource(id){return this.sources.get(id);}
  addSource(id,source){this.sources.set(id,{initial:source.data,data:[],setData(value){this.data.push(value);}});}
  addLayer(layer){this.layers.push(layer);}
  jumpTo(value){this.jumps.push(structuredClone(value));}
  addControl(){} on(name,callback){this.handlers.set(name,callback);} resize(){} setStyle(){}
 }
 class FakeMarker{
  constructor(options={}){this.options=options;this.points=[];this.adds=0;this.removes=0;}
  setLngLat(point){this.points.push([...point]);return this;}
  addTo(){this.adds++;return this;} remove(){this.removes++;return this;}
 }
 const context={...model,console,Number,Math,Infinity,Map,Set,Promise,performance:{now:()=>0},
  document:{hidden:false,visibilityState:'visible',documentElement:{dataset:{theme:'light'}},
   fonts:{add(){}},getElementById:node,createElement:()=>node('created-'+nextId++),addEventListener(){}},
  location:{pathname:'/private-test',protocol:'https:',origin:'https://private.test',host:'private.test'},
  maplibregl:{addProtocol(){},Map:FakeMap,Marker:FakeMarker,NavigationControl:class{},ScaleControl:class{}},
  MutationObserver:class{observe(){}},ResizeObserver:class{observe(){}},
  FontFace:class{async load(){return this;}},
  fetch:async path=>({ok:true,json:async()=>path==='/api/maps/status'?{ok:true,bytes:0}:{sources:{basemap:{}}}}),
  requestAnimationFrame:callback=>{const id=++nextId;raf.set(id,callback);return id;},
  cancelAnimationFrame:id=>raf.delete(id),
  setTimeout:(callback,delay)=>{const id=++nextId;timers.set(id,{callback,delay});return id;},
  clearTimeout:id=>timers.delete(id),setInterval(){},
  WebSocket:class{constructor(){throw Error('Test never authorizes live socket');}}
 };
 context.window=context;context.window.pmtiles={Protocol:class{tile(){}}};context.window.addEventListener=()=>{};
 vm.createContext(context);vm.runInContext(source,context,{filename:'candidate/static/gps-map.js'});
 const attach=view=>{
  view.map=new FakeMap();view.marker=new FakeMarker();view.markerElement=node(view.prefix+'Marker');view.addOverlays();return view;
 };
 const create=()=>attach(new context.__testViews.GpsMapView('test',true));
 const flushRaf=()=>{const callbacks=[...raf.values()];raf.clear();for(const callback of callbacks)callback(10);};
 const flushTimer=()=>{const callbacks=[...timers.values()];timers.clear();for(const item of callbacks)item.callback();};
 return {context,node,raf,timers,attach,create,flushRaf,flushTimer};
}

let passed=0;
const test=async(name,body)=>{await body();passed++;console.log('PASS '+name);};

await test('unchanged fix/accuracy and changing receipt metadata do not resend GeoJSON or re-add marker',()=>{
 const h=harness(),view=h.create(),frames=[{t_s:1,sample:fix()}];
 view.update(fix(),frames,1);h.flushRaf();
 for(let i=0;i<100;i++){view.update(fix(35,129,{received_at:100+i}),frames,1);h.flushRaf();}
 assert.equal(view.marker.adds,1);assert.equal(view.marker.points.length,1);assert.equal(view.map.jumps.length,1);
 assert.equal(view.map.getSource('gps-accuracy').data.length,1);
 assert.equal(view.map.getSource('gps-track').data.length,1);
});

await test('1000 queued fixes retain one paint and apply only newest position',()=>{
 const h=harness(),view=h.create();
 for(let i=0;i<1000;i++)view.update(fix(35+i/100000,129),[],Infinity);
 assert.equal(h.raf.size,1);assert.equal(h.timers.size,1);
 assert.equal(view.marker.points.length,0);
 h.flushRaf();assert.equal(h.raf.size,0);assert.equal(h.timers.size,0);
 assert.deepEqual(view.marker.points,[[129,35.00999]]);assert.equal(view.marker.adds,1);
});

await test('real movement updates marker/circle/path while keeping marker attached once',()=>{
 const h=harness(),view=h.create(),frames=[{t_s:1,sample:fix()}];
 view.update(frames[0].sample,frames,1);h.flushRaf();
 frames.push({t_s:2,sample:fix(35.001,129.002)});view.update(frames[1].sample,frames,2);h.flushRaf();
 assert.equal(view.marker.points.length,2);assert.equal(view.marker.adds,1);
 assert.equal(view.map.getSource('gps-accuracy').data.length,2);
 const latest=view.map.getSource('gps-track').data.at(-1);
 assert.deepEqual(latest.features[0].geometry.coordinates,[[129,35],[129.002,35.001]]);
 assert.deepEqual(view.map.jumps.at(-1).center,[129.002,35.001]);
});

await test('accuracy-only revision changes circle without moving/re-adding marker or track',()=>{
 const h=harness(),view=h.create(),frames=[{t_s:1,sample:fix()}];
 view.update(fix(),frames,1);h.flushRaf();view.update(fix(35,129,{h_acc_m:.5}),frames,1);h.flushRaf();
 const source=view.map.getSource('gps-accuracy');assert.equal(source.data.length,2);
 assert.deepEqual(source.data.at(-1),model.accuracyCircle([129,35],.5));
 assert.equal(view.marker.points.length,1);assert.equal(view.marker.adds,1);
 assert.equal(view.map.getSource('gps-track').data.length,1);
});

await test('course-only change updates heading without resending unchanged geometry',()=>{
 const h=harness(),view=h.create(),frames=[{t_s:1,sample:fix()}];
 view.update(fix(),frames,1);h.flushRaf();view.update(fix(35,129,{course_deg:90}),frames,1);h.flushRaf();
 assert.equal(view.markerElement.styles.get('--gps-course'),'90deg');
 assert.equal(view.markerElement.classes.get('has-course'),true);
 assert.equal(view.marker.adds,1);assert.equal(view.marker.points.length,1);
 assert.equal(view.map.getSource('gps-accuracy').data.length,1);
 assert.equal(view.map.getSource('gps-track').data.length,1);
});

await test('replaced history array changes corrected middle geometry even with unchanged endpoints/timestamps',()=>{
 const h=harness(),view=h.create();
 const frames=[{t_s:1,sample:fix()},{t_s:2,sample:fix(35.001,129.001)},{t_s:3,sample:fix(35.002,129.002)}];
 view.update(frames[2].sample,frames,3);h.flushRaf();
 const replacement=[frames[0],{t_s:2,sample:fix(35.01,129.01)},frames[2]];
 view.update(frames[2].sample,replacement,3);h.flushRaf();
 assert.equal(view.map.getSource('gps-track').data.length,2);
 assert.deepEqual(view.map.getSource('gps-track').data.at(-1).features[0].geometry.coordinates[1],[129.01,35.01]);
 assert.equal(view.marker.points.length,1);assert.equal(view.map.getSource('gps-accuracy').data.length,1);
});

await test('stale repeated readings preserve last position and clear accuracy once',()=>{
 const h=harness(),view=h.create(),frames=[];
 view.update(fix(),frames);h.flushRaf();
 for(let i=0;i<20;i++){view.update(fix(35,129,{valid:false,state:'stale',received_at:200+i}),frames);h.flushRaf();}
 assert.equal(view.marker.adds,1);assert.equal(view.marker.removes,0);assert.equal(view.marker.points.length,1);
 assert.equal(view.markerElement.classes.get('stale'),true);
 assert.equal(view.markerElement.classes.get('has-course'),false);
 assert.equal(view.map.getSource('gps-accuracy').data.length,2);
 assert.deepEqual(view.map.getSource('gps-accuracy').data.at(-1),{type:'FeatureCollection',features:[]});
});

await test('drag before pending paint releases follow; explicit recenter uses latest position',()=>{
 const h=harness(),view=h.create();view.update(fix(),[]);
 h.node('testMap').dispatch('pointerdown');h.flushRaf();assert.equal(view.model.follow,false);
 assert.equal(view.map.jumps.length,0);
 view.update(fix(35.002,129.003),[]);h.flushRaf();assert.equal(view.map.jumps.length,0);
 h.node('testFollow').dispatch('click');assert.equal(view.model.follow,true);
 assert.deepEqual(view.map.jumps.at(-1).center,[129.003,35.002]);
 view.update(fix(35.002,129.003),[]);h.flushRaf();assert.equal(view.map.jumps.length,1);
});

await test('focus/visibility changes do not arm follow or change desired replay position',()=>{
 const h=harness(),view=h.create();view.model.userPan();view.update(fix(),[]);
 h.context.document.hidden=true;h.context.document.visibilityState='hidden';h.flushTimer();
 assert.equal(view.model.follow,false);assert.equal(view.map.jumps.length,0);
 assert.deepEqual(view.marker.points.at(-1),[129,35]);
 h.context.document.hidden=false;h.context.document.visibilityState='visible';
 view.update(fix(35.003,129.004),[]);h.flushRaf();assert.equal(view.map.jumps.length,0);
});

await test('fallback timer paints once and a later old rAF cannot paint again',()=>{
 const h=harness(),view=h.create();view.update(fix(),[]);const oldRaf=[...h.raf.values()][0];
 assert.equal([...h.timers.values()][0].delay,100);h.flushTimer();oldRaf(100);
 assert.equal(view.marker.adds,1);assert.equal(view.map.getSource('gps-accuracy').data.length,1);
 assert.equal(h.raf.size,0);assert.equal(h.timers.size,0);
});

await test('reset invalidates already-dispatched old paint and a new record remains latest',()=>{
 const h=harness(),view=h.create();view.update(fix(),[]);const old=[...h.raf.values()][0];
 view.reset();view.update(fix(36,130),[]);old(10);assert.equal(view.marker.points.length,0);
 h.flushRaf();assert.deepEqual(view.marker.points,[[130,36]]);assert.equal(view.marker.adds,1);
});

await test('style reload reapplies overlays once with current pose and no marker re-attachment',()=>{
 const h=harness(),view=h.create(),frames=[{t_s:1,sample:fix()}];view.update(fix(),frames,1);h.flushRaf();
 view.addOverlays();view.requestPaint();h.flushRaf();
 assert.equal(view.marker.adds,1);assert.equal(view.marker.points.length,1);
 assert.equal(view.map.getSource('gps-accuracy').data.length,1);
 assert.equal(view.map.getSource('gps-track').data.length,1);
});

await test('real archive hook Pause cursor and future cache frames retain exact chosen fix',()=>{
 const h=harness(),view=h.attach(h.context.__testViews.archive);
 const frames=[{t_s:1,sample:fix()},{t_s:2,sample:fix(35.001,129.002)},{t_s:10,sample:fix(36,130)}];
 h.context.RoverGps.archive(frames,2,'first',true);h.flushRaf();
 assert.deepEqual(view.marker.points.at(-1),[129.002,35.001]);
 for(let i=0;i<20;i++)h.context.RoverGps.archive(frames,2,'first',true);h.flushRaf();
 assert.equal(view.marker.points.length,1);assert.equal(view.marker.adds,1);
 assert.equal(view.map.getSource('gps-accuracy').data.length,1);
 h.context.RoverGps.archive(frames,1,'first',true);h.flushRaf();
 assert.deepEqual(view.marker.points.at(-1),[129,35]);
 assert.equal(view.until,1);
});

await test('archive missing/stale frame removes marker once and new valid record cannot resurrect older callback',()=>{
 const h=harness(),view=h.attach(h.context.__testViews.archive),frames=[{t_s:1,sample:fix()}];
 h.context.RoverGps.archive(frames,1,'first',true);h.flushRaf();
 h.context.RoverGps.archive(frames,5,'first',true);h.flushRaf();
 h.context.RoverGps.archive(frames,5,'first',true);h.flushRaf();assert.equal(view.marker.removes,1);
 const next=[{t_s:1,sample:fix(36,130)}];h.context.RoverGps.archive(next,1,'next',true);h.flushRaf();
 assert.deepEqual(view.marker.points.at(-1),[130,36]);assert.equal(view.marker.adds,2);
});

await test('updates before asynchronous map initialization are held as newest state until style load',async()=>{
 const h=harness(),view=new h.context.__testViews.GpsMapView('new',true);
 view.update(fix(),[]);view.update(fix(36,130),[]);
 assert.equal(view.paintTask,null);await view.init();
 view.map.handlers.get('style.load')();h.flushRaf();
 assert.deepEqual(view.marker.points,[[130,36]]);assert.equal(view.marker.adds,1);
});

await test('replay times within the same visible GPS prefix do not resend track; next fix and rewind do',()=>{
 const h=harness(),view=h.create(),frames=[
  {t_s:1,sample:fix()},{t_s:2,sample:fix(35.001,129.001)},{t_s:3,sample:fix(35.002,129.002)}];
 const source=view.map.getSource('gps-track');
 view.update(frames[1].sample,frames,2);h.flushRaf();assert.equal(source.data.length,1);
 for(let i=1;i<60;i++){view.update(frames[1].sample,frames,2+i/60);h.flushRaf();}
 assert.equal(source.data.length,1);
 view.update(frames[2].sample,frames,3);h.flushRaf();assert.equal(source.data.length,2);
 assert.deepEqual(source.data.at(-1),model.pathFeatures(frames,3));
 view.update(frames[1].sample,frames,2.1);h.flushRaf();assert.equal(source.data.length,3);
 assert.deepEqual(source.data.at(-1),model.pathFeatures(frames,2.1));
 view.update(frames[1].sample,frames,2.9);h.flushRaf();assert.equal(source.data.length,3);
});

await test('in-place intermediate coordinate correction is detected with identical array, endpoints and cutoff',()=>{
 const h=harness(),view=h.create(),frames=[
  {t_s:1,sample:fix()},{t_s:2,sample:fix(35.001,129.001)},{t_s:3,sample:fix(35.002,129.002)}];
 const source=view.map.getSource('gps-track');
 view.update(frames[2].sample,frames,3);h.flushRaf();
 frames[1].sample.longitude=129.1;
 view.update(frames[2].sample,frames,3);h.flushRaf();assert.equal(source.data.length,2);
 assert.deepEqual(source.data.at(-1),model.pathFeatures(frames,3));
 assert.equal(source.data.at(-1).features[0].geometry.coordinates[1][0],129.1);
});

await test('invalid-fix and timestamp gap corrections retain exact path break behavior',()=>{
 const h=harness(),view=h.create(),frames=[
  {t_s:1,sample:fix()},{t_s:2,sample:fix(35.001,129.001)},
  {t_s:8,sample:fix(35.002,129.002)},{t_s:9,sample:fix(35.003,129.003)}];
 const source=view.map.getSource('gps-track');
 view.update(frames[3].sample,frames,Infinity);h.flushRaf();assert.equal(source.data.at(-1).features.length,2);
 frames[1].sample.valid=false;
 view.update(frames[3].sample,frames,Infinity);h.flushRaf();assert.equal(source.data.length,2);
 assert.deepEqual(source.data.at(-1),model.pathFeatures(frames,Infinity));
 frames[1].sample.valid=true;frames[2].t_s=7;
 view.update(frames[3].sample,frames,Infinity);h.flushRaf();assert.equal(source.data.length,3);
 assert.deepEqual(source.data.at(-1),model.pathFeatures(frames,Infinity));
 assert.equal(source.data.at(-1).features.length,1);
});

await test('unsorted input preserves the original first-over-cutoff break rather than a sorted binary prefix',()=>{
 const h=harness(),view=h.create(),frames=[
  {t_s:1,sample:fix()},{t_s:4,sample:fix(35.004,129.004)},
  {t_s:2,sample:fix(35.002,129.002)},{t_s:3,sample:fix(35.003,129.003)},
  {t_s:7,sample:fix(35.007,129.007)}];
 const source=view.map.getSource('gps-track');
 for(const cutoff of [2.5,3.5,4.5,4.9,3.2,7,Infinity,NaN]){
  view.update(fix(),frames,cutoff);h.flushRaf();
  assert.deepEqual(source.data.at(-1),model.pathFeatures(frames,cutoff));
 }
 assert.equal(source.data.length,4);
});

await test('same geometry replacement and future-only corrections do not resend until visible',()=>{
 const h=harness(),view=h.create(),frames=[
  {t_s:1,sample:fix()},{t_s:2,sample:fix(35.001,129.001)},{t_s:3,sample:fix(35.002,129.002)}];
 const source=view.map.getSource('gps-track');
 view.update(frames[1].sample,frames,2);h.flushRaf();
 const replaced=structuredClone(frames);
 view.update(replaced[1].sample,replaced,2.1);h.flushRaf();assert.equal(source.data.length,1);
 replaced[2].sample.longitude=129.2;
 view.update(replaced[1].sample,replaced,2.9);h.flushRaf();assert.equal(source.data.length,1);
 view.update(replaced[2].sample,replaced,3);h.flushRaf();assert.equal(source.data.length,2);
 assert.deepEqual(source.data.at(-1),model.pathFeatures(replaced,3));
});

await test('unchanged empty geometry does not resend on cutoff changes but first actual line does',()=>{
 const h=harness(),view=h.create(),frames=[
  {t_s:1,sample:fix()},{t_s:2,sample:fix(35.001,129.001)}];
 const source=view.map.getSource('gps-track');
 for(const cutoff of [0,.5,1,1.1,1.9]){view.update(fix(),frames,cutoff);h.flushRaf();}
 assert.equal(source.data.length,1);assert.deepEqual(source.data.at(-1),model.pathFeatures(frames,1.9));
 view.update(frames[1].sample,frames,2);h.flushRaf();assert.equal(source.data.length,2);
});

console.log(`${passed} GPS production-module regression tests passed`);
