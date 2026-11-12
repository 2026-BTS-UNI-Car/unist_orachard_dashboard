// NET-08: the camera socket closes when its picture box has been off screen for 1 s and opens
// again when the box is back; automatic mode does not ask for the 1080p original preview.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource();

const panelCode = source.slice(source.indexOf('// Hidden camera panel (NET-08)'), source.indexOf('function toggleLidarLive('));
assert.ok(panelCode.includes('function watchCameraPanel('), 'panel code found');

function fixture() {
  const timers = new Map(); let next = 0; const calls = [];
  const observed = [];
  class FakeObserver {
    constructor(callback) { this.callback = callback; FakeObserver.last = this; }
    observe(element) { observed.push(element); }
    unobserve(element) { observed.splice(observed.indexOf(element), 1); }
  }
  const box = { id: 'box' };
  const context = {
    IntersectionObserver: FakeObserver,
    document: { hidden: false },
    state: { cameraLive: { socket: null, enabled: false, userStopped: false } },
    $: id => (id === 'roverCameraImage' ? { parentElement: box } : null),
    currentRoute: () => '/rover-control',
    routeSupportsCameraLive: () => true,
    startCameraLive: () => { calls.push('start'); context.state.cameraLive.socket = {}; context.state.cameraLive.enabled = true; },
    closeCameraLive: (message, options) => { calls.push(['close', options.userStopped]); context.state.cameraLive.socket = null; context.state.cameraLive.enabled = false; context.state.cameraLive.userStopped = options.userStopped; },
    window: { setTimeout: fn => { const id = ++next; timers.set(id, fn); return id; }, clearTimeout: id => timers.delete(id) },
  };
  vm.createContext(context);
  vm.runInContext(panelCode + '\nthis.cameraPanel = cameraPanel;', context);
  const report = visible => FakeObserver.last.callback([{ target: box, isIntersecting: visible }]);
  const fire = () => { const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()); };
  return { context, calls, observed, report, fire, timers, box };
}

{
  const f = fixture();
  f.context.watchCameraPanel();
  assert.deepEqual(f.observed, [f.box], 'the picture box is watched');
  f.context.watchCameraPanel();
  assert.equal(f.observed.length, 1, 'watching again does not add a second watch');
  f.context.state.cameraLive.socket = {}; f.context.state.cameraLive.enabled = true;
  f.report(false);
  assert.deepEqual(f.calls, [], 'nothing closes before 1 s');
  f.report(true); f.fire();
  assert.deepEqual(f.calls, [], 'a short scroll away does not close the camera');
  f.report(false); f.fire();
  assert.deepEqual(f.calls, [['close', false]], 'off screen for 1 s closes the camera, not as a user stop');
  assert.equal(f.context.cameraPanel.closed, true);
  f.report(true);
  assert.deepEqual(f.calls, [['close', false], 'start'], 'back on screen opens it again');
  assert.equal(f.context.cameraPanel.closed, false);
}
{
  const f = fixture();
  f.context.watchCameraPanel();
  f.context.state.cameraLive.socket = {}; f.context.state.cameraLive.enabled = true;
  f.context.state.cameraLive.userStopped = true;
  f.report(false); f.fire();
  assert.deepEqual(f.calls, [], 'a camera the user stopped is left alone');
  f.report(true);
  assert.deepEqual(f.calls, [], 'and is not started when the box comes back');
}
{
  const f = fixture();
  f.context.watchCameraPanel();
  f.context.state.cameraLive.socket = {}; f.context.state.cameraLive.enabled = true;
  f.report(false); f.fire();
  f.context.document.hidden = true;
  f.report(true);
  assert.deepEqual(f.calls, [['close', false]], 'a hidden tab does not reopen the camera (the hidden-tab rule does)');
}

// Preview mode: a chosen quality keeps the page's settings ("detail" asks for the original where
// allowed); automatic mode follows the link grade's plan (NET-08, NET-10).
const urlCode = source.slice(source.indexOf('function cameraWebSocketUrl('), source.indexOf('function startCameraLive('));
function urlFor(profile, search = '', allowed = true, graded = 'balanced') {
  const context = { URLSearchParams, location: { search, protocol: 'http:', host: '127.0.0.1' }, liveNetworkProfile: () => profile,
    effectiveLinkProfile: () => graded,
    window: { UBTS_CAMERA_ORIGINAL_PREVIEW: allowed, UBTS_CAMERA_ACK_WINDOW: 3 }, state: { cameraLive: { resolution: '1280x720', fps: 60 } } };
  vm.createContext(context); vm.runInContext(urlCode, context);
  return new URL(context.cameraWebSocketUrl()).searchParams;
}
for (const profile of ['tiny', 'slow', 'balanced']) {
  const params = urlFor(profile);
  assert.equal(params.has('quality_mode'), false, `${profile}: no original preview`);
  assert.equal(params.get('resolution'), '1280x720', `${profile}: the chosen preview size`);
  assert.equal(params.get('profile'), profile);
  assert.equal(params.get('ack_window'), '3', `${profile}: the page's window`);
}
assert.equal(urlFor('detail').get('quality_mode'), 'original', 'detail asks for the original where allowed');
assert.equal(urlFor('detail', '', false).has('quality_mode'), false, 'not where the dashboard does not allow it');
assert.equal(urlFor('auto', '?camera_preview=original').get('quality_mode'), 'original', 'an explicit page option still wins');
assert.equal(urlFor('detail', '?camera_preview=legacy').has('quality_mode'), false, 'and the explicit legacy option too');
const plans = {
  detail: { resolution: '1920x1080', fps: '30', original: true, flow: 'decode-ack' },
  balanced: { resolution: '640x360', fps: '6', quality: '55', profile: 'balanced', flow: 'decode-ack-v2', window: '2' },
  slow: { resolution: '320x180', fps: '5', quality: '35', profile: 'balanced', flow: 'decode-ack-v2', window: '2' },
  tiny: { resolution: '320x180', fps: '2', profile: 'tiny', flow: 'decode-ack' },
};
for (const [graded, plan] of Object.entries(plans)) {
  const params = urlFor('auto', '', true, graded);
  assert.equal(params.get('resolution'), plan.resolution, `auto/${graded}: size`);
  assert.equal(params.get('fps'), plan.fps, `auto/${graded}: rate`);
  if (plan.quality) assert.equal(params.get('quality'), plan.quality, `auto/${graded}: quality`);
  if (plan.profile) assert.equal(params.get('profile'), plan.profile, `auto/${graded}: bridge profile`);
  assert.equal(params.get('quality_mode') === 'original', Boolean(plan.original), `auto/${graded}: original preview`);
  assert.equal(params.get('flow'), plan.flow, `auto/${graded}: flow`);
  if (plan.window) assert.equal(params.get('ack_window'), plan.window, `auto/${graded}: window`);
}
assert.equal(urlFor('auto', '', false, 'detail').has('quality_mode'), false, 'good link without permission: no original');
console.log('Camera panel off screen closes after 1 s and reopens; automatic mode uses no original preview: PASS');
