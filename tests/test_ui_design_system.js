// Design system guards: the token file is the only place for raw values, both
// themes define the same colours with readable contrast, the new style sheets
// carry no decoration, and nothing is loaded from outside the dashboard.
// Reads the shipped files only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const staticDir = path.join(__dirname, '../static');
const read = name => fs.readFileSync(path.join(staticDir, name), 'utf8').replace(/\r\n/g, '\n');
const stripComments = css => css.replace(/\/\*[\s\S]*?\*\//g, '');
const tokens = read('css/tokens.css');
const systemSheets = fs.readdirSync(path.join(staticDir, 'css')).filter(name => name.endsWith('.css')).map(name => `css/${name}`);
const pageSheets = fs.readdirSync(staticDir).filter(name => name.endsWith('.css'));

// A block of the token file: ':root {' (light, default) or ':root[data-theme="dark"] {'.
function block(selector) {
  const start = tokens.indexOf(`${selector} {`);
  assert.ok(start >= 0, selector);
  const end = tokens.indexOf('\n}', start);
  return Object.fromEntries([...stripComments(tokens.slice(start, end)).matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)].map(match => [match[1], match[2].trim()]));
}
const light = block(':root'), dark = {...light, ...block(':root[data-theme="dark"]')};

// The scales that were decided.
{
  const px = (bag, prefix) => Object.keys(bag).filter(name => name.startsWith(prefix)).map(name => bag[name]);
  assert.deepEqual(px(light, '--space-'), ['4px', '8px', '12px', '16px', '24px', '32px']);
  assert.deepEqual(px(light, '--text-'), ['12px', '14px', '16px', '20px', '28px']);
  assert.deepEqual(px(light, '--leading-'), ['16px', '20px', '24px', '28px', '36px'], 'one line height per size');
  assert.deepEqual(px(light, '--weight-'), ['400', '600']);
  assert.deepEqual([light['--radius-control'], light['--radius-panel'], light['--radius-round']], ['4px', '8px', '50%']);
  assert.match(tokens, /:root \{\n  color-scheme: light;/, 'light is the default theme');
}

// Dark redefines every colour of the light theme (and nothing that light does not have).
{
  const colourNames = bag => Object.keys(bag).filter(name => /^--(color|chart|shadow)-/.test(name)).sort();
  const darkOwn = block(':root[data-theme="dark"]');
  assert.deepEqual(colourNames(darkOwn).filter(name => !(name in light)), [], 'dark defines a colour that light lacks');
  const sameInBoth = new Set(['--color-media-bg', '--color-media-text', '--color-media-scrim', '--color-on-stop']);
  assert.deepEqual(colourNames(light).filter(name => !(name in darkOwn) && !sameInBoth.has(name)), [], 'light colour without a dark value');
}

// Text contrast (WCAG 2 relative luminance): body text pairs reach 4.5:1 in both themes.
{
  const luminance = hex => {
    assert.match(hex, /^#[0-9a-f]{6}$/, hex);
    const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255).map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const ratio = (a, b) => { const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
  const pairs = [
    ['--color-text', '--color-bg'], ['--color-text', '--color-surface'], ['--color-text', '--color-fill'], ['--color-text', '--color-accent-soft'],
    ['--color-text-secondary', '--color-bg'], ['--color-text-secondary', '--color-surface'], ['--color-text-secondary', '--color-fill'],
    ['--color-accent', '--color-bg'], ['--color-accent', '--color-surface'], ['--color-accent', '--color-fill'], ['--color-accent', '--color-accent-soft'],
    ['--color-on-accent', '--color-accent'], ['--color-on-danger', '--color-danger'], ['--color-on-warning', '--color-warning'],
    ['--color-on-stop', '--color-stop'], ['--color-media-text', '--color-media-bg'],
  ];
  for (const [name, theme] of [['light', light], ['dark', dark]]) {
    for (const [fg, bg] of pairs) {
      const value = ratio(theme[fg], theme[bg]);
      assert.ok(value >= 4.5, `${name}: ${fg} on ${bg} is ${value.toFixed(2)}:1`);
    }
    // Status marks (dots, strip edges) and control outlines are not text: 3:1 against the surface.
    for (const mark of ['--color-ok', '--color-warning', '--color-danger', '--color-stop', '--color-border-strong']) {
      const value = ratio(theme[mark], theme['--color-surface']);
      assert.ok(value >= 3, `${name}: ${mark} on the surface is ${value.toFixed(2)}:1`);
    }
  }
}

// Every custom property that a style sheet or a page uses is defined in the token file.
// Properties that a script sets at run time (setProperty) are the only exception.
{
  const scripts = fs.readdirSync(staticDir).filter(name => /\.(js|mjs)$/.test(name)).map(read).join('\n');
  const fromScript = new Set([...scripts.matchAll(/setProperty\(\s*['"](--[a-z0-9-]+)['"]/g)].map(match => match[1]));
  const sources = [...systemSheets, 'ui-kit.html'];
  const missing = [];
  for (const name of sources) {
    for (const match of read(name).matchAll(/var\(\s*(--[a-z0-9-]+)/g)) {
      if (!(match[1] in light) && !fromScript.has(match[1])) missing.push(`${name}: ${match[1]}`);
    }
  }
  assert.deepEqual([...new Set(missing)], [], 'custom property that tokens.css does not define');
  // Custom properties are declared in the token file only (a sheet may still set one for its own subtree from script).
  for (const name of systemSheets.filter(sheet => sheet !== 'css/tokens.css')) {
    const declared = [...stripComments(read(name)).matchAll(/(?:^|[;{\s])(--[a-z0-9-]+)\s*:/g)].map(match => match[1])
      .filter(variable => variable !== '--banner-height');
    assert.deepEqual(declared, [], `${name} declares a custom property; put it in css/tokens.css`);
  }
}

// The design system sheets hold no raw values and no decoration.
{
  for (const name of systemSheets.filter(sheet => sheet !== 'css/tokens.css')) {
    const css = stripComments(read(name));
    assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, `${name}: colour outside the token file`);
    assert.doesNotMatch(css, /\brgba?\(|\bhsla?\(|color-mix\(/, `${name}: colour outside the token file`);
    assert.doesNotMatch(css, /gradient\(/, `${name}: gradient`);
    assert.doesNotMatch(css, /backdrop-filter|\bfilter\s*:|text-shadow/, `${name}: blur, filter or text shadow`);
    assert.doesNotMatch(css, /text-transform\s*:\s*uppercase/, `${name}: upper-case label`);
    for (const match of css.matchAll(/box-shadow\s*:\s*([^;]+);/g)) assert.equal(match[1].trim(), 'var(--shadow-overlay)', `${name}: one shadow, for overlays`);
    for (const match of css.matchAll(/font-weight\s*:\s*([^;]+);/g)) assert.match(match[1].trim(), /^var\(--weight-(regular|strong)\)$/, `${name}: font-weight ${match[1]}`);
    for (const match of css.matchAll(/font-size\s*:\s*([^;]+);/g)) assert.match(match[1].trim(), /^var\(--text-(xs|sm|md|lg|xl)\)$/, `${name}: font-size ${match[1]}`);
    for (const match of css.matchAll(/border-radius\s*:\s*([^;]+);/g)) assert.match(match[1].trim(), /^(var\(--radius-(control|panel|round)\)|2px)$/, `${name}: border-radius ${match[1]}`);
  }
  for (const name of [...systemSheets, ...pageSheets]) {
    const css = stripComments(read(name));
    assert.doesNotMatch(css, /@import/, `${name}: @import`);
    for (const match of css.matchAll(/url\(\s*['"]?([^'")]+)['"]?\s*\)/g)) assert.match(match[1], /^\/static\//, `${name}: url(${match[1]}) is not a dashboard file`);
  }
  // Focus ring and reduced motion exist once, for everything.
  const base = read('css/base.css');
  assert.match(base, /:focus-visible \{\n  outline: 2px solid var\(--color-accent\);/);
  assert.match(base, /@media \(prefers-reduced-motion: reduce\)/);
}

// First-load weight (the dashboard is used over a slow link): the interface
// loads no font file and no image; text uses the fonts of the device.
{
  for (const name of systemSheets) {
    const css = read(name);
    assert.doesNotMatch(css, /@font-face/i, `${name}: @font-face (the interface must not load a font file)`);
    assert.doesNotMatch(css, /\.(woff2?|ttf|otf|eot)\b/i, `${name}: reference to a font file`);
    assert.doesNotMatch(stripComments(css), /url\(/i, `${name}: url() (no font, image or icon file; draw icons with CSS or inline SVG)`);
  }
  // The font tokens are stacks of installed fonts that end in a generic family.
  const families = value => value.split(',').map(item => item.trim());
  const sans = families(light['--font-sans']), mono = families(light['--font-mono']);
  assert.deepEqual([sans[0], sans.at(-1)], ['system-ui', 'sans-serif']);
  assert.equal(mono.at(-1), 'monospace');
  for (const korean of ['"Apple SD Gothic Neo"', '"Malgun Gothic"', '"Noto Sans KR"']) assert.ok(sans.includes(korean), `${korean} in --font-sans`);
  for (const name of [...systemSheets, ...pageSheets]) {
    for (const match of stripComments(read(name)).matchAll(/font-family\s*:\s*([^;}]+)/g)) {
      const value = match[1].trim();
      assert.ok(/^(inherit|var\(--font-(sans|mono)\))$/.test(value) || !systemSheets.includes(name), `${name}: font-family ${value} (use the font tokens)`);
    }
  }
  assert.match(read('css/base.css'), /font-variant-numeric: tabular-nums;/, 'read-outs keep their width while digits change');
}

// Pages load nothing from outside the dashboard (it must work from the vehicle's own access point),
// and the dashboard page requests exactly these files when it opens: adding a request needs a reason.
{
  for (const name of ['index.html', 'ui-kit.html']) {
    const html = read(name);
    for (const match of html.matchAll(/\b(?:href|src)="([^"]*)"/g)) {
      assert.doesNotMatch(match[1], /^(?:[a-z][a-z0-9+.-]*:)?\/\//i, `${name}: external resource ${match[1]}`);
    }
    assert.doesNotMatch(html, /rel="preload"|rel="prefetch"|@font-face/, `${name}: preload or font`);
  }
  const html = read('index.html');
  const loaded = [...html.matchAll(/<(?:link|script)\b[^>]*\b(?:href|src)="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(loaded, [
    '/static/favicon.ico', '/static/assets/favicon.png', '/static/assets/apple-touch-icon.png',
    '/static/css/tokens.css', '/static/css/base.css', '/static/css/components.css', '/static/css/frame.css',
    '/static/styles.css', '/static/vendor/maplibre-gl/maplibre-gl.css', '/static/gps-map.css', '/static/live-map.css',
    '/static/vendor/hls/hls.min.js', '/static/archive-video.js', '/static/app.js',
    '/static/field-preparation.css', '/static/field-preparation.js', '/static/vendor/pmtiles/pmtiles.js',
    '/static/gps-map.js', '/static/live-map.js', '/static/media-jobs.css', '/static/media-jobs.js',
  ], 'files that index.html loads');
  // The markup itself brings no picture: an <img> gets its source from a live stream or a recording.
  for (const match of html.matchAll(/<img\b[^>]*>/g)) assert.doesNotMatch(match[0], /\bsrc=/, `image loaded with the page: ${match[0]}`);
}

// The check page shows every component and is not part of the navigation.
{
  const kit = read('ui-kit.html');
  for (const sheet of ['tokens', 'base', 'components', 'frame']) assert.ok(kit.includes(`href="/static/css/${sheet}.css"`), sheet);
  assert.ok(kit.indexOf('css/tokens.css') < kit.indexOf('css/base.css') && kit.indexOf('css/base.css') < kit.indexOf('css/components.css'), 'tokens are loaded first');
  for (const cls of ['btn btn-primary', 'btn btn-danger', 'btn btn-quiet', 'btn btn-hold', 'stop-button', 'toggle', 'toggle-track', 'slider', 'check', 'select', 'input',
    'tabs', 'tab', 'segmented', 'status-chip ok', 'status-chip warn', 'status-chip bad', 'statusbar', 'page-head', 'status-strip', 'status-strip-item',
    'panel', 'panel-head', 'panel-section', 'kv-list', 'table', 'notice', 'notice ok', 'notice warn', 'notice bad', 'empty-state', 'progress', 'dialog']) {
    assert.ok(new RegExp(`class="[^"]*\\b${cls.replace(/ /g, '\\b[^"]*\\b')}\\b`).test(kit), `ui-kit.html shows .${cls}`);
  }
  const components = read('css/components.css') + read('css/frame.css');
  for (const cls of ['btn', 'btn-primary', 'btn-danger', 'btn-quiet', 'btn-hold', 'toggle', 'slider', 'select', 'input', 'tabs', 'tab', 'status-chip',
    'panel', 'panel-head', 'kv-list', 'table', 'notice', 'empty-state', 'dialog', 'statusbar', 'stop-button', 'page-head', 'status-strip']) {
    assert.match(components, new RegExp(`\\.${cls}\\b[^{}]*\\{`), `.${cls} has a rule`);
  }
  assert.doesNotMatch(read('index.html'), /ui-kit/, 'the check page is not linked from the dashboard');
}

console.log('PASS: token scales, both themes with text contrast, defined custom properties, no raw values or decoration in the design system, no external resources, check page');
