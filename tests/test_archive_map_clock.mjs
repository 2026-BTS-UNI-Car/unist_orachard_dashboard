// 2026-11-21: the recorded map follows the playback; it does not hold the video or the sensor clock back.
// (Until then a map that was more than 0.35 s behind paused the video. Through a link with a 0.2 s round trip that
// happened several times a second, and for minutes after a jump forward.)
import assert from 'node:assert/strict';import vm from 'node:vm';
const source=(await import('node:module')).createRequire(import.meta.url)('./helpers/app_source.js').readAppSource();
const start=source.indexOf('function syncArchiveMapClock('),end=source.indexOf('function archivePlaybackTick(',start);
let lag=5,plays=0,pauses=0;
const state={recordings:{selectedId:'record'},archivePlayback:{playing:true,timeS:1,startTimeS:0,startedAt:500}};
const video={paused:false,pause(){this.paused=true;pauses++;},play(){plays++;this.paused=false;return Promise.resolve();}};
const asked=[];
const context=vm.createContext({state,Promise,Number,window:{LiveMap:{archiveReady:()=>false,archiveLag:(id,t)=>{asked.push([id,t]);return lag;}}}});
vm.runInContext(source.slice(start,end),context);
context.video=video;
vm.runInContext('syncArchiveMapClock(1,1000,video)',context);
assert.equal(video.paused,false,'a map that is behind does not pause the video');
assert.equal(pauses,0);assert.equal(plays,0);
assert.equal(state.archivePlayback.mapLagS,5,'the label is told how far the map is behind');
assert.equal(state.archivePlayback.playing,true);
assert.deepEqual(asked.at(-1),['record',1],'the map is asked about the playback time of the selected recording');
lag=0;vm.runInContext('syncArchiveMapClock(1.2,1200,video)',context);
assert.equal(state.archivePlayback.mapLagS,0);assert.equal(plays,0,'nothing was paused, so nothing is resumed');
lag=7.5;vm.runInContext('syncArchiveMapClock(2,2000,null)',context);
assert.equal(state.archivePlayback.startTimeS,0,'without a video the sensor clock keeps running as well');
assert.equal(state.archivePlayback.startedAt,500);
assert.equal(state.archivePlayback.mapLagS,7.5);
for(const odd of [NaN,-3,undefined,'x']){lag=odd;vm.runInContext('syncArchiveMapClock(2)',context);assert.equal(state.archivePlayback.mapLagS,0,String(odd));}
assert.equal(vm.runInContext('ARCHIVE_MAP_LAG_NOTE_S',context),2,'the note starts at two seconds; a map a few tenths behind is the normal case');
// A page without the map module (LiveMap missing) is never "behind".
const bare=vm.createContext({state:{recordings:{selectedId:'record'},archivePlayback:{}},Number,window:{}});
vm.runInContext(source.slice(start,end),bare);vm.runInContext('syncArchiveMapClock(1)',bare);
assert.equal(bare.state.archivePlayback.mapLagS,0);
// The playback tick: the clock of a recording without video advances with wall time although the map is behind.
const tickStart=source.indexOf('function archivePlaybackTick('),tickEnd=source.indexOf('function setArchiveTime(',tickStart);
const p={playing:true,timeS:3,startTimeS:3,startedAt:1000,durationS:60,sourceSwitchTimeS:null,lastPlaybackRenderAt:0,nativeVideoOffsetS:0};
let rendered=0;
const tick=vm.createContext({state:{recordings:{selectedId:'record'},archivePlayback:p},Number,window:{LiveMap:{archiveReady:()=>false,archiveLag:()=>40}},
  archiveNativeVideo:()=>null,performance:{now:()=>0},stopArchivePlayback(){p.playing=false;},queueArchivePlaybackTick(){},
  scheduleArchiveSegmentsForTime:async()=>{},debugLog(){},renderArchiveFrame(){rendered++;}});
vm.runInContext(source.slice(start,end)+source.slice(tickStart,tickEnd),tick);
vm.runInContext('archivePlaybackTick(3000)',tick);
assert.equal(p.timeS,5,'two seconds of wall time are two seconds of recording');
assert.equal(p.mapLagS,40);assert.equal(rendered,1);
vm.runInContext('archivePlaybackTick(4000)',tick);assert.equal(p.timeS,6);
console.log('The recorded map follows the playback: a late map pauses neither the video nor the sensor clock');
