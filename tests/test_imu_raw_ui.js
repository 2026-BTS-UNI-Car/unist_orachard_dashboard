// Test actual live/archive selection without a browser or any sensor access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = require('./helpers/app_source').readAppSource();
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
const raw = {orientation: {x:0,y:0,z:.5,w:Math.sqrt(3)/2}, euler_deg:{yaw:60}};
const oldCorrected = {orientation:{x:0,y:0,z:0,w:1}, euler_deg:{yaw:0}};
const context = {state:{imuLive:{raw,corrected:oldCorrected,correctedTopicAlive:true}}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function imuDisplaySample()'), source.indexOf('function vectorNorm(')), context);
assert.equal(context.imuDisplaySample(),raw);
context.state.imuLive.raw = {};
assert.deepEqual(context.imuDisplaySample(),{}); // never fall back to old rebased state
assert.ok(!html.includes('roverImuZeroButton'));
assert.ok(!source.includes('postJson("/api/imu/zero"'));
assert.ok(!source.includes('calibrationState === "collecting"'));
// Archive playback uses the recorded corrected sample when it exists. New
// frames with corrected:null select their raw sample without rewriting either.
const selection = source.match(/const imuSample = (imuFrame\?\.corrected \|\| imuFrame\?\.raw \|\| \{\});/);
assert.ok(selection,'historical archive choice is explicit');
const select = vm.runInNewContext(`(imuFrame) => (${selection[1]})`);
assert.equal(select({raw,corrected:oldCorrected}),oldCorrected);
assert.equal(select({raw,corrected:null}),raw);
console.log('IMU live raw + historical archive selection: PASS');
