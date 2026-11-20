// 2026-11-21: the map control generation of a recording that already has map cells.
// Such a recording answers a map reading with a wrapped reply ({map_frame, seed, events}); the generation is in the
// seed. The page read it from the wrapper only, never learned it, and every play of a recording whose map work the
// server had ended (15 s after a pause, or after its player left) was refused with mapping_control_revoked_refresh.
import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
const elements=new Map(),calls=[],timers=new Map();let timerId=0,testNow=1000;
let current='generation-1',held=null;
const element=id=>{if(!elements.has(id))elements.set(id,{events:{},value:30,addEventListener(event,callback){this.events[event]=callback;}});return elements.get(id);};
class MetricMap{constructor(){this.snapshot={};}set(value){this.snapshot=value;this.tileEpoch=value.tile_epoch;return Promise.resolve();}draw(){}zoom(){}}
const json={get:()=>'application/json'};
const seed=()=>({ok:true,playback_driven:true,state:'regenerating',scan_count:3,version:3,map_epoch:'epoch',tile_epoch:'epoch:0',tile_version:3,
  frame_t_s:0,tiles:[],mapping_generation:current,generation:{processed_t_s:0,waiting_for_cursor:true}});
const context=vm.createContext({MetricMap,ArchiveMapBuffer:class{},ArchiveSegmentGallery:class{reset(){}},decodeArchiveWindow:async value=>value,
 AbortController,AbortSignal,URLSearchParams,performance:{now:()=>testNow},Date,crypto:{randomUUID:()=> 'test-browser'},location:{pathname:'/rover-control'},IntersectionObserver:class{observe(){}},
 document:{hidden:false,getElementById:element,querySelectorAll:()=>[],addEventListener(){}},
 window:{addEventListener(){},setArchiveTime(t,options){calls.push({seek:t,options});}},
 setInterval(){},clearTimeout(id){timers.delete(id);},setTimeout(callback,delay){timers.set(++timerId,{callback,delay});return timerId;},
 fetch:async(path,options)=>{
  const body=options.body?JSON.parse(options.body):undefined;
  calls.push({path,body});
  if(path.includes('/map-playback')){
   // The server's rule for a recording whose map work it has ended: only the current generation with the resume flag.
   if(body.mapping_generation!==current)return {ok:false,status:409,headers:json,json:async()=>({ok:false,error:'mapping_control_revoked_refresh'})};
   if(!body.resume_mapping&&body.playing)return {ok:false,status:409,headers:json,json:async()=>({ok:false,error:'mapping_stopped_press_play_to_resume'})};
   return {ok:true,status:200,headers:json,json:async()=>({ok:true,playback_driven:true,processed_t_s:0,mapping_generation:current})};
  }
  if(path.includes('/held/')&&held)await held.promise;
  // What the server sends for a recording with map cells and the tiles transport: the status wrapped as a window.
  const value=path.includes('/held/')?{...seed(),mapping_generation:'generation-of-the-held-recording'}:seed();
  return {ok:true,status:200,headers:json,json:async()=>({ok:true,map_frame:true,seed:value,events:[],cell_codec:'xor2-v1'})};
 }});
vm.runInContext(fs.readFileSync(new URL('../static/live-map.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,''),context);
const run=code=>vm.runInContext(code,context);
const settle=async()=>{for(let i=0;i<6;i+=1)await new Promise(resolve=>setImmediate(resolve));};
const fire=async()=>{for(const [id,timer] of [...timers]){timers.delete(id);timer.callback();}await settle();};
const pushes=()=>calls.filter(c=>c.path?.endsWith('/map-playback'));

// 1. Choosing the recording: the generation is taken from the seed of the wrapped reply.
await run("selectArchive('record')");await settle();
assert.equal(run('archiveMappingGeneration'),'generation-1');
run("archiveTime('record',.1,true)");await settle();
assert.equal(pushes().at(-1).body.mapping_generation,'generation-1');
assert.equal(pushes().at(-1).body.resume_mapping,true);
assert.equal(run('window.LiveMap.waitReason()'),'');

// 2. Paused for more than 15 s: the server ended the map work and made a new generation. The next play is refused
//    once with the old generation, the reading that follows brings the new one, and the retry resumes the map.
testNow+=500;run("archiveTime('record',.6,false)");await settle();
current='generation-2';
testNow+=20000;run("archiveTime('record',.6,true)");await settle();
assert.equal(pushes().at(-1).body.mapping_generation,'generation-1');
assert.equal(pushes().at(-1).body.resume_mapping,true);
assert.equal(run('archivePlaying'),true,'the recording keeps playing');
assert.ok(!calls.some(c=>c.options?.pause));
assert.equal(element('liveArchiveStatus').textContent,'지도 만들기가 다른 곳에서 바뀌었습니다. 재생을 다시 누르면 이어서 그립니다.');
await fire();
assert.equal(run('archiveMappingGeneration'),'generation-2','the reading after the refusal brings the current generation');
const before=pushes().length;
testNow+=1000;run("archiveTime('record',1.6,true)");await settle();
assert.equal(pushes().length,before,'the retry waits its three seconds');
testNow+=2200;run("archiveTime('record',3.8,true)");await settle();
assert.equal(pushes().length,before+1);
assert.equal(pushes().at(-1).body.mapping_generation,'generation-2');
assert.equal(pushes().at(-1).body.resume_mapping,true,'the play that was refused is still the explicit resume');
assert.equal(run('window.LiveMap.waitReason()'),'','the map is being made again');

// 3. A late reading of a recording that is no longer selected must not bring its generation to the selected one.
held={};held.promise=new Promise(resolve=>{held.release=resolve;});
const first=run("selectArchive('held/first')");await settle();
await run("selectArchive('record2')");await settle();
assert.equal(run('archiveMappingGeneration'),'generation-2');
held.release();await first;await settle();
assert.equal(run('archiveMappingGeneration'),'generation-2','the earlier recording answered late: ignored');
console.log('The map control generation is read from the seed of a wrapped reply; a refused play resumes the map by itself');
