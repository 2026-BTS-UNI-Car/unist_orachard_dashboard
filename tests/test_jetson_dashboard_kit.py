"""Policy tests for the Jetson dashboard install kit (deploy/jetson_dashboard).

A fake Docker Engine and a fake dashboard HTTP server stand in for the Jetson: no socket, device,
SSH or real container is used. The bundle maker test builds a small throwaway git repository.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import posixpath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
import urllib.parse
from pathlib import Path

KIT = Path(__file__).resolve().parents[1] / 'deploy' / 'jetson_dashboard'
sys.path.insert(0, str(KIT))
import install_dashboard as kit  # noqa: E402
import make_bundle  # noqa: E402
import rollback_dashboard  # noqa: E402

NAME = 'unist-orachard-dashboard'
PORT = '18080'


def sha(data):
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------------------------------------
# fakes
# ------------------------------------------------------------------------------------------------
class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class FakeDocker:
    """Enough of the Engine API for the kit. It has no remove/prune/pull on purpose."""

    MUTATING = ('commit', 'create', 'put_archive', 'start', 'stop', 'kill', 'rename', 'set_restart')

    def __init__(self, clock=None):
        self.containers_ = {}
        self.images = {}
        self.tags = {}
        self.calls = []
        self.n = 0
        self.clock = clock
        self.preflight_exit = 0
        self.on_call = {}
        self.started = 0

    def _id(self):
        self.n += 1
        return sha(b'fake-%d' % self.n)

    def _hook(self, name, *args):
        self.calls.append((name,) + args)
        hook = self.on_call.get(name)
        if hook:
            hook(*args)

    def add_image(self, files, config, tag=None):
        iid = 'sha256:' + self._id()
        self.images[iid] = dict(Id=iid, Config=copy.deepcopy(config), RootFS=dict(Layers=['sha256:' + self._id()]),
                                files=dict(files))
        if tag:
            self.tags[tag] = iid
        return iid

    def _resolve_image(self, ref):
        return ref if ref in self.images else self.tags.get(ref)

    def _find(self, ref):
        if ref in self.containers_:
            return self.containers_[ref]
        for c in self.containers_.values():
            if c['Name'] == '/' + ref:
                return c
        return None

    def add_container(self, name, image, config, host, running=True, extra=None):
        cid = self.create(name, dict(config, Image=image, HostConfig=host))
        c = self.containers_[cid]
        c['files'].update(extra or {})
        if running:
            self.start(cid)
        self.calls.clear()
        return cid

    # -- API --------------------------------------------------------------------------------------
    def containers(self, include_stopped=False):
        return [dict(Id=c['Id'], ImageID=c['Image']) for c in self.containers_.values()
                if include_stopped or c['State']['Running']]

    def inspect(self, ref):
        c = self._find(ref)
        if c is None:
            return None
        out = copy.deepcopy({k: v for k, v in c.items() if k != 'files'})
        if c['State']['Running'] and c['Config'].get('Healthcheck'):
            out['State']['Health'] = dict(Status=c.get('health', 'healthy'))
        return out

    def image(self, ref):
        iid = self._resolve_image(ref)
        if iid is None:
            return None
        return copy.deepcopy({k: v for k, v in self.images[iid].items() if k != 'files'})

    def read_tree(self, cid, path, timeout=60.0, max_bytes=kit.MAX_TREE_BYTES):
        c = self._find(cid)
        prefix = path.rstrip('/')
        names = [p for p in c['files'] if p == prefix or p.startswith(prefix + '/')]
        if not names:
            return None
        parent = posixpath.dirname(prefix)
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as archive:
            dirs = set()
            for p in sorted(names):
                rel = posixpath.relpath(p, parent)
                d = posixpath.dirname(rel)
                while d and d not in dirs:
                    dirs.add(d)
                    d = posixpath.dirname(d)
            for d in sorted(dirs):
                info = tarfile.TarInfo(d)
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            for p in sorted(names):
                body = c['files'][p]
                info = tarfile.TarInfo(posixpath.relpath(p, parent))
                info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
        raw.seek(0)
        return kit.tar_digest(raw, parent, max_bytes)

    def read_file(self, cid, path, max_bytes=65536, timeout=10.0):
        c = self._find(cid)
        body = c['files'].get(path)
        return None if body is None else body[:max_bytes]

    @staticmethod
    def merge(user, container):
        """Like the Engine's commit: empty fields of the given config come from the container."""
        user = copy.deepcopy(user)
        container = copy.deepcopy(container)
        for key in ('User', 'WorkingDir', 'Healthcheck', 'StopSignal'):
            if not user.get(key):
                user[key] = container.get(key)
        if not user.get('Env'):
            user['Env'] = container.get('Env')
        labels = dict(user.get('Labels') or {})
        for key, value in (container.get('Labels') or {}).items():
            labels.setdefault(key, value)
        user['Labels'] = labels
        if not user.get('Entrypoint'):
            if not user.get('Cmd'):
                user['Cmd'] = container.get('Cmd')
            if user.get('Entrypoint') is None:
                user['Entrypoint'] = container.get('Entrypoint')
        return user

    def commit(self, cid, repo, tag, pause, config=None, comment='', timeout=120.0):
        self._hook('commit', cid, repo, tag, pause)
        c = self._find(cid)
        base = self.images[c['Image']]
        iid = 'sha256:' + self._id()
        merged = c['Config'] if config is None else self.merge(config, c['Config'])
        self.images[iid] = dict(Id=iid, Config=copy.deepcopy(merged),
                                RootFS=dict(Layers=base['RootFS']['Layers'] + ['sha256:' + self._id()]),
                                files=dict(c['files']))
        self.tags[repo + ':' + tag] = iid
        return iid

    def create(self, name, spec):
        self._hook('create', name)
        assert self._find(name) is None, 'name conflict ' + name
        spec = copy.deepcopy(spec)
        host = spec.pop('HostConfig', {})
        iid = self._resolve_image(spec['Image'])
        assert iid is not None, 'image missing (a pull would be needed)'
        image = self.images[iid]
        config = dict(copy.deepcopy(image['Config']), **spec)
        cid = self._id()
        mounts = []
        for bind in host.get('Binds') or []:
            src, dst = bind.split(':')[:2]
            mounts.append(dict(Type='bind', Source=src, Destination=dst, Mode='', RW=True, Propagation='rprivate'))
        self.containers_[cid] = dict(
            Id=cid, Name='/' + name, Image=iid, Config=config, HostConfig=host, Mounts=mounts,
            NetworkSettings=dict(Networks={host.get('NetworkMode', 'bridge'): {}}),
            State=dict(Running=False, Paused=False, Restarting=False, Status='created', ExitCode=0, StartedAt=None),
            files=dict(image['files']))
        return cid

    def put_archive(self, cid, path, data, timeout=60.0):
        self._hook('put_archive', cid, path)
        c = self._find(cid)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
            for member in archive:
                if member.isfile():
                    c['files'][posixpath.join(path, member.name)] = archive.extractfile(member).read()

    def start(self, cid):
        self._hook('start', cid)
        c = self._find(cid)
        entry = c['Config'].get('Entrypoint') or []
        self.started += 1
        c['State'].update(StartedAt='2026-11-13T00:00:%02dZ' % self.started)
        if entry[:2] == ['python3', '-c'] and entry[2] == kit.REMOVE_CODE:
            for p in c['Config'].get('Cmd') or []:
                c['files'].pop(p, None)
            c['State'].update(Running=False, Status='exited', ExitCode=0)
            c['logs'] = 'UBTS_KIT_REMOVE_OK'
        elif entry[:2] == ['python3', '-c'] and entry[2] == kit.IMPORT_CODE:
            code = self.preflight_exit
            c['State'].update(Running=False, Status='exited', ExitCode=code)
            c['logs'] = 'UBTS_KIT_IMPORT_OK []' if code == 0 else 'ModuleNotFoundError: scipy'
        else:
            c['State'].update(Running=True, Status='running')

    def stop(self, cid, grace=20):
        self._hook('stop', cid)
        c = self._find(cid)
        c['State'].update(Running=False, Status='exited', ExitCode=0)

    def kill(self, cid):
        self._hook('kill', cid)
        self.stop(cid)

    def wait(self, cid, timeout):
        c = self._find(cid)
        assert not c['State']['Running']
        return c['State']['ExitCode']

    def rename(self, cid, name):
        self._hook('rename', cid, name)
        other = self._find(name)
        assert other is None, 'name conflict ' + name
        self._find(cid)['Name'] = '/' + name

    def set_restart(self, cid, policy):
        self._hook('set_restart', cid, policy)
        self._find(cid)['HostConfig']['RestartPolicy'] = copy.deepcopy(policy)

    def logs(self, cid, tail=60):
        return self._find(cid).get('logs', '')

    def mutations(self):
        return [c for c in self.calls if c[0] in self.MUTATING]


class FakeHTTP:
    """The dashboard as the kit sees it: answers for the running container that owns the port."""

    def __init__(self, docker):
        self.docker = docker
        self.rover = dict(ok=True, armed=False, moving=False, connected=True)
        self.recorder = dict(ok=True, state='idle', active=False, finalizing=False)
        self.jobs = [dict(id='live', active=False)]
        self.supervisor = dict(active=False, motor_lease_acquired=False, motor_command_sent=False)
        self.broken_health = set()
        self.tamper_static = set()
        self.down = set()
        self.requests = []

    def owner(self, port):
        for c in self.docker.containers_.values():
            if c['State']['Running'] and 'DASHBOARD_PORT=%s' % port in (c['Config'].get('Env') or []):
                return c
        return None

    def __call__(self, url, timeout=5.0):
        self.requests.append(url)
        parsed = urllib.parse.urlparse(url)
        port = str(parsed.port)
        if port == '18790':
            return 200, json.dumps(self.recorder).encode()
        c = self.owner(port)
        if c is None or c['Id'] in self.down:
            raise kit.KitError('http_unreachable', url)
        path = parsed.path
        if path == '/api/health':
            if c['Id'] in self.broken_health:
                return 500, b'{}'
            return 200, b'{"ok": true}'
        if path == '/api/rover/status':
            return 200, json.dumps(self.rover).encode()
        if path == '/api/recordings/status':
            return 200, json.dumps(self.recorder).encode()
        if path == '/api/live/mapping-jobs':
            return 200, json.dumps(dict(ok=True, jobs=self.jobs)).encode()
        if path == '/api/autonomy/supervisor':
            return (200, json.dumps(self.supervisor).encode()) if self.supervisor is not None else (404, b'')
        if path == '/api/telemetry':
            return 200, b'{"meta": {}}'
        if path == '/':
            return 200, c['files'].get('/app/static/index.html', b'')
        if path.startswith('/static/'):
            body = c['files'].get('/app' + urllib.parse.unquote(path))
            if body is None:
                return 404, b''
            if c['Id'] in self.tamper_static:
                body = body + b'/* old */'
            return 200, body
        return 404, b''


# ------------------------------------------------------------------------------------------------
# fixtures
# ------------------------------------------------------------------------------------------------
OLD_FILES = {
    'requirements.txt': b'numpy\n',
    'dashboard_server/__init__.py': b'',
    'dashboard_server/app.py': b'OLD = 1\n',
    'dashboard_server/native_raster.cc': b'// raster\n',
    'static/index.html': b'<script src="/static/app.js"></script>\n',
    'static/app.js': b'old();\n',
    'static/style.css': b'body{}\n',
    'docs/a.md': b'# a\n',
}
NEW_FILES = {
    'requirements.txt': b'numpy\n',
    'dashboard_server/__init__.py': b'',
    'dashboard_server/app.py': b'NEW = 2\n',
    'dashboard_server/native_raster.cc': b'// raster\n',
    'dashboard_server/static_assets.py': b'X = 1\n',
    'static/index.html': b'<script src="/static/app/00-main.js"></script>\n',
    'static/app/00-main.js': b'main();\n',
    'static/style.css': b'body{}\n',
    'docs/a.md': b'# a\n',
}
ROOTS = [dict(path='requirements.txt', kind='file'), dict(path='dashboard_server', kind='dir'),
         dict(path='static', kind='dir'), dict(path='docs', kind='dir')]


def build_kit(directory, files=None, remove=('static/app.js',), roots=None):
    files = NEW_FILES if files is None else files
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload = make_bundle.deterministic_tar([(k, v, 0o644) for k, v in files.items()], 1763000000)
    manifest = dict(schema=kit.MANIFEST_SCHEMA, bundle_id='ab' * 6, commit='ab' * 20, commit_time_epoch=1763000000,
                    app_root='/app', managed_roots=roots or ROOTS, remove=list(remove),
                    rebuild_sources=['dashboard_server/native_raster.cc'],
                    files={k: dict(sha256=sha(v), size=len(v), mode=0o644) for k, v in files.items()},
                    payload=dict(name='payload.tar', sha256=sha(payload), bytes=len(payload)), tools={})
    (directory / 'payload.tar').write_bytes(payload)
    (directory / 'MANIFEST.json').write_bytes(json.dumps(manifest).encode())
    return directory


def jetson(clock=None, old_files=None, extra=None):
    docker = FakeDocker(clock)
    files = {'/app/' + k: v for k, v in (OLD_FILES if old_files is None else old_files).items()}
    files['/app/dashboard_server/_native_raster.so'] = b'\x7fELF'
    config = dict(Hostname='jetson', Env=['DASHBOARD_PORT=' + PORT, 'RECORDER_API_URL=http://127.0.0.1:18790',
                                          'SECRET_TOKEN=do-not-print'],
                  Cmd=['python', '-m', 'dashboard_server'], WorkingDir='/app',
                  Healthcheck=dict(Test=['CMD', 'true']), ExposedPorts={'8080/tcp': {}},
                  Labels={'com.docker.compose.service': 'dashboard'})
    image = docker.add_image(files, config, tag='unist-orachard-dashboard:latest')
    host = dict(NetworkMode='host', RestartPolicy=dict(Name='unless-stopped', MaximumRetryCount=0),
                Binds=['/home/rover/ws/runtime:/app/runtime'], Privileged=True, PidMode='host')
    layer_only = {'/app/dashboard_server/layer_only/local.json': b'{"local": true}\n'}
    layer_only.update(extra or {})
    old = docker.add_container(NAME, 'unist-orachard-dashboard:latest', config, host, extra=layer_only)
    other = docker.add_container('unist-orachard-ros2-camera-bridge',
                                 docker.add_image({}, dict(Cmd=['bridge'])), dict(Cmd=['bridge']),
                                 dict(NetworkMode='host'))
    return docker, FakeHTTP(docker), old, other


class KitCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='kit-test-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kit_dir = build_kit(self.tmp / 'kit')
        self.state = self.tmp / 'state'
        self.clock = FakeClock()
        self.docker, self.http, self.old, self.other = jetson(self.clock)
        self.out = io.StringIO()

    def run_kit(self, *args):
        argv = ['--kit-dir', str(self.kit_dir), '--state-dir', str(self.state), *args]
        return kit.main(argv, docker=self.docker, http=self.http, clock=self.clock, sleep=self.clock.sleep,
                        out=self.out)

    def run_rollback(self, *args):
        argv = ['--state-dir', str(self.state), *args]
        return rollback_dashboard.main(argv, docker=self.docker, http=self.http, clock=self.clock,
                                       sleep=self.clock.sleep, out=self.out)

    def receipt(self):
        path = Path((self.state / 'LATEST_RECEIPT').read_text(encoding='utf-8').strip())
        return json.loads(path.read_bytes())

    def files_of(self, ref):
        c = self.docker._find(ref)
        return {k[len('/app/'):]: v for k, v in c['files'].items() if k.startswith('/app/')}


# ------------------------------------------------------------------------------------------------
# tests
# ------------------------------------------------------------------------------------------------
class DryRun(KitCase):
    def test_dry_run_reports_plan_and_changes_nothing(self):
        self.assertEqual(self.run_kit(), 0, self.out.getvalue())
        self.assertEqual(self.docker.mutations(), [])
        self.assertFalse(self.state.exists())
        text = self.out.getvalue()
        self.assertIn('dashboard_server/app.py', text)
        self.assertIn('static/app/00-main.js', text)
        self.assertIn('static/app.js', text)  # deleted by the repository
        self.assertIn('dashboard_server/layer_only/local.json', text)  # writable-layer file kept
        self.assertNotIn('do-not-print', text)

    def test_dry_run_json_plan(self):
        self.assertEqual(self.run_kit('--json'), 0)
        text = self.out.getvalue()
        result = json.loads(text[text.index('\n{') + 1:])
        plan = result['plan']
        self.assertEqual(plan['changed'], ['dashboard_server/app.py', 'static/index.html'])
        self.assertEqual(plan['added'], ['dashboard_server/static_assets.py', 'static/app/00-main.js'])
        self.assertEqual(plan['remove'], ['static/app.js'])
        self.assertEqual(plan['stale'], ['dashboard_server/layer_only/local.json'])
        self.assertEqual(plan['new_dirs'], ['static/app'])

    def test_finds_dashboard_without_name(self):
        self.docker.rename(self.old, 'renamed-dashboard')
        self.docker.calls.clear()
        self.assertEqual(self.run_kit(), 0, self.out.getvalue())
        self.assertIn('renamed-dashboard', self.out.getvalue())

    def test_explicit_missing_container_fails(self):
        self.assertEqual(self.run_kit('--container', 'nope'), 2)
        self.assertIn('target_missing', self.out.getvalue())


class Apply(KitCase):
    def test_apply_replaces_container_and_keeps_old(self):
        self.assertEqual(self.run_kit('--apply'), 0, self.out.getvalue())
        new = self.docker.inspect(NAME)
        self.assertNotEqual(new['Id'], self.old)
        files = self.files_of(NAME)
        for rel, body in NEW_FILES.items():
            self.assertEqual(files[rel], body, rel)
        self.assertNotIn('static/app.js', files)
        self.assertEqual(files['dashboard_server/layer_only/local.json'], b'{"local": true}\n')
        self.assertEqual(files['dashboard_server/_native_raster.so'], b'\x7fELF')
        old = self.docker.inspect(self.old)
        self.assertTrue(old['Name'].startswith('/' + NAME + '-kept-'))
        self.assertFalse(old['State']['Running'])
        self.assertEqual(old['HostConfig']['RestartPolicy'], dict(Name='no', MaximumRetryCount=0))
        self.assertEqual(new['HostConfig']['RestartPolicy'], dict(Name='unless-stopped', MaximumRetryCount=0))
        self.assertEqual(new['Config']['Env'], self.docker.inspect(self.old)['Config']['Env'])
        self.assertEqual(new['Config']['Labels']['ubts.dashboard_kit.replaces'], self.old)
        self.assertFalse(new['Config'].get('Entrypoint'))  # the stage remover must not become the entrypoint
        self.assertEqual(self.docker.image(new['Image'])['Config']['Cmd'], ['python', '-m', 'dashboard_server'])
        new_layers = self.docker.image(new['Image'])['RootFS']['Layers']
        snap = self.receipt()['images']['snapshot']
        self.assertEqual(new_layers[:-1], self.docker.image(snap)['RootFS']['Layers'])
        r = self.receipt()
        self.assertEqual(r['result'], 'installed')
        self.assertTrue(r['ok'])
        self.assertEqual({c['path'] for c in r['plan']['changed']}, {'dashboard_server/app.py', 'static/index.html'})
        self.assertEqual(r['plan']['changed'][0]['after'], sha(NEW_FILES['dashboard_server/app.py']))
        self.assertEqual(r['plan']['changed'][0]['before'], sha(OLD_FILES['dashboard_server/app.py']))
        self.assertEqual(r['static_check']['files'], 3)
        self.assertEqual(r['unrelated_containers']['changed'], [])
        raw = (self.state / 'LATEST_RECEIPT').read_text(encoding='utf-8')
        self.assertNotIn('do-not-print', Path(raw.strip()).read_text(encoding='utf-8'))
        commit_calls = [c for c in self.docker.calls if c[0] == 'commit']
        self.assertTrue(commit_calls[0][4])  # snapshot pauses the running container
        self.assertEqual(self.docker.inspect(self.other)['State']['StartedAt'], '2026-11-13T00:00:02Z')

    def test_second_apply_is_nothing_to_do(self):
        self.assertEqual(self.run_kit('--apply'), 0)
        self.docker.calls.clear()
        self.assertEqual(self.run_kit('--apply'), 0, self.out.getvalue())
        self.assertEqual(self.docker.mutations(), [])
        self.assertEqual(self.receipt()['result'], 'nothing_to_do')

    def test_not_idle_aborts_before_any_change(self):
        cases = [('rover', dict(ok=True, armed=True, moving=False)), ('rover', dict(ok=True, armed=False, moving=True)),
                 ('rover', dict(ok=True)), ('recorder', dict(state='recording', active=True)),
                 ('recorder', dict(state='idle', active=False, finalizing=True)),
                 ('recorder', dict(ok=False, state='not_connected', active=None)),
                 ('jobs', [dict(id='archive:x', active=True)]), ('jobs', [dict(id='live')]),
                 ('supervisor', dict(active=True))]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.setUp()
                setattr(self.http, field, value)
                code = self.run_kit('--apply')
                self.assertEqual(code, 1, self.out.getvalue())
                self.assertEqual(self.docker.mutations(), [])
                self.assertEqual(self.receipt()['result'], 'aborted_no_change')
                self.assertEqual(self.receipt()['error']['code'], 'not_idle')
                self.assertEqual(self.run_kit(), 2)

    def test_bridge_status_file_armed_aborts(self):
        self.docker._find(self.old)['files']['/app/runtime/rover_bridge_status.json'] = b'{"armed": true, "moving": false}'
        self.assertEqual(self.run_kit('--apply'), 1)
        self.assertEqual(self.docker.mutations(), [])

    def test_rover_bridge_unavailable_is_idle_and_recorder_option(self):
        self.http.rover = dict(ok=False, connected=False, state='unavailable', error='motor_bridge_unavailable')
        self.http.recorder = dict(ok=False, state='not_connected', active=None)
        self.assertEqual(self.run_kit(), 2)
        self.assertEqual(self.run_kit('--recorder-unavailable-ok'), 0, self.out.getvalue())

    def test_ready_failure_rolls_back_automatically(self):
        def break_new(cid):
            c = self.docker._find(cid)
            if c['Id'] != self.old and c['Config'].get('Cmd') == ['python', '-m', 'dashboard_server']:
                self.http.broken_health.add(c['Id'])
        self.docker.on_call['start'] = break_new
        self.assertEqual(self.run_kit('--apply', '--ready-timeout', '5'), 1, self.out.getvalue())
        r = self.receipt()
        self.assertEqual(r['result'], 'failed_rolled_back')
        self.assertTrue(r['rollback']['ok'])
        cur = self.docker.inspect(NAME)
        self.assertEqual(cur['Id'], self.old)
        self.assertTrue(cur['State']['Running'])
        self.assertEqual(cur['HostConfig']['RestartPolicy'], dict(Name='unless-stopped', MaximumRetryCount=0))
        failed = self.docker.inspect(r['containers']['new']['id'])
        self.assertTrue(failed['Name'].startswith('/' + NAME + '-failed-'))
        self.assertFalse(failed['State']['Running'])
        self.assertEqual(failed['HostConfig']['RestartPolicy']['Name'], 'no')
        self.assertEqual(self.files_of(NAME)['dashboard_server/app.py'], OLD_FILES['dashboard_server/app.py'])

    def test_static_mismatch_rolls_back(self):
        def tamper(cid):
            if cid != self.old and self.docker._find(cid)['Config'].get('Cmd') == ['python', '-m', 'dashboard_server']:
                self.http.tamper_static.add(cid)
        self.docker.on_call['start'] = tamper
        self.assertEqual(self.run_kit('--apply'), 1)
        r = self.receipt()
        self.assertEqual(r['error']['code'], 'static_mismatch')
        self.assertEqual(r['result'], 'failed_rolled_back')
        self.assertEqual(self.docker.inspect(NAME)['Id'], self.old)

    def test_preflight_failure_aborts_without_touching_old(self):
        self.docker.preflight_exit = 1
        self.assertEqual(self.run_kit('--apply'), 1)
        r = self.receipt()
        self.assertEqual(r['result'], 'aborted_no_change')
        self.assertEqual(r['error']['code'], 'preflight_import_failed')
        self.assertIn('scipy', r['preflight_log_tail'])
        touched = [c for c in self.docker.mutations() if c[0] in ('stop', 'rename', 'set_restart') and c[1] == self.old]
        self.assertEqual(touched, [])
        self.assertTrue(self.docker.inspect(NAME)['State']['Running'])

    def test_deadline_before_switch_leaves_old_untouched(self):
        self.docker.on_call['commit'] = lambda *a: setattr(self.clock, 't', self.clock.t + 300)
        self.assertEqual(self.run_kit('--apply'), 1)
        r = self.receipt()
        self.assertEqual(r['error']['code'], 'deadline')
        self.assertEqual(r['result'], 'aborted_no_change')
        self.assertEqual([c for c in self.docker.mutations() if c[1] == self.old and c[0] != 'commit'], [])

    def test_ready_timeout_is_bounded_and_rolls_back(self):
        self.docker.on_call['start'] = lambda cid: self.http.broken_health.add(cid) if cid != self.old else None
        before = self.clock.t
        self.assertEqual(self.run_kit('--apply', '--ready-timeout', '30'), 1)
        self.assertLess(self.clock.t - before, 30 + 90 + 5)
        self.assertEqual(self.receipt()['error']['code'], 'ready_timeout')
        self.assertEqual(self.docker.inspect(NAME)['Id'], self.old)

    def test_requirements_change_is_blocked(self):
        files = dict(NEW_FILES, **{'requirements.txt': b'numpy\nscipy\n'})
        self.kit_dir = build_kit(self.tmp / 'kit-req', files)
        self.assertEqual(self.run_kit(), 2)
        self.assertIn('requirements_changed', self.out.getvalue())
        self.assertEqual(self.run_kit('--apply'), 1)
        self.assertEqual(self.receipt()['error']['code'], 'blocked')
        self.assertEqual(self.docker.mutations(), [])

    def test_native_raster_change_is_blocked(self):
        files = dict(NEW_FILES, **{'dashboard_server/native_raster.cc': b'// new raster\n'})
        self.kit_dir = build_kit(self.tmp / 'kit-raster', files)
        self.assertEqual(self.run_kit(), 2)
        self.assertIn('native_raster_source_changed', self.out.getvalue())

    def test_line_endings_only_rebuild_sources_are_not_blocked(self):
        # The installed image was built from a CRLF checkout: the same source text with other bytes.
        crlf = dict(OLD_FILES)
        for rel in ('requirements.txt', 'dashboard_server/native_raster.cc'):
            crlf[rel] = OLD_FILES[rel].replace(b'\n', b'\r\n')
        self.docker, self.http, self.old, self.other = jetson(self.clock, old_files=crlf)
        self.assertEqual(self.run_kit(), 0, self.out.getvalue())
        text = self.out.getvalue()
        self.assertIn('line_endings_only:requirements_changed', text)
        self.assertIn('line_endings_only:native_raster_source_changed', text)
        self.assertEqual(self.run_kit('--apply'), 0, self.out.getvalue())
        self.assertEqual(self.receipt()['result'], 'installed')

    def test_line_endings_plus_text_change_is_still_blocked(self):
        crlf = dict(OLD_FILES, **{'dashboard_server/native_raster.cc': b'// raster\r\n'})
        files = dict(NEW_FILES, **{'dashboard_server/native_raster.cc': b'// new raster\n'})
        self.kit_dir = build_kit(self.tmp / 'kit-raster-crlf', files)
        self.docker, self.http, self.old, self.other = jetson(self.clock, old_files=crlf)
        self.assertEqual(self.run_kit(), 2)
        self.assertIn('native_raster_source_changed', self.out.getvalue())
        self.assertEqual(self.run_kit('--apply'), 1)
        self.assertEqual(self.docker.mutations(), [])

    def test_esp_disconnected_fault_is_idle(self):
        self.http.rover = dict(ok=False, connected=False, state='fault', armed=False, moving=False,
                               reason='hardware_fault', last_error='ESP disconnected')
        self.assertEqual(self.run_kit(), 0, self.out.getvalue())

    def test_disconnected_but_armed_moving_or_unknown_is_not_idle(self):
        cases = (dict(ok=False, connected=False, state='fault', armed=True, moving=False),
                 dict(ok=False, connected=False, state='fault', armed=False, moving=True),
                 dict(ok=False, connected=False, state='fault'),
                 dict(ok=False, connected=True, state='fault', armed=False, moving=False))
        for rover in cases:
            with self.subTest(rover=rover):
                self.http.rover = rover
                self.assertEqual(self.run_kit(), 2, self.out.getvalue())
                self.assertEqual(self.run_kit('--apply'), 1)
                self.assertEqual(self.docker.mutations(), [])

    def test_tampered_bundle_is_refused(self):
        payload = self.kit_dir / 'payload.tar'
        data = bytearray(payload.read_bytes())
        data[700] ^= 1
        payload.write_bytes(bytes(data))
        self.assertEqual(self.run_kit('--apply'), 2)
        self.assertIn('bundle_payload_sha256', self.out.getvalue())
        self.assertEqual(self.docker.mutations(), [])

    def test_options_are_bounded(self):
        self.assertEqual(self.run_kit('--deadline', '5'), 2)
        self.assertEqual(self.run_kit('--ready-timeout', '0'), 2)
        self.assertEqual(self.run_kit('--image-repo', 'Bad Repo'), 2)


class Rollback(KitCase):
    def test_rollback_restores_kept_container(self):
        self.assertEqual(self.run_kit('--apply'), 0)
        installed = self.docker.inspect(NAME)['Id']
        self.docker.calls.clear()
        self.assertEqual(self.run_rollback(), 0, self.out.getvalue())
        self.assertEqual(self.docker.mutations(), [])
        self.assertEqual(self.run_rollback('--apply'), 0, self.out.getvalue())
        cur = self.docker.inspect(NAME)
        self.assertEqual(cur['Id'], self.old)
        self.assertTrue(cur['State']['Running'])
        self.assertEqual(cur['HostConfig']['RestartPolicy'], dict(Name='unless-stopped', MaximumRetryCount=0))
        gone = self.docker.inspect(installed)
        self.assertTrue(gone['Name'].startswith('/' + NAME + '-rolledback-'))
        self.assertFalse(gone['State']['Running'])
        self.assertEqual(gone['HostConfig']['RestartPolicy']['Name'], 'no')
        run = Path((self.state / 'LATEST_RECEIPT').read_text(encoding='utf-8').strip()).parent
        result = json.loads(next(run.glob('ROLLBACK_*.json')).read_bytes())
        self.assertTrue(result['ok'])
        self.assertEqual(self.run_rollback(), 2)  # nothing left to roll back

    def test_rollback_refuses_when_another_container_has_the_name(self):
        self.assertEqual(self.run_kit('--apply'), 0)
        installed = self.docker.inspect(NAME)['Id']
        self.docker.rename(installed, 'moved-away')
        self.docker.add_container(NAME, self.docker.inspect(self.old)['Image'],
                                  dict(Env=['DASHBOARD_PORT=19999'], Cmd=['python', '-m', 'dashboard_server'],
                                       WorkingDir='/app'), dict(NetworkMode='host'))
        self.assertEqual(self.run_rollback('--apply'), 2)
        self.assertIn('current_is_not_this_install', self.out.getvalue())

    def test_rollback_uses_direct_checks_when_dashboard_does_not_answer(self):
        self.assertEqual(self.run_kit('--apply'), 0)
        installed = self.docker.inspect(NAME)['Id']
        self.docker._find(installed)['files']['/app/runtime/rover_bridge_status.json'] = b'{"armed": false, "moving": false}'
        self.http.down.add(installed)
        self.assertEqual(self.run_rollback('--apply'), 0, self.out.getvalue())
        self.assertIn('recorder=idle(direct)', self.out.getvalue())
        self.assertEqual(self.docker.inspect(NAME)['Id'], self.old)

    def test_rollback_fallback_refuses_without_bridge_file(self):
        self.assertEqual(self.run_kit('--apply'), 0)
        installed = self.docker.inspect(NAME)['Id']
        self.http.down.add(installed)
        self.assertEqual(self.run_rollback('--apply'), 2)
        self.assertIn('rover bridge status file not readable', self.out.getvalue())
        self.assertEqual(self.docker.inspect(NAME)['Id'], installed)


class SourcePolicy(unittest.TestCase):
    SOURCES = ('install_dashboard.py', 'rollback_dashboard.py')

    def strings_and_names(self, name):
        tree = ast.parse((KIT / name).read_text(encoding='utf-8'))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node, clean=False):
                docstrings.add(id(node.body[0].value))
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and id(n) not in docstrings]
        names = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
        names += [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
        imports = [a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names]
        imports += [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
        return strings, names, imports

    def test_no_compose_prune_pull_remove_or_subprocess(self):
        for name in self.SOURCES:
            strings, names, imports = self.strings_and_names(name)
            text = ' '.join(strings).lower()
            for word in ('compose', 'prune', '/images/create', 'pull=1', 'pull=true', '/build', 'delete'):
                self.assertNotIn(word, text, (name, word))
            for word in ('subprocess', 'os.system', 'popen'):
                self.assertNotIn(word, ' '.join(imports + names).lower(), (name, word))
            self.assertFalse([n for n in names if n.lower() in ('rmi', 'prune', 'remove_container', 'remove_image')])
            methods = [s for s in strings if s in ('GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'PATCH')]
            self.assertNotIn('DELETE', methods, name)

    def test_docker_api_has_only_bounded_calls(self):
        methods = [m for m in dir(kit.DockerAPI) if not m.startswith('__')]
        for word in ('remove', 'prune', 'pull', 'delete', 'build', 'exec'):
            self.assertFalse([m for m in methods if word in m.lower()], word)
        tree = ast.parse((KIT / 'install_dashboard.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == '_UnixConnection':
                self.assertEqual(len(node.args), 2, 'every Docker connection has a timeout')
        self.assertGreater(kit.DOCKER_CALL_TIMEOUT_S, 0)
        self.assertLessEqual(kit.INSTALL_DEADLINE_S + kit.ROLLBACK_RESERVE_S, 900)

    def test_http_ignores_environment_proxies(self):
        # An empty ProxyHandler keeps build_opener from adding the default one that reads http_proxy.
        self.assertFalse([h for h in kit._OPENER.handlers if getattr(h, 'proxies', None)])
        source = (KIT / 'install_dashboard.py').read_text(encoding='utf-8')
        self.assertIn('build_opener(urllib.request.ProxyHandler({})', source)
        self.assertTrue([h for h in kit._OPENER.handlers if type(h).__name__ == 'HTTPCookieProcessor'])

    def test_python_310_compatible_syntax(self):
        for name in self.SOURCES + ('make_bundle.py',):
            source = (KIT / name).read_text(encoding='utf-8')
            ast.parse(source, feature_version=(3, 10))
            self.assertNotIn('\r', source)
            self.assertNotIn('datetime.UTC', source)

    def test_tar_digest_round_trip_and_overlay_modes(self):
        bundle = type('B', (), {})()
        bundle.data = {'static/a.js': b'a();\n', 'x/run.sh': b'#!/bin/sh\n'}
        bundle.files = {'static/a.js': dict(mode=0o644), 'x/run.sh': dict(mode=0o755)}
        raw = kit.overlay_tar(bundle, ['static/a.js', 'x/run.sh'], ['x'], 1763000000)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            members = {m.name: m for m in archive.getmembers()}
        self.assertEqual(members['x/run.sh'].mode, 0o755)
        self.assertEqual(members['static/a.js'].uid, 0)
        self.assertTrue(members['x'].isdir())
        out = kit.tar_digest(io.BytesIO(raw), '/app')
        self.assertEqual(out['static/a.js'], ('file', sha(b'a();\n'), 5))
        dot = io.BytesIO()
        with tarfile.open(fileobj=dot, mode='w') as archive:
            info = tarfile.TarInfo('./static/.hidden')
            info.size = 1
            archive.addfile(info, io.BytesIO(b'h'))
        dot.seek(0)
        self.assertIn('static/.hidden', kit.tar_digest(dot, '/app'))

    def test_commit_body_keeps_the_stage_entrypoint_out_of_the_image(self):
        snapshot = dict(Cmd=['python', '-m', 'dashboard_server'], Entrypoint=None, WorkingDir='/app',
                        Labels={'a': '1'}, Env=['A=1'])
        stage = dict(snapshot, Entrypoint=['python3', '-c', kit.REMOVE_CODE], Cmd=['/app/static/app.js'])
        leaked = FakeDocker.merge(copy.deepcopy(snapshot), stage)
        self.assertEqual(leaked['Entrypoint'][:2], ['python3', '-c'])  # what a plain body would do
        fixed = FakeDocker.merge(kit.commit_config(snapshot), stage)
        self.assertEqual(fixed['Entrypoint'], [])
        self.assertEqual(fixed['Cmd'], ['python', '-m', 'dashboard_server'])
        self.assertEqual(kit.image_config_diff(snapshot, fixed), [])
        self.assertEqual(kit.image_config_diff(snapshot, leaked), ['Entrypoint'])
        self.assertEqual(kit.spec_diff(dict(Config=dict(Entrypoint=None)), dict(Config=dict(Entrypoint=[]))), [])

    def test_preflight_imports_what_create_server_imports(self):
        imported = []
        fake = type(sys)('importlib')
        fake.import_module = imported.append
        out = io.StringIO()
        root = str(Path(__file__).resolve().parents[1])
        with unittest.mock.patch.dict(sys.modules, {'importlib': fake}), \
                unittest.mock.patch.dict('os.environ', {'UBTS_KIT_APP_ROOT': root}), \
                unittest.mock.patch.object(sys, 'argv', ['-c', 'dashboard_server.app']), \
                unittest.mock.patch.object(sys, 'path', list(sys.path)), \
                unittest.mock.patch('sys.stdout', out):
            exec(compile(kit.IMPORT_CODE, 'preflight', 'exec'), {'__name__': '__main__'})
        self.assertEqual(imported[0], 'dashboard_server.app')
        for module in ('dashboard_server.source_live_mapping', 'dashboard_server.mapping_operations',
                       'dashboard_server.static_assets', 'dashboard_server.telemetry'):
            self.assertIn(module, imported)
        self.assertEqual(len(imported), len(set(imported)))
        self.assertIn('UBTS_KIT_IMPORT_OK', out.getvalue())

    def test_strip_versions(self):
        self.assertEqual(kit.strip_versions(b'import a from "./a.mjs?v=0123456789";'), b'import a from "./a.mjs";')
        self.assertEqual(kit.strip_versions(b'url(/static/f.woff2?v=abcdefabcd)'), b'url(/static/f.woff2)')
        self.assertEqual(kit.strip_versions(b'"?v=0123"'), b'"?v=0123"')

    def test_demux_logs(self):
        frame = b'\x01\x00\x00\x00\x00\x00\x00\x03abc' + b'\x02\x00\x00\x00\x00\x00\x00\x02de'
        self.assertEqual(kit.demux_logs(frame), 'abcde')
        self.assertEqual(kit.demux_logs(b'plain'), 'plain')


def git(repo, *args):
    return subprocess.run(['git', '-c', 'core.autocrlf=true', '-C', str(repo), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


@unittest.skipIf(shutil.which('git') is None, 'git not installed')
class BundleMaker(unittest.TestCase):
    DOCKERFILE = (b'FROM python:3.11-slim AS raster-builder\n'
                  b'COPY dashboard_server/native_raster.cc /build/native_raster.cc\n'
                  b'RUN g++ -shared /build/native_raster.cc -o /build/_native_raster.so\n\n'
                  b'FROM python:3.11-slim\nWORKDIR /app\nCOPY requirements.txt ./\nRUN pip install -r requirements.txt\n'
                  b'COPY dashboard_server ./dashboard_server\n'
                  b'COPY --from=raster-builder /build/_native_raster.so ./dashboard_server/_native_raster.so\n'
                  b'COPY static ./static\nCOPY scripts/__init__.py ./scripts/__init__.py\nCMD ["python"]\n')

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='kit-bundle-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        repo = self.repo = self.tmp / 'repo'
        repo.mkdir()
        git(repo, 'init', '-q')
        git(repo, 'config', 'user.name', 'kit-test')
        git(repo, 'config', 'user.email', 'kit-test@example.invalid')
        files = {
            'Dockerfile': self.DOCKERFILE,
            '.dockerignore': b'.git/\n**/__pycache__\n*.log\n',
            '.gitattributes': b'*.py text eol=lf\n*.js text eol=lf\n',
            'requirements.txt': b'numpy\n',
            'dashboard_server/__init__.py': b'',
            'dashboard_server/app.py': b'A = 1\nB = 2\n',
            'dashboard_server/native_raster.cc': b'// c\n',
            'dashboard_server/__pycache__/junk.pyc': b'junk',
            'static/index.html': b'<html></html>\n',
            'static/old.js': b'old();\n',
            'scripts/__init__.py': b'',
            'scripts/bridge.py': b'import helper\nfrom scripts import policy\n',
            'scripts/helper.py': b'X = 1\n',
            'scripts/policy.py': b'Y = 1\n',
            'Dockerfile.ros2': b'FROM ros\nWORKDIR /app\nCOPY scripts/__init__.py ./scripts/__init__.py\n'
                               b'COPY scripts/bridge.py scripts/policy.py ./scripts/\n',
            'deploy/jetson_dashboard/README_KO.md': b'# kit\n',
        }
        for rel, body in files.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        git(repo, 'add', '-A')
        git(repo, 'add', '-f', 'dashboard_server/__pycache__/junk.pyc')
        git(repo, 'commit', '-q', '-m', 'base')
        git(repo, 'tag', 'installed')
        (repo / 'static/old.js').unlink()
        (repo / 'static/app').mkdir()
        (repo / 'static/app/00.js').write_bytes(b'new();\n')
        (repo / 'dashboard_server/app.py').write_bytes(b'A = 3\n')
        (repo / 'scripts/helper.py').write_bytes(b'X = 2\n')
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '-m', 'ui')

    def test_bundle_contents_lf_remove_and_notes(self):
        result = make_bundle.make(self.repo, 'HEAD', 'installed', self.tmp / 'out1')
        m = result['manifest']
        self.assertEqual(sorted(m['files']), ['dashboard_server/__init__.py', 'dashboard_server/app.py',
                                              'dashboard_server/native_raster.cc', 'requirements.txt',
                                              'scripts/__init__.py', 'static/app/00.js', 'static/index.html'])
        self.assertEqual(m['files']['dashboard_server/app.py']['sha256'], sha(b'A = 3\n'))
        self.assertEqual(m['remove'], ['static/old.js'])
        self.assertEqual(m['generated_in_image'], ['dashboard_server/_native_raster.so'])
        self.assertEqual(m['rebuild_sources'], ['dashboard_server/native_raster.cc'])
        self.assertEqual(m['dockerignore_excluded'], ['dashboard_server/__pycache__/junk.pyc'])
        missing = m['notes']['dockerfile_ros2_imports']['missing_imports']
        self.assertEqual(missing, [dict(file='scripts/helper.py', imported_by=['scripts/bridge.py'])])
        self.assertEqual(m['baseline']['not_installed_by_this_kit'], {'ros_bridge_images': ['M scripts/helper.py']})
        folder = Path(result['folder'])
        with tarfile.open(folder / 'payload.tar') as archive:
            body = archive.extractfile('dashboard_server/app.py').read()
        self.assertEqual(body, b'A = 3\n')
        sums = (folder / 'SHA256SUMS').read_text(encoding='ascii')
        self.assertIn(sha((folder / 'MANIFEST.json').read_bytes()) + '  MANIFEST.json', sums)
        with tarfile.open(result['archive'], 'r:gz') as archive:
            names = archive.getnames()
        self.assertIn('ubts-dashboard-kit-%s/install_dashboard.py' % m['bundle_id'], names)
        again = make_bundle.make(self.repo, 'HEAD', 'installed', self.tmp / 'out2')
        self.assertEqual(again['manifest']['payload']['sha256'], m['payload']['sha256'])
        with self.assertRaises(make_bundle.BundleError):
            make_bundle.make(self.repo, 'HEAD', 'installed', self.tmp / 'out1')

    def test_bundle_is_accepted_by_installer_end_to_end(self):
        result = make_bundle.make(self.repo, 'HEAD', 'installed', self.tmp / 'out')
        bundle = kit.Bundle(result['folder'])
        self.assertEqual(bundle.remove, ['static/old.js'])
        self.assertEqual(bundle.tool_mismatch, [])
        clock = FakeClock()
        base_files = {}
        for rel in ('requirements.txt', 'dashboard_server/__init__.py', 'dashboard_server/app.py',
                    'dashboard_server/native_raster.cc', 'static/index.html', 'static/old.js', 'scripts/__init__.py'):
            base_files[rel] = git(self.repo, 'show', 'installed:' + rel)
        docker, http, old, _ = jetson(clock, old_files=base_files)
        out = io.StringIO()
        code = kit.main(['--kit-dir', result['folder'], '--state-dir', str(self.tmp / 'state'), '--apply'],
                        docker=docker, http=http, clock=clock, sleep=clock.sleep, out=out)
        self.assertEqual(code, 0, out.getvalue())
        files = {k[5:]: v for k, v in docker._find(NAME)['files'].items()}
        self.assertEqual(files['dashboard_server/app.py'], b'A = 3\n')
        self.assertEqual(files['static/app/00.js'], b'new();\n')
        self.assertNotIn('static/old.js', files)

    def test_dockerfile_change_needs_explicit_permission(self):
        (self.repo / 'Dockerfile').write_bytes(self.DOCKERFILE.replace(b'CMD ["python"]', b'ENV X=1\nCMD ["python"]'))
        git(self.repo, 'commit', '-q', '-am', 'env')
        with self.assertRaises(make_bundle.BundleError):
            make_bundle.make(self.repo, 'HEAD', 'installed', self.tmp / 'out3')
        result = make_bundle.make(self.repo, 'HEAD', 'installed', self.tmp / 'out4', allow_dockerfile_change=True)
        self.assertTrue(result['manifest']['notes']['dockerfile_changed_since_baseline'])

    def test_dockerignore_rules(self):
        rules = make_bundle.parse_dockerignore('.git/\n**/__pycache__\n*.log\n!keep.log\ndocs/*.png\n')
        self.assertTrue(make_bundle.ignored('a/b/__pycache__/x.pyc', rules))
        self.assertTrue(make_bundle.ignored('x.log', rules))
        self.assertFalse(make_bundle.ignored('keep.log', rules))
        self.assertFalse(make_bundle.ignored('static/x.log', rules))
        self.assertTrue(make_bundle.ignored('docs/a.png', rules))
        self.assertFalse(make_bundle.ignored('docs/sub/a.png', rules))


if __name__ == '__main__':
    unittest.main()
