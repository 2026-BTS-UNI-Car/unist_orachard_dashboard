import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../static/app.js',import.meta.url),'utf8');
const start=source.indexOf('function recordingClockLabel(');
const end=source.indexOf('function renderRecordings(',start);
assert.ok(start>=0 && end>start);
const context=vm.createContext({});
vm.runInContext(source.slice(start,end),context);
for(const quality of ['host_unverified','host_clock',undefined,'unknown']) {
  context.payload={clock_quality:quality};
  assert.equal(vm.runInContext('recordingClockLabel(payload)',context),'장치 시각 미검증');
  assert.match(vm.runInContext('recordingClockDescription(payload)',context),/검증된 GPS 시간 정보가 없어/);
}
context.payload={clock_quality:'gnss_receiver_estimated'};
assert.equal(vm.runInContext('recordingClockLabel(payload)',context),'GPS UTC 기준');
assert.match(vm.runInContext('recordingClockDescription(payload)',context),/수신 지연은 보정하지 않았습니다/);
context.payload={clock_quality:'gnss_estimated'};
assert.equal(vm.runInContext('recordingClockLabel(payload)',context),'GPS 시각 보정');
assert.match(vm.runInContext('recordingClockDescription(payload)',context),/센서 시각과 재생 시간은 원본/);
console.log('Recording clock labels distinguish unverified host, receiver UTC estimate, and historical sidecar');
