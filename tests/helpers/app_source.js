'use strict';
// The page script is split into ordered parts under static/app/. index.html loads them one after
// another as classic scripts, so together they behave like one file. Tests that cut pieces out of
// the page script read the parts joined in that order.
const fs = require('node:fs');
const path = require('node:path');

const STATIC_DIR = path.join(__dirname, '..', '..', 'static');
const PART_TAG = /<script src="\/static\/(app\/[A-Za-z0-9._-]+\.js)"><\/script>/g;

// Paths of the parts relative to static/, in load order.
function appPartPaths(indexHtml = fs.readFileSync(path.join(STATIC_DIR, 'index.html'), 'utf8')) {
  return [...indexHtml.matchAll(PART_TAG)].map(match => match[1]);
}

function readAppSource() {
  const parts = appPartPaths();
  if (parts.length === 0) throw new Error('index.html lists no /static/app/*.js script');
  return parts.map(part => fs.readFileSync(path.join(STATIC_DIR, part), 'utf8')).join('');
}

module.exports = { STATIC_DIR, appPartPaths, readAppSource };
