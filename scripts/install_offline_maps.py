#!/usr/bin/env python3
"""Install attributed Korea PMTiles and local glyphs. Requires the official pmtiles CLI."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ASSETS_REV = '028c18f713baecad011301ff7a69acc39bcc2ae7'
BOUNDS = [124.0, 32.8, 132.1, 38.9]


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''): digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pmtiles', default='pmtiles')
    parser.add_argument('--date', default='20260925')
    parser.add_argument('--output', type=Path, default=ROOT/'data/maps')
    parser.add_argument('--existing-map', action='store_true', help='Verify an already extracted map instead of downloading')
    args = parser.parse_args()
    if len(args.date) != 8 or not args.date.isdigit(): parser.error('date must be YYYYMMDD')
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    source = f'https://build.protomaps.com/{args.date}.pmtiles'
    target = output/'korea.pmtiles'
    if not args.existing_map:
        if target.exists(): parser.error('Map already exists; use --existing-map or install into a new directory')
        partial = output/'korea.partial.pmtiles'
        subprocess.run([args.pmtiles, 'extract', source, str(partial), '--bbox='+','.join(map(str, BOUNDS)), '--maxzoom=15'], check=True)
        subprocess.run([args.pmtiles, 'verify', str(partial)], check=True)
        partial.replace(target)
    subprocess.run([args.pmtiles, 'verify', str(target)], check=True)
    header = json.loads(subprocess.check_output([args.pmtiles, 'show', str(target), '--header-json']))
    metadata = json.loads(subprocess.check_output([args.pmtiles, 'show', str(target), '--metadata']))
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary)/'assets.zip'
        urllib.request.urlretrieve(f'https://github.com/protomaps/basemaps-assets/archive/{ASSETS_REV}.zip', archive)
        with zipfile.ZipFile(archive) as bundle:
            for entry in bundle.infolist():
                parts = Path(entry.filename).parts[1:]
                if entry.is_dir() or not parts or parts[0] not in ('fonts', 'sprites'): continue
                destination = output.joinpath(*parts).resolve()
                if not destination.is_relative_to(output): raise ValueError('Invalid asset path')
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as src, destination.open('wb') as dst: shutil.copyfileobj(src, dst)
    for name in ('korean.woff2', 'KOREAN-OFL.txt'):
        shutil.copy2(ROOT/'static/vendor/korean-font'/name, output/'fonts'/name)
    manifest = dict(schema='orchard.offline_maps.v1', source=source, data_date=f'{args.date[:4]}-{args.date[4:6]}-{args.date[6:]}',
                    bounds=BOUNDS, minzoom=0, maxzoom=15, bytes=target.stat().st_size, sha256=sha256(target),
                    assets_revision=ASSETS_REV, header=header, metadata=metadata,
                    attribution='© OpenStreetMap contributors · Protomaps', license='ODbL 1.0',
                    license_url='https://www.openstreetmap.org/copyright')
    temporary = output/'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(output/'manifest.json')
    print(json.dumps({k: manifest[k] for k in ('data_date', 'bytes', 'sha256', 'bounds')}, ensure_ascii=False))


if __name__ == '__main__': main()
