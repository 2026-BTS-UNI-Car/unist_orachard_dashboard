// 2026-11-21: a refused map control (another recording's map is being made, another device controls this recording,
// the map was stopped) does not pause the recording. The player keeps playing, the reason stays in the map's status
// line, and the request is tried again after a wait instead of in a tight loop.
import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
const elements=new Map(),calls=[],timers=new Map();let timerId=0,testNow=1000,refusal=null,broken=false;
const element=id=>{if(!elements.has(id))elements.set(id,{events:{},value:30,addEventListener(event,callback){this.events[event]=callback;}});return elements.get(id);};
class MetricMap{constructor(){this.snapshot={};}set(value){this.snapshot=value;this.tileEpoch=value.tile_epoch;return Promise.resolve();}draw(){}zoom(){}}
const json={get:()=>'application/json'};
const context=vm.createContext({MetricMap,ArchiveMapBuffer:class{},ArchiveSegmentGallery:class{reset(){}},decodeArchiveWindow:async value=>value,
 AbortController,AbortSignal,URLSearchParams,performance:{now:()=>testNow},Date,crypto:{randomUUID:()=> 'test-browser'},location:{pathname:'/other'},
 document:{hidden:false,getElementById:element,querySelectorAll:()=>[],addEventListener(){}},
 window:{addEventListener(){},setArchiveTime(t,options){calls.push({seek:t,options});}},
 setInterval(){},clearTimeout(id){timers.delete(id);},setTimeout(callback,delay){timers.set(++timerId,{callback,delay});return timerId;},
 fetch:async(path,options)=>{
  calls.push({path,body:options.body?JSON.parse(options.body):undefined});
  if(path.includes('/map-playback')){
   if(broken)return {ok:false,status:502,headers:{get:()=>'text/html'},json:async()=>{throw new Error('html');}};
   if(refusal)return {ok:false,status:409,headers:json,json:async()=>({ok:false,error:refusal})};
   return {ok:true,status:200,headers:json,json:async()=>({ok:true,playback_driven:true,processed_t_s:0,mapping_generation:'generation-2'})};
  }
  return {ok:true,status:200,headers:json,json:async()=>({ok:true,playback_driven:true,state:'regenerating',scan_count:0,version:0,map_epoch:'epoch',
   mapping_generation:'generation-2',tiles:[],generation:{processed_t_s:0,waiting_for_cursor:true}})};
 }});
vm.runInContext(fs.readFileSync(new URL('../static/live-map.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,''),context);
const run=code=>vm.runInContext(code,context);
const settle=async()=>{for(let i=0;i<6;i+=1)await new Promise(resolve=>setImmediate(resolve));};
const pushes=()=>calls.filter(c=>c.path?.endsWith('/map-playback'));
const status=()=>element('liveArchiveStatus').textContent;

await run("selectArchive('second')");await settle();
// 1. Another recording's map is still being made: the first play of this recording is refused.
refusal='another_recording_mapping_active_stop_it_first';
run("archiveTime('second',.1,true)");await settle();
assert.equal(pushes().length,1);
assert.ok(!calls.some(c=>c.options?.pause),'the player is not paused');
assert.equal(run('archivePlaying'),true,'the map module still knows that the recording is playing');
assert.equal(status(),'다른 기록의 지도를 만드는 중입니다. 그 작업이 끝나면 이 기록의 지도를 이어서 그립니다.');
assert.equal(run('window.LiveMap.waitReason()'),'지도는 잠시 멈춰 있습니다');
// 2. The playback goes on; the refused request is not repeated in a tight loop.
for(const t of [.4,.8,1.2,2.0]){testNow+=400;run(`archiveTime('second',${t},true)`);await settle();}
assert.equal(pushes().length,1,'no retry inside the wait');
assert.equal(status(),'다른 기록의 지도를 만드는 중입니다. 그 작업이 끝나면 이 기록의 지도를 이어서 그립니다.','a map reading in between does not hide the reason');
// 3. After the wait it is tried again; the other recording's work has ended meanwhile.
refusal=null;testNow+=2000;run("archiveTime('second',4,true)");await settle();
assert.equal(pushes().length,2);
assert.equal(pushes().at(-1).body.playing,true);assert.equal(pushes().at(-1).body.t_s,4);
assert.equal(run('window.LiveMap.waitReason()'),'','the note goes when the map control is accepted');
assert.notEqual(status(),'다른 기록의 지도를 만드는 중입니다. 그 작업이 끝나면 이 기록의 지도를 이어서 그립니다.');
assert.ok(!calls.some(c=>c.options?.pause));

// 4. A map stopped in the job list stays stopped: the retries do not carry the resume flag; pause and play do.
refusal='mapping_stopped_press_play_to_resume';
testNow+=400;run("archiveTime('second',4.4,true)");await settle();
const stopped=pushes().length;
assert.equal(pushes().at(-1).body.resume_mapping,false);
assert.equal(status(),'이 기록의 지도 만들기가 멈춰 있습니다. 재생을 다시 누르면 이어서 그립니다.');
assert.equal(run('archivePlaying'),true);
testNow+=3100;run("archiveTime('second',7.5,true)");await settle();
assert.equal(pushes().length,stopped+1,'one retry after the wait');
assert.equal(pushes().at(-1).body.resume_mapping,false,'a retry is not an explicit resume');
testNow+=100;run("archiveTime('second',7.6,false)");await settle();
assert.equal(pushes().at(-1).body.playing,false,'the pause is sent at once');
assert.equal(status(),'이 기록의 지도 만들기가 멈춰 있습니다. 재생을 다시 누르면 이어서 그립니다.','a stopped map says so while paused as well');
assert.ok(!calls.some(c=>c.options?.pause));
refusal=null;testNow+=100;run("archiveTime('second',7.6,true)");await settle();
assert.equal(pushes().at(-1).body.playing,true);
assert.equal(pushes().at(-1).body.resume_mapping,true,'pressing play again is the explicit resume and goes out at once');
assert.equal(pushes().at(-1).body.mapping_generation,'generation-2');

// 5. A request that fails on the way (a tunnel error page) is retried after a second, not at once.
broken=true;testNow+=400;run("archiveTime('second',8,true)");await settle();
const failed=pushes().length;
for(const t of [8.2,8.4,8.6]){testNow+=200;run(`archiveTime('second',${t},true)`);await settle();}
assert.equal(pushes().length,failed,'no retry inside one second');
assert.ok(!calls.some(c=>c.options?.pause));
broken=false;testNow+=600;run("archiveTime('second',9.4,true)");await settle();
assert.equal(pushes().length,failed+1);

// 6. Choosing another recording clears the note and the wait.
refusal='another_device_controls_this_recording';
testNow+=400;run("archiveTime('second',9.8,true)");await settle();
assert.equal(status(),'다른 기기가 이 기록의 지도를 만들고 있습니다. 영상과 센서는 그대로 재생됩니다.');
refusal=null;await run("selectArchive('third')");await settle();
assert.equal(run('window.LiveMap.waitReason()'),'');
const before=pushes().length;run("archiveTime('third',.1,true)");await settle();
assert.equal(pushes().length,before+1,'the first play of the next recording is not held by the earlier wait');
console.log('A refused map control keeps the recording playing, shows its reason and is retried after a wait');
