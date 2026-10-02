import {MetricMap} from './metric-map.mjs';
const $=id=>document.getElementById(id),text=(id,value)=>{if($(id))$(id).textContent=value;};
const live=new MetricMap('liveMapCanvas'),archive=new MetricMap('liveArchiveCanvas');
const active=()=>location.pathname.replace(/\/$/,'')==='/rover-control'&&!document.hidden;
let polling=false,timer=null,selected='',archiveEpoch=0,speedBusy=false;
async function request(path,body){
  const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(8000),...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
  const value=await response.json();if(!response.ok||value.ok===false)throw new Error(value.error||value.message||`HTTP ${response.status}`);return value;
}
function paint(value){
  live.set(value);text('liveMapState',value.active?'그리는 중':value.state==='needs_reset'?'위치 추정 중단':'멈춤');
  $('liveMapStart').disabled=value.active||value.state==='needs_reset';$('liveMapStop').disabled=!value.active;
  text('liveMapStatus',`${value.scan_count||0} scans · ${value.active?'지도 누적 중':value.state==='needs_reset'?'새 지도 필요':'마지막 지도 유지'}${value.reason?` · ${value.reason}`:''}`);
}
for(const operation of ['start','stop','reset'])$('liveMap'+operation[0].toUpperCase()+operation.slice(1)).addEventListener('click',async()=>{
  try{paint(await request('/api/live/map/'+operation,{}));}catch(error){text('liveMapStatus',error.message);}
});
for(const button of document.querySelectorAll('[data-live-zoom]'))button.addEventListener('click',()=>live.zoom(button.dataset.liveZoom));
for(const button of document.querySelectorAll('[data-archive-zoom]'))button.addEventListener('click',()=>archive.zoom(button.dataset.archiveZoom));
async function selectArchive(id){
  if(id===selected)return;selected=id;const epoch=++archiveEpoch;archive.set({});$('liveArchiveRegenerate').disabled=!id;
  if(!id){text('liveArchiveStatus','기록을 선택하세요.');return;}
  try{await pollArchive(id,epoch);}catch(error){if(epoch===archiveEpoch)text('liveArchiveStatus',error.message);}
}
async function pollArchive(id,epoch){
  const value=await request(`/api/live/recordings/${encodeURIComponent(id)}/map`);
  if(epoch!==archiveEpoch)return;
  if(value.state==='regenerating'){
    text('liveArchiveStatus','원본 LiDAR·IMU 기록으로 지도 만드는 중…');
    setTimeout(()=>{if(epoch===archiveEpoch)pollArchive(id,epoch).catch(error=>text('liveArchiveStatus',error.message));},2000);return;
  }
  if(value.grid)archive.set(value);
  text('liveArchiveStatus',value.regeneration_error?`재생성 실패 · ${value.regeneration_error}`:value.grid?`저장된 지도 · ${value.scan_count} scans · 빨간 경로는 전체 기록 · 표시용 위치 추정`:'저장된 지도 없음 · 기록으로 재생성을 누르세요.');
}
$('liveArchiveRegenerate').addEventListener('click',async()=>{
  const id=selected,epoch=archiveEpoch;if(!id)return;$('liveArchiveRegenerate').disabled=true;
  try{await request(`/api/live/recordings/${encodeURIComponent(id)}/regenerate`,{});await pollArchive(id,epoch);}
  catch(error){if(epoch===archiveEpoch)text('liveArchiveStatus',error.message);}
  finally{if(epoch===archiveEpoch)$('liveArchiveRegenerate').disabled=false;}
});
$('hardwareAutonomySpeed').addEventListener('input',()=>text('hardwareAutonomySpeedValue',`${$('hardwareAutonomySpeed').value}%`));
$('hardwareAutonomySpeed').addEventListener('change',async()=>{
  if(speedBusy)return;speedBusy=true;$('hardwareAutonomySpeed').disabled=true;
  try{const value=await request('/api/autonomy/speed',{speed_percent:Number($('hardwareAutonomySpeed').value)});text('hardwareAutonomyStatus',`${value.message} · 속도 ${value.speed_percent}% 저장됨`);}
  catch(error){text('hardwareAutonomyStatus',error.message);}
  finally{speedBusy=false;$('hardwareAutonomySpeed').disabled=false;}
});
async function poll(){
  clearTimeout(timer);if(polling||!active())return;polling=true;
  const results=await Promise.allSettled([request('/api/live/map'+(live.imageVersion!==undefined?`?since=${live.imageVersion}`:'')),request('/api/autonomy')]);
  if(results[0].status==='fulfilled')paint(results[0].value);else text('liveMapStatus',`지도 연결 대기 · 마지막 지도 유지 · ${results[0].reason.message}`);
  if(results[1].status==='fulfilled'&&!speedBusy&&document.activeElement!==$('hardwareAutonomySpeed')){
    $('hardwareAutonomySpeed').value=results[1].value.speed_percent;text('hardwareAutonomySpeedValue',`${results[1].value.speed_percent}%`);
    text('hardwareAutonomyStatus',`${results[1].value.message} · 현재 순항속도 설정 ${results[1].value.speed_percent}%`);
  }
  polling=false;if(active())timer=setTimeout(poll,1000);
}
function routeChanged(){live.draw();archive.draw();if(active())poll();}
window.LiveMap={selectArchive,routeChanged};
document.addEventListener('visibilitychange',routeChanged);window.addEventListener('popstate',routeChanged);
// The main application exposes route changes through the GPS hook.
const gps=window.RoverGps;if(gps){const original=gps.routeChanged;gps.routeChanged=(...args)=>{original?.(...args);routeChanged();};}
setInterval(()=>{if(active()&&!polling)poll();},3000);
routeChanged();
