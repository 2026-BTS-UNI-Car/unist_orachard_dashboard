import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../static/app.js',import.meta.url),'utf8');
const functionSource=source.slice(source.indexOf('function trimArchiveTimelineCache() {'),source.indexOf('function archiveFrameKey('));
const revoked=[];
const cache={objectUrls:new Map(),imageElements:new Map(),gpsFrames:[{t_s:0},{t_s:1000}],motorFrames:[{t_s:0}]};
for(const kind of ['lidar','camera','imu']) {
  cache[`${kind}Frames`]=Array.from({length:1000},(_,i)=>({t_s:i,cacheKey:`${kind}:${i}`}));
  cache[`${kind}Keys`]=new Set(cache[`${kind}Frames`].map(f=>f.cacheKey));
}
cache.objectUrls.set('camera:0','blob:old');cache.objectUrls.set('camera:500','blob:current');
cache.imageElements.set('camera:0',{});cache.imageElements.set('camera:500',{});
const state={archivePlayback:{timeS:500,segmentDurationS:4,cache,segmentCache:new Map(Array.from({length:250},(_,i)=>[String(i),{start_s:i*4,duration_s:4}]))}};
const context=vm.createContext({state,ARCHIVE_SEGMENT_SECONDS:4,URL:{revokeObjectURL:v=>revoked.push(v)}});
vm.runInContext(functionSource,context);vm.runInContext('trimArchiveTimelineCache()',context);
for(const kind of ['lidar','camera','imu']) {
  const expected=kind==='imu'?72:24;
  assert.equal(cache[`${kind}Frames`].length,expected);assert.equal(cache[`${kind}Keys`].size,expected);
  assert.equal(cache[`${kind}Frames`][0].t_s,kind==='imu'?476:492);assert.equal(cache[`${kind}Frames`].at(-1).t_s,kind==='imu'?547:515);
}
assert.equal(state.archivePlayback.segmentCache.size,6);assert.deepEqual(revoked,['blob:old']);
assert.equal(cache.gpsFrames.length,2);assert.equal(cache.motorFrames.length,1);
assert.equal(cache.imageElements.size,1);assert.equal(cache.objectUrls.size,1);
state.archivePlayback.timeS=0;vm.runInContext('trimArchiveTimelineCache()',context);
assert.equal(state.archivePlayback.segmentCache.size,0);assert.equal(cache.lidarKeys.size,0);
console.log('bounded playback window, refetch on backward seek, image release and GPS history passed');
