import assert from 'node:assert/strict';
import {ArchiveMapBuffer} from '../static/archive-map-buffer.mjs';
const tile=(x,label)=>({x,y:0,image_base64:label});
const seed={tile_epoch:'a',tile_version:1,version:1,frame_t_s:0,segments:3,needs_regeneration:true,tiles:[tile(0,'old')],path:[{stamp:0}]};
const events=[{tile_epoch:'a',tile_version:2,version:2,frame_t_s:.1,tiles:[tile(0,'changed')],path:[{stamp:.1}]},
 {tile_epoch:'b',tile_version:3,version:3,frame_t_s:.2,tiles_reset:true,tiles:[tile(9,'new segment')],path:[{stamp:.2}]}];
const buffer=new ArchiveMapBuffer({start_s:0,end_s:1,seed,events,
 segment_catalog:[{segment:0,start_s:0,end_s:.1},{segment:1,start_s:.2,end_s:1}]});
const early=buffer.at(.05);assert.equal(early.version,1);assert.equal(early.tiles[0].image_base64,'old');
assert.deepEqual(early.segment_catalog,[{segment:0,start_s:0,end_s:.05}]);
const delta=buffer.at(.15,1,'a');assert.equal(delta.version,2);assert.equal(delta.path_append,true);assert.equal(delta.tiles[0].image_base64,'changed');
assert.equal(delta.segments,3);assert.equal(delta.needs_regeneration,true);
const disconnected=buffer.at(.25,2,'a');assert.equal(disconnected.tiles_reset,true);assert.deepEqual(disconnected.path,[{stamp:.2}]);assert.equal(disconnected.tiles[0].x,9);
const back=buffer.at(.05,3,'b');assert.equal(back.tiles_reset,true);assert.equal(back.version,1);assert.equal(back.tiles[0].x,0);
assert.equal(back.segment_catalog.length,1,'buffered navigation hides future segments');
assert.equal(buffer.at(2),null);
const retained=buffer.at(.25,3,'b');
assert.equal(retained.tiles_reset,false,'already displayed segment must not clear again inside the same window');
assert.deepEqual(retained.tiles,[]);assert.deepEqual(retained.path,[]);
const cold=new ArchiveMapBuffer({start_s:0,end_s:2,
 seed:{version:0,scan_count:0,tiles:[],tiles_reset:true},events:[
  {tile_epoch:'continuous',tile_version:1,version:1,frame_t_s:.1,tiles_reset:true,tiles:[tile(0,'first')],path:[{stamp:.1}]},
  {tile_epoch:'continuous',tile_version:2,version:2,frame_t_s:.2,tiles_reset:false,tiles:[tile(1,'second')],path:[{stamp:.2}]}]});
assert.equal(cold.at(.1).tiles_reset,true);
const next=cold.at(.2,1,'continuous');
assert.equal(next.tiles_reset,false,'a seed before the first scan must not erase established tiles');
assert.deepEqual(next.tiles,[tile(1,'second')]);assert.deepEqual(next.path,[{stamp:.2}]);
assert.equal(cold.at(.2,2,'continuous').tiles_reset,false);
assert.equal(cold.at(0,2,'continuous').scan_count,0,'backward seek still clears future cells');
// Next windows overlap the end of the previous one. Even if its last render
// was just before the boundary, the displayed version remains incremental.
const overlap=new ArchiveMapBuffer({start_s:10,end_s:22,
 seed:{tile_epoch:'continuous',tile_version:100,version:100,tiles:[tile(0,'retained')],path:[]},
 events:[{tile_epoch:'continuous',tile_version:120,version:120,frame_t_s:12,tiles:[tile(1,'end')],path:[{stamp:12}]},
         {tile_epoch:'continuous',tile_version:121,version:121,frame_t_s:12.1,tiles:[tile(2,'new')],path:[{stamp:12.1}]}]});
assert.equal(overlap.at(12.1,120,'continuous').tiles_reset,false);
assert.deepEqual(overlap.at(12.1,120,'continuous').tiles,[tile(2,'new')]);
console.log('Map cache playback cursor, backward seek and disconnected submaps passed');
