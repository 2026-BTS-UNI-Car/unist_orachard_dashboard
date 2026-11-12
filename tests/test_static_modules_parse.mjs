// Every ES module the page loads must parse as a module. Other tests run module files as plain scripts in a VM,
// where a duplicate top-level function is allowed; in a browser it is a SyntaxError that stops the whole module
// (this happened once to live-map.js and silently stopped the live map).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const STATIC = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'static');
const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
const modules = new Set();
for (const match of html.matchAll(/<script\b[^>]*\btype="module"[^>]*\bsrc="\/static\/([^"?]+)"/g)) modules.add(match[1]);
for (const match of html.matchAll(/<script\b[^>]*\bsrc="\/static\/([^"?]+)"[^>]*\btype="module"/g)) modules.add(match[1]);
const walk = dir => fs.readdirSync(dir, {withFileTypes: true}).flatMap(entry => {
  const full = path.join(dir, entry.name);
  if (entry.isDirectory()) return entry.name === 'vendor' ? [] : walk(full);
  return entry.name.endsWith('.mjs') ? [path.relative(STATIC, full).split(path.sep).join('/')] : [];
});
for (const rel of walk(STATIC)) modules.add(rel);
assert.ok(modules.has('live-map.js') && modules.has('gps-map.js'), `module scripts found: ${[...modules].join(', ')}`);
const failures = [];
for (const rel of [...modules].sort()) {
  const result = spawnSync(process.execPath, ['--input-type=module', '--check'], {input: fs.readFileSync(path.join(STATIC, rel))});
  if (result.status !== 0) failures.push(`${rel}: ${String(result.stderr).split('\n').find(line => line.includes('Error')) || result.status}`);
}
assert.deepEqual(failures, [], 'modules that do not parse');
console.log(`static modules parse: ${modules.size} files`);
