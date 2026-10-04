import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
const elements=new Map(),calls=[],timers=[];
const element=id=>{if(!elements.has(id))elements.set(id,{events:{},value:30,addEventListener(event,callback){this.events[event]=callback;}});return elements.get(id);};
class MetricMap{constructor(){this.snapshot={};}set(value){this.snapshot=value;this.tileEpoch=value.tile_epoch;return Promise.resolve();}draw(){}zoom(){}}
const context=vm.createContext({MetricMap,ArchiveMapBuffer:class{},ArchiveSegmentGallery:class{reset(){}},decodeArchiveWindow:async value=>value,
 AbortController,AbortSignal,URLSearchParams,performance,Date,location:{pathname:'/other'},
 document:{hidden:false,getElementById:element,querySelectorAll:()=>[],addEventListener(){}},
 window:{addEventListener(){},setArchiveTime(t,options){calls.push({seek:t,options});}},
 setInterval(){},clearTimeout(){},setTimeout(callback,delay){timers.push({callback,delay});return timers.length;},
 fetch:async(path,options)=>{
  calls.push({path,body:options.body?JSON.parse(options.body):undefined});
  return {ok:true,json:async()=>path.endsWith('/new-map')?{ok:true,map_epoch:'new'}:
   path.includes('/map-playback')?{ok:true,playback_driven:true}:
   {ok:true,playback_driven:true,state:'regenerating',scan_count:0,version:0,map_epoch:'new',tiles:[],generation:{processed_t_s:0}}};
 }});
vm.runInContext(fs.readFileSync(new URL('../static/live-map.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,''),context);
await vm.runInContext("selectArchive('record')",context);
assert.equal(element('liveArchiveRegenerate').disabled,false,'causal worker must allow user to make a new map');
await element('liveArchiveRegenerate').events.click();
assert.equal(calls.filter(c=>c.path?.endsWith('/new-map')).length,1);
assert.ok(calls.some(c=>c.seek===0&&c.options.pause));
assert.equal(vm.runInContext('archive.snapshot.scan_count',context),0);
assert.ok(!calls.some(c=>c.path?.endsWith('/regenerate')));
assert.ok(!calls.some(c=>c.path?.includes('/map-window')),'causal map must not prefetch future cells');
vm.runInContext("archiveTime('record',.1,true)",context);
await new Promise(resolve=>setImmediate(resolve));
assert.ok(calls.some(c=>c.path?.endsWith('/map-playback')&&c.body.playing&&c.body.t_s===.1));
vm.runInContext("archive.snapshot={map_epoch:'new',frame_t_s:.1,scan_count:5};archiveTime('record',.08,true)",context);
assert.equal(vm.runInContext('archive.snapshot.scan_count',context),5,'media-clock jitter must retain drawn cells');
assert.equal(vm.runInContext('archiveTimeS',context),.1,'running cursor must not rewind implicitly');
vm.runInContext("archiveTime('record',.05,true,true)",context);
assert.equal(vm.runInContext('archive.snapshot.scan_count',context),0,'explicit backward seek removes future cells');
assert.equal(vm.runInContext('archiveTimeS',context),.05);
vm.runInContext("archiveTime('record',.15,false)",context);
await new Promise(resolve=>setImmediate(resolve));
assert.ok(calls.some(c=>c.path?.endsWith('/map-playback')&&c.body.playing===false&&c.body.t_s===.15));
assert.equal(vm.runInContext('archive.snapshot.map_epoch',context),'new');
console.log('New map clears only derived view; play/pause sends causal cursor, no future map prefetch');
