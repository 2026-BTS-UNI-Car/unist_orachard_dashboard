import assert from 'node:assert/strict';
import {jobLabel} from '../static/mapping-jobs.mjs';
assert.match(jobLabel({kind:'archive',recording_id:'record',state:'waiting_live_priority',processed_t_s:12.2,requested_t_s:19}),/실시간 매핑 우선/);
assert.match(jobLabel({kind:'live',state:'waiting_for_geometry',input_scans:22,scan_count:15,queue_scans:2,reason:'IMU_shock',last_progress_age_s:2.1}),/입력 22 \/ 지도 15/);
assert.match(jobLabel({kind:'live',state:'paused',diagnostic:{receipt_to_map_ms:76}}),/76ms/);
console.log('Mapping job labels: OK');
