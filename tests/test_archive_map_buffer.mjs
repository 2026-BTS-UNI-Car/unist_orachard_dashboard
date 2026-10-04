import assert from 'node:assert/strict';
import {ArchiveMapBuffer} from '../static/archive-map-buffer.mjs';
const tile=(x,label)=>({x,y:0,image_base64:label});
const seed={tile_epoch:'a',tile_version:1,version:1,frame_t_s:0,tiles:[tile(0,'old')],path:[{stamp:0}]};
const events=[{tile_epoch:'a',tile_version:2,version:2,frame_t_s:.1,tiles:[tile(0,'changed')],path:[{stamp:.1}]},
 {tile_epoch:'b',tile_version:3,version:3,frame_t_s:.2,tiles_reset:true,tiles:[tile(9,'new segment')],path:[{stamp:.2}]}];
const buffer=new ArchiveMapBuffer({start_s:0,end_s:1,seed,events});
const early=buffer.at(.05);assert.equal(early.version,1);assert.equal(early.tiles[0].image_base64,'old');
const delta=buffer.at(.15,1,'a');assert.equal(delta.version,2);assert.equal(delta.path_append,true);assert.equal(delta.tiles[0].image_base64,'changed');
const disconnected=buffer.at(.25,2,'a');assert.equal(disconnected.tiles_reset,true);assert.deepEqual(disconnected.path,[{stamp:.2}]);assert.equal(disconnected.tiles[0].x,9);
const back=buffer.at(.05,3,'b');assert.equal(back.tiles_reset,true);assert.equal(back.version,1);assert.equal(back.tiles[0].x,0);
assert.equal(buffer.at(2),null);
console.log('Map cache playback cursor, backward seek and disconnected submaps passed');
