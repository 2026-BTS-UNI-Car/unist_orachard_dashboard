// One recording per driven stretch (2026-11-20): what the page and the server must agree on.
// - The stop reasons in words are written into the record of a stretch by the server
//   (dashboard_server/run_recording.py STOP_DETAIL / STOP_REASON) and shown by the autonomy tab
//   (static/steer/auto-model.mjs): the two tables are the same.
// - The recording list shows a stretch in two short lines (static/app/12-archive.js recordingStretchText), with the
//   same words for a time as the autonomy tab (durationText).
// No browser. Run: node --test tests/test_run_recording_words.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { STOP_DETAIL, STOP_REASON, durationText, stopWords } from '../static/steer/auto-model.mjs';

const read = (path) => fs.readFileSync(new URL(path, import.meta.url), 'utf8');

test('the stop reasons of the record are the words of the autonomy tab', () => {
  const python = read('../dashboard_server/run_recording.py');
  const table = (name) => {
    const block = python.match(new RegExp(`^${name} = \\{([\\s\\S]*?)^\\}`, 'm'));
    assert.ok(block, `${name} is in run_recording.py`);
    return Object.fromEntries([...block[1].matchAll(/"([^"]+)":\s*"([^"]*)"/g)].map(([, key, value]) => [key, value]));
  };
  assert.deepEqual(table('STOP_DETAIL'), STOP_DETAIL);
  assert.deepEqual(table('STOP_REASON'), STOP_REASON);
  // the same choice between the detail and the category as stopWords (the server's stop_words mirrors it)
  assert.equal(stopWords({ stop_reason: 'ROW_END', stop_detail: 'operator_stop' }), '열 끝 도착');
  assert.equal(stopWords({ stop_reason: 'CONTROL_AUTHORITY', stop_detail: 'lease_lost' }), '조종 권한 거둠');
  assert.equal(stopWords({ stop_reason: 'CONTROL_AUTHORITY', stop_detail: 'new_detail' }), '조향·구동 응답 부족');
  assert.match(python, /def stop_words\(category, detail\):[\s\S]*?if category == "ROW_END":[\s\S]*?detail in STOP_DETAIL[\s\S]*?STOP_REASON\.get\(category\)/);
});

test('the recording list: a stretch in two short lines', () => {
  const source = read('../static/app/12-archive.js');
  const cut = (name) => {
    const start = source.indexOf(`function ${name}(`);
    assert.ok(start >= 0, `${name} is in 12-archive.js`);
    return source.slice(start, source.indexOf('\n}\n', start) + 3);
  };
  const box = {};
  vm.runInNewContext(`${cut('fmtDurationWords')}\n${cut('recordingStretchText')}\nout.words = fmtDurationWords; out.text = recordingStretchText;`, { out: box });
  for (const seconds of [0, 0.4, 35.2, 59.6, 60, 105, 3599.6, 3600, 3725, 7260]) assert.equal(box.words(seconds), durationText(seconds), `${seconds} s`);
  const row = (segment) => JSON.parse(JSON.stringify(box.text({ id: 'r', tag: 'autonomy', segment })));   // out of the other realm
  assert.deepEqual(row({ run: 7, segment: 2, resumed: true, ended: true, end_text: '열 끝 도착', drive_time_s: 105.2, distance_m: 43.2 }),
    ['자율주행 · 2번 구간 · 다시 출발', '1분 45초 · 43.2 m · 열 끝 도착']);
  assert.deepEqual(row({ run: 7, segment: 1, resumed: false, ended: true, end_text: '화면 연결 끊김', drive_time_s: 35.2, distance_m: 12.3 }),
    ['자율주행 · 1번 구간', '35초 · 12.3 m · 화면 연결 끊김']);
  assert.deepEqual(row({ run: 7, segment: 3, resumed: true, ended: false }), ['자율주행 · 3번 구간 · 다시 출발', '기록 중'], 'the stretch that is being recorded');
  assert.deepEqual(row({ run: 3, segment: null, resumed: true, ended: true, end_text: null, drive_time_s: 4, distance_m: null }),
    ['자율주행 · 다시 출발', '4초'], 'a number that is not known is not made up; what is missing is left out');
  assert.deepEqual(row({ segment: 1, resumed: false, ended: true, end_text: '출발하지 않음', drive_time_s: 0, distance_m: 0 }),
    ['자율주행 · 1번 구간', '0초 · 0.0 m · 출발하지 않음']);
  assert.equal(box.text({ id: 'r', tag: 'autonomy' }), null, 'a recording without a record keeps its old lines');
  assert.equal(box.text({ id: 'r', tag: 'manual', segment: null }), null);
  // the list uses it: the head line in place of the sensor label, the second line only when there is one
  assert.match(source, /const stretch = recordingStretchText\(item\);/);
  assert.match(source, /<span>\$\{escapeHtml\(stretch \? stretch\[0\] : recordingSensorLabel\(item\)\)\}<\/span>/);
  assert.match(source, /\$\{stretch && stretch\[1\] \? `<small>\$\{escapeHtml\(stretch\[1\]\)\}<\/small>` : ""\}/);
});
