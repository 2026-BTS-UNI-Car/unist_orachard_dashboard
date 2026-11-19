// The seven pages other than Rover Control after the tidy-up of 2026-11-20 (docs/SCREENS_KO.md 1-1): the key state
// of a page first, rarely used parts in closed folds, less text. The menu, the pages and the markup order of the
// panel titles are unchanged (tests/test_menu_restore.js); the visual order comes from two order classes.
// Reads the shipped files only.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'static', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(ROOT, 'static', 'styles.css'), 'utf8');
const app = require('./helpers/app_source').readAppSource();

const page = (route) => {
  const start = html.indexOf(`data-page="${route}"`);
  assert.ok(start >= 0, route);
  const end = html.indexOf('<section class="page', start + 1);
  return html.slice(start, end < 0 ? html.length : end);
};
// The markers appear in this order in the text.
const inOrder = (text, markers, message) => {
  const at = markers.map((marker) => text.indexOf(marker));
  assert.ok(at.every((index) => index >= 0), `${message}: ${markers[at.findIndex((index) => index < 0)]} is missing`);
  assert.deepEqual([...at].sort((a, b) => a - b), at, message);
};
// True when the element with this id sits inside a <details> of the text.
const folded = (text, id) => {
  const at = text.indexOf(`id="${id}"`);
  assert.ok(at >= 0, id);
  return text.lastIndexOf('<details', at) > text.lastIndexOf('</details>', at);
};
const PAGES = ['/', '/connection', '/system', '/network', '/docker', '/fan', '/docs'];

// ---- Folds: closed when the page opens; what is used on a field day stays outside them ----
{
  for (const route of PAGES) assert.doesNotMatch(page(route), /<details[^>]*\sopen/, `${route}: a fold starts closed`);
  const inside = {
    '/connection': ['lidarDeviceDetail', 'lidarByIdDetail', 'lidarDriverDetail', 'lidarLastTestDetail', 'lidarPointSlider', 'lidarDownloadButton',
      'cameraResolutionSelect', 'cameraFpsSelect'],
    '/system': ['coreList', 'cpuModeText', 'chartSwap', 'swapReadout', 'chartSystemNetwork', 'systemNetworkReadout', 'chartPower', 'powerReadout'],
    '/docker': ['containerLogs'],
  };
  const outside = {
    '/': ['overviewServices', 'hostName', 'hostTemp', 'chartOverviewSystem', 'chartOverviewNetwork'],
    '/connection': ['connectionSensors', 'connectionServices', 'lidarTestButton', 'lidarTestResult', 'lidarLiveButton', 'lidarScanCanvas', 'lidarRangeSlider',
      'lidarScanStats', 'cameraLiveButton', 'cameraLiveImage', 'cameraLiveStats'],
    '/system': ['systemCpuValue', 'chartCpu', 'systemMemoryValue', 'chartMemory', 'systemSwapValue', 'systemNetworkValue', 'systemPowerValue', 'diskList'],
    '/docker': ['containerList', 'containerDetail', 'containerActions', 'containerRefreshButton', 'chartDockerCpu', 'chartDockerMem'],
    '/fan': ['fanTempValue', 'fanAutoToggle', 'fanPwmSlider', 'chartFanTemp'],
  };
  for (const [route, ids] of Object.entries(inside)) for (const id of ids) assert.ok(folded(page(route), id), `${route}: ${id} is in a fold`);
  for (const [route, ids] of Object.entries(outside)) for (const id of ids) assert.ok(!folded(page(route), id), `${route}: ${id} is not folded`);
  // The LiDAR picture and the camera picture are never folded: their streams follow the visibility of these boxes.
  for (const id of ['lidarScanCanvas', 'cameraLiveImage']) assert.ok(!folded(page('/connection'), id));
  // Network and Fan Control have no fold at all: every control of a mode change is on the page.
  for (const route of ['/network', '/fan', '/']) assert.ok(!page(route).includes('<details'), `${route}: no fold`);
  // Values that a panel shows twice are hidden, not removed (the script still writes them).
  assert.match(page('/system'), /<div class="progress" hidden="" id="memoryBar"><i><\/i><\/div>\n<small hidden="" id="memoryDetail">--<\/small>/);
}

// ---- Visual order: two classes, the markup order of the panels stays ----
{
  assert.match(css, /\n\.order-first \{\n  order: -1;\n\}\n/);
  assert.match(css, /\n\.order-last,\n\[data-page="\/network"\] > \.status-strip \{\n  order: 1;\n\}\n/);
  const classOf = (text, title) => {
    const at = text.indexOf(`<h2>${title}</h2>`);
    assert.ok(at >= 0, title);
    const open = text.lastIndexOf('<article class="', at);
    return text.slice(open + 16, text.indexOf('"', open + 16)).split(' ');
  };
  const overview = page('/'), connection = page('/connection');
  // Overview: the service list and the Jetson facts before the two charts.
  for (const title of ['CPU / Memory', 'Network RX / TX']) assert.ok(classOf(overview, title).includes('order-last'), title);
  for (const title of ['ROS2 / Rover', 'Jetson 정보']) assert.ok(!classOf(overview, title).some((name) => name.startsWith('order-')), title);
  // Connection: the sensor list first, then the device check and the services, then the two pictures.
  assert.ok(classOf(connection, 'ROS2 센서 링크').includes('order-first'));
  for (const title of ['2D LiDAR 지도', '카메라 실시간 화면']) assert.ok(classOf(connection, title).includes('order-last'), title);
  for (const title of ['RPLIDAR 상태 확인', '서비스 연결']) assert.ok(!classOf(connection, title).some((name) => name.startsWith('order-')), title);
  // Network: the mode panel before the address strip and the charts. Docker: the list and the details before the charts.
  assert.match(page('/network'), /<section class="dashboard-grid order-last">\n<article class="panel chart-panel wide">\n<div class="panel-head">\n<h2>RX \/ TX<\/h2>/);
  assert.match(page('/network'), /<article class="panel wide">\n<div class="panel-head">\n<h2>연결 정보<\/h2>/, 'the interface list takes a full row');
  assert.match(page('/docker'), /<section class="dashboard-grid order-last">\n<article class="panel chart-panel">\n<div class="panel-head"><h2>Container CPU<\/h2>/);
  // The page header and the mode panel keep the default order, so they come first.
  for (const route of PAGES) assert.doesNotMatch(page(route), /<header class="page-head[^"]*order-/, route);
  assert.match(page('/network'), /<section class="panel network-mode-panel">/);
}

// ---- Network mode panel: the trial notice and its buttons first, then the mode buttons, the state, the form, the lists ----
{
  const network = page('/network');
  inOrder(network, ['id="networkModeTag"', 'id="wifiTrialBanner"', 'id="wifiTrialActions"', 'id="wifiTrialConfirm"', 'id="wifiTrialRevert"',
    'id="networkEnableApButton"', 'id="networkScanWifiButton"', 'id="networkModeReadout"', 'id="networkModeResult"', 'id="wifiResult"',
    'id="wifiConnectForm"', 'id="wifiSavedBlock"', 'id="wifiSavedList"', 'id="wifiScanBlock"', 'id="wifiScanButton"', 'id="wifiScanList"'], 'order inside the mode panel');
  // The connect form serves both lists and belongs to neither block.
  const form = network.slice(network.indexOf('<form '), network.indexOf('</form>'));
  for (const id of ['wifiPassword', 'wifiAutoconnect', 'wifiPriority', 'wifiConnectSubmit', 'wifiConnectCancel']) assert.ok(form.includes(`id="${id}"`), id);
  assert.ok(network.indexOf('</form>') < network.indexOf('id="wifiSavedBlock"'));
  assert.match(network, /<\/form>\n<div class="panel-section wifi-stack" id="wifiSavedBlock">/);
  // The buttons are the ones the script wires; the page adds no second way to change the mode.
  assert.equal((network.match(/<button /g) || []).length, 7, 'trial keep and revert, AP, Wi-Fi, submit, cancel, scan');
  for (const id of ['networkEnableApButton', 'networkScanWifiButton']) assert.match(app, new RegExp(`\\$\\("${id}"\\)\\?\\.addEventListener\\("click"`), id);
}

// ---- System: a summary strip with the values of the panels below it ----
{
  const system = page('/system');
  inOrder(system, ['<header class="page-head">', '<section class="status-strip">', 'id="systemStripCpu"', 'id="systemStripMemory"', 'id="systemStripTemp"',
    'id="systemStripDisk"', '<section class="resource-grid">', '<h2>CPU</h2>', '<h2>Memory</h2>', '<h2>Disk</h2>'], 'strip, then the panels');
  const writes = (id) => app.match(new RegExp(`text\\("${id}", ([^\\n]*)\\);`));
  for (const id of ['systemStripCpu', 'systemStripCpuText', 'systemStripMemory', 'systemStripMemoryText', 'systemStripTemp', 'systemStripTempText',
    'systemStripDisk', 'systemStripDiskText']) assert.ok(writes(id), `the script fills ${id}`);
  // Same expressions as the panel values.
  assert.equal(writes('systemStripCpu')[1], writes('systemCpuValue')[1]);
  assert.equal(writes('systemStripMemory')[1], writes('systemMemoryValue')[1]);
  assert.equal(writes('systemStripTemp')[1], writes('hostTemp')[1]);
  // The CPU and the memory card share the first row; the power card takes a row.
  assert.doesNotMatch(css, /\.resource-card:first-child/);
  assert.match(system, /<article class="panel resource-card wide">\n<div class="panel-head"><h2>Power<\/h2>/);
}

// ---- Less text: one short line under each page title, no caption under the device values ----
{
  for (const route of PAGES) {
    const note = page(route).match(/<p class="page-note">([^<]*)<\/p>/);
    assert.ok(note && note[1].length > 0 && note[1].length <= 24, `${route}: a short line of context (${note && note[1].length} characters)`);
  }
  assert.doesNotMatch(page('/connection'), /<article class="probe-card">\n<span>[^<]+<\/span>\n<strong id="[^"]+">--<\/strong>\n<small>/, 'no caption line in a device card');
  // The notes that are always on the page (the note of the connect form is written when the form opens).
  const notes = [...page('/network').matchAll(/<p class="control-note">([^<]*)<\/p>/g)].map((match) => match[1]);
  assert.equal(notes.length, 3, 'auto switch, saved list, scan list');
  for (const note of notes) assert.ok(note.length <= 70, `network note is short: ${note}`);
}

console.log('PASS: folds closed and holding only rarely used parts, visual order by two classes, order inside the network mode panel, system summary strip, short page notes');
