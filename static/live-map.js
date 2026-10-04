import {MetricMap} from './metric-map.mjs';
import {ArchiveMapBuffer} from './archive-map-buffer.mjs';
import {ArchiveSegmentGallery} from './archive-segments.mjs';
import {decodeArchiveWindow} from './archive-window-codec.mjs';
const $=id=>document.getElementById(id),text=(id,value)=>{if($(id))$(id).textContent=value;};
// Correct the real-rover front marker as requested, without modifying any
// retained scan coordinates, localization yaw or simulation body-frame heading.
const live=new MetricMap('liveMapCanvas',false,Math.PI),archive=new MetricMap('liveArchiveCanvas',false,Math.PI);
const active=()=>location.pathname.replace(/\/$/,'')==='/rover-control'&&!document.hidden;
let polling=false,timer=null,selected='',archiveEpoch=0,speedBusy=false,lastAutonomyPoll=0;
let mapBuffer=null,nextMapBuffer=null,mapWindowPending=null,bufferPaintPending=false,windowSupported=null;
let archiveTimer=null,archivePending=null,archiveTimeS=0,archivePlaying=false,followGeneration=false,archiveGenerating=false;
let archiveRetryMs=75;
let cursorMapping=false,playbackPending=null,lastPlaybackPush=-Infinity;
const segments=new ArchiveSegmentGallery(archive,time=>window.setArchiveTime?.(time,{pause:true}));
archive.beforeCommit=(previous,next)=>segments.beforeCommit(previous,next);
archive.afterDraw=()=>{
  if(archive.snapshot.playback_driven){
    if($('liveArchiveSegments'))$('liveArchiveSegments').hidden=true;
    text('liveArchiveMapNote','새 지도 만들기 → 재생 · 일시정지 시 지도 유지 · 원본 기록 보존');
  }else{
    segments.update(archive.snapshot,archiveTimeS);
    text('liveArchiveMapNote','이전 방식의 기록 지도 · 새 지도 만들기 후 재생하면 같은 지도에 계속 누적합니다.');
  }
  mapControls('archive',archive);
};
live.afterDraw=()=>mapControls('live',live);
function mapControls(kind,map){
  for(const button of document.querySelectorAll(`[data-${kind}-zoom]`)){
    const mode=button.dataset[kind+'Zoom'];
    if(mode==='fit'||mode==='full'){
      const active=map.auto && map.fitMode===(mode==='fit'?'nearby':'full');
      button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
    }
  }
}
function archiveQuality(value){
  if(value.needs_regeneration)return ' · 이전 지도 · 재생성 필요';
  if(value.tracking==='relocalizing')return ' · 위치 다시 찾는 중 · 이전 지도 유지';
  return Number(value.segments)>1?` · 구간 ${(value.segment??0)+1} · 이전 지도 보존 · 구간 연결 미확정`:'';
}
async function request(path,body,signal){
  const timeout=path.includes('/map-window')||path.includes('/map?')?30000:8000;
  const response=await fetch(path,{cache:'no-store',priority:path.includes('/map')?'high':'auto',signal:signal?AbortSignal.any([signal,AbortSignal.timeout(timeout)]):AbortSignal.timeout(timeout),...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
  const value=await response.json();if(!response.ok||value.ok===false)throw new Error(value.error||value.message||`HTTP ${response.status}`);return value;
}
function paint(value){
  const waiting=value.state==='waiting_for_sensors';
  const restored=value.persistence?.restored&&value.state==='needs_reset';
  live.set(value);text('liveMapState',restored?'저장된 지도':waiting?'센서 동기화 대기':value.active?'그리는 중':value.state==='needs_reset'?'위치 추정 중단':'멈춤');
  $('liveMapStart').disabled=value.active||value.state==='needs_reset';$('liveMapStop').disabled=!value.active;
  text('liveMapStatus',restored?'저장된 지도 유지 · 새 지도를 눌러 그리기를 시작하세요.':`${waiting?'GPS·IMU·LiDAR 동기화 대기 · 마지막 지도 유지':value.active?'지도 누적 중':value.state==='needs_reset'?'새 지도 필요':'마지막 지도 유지'}${value.reason?` · ${value.reason}`:''}${value.persistence?.saved===false?' · 원본 셀 저장 실패 · 표시 지도 유지':''}`);
}
for(const operation of ['start','stop','reset'])$('liveMap'+operation[0].toUpperCase()+operation.slice(1)).addEventListener('click',async()=>{
  try{paint(await request('/api/live/map/'+operation,{}));}catch(error){text('liveMapStatus',error.message);}
});
for(const button of document.querySelectorAll('[data-live-zoom]'))button.addEventListener('click',()=>live.zoom(button.dataset.liveZoom));
for(const button of document.querySelectorAll('[data-archive-zoom]'))button.addEventListener('click',()=>archive.zoom(button.dataset.archiveZoom));
async function selectArchive(id){
  if(id===selected)return;selected=id;const epoch=++archiveEpoch;
  clearTimeout(archiveTimer);archiveTimer=null;archivePending?.controller.abort();
  mapBuffer=null;nextMapBuffer=null;windowSupported=null;mapWindowPending?.controller.abort();mapWindowPending=null;
  archiveTimeS=0;archivePlaying=false;followGeneration=false;archiveGenerating=false;cursorMapping=false;lastPlaybackPush=-Infinity;
  playbackPending?.controller.abort();playbackPending=null;segments.reset();archive.set({});$('liveArchiveRegenerate').disabled=!id;
  if(!id){text('liveArchiveStatus','기록을 선택하세요.');return;}
  try{await pollArchive(id,epoch);}catch(error){if(epoch===archiveEpoch&&error.name!=='AbortError'){text('liveArchiveStatus',error.message);scheduleArchive(500);}}
}
async function pollArchive(id,epoch){
  if(epoch!==archiveEpoch||archivePending?.epoch===epoch)return;
  if(!cursorMapping && !followGeneration && windowSupported!==false){
    if(nextMapBuffer?.contains(archiveTimeS)){mapBuffer=nextMapBuffer;nextMapBuffer=null;}
    if(mapBuffer?.contains(archiveTimeS)){
      if(!bufferPaintPending){
        const value=mapBuffer.at(archiveTimeS,archive.snapshot.tile_version,archive.tileEpoch);
        if(value && value.version!==archive.snapshot.version){
          bufferPaintPending=true;
          try{await archive.set(value);}finally{bufferPaintPending=false;}
        }
        text('liveArchiveStatus',`GPS·IMU·LiDAR 통합 지도 · ${(archive.snapshot.frame_t_s||0).toFixed(1)}초 · 센서 시각 동기 재생${archiveQuality(archive.snapshot)}`);
      }
      if(!nextMapBuffer && !mapWindowPending && mapBuffer.end_s<mapBuffer.duration_s && mapBuffer.end_s-archiveTimeS<10){
        loadMapWindow(id,epoch,mapBuffer.end_s,12,true).catch(()=>{});
      }
      if(archivePlaying)scheduleArchive();return;
    }
    // A seek gets one complete map first. Bulk look-ahead must not block that
    // response or occupy the connection needed by the current playback cursor.
  }
  const pending={epoch,controller:new AbortController()},target=followGeneration?null:archiveTimeS;
  archivePending=pending;const query=new URLSearchParams({transport:'tiles',codec:'xor2-deflate-v1'});
  if(archive.tileEpoch){query.set('tile_epoch',archive.tileEpoch);query.set('tile_since',archive.snapshot.tile_version || 0);}
  if(target!==null)query.set('t_s',(Math.floor(target*1000)/1000).toFixed(3));
  if(archive.imageVersion!==undefined)query.set('since',`${archive.snapshot.map_epoch}:${archive.imageVersion}`);
  let value;
  try{
    value=await request(`/api/live/recordings/${encodeURIComponent(id)}/map?${query}`,undefined,pending.controller.signal);
    if(value.map_frame)value=(await decodeArchiveWindow(value)).seed;
    if(epoch!==archiveEpoch||(!followGeneration&&(target===null||target>archiveTimeS+.001)))return;
    cursorMapping=value.playback_driven===true;
    if(value.grid||value.scan_count===0)archive.set(value);
    archiveRetryMs=75;
    if(!cursorMapping && !followGeneration && windowSupported!==false && !mapWindowPending){
      loadMapWindow(id,epoch,Math.max(0,archiveTimeS),3,false).then(()=>scheduleArchive()).catch(error=>{
        if(epoch===archiveEpoch&&error.name!=='AbortError')scheduleArchive(500);
      });
    }
    const generation=value.generation||{},duration=Number(generation.duration_s)||0;
    archiveGenerating=value.state==='regenerating';$('liveArchiveRegenerate').disabled=archiveGenerating;
    text('liveArchiveStatus',value.mapping_error?`지도 처리 오류 · ${value.mapping_error} · 이전 지도 유지`:value.regeneration_error?`재생성 실패 · ${value.regeneration_error}`:
      cursorMapping?`${value.grid?(archivePlaying?'재생하며 지도 저장 중':'저장된 지도 유지'):'재생하면 새 지도가 쌓입니다'} · ${(value.frame_t_s||0).toFixed(1)}초${archiveQuality(value)}`:
      value.state==='regenerating'?(generation.stage==='localizing'?'LiDAR·IMU 위치 추정 계산 중…':`지도 생성 ${Math.round((generation.progress||0)*100)}% · ${(generation.processed_t_s||0).toFixed(1)} / ${duration.toFixed(1)}초`):
      value.needs_regeneration?'이전 방식의 지도입니다. 새 지도 만들기 후 재생하세요.':
      value.state==='interrupted'?'중단된 지도 생성 · 부분 결과 유지 · 다시 재생성할 수 있습니다.':
      value.grid?`${followGeneration?'생성된 전체 지도':'재생 시각의 지도'} · ${(value.frame_t_s||0).toFixed(1)}초${archiveQuality(value)}`:
      value.history_available?'첫 지도 관측 전 · 재생하면 지도가 쌓입니다.':'저장된 지도 없음 · 새 지도 만들기 후 재생하세요.');
  }finally{
    if(archivePending===pending)archivePending=null;
    // A paused seek can occur while the previous request is in flight. Its
    // timer may have found that request busy; always service the newest cursor
    // after completion instead of leaving the canvas empty until another seek.
    const cursorChanged=!followGeneration&&(target===null||Math.abs(target-archiveTimeS)>.05);
    if(epoch===archiveEpoch&&(value?.state==='regenerating'||archivePlaying||cursorChanged||
        (cursorMapping&&Number(value?.generation?.processed_t_s||0)<archiveTimeS-.15)))scheduleArchive();
  }
}
async function loadMapWindow(id,epoch,start,span,next){
  if(mapWindowPending)return;
  const pending={controller:new AbortController(),epoch,start,end:start+span,next};mapWindowPending=pending;
  try{
    const raw=await request(`/api/live/recordings/${encodeURIComponent(id)}/map-window?start_s=${start.toFixed(3)}&span_s=${span}&codec=xor2-deflate-v1`,undefined,pending.controller.signal);
    const value=await decodeArchiveWindow(raw);
    if(epoch!==archiveEpoch)return;
    windowSupported=value.available===true;
    if(windowSupported){const window=new ArchiveMapBuffer(value);if(next)nextMapBuffer=window;else mapBuffer=window;}
  }finally{if(mapWindowPending===pending)mapWindowPending=null;}
}
function scheduleArchive(delay=75){
  if(archiveTimer!==null||!selected||!active())return;
  const id=selected,epoch=archiveEpoch;
  archiveTimer=setTimeout(()=>{archiveTimer=null;pollArchive(id,epoch).catch(error=>{
    if(epoch===archiveEpoch&&error.name!=='AbortError'){
      text('liveArchiveStatus',`${error.message} · 마지막 지도 유지 · 재시도 중`);
      archiveRetryMs=Math.min(2000,Math.max(500,archiveRetryMs*2));scheduleArchive(archiveRetryMs);
    }
  });},delay);
}
function archiveTime(id,timeS,playing,seekRequested=false){
  if(id!==selected)return;
  // Video quality changes/buffering can report a slightly older media clock.
  // Only an explicit user seek or a paused change may rewind a running map.
  // Keep both the requested raw prefix and rendered cells monotonic otherwise.
  timeS=Math.max(0,Number(timeS)||0);
  if(playing && archivePlaying && !seekRequested)timeS=Math.max(timeS,archiveTimeS);
  if(playing||Math.abs(timeS-archiveTimeS)>.05)followGeneration=false;
  if(!followGeneration&&(seekRequested||!playing)&&Math.max(Number(archive.snapshot.frame_t_s)||0,Number(archive.pendingImage?.snapshot.frame_t_s)||0)>timeS){
    // Cancel a future PNG decoder immediately on backward seek/play-from-zero.
    // The same epoch keeps the user's metric zoom/pan while an earlier frame loads.
    archive.set({map_epoch:archive.snapshot.map_epoch,preserve_view_on_seek:true,scan_count:0,version:0});
  }
  const jump=Number(timeS)-archiveTimeS;
  // Media clocks can jump after buffering or a busy render frame. Only an
  // explicit seek (or backward/paused change) invalidates a future download.
  const seek=seekRequested || jump<-.05 || (!playing && Math.abs(jump)>.05);
  const playChanged=Boolean(playing)!==archivePlaying;
  const changed=Math.abs(jump)>.05||playChanged;
  archiveTimeS=Math.max(0,Number(timeS)||0);archivePlaying=Boolean(playing);
  if(cursorMapping && (changed || archivePlaying))pushPlayback(playChanged||seekRequested);
  // A future window is intentionally ahead of the clock. Normal playback must
  // not abort it simply because the current cursor precedes its start.
  if(mapWindowPending && seek && (archiveTimeS<mapWindowPending.start || archiveTimeS>mapWindowPending.end)){
    mapWindowPending.controller.abort();mapWindowPending=null;
  }
  if(changed && !archivePlaying && archivePending){archivePending.controller.abort();archivePending=null;}
  if(changed)scheduleArchive();
}
async function pushPlayback(force=false){
  if(!cursorMapping||!selected||playbackPending||(!force&&performance.now()-lastPlaybackPush<250))return;
  const id=selected,epoch=archiveEpoch,time=archiveTimeS,playing=archivePlaying;
  const pending={controller:new AbortController(),epoch};playbackPending=pending;lastPlaybackPush=performance.now();
  try{
    await request(`/api/live/recordings/${encodeURIComponent(id)}/map-playback`,{t_s:time,playing},pending.controller.signal);
  }catch(error){if(epoch===archiveEpoch&&error.name!=='AbortError')text('liveArchiveStatus',`${error.message} · 마지막 지도 유지`);}
  finally{
    if(playbackPending===pending)playbackPending=null;
    if(epoch===archiveEpoch&&(Math.abs(time-archiveTimeS)>.05||playing!==archivePlaying))pushPlayback(true);
  }
}
$('liveArchiveRegenerate').addEventListener('click',async()=>{
  const id=selected;if(!id)return;
  window.setArchiveTime?.(0,{pause:true});
  const epoch=++archiveEpoch;archivePending?.controller.abort();archivePending=null;
  mapWindowPending?.controller.abort();mapWindowPending=null;playbackPending?.controller.abort();playbackPending=null;
  mapBuffer=null;nextMapBuffer=null;windowSupported=null;$('liveArchiveRegenerate').disabled=true;
  try{
    const value=await request(`/api/live/recordings/${encodeURIComponent(id)}/new-map`,{});
    if(epoch!==archiveEpoch)return;
    cursorMapping=true;archiveTimeS=0;archivePlaying=false;archiveGenerating=false;followGeneration=false;
    segments.reset();archive.set({map_epoch:value.map_epoch,preserve_view_on_seek:true,version:0,scan_count:0,tiles:[],tiles_reset:true,playback_driven:true});
    text('liveArchiveStatus','새 지도 준비됨 · 재생을 누르면 지도가 쌓이고 저장됩니다.');
  }
  catch(error){if(epoch===archiveEpoch)text('liveArchiveStatus',error.message);}
  finally{if(epoch===archiveEpoch)$('liveArchiveRegenerate').disabled=archiveGenerating;}
});
$('hardwareAutonomySpeed').addEventListener('input',()=>text('hardwareAutonomySpeedValue',`${$('hardwareAutonomySpeed').value}%`));
$('hardwareAutonomySpeed').addEventListener('change',async()=>{
  if(speedBusy)return;speedBusy=true;$('hardwareAutonomySpeed').disabled=true;
  try{const value=await request('/api/autonomy/speed',{speed_percent:Number($('hardwareAutonomySpeed').value)});text('hardwareAutonomyStatus',`${value.message} · 속도 ${value.speed_percent}% 저장됨`);}
  catch(error){text('hardwareAutonomyStatus',error.message);}
  finally{speedBusy=false;$('hardwareAutonomySpeed').disabled=false;}
});
async function poll(){
  const started=performance.now();
  clearTimeout(timer);if(polling||!active())return;polling=true;
  const query=new URLSearchParams({transport:'tiles'});
  if(live.tileEpoch){query.set('tile_epoch',live.tileEpoch);query.set('tile_since',live.snapshot.tile_version || 0);}
  const refreshAutonomy=Date.now()-lastAutonomyPoll>3000;
  if(refreshAutonomy)lastAutonomyPoll=Date.now();
  const results=await Promise.allSettled([request('/api/live/map?'+query),refreshAutonomy?request('/api/autonomy'):Promise.resolve(null)]);
  if(results[0].status==='fulfilled')paint(results[0].value);else text('liveMapStatus',`지도 연결 대기 · 마지막 지도 유지 · ${results[0].reason.message}`);
  if(results[1].status==='fulfilled'&&results[1].value&&!speedBusy&&document.activeElement!==$('hardwareAutonomySpeed')){
    $('hardwareAutonomySpeed').value=results[1].value.speed_percent;text('hardwareAutonomySpeedValue',`${results[1].value.speed_percent}%`);
    text('hardwareAutonomyStatus',`${results[1].value.message} · 현재 순항속도 설정 ${results[1].value.speed_percent}%`);
  }
  polling=false;if(active())timer=setTimeout(poll,Math.max(0,(live.snapshot.active?100:1000)-(performance.now()-started)));
}
function routeChanged(){live.draw();archive.draw();if(active()){poll();if(selected)scheduleArchive();}}
window.LiveMap={selectArchive,archiveTime,routeChanged};
document.addEventListener('visibilitychange',routeChanged);window.addEventListener('popstate',routeChanged);
// The main application exposes route changes through the GPS hook.
const gps=window.RoverGps;if(gps){const original=gps.routeChanged;gps.routeChanged=(...args)=>{original?.(...args);routeChanged();};}
setInterval(()=>{if(active()&&!polling)poll();},3000);
routeChanged();
