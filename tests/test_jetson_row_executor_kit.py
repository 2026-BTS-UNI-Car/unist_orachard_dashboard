"""The row executor kit (deploy/jetson_row_executor/) with a fake Docker API and a fake host. No Docker, no ROS, no SSH.
Checks: the dry run changes nothing; refusals (a motor config in shadow mode, a missing image, the port in use, a
tampered kit, a package whose SHA256SUMS does not match); the shadow install (no --motor-output, no control socket
mount, loopback operator page, token file next to the link file and never printed, staged files, the field tuning copy
only when missing), the link file only after the executor is ready, a failed start restores the earlier executor and
puts nothing in the dashboard's way, the two start-up races of the ready check (an empty status before the executor's
first loop iteration, a 401/403 while the token file still holds the earlier token) are waited out until the deadline
while a real run_state other than idle still fails at once, the rollback, the kit builder from two commits, and that the tools have no
remove / prune / pull / build / compose / subprocess (the installer) calls.
Run: python -B -m unittest tests.test_jetson_row_executor_kit
"""
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT_SRC = ROOT / 'deploy' / 'jetson_row_executor'


def load(name):
    spec = importlib.util.spec_from_file_location(name, KIT_SRC / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inst = load('install_row_executor')
make = load('make_executor_kit')
SIM_COMMIT = '1' * 40
DASH_ID = 'd' * 64
LIDAR_ID = 'c' * 64
TOKEN = 'tok-' + 'z' * 24


def sha(data):
    return hashlib.sha256(data).hexdigest()


def shadow_config(**over):
    value = {'vehicle_profile': 'front_steer', 'steer_bridge_kind': 'esp32_abs_hold', 'bind_host': '127.0.0.1',
             'bind_port': 8097, 'socket_path': '/run/ubts-rover/control.sock', 'motor_output_enabled': False,
             'tuning_file': '/var/lib/row_executor/field_tuning.json'}
    value.update(over)
    return (json.dumps(value, indent=1, sort_keys=True) + '\n').encode('utf-8')


def write_kit(folder, tamper=None, bad_sums=False):
    package = {'executor_main.py': b'# executor\n', 'field_tuning.default.json': b'{"schema": "ubts.field_tuning.v1"}\n',
               'config.vehicle_esp32_shadow.json': shadow_config(), 'tests/test_x.py': b'# test\n'}
    sums = ''.join('%s  %s\n' % (sha(data), rel) for rel, data in sorted(package.items()))
    if bad_sums:
        sums = sums.replace(sha(package['executor_main.py']), '0' * 64)
    files = {'row_executor/' + rel: data for rel, data in package.items()}
    files['row_executor/SHA256SUMS'] = sums.encode()
    files['config/config.vehicle_esp32_shadow.json'] = package['config.vehicle_esp32_shadow.json']
    manifest = {'schema': inst.MANIFEST_SCHEMA, 'sim_commit': SIM_COMMIT, 'dash_commit': '2' * 40,
                'files': {rel: sha(data) for rel, data in files.items()}}
    for rel, data in files.items():
        path = Path(folder) / 'payload' / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if rel != tamper else data + b'x')
    (Path(folder) / 'MANIFEST.json').write_bytes(json.dumps(manifest).encode())
    return folder


class FakeHost:
    def __init__(self, home):
        self._home = home
        self.files = {}
        self.dirs = {'/run/ubts-rover'}
        self.port_open = False
        self.status = {'run_state': 'idle', 'mode': 'SHADOW', 'lidar_age': None, 'imu_age': None,
                       'vehicle_control': 'none', 'feasibility': {'status': 'unknown'}}
        self.rates = (10, 33)            # messages per status read (the installer polls once per second)
        self.counts = [0, 0]
        self.http_calls = []
        self.empty_replies = 0           # replies of the operator API before the loop's first status ({}, 2026-11-16)

    def home(self):
        return self._home

    def exists(self, path):
        return path in self.files or path in self.dirs

    def isdir(self, path):
        return path in self.dirs

    def makedirs(self, path):
        self.dirs.add(path)

    def write_bytes(self, path, data, mode=0o644):
        self.files[path] = bytes(data)

    def read_bytes(self, path, limit=1024 * 1024):
        return self.files.get(path)

    def rename(self, source, target):
        if source not in self.files:
            raise OSError('missing %s' % source)
        self.files[target] = self.files.pop(source)

    def port_free(self, host, port):
        return not self.port_open

    def http_status(self, port, token, timeout=2.):
        self.http_calls.append((port, token))
        if token != TOKEN:
            return 403, {'ok': False, 'error': 'forbidden'}
        if self.empty_replies:
            self.empty_replies -= 1
            return 200, {}
        self.counts = [self.counts[0] + self.rates[0], self.counts[1] + self.rates[1]]
        status = dict(self.status, counts=dict(scans_processed=self.counts[0], imu_fed=self.counts[1]))
        return 200, status


def container(cid, name, image, env=(), mounts=(), running=True, host_config=None):
    return {'Id': cid, 'Name': '/' + name, 'Image': image, 'Config': {'Env': list(env)},
            'HostConfig': host_config or {'RestartPolicy': {'Name': 'unless-stopped'}, 'NetworkMode': 'host',
                                          'IpcMode': 'host'},
            'Mounts': [{'Type': 'bind', 'Source': s, 'Destination': d} for s, d in mounts],
            'State': {'Status': 'running' if running else 'exited', 'Running': running}}


class FakeDocker:
    def __init__(self):
        self.containers = {
            DASH_ID: container(DASH_ID, inst.DASHBOARD_NAME, 'sha256:' + 'e' * 64, ['DASHBOARD_PORT=8080'],
                               [('/home/unist/ws/runtime', '/app/runtime'), ('/run/ubts-rover', '/run/ubts-rover')]),
            LIDAR_ID: container(LIDAR_ID, inst.ROS_REFERENCE, 'sha256:' + 'b' * 64, ['ROS_DOMAIN_ID=0'])}
        self.images = {inst.IMAGE: {'Id': inst.IMAGE, 'Architecture': 'arm64', 'RepoTags': ['ubts-c0:x'],
                                    'Config': {'Env': ['ROS_DISTRO=iron']}}}
        self.calls = []
        self.n = 0
        self.preflight_log = None
        self.start_fails = False
        self.exit_after_start = False
        self.old_tokens = []             # token file reads before the new executor wrote its own token

    def _find(self, ref):
        for cid, info in self.containers.items():
            if ref == cid or ref == info['Name'].lstrip('/'):
                return cid
        return None

    def inspect(self, ref):
        cid = self._find(ref)
        return json.loads(json.dumps(self.containers[cid])) if cid else None

    def image(self, ref):
        return self.images.get(ref)

    def create(self, name, spec):
        self.calls.append(('create', name))
        assert self._find(name) is None
        self.n += 1
        cid = ('%x' % (self.n + 9)) * 64
        self.containers[cid[:64]] = dict(container(cid[:64], name, spec['Image'], spec.get('Env', ()), running=False,
                                                   host_config=dict(spec['HostConfig'])), Spec=spec)
        return cid[:64]

    def read_file(self, cid, path, max_bytes=0):
        self.calls.append(('read_file', path))
        if not path.endswith('operator_token.txt'):
            return None
        return ((self.old_tokens.pop(0) if self.old_tokens else TOKEN) + '\n').encode()

    def start(self, cid):
        self.calls.append(('start', self.containers[cid]['Name']))
        if self.start_fails and 'preflight' not in self.containers[cid]['Name']:
            raise inst.KitError('docker_api', 'start failed')
        self.containers[cid]['State'] = {'Status': 'running', 'Running': True}
        if self.exit_after_start and 'Spec' in self.containers[cid] and 'preflight' not in self.containers[cid]['Name']:
            self.containers[cid]['State'] = {'Status': 'exited', 'Running': False, 'ExitCode': 2}

    def stop(self, cid, grace=10):
        self.calls.append(('stop', self.containers[cid]['Name']))
        self.containers[cid]['State'] = {'Status': 'exited', 'Running': False}

    def wait(self, cid, timeout):
        self.containers[cid]['State'] = {'Status': 'exited', 'Running': False, 'ExitCode': 0}
        return 0

    def rename(self, cid, name):
        self.calls.append(('rename', self.containers[cid]['Name'], name))
        self.containers[cid]['Name'] = '/' + name

    def set_restart(self, cid, policy):
        self.calls.append(('restart', self.containers[cid]['Name'], policy['Name']))
        self.containers[cid]['HostConfig']['RestartPolicy'] = policy

    def logs(self, cid, tail=60):
        if 'preflight' in self.containers[cid]['Name']:
            if self.preflight_log is not None:
                return self.preflight_log
            motor = 'UBTS_KIT_MODE=motor' in self.containers[cid]['Config']['Env']
            return 'x\n%s %s\n' % (inst.MARKER, json.dumps({'python': '3.10.12', 'motor_allowed': motor,
                                                             'feasibility': 'unknown'}))
        return 'operator page: http://127.0.0.1:8097/ (token in /run/ubts-row-executor-link/operator_token.txt)\n'

    def by_name(self, name):
        cid = self._find(name)
        return self.containers[cid] if cid else None


class KitCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ubts-rowexec-kit-')
        self.kit = write_kit(os.path.join(self.tmp.name, 'kit'))
        self.state = os.path.join(self.tmp.name, 'state')
        self.docker = FakeDocker()
        self.host = FakeHost('/home/unist')
        self.clock = [0.]

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *args):
        out = io.StringIO()
        code = inst.main(['--kit-dir', self.kit, '--state-dir', self.state, '--ready-timeout', '5', *args],
                         docker=self.docker, host=self.host, out=out)
        return code, out.getvalue()

    def install(self, *args):
        options = inst.build_parser().parse_args(['--apply', '--kit-dir', self.kit, '--ready-timeout', '5', *args])
        installer = inst.Installer(self.docker, self.host, inst.Kit(self.kit), options,
                                   clock=lambda: self.clock[0], sleep=lambda s: self.clock.__setitem__(0, self.clock[0] + s),
                                   out=io.StringIO())
        self.installs = getattr(self, 'installs', 0) + 1
        installer.stamp += 'n%d' % self.installs        # two installs inside one second get their own names
        return installer.apply(), installer

    def link(self):
        return '/home/unist/ws/runtime/row_executor/dashboard_link.json'


class DryRunAndRefusals(KitCase):
    def test_dry_run_changes_nothing(self):
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertIn('점검 통과', out)
        self.assertEqual([c for c in self.docker.calls if c[0] != 'read_file'], [])
        self.assertEqual(self.host.files, {})
        report = json.loads(out[:out.rindex('}') + 1])
        self.assertNotIn('/run/ubts-rover:/run/ubts-rover:rw', report['plan']['binds'])
        self.assertNotIn('--motor-output', report['plan']['cmd'][-1])

    def test_refusals(self):
        self.docker.images = {}
        self.assertEqual(self.run_main()[0], 2)
        self.docker = FakeDocker()
        self.host.port_open = True
        code, out = self.run_main('--apply')
        self.assertEqual(code, 2)
        self.assertIn('port_in_use', out)
        self.host.port_open = False
        code, out = self.run_main('--apply', '--mode', 'motor')        # the SHADOW config in motor mode
        self.assertEqual(code, 2)
        self.assertIn('config_not_motor', out)
        self.host.files['/tmp/motor.json'] = shadow_config(motor_output_enabled=True)
        code, out = self.run_main('--config-file', '/tmp/motor.json')  # a motor config in shadow mode
        self.assertIn('config_enables_motor', out)
        self.host.files['/tmp/far.json'] = shadow_config(bind_host='10.77.0.1')
        self.assertIn('config_bind_not_loopback', self.run_main('--config-file', '/tmp/far.json')[1])
        self.assertEqual([c for c in self.docker.calls if c[0] in ('create', 'start', 'stop', 'rename')], [])

    def test_tampered_kit(self):
        bad = write_kit(os.path.join(self.tmp.name, 'bad'), tamper='row_executor/executor_main.py')
        self.assertIn('kit_file_sha256', str(self._kit_error(bad)))
        sums = write_kit(os.path.join(self.tmp.name, 'sums'), bad_sums=True)
        self.assertIn('kit_package_sums', str(self._kit_error(sums)))

    def _kit_error(self, folder):
        with self.assertRaises(inst.KitError) as caught:
            inst.Kit(folder)
        return caught.exception


class Install(KitCase):
    def test_shadow_install(self):
        code, installer = self.install()
        self.assertEqual(code, 0, installer.receipt)
        new = self.docker.by_name(inst.NAME)
        spec = new['Spec']
        cmd = spec['Cmd'][-1]
        self.assertIn('row_executor.executor_main', cmd)
        self.assertIn('--no-print-token', cmd)
        self.assertIn('--token-file /run/ubts-row-executor-link/operator_token.txt', cmd)
        self.assertNotIn('--motor-output', cmd)
        binds = spec['HostConfig']['Binds']
        self.assertFalse(any('/run/ubts-rover' in b for b in binds))
        self.assertIn('/home/unist/ws/runtime/row_executor:/run/ubts-row-executor-link:rw', binds)
        self.assertTrue(any(b.endswith(':/opt/ubts/row_executor:ro') for b in binds))
        self.assertEqual((spec['HostConfig']['NetworkMode'], spec['HostConfig']['IpcMode']), ('host', 'host'))
        self.assertEqual(spec['HostConfig']['CpuShares'], inst.CONTROL_CPU_SHARES)   # 2026-11-19 CPU weight
        self.assertIn('ROS_DOMAIN_ID=0', spec['Env'])
        link = json.loads(self.host.files[self.link()])
        self.assertEqual((link['url'], link['token_file']),
                         ('http://127.0.0.1:8097', '/app/runtime/row_executor/operator_token.txt'))
        self.assertEqual(self.host.files['/home/unist/ubts-row-executor/state/field_tuning.json'],
                         b'{"schema": "ubts.field_tuning.v1"}\n')
        receipt = json.dumps(installer.receipt, default=str)
        self.assertNotIn(TOKEN, receipt)
        ready = installer.receipt['ready']
        self.assertEqual((ready['mode'], ready['run_state'], ready['scan_hz'], ready['imu_hz']), ('SHADOW', 'idle', 10., 33.))
        preflight = self.docker.by_name('%s-kit-preflight-%s' % (inst.NAME, installer.stamp))
        self.assertEqual(preflight['HostConfig']['NetworkMode'], 'none')
        self.assertEqual(preflight['State']['Status'], 'exited')   # kept

    def test_field_tuning_of_earlier_runs_is_kept(self):
        self.host.files['/home/unist/ubts-row-executor/state/field_tuning.json'] = b'{"tuned": 1}\n'
        self.assertEqual(self.install()[0], 0)
        self.assertEqual(self.host.files['/home/unist/ubts-row-executor/state/field_tuning.json'], b'{"tuned": 1}\n')

    def test_link_waits_for_fresh_sensors_and_a_failed_ready_restores(self):
        self.host.rates = (0, 33)                     # no /scan
        code, installer = self.install()
        self.assertEqual(code, 1)
        self.assertEqual(installer.receipt['error']['code'], 'ready_timeout')
        self.assertNotIn(self.link(), self.host.files)
        failed = self.docker.by_name('%s-failed-%s' % (inst.NAME, installer.stamp))
        self.assertEqual((failed['State']['Running'], failed['HostConfig']['RestartPolicy']['Name']), (False, 'no'))
        self.assertIsNone(self.docker.by_name(inst.NAME))

    def test_wrong_mode_or_run_state_refuses(self):
        self.host.status = dict(self.host.status, mode='MOTOR')
        code, installer = self.install()
        self.assertEqual((code, installer.receipt['error']['code']), (1, 'mode_mismatch'))

    def test_a_real_run_state_other_than_idle_still_fails_at_once(self):
        self.host.empty_replies = 1                   # waited out first, then the status says active
        self.host.status = dict(self.host.status, run_state='active')
        code, installer = self.install()
        self.assertEqual((code, installer.receipt['error']), (1, {'code': 'not_idle', 'detail': 'active'}))
        self.assertEqual(len(self.host.http_calls), 2)
        self.assertEqual(installer.receipt['result'], 'failed_rolled_back')
        self.assertNotIn(self.link(), self.host.files)

    def test_ready_waits_for_the_first_status_of_the_loop(self):
        # Jetson 2026-11-16: the operator API answered 200 {} about 2 s after the start (ROS still starting); the
        # earlier installer took that for 'not_idle: None' and rolled back
        self.host.empty_replies = 2
        code, installer = self.install('--ready-timeout', '10')   # 2 s waiting + the 3 s rate window
        self.assertEqual(code, 0, installer.receipt)
        ready = installer.receipt['ready']
        self.assertEqual((ready['run_state'], ready['mode'], ready['scan_hz'], ready['imu_hz']), ('idle', 'SHADOW', 10., 33.))
        self.assertEqual(json.loads(self.host.files[self.link()])['url'], 'http://127.0.0.1:8097')

    def test_ready_waits_for_the_token_of_the_new_executor(self):
        old_id, old_token = 'f' * 64, 'old-' + 'y' * 24
        self.docker.containers[old_id] = container(old_id, inst.NAME, inst.IMAGE)
        self.host.port_open = True
        self.docker.old_tokens = [old_token] * 2       # the earlier executor's token is still in the link folder
        code, installer = self.install('--ready-timeout', '10')
        self.assertEqual(code, 0, installer.receipt)
        self.assertEqual([t for _, t in self.host.http_calls[:3]], [old_token, old_token, TOKEN])
        receipt = json.dumps(installer.receipt, default=str)
        self.assertNotIn(TOKEN, receipt)
        self.assertNotIn(old_token, receipt)
        self.assertEqual(self.docker.by_name('%s-kept-%s' % (inst.NAME, installer.stamp))['Id'], old_id)

    def test_races_that_never_end_fail_at_the_deadline(self):
        cases = (('first_status_of_the_loop', lambda: setattr(self.host, 'empty_replies', 99)),
                 ('token_of_the_new_executor', lambda: setattr(self.docker, 'old_tokens', ['old-' + 'y' * 24] * 99)))
        for waiting, setup in cases:
            with self.subTest(waiting=waiting):
                self.docker, self.host = FakeDocker(), FakeHost('/home/unist')
                setup()
                code, installer = self.install()
                self.assertEqual((code, installer.receipt['error']['code']), (1, 'ready_timeout'))
                self.assertIn(waiting, installer.receipt['error']['detail'])
                self.assertEqual(installer.receipt['result'], 'failed_rolled_back')
                self.assertNotIn(self.link(), self.host.files)
                self.assertIsNone(self.docker.by_name(inst.NAME))

    def test_replace_keeps_the_earlier_executor_and_restores_it_on_failure(self):
        old_id = 'f' * 64
        self.docker.containers[old_id] = container(old_id, inst.NAME, inst.IMAGE)
        self.host.port_open = True                    # the earlier executor answers on the port
        self.host.files[self.link()] = b'{"old": 1}\n'
        self.docker.exit_after_start = True
        code, installer = self.install()
        self.assertEqual(code, 1)
        self.assertEqual(self.docker.by_name(inst.NAME)['Id'], old_id)
        self.assertTrue(self.docker.containers[old_id]['State']['Running'])
        self.assertEqual(self.host.files[self.link()], b'{"old": 1}\n')
        self.docker.exit_after_start = False
        code, installer = self.install()
        self.assertEqual(code, 0)
        kept = self.docker.by_name('%s-kept-%s' % (inst.NAME, installer.stamp))
        self.assertEqual((kept['Id'], kept['HostConfig']['RestartPolicy']['Name']), (old_id, 'no'))
        self.assertIn('%s.before-%s' % (self.link(), installer.stamp), self.host.files)

    def test_preflight_failure_changes_no_running_container(self):
        self.docker.preflight_log = 'Traceback: ImportError\n'
        code, installer = self.install()
        self.assertEqual((code, installer.receipt['result']), (1, 'failed_before_change'))
        self.assertFalse([c for c in self.docker.calls if c[0] in ('stop', 'rename')])

    def test_motor_mode_mounts_the_control_socket(self):
        self.host.files['/tmp/motor.json'] = shadow_config(motor_output_enabled=True)
        self.host.status = dict(self.host.status, mode='MOTOR')
        code, installer = self.install('--mode', 'motor', '--config-file', '/tmp/motor.json')
        self.assertEqual(code, 0, installer.receipt)
        spec = self.docker.by_name(inst.NAME)['Spec']
        self.assertIn('--motor-output', spec['Cmd'][-1])
        self.assertIn('/run/ubts-rover:/run/ubts-rover:rw', spec['HostConfig']['Binds'])


class Rollback(KitCase):
    def test_rollback_of_the_latest_install(self):
        code, out = self.run_main('--apply')
        self.assertEqual(code, 0, out)
        latest = open(os.path.join(self.state, 'LATEST_RECEIPT'), encoding='utf-8').read().strip()
        self.host.files[latest] = open(latest, 'rb').read()
        self.host.files[os.path.join(self.state, 'LATEST_RECEIPT')] = latest.encode()
        code, out = self.run_main('--rollback')
        self.assertEqual(code, 0, out)
        names = [info['Name'] for info in self.docker.containers.values()]
        self.assertTrue(any(n.startswith('/%s-rolledback-' % inst.NAME) for n in names))
        self.assertNotIn(self.link(), self.host.files)
        self.assertTrue(any(k.startswith(self.link() + '.rolledback-') for k in self.host.files))
        self.assertNotIn(TOKEN, out)

    def test_refused_attempt_is_not_what_rollback_undoes(self):
        self.docker.images = {}
        self.run_main('--apply')
        self.assertFalse(os.path.exists(os.path.join(self.state, 'LATEST_RECEIPT')))
        self.assertEqual(self.run_main('--rollback')[0], 2)


class Tools(unittest.TestCase):
    def test_no_destructive_or_build_calls(self):
        text = (KIT_SRC / 'install_row_executor.py').read_text(encoding='utf-8').split('"""', 2)[2]   # code only
        for word in ("'DELETE'", 'prune', '/images/create', '/build', 'compose', 'subprocess', 'os.remove', 'unlink',
                     'rmtree', '/kill', 'shutil'):
            self.assertNotIn(word, text, word)
        self.assertIsNone(re.search(r"'--motor-output'", text.replace("args.append('--motor-output')", '')))

    def test_kit_builder(self):
        with tempfile.TemporaryDirectory(prefix='ubts-kit-build-') as tmp:
            sim = Path(tmp) / 'sim'
            pkg = sim / 'tools' / 'row_executor'
            pkg.mkdir(parents=True)
            files = {'executor_main.py': b'# e\n', 'config.vehicle_esp32_shadow.json': shadow_config(),
                     'field_tuning.default.json': b'{}\n'}
            for name, data in files.items():
                (pkg / name).write_bytes(data)
            (pkg / 'SHA256SUMS').write_bytes(''.join('%s  %s\n' % (sha(d), n) for n, d in sorted(files.items())).encode())
            env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid', GIT_COMMITTER_NAME='t',
                       GIT_COMMITTER_EMAIL='t@example.invalid')
            dash = Path(tmp) / 'dash'
            (dash / 'deploy' / 'jetson_row_executor').mkdir(parents=True)
            for name in ('install_row_executor.py', 'README_KO.md'):
                (dash / 'deploy' / 'jetson_row_executor' / name).write_bytes((KIT_SRC / name).read_bytes())
            for repo in (sim, dash):
                for args in (['init', '-q'], ['add', '-A'], ['-c', 'commit.gpgsign=false', 'commit', '-q', '-m', 'x']):
                    subprocess.run(['git', '-C', str(repo), '-c', 'core.autocrlf=false'] + args, check=True, env=env,
                                   capture_output=True)
            out = Path(tmp) / 'out'
            root, archive, manifest = make.build(sim, 'HEAD', dash, 'HEAD', out)
            self.assertIn('row_executor/executor_main.py', manifest['files'])
            self.assertIn('config/config.vehicle_esp32_shadow.json', manifest['files'])
            self.assertEqual(manifest['runtime_image'], inst.IMAGE)
            kit = inst.Kit(root)
            self.assertEqual(kit.commit, manifest['sim_commit'])
            with self.assertRaises(SystemExit):
                make.build(sim, 'HEAD', dash, 'HEAD', out)


if __name__ == '__main__':
    unittest.main()
