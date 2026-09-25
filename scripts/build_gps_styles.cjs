// Rebuild local styles from the pinned, vendored Protomaps package.
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..', 'static');
const vm = require('node:vm');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(root, 'vendor/protomaps_basemaps/basemaps.js'), 'utf8'), context);
const basemaps = context.basemaps;
for (const theme of ['light', 'dark']) {
  const style = {
    version: 8, glyphs: '/maps/fonts/{fontstack}/{range}.pbf', sprite: `/maps/sprites/v4/${theme}`,
    sources: {basemap: {type: 'vector', url: 'pmtiles:///maps/korea.pmtiles',
      attribution: '<a href="https://www.openstreetmap.org/copyright">&copy; OpenStreetMap contributors</a> · <a href="https://protomaps.com">Protomaps</a>'}},
    layers: basemaps.layers('basemap', basemaps.namedFlavor(theme), {lang: 'ko'}),
  };
  fs.writeFileSync(path.join(root, `gps-style-${theme}.json`), JSON.stringify(style));
}
