import assert from 'node:assert/strict';
import {worldBounds, occupiedBounds, occupiedBoundsFromPixels, occupiedCellsFromPixels, nearbyWorldBounds, fullWorldBounds, fittedView, worldToScreen, pathOpacity, archivePath, virtualGpsPoint, orchardLayout} from '../static/sim-map-model.mjs';

// Fit occupied objects and the current rover, not vast free space or old paths.
// Screen Y must not mirror the ROS frame.
const survey={grid:{origin:{x:-50,y:-50},width:1000,height:1000,resolution:.1,
  occupied_bounds:{minX:-3,minY:-2,maxX:7,maxY:3}},path:[{x:90,y:40,stamp:1}],pose:{x:9,y:4,yaw:0}};
const bounds=worldBounds(survey);
assert.deepEqual(bounds,{minX:-4,minY:-3,maxX:10,maxY:5});
const view=fittedView(bounds,720,400);
for(const corner of [{x:bounds.minX,y:bounds.minY},{x:bounds.maxX,y:bounds.maxY}]){
 const p=worldToScreen(corner,view,720,400);assert.ok(p.x>=27&&p.x<=693&&p.y>=27&&p.y<=373);
}
assert.ok(worldToScreen({x:0,y:1},view,720,400).y<worldToScreen({x:0,y:0},view,720,400).y);
assert.equal(survey.path.length,1,'bounds calculation must not mutate the recorded path');
assert.equal(survey.grid.occupied_bounds.minX,-3,'padding must not mutate published occupancy');
assert.deepEqual(worldBounds({...survey,grid:{...survey.grid,occupied_bounds:null}}),{minX:7,minY:2,maxX:11,maxY:6});
assert.deepEqual(worldBounds({path:[{x:100,y:100}]}),{minX:-2,minY:-2,maxX:2,maxY:2});
assert.deepEqual(worldBounds({pose:{x:3,y:4}}),{minX:1,minY:2,maxX:5,maxY:6});
assert.equal(occupiedBounds({...survey.grid,occupied_bounds:{minX:-51,minY:0,maxX:0,maxY:2}}),undefined);
assert.equal(occupiedBounds({...survey.grid,occupied_bounds:{minX:0,minY:0,maxX:0,maxY:2}}),undefined);
assert.equal(occupiedBounds({...survey.grid,occupied_bounds:{minX:0,minY:0,maxX:NaN,maxY:2}}),undefined);
assert.deepEqual(worldBounds({grid:{width:Infinity,height:1,resolution:.1,origin:{x:0,y:0}}}),{minX:-2,minY:-2,maxX:2,maxY:2});

// Legacy archives decode only the verified palette and row convention. Include
// whole occupied cells, including the upper outer edge of the last grid row.
const legacyGrid={width:3,height:2,resolution:.5,origin:{x:-2,y:4},encoding:'png',row_order:'min_y_first'};
const pixels=new Uint8ClampedArray([245,25,127,127,245,25].flatMap(value=>[value,value,value,255]));
const decoded=occupiedBoundsFromPixels(legacyGrid,pixels);
assert.deepEqual(decoded,{minX:-1.5,minY:4,maxX:-.5,maxY:5});
assert.deepEqual(worldBounds({grid:legacyGrid},decoded),{minX:-2.5,minY:3,maxX:.5,maxY:6});
const noHits=new Uint8ClampedArray([245,245,127,127,245,127].flatMap(value=>[value,value,value,255]));
assert.equal(occupiedBoundsFromPixels(legacyGrid,noHits),null);
assert.deepEqual(worldBounds({grid:{...legacyGrid,occupied_bounds:null}},decoded),{minX:-3.25,minY:2.5,maxX:.75,maxY:6.5},'explicit empty metadata takes precedence over a decoded fallback');
assert.equal(occupiedBoundsFromPixels({...legacyGrid,row_order:'max_y_first'},pixels),undefined);
assert.equal(occupiedBoundsFromPixels({...legacyGrid,encoding:'jpeg'},pixels),undefined);
assert.equal(occupiedBoundsFromPixels(legacyGrid,pixels.subarray(4)),undefined);
for(const bad of [[0,0,0,255],[25,25,25,0],[25,24,25,255]]){
 const malformed=pixels.slice();malformed.set(bad,0);
 assert.equal(occupiedBoundsFromPixels(legacyGrid,malformed),undefined);
}
assert.deepEqual(worldBounds({grid:legacyGrid}),{minX:-3,minY:3,maxX:.5,maxY:6},'unknown palette falls back to the raster, without claiming occupancy');

// The default viewport is local, not a semantic filter: a distant occupied cell
// remains in the original raster/index even when it does not drive the fit.
const localGrid={width:40,height:40,resolution:1,origin:{x:-20.5,y:-20.5},encoding:'png',row_order:'min_y_first'};
const localPixels=new Uint8ClampedArray(40*40*4).fill(245);
for(let i=3;i<localPixels.length;i+=4)localPixels[i]=255;
for(const [x,y] of [[0,0],[12,0],[12,1],[-19,-19]])localPixels.set([25,25,25,255],((y+20)*40+x+20)*4);
const cells=occupiedCellsFromPixels(localGrid,localPixels);
assert.equal(cells.indices.length,4);
assert.deepEqual(nearbyWorldBounds({grid:localGrid,pose:{x:0,y:0}},cells),{minX:-1.5,minY:-1.5,maxX:13.5,maxY:1.5},'12 m circle includes its boundary but excludes the diagonal point beyond it');
assert.equal(cells.indices.length,4,'local fit must retain far occupied cells');
assert.deepEqual(nearbyWorldBounds({grid:localGrid,pose:{x:100,y:100}},cells),{minX:98,minY:98,maxX:102,maxY:102});
assert.deepEqual(nearbyWorldBounds({grid:localGrid,pose:{x:2,y:3}},undefined),{minX:-10,minY:-9,maxX:14,maxY:15},'unknown palette uses an explicit local window');
assert.deepEqual(nearbyWorldBounds({grid:localGrid,pose:{x:NaN,y:0}},cells),{minX:-20.5,minY:-20.5,maxX:13.5,maxY:2.5},'invalid pose falls back to all known objects');
assert.deepEqual(fullWorldBounds({grid:localGrid,pose:{x:0,y:0},path:[{x:500,y:500}]}),{minX:-21.5,minY:-21.5,maxX:20.5,maxY:20.5},'Full fits the complete raster regardless of occupancy or historic paths');
assert.deepEqual(fullWorldBounds({grid:localGrid,pose:{x:100,y:100}}),{minX:-21.5,minY:-21.5,maxX:101,maxY:101});

// Old route segments remain visible indefinitely; later segments are stronger.
assert.equal(pathOpacity(1,1),1);
assert.ok(pathOpacity(60,100)>pathOpacity(0,100));
assert.equal(pathOpacity(0,1e9),.22);
assert.equal(pathOpacity(100,50),1,'clock rewind must not create alpha above 1');

// Archive scrub uses the recorder wall-time t_s even when simulation runs slowly.
const path=[{x:0,stamp:100,t_s:0},{x:1,stamp:101,t_s:4},{x:2,stamp:102,t_s:8}];
assert.equal(archivePath(path,3,100).length,1);
assert.equal(archivePath(path,4,100).length,2);
assert.equal(archivePath(path,0,100).length,1);
assert.equal(archivePath([{stamp:20},{stamp:22}],1,20).length,1);
assert.equal(path.length,3);

// Invalid fixes never place a GPS marker at geographic zero; local projection is metric.
assert.equal(virtualGpsPoint({valid:false,latitude:0,longitude:0}),null);
assert.deepEqual(virtualGpsPoint({valid:true,local_x:3,local_y:-2,yaw:1,stamp:10}),{x:3,y:-2,yaw:1,stamp:10});
assert.equal(virtualGpsPoint({valid:true,local_x:0,local_y:0,stamp:0,received_at:1000}).stamp,0);
const gps=virtualGpsPoint({valid:true,latitude:37+10/111320,longitude:127,origin:{latitude:37,longitude:127}});
assert.ok(Math.abs(gps.y-10)<1e-7);assert.equal(gps.x,0);

// The basemap comes from the recorded world's rows and spawn, never an assumed centre.
const layout=orchardLayout({spawn:[-3,0,-.05],row_y:[-1.9,1.9,5.7,9.5,13.3,17.1],row_x_range:[0,30]});
assert.deepEqual(layout.rows,[-1.9,1.9,5.7,9.5,13.3,17.1]);
assert.equal(layout.minX,3);assert.equal(layout.maxX,33);
const shifted=orchardLayout({spawn:[-3,-9.5,0],row_y:[-11.4,-7.6],row_x_range:[0,30]});
assert.ok(Math.abs(shifted.rows[0]+1.9)<1e-9&&Math.abs(shifted.rows[1]-1.9)<1e-9);
assert.equal(orchardLayout({}),null,'missing archive layout must not invent a different orchard');
assert.equal(orchardLayout({spawn:[0,0,0],row_y:[NaN],row_x_range:[0,30]}),null);
console.log('PASS: metric map bounds/autofit, ROS axis orientation, permanent fading path, recording-time scrub, virtual GPS projection and actual-world layout');
