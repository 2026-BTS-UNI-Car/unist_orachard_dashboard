// LiDAR in the 자율주행 tab of Rover Control (2026-11-20, static/app/05-live-lidar-camera.js syncAutoLidar and
// autoLidarSentence, static/app/07-lidar-data.js renderLidarLiveState). User: "자율주행 시에 라이다 데이터라도 실시간으로
// 받아오게 하면 좋을 듯", in the place of the live occupancy grid map of that tab.
//   - showing 자율주행 starts the stream without a button: one the user did not stop follows its picture (the panel
//     rule, run here with a fake observer), one the user stopped is borrowed (started for the tab);
//   - leaving the tab or the page stops only a borrowed stream, as the user had left it; a stream the user had running
//     is never stopped by this rule (off screen it pauses by the old 1 s rule and comes back by itself);
//   - in 자율주행 the button is hidden and one sentence under the picture says the state, also when nothing connects
//     and when the picture is no longer fed (it then says that the picture is the last scan);
//   - the autonomy buttons do not depend on it (the block never touches the executor, the bridge or the recorder).
// Fake observer, sockets and elements only. Run: node tests/test_auto_lidar.js
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource();
const between = (begin, end) => { const a = source.indexOf(begin), b = source.indexOf(end, a); assert.ok(a >= 0 && b > a, begin); return source.slice(a, b); };

const panelCode = between('// Hidden LiDAR and IMU pictures (UI-16)', 'function toggleLidarLive(');
const autoCode = between('function toggleLidarLive() {', 'function startLidarLive() {');
const drawCode = between('function renderLidarLiveState(overrideLabel) {', 'function renderLiveSensorLinks() {');

// (the statements of the block, without its comment lines)
assert.doesNotMatch(autoCode.replace(/^\s*\/\/.*$/gm, ''), /executor|\/api\/|postJson|getJson|fetch|controlRequest|stopRoverNow|roverControl|recording/i,
  'the LiDAR of the tab never touches the executor, the bridge or the recorder');

// The page parts the block needs: the tab, the route, the LiDAR state with a start and a close that act like the real
// ones (a start waits for the picture: sensorPanelDefersStart of the panel rule), the two elements of the LiDAR part.
function page({ tab = 'manual', route = '/rover-control', userStopped = false } = {}) {
  const timers = new Map(); let next = 0; const calls = [], listeners = {}, observers = [];
  class FakeObserver {
    constructor(callback) { this.callback = callback; this.targets = []; observers.push(this); }
    observe(element) { this.targets.push(element); }
    unobserve(element) { this.targets.splice(this.targets.indexOf(element), 1); }
  }
  const box = { id: 'lidar-picture' };
  const button = { textContent: '', hidden: false, classList: { toggle() {} } };
  const note = { textContent: '', hidden: true };
  const context = {
    document: { hidden: false }, IntersectionObserver: FakeObserver, CAMERA_PANEL_HIDDEN_MS: 1000,
    state: { lidarScan: null, lidarLive: { socket: null, enabled: false, connected: false, scanTopicAlive: false, userStopped, reconnectAttempt: 2,
      pointCount: 0, source: 'waiting', error: '', lastMessageAt: null }, imuLive: { socket: null, enabled: false, userStopped: false }, telemetry: null },
    tab, route, Date: { now: () => context.clock }, clock: 1_800_000_000_000,
    setInterval: (fn, ms) => { assert.equal(ms, 1000); context.everySecond = fn; return 1; },
    $: (id) => ({ roverLidarCanvas: { parentElement: box }, roverLidarToggle: button, roverLidarNote: note }[id] || null),
    currentRoute: () => context.route, roverTab: () => context.tab,
    routeSupportsLidarLive: () => ['/connection', '/rover-control'].includes(context.route), routeSupportsImuLive: () => false,
    startImuLive() {}, closeImuLive() {},
    window: { setTimeout: (fn) => { const id = ++next; timers.set(id, fn); return id; }, clearTimeout: (id) => timers.delete(id),
      addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); } },
    chartColors: () => ({}), renderReadout() {}, lidarSourceLabel: () => '', lidarAgeLabel: () => '', renderLiveSensorLinks() {},
  };
  // The real start begins with the panel rule; the socket itself is a stand-in.
  context.startLidarLive = () => {
    const live = context.state.lidarLive;
    if (!context.routeSupportsLidarLive() || live.socket) return;
    if (context.sensorPanelDefersStart('lidar')) { calls.push('wait for the picture'); return; }
    Object.assign(live, { socket: {}, enabled: true, userStopped: false, connected: false });
    calls.push('open');
    context.renderLidarLiveState();
  };
  context.closeLidarLive = (message, options = {}) => {
    const live = context.state.lidarLive;
    Object.assign(live, { socket: null, enabled: false, connected: false, scanTopicAlive: false, userStopped: options.userStopped !== false });
    calls.push(live.userStopped ? 'stop as the user' : 'pause');
    context.renderLidarLiveState();
  };
  context.stopLidarLive = (message) => context.closeLidarLive(message, { userStopped: true });
  vm.createContext(context);
  vm.runInContext(`${panelCode}\n${autoCode}\n${drawCode}\nthis.sensorPanels = sensorPanels; this.autoLidar = autoLidar;`, context);
  const observer = () => observers.find((o) => o.targets.includes(box));
  return {
    context, calls, button, note, live: context.state.lidarLive,
    showTab(name) { context.tab = name; for (const fn of listeners['ubts:rover-tab'] || []) fn({ detail: { tab: name } }); },
    openPage(name) { context.route = name; for (const fn of listeners['ubts:route'] || []) fn({ detail: { route: name } }); },
    // What the browser reports for the one LiDAR picture: on screen in 수동 조종 and 자율주행, not in the other tabs.
    picture(visible) { observer().callback([{ target: box, isIntersecting: visible }]); },
    aSecondLater() { const pending = [...timers.values()]; timers.clear(); pending.forEach((fn) => fn()); },
    listeners,
  };
}

// The block listens to the tab and to the page.
{
  const p = page();
  assert.deepEqual(Object.keys(p.listeners).sort(), ['ubts:route', 'ubts:rover-tab']);
}

// A stream the user did not stop: opening 자율주행 starts it without a button, as soon as its picture is on screen.
{
  const p = page({ tab: 'setup' });
  p.context.watchSensorPanel('lidar');
  p.context.startLidarLive();                       // what renderRoute asks when Rover Control opens
  p.picture(false);                                 // 보정·설정 has no LiDAR picture
  assert.deepEqual(p.calls, ['wait for the picture']);
  p.showTab('auto');
  assert.equal(p.context.autoLidar.borrowed, false, 'nothing to borrow: the user never stopped it');
  assert.deepEqual([p.button.hidden, p.note.hidden], [true, false], '자율주행: no button, one sentence');
  assert.equal(p.note.textContent, '이 그림이 화면에 보이면 LiDAR를 받기 시작합니다.');
  p.picture(true);                                  // the tab shows the picture
  assert.deepEqual(p.calls, ['wait for the picture', 'open'], 'the start request, no press');
  assert.equal(p.note.textContent, 'LiDAR에 연결하고 있습니다.');
  // it goes on while the tab is shown, whatever the run does (this rule never looks at the run)
  p.showTab('auto'); p.openPage('/rover-control');
  assert.deepEqual(p.calls, ['wait for the picture', 'open']);
  // to 수동 조종: the same picture, the same stream; the button is back
  p.showTab('manual');
  assert.deepEqual(p.calls, ['wait for the picture', 'open'], 'nothing is closed on the way to 수동 조종');
  assert.deepEqual([p.button.hidden, p.note.hidden], [false, true]);
  // to 기록: off screen for 1 s pauses it (the old rule, not a user stop), back in 자율주행 it opens by itself
  p.showTab('record'); p.picture(false); p.aSecondLater();
  assert.deepEqual(p.calls.slice(2), ['pause']);
  assert.equal(p.live.userStopped, false);
  p.showTab('auto'); p.picture(true);
  assert.deepEqual(p.calls.slice(2), ['pause', 'open']);
}

// A stream the user stopped on 수동 조종: 자율주행 starts it for itself and stops it again when the tab is left.
{
  const p = page({ tab: 'manual', userStopped: true });
  p.context.watchSensorPanel('lidar'); p.picture(true);
  p.showTab('auto');
  assert.deepEqual(p.calls, ['open'], 'the tab starts it although the user had stopped it');
  assert.deepEqual([p.context.autoLidar.borrowed, p.live.userStopped, p.live.reconnectAttempt], [true, false, 0]);
  p.showTab('auto');
  assert.deepEqual(p.calls, ['open'], 'once');
  p.showTab('manual');
  assert.deepEqual(p.calls, ['open', 'stop as the user'], 'leaving the tab stops what the tab itself had started');
  assert.deepEqual([p.context.autoLidar.borrowed, p.live.userStopped, p.live.socket], [false, true, null]);
  assert.equal(p.button.textContent, 'LiDAR 시작', '수동 조종 is as the user left it');
  p.picture(false); p.aSecondLater(); p.picture(true);
  assert.deepEqual(p.calls, ['open', 'stop as the user'], 'and it stays stopped there');
  // the same when the page is left from 자율주행 (the tab stays chosen)
  p.showTab('auto');
  assert.deepEqual(p.calls.slice(2), ['open']);
  p.openPage('/system');
  assert.deepEqual(p.calls.slice(2), ['open', 'stop as the user']);
  assert.equal(p.live.userStopped, true);
  p.openPage('/rover-control');
  assert.deepEqual(p.calls.slice(4), ['open'], 'back on the page with 자율주행 chosen: started again');
  // hidden picture while borrowed (another tab of the browser, or scrolled away): the old pause, still borrowed
  p.picture(false); p.aSecondLater();
  assert.deepEqual(p.calls.slice(5), ['pause']);
  assert.equal(p.context.autoLidar.borrowed, true);
  p.picture(true);
  assert.deepEqual(p.calls.slice(5), ['pause', 'open']);
}

// A stream the user had running is never stopped by this rule, wherever the user goes from 자율주행.
for (const leave of [(p) => p.showTab('manual'), (p) => p.showTab('setup'), (p) => p.showTab('record'), (p) => p.openPage('/connection')]) {
  const p = page({ tab: 'manual' });
  p.context.watchSensorPanel('lidar'); p.picture(true);
  p.context.startLidarLive();
  p.showTab('auto');
  leave(p);
  assert.deepEqual(p.calls, ['open'], 'no stop from the tab rule');
  assert.equal(p.live.userStopped, false);
}

// The LiDAR button is the user's own choice: pressed after a borrowed start, the tab no longer undoes it.
{
  const p = page({ tab: 'manual', userStopped: true });
  p.context.watchSensorPanel('lidar'); p.picture(true);
  p.showTab('auto');
  assert.equal(p.context.autoLidar.borrowed, true);
  p.context.toggleLidarLive();                      // (the button of Connection, or of 수동 조종 in a second step)
  assert.equal(p.context.autoLidar.borrowed, false);
}

// The sentence under the picture in 자율주행: the state in plain words, also when nothing connects, and never "live"
// for a picture that is no longer fed.
{
  const p = page({ tab: 'auto' });
  const now = p.context.clock;
  const say = (live, scan = null) => {
    Object.assign(p.live, { socket: null, enabled: false, connected: false, scanTopicAlive: false, lastMessageAt: null }, live);
    p.context.state.lidarScan = scan;
    p.context.renderLidarLiveState();
    return p.note.textContent;
  };
  const scan = { ok: true, points: [{ angle_deg: 1, distance_m: 2 }] };
  const open = { socket: {}, enabled: true, connected: true };
  const seen = [];
  const check = (text, expected, why) => { assert.equal(text, expected, why); seen.push(text); };
  check(say({ socket: {}, enabled: true }), 'LiDAR에 연결하고 있습니다.');
  check(say(open), 'LiDAR에 연결했습니다. 스캔을 기다리고 있습니다.');
  check(say({ ...open, scanTopicAlive: true, lastMessageAt: now - 200 }, scan), 'LiDAR를 실시간으로 받고 있습니다.');
  check(say({ ...open, scanTopicAlive: true, lastMessageAt: now - 3001 }, scan), 'LiDAR 스캔이 오지 않습니다. 그림은 마지막으로 받은 스캔입니다.',
    'an open connection that went quiet for more than 3 s is not called live');
  check(say({ ...open, scanTopicAlive: false, lastMessageAt: now - 100 }, scan), 'LiDAR 스캔이 오지 않습니다. 그림은 마지막으로 받은 스캔입니다.',
    'the bridge answers but has no scans');
  check(say({ enabled: true }), 'LiDAR에 연결하지 못했습니다. 자율주행 조작은 그대로 할 수 있습니다.', 'no bridge or a refused connection');
  check(say({ enabled: true }, scan), 'LiDAR 연결이 끊겼습니다. 그림은 마지막으로 받은 스캔입니다.', 'a lost connection with an old picture on the canvas');
  check(say({}), '이 그림이 화면에 보이면 LiDAR를 받기 시작합니다.');
  for (const text of seen) {
    for (const sentence of text.split(/(?<=\.) /)) assert.match(sentence, /(니다|세요)\.$/, sentence);
    assert.doesNotMatch(text, /WebSocket|socket|ws|—|→|·/i, 'no developer words, no joining signs');
  }
  assert.deepEqual([p.button.hidden, p.note.hidden], [true, false]);
  // nothing arrives any more: no event redraws the sentence, the once-a-second look does
  say({ ...open, scanTopicAlive: true, lastMessageAt: now - 200 }, scan);
  assert.equal(p.note.textContent, 'LiDAR를 실시간으로 받고 있습니다.');
  p.context.clock = now + 2500; p.context.everySecond();
  assert.equal(p.note.textContent, 'LiDAR를 실시간으로 받고 있습니다.', 'a gap of under 3 s is not an alarm (3 scans a second on the slowest grade)');
  p.context.clock = now + 3500; p.context.everySecond();
  assert.equal(p.note.textContent, 'LiDAR 스캔이 오지 않습니다. 그림은 마지막으로 받은 스캔입니다.');
  p.live.lastMessageAt = p.context.clock; p.context.everySecond();
  assert.equal(p.note.textContent, 'LiDAR를 실시간으로 받고 있습니다.', 'and back when scans arrive again');
  // the look does nothing in the other tabs or in a hidden page
  p.showTab('manual');
  assert.deepEqual([p.button.hidden, p.note.hidden, p.note.textContent], [false, true, ''], '수동 조종 keeps its button and shows no sentence');
  p.note.textContent = 'untouched'; p.context.everySecond();
  assert.equal(p.note.textContent, 'untouched');
  p.showTab('auto'); p.context.document.hidden = true; p.note.textContent = 'untouched'; p.context.everySecond();
  assert.equal(p.note.textContent, 'untouched');
}

// The markup: the LiDAR part is in both tabs, the live occupancy grid map in 수동 조종 only; the local grid picture of
// the monitoring view stays.
{
  const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'index.html'), 'utf8');
  assert.match(html, /<article class="panel rover-live-panel rover-lidar-panel" data-rover-tab="manual auto">/);
  assert.match(html, /<article class="panel rover-live-panel rover-map-panel sim-live-map-panel" data-rover-tab="manual" id="liveMapPanel">/);
  const auto = html.slice(html.indexOf('id="autoMonitor"'), html.indexOf('id="steerPotPanel"'));
  for (const id of ['autoGridBlock', 'localGridPanel', 'localGridCanvas']) assert.ok(auto.includes(`id="${id}"`), `${id} stays in the monitoring view`);
}
console.log('PASS: LiDAR in the 자율주행 tab: started without a button, a stopped stream borrowed and given back, a running one never stopped by the tab, the sentence under the picture');
