import assert from 'node:assert/strict';
import {receptionText, usedSatellitesText} from '../static/gps-map-model.mjs';
const report = (extra={}) => ({state:'fresh', age_s:1, detected_count:3, tracked_count:2, ...extra});
const sample = {state:'no_fix', connected:true, valid:false, satellites:0,
  reception:{nav_sig:report(), nav_sat:report({detected_count:2, tracked_count:1})}};
assert.equal(usedSatellitesText(sample), '위치계산 사용 0개');
assert.equal(receptionText(sample), '신호 검출 3 · 추적 2 (1.0초 전) / 위성 검출 2 · 추적 1 (1.0초 전)');
assert.match(receptionText(sample, 12), /신호 진단 오래됨/);
assert.match(receptionText(sample, 11), /12.0초 전/);
assert.equal(receptionText({}), '신호 진단 미보고');
assert.equal(receptionText({reception:{nav_sig:{state:'no_data'}}}), '신호 진단 미보고');
assert.equal(receptionText({reception:{nav_sig:report({detected_count:0,tracked_count:0})}}), '신호 검출 0 · 추적 0 (1.0초 전)');
assert.equal(receptionText({reception:{nav_sig:report({state:'disconnected'})}}), '신호 진단 연결 끊김');
for(const value of [null, true, NaN, -1, 121])
  assert.match(receptionText({reception:{nav_sig:report({detected_count:value})}}), /확인 불가/);
for(const value of [null, true, NaN, -1, 256]) assert.equal(usedSatellitesText({satellites:value}), '위치계산 사용 —개');
assert.equal(usedSatellitesText({...sample,state:'stale'}), '위치계산 사용 —개');
assert.equal(receptionText(sample),receptionText(sample,0)); // Recorded age stays historical, independent of wall date.
console.log('GPS reception UI model: PASS');
