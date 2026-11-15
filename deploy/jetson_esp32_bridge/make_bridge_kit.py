#!/usr/bin/env python3
"""Build the ESP32-S3 bridge kit from one commit of this repository (run on the PC).

  python deploy/jetson_esp32_bridge/make_bridge_kit.py --commit <commit> --out <folder outside the repository>

Files are read with 'git cat-file' from the commit (the bytes stored there, LF), never from the work tree. The kit:
  ubts-esp32-bridge-kit-<commit 12>/
    install_esp32_bridge.py, rollback_esp32_bridge.py, README_KO.md   (from the same commit)
    payload/scripts/...                                                 (the bridge files the image lacks)
    MANIFEST.json   (schema, commit, file SHA-256)
    SHA256SUMS
  ubts-esp32-bridge-kit-<commit 12>.tar.gz   (the same folder; the same commit gives the same bytes)
An existing output is never overwritten.
"""
import argparse
import gzip
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

SCHEMA = 'ubts.esp32_bridge_kit.manifest.v1'
PAYLOAD = ('scripts/esp32_abs_hold.py', 'scripts/esp32_steer_bridge.py', 'scripts/esp_rover_bridge.py',
           'scripts/steer_control_core.py', 'scripts/rover_control_core.py')
TOOLS = ('deploy/jetson_esp32_bridge/install_esp32_bridge.py', 'deploy/jetson_esp32_bridge/rollback_esp32_bridge.py',
         'deploy/jetson_esp32_bridge/README_KO.md')


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), '-c', 'core.autocrlf=false', *args], check=True,
                          capture_output=True).stdout


def build(repo, commit, out_dir):
    full = git(repo, 'rev-parse', '--verify', commit + '^{commit}').decode().strip()
    name = 'ubts-esp32-bridge-kit-%s' % full[:12]
    root = Path(out_dir) / name
    archive = Path(out_dir) / (name + '.tar.gz')
    if root.exists() or archive.exists():
        raise SystemExit('output exists, not overwritten: %s' % root)
    files = {}
    for rel in PAYLOAD:
        files['payload/' + rel] = git(repo, 'cat-file', 'blob', '%s:%s' % (full, rel))
    for rel in TOOLS:
        files[rel.split('/')[-1]] = git(repo, 'cat-file', 'blob', '%s:%s' % (full, rel))
    manifest = {'schema': SCHEMA, 'commit': full,
                'files': {rel: hashlib.sha256(files['payload/' + rel]).hexdigest() for rel in PAYLOAD},
                'tools': {rel.split('/')[-1]: hashlib.sha256(files[rel.split('/')[-1]]).hexdigest() for rel in TOOLS},
                'note': 'Copies the files into a new container made from the old bridge image; no build, no pull.'}
    files['MANIFEST.json'] = (json.dumps(manifest, indent=1, sort_keys=True) + '\n').encode('utf-8')
    sums = ''.join('%s  %s\n' % (hashlib.sha256(data).hexdigest(), rel) for rel, data in sorted(files.items()))
    files['SHA256SUMS'] = sums.encode('utf-8')
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for rel, data in sorted(files.items()):
            info = tarfile.TarInfo('%s/%s' % (name, rel))
            info.size, info.mtime, info.uid, info.gid = len(data), 0, 0, 0
            info.mode = 0o755 if rel.endswith('.py') and '/' not in rel else 0o644
            tar.addfile(info, io.BytesIO(data))
    with open(archive, 'wb') as handle:
        with gzip.GzipFile(filename='', mode='wb', fileobj=handle, mtime=0) as gz:
            gz.write(raw.getvalue())
    return root, archive, manifest


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--commit', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--repo', default=str(Path(__file__).resolve().parents[2]))
    args = p.parse_args(argv)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    root, archive, manifest = build(args.repo, args.commit, args.out)
    print('kit: %s' % root)
    print('archive: %s (sha256 %s)' % (archive, hashlib.sha256(archive.read_bytes()).hexdigest()))
    print('MANIFEST.json sha256 %s' % hashlib.sha256((root / 'MANIFEST.json').read_bytes()).hexdigest())
    return 0


if __name__ == '__main__':
    sys.exit(main())
