import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
class Element {
  constructor(){this.value='';this.children=[];this.events=new Map();this.currentTime=12;this.paused=true;}
  addEventListener(k,v){this.events.set(k,v);} removeEventListener(k){this.events.delete(k);}
  replaceChildren(){this.children=[];} append(v){this.children.push(v);} load(){} canPlayType(){return false;}
}
class Hls {
  static Events={MANIFEST_PARSED:'manifest',LEVEL_SWITCHED:'level',ERROR:'error',FRAG_LOADED:'frag'};
  static ErrorTypes={MEDIA_ERROR:'media',NETWORK_ERROR:'network',OTHER_ERROR:'other'};static isSupported(){return true;}
  constructor(config){this.config=config;this.events={};this.levels=[{height:360},{height:720}];this.autoLevelEnabled=true;}
  on(k,f){this.events[k]=f;} loadSource(v){this.source=v;}attachMedia(v){this.video=v;}
  destroy(){this.dead=true;}recoverMediaError(){this.recovered=true;this.recoveries=(this.recoveries||0)+1;}
  swapAudioCodec(){this.swapped=true;}startLoad(){this.starts=(this.starts||0)+1;}
}
const context=vm.createContext({window:{Hls},document:{createElement:()=>new Element()}});
vm.runInContext(fs.readFileSync(new URL('../static/archive-video.js',import.meta.url),'utf8'),context);
const Player=context.window.ArchiveVideoPlayer;const video=new Element(),select=new Element(),updates=[];
const player=new Player(video,select,{manifest_url:'/master.m3u8'},'/native.mp4',v=>updates.push(v));
const hls=player.hls;assert.equal(hls.config.backBufferLength,12);assert.equal(hls.config.capLevelToPlayerSize,true);
assert.equal(hls.config.startLevel,-1);assert.equal(hls.config.maxBufferLength,24);
hls.events.manifest(null,{levels:hls.levels});assert.equal(select.children.length,3);
player.choose('1');assert.equal(hls.nextLevel,1);player.choose('-1');assert.equal(hls.nextLevel,-1);
hls.events.error(null,{fatal:true,type:'media'});assert.equal(hls.recovered,true);
hls.events.error(null,{fatal:true,type:'media'});assert.equal(hls.swapped,true);assert.notEqual(hls.dead,true,'second media error swaps codec');
hls.events.error(null,{fatal:true,type:'media'});assert.equal(hls.dead,true);assert.equal(video.src,'/native.mp4');
player.destroy();assert.equal(select.events.has('change'),false);
hls.events.level(null,{level:1});assert.equal(updates.at(-1).mode,'mp4');
// D1: network errors retry with backoff and keep HLS (fast link: mp4 only after 5 retries).
{
  const timers=[];const t={set:(f,ms)=>{timers.push({f,ms});return timers.length;},clear(){}};
  const v=new Element(),notes=[];
  const p=new Player(v,new Element(),{manifest_url:'/m.m3u8'},'/native.mp4',n=>notes.push(n),{timers:t,profile:'balanced'});
  const h=p.hls;
  for(let i=0;i<5;i++){h.events.error(null,{fatal:true,type:'network',details:'fragLoadError'});}
  assert.deepEqual(timers.map(x=>x.ms),[1000,2000,4000,8000,15000]);
  assert.equal(p.mode,'adaptive');assert.equal(v.src,undefined);
  timers.at(-1).f();assert.equal(h.starts,1,'segments resume with startLoad');
  h.events.frag();assert.equal(p.networkRetries,0,'a loaded fragment resets the backoff');
  assert.equal(notes.at(-1).retrying,false);
  h.events.error(null,{fatal:true,type:'network',details:'manifestLoadError'});timers.at(-1).f();
  assert.equal(h.source,'/m.m3u8','manifest errors reload the manifest');
  for(let i=0;i<5;i++)h.events.error(null,{fatal:true,type:'network',details:'fragLoadError'});
  assert.equal(p.mode,'mp4','fast link falls back after the retries are exhausted');
}
// Slow link: never the 1080p mp4; 360p automatic cap; deeper buffer.
{
  const timers=[];const t={set:(f,ms)=>{timers.push({f,ms});return timers.length;},clear(){}};
  const v=new Element();
  const p=new Player(v,new Element(),{manifest_url:'/m.m3u8'},'/native.mp4',()=>{},{timers:t,profile:'slow'});
  const h=p.hls;
  assert.equal(h.config.startLevel,0);assert.equal(h.config.maxBufferLength,60);assert.equal(h.config.backBufferLength,30);
  assert.equal(h.config.abrEwmaDefaultEstimate,600000);assert.ok(h.config.fragLoadPolicy.default.errorRetry.maxNumRetry>=6);
  h.levels=[{height:240},{height:360},{height:720},{height:1080}];
  h.events.manifest(null,{levels:h.levels});assert.equal(h.autoLevelCapping,1);
  for(let i=0;i<30;i++)h.events.error(null,{fatal:true,type:'network',details:'fragLoadError'});
  assert.equal(p.mode,'adaptive');assert.equal(v.src,undefined);assert.equal(timers.at(-1).ms,15000);
  for(let i=0;i<4;i++)h.events.error(null,{fatal:true,type:'media'});
  assert.equal(p.mode,'adaptive','media errors do not force the mp4 on a slow link');
  h.events.error(null,{fatal:true,type:'other'});assert.equal(p.mode,'adaptive');
  p.choose('3');assert.equal(h.nextLevel,3,'manual choice still allowed');
  p.destroy();assert.equal(h.dead,true);
}
console.log('adaptive VOD quality, recovery, fallback and lifecycle passed');
