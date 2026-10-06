// Contract: display never issues motor requests or interprets passed checks as authority.
const assert=require('node:assert/strict');
const {render}=require('../static/field-preparation.js');
const document={createDocumentFragment(){return {children:[],appendChild(n){this.children.push(n);}};},createElement(){return {};} };
const summary={},list={replaceChildren(fragment){this.children=fragment.children;this.replacements=(this.replacements||0)+1;}};
const node={open:true,dataset:{},ownerDocument:document,querySelector(s){return s==='summary'?summary:list;}};
const payload={schema:'ubts.field_preflight.v1',checks:[{id:'october_source_review_complete',passed:true},{id:'braking',passed:false}]};
render(node,payload);
assert.match(summary.textContent,/확인 1\/2/);
assert.match(summary.textContent,/자동 구동 보류/);
assert.match(list.children[1].textContent,/미완료.*정지 거리/);
render(node,payload);
assert.equal(list.replacements,1,'poll does not rebuild unchanged rows');
assert.equal(node.open,true,'expanded panel is preserved');
render(node,{schema:'ubts.field_preflight.v1',checks:[{id:'<script>motor()</script>',passed:'true'}],runtime_authority:true});
assert.equal(list.children[0].textContent,'미완료 · <script>motor()</script>');
assert.match(summary.textContent,/자동 구동 보류/);
render(node,{});assert.match(summary.textContent,/수신 대기/);assert.equal(list.children.length,0);
console.log('field preparation UI contract: passed');
