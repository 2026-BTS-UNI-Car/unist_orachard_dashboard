import assert from 'node:assert/strict';
import {ArchiveSegmentGallery,visibleSegments} from '../static/archive-segments.mjs';
const rows=[{segment:0,start_s:0,end_s:4},{segment:1,start_s:5,end_s:8},{segment:2,start_s:10,end_s:12}];
assert.deepEqual(visibleSegments(rows,6),[{segment:0,start_s:0,end_s:4},{segment:1,start_s:5,end_s:6}]);
const node=()=>({children:[],className:'',addEventListener(name,fn){this[name]=fn;},setAttribute(name,value){this[name]=value;},
  append(value){this.children.push(value);},replaceChildren(){this.children=[];},querySelector(){return this.children.find(c=>c.tag==='span');}});
const root=node();
globalThis.document={getElementById:()=>root,createElement:tag=>({...node(),tag,
  getContext:()=>({drawImage(){}}),toDataURL:()=> 'data:image/png;base64,preview'})};
const seeks=[],gallery=new ArchiveSegmentGallery({canvas:{}},time=>seeks.push(time));
gallery.beforeCommit({}, {map_epoch:'a'});
gallery.beforeCommit({segment:0,map_epoch:'a',grid:{},frame_t_s:4},{segment:1,map_epoch:'a'});
gallery.update({map_epoch:'a',segment:1,segment_catalog:rows},6);
assert.equal(root.children.length,2);assert.equal(root.children[0].children[0].tag,'img');
const button=root.children[1];gallery.update({map_epoch:'a',segment:1,segment_catalog:rows},7);
assert.equal(root.children[1],button,'playback updates cannot replace a pressed/hovered navigation button');
button.click();assert.deepEqual(seeks,[7],'navigation uses the newest cursor-limited end time');
gallery.update({map_epoch:'a',segment:0,segment_catalog:rows},2);
assert.equal(root.children.length,1);assert.equal(root.children[0].children.length,1,'future preview is not shown on rewind');
gallery.beforeCommit({segment:0,map_epoch:'a',grid:{}},{segment:0,map_epoch:'b'});
assert.equal(gallery.previews.size,0);assert.equal(root.children.length,0,'another epoch does not reuse previews');
delete globalThis.document;
console.log('Retained map navigation, stable buttons, rewind and epoch isolation passed');
