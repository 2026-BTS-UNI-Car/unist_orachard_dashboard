import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
class Element {
  constructor(){this.value='';this.children=[];this.events=new Map();this.currentTime=12;this.paused=true;}
  addEventListener(k,v){this.events.set(k,v);} removeEventListener(k){this.events.delete(k);}
  replaceChildren(){this.children=[];} append(v){this.children.push(v);} load(){} canPlayType(){return false;}
}
class Hls {
  static Events={MANIFEST_PARSED:'manifest',LEVEL_SWITCHED:'level',ERROR:'error'};
  static ErrorTypes={MEDIA_ERROR:'media'};static isSupported(){return true;}
  constructor(config){this.config=config;this.events={};this.levels=[{height:360},{height:720}];this.autoLevelEnabled=true;}
  on(k,f){this.events[k]=f;} loadSource(v){this.source=v;}attachMedia(v){this.video=v;}
  destroy(){this.dead=true;}recoverMediaError(){this.recovered=true;}
}
const context=vm.createContext({window:{Hls},document:{createElement:()=>new Element()}});
vm.runInContext(fs.readFileSync(new URL('../static/archive-video.js',import.meta.url),'utf8'),context);
const Player=context.window.ArchiveVideoPlayer;const video=new Element(),select=new Element(),updates=[];
const player=new Player(video,select,{manifest_url:'/master.m3u8'},'/native.mp4',v=>updates.push(v));
const hls=player.hls;assert.equal(hls.config.backBufferLength,12);assert.equal(hls.config.capLevelToPlayerSize,false);
hls.events.manifest(null,{levels:hls.levels});assert.equal(select.children.length,3);
player.choose('1');assert.equal(hls.nextLevel,1);player.choose('-1');assert.equal(hls.nextLevel,-1);
hls.events.error(null,{fatal:true,type:'media'});assert.equal(hls.recovered,true);
hls.events.error(null,{fatal:true,type:'media'});assert.equal(hls.dead,true);assert.equal(video.src,'/native.mp4');
player.destroy();assert.equal(select.events.has('change'),false);
hls.events.level(null,{level:1});assert.equal(updates.at(-1).mode,'mp4');
console.log('adaptive VOD quality, recovery, fallback and lifecycle passed');
