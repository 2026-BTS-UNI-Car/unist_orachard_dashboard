// A busy read slot (409 mapping_read_capacity_busy) or autonomy priority
// (503 deferred) is shown as a wait and retried with backoff, never as an error.
import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
const timers=[],elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{addEventListener(){},value:30,disabled:false,textContent:''});return elements.get(id);};
class MetricMap{constructor(){this.snapshot={};this.tileEpoch=null;}set(v){this.snapshot=v;return Promise.resolve();}draw(){}zoom(){}}
let reply;
const context=vm.createContext({MetricMap,ArchiveMapBuffer:class{},ArchiveSegmentGallery:class{reset(){}update(){}},decodeArchiveWindow:async v=>v,renderJobs(){},
  LIVE_MAP_TIMEOUT_MS:3000,livePollDelay:()=>1000,livePathQuery:()=>({}),acceptLiveResponse:()=>true,
  AbortController,AbortSignal,URLSearchParams,location:{pathname:'/records'},
  document:{hidden:false,getElementById:element,querySelectorAll:()=>[],addEventListener(){}},
  window:{addEventListener(){}},performance,Date,setInterval(){},clearTimeout(){},console,
  setTimeout:(callback,delay)=>{timers.push({callback,delay});return timers.length;},
  fetch:async path=>path.includes('/api/live/recordings/')?reply():{ok:true,status:200,headers:{get:()=>null},json:async()=>({ok:true,map_epoch:'L',tiles:[]})}});
const source=fs.readFileSync(new URL('../static/live-map.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');
vm.runInContext(source,context);
const status=()=>element('liveArchiveStatus').textContent;
// 503 autonomy priority with Retry-After
reply=()=>({ok:false,status:503,headers:{get:k=>k==='Retry-After'?'5':null},json:async()=>({ok:false,error:'autonomy_priority',deferred:true,retry_after_s:5})});
await vm.runInContext("selectArchive('rec')",context);
assert.match(status(),/자율주행 우선/);assert.doesNotMatch(status(),/autonomy_priority/);
assert.ok(timers.at(-1).delay>=5000,'retry after the server hint');
// 409 busy read slot
reply=()=>({ok:false,status:409,headers:{get:()=>null},json:async()=>({ok:false,error:'mapping_read_capacity_busy'})});
timers.length=0;vm.runInContext('archiveTimer=null;scheduleArchive(0)',context);
await timers.at(-1).callback();await new Promise(r=>setImmediate(r));
assert.match(status(),/지도 조회 대기 중/);
const first=timers.at(-1).delay;assert.ok(first>=500);
timers.at(-1).callback();await new Promise(r=>setImmediate(r));
assert.ok(timers.at(-1).delay>first,'backoff grows');
// A real error is still reported as an error.
reply=()=>({ok:false,status:400,headers:{get:()=>null},json:async()=>({ok:false,error:'invalid_archive_route'})});
vm.runInContext('archiveTimer=null;scheduleArchive(0)',context);
timers.at(-1).callback();await new Promise(r=>setImmediate(r));
assert.match(status(),/invalid_archive_route/);
console.log('archive deferred read tests passed');
