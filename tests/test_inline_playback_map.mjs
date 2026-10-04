import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const elements=new Map(),requests=[],commits=[],timers=new Map();
const element=id=>elements.get(id)||elements.set(id,{events:{},value:30,
  addEventListener(event,callback){this.events[event]=callback;}}).get(id);
class MetricMap {
  constructor(){this.snapshot={};}
  async set(value){this.snapshot=value;this.tileEpoch=value.tile_epoch;commits.push(value);}
  draw(){}zoom(){}
}
let now=0,sequence=0,respond=null;
const frame=t=>({ok:true,playback_driven:true,scan_count:Math.floor(t*10),tile_version:Math.floor(t*10),
  version:Math.floor(t*10),map_epoch:'epoch',tile_epoch:'epoch:0',tiles:[],frame_t_s:t,grid:{},
  generation:{processed_t_s:t,waiting_for_cursor:true}});
const sandbox={MetricMap,ArchiveMapBuffer:class{},ArchiveSegmentGallery:class{reset(){}},
  decodeArchiveWindow:async value=>value,AbortController,AbortSignal,URLSearchParams,Date,
  crypto:{randomUUID:()=> 'test-browser'},performance:{now:()=>now},
  location:{pathname:'/other'},document:{hidden:false,getElementById:element,querySelectorAll:()=>[],addEventListener(){}},
  window:{addEventListener(){}},setInterval(){},
  setTimeout(callback,delay){timers.set(++sequence,{callback,delay});return sequence;},clearTimeout(id){timers.delete(id);},
  fetch:async(path,options)=>{
    const body=options.body?JSON.parse(options.body):undefined;requests.push({path,body});
    const value=body? await (respond?.(body) ?? {ok:true,playback_driven:true,map_snapshot:frame(body.t_s)}):frame(0);
    return {ok:true,json:async()=>value};
  }};
const context=vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(new URL('../static/live-map.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,''),context);
const run=code=>vm.runInContext(code,context);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
await run("selectArchive('record')");
run("archiveTime('record',1,true)");await settle();
assert.equal(run('inlinePlaybackMap'),true);
assert.equal(run('archive.snapshot.frame_t_s'),1);
const play=requests.at(-1).body;
assert.equal(play.transport,'tiles');assert.equal(play.codec,'xor2-deflate-v1');
assert.equal(play.control.sequence,1);
assert.equal(timers.size,0,'inline cells must cancel separate map polling');
sandbox.location.pathname='/rover-control';
await run("pollArchive('record',archiveEpoch)");
assert.equal(requests.length,2,'same cursor must not send a separate map GET');
now=300;run("archiveTime('record',1.3,true)");await settle();
assert.equal(requests.at(-1).body.tile_since,10);
assert.equal(requests.at(-1).body.tile_epoch,'epoch:0');
assert.equal(run('archive.snapshot.frame_t_s'),1.3);
run("archiveTime('record',1.3,false)");await settle();
assert.equal(requests.at(-1).body.playing,false);
assert.equal(timers.size,0,'pause acknowledgement includes final cells; no backlog polling');
run("archiveTime('record',.5,false,true)");await settle();
assert.equal(run('archive.snapshot.frame_t_s'),.5,'paused backward seek receives only its historical cells');
assert.equal(requests.at(-1).body.playing,false);

let oldReply;
respond=body=>body.playing?new Promise(resolve=>{oldReply=resolve;}):{ok:true,playback_driven:true,map_snapshot:frame(body.t_s)};
now=600;run("archiveTime('record',2,true)");await settle();
run("archiveTime('record',.4,false,true)");await settle();
assert.equal(run('archive.snapshot.frame_t_s'),.4);
oldReply({ok:true,playback_driven:true,map_snapshot:frame(2)});await settle();
assert.equal(run('archive.snapshot.frame_t_s'),.4,'late Play response/decoder must not overwrite paused rewind');
assert.equal(requests.filter(r=>!r.body).length,1,'only initial selection needs standalone map GET');
console.log('Inline cursor/map shares one request; ordered pause and rewind reject delayed future cells');
