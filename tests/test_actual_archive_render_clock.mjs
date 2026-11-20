import assert from 'node:assert/strict';import fs from 'node:fs';import vm from 'node:vm';
const source=(await import('node:module')).createRequire(import.meta.url)('./helpers/app_source.js').readAppSource();
const slice=(start,end)=>{const a=source.indexOf(start),b=source.indexOf(end,a+start.length);assert.ok(a>=0&&b>a);return source.slice(a,b);};
const functions=slice('function configureArchiveNativeVideo(','function setupArchivePlayback(')
  +slice('async function toggleArchivePlayback(','function mergeArchiveMotorSegment(')
  +slice('function nearestTimelineFrame(','function requestChartAnimation(');
const turn=()=>new Promise(resolve=>setImmediate(resolve));
function harness(){
  const samples={map:[],gps:[],motor:[],text:new Map()},timers=new Map(),ready={value:true};let next=0;
  const video={currentTime:0,paused:true,seeking:false,readyState:1,duration:20,videoHeight:1080,playCount:0,pauseCount:0,
    pause(){this.paused=true;this.pauseCount++;},play(){this.paused=false;this.playCount++;return Promise.resolve();},
    removeAttribute(){},load(){this.currentTime=0;},closest(){return {classList:{toggle(){}}};}};
  const elements=new Map([['roverArchiveCameraVideo',video],['roverArchivePlayButton',{}]]);
  const p={playing:false,timeS:1,durationS:30,sync:true,nativeVideoUrl:'/first.mp4',nativeVideoReady:true,nativeVideoError:'',
    nativeVideoOffsetS:0,mapBuffering:false,mapResumeRevision:0,sourceSwitchTimeS:null,videoPlayRevision:0,videoPlayRequested:false,
    tickTimer:null,raf:null,loadingTelemetry:new Map(),cache:{gpsFrames:[],motorFrames:[],cameraFrames:[],lidarFrames:[],
      imuFrames:[{t_s:11.278361075337647,raw:{euler_deg:{yaw:1}}}]}};
  const state={archivePlayback:p,recordings:{selectedId:'record',timeline:{gps:{enabled:true},sync:{imu_frames:1}},selected:{source_video:{fps:30}}}};
  const context=vm.createContext({state,Promise,Number,performance:{now:()=>1000},document:{activeElement:null,createElement:()=>({})},
    window:{LiveMap:{archiveReady:()=>ready.value,archiveLag:()=>ready.value?0:ready.lag??5,archiveTime:(...args)=>samples.map.push(args)},
      RoverGps:{archive:(...args)=>samples.gps.push(args[1])},ArchiveVideoPlayer:class{constructor(v,s,a,u,notify){this.notify=notify;}destroy(){}}},
    $:id=>elements.get(id),text:(id,value)=>samples.text.set(id,value),fmtNumber:(v,d)=>Number(v||0).toFixed(d),fmtClock:v=>String(v),
    renderArchiveMotor:(source,frames,time)=>samples.motor.push(time),drawLidarMapOnCanvas(){},warmArchiveCameraCache(){},archiveCameraFrameSrc(){},
    chartColors:()=>({}),renderReadout(){},vectorNorm:()=>null,drawImuAttitude(){},ARCHIVE_IMU_SYNC_MAX_DELTA_S:.3,
    setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id),requestAnimationFrame:()=>++next,cancelAnimationFrame(){},
    suspendLiveCameraForArchive(){},resumeLiveCameraAfterArchive(){},scheduleArchiveSegmentsForTime:async()=>{},scheduleArchiveSeek(){},debugLog(){}});
  vm.runInContext(functions,context);return {context,p,state,video,elements,samples,ready,call:(name,...args)=>context[name](...args)};
}
let passed=0;const test=async(name,fn)=>{await fn();passed++;console.log('PASS '+name);};
await test('delayed UI quality/telemetry render uses one current native host sample for every sensor',async()=>{
  const h=harness();h.p.playing=true;h.p.timeS=10.781354075337648;h.p.nativeVideoOffsetS=1.9791630753376466;h.video.currentTime=9.299198;
  h.call('renderArchiveFrame');const expected=11.278361075337647;
  assert.equal(h.p.timeS,expected);assert.equal(h.samples.map.at(-1)[1],expected);assert.equal(h.samples.gps.at(-1),expected);
  assert.equal(h.samples.motor.at(-1),expected);assert.match(h.samples.text.get('roverArchiveImuTime'),/Δ 0\.000s/);
});
await test('paused/seek/source switch/error/metadata-not-ready cursor remains authoritative',async()=>{
  for(const guard of ['paused','seeking','source','error','ready']){
    const h=harness();h.p.playing=true;h.p.timeS=.6;h.video.currentTime=7;
    if(guard==='paused')h.p.playing=false;if(guard==='seeking')h.video.seeking=true;
    if(guard==='source')h.p.sourceSwitchTimeS=.6;
    if(guard==='error')h.p.nativeVideoError='error';if(guard==='ready')h.video.readyState=0;
    h.call('renderArchiveFrame');assert.equal(h.p.timeS,.6,guard);assert.equal(h.samples.map.at(-1)[1],.6,guard);
  }
});
await test('a map that is behind does not stop the render clock from following the video (2026-11-21)',async()=>{
  const h=harness();h.p.playing=true;h.p.timeS=.6;h.video.currentTime=7;h.p.mapLagS=12.4;
  h.call('renderArchiveFrame');assert.equal(h.p.timeS,7);assert.equal(h.samples.map.at(-1)[1],7);
  assert.match(h.samples.text.get('roverArchiveTime'),/ · 지도는 12초 뒤에 따라옵니다$/);
  h.p.mapLagS=.6;h.call('renderArchiveFrame');assert.doesNotMatch(h.samples.text.get('roverArchiveTime'),/지도/,'a map a few tenths behind is not worth a note');
  h.p.mapLagS=12.4;h.p.playing=false;h.call('renderArchiveFrame');assert.doesNotMatch(h.samples.text.get('roverArchiveTime'),/지도/,'no note while paused');
});
await test('invalid media and offset never replace the selected host cursor',async()=>{
  for(const value of [NaN,Infinity,-1]){const h=harness();h.p.playing=true;h.video.currentTime=value;h.call('renderArchiveFrame');assert.equal(h.p.timeS,1);}
  const h=harness();h.p.playing=true;h.video.currentTime=2;h.p.nativeVideoOffsetS=Infinity;h.call('renderArchiveFrame');assert.equal(h.p.timeS,1);
});
await test('explicit seek retains its cursor in the first render even when media delta is under forty milliseconds',async()=>{
  const h=harness();h.p.playing=true;h.video.currentTime=.6;h.call('setArchiveTime',.625);
  assert.equal(h.p.timeS,.625);assert.equal(h.samples.map.at(-1)[1],.625);
});
await test('metadata restoration never renders transient zero before the player restores its cursor',async()=>{
  const h=harness();h.p.playing=true;h.p.timeS=6;
  h.call('configureArchiveNativeVideo',{video_url:'/fallback.mp4',source_video:{timeline_offset_s:1}});
  h.video.onloadedmetadata();assert.equal(h.p.timeS,6);assert.equal(h.samples.map.at(-1)[1],6);
  h.video.currentTime=5;h.p.videoPlayer.notify({fallback:true});assert.equal(h.p.timeS,6);
});
await test('stale source metadata cannot clear the new pending source cursor',async()=>{
  const h=harness();h.call('configureArchiveNativeVideo',{video_url:'/old.mp4'});const old=h.video.onloadedmetadata;
  h.call('configureArchiveNativeVideo',{video_url:'/new.mp4'});h.p.sourceSwitchTimeS=7;h.p.timeS=7;
  old();assert.equal(h.p.sourceSwitchTimeS,7);assert.equal(h.p.timeS,7);assert.equal(h.p.nativeVideoReady,false);
});
await test('Stop cancels a delayed initial Play without reactivating playback',async()=>{
  const h=harness();let resolve;h.video.play=function(){this.paused=false;this.playCount++;return new Promise(r=>resolve=r);};
  const pending=h.call('toggleArchivePlayback');h.call('stopArchivePlayback',false);resolve();await pending;
  assert.equal(h.p.playing,false);assert.equal(h.p.videoPlayRequested,false);assert.equal(h.video.paused,true);
});
await test('old initial Play completion cannot pause a newer successful Play',async()=>{
  const h=harness();let resolve;h.video.play=function(){this.paused=false;this.playCount++;return this.playCount===1?new Promise(r=>resolve=r):Promise.resolve();};
  const old=h.call('toggleArchivePlayback');h.call('stopArchivePlayback',false);await h.call('toggleArchivePlayback');
  const pauses=h.video.pauseCount;resolve();await old;assert.equal(h.p.playing,true);assert.equal(h.video.paused,false);assert.equal(h.video.pauseCount,pauses);
});
await test('a map that is behind neither pauses the video nor loses Play; Stop clears the note (2026-11-21)',async()=>{
  const h=harness();h.p.playing=true;h.video.paused=false;h.ready.value=false;
  h.call('syncArchiveMapClock',1);assert.equal(h.p.mapLagS,5);assert.equal(h.p.playing,true);
  assert.equal(h.video.paused,false);assert.equal(h.video.pauseCount,0);
  h.ready.value=true;h.call('syncArchiveMapClock',1.1);assert.equal(h.p.mapLagS,0);assert.equal(h.video.playCount,0);
  h.ready.value=false;h.call('syncArchiveMapClock',1.2);h.call('stopArchivePlayback',false);
  assert.equal(h.p.mapLagS,0);assert.equal(h.p.playing,false);
  await h.call('toggleArchivePlayback');assert.equal(h.p.playing,true);assert.equal(h.video.paused,false);
});
await test('source-switch delayed Play obeys a later Stop',async()=>{
  const h=harness(),selector={append(){},value:'primary'};h.elements.set('roverArchiveCameraSource',selector);
  const primary={source_height:720,fps:15,timeline_offset_s:1,alternative_sources:[{id:'alt',label:'alternate',fps:30,
    source_width:1920,source_height:1080,timeline_offset_s:2,fallback_url:'/alt.mp4'}]};
  h.call('configureArchiveNativeVideo',{id:'record',video_url:'/primary.mp4',adaptive_video:primary});
  let resolve;h.video.play=function(){this.paused=false;return new Promise(r=>resolve=r);};h.p.playing=true;
  selector.value='alt';selector.onchange();h.call('stopArchivePlayback',false);resolve();await turn();
  assert.equal(h.p.playing,false);assert.equal(h.video.paused,true);
});
console.log(JSON.stringify({tests_passed:passed,scope:'actual candidate production functions; mocked UI/timers; no network/service/media writes'}));
