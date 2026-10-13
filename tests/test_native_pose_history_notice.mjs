import assert from 'node:assert/strict';
import {PoseHistoryNotice} from '../static/native-accepted-map.mjs';

const notice=new PoseHistoryNotice();
assert.equal(notice.update({state:'waiting'}),'');
assert.equal(notice.update({status:{map:{pose_history_complete:false,pose_history_missing_commits:0}}}),'');
const warning=notice.update({state:'observing',status:{map:{pose_history_complete:false,pose_history_missing_commits:12}}});
assert.match(warning,/12회 누락/);
for(const state of ['display_stopped','unavailable','waiting','new_map_denied']) {
  assert.equal(notice.update({state}),warning,`transport/display state ${state} must preserve a known incomplete ledger`);
}
assert.equal(notice.update({status:{map:{pose_history_complete:true,pose_history_missing_commits:0}}}),'');
assert.match(notice.update({status:{map:{pose_history_missing_commits:2**63}}}),/이력 일부 누락/);
assert.equal(notice.update({state:'new_map'}),'');
for(const missing of [-1,NaN,Infinity,'12','<script>',null,1.5]) {
  assert.equal(notice.update({status:{map:{pose_history_missing_commits:missing}}}),'');
}
console.log('pose history notice: loss shown, Stop/offline retained, explicit complete/reset clears');
