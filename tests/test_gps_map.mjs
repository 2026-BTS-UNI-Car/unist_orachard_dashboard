import assert from 'node:assert/strict';
import {FollowModel, validFix, frameAt, pathFeatures, accuracyCircle} from '../static/gps-map-model.mjs';
const sample = (latitude=35.57, longitude=129.19) => ({valid:true,latitude,longitude});
const model = new FollowModel();
assert.deepEqual(model.update(sample()), [129.19,35.57]);
model.userPan();
assert.equal(model.update(sample(35.58)), null);
assert.deepEqual(model.recenter(), [129.19,35.58]);
assert.deepEqual(model.update(sample(35.59)), [129.19,35.59]);
model.update({valid:false, state:'no_fix'});
assert.equal(model.fresh, false);
assert.deepEqual(model.position, [129.19,35.59]);
assert.equal(validFix({valid:true,latitude:null,longitude:null}), false);
assert.equal(validFix(sample(0,0)), true);
const frames = [{t_s:1,sample:sample()},{t_s:2,sample:sample(35.58)},
  {t_s:3,sample:{valid:false}},{t_s:4,sample:sample(35.59)},{t_s:5,sample:sample(35.60)},
  {t_s:15,sample:sample(35.61)},{t_s:16,sample:sample(35.62)}];
assert.equal(frameAt(frames, .5), null);
assert.equal(frameAt(frames, 1.8).t_s, 1);
assert.equal(frameAt(frames, 10), null);
assert.equal(frameAt(frames, 3.5).sample.valid, false);
assert.equal(pathFeatures(frames, 2).features.length, 1);
assert.equal(pathFeatures(frames).features.length, 3);
assert.equal(accuracyCircle([129.19,35.57],null).features.length, 0);
assert.equal(accuracyCircle([129.19,35.57],3).features[0].geometry.coordinates[0].length, 65);
console.log('GPS: default follow, pan/recenter, moving center, stale marker, replay gaps and track breaks passed');
