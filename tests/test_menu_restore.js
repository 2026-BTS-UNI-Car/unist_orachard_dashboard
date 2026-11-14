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
  '/rover-control': ['조향 가변저항', '자율주행 · 열 따라가기', '현장 조정 값', '기본 묶음', '고급 묶음', '변경 기록'],
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

// ---- New features: inside Rover Control, in or next to the drive panel; the tuning values collapsed ----
{
  const page = (route) => {
    const start = html.indexOf(`data-page="${route}"`);
    const end = html.indexOf('<section class="page', start + 1);
    return html.slice(start, end < 0 ? html.length : end);
  };
  const rover = page('/rover-control');
  const drive = rover.slice(rover.indexOf('<article class="panel rover-drive-panel">'), rover.indexOf('<article class="panel rover-tuning-panel"'));
  assert.ok(drive.includes('<h2>주행 제어</h2>'), 'the drive panel');
  for (const id of ['steerDrive', 'steerPotPanel', 'autoPanel', 'roverArmButton', 'roverStopButton']) {
    assert.ok(drive.includes(`id="${id}"`), `${id} is in the drive panel`);
  }
  assert.ok(drive.indexOf('id="hardwareAutonomyPanel"') < drive.indexOf('id="roverArmButton"'), 'old order: autonomy row, arm, speed, pad');
  assert.ok(drive.indexOf('id="roverSpeedSlider"') < drive.indexOf('data-rover-drive="forward"'));
  assert.ok(drive.indexOf('data-rover-drive="forward"') < drive.indexOf('id="steerDrive"'), 'the steering panel after the differential parts');
  assert.match(rover, /<article class="panel rover-tuning-panel" id="tuningPanel">\n<div class="panel-head"><h2>현장 조정 값<\/h2><span class="status-chip" id="tuneState">[^<]*<\/span><\/div>\n<details id="tuningDetails">\n/,
    'the tuning values are a collapsed part (no open attribute)');
  for (const id of ['tuneForm', 'tuneBasic', 'tuneAdvanced', 'tuneLocked', 'tuneLogBody', 'tuneApply', 'tunePreset']) {
    assert.ok(rover.includes(`id="${id}"`), `${id} on Rover Control`);
  }
  // The executor's local grid sits on the live occupancy grid map, which is inside the LiDAR panel (old place).
  const lidar = rover.slice(rover.indexOf('<article class="panel rover-live-panel rover-lidar-panel">'), rover.indexOf('<article class="panel rover-live-panel rover-camera-panel">'));
  const liveMap = lidar.slice(lidar.indexOf('id="liveMapPanel"'));
  assert.ok(liveMap.includes('id="liveMapCanvas"') && liveMap.indexOf('id="liveMapCanvas"') < liveMap.indexOf('id="localGridPanel"'), 'local grid under the live map');
  assert.match(liveMap, /<div class="local-grid-block" hidden="" id="localGridPanel">/, 'shown only when the executor sends a grid');
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
console.log('PASS: menu and page titles as before the redesign (becfc6d) without Research; new parts inside Rover Control; aliases');
