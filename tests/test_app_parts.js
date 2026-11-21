// The page script lives in static/app/ as ordered classic scripts. These checks keep the split sound:
// every part is listed once in index.html, in file-name order, nothing is declared twice, each part is
// valid on its own, and nothing that runs while a part loads needs a later part.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { STATIC_DIR, appPartPaths, readAppSource } = require('./helpers/app_source');

const html = fs.readFileSync(path.join(STATIC_DIR, 'index.html'), 'utf8');
const listed = appPartPaths(html);
const onDisk = fs.readdirSync(path.join(STATIC_DIR, 'app')).filter(name => name.endsWith('.js')).sort().map(name => `app/${name}`);

// One list, same order as the file names, no leftover file and no single-file script.
assert.ok(listed.length >= 2, 'index.html loads the parts');
assert.deepEqual(listed, [...listed].sort(), 'parts are loaded in file-name order');
assert.deepEqual(listed, onDisk, 'every file in static/app is loaded exactly once');
assert.equal(new Set(listed).size, listed.length, 'no part is listed twice');
assert.ok(!fs.existsSync(path.join(STATIC_DIR, 'app.js')), 'static/app.js is gone; edit the parts');
assert.doesNotMatch(html, /src="\/static\/app\.js"/, 'index.html does not load a single app.js');

// The parts stay next to each other, after archive-video.js and before the scripts that use their globals.
const scripts = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)].map(match => match[1]);
const first = scripts.indexOf(`/static/${listed[0]}`);
assert.deepEqual(scripts.slice(first, first + listed.length), listed.map(part => `/static/${part}`), 'parts are consecutive');
assert.ok(scripts.indexOf('/static/archive-video.js') < first, 'archive-video.js loads before the parts');
for (const later of ['/static/gps-map.js', '/static/live-map.js', '/static/media-jobs.js']) {
  assert.ok(scripts.indexOf(later) > first + listed.length - 1, `${later} loads after the parts`);
}
for (const part of listed) {
  const tag = html.match(new RegExp(`<script[^>]*src="/static/${part.replace(/[.]/g, '\\.')}"[^>]*>`))[0];
  assert.doesNotMatch(tag, /\b(?:async|defer|type=)/, `${part} is a plain ordered script`);
}

// Each part is a valid script by itself and ends with a line break (so joining never glues two lines).
const sources = listed.map(part => fs.readFileSync(path.join(STATIC_DIR, part), 'utf8'));
sources.forEach((source, index) => {
  assert.ok(source.endsWith('\n'), `${listed[index]} ends with a line break`);
  assert.ok(!source.includes('\r'), `${listed[index]} has LF line endings`);
  assert.doesNotThrow(() => new vm.Script(source, { filename: listed[index] }), `${listed[index]} parses`);
});
assert.doesNotThrow(() => new vm.Script(readAppSource(), { filename: 'joined' }), 'the joined parts parse');

// Top-level names (declarations that start in column 0) are declared once across all parts.
const declared = new Map();
sources.forEach((source, index) => {
  for (const match of source.matchAll(/^(?:async function|function|const|let|var|class) +([A-Za-z_$][\w$]*)/gm)) {
    assert.ok(!declared.has(match[1]), `${match[1]} is declared in ${declared.get(match[1])} and again in ${listed[index]}`);
    declared.set(match[1], listed[index]);
  }
});
assert.ok(declared.has('state') && declared.has('tick') && declared.has('stopRoverNow'), 'top-level names were found');

// Load order: a function declared in a later part does not exist yet while an earlier part loads.
// Rule kept simple on purpose: code outside top-level function bodies (declarations with initialisers
// and bare statements) may only name functions from the same or an earlier part. Code inside top-level
// function bodies runs after every part has loaded and may name anything.
const partOf = name => listed.indexOf(declared.get(name));
const functionNames = new Set();
sources.forEach(source => { for (const match of source.matchAll(/^(?:async function|function) +([A-Za-z_$][\w$]*)/gm)) functionNames.add(match[1]); });
sources.forEach((source, index) => {
  const outside = source
    .replace(/^(?:async function|function) [\s\S]*?^\}\n/gm, '')   // top-level function bodies
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')       // comments
    .replace(/"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'/g, '""');       // string literals
  for (const match of outside.matchAll(/(?<![.\w$])[A-Za-z_$][\w$]*/g)) {
    const name = match[0];
    if (!functionNames.has(name)) continue;
    assert.ok(partOf(name) <= index, `${listed[index]} names ${name} outside a function, but it is declared later in ${declared.get(name)}`);
  }
});

console.log(`app parts: ${listed.length} files, ${declared.size} top-level names`);
