// Live map polling: path increments (S02), link-adaptive interval and 3 s
// timeout (S08), and late responses after New map/Stop (S17).
import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
import {mergeLivePath,livePathQuery,livePollDelay,acceptLiveResponse,LIVE_MAP_TIMEOUT_MS} from '../static/live-map-delta.mjs';

// --- pure helpers -----------------------------------------------------------
{
  const full=Array.from({length:50},(_,i)=>({x:i/10,y:0,yaw:0,stamp:1000+i/10}));
  let shown={path:[],path_token:'e:0'};
  for(let n=1;n<=full.length;n++){
    // server: resend from the client's last point
    const have=shown.path.length,from=have?have-1:null;
    const value=from===null?{path:full.slice(0,n),path_token:'e:0',path_count:n}:
      {path:full.slice(from,n),path_from:from,path_token:'e:0',path_count:n};
    const merged=mergeLivePath(shown,value);
    assert.equal(merged.ok,true);
    shown={...value,path:merged.path};
    assert.deepEqual(shown.path,full.slice(0,n),'increment equals full path');
  }
  // Same result as applying full responses.
  assert.deepEqual(mergeLivePath({path:[1,2]},{path:full}).path,full);
  // Token mismatch, tiles reset, a gap or a wrong count keep the previous path and request a full path.
  const prev={path:full.slice(0,10),path_token:'e:0'};
  for(const bad of [{path_from:9,path:full.slice(9,12),path_token:'e:1',path_count:12},
    {path_from:9,path:full.slice(9,12),path_token:'e:0',path_count:12,tiles_reset:true},
    {path_from:11,path:full.slice(11,12),path_token:'e:0',path_count:12},
    {path_from:9,path:full.slice(9,12),path_token:'e:0',path_count:13}]){
    const merged=mergeLivePath(prev,bad);assert.equal(merged.ok,false);assert.equal(merged.path,prev.path);
  }
  // Earlier contract (archive windows) is unchanged.
  assert.deepEqual(mergeLivePath({path:[1]},{path:[2],path_append:true}).path,[1,2]);
  assert.deepEqual(mergeLivePath({path:[1]},{path:[2],path_append:true,tiles_reset:true}).path,[2]);
  // Query: only when a token and a finite last stamp exist and no full path is pending.
  assert.deepEqual(livePathQuery({path_token:'e:0',path:[{stamp:1.25},{stamp:1000.123456789}]}),
    {path_token:'e:0',path_count:'2',path_stamp:'1000.123456789'});
  assert.deepEqual(livePathQuery({path_token:'e:0',path:[{stamp:1}]},true),{});
  assert.deepEqual(livePathQuery({path:[{stamp:1}]}),{});
  assert.deepEqual(livePathQuery({path_token:'e:0',path:[]}),{});
  // Poll interval = max(profile floor, 1.5 x RTT), measured start to start.
  assert.equal(LIVE_MAP_TIMEOUT_MS,3000);
  assert.equal(livePollDelay({profile:'detail',drawing:true,rttMs:20}),80);
  assert.equal(livePollDelay({profile:'balanced',drawing:true,rttMs:40}),60);
  assert.equal(livePollDelay({profile:'slow',drawing:true,rttMs:100}),400);
  assert.equal(livePollDelay({profile:'tiny',drawing:true,rttMs:100}),1900);
  assert.equal(livePollDelay({profile:'balanced',drawing:true,rttMs:1000}),500,'slow link: 1.5 x RTT');
  assert.equal(livePollDelay({profile:'balanced',drawing:false,rttMs:50}),950,'not drawing: 1 s');
  assert.equal(livePollDelay({profile:'balanced',drawing:true,rttMs:50,autonomyActive:true}),950,'autonomy priority: >= 1 s');
  assert.equal(livePollDelay({profile:'unknown',drawing:true,rttMs:0}),100);
  for(const profile of ['detail','balanced','slow','tiny'])for(const rtt of [0,10,90,300,2500]){
    const delay=livePollDelay({profile,drawing:true,rttMs:rtt});
    assert.ok(delay+rtt>=Math.max({detail:100,balanced:100,slow:500,tiny:2000}[profile],1.5*rtt)-1,`${profile} ${rtt}`);
  }
  // Late/stale responses.
  assert.equal(acceptLiveResponse({requestActionSeq:1,currentActionSeq:2,value:{},shown:{}}),false);
  assert.equal(acceptLiveResponse({requestActionSeq:2,currentActionSeq:2,actionBusy:true,value:{},shown:{}}),false);
  assert.equal(acceptLiveResponse({requestActionSeq:2,currentActionSeq:2,value:{map_epoch:'a',version:3},shown:{map_epoch:'a',version:5}}),false);
  assert.equal(acceptLiveResponse({requestActionSeq:2,currentActionSeq:2,value:{map_epoch:'b',version:0},shown:{map_epoch:'a',version:5}}),true);
  assert.equal(acceptLiveResponse({requestActionSeq:2,currentActionSeq:2,value:{map_epoch:'a',version:6},shown:{map_epoch:'a',version:5}}),true);
}

// --- live-map.js poll loop in a VM -----------------------------------------
{
  const elements=new Map(),timers=[],calls=[],pending=[];
  const element=id=>{if(!elements.has(id))elements.set(id,{id,listeners:{},addEventListener(type,fn){this.listeners[type]=fn;},value:30,disabled:false,textContent:''});return elements.get(id);};
  class MetricMap{
    constructor(){this.snapshot={};this.tileEpoch=null;}
    set(value){const merged=mergeLivePath(this.snapshot,value);this.pathNeedsFull=!merged.ok;this.snapshot={...value,path:merged.path};this.tileEpoch=value.tile_epoch;return Promise.resolve();}
    draw(){}zoom(){}
  }
  const timeouts=[];
  const signalTimeout=ms=>{timeouts.push(ms);return new AbortController().signal;};
  const context=vm.createContext({MetricMap,mergeLivePath,livePathQuery,livePollDelay,acceptLiveResponse,LIVE_MAP_TIMEOUT_MS,
    ArchiveMapBuffer:class{},ArchiveSegmentGallery:class{reset(){}},decodeArchiveWindow:async v=>v,renderJobs(){},AbortController,
    AbortSignal:{timeout:signalTimeout,any:signals=>signals[0]},URLSearchParams,
    location:{pathname:'/rover-control'},document:{hidden:false,getElementById:element,querySelectorAll:()=>[],addEventListener(){},activeElement:null},
    window:{addEventListener(){},RoverLink:{profile:()=>'balanced'}},performance,Date,setInterval(){},clearTimeout(){},console,
    setTimeout:(callback,delay)=>{timers.push({callback,delay});return timers.length;},
    fetch:(path,options)=>{calls.push(path);return new Promise(resolve=>pending.push({path,resolve}));}});
  const source=fs.readFileSync(new URL('../static/live-map.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');
  vm.runInContext(source,context);
  const respond=(index,value)=>pending[index].resolve({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>value});
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  await flush();
  const mapCalls=()=>calls.filter(p=>p.startsWith('/api/live/map?'));
  assert.equal(mapCalls().length,1,'route start polls once');
  assert.ok(timeouts.includes(3000),'live map poll uses the 3 s timeout');
  const p1=pending.findIndex(p=>p.path.startsWith('/api/live/map?'));
  const path=[{x:0,y:0,yaw:0,stamp:10},{x:1,y:0,yaw:0,stamp:10.5}];
  respond(p1,{ok:true,map_epoch:'A',tile_epoch:'A:0:0',version:5,tile_version:5,active:true,tiles:[],tiles_reset:true,path,path_token:'A:0',path_count:2});
  await flush();await flush();
  assert.equal(vm.runInContext('live.snapshot.version',context),5);
  const delay=timers.at(-1).delay;assert.ok(delay>=0&&delay<=100,'drawing on a fast link keeps ~100 ms polling');
  // Next poll names the last held point.
  timers.at(-1).callback();await flush();
  const q=new URLSearchParams(mapCalls().at(-1).split('?')[1]);
  assert.equal(q.get('path_token'),'A:0');assert.equal(q.get('path_count'),'2');assert.equal(q.get('path_stamp'),'10.5');
  const p2=pending.length-1;
  // A New map action starts while that poll is in flight; its response then arrives late.
  element('liveMapReset').listeners.click();await flush();
  const action=pending.length-1;assert.ok(pending[action].path.endsWith('/api/live/map/reset'));
  respond(action,{ok:true,map_epoch:'B',tile_epoch:'B:0:0',version:0,tile_version:0,active:false,tiles:[],tiles_reset:true,path:[],path_token:'B:0',path_count:0});
  await flush();await flush();
  assert.equal(vm.runInContext('live.snapshot.map_epoch',context),'B');
  respond(p2,{ok:true,map_epoch:'A',tile_epoch:'A:0:0',version:6,tile_version:6,active:true,tiles:[],tiles_reset:false,
    path:[path[1],{x:2,y:0,yaw:0,stamp:11}],path_from:1,path_token:'A:0',path_count:3});
  await flush();await flush();
  assert.equal(vm.runInContext('live.snapshot.map_epoch',context),'B','late poll after New map is discarded');
  // The next poll applies normally again.
  timers.at(-1).callback();await flush();
  respond(pending.length-1,{ok:true,map_epoch:'B',tile_epoch:'B:0:0',version:1,tile_version:1,active:false,tiles:[],tiles_reset:false,path:[],path_token:'B:0',path_count:0});
  await flush();await flush();
  assert.equal(vm.runInContext('live.snapshot.version',context),1);
  assert.ok(timers.at(-1).delay>=800,'not drawing: about 1 s between polls');
  // /api/autonomy runs separately and never blocks the map poll.
  assert.ok(calls.some(p=>p==='/api/autonomy'));
}
console.log('live map delta tests passed');
