import assert from 'node:assert/strict';
import {ArchiveMapBuffer} from '../static/archive-map-buffer.mjs';
import {decodeTileCells} from '../static/grid-cell-codec.mjs';
const base=new Uint8Array(1024);base[0]=1;
const first=new Uint8Array(1024);first[0]=3;
const second=new Uint8Array(1024);second[1]=2;
const encoded=bytes=>btoa(String.fromCharCode(...bytes));
const tile=(encoding,bytes)=>({x:-1,y:2,encoding,cells:encoded(bytes)});
const window={cell_codec:'xor2-v1',start_s:0,end_s:1,seed:{tile_version:1,tile_epoch:'a',tiles:[tile('packed2-v1',base)]},events:[
 {frame_t_s:.1,tile_version:2,tile_epoch:'a',tiles:[tile('xor2-v1',first)]},
 {frame_t_s:.2,tile_version:3,tile_epoch:'a',tiles:[tile('xor2-v1',second)]},
 {frame_t_s:.3,tile_version:4,tile_epoch:'b',tiles_reset:true,tiles:[tile('packed2-v1',base)]},
]};
const buffer=new ArchiveMapBuffer(window);
assert.equal(decodeTileCells(buffer.at(.05).tiles[0])[0],1);
assert.equal(decodeTileCells(buffer.at(.15).tiles[0])[0],2);
assert.equal(decodeTileCells(buffer.at(.25).tiles[0])[4],2);
assert.equal(decodeTileCells(buffer.at(.35).tiles[0])[0],1);
assert.equal(decodeTileCells(buffer.at(.05).tiles[0])[0],1,'backward seek must reconstruct independent original seed');
assert.equal(window.events[0].tiles[0].encoding,'xor2-v1','downloaded source must remain immutable');
assert.throws(()=>new ArchiveMapBuffer({...window,seed:{tiles:[]}}),/base/);
console.log('Lossless seeded cell changes, playback clock, submap reset and backward seek verified');
