// The document list (/api/docs) is asked for when the Docs page is first shown, not on the first load of every page
// (2026-11-17, one request less on a slow link): static/app/04-telemetry.js ensureDocs(), 02-shell.js renderRoute(),
// 99-main.js. No browser. Run: node tests/test_docs_on_demand.js
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource().replace(/\r\n/g, '\n');
const between = (begin, end) => { const a = source.indexOf(begin), b = source.indexOf(end, a); assert.ok(a >= 0 && b > a, begin); return source.slice(a, b); };

// The start of the page does not ask for the list; the route switch asks for it on the Docs page only.
const main = fs.readFileSync(path.join(__dirname, '..', 'static', 'app', '99-main.js'), 'utf8');
assert.doesNotMatch(main, /loadDocs\(|ensureDocs\(/, 'no document list request at start');
const route = between('function renderRoute() {', 'function initMobileNav(');
assert.match(route, /if \(route === "\/docs"\) \{\n    ensureDocs\(\);\n    renderDocsRoute\(\);\n  \}/, 'Docs page: ask for the list, then show it');
assert.equal((route.match(/ensureDocs\(/g) || []).length, 1, 'only the Docs page asks');

(async () => {
  const requests = [];
  let fail = false;
  const rendered = [];
  const context = {
    state: { docs: [], docsRequested: false },
    getJson: async (url) => { requests.push(url); if (fail) throw new Error('offline'); return { documents: [{ slug: 'a', title: 'A' }] }; },
    renderDocsIndex: (docs) => rendered.push(['index', docs.length]),
    renderDocsRoute: () => rendered.push(['route']),
    console: { error: () => {} },
  };
  vm.createContext(context);
  vm.runInContext(between('async function loadDocs() {', 'async function tick() {'), context);
  const settle = () => new Promise((done) => setTimeout(done, 0));

  // Two visits of the Docs page: one request, the list is kept.
  context.ensureDocs();
  context.ensureDocs();
  await settle();
  assert.deepEqual(requests, ['/api/docs']);
  assert.equal(context.state.docs.length, 1);
  assert.deepEqual(rendered, [['index', 1], ['route']]);

  // A failed request is asked again on the next visit (not stuck).
  context.state.docsRequested = false; context.state.docs = []; requests.length = 0; fail = true;
  context.ensureDocs();
  await settle();
  assert.deepEqual(requests, ['/api/docs']);
  assert.equal(context.state.docsRequested, false, 'failure: ask again next time');
  fail = false;
  context.ensureDocs();
  await settle();
  assert.deepEqual(requests, ['/api/docs', '/api/docs']);
  assert.equal(context.state.docs.length, 1);
  console.log('PASS: the document list is asked for on the Docs page only, once, and again after a failure');
})().catch((error) => { console.error(error); process.exit(1); });
