// Wi-Fi management of the Network page (static/network/wifi-manager.mjs): cards escape names, the trial
// notice texts, priority checks, the lazy loading from 04-telemetry.js and the markup it needs.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  ageText, clockText, errorText, lastTrialMessage, savedCardsHtml, scanCardsHtml, securityText, signalText, trialText, validPriority,
} from '../static/network/wifi-manager.mjs';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => fs.readFileSync(path.join(root, name), 'utf8');

// Names come from radios nearby: always escaped.
{
  const evil = '<img src=x onerror=alert(1)>';
  const html = scanCardsHtml([{ ssid: evil, band: '2.4GHz', channel: 6, signal_pct: 70, signal_dbm: -58, security: 'WPA2', supported: true, in_use: false, ap_count: 2 }], [], true);
  assert.ok(!html.includes('<img'), 'scan card escapes the SSID');
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(html.includes('-58 dBm · 70%') && html.includes('2.4 GHz · ch 6') && html.includes('AP 2개'));
  assert.ok(html.includes('data-wifi-action="pick-scan"') && !html.includes(' disabled'), 'a joinable network has an enabled button');
  const saved = savedCardsHtml([{ uuid: 'u', name: evil, ssid: 'x"y', autoconnect: true, priority: 200, active: false, key_mgmt: 'wpa-psk', band: '', last_used: null, storage: 'disk' }], false, true);
  assert.ok(!saved.includes('<img') && saved.includes('x&quot;y'), 'saved card escapes name and SSID');
  assert.ok(saved.includes('value="200"') && saved.includes(' checked'), 'priority and autoconnect are shown');
  assert.match(saved, /data-wifi-action="pick-saved"[^>]* disabled/, 'connect is disabled when changes are not allowed');
}

// In use, unsupported and saved networks.
{
  const networks = [
    { ssid: 'a', supported: true, in_use: true }, { ssid: 'b', supported: false, in_use: false }, { ssid: 'c', supported: true, in_use: false },
  ];
  const html = scanCardsHtml(networks, [{ ssid: 'c' }], true);
  assert.ok(html.includes('연결됨') && html.includes('연결 불가') && html.includes('저장됨'));
  assert.equal((html.match(/pick-scan/g) || []).length, 1, 'only the joinable, not connected network has a button');
}

// Texts.
assert.equal(clockText(149.2), '2:30');
assert.equal(clockText(-3), '0:00');
assert.equal(ageText(42), '42초 전');
assert.equal(ageText(90000), '1일 전');
assert.equal(securityText('wpa-psk'), 'WPA/WPA2');
assert.equal(securityText('open'), '비밀번호 없음');
assert.equal(signalText({ signal_dbm: null, signal_pct: 40 }), '40%');
assert.equal(validPriority('200'), 200);
for (const bad of ['', '1.5', '1000', '-1000', 'x']) assert.equal(validPriority(bad), null, bad);
assert.equal(errorText({ state: 'invalid_request', error: 'ssid_not_in_scan' }), '최근 검색 결과에 없는 Wi-Fi입니다. 주변 검색을 다시 하세요.');
assert.equal(errorText({ state: 'rate_limited', retry_after_s: 4.2 }), '5초 뒤에 다시 하세요.');

{
  const waiting = trialText({ ssid: 'New', phase: 'waiting_confirm', previous: 'Field-2G', ipv4: '192.0.2.9', gateway_reply: true, internet_reply: false }, 61, false);
  assert.equal(waiting.title, 'New에 연결됨 · 남은 시간 1:01');
  assert.ok(waiting.detail.includes('주소 192.0.2.9 · 게이트웨이 응답 있음 · 인터넷 응답 없음'));
  assert.ok(waiting.detail.includes('Field-2G(으)로 돌아갑니다'));
  const activating = trialText({ ssid: 'New', phase: 'activating', previous: 'AP' }, 140, true);
  assert.ok(activating.title.includes('연결하는 중') && activating.detail.endsWith('끊겨 있습니다.'));
  assert.equal(lastTrialMessage({ ssid: 'New', result: 'reverted', reason: 'not_confirmed_in_time' }).level, 'warn');
  assert.ok(lastTrialMessage({ ssid: 'New', result: 'kept', warnings: ['profile_not_on_disk'] }).text.includes('재부팅'));
}

// The module keeps no password: no storage API, no logging of form values.
{
  const source = read('static/network/wifi-manager.mjs');
  assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB/);
  assert.doesNotMatch(source, /console\.(log|info|debug)/);
  assert.doesNotMatch(source, /debugLog\?\.\([^)]*password/i);
  assert.match(source, /delete request\.password/);
}

// Loaded only for the Network page, from the telemetry part; the markup is in the Network page.
{
  const telemetry = read('static/app/04-telemetry.js');
  assert.match(telemetry, /import\("\/static\/network\/wifi-manager\.mjs"\)/);
  assert.match(telemetry, /if \(route !== "\/network" \|\| wifiScreenLoad\) return;/);
  const html = read('static/index.html');
  const start = html.indexOf('data-page="/network"');
  const page = html.slice(start, html.indexOf('<section class="page"', start + 1));
  for (const id of ['wifiTrialBanner', 'wifiTrialActions', 'wifiTrialConfirm', 'wifiTrialRevert', 'wifiScanButton', 'wifiScanList',
    'wifiConnectForm', 'wifiPassword', 'wifiAutoconnect', 'wifiPriority', 'wifiSavedList', 'wifiResult']) {
    assert.ok(page.includes(`id="${id}"`), `${id} on the Network page`);
  }
  assert.match(page, /id="wifiPassword"[^>]*type="password"|type="password"[^>]*id="wifiPassword"/);
  assert.doesNotMatch(html, /<script[^>]*wifi-manager/, 'not a script of the first load');
}

console.log('PASS: Wi-Fi management cards, trial texts, priority checks, lazy module and markup');
