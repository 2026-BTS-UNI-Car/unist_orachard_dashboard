// Execute the actual original/derived function with a small DOM tree model.
// No browser, image, layout timing, network or product process is involved.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
const began=performance.now(),delta=JSON.parse(fs.readFileSync(new URL('../docs/READOUT_DOM_REUSE_SOURCE.json',import.meta.url)));
const source=fs.readFileSync(new URL('../static/app.js',import.meta.url),'utf8');
assert.equal(crypto.createHash('sha256').update(source).digest('hex'),delta.derived_SHA256);
assert.equal(crypto.createHash('sha256').update(source.replace(delta.after.replace(/\n/g,'\r\n'),delta.before.replace(/\n/g,'\r\n'))).digest('hex'),delta.original_SHA256);
const entities={'amp':'&','lt':'<','gt':'>','quot':'"','#039':"'"};
const unescape=s=>s.replace(/&(amp|lt|gt|quot|#039);/g,(_,k)=>entities[k]);
function realm(code){
 let allocated=0;
 class Node{constructor(type){this.nodeType=type;this.parentNode=null;this.childNodes=[];allocated++;}
  get firstChild(){return this.childNodes[0]??null;}
  append(...nodes){for(const n of nodes){if(n.parentNode)n.remove();n.parentNode=this;this.childNodes.push(n);}}
  remove(){if(this.parentNode){const p=this.parentNode;p.childNodes.splice(p.childNodes.indexOf(this),1);this.parentNode=null;}}
  get textContent(){return this.nodeType===3?this.data:this.childNodes.map(n=>n.textContent).join('');}
 }
 class Text extends Node{constructor(data){super(3);this.data=String(data);}}
 class Style{
  constructor(){this.values={};}
  get cssText(){return Object.entries(this.values).map(([k,v])=>k+': '+v+';').join(' ');}
  set cssText(v){assert.equal(v,'');this.values={};}
  valid(v){return typeof v==='string'&&(/^(#[0-9a-f]{3,8}|red|blue|rgb\([^)]*\)|var\([^)]*\))$/i.test(v));}
  get background(){return this.values.background??'';}set background(v){if(this.valid(v))this.values.background=v;}
  get color(){return this.values.color??'';}set color(v){if(this.valid(v))this.values.color=v;}
 }
 class Element extends Node{constructor(tag){super(1);this.tagName=tag.toUpperCase();this.className='';this.dataset={};this.style=new Style();}
  get children(){return this.childNodes.filter(n=>n.nodeType===1);}
  set innerHTML(html){for(const n of this.childNodes)n.parentNode=null;this.childNodes=[];if(!html)return;
   // Exact flat escaped readout template. Escaped input is text, never markup.
   const pattern=/([^<]*)<([a-z]+)(?: class="([^"]+)")?>([^<]*)<\/\2>/gi;let match,at=0;
   while((match=pattern.exec(html))){assert.equal(match.index,at);if(match[1])this.append(new Text(match[1]));
    const child=new Element(match[2]);child.className=match[3]??'';if(match[4])child.append(new Text(unescape(match[4])));this.append(child);at=pattern.lastIndex;}
   const tail=html.slice(at);assert.ok(!tail.includes('<'));if(tail)this.append(new Text(tail));
  }
  querySelector(selector){if(selector==='.swatch')return this.children.find(n=>n.className==='swatch')??null;throw Error('unexpected_selector');}
 }
 const targets=new Map();const document={createElement:tag=>new Element(tag),createTextNode:s=>new Text(s)};
 const context={document,$:id=>targets.get(id)??null,escapeHtml:v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]))};
 vm.createContext(context);vm.runInContext(code,context);
 const target=id=>{if(!targets.has(id))targets.set(id,new Element('div'));return targets.get(id);};
 const snapshot=id=>target(id).children.map(root=>({tag:root.tagName,cls:root.className,
  children:root.children.map(n=>({tag:n.tagName,cls:n.className,text:n.textContent,background:n.style.background,color:n.style.color}))}));
 const live=n=>1+n.childNodes.reduce((sum,c)=>sum+live(c),0);
 return{render:context.renderReadout,target,snapshot,allocated:()=>allocated,live:id=>live(target(id)),
  replace:id=>{const n=new Element('div');targets.set(id,n);return n;},document};
}
const old=realm(delta.before),next=realm(delta.after),cases=[];
const colors=['#20c4ef','#56d399','#f4be4f','#719ef5'];
function imu(i){return [
 {label:'Roll / Pitch',value:(i/100).toFixed(1)+' / '+(-i/120).toFixed(1),sub:'Gazebo FLU /imu/data_raw',color:colors[0]},
 {label:'Acceleration',value:(9.81+i/10000).toFixed(3)+' m/s2',sub:'x '+(i/100).toFixed(2)+' y 0.00 z 9.81',color:colors[1]},
 {label:'Angular velocity',value:(i/10000).toFixed(4)+' rad/s',sub:'x 0.000 y 0.000 z '+(i/1000).toFixed(3),color:colors[2]},
 {label:'Magnetic field',value:(20+i/100).toFixed(2)+' uT',sub:'sensor-native',color:colors[3]}];}
function lidar(i){return[
 {label:'Socket',value:'ON',sub:'connected',color:colors[1]},
 {label:'Source',value:'live',sub:'/scan',color:colors[0]},
 {label:'Points',value:String(3190+i%10),sub:(i%4/10).toFixed(1)+'s',color:colors[3]}];}
function both(id,rows){old.target(id);next.target(id);old.render(id,rows);next.render(id,rows);assert.deepEqual(next.snapshot(id),old.snapshot(id));}
both('roverImuStats',imu(0));both('roverLidarStats',lidar(0));
const roots=next.target('roverImuStats').children.slice(),texts=roots.map(n=>n.children.slice(1).map(e=>e.firstChild));
const allocated0=next.allocated(),oldAllocated0=old.allocated(),live0=next.live('roverImuStats');
for(let i=1;i<=600;i++){
 both('roverImuStats',imu(i));if(i%2===0)both('roverLidarStats',lidar(i));
 assert.equal(next.live('roverImuStats'),live0);
 for(let r=0;r<4;r++){assert.equal(next.target('roverImuStats').children[r],roots[r]);
  for(let t=0;t<3;t++)assert.equal(roots[r].children[t+1].firstChild,texts[r][t]);}
}
assert.equal(next.allocated(),allocated0,'600_sensor_ticks_allocate_zero_new_DOM_nodes_after_warmup');
cases.push('600_IMU_ticks_20Hz_equivalent_and300_LiDAR_ticks_display_equal_all_existing_nodes_reused');
const baselineAlloc=old.allocated()-oldAllocated0;
for(const rows of [[...imu(1),{label:'extra',value:3,sub:'status',color:'#aabbcc'}],imu(2).slice(0,2),[],imu(3).reverse(),
 [{label:'<img src=x onerror=1>',value:'& <b>unsafe</b>',sub:'"\'&',color:'red'},{label:0,value:false,sub:0,color:undefined}],
 [{label:null,value:undefined,sub:null,color:'not a color'}],imu(4)])both('roverImuStats',rows);
cases.push('grow_shrink_empty_order_labels_safe_markup_null_false_and_invalid_color_equivalent');
let target=next.target('roverImuStats'),prior=target.children[0];target.innerHTML='<span>external replacement</span>';
next.render('roverImuStats',imu(4));assert.notEqual(target.children[0],prior);assert.deepEqual(next.snapshot('roverImuStats'),old.snapshot('roverImuStats'));
cases.push('external_target_subtree_replacement_invalidates_same_render_key_cache');
prior=target.children[0];prior.children[1].innerHTML='<b>external child</b>';
next.render('roverImuStats',imu(4));assert.notEqual(target.children[0],prior);assert.deepEqual(next.snapshot('roverImuStats'),old.snapshot('roverImuStats'));
cases.push('external_child_replacement_invalidates_cached_Text_reference');
prior=target.children[0];prior.children[1].firstChild.data='external text';prior.children[0].style.background='red';
next.render('roverImuStats',imu(4));assert.equal(target.children[0],prior);assert.deepEqual(next.snapshot('roverImuStats'),old.snapshot('roverImuStats'));
cases.push('external_text_and_swatch_style_restored_without_row_allocation');
target=next.replace('roverImuStats');next.render('roverImuStats',imu(4));assert.deepEqual(next.snapshot('roverImuStats'),old.snapshot('roverImuStats'));
cases.push('replacement_target_identity_gets_fresh_WeakMap_cache');
next.render('missingTarget',imu(4));cases.push('missing_target_remains_noop');
const report={schema:'ubts.readout.DOM_reuse.pure.v1',ok:true,source_SHA256:delta.derived_SHA256,
 cases,cases_count:cases.length,IMU_updates:600,LiDAR_updates:300,
 live_IMU_nodes_after_warmup:live0,new_DOM_nodes_during900_updates:0,original_DOM_nodes_allocated_during_same_updates:baselineAlloc,
 semantics_compared:['tag_order','class_icon','literal_text_safe_escape','background_color','row_count','target_independence'],
 actual_browser_layout_FPS_or_network:0,source_inverse_exact:true,wall_s:(performance.now()-began)/1000};
console.log(JSON.stringify(report));
