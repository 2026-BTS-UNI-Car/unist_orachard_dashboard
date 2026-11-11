// Actual bundle(), init() and create() run in a VM with generated fetch/DOM.
// No sockets, browser, real map files, dashboard API or rover commands.
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import vm from 'node:vm';
import * as model from '../static/gps-map-model.mjs';

const args=process.argv.slice(2),value=key=>args.includes(key)?args[args.indexOf(key)+1]:null;
const path=value('--source')||new URL('../static/gps-map.js',import.meta.url);
const original=readFileSync(path,'utf8');
const code=original.slice(original.indexOf('let bundlePromise'),original.indexOf('async function localStyle'))+
 original.slice(original.indexOf('class GpsMapView'),original.indexOf('const live = new GpsMapView'))+
 '\nglobalThis.View=GpsMapView;';
const ok=()=>({ok:true,status:200,json:async()=>({ok:true,bytes:100,data_date:'generated'})});
const defer=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
function harness(replies,options={}){
 let clock=0,fetches=0,maps=0,fonts=0,styles=0;const errors=[];
 const el={textContent:'',classList:{toggle(){}},setAttribute(){},addEventListener(){}};
 const context={...model,console,performance:{now:()=>clock},Number,Math,Infinity,
  byId:()=>el,text:(_id,message)=>errors.push(message),
  document:{fonts:{add(){}},createElement:()=>({...el})},
  FontFace:class{async load(){fonts++;return this;}},
  ResizeObserver:class{observe(){}},
  localStyle:async()=>{styles++;if(options.failStyleOnce&&styles===1)throw Error('generated_style_failure');return{};},
  fetch:()=>{const next=replies[fetches++];if(next instanceof Error)return Promise.reject(next);return Promise.resolve(next);},
  maplibregl:{Map:class{constructor(){maps++;}addControl(){}on(){}},NavigationControl:class{},ScaleControl:class{},Marker:class{}}
 };
 vm.createContext(context);vm.runInContext(code,context,{timeout:1000});
 return{view:prefix=>new context.View(prefix,true),at:t=>{clock=t;},get fetches(){return fetches;},
  get maps(){return maps;},get fonts(){return fonts;},errors};
}
const rows=[];
async function test(name,fn){try{await fn();rows.push({name,PASS:true});}catch(error){rows.push({name,PASS:false,error:String(error.message).slice(0,240)});}}

await test('HTTP503 cannot cache successful-looking JSON and recovers after bounded retry',async()=>{
 const h=harness([{ok:false,status:503,json:async()=>({ok:true})},ok()]),v=h.view('archive');
 await v.init();assert.equal(h.maps,0);assert.equal(h.fetches,1);
 for(let i=0;i<100;i++)await v.init();assert.equal(h.fetches,1);
 h.at(1999);await v.init();assert.equal(h.fetches,1);
 h.at(2000);await v.init();assert.equal(h.fetches,2);assert.equal(h.maps,1);
});
await test('network rejection releases failed shared metadata promise',async()=>{
 const h=harness([Error('generated_network_failure'),ok()]),v=h.view('archive');
 await v.init();h.at(2000);await v.init();assert.equal(h.fetches,2);assert.equal(h.maps,1);
});
await test('offline-map ok=false is retryable when metadata becomes ready',async()=>{
 const h=harness([{ok:true,status:200,json:async()=>({ok:false,error:'generated_missing_bundle'})},ok()]),v=h.view('archive');
 await v.init();assert.equal(h.maps,0);h.at(2000);await v.init();assert.equal(h.maps,1);
});
await test('malformed JSON does not poison all future map initialization',async()=>{
 const h=harness([{ok:true,status:200,json:async()=>{throw new SyntaxError('generated_JSON');}},ok()]),v=h.view('archive');
 await v.init();h.at(2000);await v.init();assert.equal(h.maps,1);assert.equal(h.fetches,2);
});
await test('live and replay still share one successful metadata fetch',async()=>{
 const h=harness([ok()]),live=h.view('live'),archive=h.view('archive');
 await Promise.all([live.init(),archive.init()]);assert.equal(h.fetches,1);assert.equal(h.maps,2);
 for(let i=0;i<100;i++)await Promise.all([live.init(),archive.init()]);
 assert.equal(h.fetches,1);assert.equal(h.maps,2);
});
await test('pending init remains single-flight while many sensor ticks arrive',async()=>{
 const gate=defer(),h=harness([gate.promise]),v=h.view('archive');
 const first=v.init(),rest=[];for(let i=0;i<1000;i++)rest.push(v.init());
 assert.equal(h.fetches,1);gate.resolve(ok());await Promise.all([first,...rest]);
 assert.equal(h.maps,1);assert.equal(h.fonts,1);
});
await test('font/style setup failure is backoff-limited without discarding good metadata',async()=>{
 const h=harness([ok()],{failStyleOnce:true}),v=h.view('archive');
 await v.init();for(let i=0;i<100;i++)await v.init();assert.equal(h.fonts,1);
 h.at(2000);await v.init();assert.equal(h.maps,1);assert.equal(h.fetches,1);
});
await test('manual pan remains released across a failed init and recovery',async()=>{
 const h=harness([Error('generated_network_failure'),ok()]),v=h.view('archive');
 v.model.userPan();await v.init();h.at(2000);await v.init();assert.equal(v.model.follow,false);assert.equal(h.maps,1);
});
await test('two views share one pending failure then one pending recovery',async()=>{
 const first=defer(),second=defer(),h=harness([first.promise,second.promise]),a=h.view('a'),b=h.view('b');
 const waiting=Promise.all([a.init(),b.init()]);assert.equal(h.fetches,1);
 first.reject(Error('generated_network_failure'));await waiting;
 h.at(2000);const recovery=Promise.all([a.init(),b.init()]);assert.equal(h.fetches,2);
 second.resolve(ok());await recovery;assert.equal(h.maps,2);
});

const receipt={schema:'ubts.gps_init_recovery.generated.v1',PASS:rows.every(r=>r.PASS),tests:rows.length,
 source_SHA256:createHash('sha256').update(original).digest('hex'),rows,
 actual_browser_network_API_or_hardware:0};
if(value('--receipt'))writeFileSync(value('--receipt'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify(receipt));
process.exitCode=receipt.PASS?0:1;
