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
  // Caption and label 13, body 14, panel title 16, key value 20, page title 24.
  assert.deepEqual(px(light, '--text-'), ['13px', '14px', '16px', '20px', '24px']);
  assert.deepEqual(px(light, '--leading-'), ['18px', '20px', '24px', '28px', '32px'], 'one line height per size');
  assert.deepEqual(px(light, '--weight-'), ['400', '500', '600', '700'], 'regular, medium, strong, bold: the range of the interface font');
  assert.deepEqual(px(light, '--radius-'), ['4px', '8px', '12px', '999px', '50%'], 'small marks, controls, panels, pills, dots');
  assert.deepEqual(px(light, '--tracking-'), ['-0.01em', '-0.02em'], 'headings sit a little tighter');
  assert.deepEqual([light['--page-gutter'], light['--panel-padding']], ['24px', '20px']);
  assert.match(tokens, /@media \(max-width: 1023px\) \{\n  :root \{\n    --page-gutter: 16px;\n    --panel-padding: 16px;/, '16px gutters below the rail layout');
  assert.deepEqual(px(light, '--motion-'), ['120ms', '180ms'], 'hover and press, drawer');
  assert.match(tokens, /:root \{\n  color-scheme: light;/, 'light is the default theme');
}

// Dark redefines every colour of the light theme (and nothing that light does not have).
{
  const colourNames = bag => Object.keys(bag).filter(name => /^--(color|chart|shadow)-/.test(name)).sort();
  const darkOwn = block(':root[data-theme="dark"]');
  assert.deepEqual(colourNames(darkOwn).filter(name => !(name in light)), [], 'dark defines a colour that light lacks');
  const sameInBoth = new Set(['--color-media-bg', '--color-media-text', '--color-media-scrim', '--color-on-stop']);
  // The pictures whose colour is fixed (not masks) are redefined for dark as well.
  for (const name of ['--icon-select', '--range-fill-image']) assert.ok(name in darkOwn && darkOwn[name] !== light[name], `${name} has a dark value`);
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
    ['--color-text', '--color-bg'], ['--color-text', '--color-surface'], ['--color-text', '--color-fill'], ['--color-text', '--color-fill-strong'], ['--color-text', '--color-accent-soft'],
    ['--color-text-secondary', '--color-bg'], ['--color-text-secondary', '--color-surface'], ['--color-text-secondary', '--color-fill'], ['--color-text-secondary', '--color-fill-strong'],
    // Links and selected items: the accent as text, also on its soft tint.
    ['--color-accent-text', '--color-bg'], ['--color-accent-text', '--color-surface'], ['--color-accent-text', '--color-fill'], ['--color-accent-text', '--color-accent-soft'],
    // Solid accent (primary button) in its three states.
    ['--color-on-accent', '--color-accent'], ['--color-on-accent', '--color-accent-hover'], ['--color-on-accent', '--color-accent-active'],
    // Status words on their tint (badges, notices) and on a panel.
    ['--color-ok-text', '--color-ok-soft'], ['--color-ok-text', '--color-surface'],
    ['--color-warning-text', '--color-warning-soft'], ['--color-warning-text', '--color-surface'],
    ['--color-danger-text', '--color-danger-soft'], ['--color-danger-text', '--color-surface'],
    ['--color-on-stop', '--color-stop'], ['--color-on-stop', '--color-stop-hover'], ['--color-media-text', '--color-media-bg'],
  ];
  for (const [name, theme] of [['light', light], ['dark', dark]]) {
    for (const [fg, bg] of pairs) {
      const value = ratio(theme[fg], theme[bg]);
      assert.ok(value >= 4.5, `${name}: ${fg} on ${bg} is ${value.toFixed(2)}:1`);
    }
    // Status marks (dots, strip edges) and control outlines are not text: 3:1 against the surface.
    // The solid accent is such a mark too (switch, slider fill, focus ring).
    for (const mark of ['--color-ok', '--color-warning', '--color-danger', '--color-stop', '--color-border-strong', '--color-accent']) {
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
  const sources = [...systemSheets, ...pageSheets, 'index.html', 'ui-kit.html'];
  const missing = [];
  for (const name of sources) {
    for (const match of read(name).matchAll(/var\(\s*(--[a-z0-9-]+)/g)) {
      if (!(match[1] in light) && !fromScript.has(match[1])) missing.push(`${name}: ${match[1]}`);
    }
  }
  assert.deepEqual([...new Set(missing)], [], 'custom property that tokens.css does not define');
  // And the other way round: a colour or a picture in the token file is used somewhere
  // (a sheet, a page, or a script that reads it for a canvas).
  const users = [...sources, ...fs.readdirSync(path.join(staticDir, 'app')).map(name => `app/${name}`)].map(read).join('\n') + scripts;
  const uses = users.replace(/^\s*--[a-z0-9-]+\s*:/gm, '');
  const unused = Object.keys(light).filter(name => /^--(color|chart|icon|range|shadow)-/.test(name) && !new RegExp(`${name}(?![a-z0-9-])`).test(uses));
  assert.deepEqual(unused, [], 'token that nothing uses');
  // Custom properties are declared in the token file only (a sheet may still set one for its own subtree from script).
  for (const name of [...systemSheets.filter(sheet => sheet !== 'css/tokens.css'), ...pageSheets]) {
    const declared = [...stripComments(read(name)).matchAll(/(?:^|[;{\s])(--[a-z0-9-]+)\s*:/g)].map(match => match[1])
      .filter(variable => variable !== '--banner-height');
    assert.deepEqual(declared, [], `${name} declares a custom property; put it in css/tokens.css`);
  }
}

// No sheet but the token file holds raw values, and no sheet carries decoration.
// (Vendored sheets under static/vendor are not ours and are not read here.)
{
  for (const name of [...systemSheets.filter(sheet => sheet !== 'css/tokens.css'), ...pageSheets]) {
    const css = stripComments(read(name));
    assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, `${name}: colour outside the token file`);
    assert.doesNotMatch(css, /\brgba?\(|\bhsla?\(|color-mix\(/, `${name}: colour outside the token file`);
    assert.doesNotMatch(css, /gradient\(/, `${name}: gradient`);
    assert.doesNotMatch(css, /backdrop-filter|\bfilter\s*:|text-shadow/, `${name}: blur, filter or text shadow`);
    assert.doesNotMatch(css, /text-transform\s*:\s*uppercase/, `${name}: upper-case label`);
    for (const match of css.matchAll(/box-shadow\s*:\s*([^;]+);/g)) assert.equal(match[1].trim(), 'var(--shadow-overlay)', `${name}: one shadow, for overlays`);
    for (const match of css.matchAll(/font-weight\s*:\s*([^;]+);/g)) assert.match(match[1].trim(), /^var\(--weight-(regular|medium|strong|bold)\)$/, `${name}: font-weight ${match[1]}`);
    for (const match of css.matchAll(/font-size\s*:\s*([^;]+);/g)) assert.match(match[1].trim(), /^var\(--text-(xs|sm|md|lg|xl)\)$/, `${name}: font-size ${match[1]}`);
    for (const match of css.matchAll(/border-radius\s*:\s*([^;]+);/g)) assert.match(match[1].trim(), /^(var\(--radius-(small|control|panel|pill|round)\)|0)$/, `${name}: border-radius ${match[1]}`);
    // Motion: colour changes, the switch and the drawer only, with the two durations of the token file.
    for (const match of css.matchAll(/transition\s*:\s*([^;]+);/g)) {
      for (const part of match[1].split(/,(?![^(]*\))/)) assert.match(part.trim(), /^[a-z-]+ (var\(--motion-(fast|base)\) var\(--ease-out\)|0s( linear var\(--motion-base\))?)$/, `${name}: transition ${part.trim()}`);
    }
    for (const match of css.matchAll(/animation\s*:\s*([^;]+);/g)) assert.match(match[1].trim(), /^(skeleton-pulse |none$)/, `${name}: animation ${match[1]} (only the loading skeleton moves by itself)`);
    assert.doesNotMatch(css, /url\(/i, `${name}: url() (pictures are tokens: var(--icon-...))`);
  }
  for (const name of [...systemSheets, ...pageSheets]) assert.doesNotMatch(stripComments(read(name)), /@import/, `${name}: @import`);
  // All our CSS together stays small: it is on the first load of every route. The unicode-range list of the
  // interface font is generated data (it saves about 220 kB of font on a first visit) and has its own limit.
  {
    const total = [...systemSheets, ...pageSheets].reduce((sum, name) => sum + fs.statSync(path.join(staticDir, name)).size, 0);
    const ranges = [...read('css/tokens.css').matchAll(/unicode-range:\s*([^;]*);/g)].reduce((sum, match) => sum + match[1].length, 0);
    assert.ok(ranges <= 6 * 1024, `font unicode-range lists are ${ranges} bytes (limit 6 kB)`);
    assert.ok(total - ranges <= 100 * 1024, `style sheets are ${total - ranges} bytes together without the font ranges (limit 100 kB)`);
  }
  // Solid red is STOP only: the status bar STOP, the drive panel STOP and the pad STOP. A danger button is tinted.
  {
    const all = [...systemSheets, ...pageSheets].map(name => stripComments(read(name))).join('\n');
    const solid = [...all.matchAll(/([^{}]+)\{[^{}]*background(?:-color)?:\s*var\(--color-(?:stop|danger)\);/g)].map(match => match[1].trim().split(/\s*,\s*/).pop());
    assert.deepEqual(solid.sort(), ['.drive-head-actions .action-button.danger', '.rover-pad button[data-rover-drive="stop"]', '.status-dot.bad', '.stop-button'],
      'rules with a solid red background: the three STOP buttons and the 8px status dot');
    const danger = read('css/components.css').match(/\.btn-danger,\n\.action-button\.danger \{([^}]*)\}/);
    assert.ok(danger, 'danger button rule');
    assert.match(danger[1], /background: var\(--color-danger-soft\);/); assert.match(danger[1], /color: var\(--color-danger-text\);/);
  }
  // Focus ring and reduced motion exist once, for everything.
  const base = read('css/base.css');
  assert.match(base, /:focus-visible \{\n  outline: 2px solid var\(--color-accent\);/);
  assert.match(base, /@media \(prefers-reduced-motion: reduce\)/);
}

// Interface font: one family in two files, declared in tokens.css, never blocking the text. The first load
// has to stay usable on a slow link, so the text shows in a device font until the file has arrived, and a
// first visit downloads only the small file (the characters the screens use, tools/fonts/split_ui_font.py).
// The second file holds the other Hangul syllables and is fetched only when such a syllable is shown.
{
  const crypto = require('node:crypto');
  const source = JSON.parse(read('vendor/ubts-sans/ubts-sans-var.json'));
  const split = JSON.parse(read('vendor/ubts-sans/ubts-sans-split.json'));
  assert.equal(source.hangul_syllables, 2350);
  assert.equal(split.source_sha256, source.sha256, 'the split was made from the recorded source font');
  assert.equal(split.ui.hangul_syllables + split.ko.hangul_syllables, 2350, 'every syllable is in exactly one file');
  for (const part of [split.ui, split.ko]) {
    const bytes = fs.readFileSync(path.join(staticDir, 'vendor/ubts-sans', part.file));
    assert.equal(bytes.length, part.bytes, `${part.file} size equals its record`);
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), part.sha256, `${part.file} hash equals its record`);
    assert.equal(bytes.subarray(0, 4).toString('latin1'), 'wOF2', `${part.file} is WOFF2`);
  }
  assert.ok(split.ui.bytes <= 100 * 1024, `first font file is ${split.ui.bytes} bytes (limit 100 kB)`);
  assert.ok(fs.existsSync(path.join(staticDir, 'vendor/ubts-sans/PRETENDARD-OFL.txt')), 'licence text is shipped with the font');
  const fontFiles = [`/static/vendor/ubts-sans/${split.ko.file}`, `/static/vendor/ubts-sans/${split.ui.file}`];
  for (const name of systemSheets) {
    const css = stripComments(read(name));
    const faces = [...css.matchAll(/@font-face\s*\{([^}]*)\}/g)].map(match => match[1]);
    if (name !== 'css/tokens.css') {
      assert.equal(faces.length, 0, `${name}: @font-face belongs in tokens.css`);
      assert.doesNotMatch(css, /url\(/i, `${name}: url() (no font, image or icon file; draw icons with CSS or inline SVG)`);
      continue;
    }
    assert.equal(faces.length, 2, 'two @font-face rules, one family');
    for (const face of faces) {
      assert.match(face, /font-family:\s*"UBTS Sans"/);
      assert.match(face, /font-display:\s*swap/, 'text is shown before the font arrives');
      assert.match(face, /font-weight:\s*400 700/);
    }
    // Where ranges overlap the face written last is tried first: the small file must come last.
    assert.match(faces[0], new RegExp(`url\\("${fontFiles[0]}"\\)`)); assert.match(faces[0], /unicode-range:\s*U\+AC00-D7A3;/);
    assert.match(faces[1], new RegExp(`url\\("${fontFiles[1]}"\\)`)); assert.match(faces[1], /unicode-range:\s*U\+20-7E, /);
    // The only files are the interface font. The other url() values are small inline SVG pictures (a notice
    // icon, the select arrow, the slider fill): data, not requests, each a named token of at most 1 kB.
    const urls = [...css.matchAll(/url\("([^"]*)"\)/g)].map(match => match[1]);
    assert.equal(urls.length, (css.match(/url\(/g) || []).length, 'every url() is double-quoted');
    assert.deepEqual(urls.filter(url => !url.startsWith('data:')), fontFiles, 'the only file url() values are the interface font');
    const pictures = urls.filter(url => url.startsWith('data:'));
    assert.ok(pictures.length >= 8, 'inline pictures');
    for (const url of pictures) {
      assert.match(url, /^data:image\/svg\+xml,%3Csvg xmlns='http:\/\/www\.w3\.org\/2000\/svg' /, `inline picture is SVG: ${url.slice(0, 40)}`);
      assert.ok(url.length <= 1024, `inline picture is ${url.length} bytes (limit 1 kB)`);
      assert.doesNotMatch(url.slice(60), /[()<>;]|href|url|script|http/i, 'an inline picture refers to nothing');
    }
    const named = [...css.matchAll(/(--[a-z0-9-]+):\s*url\("data:/g)].map(match => match[1]);
    assert.equal(named.length, pictures.length, 'every inline picture is a custom property');
    for (const name of named) assert.match(name, /^--(icon-[a-z]+|range-fill-image)$/, name);
  }
  // The font tokens name the interface font first, then installed fonts, and end in a generic family.
  const families = value => value.split(',').map(item => item.trim());
  const sans = families(light['--font-sans']), mono = families(light['--font-mono']);
  assert.deepEqual([sans[0], sans[1], sans.at(-1)], ['"UBTS Sans"', 'system-ui', 'sans-serif'], 'device fonts follow the interface font');
  assert.equal(mono.at(-1), 'monospace');
  for (const korean of ['"Apple SD Gothic Neo"', '"Malgun Gothic"', '"Noto Sans KR"']) assert.ok(sans.includes(korean), `${korean} in --font-sans`);
  for (const name of [...systemSheets, ...pageSheets]) {
    for (const match of stripComments(read(name)).replace(/@font-face\s*\{[^}]*\}/g, '').matchAll(/font-family\s*:\s*([^;}]+)/g)) {
      const value = match[1].trim();
      assert.match(value, /^(inherit|var\(--font-(sans|mono)\))$/, `${name}: font-family ${value} (use the font tokens)`);
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
    '/static/vendor/maplibre-gl/maplibre-gl.css',
    '/static/css/tokens.css', '/static/css/base.css', '/static/css/components.css', '/static/css/frame.css', '/static/styles.css',
    '/static/archive-video.js',
    '/static/app/00-config-state.js', '/static/app/01-format-http.js', '/static/app/02-shell.js',
    '/static/app/03-sensor-settings.js', '/static/app/04-telemetry.js', '/static/app/05-live-lidar-camera.js',
    '/static/app/06-live-imu.js', '/static/app/07-lidar-data.js', '/static/app/08-system-panels.js',
    '/static/app/09-docs.js', '/static/app/10-rover-control.js', '/static/app/11-recording.js',
    '/static/app/12-archive.js', '/static/app/13-charts-canvas.js', '/static/app/99-main.js',
    '/static/field-preparation.js', '/static/vendor/pmtiles/pmtiles.js',
    '/static/gps-map.js', '/static/live-map.js', '/static/media-jobs.js',
  ], 'files that index.html loads');
  // One page sheet: the map, preparation and media job rules live in styles.css (four requests fewer).
  assert.deepEqual(pageSheets, ['styles.css'], 'page style sheets');
  // The markup itself brings no picture: an <img> gets its source from a live stream or a recording.
  for (const match of html.matchAll(/<img\b[^>]*>/g)) assert.doesNotMatch(match[0], /\bsrc=/, `image loaded with the page: ${match[0]}`);
}

// The check page shows every component and is not part of the navigation.
{
  const kit = read('ui-kit.html');
  for (const sheet of ['tokens', 'base', 'components', 'frame']) assert.ok(kit.includes(`href="/static/css/${sheet}.css"`), sheet);
  assert.ok(kit.indexOf('css/tokens.css') < kit.indexOf('css/base.css') && kit.indexOf('css/base.css') < kit.indexOf('css/components.css'), 'tokens are loaded first');
  for (const cls of ['btn btn-primary', 'btn btn-danger', 'btn btn-quiet', 'btn btn-hold', 'stop-button', 'toggle', 'toggle-track', 'slider', 'check', 'select', 'input',
    'tabs', 'tab', 'segmented', 'btn-group', 'status-chip ok', 'status-chip warn', 'status-chip bad', 'status-chip info', 'status-dot', 'statusbar', 'sidebar', 'nav-list',
    'page-head', 'page-note', 'status-strip', 'status-strip-item', 'panel', 'panel-head', 'panel-actions', 'panel-section', 'kv-list', 'table', 'num',
    'notice', 'notice info', 'notice ok', 'notice warn', 'notice bad', 'empty-state', 'skeleton', 'progress', 'dialog', 'toast', 'icon']) {
    assert.ok(new RegExp(`class="[^"]*\\b${cls.replace(/ /g, '\\b[^"]*\\b')}\\b`).test(kit), `ui-kit.html shows .${cls}`);
  }
  // Disabled, switched-on, selected and held states are shown as well.
  for (const state of ['disabled=""', 'aria-pressed="true"', 'aria-selected="true"', 'btn btn-hold active', 'checked=""']) assert.ok(kit.includes(state), `ui-kit.html shows ${state}`);
  const components = read('css/base.css') + read('css/components.css') + read('css/frame.css');
  for (const cls of ['btn', 'btn-primary', 'btn-danger', 'btn-quiet', 'btn-hold', 'toggle', 'toggle-track', 'slider', 'check', 'select', 'input', 'tabs', 'tab', 'segmented', 'btn-group',
    'status-chip', 'status-dot', 'panel', 'panel-head', 'kv-list', 'table', 'notice', 'empty-state', 'skeleton', 'progress', 'dialog', 'toast', 'icon',
    'statusbar', 'stop-button', 'sidebar', 'nav-list', 'page-head', 'page-note', 'status-strip']) {
    assert.match(components, new RegExp(`\\.${cls}\\b[^{}]*\\{`), `.${cls} has a rule`);
  }
  // Controls are drawn by the sheets, the same in every browser: no native select arrow, slider or check box.
  for (const selector of ['.select,\nselect', '.slider,\ninput[type="range"]', '.check > input,\n.switch-control > input']) {
    const start = components.indexOf(`${selector} {`);
    assert.ok(start >= 0, selector);
    assert.ok(components.slice(start, components.indexOf('}', start)).includes('\n  appearance: none;'), `${selector.split(',')[0]} is drawn by the sheet`);
  }
  for (const part of ['::-webkit-slider-runnable-track', '::-webkit-slider-thumb', '::-moz-range-track', '::-moz-range-progress', '::-moz-range-thumb']) {
    assert.ok(components.includes(`input[type="range"]${part} {`), `slider ${part}`);
  }
  assert.doesNotMatch(components, /accent-color/, 'no browser-drawn control colour');
  // Hover styles are for devices that hover; a notice has no coloured side stripe.
  assert.match(components, /@media \(hover: hover\) \{\n  \.btn:not\(:disabled\):hover,/);
  assert.doesNotMatch(read('css/components.css'), /border-left:\s*\d+px solid var\(--color-(ok|warning|danger|accent)/, 'no coloured side stripe');
  assert.doesNotMatch(read('index.html'), /ui-kit/, 'the check page is not linked from the dashboard');
}

// Icons: one set of line symbols inside the page (no icon font, no image file, no request). Every icon
// that is used exists, every symbol is used, and the check page carries the same symbols.
{
  const sprite = html => { const match = html.match(/<svg aria-hidden="true" class="icon-sprite"[^>]*>[\s\S]*?<\/svg>/); assert.ok(match, 'icon symbols'); return match[0]; };
  const index = read('index.html'), kit = read('ui-kit.html');
  assert.equal(sprite(kit), sprite(index), 'ui-kit.html has the symbols of index.html');
  assert.ok(index.indexOf('class="icon-sprite"') < index.indexOf('<header class="statusbar"'), 'the symbols come first in the body');
  const symbols = [...sprite(index).matchAll(/<symbol id="(i-[a-z-]+)" viewBox="0 0 24 24">/g)].map(match => match[1]);
  assert.ok(symbols.length >= 12 && new Set(symbols).size === symbols.length, 'symbols with unique names on a 24px grid');
  assert.equal((sprite(index).match(/<symbol /g) || []).length, symbols.length, 'every symbol has an i- name and the 24px grid');
  assert.doesNotMatch(sprite(index), /fill=|stroke=|style=|<image|href=/, 'symbols carry shapes only; .icon gives the stroke');
  const used = html => [...html.matchAll(/<svg aria-hidden="true" class="icon[^"]*"><use href="#([^"]+)"\/><\/svg>/g)].map(match => match[1]);
  assert.equal((index.match(/<use /g) || []).length, used(index).length, 'every icon is written the same way and hidden from assistive technology');
  for (const name of [...used(index), ...used(kit)]) assert.ok(symbols.includes(name), `icon ${name} has no symbol`);
  for (const name of symbols) assert.ok(used(index).includes(name), `symbol ${name} is not used by the dashboard`);
  assert.ok(sprite(index).length <= 4096, `icon symbols are ${sprite(index).length} bytes (limit 4 kB)`);
  const icon = read('css/base.css').match(/\n\.icon \{([^}]*)\}/);
  assert.ok(icon); assert.match(icon[1], /stroke: currentColor;/); assert.match(icon[1], /stroke-width: 1\.75;/); assert.match(icon[1], /stroke-linecap: round;/);
}

// The old variable names are gone from the sheets, the pages and the scripts.
{
  const scripts = fs.readdirSync(staticDir).filter(name => /\.(js|mjs)$/.test(name));
  for (const name of [...systemSheets, ...pageSheets, 'index.html', ...scripts]) {
    const found = read(name).match(/--(?:bg|bg-2|surface|surface-2|panel|line|line-strong|text|muted|dim|blue|cyan|violet|rose|amber|red|good|shadow|accent|chart-(?:blue|cyan|rose|amber|good|bg-top|bg-bottom))(?![\w-])/g);
    assert.equal(found, null, `${name}: old style variable ${found}`);
  }
  // Banner, card and upper-case label classes of the old look have no rule and no markup left.
  const everything = [...systemSheets, ...pageSheets, 'index.html'].map(read).join('\n');
  for (const gone of ['eyebrow', 'hero-copy', 'kpi-grid', 'logo-badge', 'theme-track', 'status-pill', 'mobile-topbar', 'brand-block']) {
    assert.ok(!everything.includes(gone), `${gone} is still referenced`);
  }
}

console.log('PASS: token scales, both themes with text contrast, defined custom properties, no raw values or decoration in any sheet, the interface font in two files and a fixed request list, icons, check page');
