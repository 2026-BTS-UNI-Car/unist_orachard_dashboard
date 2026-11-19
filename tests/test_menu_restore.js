// UI-16 (2026-11-14): the menu and the page layout are those of before the redesign (becfc6d, static/index.html),
// without the research page. The new features (front-wheel steering, one-press autonomy, the steering
// potentiometer, the executor's local grid, the field tuning values) are parts of Rover Control.
//
// tests/fixtures/menu_before_redesign_becfc6d.json holds the menu and the page titles of becfc6d. Where the git
// history is there, the fixture is checked against `git show becfc6d:static/index.html` as well.
// Write the fixture again: node tests/test_menu_restore.js --write-fixture
'use strict';
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const ROOT = path.join(__dirname, '..');
const FIXTURE = path.join(__dirname, 'fixtures', 'menu_before_redesign_becfc6d.json');
const REFERENCE = 'becfc6d';
const REMOVED = ['/research'];   // decision 15: the research page stays removed

const stripTags = (markup) => markup.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim();

// [route, href, label] of every menu item.
function menuOf(html) {
  const nav = html.match(/<nav class="nav-list">([\s\S]*?)<\/nav>/);
  assert.ok(nav, 'menu');
  return [...nav[1].matchAll(/<a data-route="([^"]+)" href="([^"]+)">([\s\S]*?)<\/a>/g)].map((m) => [m[1], m[2], stripTags(m[3])]);
}

// { route: [titles of h1, h2 and h3 in page order] }.
function pageTitlesOf(html) {
  const pages = {};
  for (const chunk of html.split(/<section class="page[^"]*" data-page="/).slice(1)) {
    const route = chunk.slice(0, chunk.indexOf('"'));
    pages[route] = [...chunk.matchAll(/<h([123])\b[^>]*>([\s\S]*?)<\/h\1>/g)].map((m) => stripTags(m[2]));
  }
  return pages;
}

function referenceHtml() {
  try {
    return execFileSync('git', ['-C', ROOT, 'show', `${REFERENCE}:static/index.html`], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
  } catch (_) {
    return null;   // no history here (a copy without .git): the fixture is the reference
  }
}

function fixtureFrom(html) {
  return {
    source: `${REFERENCE}:static/index.html`,
    sha256: crypto.createHash('sha256').update(html, 'utf8').digest('hex'),
    removed: REMOVED,
    menu: menuOf(html),
    pages: pageTitlesOf(html),
  };
}

if (process.argv.includes('--write-fixture')) {
  const html = referenceHtml();
  assert.ok(html, `git show ${REFERENCE}:static/index.html`);
  fs.writeFileSync(FIXTURE, `${JSON.stringify(fixtureFrom(html), null, 1)}\n`);
  console.log(`wrote ${FIXTURE}`);
  process.exit(0);
}

const fixture = JSON.parse(fs.readFileSync(FIXTURE, 'utf8'));
const reference = referenceHtml();
if (reference !== null) {
  assert.deepEqual(fixtureFrom(reference), fixture, `the fixture is what ${REFERENCE} has`);
} else {
  console.log(`(no git history: ${REFERENCE} is read from the fixture only)`);
}

const html = fs.readFileSync(path.join(ROOT, 'static', 'index.html'), 'utf8');

// ---- Menu: the same items, labels, order and addresses as before the redesign, without Research ----
{
  const expected = fixture.menu.filter(([route]) => !REMOVED.includes(route));
  assert.equal(expected.length, 8);
  assert.deepEqual(menuOf(html), expected, 'menu = before the redesign minus Research');
  assert.deepEqual(expected.map(([, , label]) => label),
    ['Overview', 'Connection', 'System', 'Network', 'Docker', 'Fan Control', 'Rover Control', 'Docs']);
}

// ---- Pages: every old page is there with its titles in the old order; Research is not ----
// The only new titles are those of the new parts of Rover Control.
const NEW_TITLES = {
  '/rover-control': ['조향 가변저항', '조향 보정', '거리 이동', '자율주행 · 열 따라가기', '현장 조정 값', '기본 묶음', '고급 묶음', '변경 기록',
    // 2026-11-19: in-page tabs of Rover Control (the menu is unchanged)
    '주행 감시', '중심선 대비 오차', '실행기 국소 격자', '거리 이동 줄자 보정', '기록된 자율 계산'],
};
{
  const now = pageTitlesOf(html);
  const oldRoutes = Object.keys(fixture.pages).filter((route) => !REMOVED.includes(route));
  assert.deepEqual(Object.keys(now), oldRoutes, 'the same pages in the same order');
  for (const route of oldRoutes) {
    const extra = NEW_TITLES[route] || [];
    const kept = now[route].filter((title) => !extra.includes(title));
    assert.deepEqual(kept, fixture.pages[route], `${route}: the panel titles of before the redesign, in the old order`);
    for (const title of extra) assert.ok(now[route].includes(title), `${route}: new part ${title}`);
  }
  for (const route of REMOVED) assert.ok(!(route in now), `${route} stays removed`);
  // Nothing of the six-screen layout is left in the page.
  assert.doesNotMatch(html, /data-page="\/(map|sensors|records|tuning)"|data-system-panel|system-tabs|driveViewSwitch|data-view=/);
}

// ---- New features: inside Rover Control, in its in-page tabs (2026-11-19, user request: "보정관련된 영역을 좀 분리해서
// 보여주면 좋겠음 아예 탭을 따로 만들던가", "자율주행 관련 영역을 따로 만들던지 탭을 새로 만들던지"). The menu is unchanged; the
// tabs only show and hide the parts of the one Rover Control page. ----
{
  const page = (route) => {
    const start = html.indexOf(`data-page="${route}"`);
    const end = html.indexOf('<section class="page', start + 1);
    return html.slice(start, end < 0 ? html.length : end);
  };
  const rover = page('/rover-control');
  // The tab row: four tabs after the status strip, before the parts; no link, no route.
  const tabs = rover.match(/<div aria-label="Rover Control 구역" class="tabs rover-tabs" id="roverTabs" role="tablist">([\s\S]*?)<\/div>\n/);
  assert.ok(tabs, 'the tab row');
  assert.deepEqual([...tabs[1].matchAll(/data-rover-tab-button="([a-z]+)"[^>]*>([^<]+)</g)].map((m) => [m[1], m[2]]),
    [['manual', '수동 조종'], ['auto', '자율주행'], ['setup', '보정·설정'], ['record', '기록']]);
  assert.ok(!/href=|data-route=/.test(tabs[1]), 'tabs are buttons, not menu entries');
  assert.ok(rover.indexOf('<section class="status-strip">') < rover.indexOf('id="roverTabs"'));
  assert.ok(rover.indexOf('id="roverTabs"') < rover.indexOf('<section class="rover-layout"'));
  // Every part of the layout names its tab(s).
  const layout = rover.slice(rover.indexOf('<section class="rover-layout"'));
  const parts = [...layout.matchAll(/<article class="([^"]*)" data-rover-tab="([a-z ]+)"([^>]*)>/g)].map((m) => {
    const id = m[3].match(/ id="([^"]+)"/);
    return [id ? id[1] : m[1].split(' ').filter((c) => c.startsWith('rover-')).pop(), m[2]];
  });
  // 2026-11-20 (user: "실시간 점유 격자 지도 위치에 … 실시간 라이다만 넣어 줘, 자율주행 탭에서"): the LiDAR picture is a
  // part of 수동 조종 and of 자율주행; the live occupancy grid map is a part of 수동 조종 only.
  assert.deepEqual(Object.fromEntries(parts), {
    'rover-lidar-panel': 'manual auto', liveMapPanel: 'manual', 'rover-camera-panel': 'manual', 'rover-gps-panel': 'manual',
    'rover-imu-panel': 'manual', 'rover-drive-panel': 'manual',
    autoPanel: 'auto', autoMonitor: 'auto', steerPotPanel: 'setup', steerCalPanel: 'setup', steerDistTapePanel: 'setup',
    tuningPanel: 'setup', roverRecordPanel: 'record', 'rover-links-panel': 'setup', 'rover-archive-panel': 'record auto',
  }, 'the parts of each tab');
  assert.equal((layout.match(/<article /g) || []).length, parts.length, 'no part without a tab');
  // 수동 조종: the drive panel with the manual steering and the distance move (forward and backward).
  const drive = rover.slice(rover.indexOf('<article class="panel rover-drive-panel"'), rover.indexOf('id="autoPanel"'));
  assert.ok(drive.includes('<h2>주행 제어</h2>'), 'the drive panel');
  for (const id of ['steerDrive', 'steerDistPanel', 'roverArmButton', 'roverStopButton', 'steerDistDirection']) {
    assert.ok(drive.includes(`id="${id}"`), `${id} is in the drive panel`);
  }
  assert.ok(drive.indexOf('id="hardwareAutonomyPanel"') < drive.indexOf('id="roverArmButton"'), 'old order: autonomy row, arm, speed, pad');
  assert.ok(drive.indexOf('id="roverSpeedSlider"') < drive.indexOf('data-rover-drive="forward"'));
  assert.ok(drive.indexOf('data-rover-drive="forward"') < drive.indexOf('id="steerDrive"'), 'the steering panel after the differential parts');
  assert.match(drive, /<section class="panel-section" data-vehicle="front_steer" id="steerDistPanel">\n<div class="panel-head mini"><h3>거리 이동<\/h3>[^\n]*\n<details id="steerDistDetails">\n/,
    'the distance move is a collapsed part (no open attribute)');
  assert.match(drive, /data-dist-direction="forward"[^>]*>앞으로<\/button><button[^>]*data-dist-direction="backward"[^>]*>뒤로<\/button>/);
  assert.ok(!drive.includes('id="steerDistFeedback"'), 'the tape entry is in 보정·설정');
  // 자율주행 (2026-11-20, user: "준비 → … 자율주행 버튼 활성화, 멈추기 버튼 누르면 멈추는, 최대한 단순한 과정"): the status
  // sentence, the two inputs, three buttons that stay in place, one line of caution; then the monitoring view with six
  // tiles and the local grid. Everything else of the tab is in one folded 자세히.
  const auto = rover.slice(rover.indexOf('id="autoPanel"'), rover.indexOf('id="steerPotPanel"'));
  const start = auto.slice(0, auto.indexOf('id="autoMonitor"'));
  assert.deepEqual([...auto.matchAll(/<option[^>]* value="([a-z_]*)">([^<]+)</g)].map((m) => [m[1], m[2]]),
    [['off', 'GPS 사용 안 함'], ['weak_constraint', 'GPS 사용함']]);
  assert.deepEqual([...start.matchAll(/<button class="action-button ([a-z]+)"( disabled="")? id="([A-Za-z]+)" type="button">([^<]+)</g)].map((m) => [m[3], m[4], m[1], Boolean(m[2])]),
    [['autoPrepare', '준비', 'secondary', true], ['autoPrimary', '자율 주행 시작', 'primary', true], ['autoStopButton', '멈추기', 'danger', false]],
    'three buttons in this order; 멈추기 is never off');
  assert.deepEqual([...start.matchAll(/ id="([A-Za-z]+)"/g)].map((m) => m[1]),
    ['autoStatus', 'autoRowLen', 'autoGnssMode', 'autoPrepare', 'autoPrimary', 'autoStopButton', 'autoHint'], 'top to bottom');
  assert.equal((start.match(/<p /g) || []).length, 1, 'one line of caution, no paragraph of instructions');
  for (const id of ['autoRecordRuns', 'autoRecordButton', 'autoRecordState', 'autoNotice']) assert.ok(!html.includes(`id="${id}"`), `${id} is gone: the start records by itself`);
  assert.doesNotMatch(auto, /래치/, 'no latch wording on the tab');
  const more = auto.slice(auto.indexOf('<details id="autoMore"><summary>자세히</summary>'), auto.indexOf('</details>'));
  assert.equal((auto.match(/<details/g) || []).length, 1, 'one folded part (no open attribute)');
  for (const id of ['autoMode', 'autoEnd', 'autoMoreGrid', 'autoMetricsBlock', 'autoMetricsGrid', 'autoScale', 'autoScaleOffer', 'autoDetails']) {
    assert.ok(more.includes(`id="${id}"`), `${id} is in the folded 자세히`);
  }
  for (const id of ['autoMonitorGrid', 'localGridPanel', 'localGridCanvas', 'autoGridBlock']) {
    assert.ok(auto.includes(`id="${id}"`) && !more.includes(`id="${id}"`), `${id} stays on the screen`);
  }
  assert.match(auto, /<div class="sim-map-stage" hidden="" id="localGridPanel">/, 'the grid picture shows when the executor sends one');
  // 보정·설정: potentiometer, calibration (not folded any more: the tab is the fold), tape corrections, tuning (folded).
  assert.match(rover, /<article class="panel rover-setup-panel" data-rover-tab="setup" data-vehicle="front_steer" id="steerCalPanel">\n<div class="panel-head"><h2>조향 보정<\/h2>/);
  assert.ok(!rover.includes('id="steerCalDetails"'));
  for (const id of ['steerDistActual', 'steerDistFeedback', 'steerDistReset']) assert.ok(rover.slice(rover.indexOf('id="steerDistTapePanel"')).includes(`id="${id}"`), id);
  assert.match(rover, /<article class="panel rover-tuning-panel" data-rover-tab="setup" id="tuningPanel">\n<div class="panel-head"><h2>현장 조정 값<\/h2><span class="status-chip" id="tuneState">[^<]*<\/span><\/div>\n<details id="tuningDetails">\n/,
    'the tuning values are a collapsed part (no open attribute)');
  for (const id of ['tuneForm', 'tuneBasic', 'tuneAdvanced', 'tuneLocked', 'tuneLogBody', 'tuneApply', 'tunePreset']) {
    assert.ok(rover.includes(`id="${id}"`), `${id} on Rover Control`);
  }
  // 기록: one check box per sensor.
  const record = rover.slice(rover.indexOf('id="roverRecordPanel"'), rover.indexOf('rover-links-panel'));
  assert.deepEqual([...record.matchAll(/data-record-sensor="([a-z]+)"/g)].map((m) => m[1]),
    ['lidar', 'camera', 'imu', 'gps', 'drive', 'steering', 'executor', 'tf']);
  assert.ok(!record.includes('data-record-kind'), 'the kind buttons are gone');
  // 2026-11-20 (user: "글이 너무 많고 쓸데없는 기능이 너무 많다"): what is rarely used is folded (closed <details>), nothing
  // is removed and every id is where the scripts look for it. What the operator works with stays on the screen.
  const folded = (id) => {
    const at = rover.indexOf(`id="${id}"`);
    assert.ok(at >= 0, `${id} is on Rover Control`);
    return rover.lastIndexOf('<details', at) > rover.lastIndexOf('</details>', at);
  };
  for (const id of ['roverLidarRangeSlider', 'roverLidarPointSlider', 'roverLidarStats', 'roverCameraResolutionSelect', 'roverCameraFpsSelect',
    'roverCameraStats', 'roverImuStats', 'steerAdRate', 'steerAngleChoices', 'screenResetButton', 'steerCalMarginLeft', 'steerCalMore',
    'roverControlDetails', 'roverRecordSensors']) assert.ok(folded(id), `${id} is in a folded part`);
  for (const id of ['roverLidarCanvas', 'roverLidarToggle', 'roverCameraImage', 'roverCameraToggle', 'roverImuCanvas', 'roverImuHeading',
    'steerArmButton', 'steerDrag', 'steerReadout', 'steerSpeedSlider', 'steerHelp', 'steerCommandStatus', 'steerPotDetails', 'steerCalRows',
    'steerCalStop', 'steerCalStatus', 'steerDistTapeTerrain', 'steerDistActual', 'steerDistFeedback', 'roverRecordStartButton',
    'roverRecordStopButton', 'roverRecordingStatus']) assert.ok(!folded(id), `${id} stays on the screen`);
  assert.doesNotMatch(rover, /<details[^>]*\sopen\b/, 'every fold starts closed');
  // The LiDAR part: one picture for both tabs (no second canvas), its button, and the sentence that 자율주행 shows
  // instead of the button (hidden until that tab is shown).
  {
    const lidar = rover.slice(rover.indexOf('<article class="panel rover-live-panel rover-lidar-panel"'), rover.indexOf('id="liveMapPanel"'));
    assert.deepEqual([...lidar.matchAll(/<canvas [^>]*id="([A-Za-z]+)"/g)].map((m) => m[1]), ['roverLidarCanvas']);
    assert.match(lidar, /<button class="action-button secondary" id="roverLidarToggle" type="button">LiDAR 시작<\/button>/);
    assert.match(lidar, /<\/canvas>\n<\/div>\n<p class="sim-map-status" hidden="" id="roverLidarNote" role="status"><\/p>\n<details>/,
      'the sentence sits under the picture, above the folded range and point settings');
    assert.equal((html.match(/id="liveMapCanvas"/g) || []).length, 1, 'the live map itself is unchanged');
  }
  // The tape part of 보정·설정 has its own terrain choice (the options come from the terrain table).
  assert.match(rover.slice(rover.indexOf('id="steerDistTapePanel"')), /^[^]*?<label class="field" for="steerDistTapeTerrain">지형<select class="input" id="steerDistTapeTerrain"><\/select><\/label>\n<label class="field" for="steerDistActual">/);
  // The status strip of Rover Control comes first again, as the KPI row did.
  assert.ok(rover.indexOf('<section class="status-strip">') < rover.indexOf('<section class="rover-layout"'));
}

// ---- Addresses: the menu addresses, and every six-screen address as an alias of the page with that part ----
{
  const config = fs.readFileSync(path.join(ROOT, 'static', 'app', '00-config-state.js'), 'utf8');
  const routes = JSON.parse(config.match(/^const ROUTES = (\[.*\]);$/m)[1]);
  assert.deepEqual(routes, menuOf(html).map(([route]) => route));
  const aliases = Function(`return ${config.match(/^const ROUTE_ALIASES = (\{[\s\S]*?\});$/m)[1]}`)();
  assert.deepEqual(aliases, {
    '/drive': '/rover-control', '/map': '/rover-control', '/records': '/rover-control', '/tuning': '/rover-control',
    '/sensors': '/connection', '/system/resources': '/system', '/system/network': '/network', '/system/docker': '/docker',
    '/system/fan': '/fan', '/research': '/',
  });
  const focus = Function(`return ${config.match(/^const ROUTE_FOCUS = (\{.*\});$/m)[1]}`)();
  for (const id of Object.values(focus)) assert.ok(html.includes(`id="${id}"`), `alias focus ${id} exists`);
  const app = fs.readFileSync(path.join(ROOT, 'dashboard_server', 'app.py'), 'utf8');
  const served = app.match(/^APP_ROUTES = \{([\s\S]*?)\}$/m)[1].match(/"[^"]+"/g).map((item) => JSON.parse(item));
  assert.deepEqual([...served].sort(), [...routes, ...Object.keys(aliases)].sort(), 'the server answers the menu addresses and the aliases');
}
// ---- The start of the page script calls only functions that are still there (the big view switch went away) ----
{
  const app = require('./helpers/app_source').readAppSource();
  const main = fs.readFileSync(path.join(ROOT, 'static', 'app', '99-main.js'), 'utf8');
  const calls = [...main.matchAll(/^([A-Za-z_$][\w$]*)\(\)(?:\.catch\([^)]*\))?;$/gm)].map((m) => m[1]);
  assert.ok(calls.includes('renderRoute') && calls.includes('initRoverControl'));
  for (const name of calls) assert.match(app, new RegExp(`function ${name}\\(`), `99-main.js calls ${name}()`);
}
console.log('PASS: menu and page titles as before the redesign (becfc6d) without Research; new parts inside Rover Control in its in-page tabs; aliases');
