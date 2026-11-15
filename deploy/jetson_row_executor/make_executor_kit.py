#!/usr/bin/env python3
"""Build the row executor kit for the Jetson (run on the PC).

  python deploy/jetson_row_executor/make_executor_kit.py --sim-repo <simulation repository> --sim-commit <commit> \
      --out <folder outside the repositories> [--dash-commit <commit of this repository, default HEAD>]

Files are read with 'git cat-file' from the commits (the bytes stored there, LF), never from a work tree:
  ubts-row-executor-kit-<sim 12>-<dash 12>/
    install_row_executor.py, README_KO.md                    (this repository at --dash-commit)
    payload/row_executor/...                                 (tools/row_executor of the simulation repository at
                                                              --sim-commit, every file: its SHA256SUMS lists them all)
    payload/config/config.vehicle_esp32_shadow.json          (the vehicle SHADOW configuration of that package)
    MANIFEST.json   (schema, both commits, file SHA-256, the runtime image of the installer)
    SHA256SUMS
  ubts-row-executor-kit-<sim 12>-<dash 12>.tar.gz            (the same folder; the same commits give the same bytes)
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

SCHEMA = 'ubts.row_executor_kit.manifest.v1'
PACKAGE = 'tools/row_executor'
CONFIGS = ('config.vehicle_esp32_shadow.json',)
TOOLS = ('deploy/jetson_row_executor/install_row_executor.py', 'deploy/jetson_row_executor/README_KO.md')


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), '-c', 'core.autocrlf=false', *args], check=True,
                          capture_output=True).stdout


def build(sim_repo, sim_commit, dash_repo, dash_commit, out_dir):
    sim = git(sim_repo, 'rev-parse', '--verify', sim_commit + '^{commit}').decode().strip()
    dash = git(dash_repo, 'rev-parse', '--verify', dash_commit + '^{commit}').decode().strip()
    name = 'ubts-row-executor-kit-%s-%s' % (sim[:12], dash[:12])
    root = Path(out_dir) / name
    archive = Path(out_dir) / (name + '.tar.gz')
    if root.exists() or archive.exists():
        raise SystemExit('output exists, not overwritten: %s' % root)
    listed = git(sim_repo, 'ls-tree', '-r', '--name-only', sim, PACKAGE + '/').decode('utf-8').splitlines()
    files = {}
    for path in listed:
        rel = 'row_executor/' + path[len(PACKAGE) + 1:]
        files['payload/' + rel] = git(sim_repo, 'cat-file', 'blob', '%s:%s' % (sim, path))
    for config in CONFIGS:
        files['payload/config/' + config] = files['payload/row_executor/' + config]
    for rel in TOOLS:
        files[rel.split('/')[-1]] = git(dash_repo, 'cat-file', 'blob', '%s:%s' % (dash, rel))
    image = [line for line in files['install_row_executor.py'].decode('utf-8').splitlines()
             if line.startswith("IMAGE = '")][0].split("'")[1]
    manifest = {'schema': SCHEMA, 'sim_commit': sim, 'dash_commit': dash, 'runtime_image': image,
                'files': {rel[len('payload/'):]: hashlib.sha256(data).hexdigest()
                          for rel, data in sorted(files.items()) if rel.startswith('payload/')},
                'tools': {rel.split('/')[-1]: hashlib.sha256(files[rel.split('/')[-1]]).hexdigest() for rel in TOOLS},
                'note': 'A new container from an existing ROS Iron image with this package mounted read-only; no build, '
                        'no pull. Default mode SHADOW (no motor output, no control socket).'}
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
            info.mode = 0o755 if rel == 'install_row_executor.py' else 0o644
            tar.addfile(info, io.BytesIO(data))
    with open(archive, 'wb') as handle:
        with gzip.GzipFile(filename='', mode='wb', fileobj=handle, mtime=0) as gz:
            gz.write(raw.getvalue())
    return root, archive, manifest


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--sim-repo', required=True)
    p.add_argument('--sim-commit', required=True)
    p.add_argument('--dash-commit', default='HEAD')
    p.add_argument('--dash-repo', default=str(Path(__file__).resolve().parents[2]))
    p.add_argument('--out', required=True)
    args = p.parse_args(argv)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    root, archive, manifest = build(args.sim_repo, args.sim_commit, args.dash_repo, args.dash_commit, args.out)
    print('kit: %s (%d payload files)' % (root, len(manifest['files'])))
    print('archive: %s (sha256 %s)' % (archive, hashlib.sha256(archive.read_bytes()).hexdigest()))
    print('MANIFEST.json sha256 %s' % hashlib.sha256((root / 'MANIFEST.json').read_bytes()).hexdigest())
    return 0


if __name__ == '__main__':
    sys.exit(main())
