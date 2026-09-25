// Browser input state machine with a fake transport: never connects to hardware.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
const code = source.slice(source.indexOf('function roverKeyFromEvent('), source.indexOf('function setRoverCommandStatus('));
function setup() {
  const c = {activeKeys: new Set(), activeButton: '', speedPct: 30, lease: 'lease', ticket: 'one', seq: 0, epoch: 0};
  const calls = [];
  const context = {state: {roverControl: c}, document: {hidden: false}, currentRoute: () => '/rover-control',
    $: () => null, setInterval: () => 1, clearInterval: () => {}, renderRoverButtons() {}, renderRoverMotionReadout() {}, setRoverCommandStatus() {},
    postJson: async (path, payload) => { calls.push({path, payload}); return {ok: true, ticket: 'next', seq: (payload?.seq || 0), lease: 'lease'}; }};
  vm.createContext(context); vm.runInContext(code, context);
  return {context, c, calls};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  {
    const {context, calls, c} = setup();
    context.handleRoverKeyDown({code: 'KeyW', key: '\u3148', target: {}, preventDefault() {}});
    await flush();
    assert.equal(calls[0].payload.left, 30); assert.equal(calls[0].payload.right, 30);
    context.handleRoverKeyUp({code: 'KeyW', key: '\u3148'}); await flush();
    assert.equal(calls[1].path, '/api/rover/release'); assert.equal(c.activeKeys.size, 0);
  }
  {
    const {context, calls} = setup();
    context.handleRoverKeyDown({code: 'KeyW', key: 'w', target: {tagName: 'INPUT'}, preventDefault() {}});
    context.handleRoverKeyDown({code: 'KeyW', key: 'w', repeat: true, target: {}, preventDefault() {}});
    assert.equal(calls.length, 0);
  }
  {
    const {context, c, calls} = setup();
    let resolveDrive;
    const normal = context.postJson;
    context.postJson = (path, payload) => path.endsWith('/command') ? new Promise(resolve => {resolveDrive = resolve;}) : normal(path, payload);
    c.activeKeys.add('w'); const pending = context.stepRoverMotion();
    await context.stopRoverNow('test');
    resolveDrive({ok: true, lease: 'late', ticket: 'late', seq: 1}); await pending;
    assert.equal(c.lease, null); assert.equal(c.ticket, null); assert.equal(calls[0].path, '/api/rover/stop');
  }
  {
    const {context, c, calls} = setup();
    c.lease = null;
    let resolveArm;
    const normal = context.postJson;
    context.postJson = (path, payload) => path.endsWith('/arm') ? new Promise(resolve => {resolveArm = resolve;}) : normal(path, payload);
    const arm = context.armEspRover(); await context.stopRoverNow('test');
    resolveArm({ok: true, lease: 'late', ticket: 'late', seq: 0}); await arm;
    assert.equal(c.lease, null); assert.equal(calls.length, 2); assert.equal(calls[1].payload.reason, 'cancelled_arm');
  }
  {
    const {context, c, calls} = setup();
    context.document.hidden = true; await context.stepRoverMotion(); await flush();
    assert.equal(c.lease, null); assert.equal(calls[0].path, '/api/rover/stop');
  }
  console.log('PASS: Korean WASD, input focus, release, delayed drive/arm, hidden-page stop');
})().catch(error => { console.error(error); process.exitCode = 1; });
