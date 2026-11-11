// Direction pad pointer handling with fake buttons and a fake transport: never
// connects to hardware. Mouse behaviour stays as it was; a finger keeps the
// button (pointer capture) and every way of losing the pointer ends the input
// through the same release as lifting the finger.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8').replace(/\r\n/g, '\n');
const css = fs.readFileSync(path.join(__dirname, '../static/styles.css'), 'utf8').replace(/\r\n/g, '\n');
const between = (begin, end) => { const a = source.indexOf(begin), b = source.indexOf(end, a); assert.ok(a >= 0 && b > a, begin); return source.slice(a, b); };
// The real pad wiring and the real drive state machine (same cut as test_control_path_regression.js).
const code = between('function initRoverPad() {', 'function setRoverSpeed(')
  + between('function startRoverButtonCommand(', 'function roverCommandMessage(');

function setup({lease = 'lease', captureThrows = false} = {}) {
  const c = {activeKeys: new Set(), speedKeys: new Set(), activeButton: '', speedPct: 30, lease, ticket: 'one', seq: 0, epoch: 0};
  const calls = [], status = [];
  const buttons = ['forward', 'left', 'stop', 'right', 'backward'].map(action => ({
    dataset: {roverDrive: action}, handlers: {}, captured: [],
    addEventListener(type, handler) { this.handlers[type] = handler; },
    setPointerCapture(id) { if (captureThrows) throw new Error('InvalidPointerId'); this.captured.push(id); },
  }));
  const context = {
    state: {roverControl: c}, SIMULATION: false, currentRoute: () => '/rover-control',
    document: {hidden: false, querySelectorAll: selector => { assert.equal(selector, '[data-rover-drive]'); return buttons; }},
    setInterval: () => 1, clearInterval: () => {},
    renderRoverButtons() {}, renderRoverMotionReadout() {}, setRoverCommandStatus: (kind, message) => status.push([kind, message]),
    postJson: async (p, payload) => { calls.push({op: p.split('/').pop(), payload}); return {ok: true, ticket: 'next', seq: payload?.seq || 0}; },
  };
  vm.createContext(context); vm.runInContext(code, context);
  context.initRoverPad();
  const pad = Object.fromEntries(buttons.map(button => [button.dataset.roverDrive, button]));
  const fire = (action, type, extra = {}) => {
    const event = {pointerId: 7, pointerType: 'touch', prevented: false, preventDefault() { this.prevented = true; }, ...extra};
    pad[action].handlers[type](event);
    return event;
  };
  return {context, c, calls, status, pad, fire};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const ops = calls => calls.map(call => call.op);

(async () => {
  // Every pad button has the same six listeners.
  {
    const {pad} = setup();
    for (const button of Object.values(pad)) {
      assert.deepEqual(Object.keys(button.handlers).sort(), ['contextmenu', 'lostpointercapture', 'pointercancel', 'pointerdown', 'pointerleave', 'pointerup']);
    }
  }
  // Touch press: the button captures the pointer, then the drive command goes out as before.
  {
    const {c, calls, pad, fire} = setup();
    const down = fire('forward', 'pointerdown');
    assert.equal(down.prevented, true);
    assert.deepEqual(pad.forward.captured, [7], 'the finger stays with the button');
    assert.equal(c.activeButton, 'forward');
    await flush();
    assert.deepEqual(ops(calls), ['command']);
    assert.deepEqual([calls[0].payload.left, calls[0].payload.right, calls[0].payload.deadman], [30, 30, true]);
    // Lifting the finger is the release that existed before.
    fire('forward', 'pointerup'); await flush();
    assert.deepEqual(ops(calls), ['command', 'release']);
    assert.equal(calls[1].payload.deadman, false);
    assert.deepEqual([calls[1].payload.left, calls[1].payload.right], [0, 0]);
    assert.equal(c.activeButton, '');
    // The browser reports the lost capture and the leave after the lift: nothing more is sent.
    fire('forward', 'lostpointercapture'); fire('forward', 'pointerleave'); await flush();
    assert.deepEqual(ops(calls), ['command', 'release']);
  }
  // A cancelled touch and a lost capture end the input exactly like a lift.
  for (const [type, followUps] of [['pointercancel', ['lostpointercapture', 'pointerleave']], ['lostpointercapture', ['pointerleave']]]) {
    const {c, calls, fire} = setup();
    fire('left', 'pointerdown'); await flush();
    assert.deepEqual([calls[0].payload.left, calls[0].payload.right], [-30, 30]);
    fire('left', type); await flush();
    assert.deepEqual(ops(calls), ['command', 'release'], type);
    assert.deepEqual([calls[1].payload.left, calls[1].payload.right, calls[1].payload.deadman], [0, 0, false], type);
    assert.equal(c.activeButton, '', type); assert.equal(c.lease, 'lease', `${type} releases the input, it does not disarm`);
    for (const next of followUps) fire('left', next);
    await flush();
    assert.deepEqual(ops(calls), ['command', 'release'], `${type}: one release only`);
  }
  // A lost capture without a held pad button does nothing (keyboard driving is not touched).
  {
    const {c, calls, fire} = setup();
    c.activeKeys.add('w');
    fire('forward', 'lostpointercapture'); await flush();
    assert.equal(calls.length, 0); assert.deepEqual([...c.activeKeys], ['w']);
  }
  // Mouse: no capture, and leaving the button while it is held still releases.
  {
    const {c, calls, pad, fire} = setup();
    fire('right', 'pointerdown', {pointerType: 'mouse', pointerId: 1}); await flush();
    assert.deepEqual(pad.right.captured, [], 'mouse behaviour is unchanged');
    assert.deepEqual([calls[0].payload.left, calls[0].payload.right], [30, -30]);
    fire('right', 'pointerleave', {pointerType: 'mouse', pointerId: 1}); await flush();
    assert.deepEqual(ops(calls), ['command', 'release']); assert.equal(c.activeButton, '');
    const menu = fire('right', 'contextmenu', {pointerType: 'mouse'});
    assert.equal(menu.prevented, false, 'the mouse context menu is left alone');
  }
  // Long press on a touch screen: no menu over the held button.
  {
    const {fire} = setup();
    fire('backward', 'pointerdown');
    assert.equal(fire('backward', 'contextmenu').prevented, true);
  }
  // Pad STOP by touch is still the immediate stop.
  {
    const {c, calls, pad, fire} = setup();
    fire('forward', 'pointerdown'); await flush();
    fire('stop', 'pointerdown', {pointerId: 8});
    assert.equal(c.lease, null, 'lease cleared synchronously');
    assert.equal(calls.at(-1).op, 'stop'); assert.equal(calls.at(-1).payload.reason, 'pad_stop');
    assert.deepEqual(pad.stop.captured, [8]);
    await flush();
    fire('stop', 'pointerup', {pointerId: 8}); fire('forward', 'pointerup'); await flush();
    assert.deepEqual(ops(calls), ['command', 'stop'], 'nothing is sent after the stop');
  }
  // Capture can fail (pointer already gone): the press still works and its cancel still releases.
  {
    const {calls, fire} = setup({captureThrows: true});
    fire('forward', 'pointerdown'); await flush();
    fire('forward', 'pointercancel'); await flush();
    assert.deepEqual(ops(calls), ['command', 'release']);
  }
  // Without the drive lease a press sends nothing, as before.
  {
    const {c, calls, status, fire} = setup({lease: null});
    fire('forward', 'pointerdown'); fire('forward', 'pointerup'); await flush();
    assert.equal(calls.length, 0); assert.equal(c.activeButton, '');
    assert.match(status[0][1], /먼저 조종을 활성화/);
  }
  // Style: a held pad button is not a scroll, a selection or a long-press menu.
  {
    const rule = css.match(/\.rover-pad button \{[^}]*\}/);
    assert.ok(rule);
    for (const declaration of ['touch-action: none;', 'user-select: none;', '-webkit-user-select: none;', '-webkit-touch-callout: none;']) {
      assert.ok(rule[0].includes(declaration), declaration);
    }
  }
  // initRoverControl still wires the pad once, next to the unchanged key listeners.
  assert.match(between('function initRoverControl() {', 'function initRoverPad() {'), /\n  initRoverPad\(\);\n  document\.addEventListener\("keydown", handleRoverKeyDown\);\n  document\.addEventListener\("keyup", handleRoverKeyUp\);\n/);
  console.log('PASS: pad touch capture, release on lift/cancel/lost capture/leave (one release each), mouse unchanged, pad STOP, no-lease press, pad style');
})().catch(error => { console.error(error); process.exitCode = 1; });
