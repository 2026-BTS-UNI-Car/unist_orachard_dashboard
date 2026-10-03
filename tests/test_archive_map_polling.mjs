import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const elements=new Map();
const element=id=>elements.get(id)||elements.set(id,{textContent:'',value:0,disabled:false,addEventListener(){}}).get(id);
const timers=new Map();let sequence=0;
const requests=[];
class MetricMap {constructor(){this.snapshot={};}set(s){this.snapshot=s;}draw(){}zoom(){}}
const sandbox={MetricMap,console,URLSearchParams,AbortController,AbortSignal,
  location:{pathname:'/inactive'},document:{hidden:false,getElementById:element,querySelectorAll:()=>[],addEventListener(){},activeElement:null},
  window:{addEventListener(){}},setInterval(){},
  setTimeout(fn){const id=++sequence;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);},
  fetch(url){return new Promise(resolve=>requests.push({url,resolve}));}};
const source=fs.readFileSync(new URL('../static/live-map.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/,'');
vm.runInNewContext(source,sandbox);
sandbox.location.pathname='/rover-control';
const selected=sandbox.window.LiveMap.selectArchive('recording');
assert.equal(requests.length,1);
sandbox.window.LiveMap.archiveTime('recording',60,false);
// The seek timer fires before the previous request has completed.
for(const [id,fn] of [...timers]){timers.delete(id);fn();}
await Promise.resolve();await Promise.resolve();
assert.equal(requests.length,1);
requests[0].resolve({ok:true,json:async()=>({ok:true,state:'saved',scan_count:0})});
await selected;
assert.equal(timers.size,1,'paused cursor must be retried after a busy poll');
for(const [id,fn] of [...timers]){timers.delete(id);fn();}
await Promise.resolve();
assert.equal(requests.length,2);
assert.ok(requests[1].url.includes('t_s=60.000'));
requests[1].resolve({ok:true,json:async()=>({ok:true,state:'saved',scan_count:0})});
await new Promise(resolve=>setImmediate(resolve));
assert.equal(timers.size,0,'paused current cursor must not create a polling loop');
console.log('archive paused-seek busy-request regression passed');
