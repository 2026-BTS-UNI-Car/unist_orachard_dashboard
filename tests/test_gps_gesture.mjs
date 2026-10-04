import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {FollowModel} from '../static/gps-map-model.mjs';
const handlers={},elements=new Map();
const element=id=>elements.get(id)||elements.set(id,{textContent:'',classList:{toggle(){}},setAttribute(){},
  addEventListener(event,fn,options){handlers[id+':'+event]={fn,options};}}).get(id);
const source=fs.readFileSync(new URL('../static/gps-map.js',import.meta.url),'utf8');
const classSource=source.slice(source.indexOf('class GpsMapView'),source.indexOf("const live = new GpsMapView"));
const scope={byId:element,FollowModel,Number,Infinity};
vm.runInNewContext(classSource+'\nglobalThis.View=GpsMapView;',scope);
const view=new scope.View('archive',true),jumps=[];
let resizeCount=0;
view.map={jumpTo:value=>jumps.push(value),getSource:()=>null,resize(){resizeCount++;}};
view.marker={setLngLat(){return this;},addTo(){return this;},remove(){}};
view.markerElement={classList:{toggle(){}},style:{setProperty(){}}};
const sample=n=>({valid:true,latitude:35+n/10000,longitude:129,speed_mps:1,course_deg:0});
view.sample=sample(0);view.model.update(view.sample);view.paint();assert.equal(jumps.length,1);
// Input is captured before MapLibre's threshold/dragstart and a fresh GPS frame.
handlers['archiveMap:pointerdown'].fn();
assert.equal(handlers['archiveMap:pointerdown'].options.capture,true);
view.sample=sample(1);view.model.update(view.sample);view.paint();
assert.equal(jumps.length,1,'GPS must not move the center during a user gesture');
assert.equal(view.model.follow,false);
handlers['archiveFollow:click'].fn();assert.equal(view.model.follow,true);assert.equal(jumps.length,2);
await view.init();await view.init();assert.equal(resizeCount,0,'GPS paint must not resize the WebGL map');
handlers['archiveMap:wheel'].fn();assert.equal(view.model.follow,false);
console.log('GPS early gesture capture, moving-frame race, recenter and resize isolation passed');
