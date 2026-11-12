// NET-07: the page reads polar-v2 LiDAR scans into the same points as polar-v1.
// The bytes below were made by scripts/lidar_ws_bridge.encode_scan_v2 for a six-beam
// scan (two beams without a return); polar-v1 of the same scan is `expected`.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource();
const code = source.slice(source.indexOf('const LIDAR_BINARY_MAGIC'), source.indexOf('function handleLidarLiveMessage('));
const context = {TextDecoder, DataView, Uint8Array, JSON, Number, Math, String, Array, Error};
vm.createContext(context);
vm.runInContext(code, context);

const hex = '554c533202000001167b2274797065223a227363616e222c226672616d655f6964223a226c61736572222c22736f75726365223a22726f7332222c227374616d70223a31322e352c2273657175656e6365223a372c22706f696e745f636f756e74223a342c22736f757263655f706f696e745f636f756e74223a342c227265717565737465645f6d61785f706f696e7473223a342c22636f7665726167655f646567223a342c22706f696e745f656e636f64696e67223a22706f6c61722d7632222c22616e676c655f656e636f64696e67223a226265616d5f696e646578222c22616e676c655f6d696e5f646567223a2d3138302e302c22616e676c655f696e6372656d656e745f646567223a35392e39393939393939393939393939397d0000020004000500dc05ca083930cc740a141e2f';
const bytes = Uint8Array.from(hex.match(/../g).map(pair => parseInt(pair, 16)));
const expected = [[180.0, 1.5, 10], [300.0, 2.25, 20], [60.0, 12.345, 30], [120.0, 29.9, 47]];

const scan = context.decodeLidarScanV2(bytes.buffer);
assert.equal(scan.type, 'scan');
assert.equal(scan.sequence, 7);
assert.equal(scan.stamp, 12.5);
assert.equal(scan.point_encoding, 'polar-v1', 'handed on as polar-v1');
assert.deepEqual(JSON.parse(JSON.stringify(scan.points)), expected, 'the same points as polar-v1');

assert.throws(() => context.decodeLidarScanV2(bytes.slice(0, bytes.length - 1).buffer), /wrong length/);
const wrong = bytes.slice(); wrong[4] = 1;
assert.throws(() => context.decodeLidarScanV2(wrong.buffer), /protocol mismatch/);
console.log('LiDAR polar-v2 decode gives the polar-v1 points: PASS');
