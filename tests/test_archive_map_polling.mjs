import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const elements=new Map();
const element=id=>elements.get(id)||elements.set(id,{textContent:'',value:0,disabled:false,addEventListener(){}}).get(id);
const timers=new Map();let sequence=0;
const requests=[];
class MetricMap {constructor(){this.snapshot={};}set(s){this.snapshot=s;}draw(){}zoom(){}}
class ArchiveSegmentGallery {reset(){}beforeCommit(){}update(){}}
const sandbox={MetricMap,ArchiveSegmentGallery,console,URLSearchParams,AbortController,AbortSignal,
  location:{pathname:'/inactive'},document:{hidden:false,getElementById:element,querySelectorAll:()=>[],addEventListener(){},activeElement:null},
  window:{addEventListener(){}},setInterval(){},
  setTimeout(fn){const id=++sequence;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);},
  fetch(url,options){
    // This regression covers a legacy archive without negotiated map windows.
    if(url.includes('/map-window'))return Promise.resolve({ok:true,json:async()=>({ok:true,available:false})});
    return new Promise((resolve,reject)=>{
      requests.push({url,resolve,signal:options.signal});
      options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})),{once:true});
    });
  }};
const source=fs.readFileSync(new URL('../static/live-map.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');
vm.runInNewContext(source,sandbox);
sandbox.paint({active:true,state:'waiting_for_sensors',scan_count:0});
assert.equal(element('liveMapState').textContent,'센서 동기화 대기');
assert.equal(element('liveMapStart').disabled,true);assert.equal(element('liveMapStop').disabled,false);
sandbox.location.pathname='/rover-control';
const selected=sandbox.window.LiveMap.selectArchive('recording');
vm.runInNewContext('windowSupported=false;',sandbox);
assert.equal(requests.length,1);
sandbox.window.LiveMap.archiveTime('recording',60,false);
// A paused seek cancels the obsolete request and services the new cursor.
for(const [id,fn] of [...timers]){timers.delete(id);fn();}
await Promise.resolve();await Promise.resolve();
assert.equal(requests.length,2);assert.equal(requests[0].signal.aborted,true);
await selected;
assert.ok(requests[1].url.includes('t_s=60.000'));
requests[1].resolve({ok:true,json:async()=>({ok:true,state:'saved',scan_count:0})});
await new Promise(resolve=>setImmediate(resolve));
// The obsolete request's finally block may schedule one catch-up poll. That
// catch-up must settle at the current cursor, rather than forming a loop.
assert.ok(timers.size<=1);
for(const [id,fn] of [...timers]){timers.delete(id);fn();}
await Promise.resolve();
if(requests.length===3){
  assert.ok(requests[2].url.includes('t_s=60.000'));
  requests[2].resolve({ok:true,json:async()=>({ok:true,state:'saved',scan_count:0})});
  await new Promise(resolve=>setImmediate(resolve));
}
assert.equal(timers.size,0,'paused current cursor must not create a polling loop');
console.log('archive paused seek cancels obsolete request and does not poll indefinitely');
