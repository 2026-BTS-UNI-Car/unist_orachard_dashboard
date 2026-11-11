// Browser input state machine with a fake transport: never connects to hardware.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource();
const code = source.slice(source.indexOf('function setRoverSpeed('), source.indexOf('function setRecordingKind('))
  + source.slice(source.indexOf('function roverKeyFromEvent('), source.indexOf('function setRoverCommandStatus('));
function setup() {
  const c = {activeKeys: new Set(), speedKeys: new Set(), activeButton: '', speedPct: 30, lease: 'lease', ticket: 'one', seq: 0, epoch: 0};
  const calls = [];
  const slider = {id: 'roverSpeedSlider', type: 'range', tagName: 'INPUT', value: '30'};
  const context = {state: {roverControl: c}, document: {hidden: false, querySelector: () => null}, currentRoute: () => '/rover-control',
    $: id => id === 'roverSpeedSlider' ? slider : null, text() {}, localStorage: {setItem() {}}, ROVER_SPEED_KEY: 'speed',
    setInterval: () => 1, clearInterval: () => {}, renderRoverButtons() {}, renderRoverMotionReadout() {}, setRoverCommandStatus() {}, resumeLiveCameraAfterArchive() {},
    postJson: async (path, payload) => { calls.push({path, payload}); return {ok: true, ticket: 'next', seq: (payload?.seq || 0), lease: 'lease'}; }};
  vm.createContext(context); vm.runInContext(code, context);
  return {context, c, calls, slider};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const key = (letter, extra = {}) => ({code: `Key${letter.toUpperCase()}`, key: letter, target: {}, preventDefault() {}, ...extra});
const wheels = call => [call.payload.left, call.payload.right];
(async () => {
  {
    for (const [keys, expected] of [['w', [30,30]], ['a', [-30,30]], ['s', [-30,-30]], ['d', [30,-30]],
      ['wa', [30,45]], ['wd', [45,30]], ['sa', [-30,-45]], ['sd', [-45,-30]], ['ws', [0,0]], ['ad', [0,0]]]) {
      const {context, c} = setup();
      c.activeKeys = new Set(keys);
      const motion = context.roverMotionVector();
      assert.deepEqual([motion.left, motion.right], expected, keys);
    }
    const {context, c} = setup(); c.activeKeys = new Set('wa');
    c.speedPct = 80; assert.equal(context.roverMotionVector().right, 100);
    c.speedPct = 100; assert.equal(context.roverMotionVector().left, 100);
    c.speedPct = 0; assert.equal(context.roverMotionVector().right, 0);
  }
  {
    const {context, calls, c, slider} = setup();
    context.setRoverDrivePanelCollapsed(true, false);
    context.handleRoverKeyDown(key('w')); await flush();
    context.handleRoverKeyDown(key('a')); await flush();
    context.handleRoverKeyUp(key('a')); await flush();
    assert.deepEqual(calls.map(wheels), [[30,30], [30,45], [30,30]]);
    assert.ok(calls.every(call => call.path.endsWith('/command')));
    context.document.activeElement = slider;
    assert.equal(context.roverKeyTargetBlocked({target: slider}), false);
    context.setRoverSpeed(40, true); await flush();
    assert.deepEqual(wheels(calls.at(-1)), [40,40]);
    context.handleRoverKeyDown(key('e', {target: slider, key: '\u3137'})); await flush();
    assert.equal(c.speedPct, 45); assert.equal(slider.value, '45');
    context.handleRoverKeyDown(key('e', {repeat: true}));
    context.handleRoverKeyDown(key('e')); await flush();
    assert.equal(c.speedPct, 45);
    context.handleRoverKeyUp(key('e')); context.handleRoverKeyDown(key('e')); await flush();
    assert.equal(c.speedPct, 50); assert.deepEqual(wheels(calls.at(-1)), [50,50]);
    context.handleRoverKeyDown(key('q')); await flush(); assert.equal(c.speedPct, 45);
    context.handleRoverKeyUp(key('q')); context.setRoverSpeed(2, false); await flush();
    context.handleRoverKeyDown(key('q')); await flush(); assert.equal(c.speedPct, 0);
    context.handleRoverKeyUp(key('e')); context.setRoverSpeed(98, false); await flush();
    context.handleRoverKeyDown(key('e')); await flush(); assert.equal(c.speedPct, 100);
    assert.deepEqual([...c.activeKeys], ['w']);
  }
  {
    const {context, calls, c} = setup();
    let resolveDrive;
    const normal = context.postJson;
    context.postJson = (path, payload) => {
      if (!resolveDrive) { calls.push({path, payload}); return new Promise(resolve => { resolveDrive = resolve; }); }
      return normal(path, payload);
    };
    context.handleRoverKeyDown(key('w'));
    context.handleRoverKeyDown(key('a'));
    context.setRoverSpeed(40, true); context.setRoverSpeed(50, true);
    assert.equal(calls.length, 1);
    resolveDrive({ok: true, ticket: 'next', seq: 1}); await flush();
    assert.deepEqual(calls.map(wheels), [[30,30], [50,75]]);
    assert.equal(calls[1].payload.seq, 2);
    assert.equal(c.activeKeys.size, 2);
  }
  {
    const {context, c, calls} = setup(); c.lease = null;
    context.handleRoverKeyDown(key('w')); context.handleRoverKeyDown(key('e'));
    assert.equal(calls.length, 0); assert.equal(c.speedPct, 30);
    await context.armEspRover(); assert.equal(calls[0].path, '/api/rover/arm');
    await context.armEspRover(); assert.equal(calls[1].path, '/api/rover/stop'); assert.equal(c.lease, null);
  }
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
  console.log('PASS: password-free toggle, WASD chords/transitions, live slider, Q/E 5-point steps/repeats/bounds, latest-only input, Korean keys, focus, delayed drive/arm and stop');
})().catch(error => { console.error(error); process.exitCode = 1; });
