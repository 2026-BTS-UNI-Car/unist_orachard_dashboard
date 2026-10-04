import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
const calls=[],timers=[],elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{addEventListener(){},value:30,disabled:false,textContent:''});return elements.get(id);};
class MetricMap {
  constructor(){this.snapshot={};this.tileEpoch=null;}
  set(value){this.snapshot=value;this.tileEpoch=value.tile_epoch;return Promise.resolve();}
  draw(){}zoom(){}
}
let fail=false;
const context=vm.createContext({MetricMap,ArchiveMapBuffer:class{},AbortController,AbortSignal,URLSearchParams,
 location:{pathname:'/other'},document:{hidden:false,getElementById:element,querySelectorAll:()=>[],addEventListener(){}},
 window:{addEventListener(){}},performance,Date,setInterval(){},clearTimeout(){},
 setTimeout:(callback,delay)=>{timers.push({callback,delay});return timers.length;},
 fetch:async(path,options)=>{
  calls.push({path,signal:options.signal});
  if(path.includes('map-window'))return new Promise((resolve,reject)=>{
    options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})),{once:true});
  });
  if(fail)throw new Error('temporary offline');
  const time=Number(new URLSearchParams(path.split('?')[1]).get('t_s')||0);
  return {ok:true,json:async()=>({ok:true,grid:{},scan_count:1,version:time+1,frame_t_s:time,tile_epoch:'a',tiles:[]})};
 }});
const source=fs.readFileSync(new URL('../static/live-map.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');
vm.runInContext(source,context);
await Promise.race([vm.runInContext("selectArchive('example')",context),new Promise((_,reject)=>setTimeout(()=>reject(new Error('bulk window blocked first frame')),250))]);
assert.ok(calls[0].path.includes('/map?'));assert.ok(calls[1].path.includes('/map-window?'));
assert.equal(vm.runInContext('archive.snapshot.frame_t_s',context),0);
const old=calls[1];vm.runInContext("archiveTime('example',15,false)",context);
assert.equal(old.signal.aborted,true);
await vm.runInContext("pollArchive('example',archiveEpoch)",context);
assert.equal(vm.runInContext('archive.snapshot.frame_t_s',context),15);
assert.ok(calls.some(c=>c.path.includes('t_s=15.000')));
// A network error on a paused seek retries instead of leaving an empty map.
fail=true;context.location.pathname='/rover-control';
vm.runInContext("archiveTime('example',20,false)",context);
const timer=timers.at(-1);await timer.callback();
await new Promise(resolve=>setImmediate(resolve));
assert.equal(vm.runInContext('archive.snapshot.frame_t_s',context),15);
assert.ok(timers.at(-1).delay>=500);assert.match(element('liveArchiveStatus').textContent,/재시도/);
console.log('Current frame precedes prefetch, stale seek requests abort, paused failures retry while retaining the last map');
