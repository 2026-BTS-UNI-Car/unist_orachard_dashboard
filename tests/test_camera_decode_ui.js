// Execute actual camera display/ACK functions with a deterministic image loader.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const code=source.slice(source.indexOf('function displayBinaryCameraFrame('),source.indexOf('function renderCameraLiveState('));
function fixture(visible=true){
  const sent=[],revoked=[],timers=new Map(); let sequence=0;
  const image={src:'blob:old',style:{transform:''}};
  const socket={readyState:1,send:data=>sent.push(JSON.parse(data))};
  const context={state:{cameraLive:{socket,objectUrl:'blob:old'}},WebSocket:{OPEN:1},
    URL:{createObjectURL:()=>`blob:new${++sequence}`,revokeObjectURL:url=>revoked.push(url)},
    currentRoute:()=>'/rover-control',$:()=>visible?image:null,renderCameraLiveState:()=>{},debugLog:()=>{},performance:{now:()=>1234},
    window:{setTimeout:fn=>{const id=++sequence;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id)}};
  vm.createContext(context);vm.runInContext(code,context);
  return {context,image,socket,sent,revoked,timers,start:flow=>context.displayBinaryCameraFrame({}, {frame_count:7,flow:flow||'decode-ack'},socket)};
}
{
  const f=fixture();f.start('latest-ack');assert.equal(f.sent.length,0,'even legacy flow cannot acknowledge before decode');
  const onload=f.image.onload;f.image.currentSrc='blob:old';onload();assert.equal(f.sent.length,0);
  f.image.currentSrc=f.image.src;onload();onload();
  assert.equal(f.sent.length,1);assert.equal(f.sent[0].decode_status,'decoded');
  assert.ok(f.revoked.includes('blob:old'));assert.equal(f.timers.size,0);
}
for(const reason of ['error','timeout']){
  const f=fixture();f.start();const obsolete=f.image.onload;
  if(reason==='error')f.image.onerror();else [...f.timers.values()][0]();
  assert.equal(f.sent[0].decode_status,reason);assert.equal(f.image.src,'blob:old');
  assert.equal(f.context.state.cameraLive.objectUrl,'blob:old');
  assert.ok(!f.revoked.includes('blob:old'));obsolete();assert.equal(f.sent.length,1);
}
{
  const f=fixture();f.start();const obsolete=f.image.onload;
  f.context.state.cameraLive.socket={readyState:1};f.context.state.cameraLive.objectUrl='blob:other';
  obsolete();assert.equal(f.sent.length,0);assert.equal(f.context.state.cameraLive.objectUrl,'blob:other');
}
{
  const f=fixture(false);f.start();assert.equal(f.sent[0].decode_status,'not_visible');
  assert.equal(f.context.state.cameraLive.objectUrl,'blob:old');assert.ok(!f.revoked.includes('blob:old'));
}
console.log('Camera decode success/error/timeout/obsolete socket/hidden route: PASS');
