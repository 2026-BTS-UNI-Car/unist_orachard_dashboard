import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {ArchiveMapBuffer} from '../static/archive-map-buffer.mjs';
const elements=new Map(),requests=[],timers=new Map();let counter=0,testNow=0,holdWindow=false,finishWindow;
const element=id=>elements.get(id)||elements.set(id,{events:{},value:30,
  addEventListener(event,callback){this.events[event]=callback;}}).get(id);
class MetricMap {constructor(){this.snapshot={};}async set(s){this.snapshot=s;this.tileEpoch=s.tile_epoch;}draw(){}zoom(){}}
const seed={ok:true,playback_driven:true,state:'saved',version:0,scan_count:0,tiles:[],
 map_epoch:'epoch',generation:{stage:'complete',processed_t_s:30,duration_s:30}};
const events=Array.from({length:30},(_,i)=>({ok:true,playback_driven:true,map_epoch:'epoch',tile_epoch:'epoch:0',
 tile_version:i+1,version:i+1,scan_count:i+1,frame_t_s:(i+1)/10,tiles:[],tiles_reset:i===0,path:[]}));
const context=vm.createContext({MetricMap,ArchiveMapBuffer,ArchiveSegmentGallery:class{reset(){}},
 decodeArchiveWindow:async value=>value,AbortController,AbortSignal,URLSearchParams,performance:{now:()=>testNow},Date,
 crypto:{randomUUID:()=> 'test'},location:{pathname:'/other'},
 document:{hidden:false,getElementById:element,querySelectorAll:()=>[],addEventListener(){}},
 window:{addEventListener(){}},setInterval(){},setTimeout(callback,delay){timers.set(++counter,{callback,delay});return counter;},clearTimeout(id){timers.delete(id);},
 fetch:async(path,options)=>{
  requests.push({path,body:options.body});
  if(path.includes('/map-window') && holdWindow)await new Promise(resolve=>{finishWindow=resolve;});
  const value=path.includes('/map-window')?{ok:true,available:true,complete:true,start_s:0,end_s:3,duration_s:30,seed,events}:
   path.includes('/recordings/')?seed:{};
  return {ok:true,json:async()=>value};
 }});
const run=code=>vm.runInContext(code,context);
vm.runInContext(fs.readFileSync(new URL('../static/live-map.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,''),context);
await run("selectArchive('record')");
assert.equal(run('immutableCursorMap'),true,'complete empty seed reports its history state');
run("archiveTime('record',.1,true)");
await run("pollArchive('record',archiveEpoch)");await new Promise(resolve=>setImmediate(resolve));
assert.ok(requests.some(r=>r.path.includes('/map-window')));
assert.ok(!requests.some(r=>r.path.endsWith('/map-playback')),'completed map does not repeatedly drive a localization worker');
const before=requests.filter(r=>r.path.includes('/map?')).length;
run("archiveTime('record',.5,true)");await run("pollArchive('record',archiveEpoch)");
assert.equal(run('archive.snapshot.frame_t_s'),.5);
assert.equal(requests.filter(r=>r.path.includes('/map?')).length,before,'cached original-rate scans draw without another current-frame HTTP response');
run("archiveTime('record',.2,false,true)");await run("pollArchive('record',archiveEpoch)");
assert.equal(run('archive.snapshot.frame_t_s'),.2,'backward seek excludes future frames');
assert.equal(timers.size,0,'paused cached playback stops timers and future downloads');
seed.state='regenerating';seed.generation.stage='drawing';seed.generation.duration_s=60;
await run("selectArchive('partial')");
assert.equal(run('immutableCursorMap'),false);
assert.equal(run('bufferedCursorMap'),true,'already calculated prefix can be reused before the entire recording finishes');
run("archiveTime('partial',1,true)");await new Promise(resolve=>setImmediate(resolve));
const control=requests.findLast(r=>r.path.endsWith('/map-playback'));
assert.equal(JSON.parse(control.body).playing,true);
assert.equal(JSON.parse(control.body).transport,undefined,'cached cells need only a light ordered worker control');
testNow=300;run("archiveTime('partial',31,true)");await new Promise(resolve=>setImmediate(resolve));
assert.equal(run('bufferedCursorMap'),false,'crossing the processed prefix returns to causal generation');
assert.equal(JSON.parse(requests.findLast(r=>r.path.endsWith('/map-playback')).body).transport,'tiles');
holdWindow=true;await run("selectArchive('slow')");
run("archiveTime('slow',1,true)");await run("pollArchive('slow',archiveEpoch)");
await new Promise(resolve=>setImmediate(resolve));
assert.ok(requests.findLast(r=>r.path.includes('/map-window')).path.includes('span_s=12'),'startup window must survive a slow download');
const pendingGets=requests.filter(r=>r.path.includes('/map?')).length;
run("archiveTime('slow',2,true)");await run("pollArchive('slow',archiveEpoch)");
assert.equal(requests.filter(r=>r.path.includes('/map?')).length,pendingGets,'current-map GETs must not starve a pending cell window');
holdWindow=false;finishWindow();await new Promise(resolve=>setImmediate(resolve));
console.log('Complete causal maps use locally timed cells; new/unfinished maps retain causal worker control');
