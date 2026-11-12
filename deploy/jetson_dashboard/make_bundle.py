#!/usr/bin/env python3
"""Make the Jetson dashboard install bundle from one commit of this repository (run on the PC).

The files that go to /app are read from the commit's Dockerfile (final stage COPY lines, .dockerignore
applied). Blobs are read with git cat-file, so line endings are exactly as stored (LF).

Output (nothing is overwritten):
  <out>/ubts-dashboard-kit-<commit12>/        MANIFEST.json, payload.tar, install_dashboard.py,
                                              rollback_dashboard.py, README_KO.md, SHA256SUMS
  <out>/ubts-dashboard-kit-<commit12>.tar.gz  the same folder in one file

With --baseline (default: the tag of the last recorded Jetson install) the manifest also lists the files
the repository deleted since then (the installer removes them), and the changes this kit does NOT
install (ROS bridge images, native stream, compose). Dockerfile.ros2 is checked for scripts that are
imported but not copied (todo D-06).
"""
from __future__ import annotations

import argparse
import ast
import datetime
import gzip
import hashlib
import io
import json
import posixpath
import re
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST_SCHEMA = 'ubts.dashboard_kit.manifest.v1'
APP_ROOT = '/app'
DEFAULT_BASELINE = 'baseline-installed-20261025'
KIT_TOOLS = ('install_dashboard.py', 'rollback_dashboard.py', 'README_KO.md')
KIT_SOURCE_DIR = 'deploy/jetson_dashboard'


class BundleError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Git:
    def __init__(self, repo):
        self.repo = str(repo)

    def run(self, *args, data=None, check=True):
        proc = subprocess.run(['git', '-c', 'core.autocrlf=false', '-c', 'core.quotepath=off', '-C', self.repo, *args],
                              input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if check and proc.returncode != 0:
            raise BundleError('git %s: %s' % (' '.join(args[:3]), proc.stderr.decode('utf-8', 'replace').strip()[:300]))
        return proc

    def text(self, *args):
        return self.run(*args).stdout.decode('utf-8').strip()

    def rev(self, ref):
        return self.text('rev-parse', '--verify', ref + '^{commit}')

    def kind(self, commit, path):
        proc = self.run('cat-file', '-t', '%s:%s' % (commit, path), check=False)
        return proc.stdout.decode().strip() if proc.returncode == 0 else None

    def show(self, commit, path):
        proc = self.run('cat-file', 'blob', '%s:%s' % (commit, path), check=False)
        return proc.stdout if proc.returncode == 0 else None

    def ls_tree(self, commit, path):
        out = self.run('ls-tree', '-r', '-z', '--full-tree', commit, '--', path).stdout
        rows = []
        for item in out.split(b'\0'):
            if not item:
                continue
            meta, name = item.split(b'\t', 1)
            mode, kind, oid = meta.decode().split()
            rows.append((mode, kind, oid, name.decode('utf-8')))
        return rows

    def blobs(self, oids):
        if not oids:
            return {}
        out = self.run('cat-file', '--batch', data=('\n'.join(oids) + '\n').encode()).stdout
        result = {}
        pos = 0
        for oid in oids:
            end = out.index(b'\n', pos)
            header = out[pos:end].decode().split()
            if len(header) != 3 or header[1] != 'blob':
                raise BundleError('git object %s is not a blob' % oid)
            size = int(header[2])
            result[oid] = out[end + 1:end + 1 + size]
            pos = end + 1 + size + 1
        return result

    def diff(self, old, new, paths):
        out = self.run('diff', '--name-status', '--no-renames', '-z', old, new, '--', *paths).stdout
        items = [x.decode('utf-8') for x in out.split(b'\0') if x]
        return [(items[i], items[i + 1]) for i in range(0, len(items) - 1, 2)]


# ----------------------------------------------------------------------------------------------------
# Dockerfile and .dockerignore
# ----------------------------------------------------------------------------------------------------
def parse_dockerfile(text):
    lines = []
    buf = ''
    for raw in text.splitlines():
        line = raw.rstrip()
        if not buf and (not line.strip() or line.lstrip().startswith('#')):
            continue
        if line.endswith('\\'):
            buf += line[:-1] + ' '
            continue
        buf += line
        lines.append(buf.strip())
        buf = ''
    stages = []
    for line in lines:
        word, _, rest = line.partition(' ')
        word = word.upper()
        if word == 'FROM':
            stages.append(dict(source=rest.strip(), workdir='/', copies=[], env=[], other=[]))
            continue
        if not stages:
            continue
        stage = stages[-1]
        if word == 'WORKDIR':
            stage['workdir'] = posixpath.normpath(posixpath.join(stage['workdir'], rest.strip()))
        elif word in ('COPY', 'ADD'):
            rest = rest.strip()
            if rest.startswith('['):
                tokens = json.loads(rest)
                flags = []
            else:
                tokens = shlex.split(rest)
                flags = [t for t in tokens if t.startswith('--')]
                tokens = [t for t in tokens if not t.startswith('--')]
            stage['copies'].append(dict(kind=word, flags=flags, sources=tokens[:-1], dest=tokens[-1], line=line))
        elif word == 'ENV':
            stage['env'].append(rest.strip())
        else:
            stage['other'].append(line)
    if not stages:
        raise BundleError('Dockerfile has no FROM')
    return stages


def _ignore_regex(pattern):
    out = ''
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith('**/', i):
            out += '(?:.*/)?'
            i += 3
            continue
        if pattern.startswith('**', i):
            out += '.*'
            i += 2
            continue
        if c == '*':
            out += '[^/]*'
        elif c == '?':
            out += '[^/]'
        elif c == '[':
            j = pattern.find(']', i)
            if j < 0:
                out += re.escape(c)
            else:
                out += pattern[i:j + 1]
                i = j
        else:
            out += re.escape(c)
        i += 1
    return re.compile('^' + out + '(?:/.*)?$')


def parse_dockerignore(text):
    rules = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        negate = line.startswith('!')
        if negate:
            line = line[1:].strip()
        line = posixpath.normpath(line.lstrip('/'))
        rules.append((negate, _ignore_regex(line)))
    return rules


def ignored(path, rules):
    result = False
    for negate, regex in rules:
        if regex.match(path):
            result = not negate
    return result


# ----------------------------------------------------------------------------------------------------
# Image file set
# ----------------------------------------------------------------------------------------------------
def image_files(git, commit, stages, rules):
    """{rel under /app: (mode, oid, repo path)}, managed roots, generated paths, rebuild sources."""
    final = stages[-1]
    files = {}
    roots = []
    generated = []
    excluded = []
    copy_lines = []
    for copy in final['copies']:
        copy_lines.append(copy['line'])
        if copy['kind'] != 'COPY':
            raise BundleError('ADD is not supported: ' + copy['line'])
        dest = copy['dest']
        dest_abs = posixpath.normpath(posixpath.join(final['workdir'], dest))
        dest_is_dir = dest.endswith('/') or dest in ('.', './') or len(copy['sources']) > 1
        if any(f.startswith('--from') for f in copy['flags']):
            for source in copy['sources']:
                target = posixpath.join(dest_abs, posixpath.basename(source)) if dest_is_dir else dest_abs
                generated.append(posixpath.relpath(target, APP_ROOT))
            continue
        for source in copy['sources']:
            source = posixpath.normpath(source)
            kind = git.kind(commit, source)
            if kind == 'tree':
                rel_root = posixpath.relpath(dest_abs, APP_ROOT)
                if rel_root.startswith('..'):
                    raise BundleError('COPY outside /app: ' + copy['line'])
                roots.append(dict(path=rel_root, kind='dir'))
                for mode, otype, oid, path in git.ls_tree(commit, source):
                    if otype != 'blob':
                        raise BundleError('submodule in image path: ' + path)
                    if mode == '120000':
                        raise BundleError('symbolic link in image path: ' + path)
                    if ignored(path, rules):
                        excluded.append(path)
                        continue
                    rel = posixpath.join(rel_root, posixpath.relpath(path, source))
                    files[rel] = (mode, oid, path)
            elif kind == 'blob':
                target = posixpath.join(dest_abs, posixpath.basename(source)) if dest_is_dir else dest_abs
                rel = posixpath.relpath(target, APP_ROOT)
                if rel.startswith('..'):
                    raise BundleError('COPY outside /app: ' + copy['line'])
                if ignored(source, rules):
                    excluded.append(source)
                    continue
                mode, otype, oid, path = git.ls_tree(commit, source)[0]
                roots.append(dict(path=rel, kind='file'))
                files[rel] = (mode, oid, path)
            else:
                raise BundleError('COPY source not in commit: ' + source)
    rebuild = {}
    for stage in stages[:-1]:
        for copy in stage['copies']:
            for source in copy['sources']:
                if git.kind(commit, source) == 'blob':
                    rebuild[source] = True
    if final['workdir'] != APP_ROOT:
        raise BundleError('final WORKDIR is %s, the kit expects %s' % (final['workdir'], APP_ROOT))
    return files, roots, generated, excluded, sorted(rebuild), copy_lines


# ----------------------------------------------------------------------------------------------------
# Dockerfile.ros2 import check (todo D-06)
# ----------------------------------------------------------------------------------------------------
def _imported_modules(source):
    names = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split('.')
                names.add(parts[1] if parts[0] == 'scripts' and len(parts) > 1 else parts[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            parts = module.split('.') if module else []
            if node.level:
                if parts:
                    names.add(parts[0])
                else:
                    names.update(alias.name for alias in node.names)
            elif parts and parts[0] == 'scripts':
                if len(parts) > 1:
                    names.add(parts[1])
                else:
                    names.update(alias.name for alias in node.names)
            elif parts:
                names.add(parts[0])
    return names


def ros_missing_copies(git, commit):
    raw = git.show(commit, 'Dockerfile.ros2')
    if raw is None:
        return None
    stages = parse_dockerfile(raw.decode('utf-8'))
    copied = set()
    for copy in stages[-1]['copies']:
        for source in copy['sources']:
            source = posixpath.normpath(source)
            if source.startswith('scripts/') and source.endswith('.py'):
                copied.add(source)
    available = set(path for _, _, _, path in git.ls_tree(commit, 'scripts') if path.endswith('.py'))
    missing = {}
    seen = set()
    todo = sorted(copied)
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        body = git.show(commit, path)
        if body is None:
            continue
        for name in sorted(_imported_modules(body.decode('utf-8', 'replace'))):
            candidate = 'scripts/%s.py' % name
            if candidate not in available:
                continue
            if candidate not in copied:
                missing.setdefault(candidate, []).append(path)
            todo.append(candidate)
    compose = git.show(commit, 'docker-compose.yml') or b''
    entry = set('scripts/%s.py' % m for m in re.findall(r'/app/scripts/([A-Za-z0-9_]+)\.py', compose.decode()))
    entry |= set('scripts/%s.py' % m for m in re.findall(r'-m scripts\.([A-Za-z0-9_]+)', compose.decode()))
    return dict(copied=len(copied),
                missing_imports=[dict(file=k, imported_by=sorted(set(v))) for k, v in sorted(missing.items())],
                compose_entrypoints_not_copied=sorted(entry - copied))


# ----------------------------------------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------------------------------------
def deterministic_tar(entries, mtime):
    """entries: [(name, bytes, mode)] -> tar bytes (sorted, root:root, fixed mtime)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, body, mode in sorted(entries):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = mode
            info.mtime = mtime
            info.uid = info.gid = 0
            info.uname = info.gname = 'root'
            archive.addfile(info, io.BytesIO(body))
    return raw.getvalue()


def make(repo, commit_ref, baseline_ref, out_dir, allow_dockerfile_change=False, tool_dir=HERE):
    git = Git(repo)
    commit = git.rev(commit_ref)
    info = git.text('show', '-s', '--format=%ct%x00%s', commit).split('\0')
    commit_time = int(info[0])
    dockerfile = git.show(commit, 'Dockerfile')
    if dockerfile is None:
        raise BundleError('no Dockerfile in commit')
    stages = parse_dockerfile(dockerfile.decode('utf-8'))
    rules = parse_dockerignore((git.show(commit, '.dockerignore') or b'').decode('utf-8'))
    files, roots, generated, excluded, rebuild, copy_lines = image_files(git, commit, stages, rules)
    oids = sorted(set(oid for _, oid, _ in files.values()))
    blobs = git.blobs(oids)
    manifest_files = {}
    entries = []
    for rel in sorted(files):
        mode, oid, path = files[rel]
        body = blobs[oid]
        file_mode = 0o755 if mode == '100755' else 0o644
        manifest_files[rel] = dict(sha256=sha256(body), size=len(body), mode=file_mode)
        entries.append((rel, body, file_mode))
    payload = deterministic_tar(entries, commit_time)

    baseline = None
    remove = []
    dockerfile_changed = False
    if baseline_ref:
        base = git.rev(baseline_ref)
        repo_roots = []
        for copy in stages[-1]['copies']:
            if not any(f.startswith('--from') for f in copy['flags']):
                repo_roots.extend(posixpath.normpath(s) for s in copy['sources'])
        status = git.diff(base, commit, repo_roots)
        by_repo = dict((path, rel) for rel, (_, _, path) in files.items())
        root_map = []
        for copy in stages[-1]['copies']:
            if any(f.startswith('--from') for f in copy['flags']):
                continue
            dest_abs = posixpath.normpath(posixpath.join(stages[-1]['workdir'], copy['dest']))
            for source in copy['sources']:
                root_map.append((posixpath.normpath(source), posixpath.relpath(dest_abs, APP_ROOT)))
        for code, path in status:
            if code != 'D' or path in by_repo or ignored(path, rules):
                continue
            for source, rel_root in root_map:
                if path.startswith(source + '/'):
                    remove.append(posixpath.join(rel_root, posixpath.relpath(path, source)))
        old_dockerfile = git.show(base, 'Dockerfile')
        dockerfile_changed = old_dockerfile != dockerfile
        outside = {}
        for code, path in git.diff(base, commit, ['scripts', 'Dockerfile.ros2', 'Dockerfile.lidar_driver', 'deploy',
                                                  'docker-compose.yml', 'docker-compose.jetson-fan.yml']):
            if path in by_repo or path.startswith(KIT_SOURCE_DIR + '/'):
                continue
            if path.startswith('deploy/native_sensor_stream/'):
                group = 'native_sensor_stream'
            elif path.startswith('scripts/') or path.startswith('Dockerfile.'):
                group = 'ros_bridge_images'
            elif path.startswith('docker-compose'):
                group = 'compose_files'
            else:
                group = 'other_deploy'
            outside.setdefault(group, []).append('%s %s' % (code, path))
        counts = {}
        for code, _ in status:
            counts[code] = counts.get(code, 0) + 1
        baseline = dict(ref=baseline_ref, commit=base, image_path_changes=counts, removed_files=sorted(remove),
                        not_installed_by_this_kit=outside)
        if dockerfile_changed and not allow_dockerfile_change:
            raise BundleError('Dockerfile changed since %s: the image must be rebuilt, the overlay kit cannot '
                              'carry ENV/RUN changes (use --allow-dockerfile-change only if COPY lines alone changed)'
                              % baseline_ref)

    tools = {}
    tool_bodies = {}
    for name in KIT_TOOLS:
        body = (Path(tool_dir) / name).read_bytes()
        if b'\r\n' in body:
            raise BundleError('%s has CRLF line endings' % name)
        tool_bodies[name] = body
        tools[name] = sha256(body)
    tools_vs_commit = {}
    for name in KIT_TOOLS:
        in_commit = git.show(commit, KIT_SOURCE_DIR + '/' + name)
        tools_vs_commit[name] = 'absent' if in_commit is None else 'same' if in_commit == tool_bodies[name] else 'differs'

    ros2 = git.show(commit, 'Dockerfile.ros2')
    notes = dict(
        dashboard_server_also_copied_into_ros_image=bool(ros2 and b'COPY dashboard_server' in ros2),
        dockerfile_ros2_imports=ros_missing_copies(git, commit),
        dockerfile_changed_since_baseline=dockerfile_changed,
    )
    bundle_id = commit[:12]
    manifest = dict(
        schema=MANIFEST_SCHEMA, bundle_id=bundle_id, commit=commit, commit_time_epoch=commit_time,
        commit_time_utc=datetime.datetime.fromtimestamp(commit_time, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        commit_subject=info[1], made_utc=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        app_root=APP_ROOT, managed_roots=roots, generated_in_image=sorted(generated), rebuild_sources=rebuild,
        dockerfile=dict(sha256=sha256(dockerfile), final_stage_copy_lines=copy_lines, final_stage_env=stages[-1]['env']),
        dockerignore_excluded=sorted(excluded), files=manifest_files, remove=sorted(remove),
        payload=dict(name='payload.tar', sha256=sha256(payload), bytes=len(payload), file_count=len(manifest_files)),
        baseline=baseline, notes=notes, tools=tools, tools_vs_commit=tools_vs_commit)

    out_dir = Path(out_dir)
    folder_name = 'ubts-dashboard-kit-' + bundle_id
    folder = out_dir / folder_name
    archive_path = out_dir / (folder_name + '.tar.gz')
    if folder.exists() or archive_path.exists():
        raise BundleError('output exists, nothing overwritten: ' + str(folder))
    folder.mkdir(parents=True)
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + '\n').encode('utf-8')
    written = {'MANIFEST.json': manifest_raw, 'payload.tar': payload}
    written.update(tool_bodies)
    sums = ''.join('%s  %s\n' % (sha256(body), name) for name, body in sorted(written.items()))
    written['SHA256SUMS'] = sums.encode('ascii')
    for name, body in written.items():
        (folder / name).write_bytes(body)
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, compresslevel=6) as zipped:
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.PAX_FORMAT) as archive:
            info_dir = tarfile.TarInfo(folder_name)
            info_dir.type = tarfile.DIRTYPE
            info_dir.mode = 0o755
            info_dir.mtime = commit_time
            info_dir.uname = info_dir.gname = 'root'
            archive.addfile(info_dir)
            for name in sorted(written):
                info_file = tarfile.TarInfo(folder_name + '/' + name)
                info_file.size = len(written[name])
                info_file.mode = 0o755 if name.endswith('.py') else 0o644
                info_file.mtime = commit_time
                info_file.uname = info_file.gname = 'root'
                archive.addfile(info_file, io.BytesIO(written[name]))
    archive_path.write_bytes(raw.getvalue())
    return dict(folder=str(folder), archive=str(archive_path), archive_sha256=sha256(raw.getvalue()),
                archive_bytes=len(raw.getvalue()), manifest_sha256=sha256(manifest_raw), manifest=manifest)


def main(argv=None):
    p = argparse.ArgumentParser(description='Jetson 대시보드 설치 묶음 만들기 (PC)')
    p.add_argument('--repo', default=str(HERE.parent.parent), help='대시보드 레포 경로')
    p.add_argument('--commit', default='HEAD', help='묶을 커밋 (기본 HEAD)')
    p.add_argument('--baseline', default=DEFAULT_BASELINE, help='Jetson 에 마지막으로 설치된 판의 ref (지운 파일 계산용)')
    p.add_argument('--no-baseline', action='store_true')
    p.add_argument('--out', required=True, help='출력 폴더 (레포 밖 권장)')
    p.add_argument('--allow-dockerfile-change', action='store_true')
    opt = p.parse_args(argv)
    try:
        result = make(opt.repo, opt.commit, None if opt.no_baseline else opt.baseline, opt.out,
                      opt.allow_dockerfile_change)
    except BundleError as error:
        print('실패: %s' % error, file=sys.stderr)
        return 2
    m = result['manifest']
    print('묶음 %s (%s)' % (m['bundle_id'], m['commit_subject']))
    print('파일 %d개, payload %d B, 지울 파일 %d개' % (len(m['files']), m['payload']['bytes'], len(m['remove'])))
    for item in (m['notes']['dockerfile_ros2_imports'] or {}).get('missing_imports', []):
        print('알림(D-06): ROS 이미지에 복사되지 않는 %s (import: %s)' % (item['file'], ', '.join(item['imported_by'])))
    if m['baseline']:
        for group, rows in sorted(m['baseline']['not_installed_by_this_kit'].items()):
            print('이 묶음이 설치하지 않는 변경 [%s] %d개' % (group, len(rows)))
    print('폴더 %s' % result['folder'])
    print('파일 %s (%d B, SHA-256 %s)' % (result['archive'], result['archive_bytes'], result['archive_sha256']))
    print('MANIFEST.json SHA-256 %s' % result['manifest_sha256'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
