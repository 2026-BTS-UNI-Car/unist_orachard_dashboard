import assert from 'node:assert/strict';
import {NativeAcceptedMapClient,PoseHistoryNotice} from '../static/native-accepted-map.mjs';

const notice=new PoseHistoryNotice();
assert.equal(notice.update({state:'waiting'}),'');
assert.equal(notice.update({status:{map:{pose_history_complete:false,pose_history_missing_commits:0}}}),'');
const warning=notice.update({state:'observing',status:{map:{pose_history_complete:false,pose_history_missing_commits:12}}});
assert.match(warning,/12회 누락/);
assert.doesNotMatch(warning,/누적은 계속/);
for(const state of ['display_stopped','unavailable','waiting','new_map_denied']) {
  assert.equal(notice.update({state}),warning,`transport/display state ${state} must preserve a known incomplete ledger`);
}
assert.equal(notice.update({status:{map:{pose_history_complete:true,pose_history_missing_commits:0}}}),'');
assert.match(notice.update({status:{map:{pose_history_missing_commits:2**63}}}),/이력 일부 누락/);
assert.equal(notice.update({state:'new_map'}),'');
for(const missing of [-1,NaN,Infinity,'12','<script>',null,1.5]) {
  assert.equal(notice.update({status:{map:{pose_history_missing_commits:missing}}}),'');
}
assert.match(notice.update({status:{map:{pose_history_complete:false,pose_history_warning:'retained_pose_history_capacity',pose_history_missing_commits:0}}}),/이력 일부 누락/);

// Use the real client and presenter: an old healthy reply after Stop must not
// erase known loss, and an old complete page must release its returned pin.
const flags=Object.fromEntries(['runtime_authority','motor_command_sent','autonomy_output_enabled',
  'execution_eligible','physical_accuracy_verified','live_input_eligible'].map(key=>[key,false]));
for(const available of [false,true]) {
  let resolve;const deferred=new Promise(done=>{resolve=done;}),events=[],releases=[];
  const history=new PoseHistoryNotice();history.update({status:{map:{pose_history_missing_commits:12}}});
  const lease='e'.repeat(32);
  const client=new NativeAcceptedMapClient({request:async path=>{
    if(path.includes('release?')){releases.push(path);return {ok:true};}
    return deferred;
  },commit:()=>assert.fail('superseded reply committed'),status:value=>events.push({state:value.state,notice:history.update(value)})});
  client.schedule=()=>{};client.start();const pending=client.poll();client.stop();
  resolve({ok:true,available,...flags,page:available?{lease_id:lease}:undefined,
    status:{map:{pose_history_complete:true,pose_history_missing_commits:0}}});
  await pending;
  assert.deepEqual(events.map(event=>event.state),['display_stopped']);
  assert.match(events.at(-1).notice,/12회 누락/);
  assert.equal(releases.length,available?1:0);
  if(available)assert.ok(releases[0].endsWith(lease));
  assert.equal(client.busy,false);
}
console.log('pose history notice: loss shown, Stop/offline retained, explicit complete/reset clears');
