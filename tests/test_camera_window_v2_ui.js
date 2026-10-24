const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const code=source.slice(source.indexOf('function resetCameraWindowDecode('),source.indexOf('function renderCameraLiveState('));
function fixture(){
 const sent=[],closed=[],revoked=[],timers=new Map();let next=0;
 const socket={readyState:1,send:raw=>sent.push(JSON.parse(raw)),close:(...args)=>closed.push(args)};
 const image={src:'blob:old'};
 const context={SIMULATION:false,state:{cameraLive:{socket,objectUrl:'blob:old'}},WebSocket:{OPEN:1},
  URL:{createObjectURL:()=>`blob:new${++next}`,revokeObjectURL:u=>revoked.push(u)},
  currentRoute:()=>'/rover-control',$:()=>image,renderCameraLiveState:()=>{},debugLog:()=>{},performance:{now:()=>1},
  window:{setTimeout:fn=>{const id=++next;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id)}};
 vm.createContext(context);vm.runInContext(code,context);
 const payload=(seq,session='a'.repeat(32))=>({ack_protocol:2,stream_session:session,source_epoch:1,delivery_sequence:seq,frame_count:seq});
 return{context,socket,image,sent,closed,revoked,timers,payload,
  send:(seq)=>context.displayWindowedCameraFrame({size:1000},payload(seq),socket),
  decoded:()=>{image.currentSrc=image.src;image.onload();}};
}
{
 const f=fixture();f.send(1);const firstURL=f.image.src;f.send(2);f.send(3);
 assert.equal(f.image.src,firstURL);assert.equal(f.context.state.cameraLive.v2Decode.pending.payload.delivery_sequence,3);
 assert.deepEqual(f.sent.map(x=>[x.delivery_sequence,x.decode_status]),[[2,'superseded']]);
 f.decoded();assert.notEqual(f.image.src,firstURL);f.decoded();
 assert.deepEqual(f.sent.map(x=>[x.delivery_sequence,x.decode_status]),[[2,'superseded'],[1,'decoded'],[3,'decoded']]);
 assert.equal(f.timers.size,0);assert.equal(f.context.state.cameraLive.v2Decode.pending,null);
 for(const row of f.sent)assert.deepEqual(Object.keys(row).sort(),['ack_protocol','decode_status','delivery_sequence','frame_count','source_epoch','stream_session','type'].sort());
}
{
 const f=fixture();f.send(1);f.send(2);const late=f.image.onload;
 f.context.displayWindowedCameraFrame({size:1},f.payload(3,'b'.repeat(32)),f.socket);
 assert.equal(f.closed[0][0],1002);assert.equal(f.context.state.cameraLive.v2Decode,null);
 assert.equal(f.image.src,'blob:old');late();assert.equal(f.sent.length,0);assert.equal(f.timers.size,0);
}
{
 const f=fixture();f.send(1);f.send(2);const late=f.image.onload;
 const next={readyState:1,send:raw=>f.sent.push(JSON.parse(raw)),close:()=>{}};f.context.state.cameraLive.socket=next;
 f.context.displayWindowedCameraFrame({size:1},f.payload(1,'c'.repeat(32)),next);
 late();assert.equal(f.sent.length,0);assert.equal(f.context.state.cameraLive.v2Decode.socket,next);
 f.decoded();assert.equal(f.sent[0].stream_session,'c'.repeat(32));
}
{
 const f=fixture();f.send(1);f.send(2);[...f.timers.values()][0]();f.image.onerror();
 assert.deepEqual(f.sent.map(x=>x.decode_status),['timeout','error']);assert.equal(f.image.src,'blob:old');
 assert.equal(f.context.state.cameraLive.objectUrl,'blob:old');
}
{
 const f=fixture();assert.throws(()=>f.context.displayWindowedCameraFrame({size:5*1024*1024+1},f.payload(1),f.socket));
 f.send(1);f.send(1);assert.equal(f.sent.length,0);f.decoded();assert.equal(f.sent.length,1);
}
{
 const f=fixture();f.context.displayBinaryCameraFrame({size:1},{frame_count:7},f.socket);f.decoded();
 assert.deepEqual(f.sent[0],{type:'frame_ack',frame_count:7,decode_status:'decoded'});
}
{
 const snippet=source.slice(source.indexOf('function cameraWebSocketUrl('),source.indexOf('function startCameraLive('));
 for(const [query,expected]of[['',false],['?camera_ack_window=3',true],['?camera_ack_window=4',false]]){
  const f=fixture();Object.assign(f.context,{URLSearchParams,location:{search:query,protocol:'http:',host:'127.0.0.1'},liveNetworkProfile:()=> 'detail'});
  f.context.state.cameraLive.resolution='1920x1080';f.context.state.cameraLive.fps=30;vm.runInContext(snippet,f.context);
  const u=new URL(f.context.cameraWebSocketUrl());assert.equal(u.searchParams.get('flow'),expected?'decode-ack-v2':'decode-ack');
 }
}
console.log('Camera V2 bounded latest pending, exact ACK, stale-source/socket, decode errors, V1 and opt-in: PASS');
