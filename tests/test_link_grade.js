// NET-10: the link grade follows a changed link within 5 s, does not swing with a
// swinging link, says "offline" after two lost pings, and in automatic mode
// reopens the streams once per change of plan. The status bar badge shows it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app', '04-link-grade.js'), 'utf8');

function fixture(chosen = 'auto') {
  let now = 0;
  const badge = {className: 'status-chip', textContent: '', title: ''};
  const reopened = [];
  const context = {
    performance: {now: () => now}, Date, Math, Number, JSON, URLSearchParams, AbortController,
    location: {search: ''}, localStorage: {getItem: () => null}, document: {hidden: false, addEventListener() {}},
    state: {telemetryFailures: 0, lastTelemetryPoll: 0, imuLive: {}, cameraLive: {}, lidarLive: {}, recordings: {}},
    liveNetworkProfile: () => chosen,
    $: id => (id === 'connectionState' ? badge : null),
    setTimeout, clearTimeout, setInterval: () => 1,
    pageFetch: async () => ({ok: false, status: 204, headers: {get: () => 'app;dur=1.0'}}),
    window: {RoverGps: {profileChanged: () => reopened.push('gps')}},
    closeImuLive() {}, closeCameraLive() {}, closeLidarLive() {}, startImuLive() {}, startCameraLive() {}, startLidarLive() {},
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  const sample = (ms, ok = true) => { now += 1000; context.noteLinkSample(ms, ok, 'ping'); };
  return {context, badge, reopened, sample, grade: () => context.window.LinkGrade.grade(), profile: () => context.window.LinkGrade.profile(),
    advance: ms => { now += ms; }};
}

// Grades from the smallest of the last 4 samples.
{
  const f = fixture();
  assert.equal(f.grade(), 'unknown');
  assert.equal(f.profile(), 'slow', 'a cautious plan before the first answer');
  f.sample(12);
  assert.equal(f.grade(), 'unknown', 'one answer is not enough (the first comes while the page starts)');
  f.sample(12);
  assert.equal(f.grade(), 'good');
  assert.equal(f.profile(), 'detail');
  assert.equal(f.badge.textContent, '연결 좋음');
  assert.match(f.badge.className, /ok/);
  assert.deepEqual(f.reopened, ['gps'], 'the streams reopen with the new plan once');
}
// A first answer slowed by the starting page does not hold the page in the very slow plan.
{
  const f = fixture();
  f.sample(499); f.sample(4.7);
  assert.equal(f.grade(), 'good', 'good after the second answer');
}
// A slower link is followed within 5 s (bench profiles: good 10 ms, normal 40, bad 150, very bad 300 ms).
for (const [rtt, grade, profile] of [[45, 'normal', 'balanced'], [160, 'slow', 'slow'], [310, 'very_slow', 'tiny']]) {
  const f = fixture();
  for (let k = 0; k < 6; k++) f.sample(10);
  assert.equal(f.grade(), 'good');
  let seconds = 0;
  while (f.grade() === 'good' && seconds < 10) { f.sample(rtt); seconds += 1; }
  assert.equal(f.grade(), grade, `${rtt} ms`);
  assert.equal(f.profile(), profile);
  assert.ok(seconds <= 5, `${rtt} ms: followed after ${seconds} s`);
}
// Queueing behind a picture does not lower the grade: only the smallest sample counts.
{
  const f = fixture();
  for (const rtt of [10, 95, 60, 11, 120, 80, 12, 99]) f.sample(rtt);
  assert.equal(f.grade(), 'good');
}
// A better link is taken only after 10 s with margin.
{
  const f = fixture();
  for (let k = 0; k < 6; k++) f.sample(160);
  assert.equal(f.grade(), 'slow');
  let seconds = 0;
  while (f.grade() === 'slow' && seconds < 30) { f.sample(12); seconds += 1; }
  assert.equal(f.grade(), 'good');
  assert.ok(seconds >= 10 && seconds <= 15, `upgraded after ${seconds} s`);
}
// A first answer slower than the link (the page was starting) does not hold a bench "bad" link
// (150 ms round trip, about 160 ms measured) in the very slow plan: it comes back to "slow".
{
  const f = fixture();
  f.sample(480); f.sample(310); f.sample(300); f.sample(290);
  assert.equal(f.grade(), 'very_slow');
  let seconds = 0;
  while (f.grade() === 'very_slow' && seconds < 30) { f.sample(158 + (seconds % 3) * 6); seconds += 1; }
  assert.equal(f.grade(), 'slow', `still ${f.grade()} after ${seconds} s`);
  assert.ok(seconds <= 15, `back to slow after ${seconds} s`);
}
// While driving, control answers come about 5 a second and some wait behind a camera picture:
// a burst of slow control samples does not lower the grade while the pings show the bare link.
{
  const f = fixture();
  for (let k = 0; k < 6; k++) f.sample(45);
  assert.equal(f.grade(), 'normal');
  for (let second = 0; second < 60; second++) {
    f.sample(46);
    for (let k = 0; k < 5; k++) { f.advance(150); f.context.noteLinkSample(120 + (k * 37) % 90, true, 'control'); }
    f.advance(-750);
  }
  assert.equal(f.grade(), 'normal', 'queued control answers do not change the plan');
  assert.equal(f.context.window.LinkGrade.changesPerMinute(), 0);
}
// A link that swings between good and slow every 6 s changes the grade at most 4 times a minute.
{
  const f = fixture();
  f.sample(12);
  const start = f.context.window.LinkGrade.changesPerMinute();
  for (let second = 0; second < 120; second++) f.sample(Math.floor(second / 6) % 2 ? 180 : 12);
  assert.ok(f.context.window.LinkGrade.changesPerMinute() <= 4, `changes in the last minute: ${f.context.window.LinkGrade.changesPerMinute()}`);
  assert.ok(start <= 1);
}
// Two pings in a row without an answer: offline; the plan stays, nothing reopens; the next answer recovers.
{
  const f = fixture();
  f.sample(45); f.sample(45);
  const reopenedBefore = f.reopened.length;
  f.sample(null, false);
  assert.equal(f.grade(), 'normal', 'one lost ping is not offline');
  f.sample(null, false);
  assert.equal(f.grade(), 'offline');
  assert.equal(f.profile(), 'balanced', 'the last plan is kept while offline');
  assert.equal(f.reopened.length, reopenedBefore, 'nothing reopens while offline');
  assert.equal(f.badge.textContent, '연결 끊김');
  assert.match(f.badge.className, /bad/);
  assert.match(f.badge.title, /스스로 정지/);
  f.sample(45);
  assert.equal(f.grade(), 'normal');
}
// Slow: the badge says why the pictures are smaller; a chosen quality never reopens streams.
{
  const f = fixture();
  f.sample(160); f.sample(160);
  assert.equal(f.badge.textContent, '연결 느림');
  assert.match(f.badge.title, /연결이 느려 화면 자료를 줄였습니다/);
  const chosen = fixture('detail');
  chosen.sample(160); chosen.sample(160);
  assert.deepEqual(chosen.reopened, [], 'a chosen transfer quality is not changed by the grade');
  assert.match(chosen.badge.title, /직접 골랐습니다/);
}
// The status bar shows "offline" when the status requests stop, whatever the pings say.
{
  const f = fixture();
  f.sample(12);
  f.context.state.telemetryFailures = 3;
  f.context.renderLinkBadge();
  assert.equal(f.badge.textContent, '연결 끊김');
}
console.log('Link grade: follows within 5 s, no swinging, offline after two lost pings, one badge: PASS');
