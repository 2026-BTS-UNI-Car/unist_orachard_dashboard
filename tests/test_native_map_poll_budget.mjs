import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {performance as realPerformance} from 'node:perf_hooks';
import {NativeAcceptedMapClient} from '../static/native-accepted-map.mjs';

const began=realPerformance.now();
const flags=Object.fromEntries(['runtime_authority','motor_command_sent','autonomy_output_enabled',
  'execution_eligible','physical_accuracy_verified','live_input_eligible'].map(k=>[k,false]));
const waiting=()=>({ok:true,available:false,status:{},...flags});
function page(index=0,count=1) {
  const token='c'.repeat(32);
  return {ok:true,available:true,...flags,status:{map:{active:true,pose_continuity:true}},page:{
    ...flags,schema:'ubts.native.accepted_map.tiles.v1',storage:'ternary_native_evidence_v1',
    retained_historical_generation:true,tile_size:64,tiles_reset:true,tile_version:1,
    tile_epoch:'a'.repeat(64),generation_id:'b'.repeat(64),lease_id:token,
    page_index:index,page_count:count,tile_count:count,
    tiles:[{x:index,y:0,encoding:'rle2-v1',cells:[0,4095,2,1]}],
    grid:{encoding:'tiles-cells-v1',row_order:'min_y_first',resolution:.05,width:128,height:64,origin:{x:0,y:0}},
    pose:{x:0,y:0,yaw:0},cell_count:count,context:{},path:[],path_vertex_limit:256,
    path_representation:'accepted_render_pose_vertices_uniform_thinning',path_compressions:0,
    next_cursor:index+1===count?null:`${token}:${index+1}`}};
}
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return{resolve,promise};};
async function virtual(body) {
  const originalPerformance=Object.getOwnPropertyDescriptor(globalThis,'performance');
  const originalSet=globalThis.setTimeout,originalClear=globalThis.clearTimeout;
  const clock={now:0},timers=new Map();let serial=0;
  Object.defineProperty(globalThis,'performance',{configurable:true,value:{now:()=>clock.now}});
  globalThis.setTimeout=(fn,ms)=>{const id=++serial;timers.set(id,{fn,ms,at:clock.now+ms});return id;};
  globalThis.clearTimeout=id=>timers.delete(id);
  const fire=id=>{const timer=timers.get(id);assert.ok(timer,'owned timer exists');timers.delete(id);
    clock.now=Math.max(clock.now,timer.at);return timer.fn();};
  try {await body({clock,fire,timers,delay:c=>timers.get(c.timer)?.ms});}
  finally {globalThis.setTimeout=originalSet;globalThis.clearTimeout=originalClear;
    if(originalPerformance)Object.defineProperty(globalThis,'performance',originalPerformance);
    else delete globalThis.performance;}
}
const tests=[];
const test=(name,fn)=>tests.push([name,fn]);

test('only two timing edits reverse to a verified original Git checkout body',async()=>{
  const current=await readFile(new URL('../static/native-accepted-map.mjs',import.meta.url),'utf8');
  const eol=current.includes('\r\n')?'\r\n':'\n';
  const a=['    this.busy=true;const pollStarted=performance.now();',
    '    const revision=this.revision,abort=new AbortController();this.controller=abort;'].join(eol);
  const b='      if(this.running)this.schedule(Math.max(0,this.pollMs-(performance.now()-pollStarted)));}';
  assert.equal(current.split(a).length,2);assert.equal(current.split(b).length,2);
  const original=current.replace(a,'    this.busy=true;const revision=this.revision,abort=new AbortController();this.controller=abort;')
    .replace(b,'      if(this.running)this.schedule(this.pollMs);}');
  // Exact reviewed LF Git blob and its exact Windows CRLF checkout body.
  // Mixed endings or any unrelated source change fail this inverse proof.
  const originalSHA=createHash('sha256').update(original).digest('hex');
  assert.ok(new Set(['92c00ff13bd0db685f50b76284cacb8280cf280cac7d217984985d3ac151688a',
    '9f4932f8a4212455d3eee883018db1ab8971886396a6462c870ae5606c3e7160']).has(originalSHA));
});
test('real async start-poll-request uses 500ms including 120ms work',()=>virtual(async({clock,fire,delay})=>{
  let calls=0;const c=new NativeAcceptedMapClient({request:async()=>{calls++;clock.now+=120;return waiting();},commit:async()=>{}});
  c.start();await fire(c.timer);assert.equal(calls,1);assert.equal(delay(c),380);assert.equal(c.pollMs,500);c.stop();
}));
test('overrun schedules one next poll without catch-up requests',()=>virtual(async({clock,fire,delay})=>{
  let calls=0;const c=new NativeAcceptedMapClient({request:async()=>{clock.now+=(++calls===1?650:10);return waiting();},commit:async()=>{}});
  c.start();await fire(c.timer);assert.equal(calls,1);assert.equal(delay(c),0);
  await fire(c.timer);assert.equal(calls,2);assert.equal(delay(c),490);c.stop();
}));
test('both pages rendering and lease release consume the original period',()=>virtual(async({clock,fire,delay})=>{
  let pages=0,commits=0,releases=0;const c=new NativeAcceptedMapClient({
    request:async path=>{if(path.includes('release?')){releases++;clock.now+=40;return{ok:true};}
      clock.now+=(pages===0?100:130);return page(pages++,2);},
    commit:async value=>{await Promise.resolve();commits++;assert.equal(value.tiles.length,2);clock.now+=120;}});
  c.start();await fire(c.timer);assert.equal(commits,1);assert.equal(pages,2);assert.equal(releases,1);
  assert.equal(delay(c),110);assert.equal(c.last.tiles.length,2);c.stop();
}));
test('error keeps last good image and cannot become immediate busy spin',()=>virtual(async({clock,fire,delay})=>{
  const last={marker:'last_good'},notices=[];const c=new NativeAcceptedMapClient({request:async()=>{clock.now+=80;throw Error('fixture');},
    commit:async()=>assert.fail('no incomplete commit'),status:v=>notices.push(v)});c.last=last;
  c.start();await fire(c.timer);assert.equal(c.last,last);assert.equal(delay(c),420);
  assert.equal(notices.at(-1).state,'unavailable');c.stop();
}));
test('pending real Promise blocks a concurrent caller',()=>virtual(async({clock,fire,delay})=>{
  const pending=deferred();let calls=0;const c=new NativeAcceptedMapClient({request:()=>{calls++;return pending.promise;},commit:async()=>{}});
  c.start();const first=fire(c.timer);await Promise.resolve();await c.poll();assert.equal(calls,1);
  clock.now=150;pending.resolve(waiting());await first;assert.equal(delay(c),350);c.stop();
}));
test('Stop aborts slow fetch and prevents new timer or old display mutation',()=>virtual(async({clock,fire,timers})=>{
  const pending=deferred();let signal,commits=0;const c=new NativeAcceptedMapClient({request:(_path,s)=>{signal=s;return pending.promise;},commit:async()=>{commits++;}});
  const last={marker:'last_good'};c.last=last;c.start();const first=fire(c.timer);await Promise.resolve();
  c.stop();assert.equal(signal.aborted,true);clock.now=900;pending.resolve(waiting());await first;
  assert.equal(commits,0);assert.equal(c.last,last);assert.equal(c.timer,null);assert.equal(timers.size,0);
}));
test('Stop-Start fences obsolete full reply and new owner remains single-flight',()=>virtual(async({clock,fire,delay})=>{
  const pending=deferred();let calls=0,commits=0,releases=0;
  const c=new NativeAcceptedMapClient({request:async path=>{if(path.includes('release?')){releases++;return{ok:true};}
    calls++;return calls===1?pending.promise:page();},commit:async()=>{commits++;}});
  c.start();const first=fire(c.timer);await Promise.resolve();c.stop();c.start();await fire(c.timer);
  assert.equal(calls,1);clock.now=100;pending.resolve(page());await first;
  assert.equal(commits,0);assert.equal(releases,1);assert.equal(delay(c),400);
  await fire(c.timer);assert.equal(commits,1);assert.equal(calls,2);assert.equal(releases,2);c.stop();
}));
test('malformed authority page remains rejected with the same error path',()=>virtual(async({fire,delay})=>{
  let commits=0;const c=new NativeAcceptedMapClient({request:async path=>{if(path.includes('release?'))return{ok:true};
    const value=page();value.page.physical_accuracy_verified=true;return value;},commit:async()=>{commits++;}});
  c.start();await fire(c.timer);assert.equal(commits,0);assert.equal(c.last,null);assert.equal(delay(c),500);c.stop();
}));

let passed=0;
for(const [name,fn] of tests){try{await fn();passed++;}catch(error){console.error(JSON.stringify({ok:false,name,reason:String(error)}));throw error;}}
console.log(JSON.stringify({ok:true,synthetic_behavior_tests:passed,wall_ms:realPerformance.now()-began,
  original_period_ms:500,new_concurrent_polls:0,installed_UI_body_verified:false,live_latency_gain_measured:false}));
