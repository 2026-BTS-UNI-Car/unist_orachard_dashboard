import assert from 'node:assert/strict';
import {deflateSync} from 'node:zlib';
import {decodeArchiveWindow} from '../static/archive-window-codec.mjs';
import {ArchiveMapBuffer} from '../static/archive-map-buffer.mjs';
const raw=new Uint8Array(2048);raw.fill(0x55,0,1024);raw[1024]=3;
const window={cell_codec:'xor2-deflate-v1',cell_blob_bytes:2048,cell_blob:deflateSync(raw).toString('base64'),start_s:0,end_s:1,
 seed:{tile_epoch:'a',tile_version:1,version:1,frame_t_s:0,tiles:[{x:0,y:0,encoding:'packed2-v1',cells:0}]},
 events:[{tile_epoch:'a',tile_version:2,version:2,frame_t_s:.5,tiles:[{x:0,y:0,encoding:'xor2-v1',cells:1024}]}]};
const decoded=await decodeArchiveWindow(window);assert.equal(decoded.cell_codec,'xor2-v1');
assert.equal(window.seed.tiles[0].cells,0,'downloaded source is immutable');
const buffer=new ArchiveMapBuffer(decoded);
assert.equal(atob(buffer.at(.2).tiles[0].cells).charCodeAt(0),0x55);
assert.equal(atob(buffer.at(.7).tiles[0].cells).charCodeAt(0),0x56,'exact 2-bit XOR is applied');
assert.equal(atob(buffer.at(.2,2,'a').tiles[0].cells).charCodeAt(0),0x55,'rewind restores the original cell state');
for(const change of [{cell_blob_bytes:1024},{cell_blob_bytes:2049},{cell_blob_bytes:65*1024*1024},
 {events:[{tiles:[{encoding:'xor2-v1',cells:1025}]}]}])await assert.rejects(decodeArchiveWindow({...window,...change}));
const legacy={cell_codec:'xor2-v1'};assert.equal(await decodeArchiveWindow(legacy),legacy);
console.log('Lossless compressed map windows, bounded inflate, invalid offsets and backward seek passed');
