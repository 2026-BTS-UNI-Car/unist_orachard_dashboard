import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
const source=(await import('node:module')).createRequire(import.meta.url)('./helpers/app_source.js').readAppSource();
const functions=source.slice(source.indexOf('function suspendLiveCameraForArchive() {'),source.indexOf('async function toggleArchivePlayback() {'));
const state={telemetry:{services:{rover_bridge:{armed:false,updated_at:100}}},roverControl:{},
  cameraLive:{socket:{},userStopped:false},archivePlayback:{resumeLiveCamera:false}};
let closed=0,started=0,route=true;
const context=vm.createContext({state,Date:{now:()=>101000},Number,
  closeCameraLive(){closed++;state.cameraLive.socket=null;state.cameraLive.userStopped=true;},
  startCameraLive(){started++;state.cameraLive.socket={};state.cameraLive.userStopped=false;},
  routeSupportsCameraLive:()=>route});
vm.runInContext(functions,context);
const call=n=>vm.runInContext(n+'()',context);
call('suspendLiveCameraForArchive');assert.equal(closed,1);assert.equal(state.archivePlayback.resumeLiveCamera,true);
call('resumeLiveCameraAfterArchive');assert.equal(started,1);call('resumeLiveCameraAfterArchive');assert.equal(started,1);
for(const status of [{armed:true,updated_at:100},{updated_at:100},{armed:false,updated_at:90},{armed:false,updated_at:102}]) {
  state.telemetry.services.rover_bridge=status;call('suspendLiveCameraForArchive');assert.equal(closed,1);
}
state.telemetry.services.rover_bridge={armed:false,updated_at:100};state.roverControl.lease='active';
call('suspendLiveCameraForArchive');assert.equal(closed,1);delete state.roverControl.lease;
// User choice during playback cancels restoration; leaving the live route
// must not open a stream either.
call('suspendLiveCameraForArchive');state.archivePlayback.resumeLiveCamera=false;
call('resumeLiveCameraAfterArchive');assert.equal(started,1);
state.archivePlayback.resumeLiveCamera=true;route=false;call('resumeLiveCameraAfterArchive');assert.equal(started,1);
assert.equal(state.archivePlayback.resumeLiveCamera,false);
// Production services is a summary without armed/updated_at. Use the raw
// connection bridge and never let its active state be replaced by a summary.
route=true;state.cameraLive.socket={};state.cameraLive.userStopped=false;
state.telemetry={services:{rover_bridge:{ok:true}},connection:{rover_bridge:{armed:true,updated_at:100,alive:true}}};
call('suspendLiveCameraForArchive');assert.equal(closed,2);
state.telemetry.connection.rover_bridge.armed=false;
call('suspendLiveCameraForArchive');assert.equal(closed,3);assert.equal(state.archivePlayback.resumeLiveCamera,true);
call('resumeLiveCameraAfterArchive');assert.equal(started,2);
state.telemetry.connection.rover_bridge.alive=false;call('suspendLiveCameraForArchive');assert.equal(closed,3);
console.log('archive/live bandwidth contention, driver state, manual choice and route lifecycle passed');
