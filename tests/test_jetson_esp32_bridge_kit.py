"""The ESP32-S3 bridge kit (deploy/jetson_esp32_bridge/) with a fake Docker API and a fake host. No Docker, no device,
no SSH. Checks: the dry run changes nothing; refusals (device, identity, lock, name, tampered kit); the install keeps
the old container (renamed, restart no), builds the new one from the same image with the same mounts, the by-id
device, 'exec' in the command and the ESP32 settings, verifies the copied files; a failed start restores the old
state; the rollback; and that the tools have no remove/prune/pull/build/compose/subprocess.
Run: python -B -m unittest tests.test_jetson_esp32_bridge_kit
"""
import importlib.util
import io
import json
import re
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT_SRC = ROOT / 'deploy' / 'jetson_esp32_bridge'


def load(name):
    sys.path.insert(0, str(KIT_SRC))
    try:
        spec = importlib.util.spec_from_file_location(name, KIT_SRC / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(KIT_SRC))


inst = load('install_esp32_bridge')
sys.modules.setdefault('install_esp32_bridge', inst)
roll = load('rollback_esp32_bridge')
make = load('make_bridge_kit')

OLD_ID = 'a' * 64
IMAGE = 'sha256:' + 'f' * 64
DASH_ID = 'd' * 64


def old_inspect():
    return {
        'Id': OLD_ID, 'Name': '/' + inst.OLD_NAME, 'Image': IMAGE,
        'Config': {'Image': 'unist-orachard-ros2-esp-rover-bridge:latest', 'WorkingDir': '/app',
                   'Entrypoint': ['/ros_entrypoint.sh'],
                   'Cmd': ['/bin/bash', '-lc', 'source /opt/ros/iron/setup.bash && python3 -m scripts.rover_control_bridge'],
                   'Env': ['ROS_DOMAIN_ID=0', 'ROVER_SERIAL_PORT=/dev/ubts-motor',
                           'ROVER_ESP_IDENTITY=Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001',
                           'ROVER_CONTROL_SOCKET=/run/ubts-rover/control.sock',
                           'ROVER_BRIDGE_STATUS_FILE=/app/runtime/rover_bridge_status.json', 'PATH=/usr/bin:/bin',
                           'ROS_DISTRO=iron', 'PYTHONUNBUFFERED=1'],
                   'Labels': {'com.docker.compose.service': 'ros2-esp-rover-bridge'}},
        'HostConfig': {'Binds': ['/home/unist/ws/runtime:/app/runtime:rw', '/run/ubts-rover:/run/ubts-rover:rw',
                                 '/run/lock:/run/lock:rw', '/run/udev:/run/udev:ro'],
                       'NetworkMode': 'host', 'IpcMode': 'host', 'SecurityOpt': ['no-new-privileges:true', 'label=disable'],
                       'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0},
                       'Devices': [{'PathOnHost': '/dev/serial/by-id/usb-Silicon_Labs_CP2102-if00-port0',
                                    'PathInContainer': '/dev/ubts-motor', 'CgroupPermissions': 'rwm'}],
                       'LogConfig': {'Type': 'json-file', 'Config': {}}},
        'Mounts': [{'Type': 'bind', 'Source': '/home/unist/ws/runtime', 'Destination': '/app/runtime'},
                   {'Type': 'bind', 'Source': '/run/ubts-rover', 'Destination': '/run/ubts-rover'},
                   {'Type': 'bind', 'Source': '/run/lock', 'Destination': '/run/lock'},
                   {'Type': 'bind', 'Source': '/run/udev', 'Destination': '/run/udev'}],
        'State': {'Status': 'exited', 'Running': False, 'ExitCode': 255,
                  'Error': 'error gathering device information while adding custom device'},
    }


def dashboard_inspect():
    return {'Id': DASH_ID, 'Name': '/' + inst.DASHBOARD_NAME, 'Image': 'sha256:' + 'e' * 64,
            'Config': {'Env': ['ROVER_CONTROL_SOCKET=/run/ubts-rover/control.sock', 'DASHBOARD_MODE=live']},
            'Mounts': [{'Type': 'bind', 'Source': '/home/unist/ws/runtime', 'Destination': '/app/runtime'},
                       {'Type': 'bind', 'Source': '/run/ubts-rover', 'Destination': '/run/ubts-rover'}],
            'State': {'Status': 'running', 'Running': True}}


class FakeDocker:
    def __init__(self, host):
        self.host = host
        self.containers = {OLD_ID: old_inspect(), DASH_ID: dashboard_inspect()}
        self.files = {}
        self.calls = []
        self.n = 0
        self.preflight_exit = 0
        self.start_fails_for = set()

    def _find(self, ref):
        for cid, info in self.containers.items():
            if ref == cid or ref == info['Name'].lstrip('/') or (len(ref) >= 12 and cid.startswith(ref)):
                return cid
        return None

    def inspect(self, ref):
        cid = self._find(ref)
        return json.loads(json.dumps(self.containers[cid])) if cid else None

    def image(self, ref):
        return {'Id': IMAGE, 'Architecture': 'arm64'} if ref == IMAGE else None

    def create(self, name, spec):
        self.calls.append(('create', name))
        if self._find(name):
            raise inst.KitError('docker_api', 'name in use')
        self.n += 1
        cid = ('%x' % self.n) * 64
        cid = cid[:64]
        self.containers[cid] = {'Id': cid, 'Name': '/' + name, 'Image': spec['Image'], 'Spec': spec,
                                'Config': {'Env': spec.get('Env', []), 'Cmd': spec['Cmd']},
                                'HostConfig': dict(spec.get('HostConfig', {})),
                                'Mounts': [], 'State': {'Status': 'created', 'Running': False}}
        return cid

    def put_archive(self, cid, path, data):
        self.calls.append(('put_archive', cid, path))
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for member in tar:
                self.files[(cid, path.rstrip('/') + '/' + member.name)] = tar.extractfile(member).read()

    def read_file(self, cid, path):
        return self.files.get((cid, path))

    def start(self, cid):
        self.calls.append(('start', cid))
        info = self.containers[cid]
        if cid in self.start_fails_for:
            raise inst.KitError('docker_api', 'start failed')
        info['State'] = {'Status': 'running', 'Running': True}
        spec = info.get('Spec', {})
        if 'kit-preflight' in info['Name']:
            info['State'] = {'Status': 'exited', 'Running': False, 'ExitCode': self.preflight_exit}
        elif spec.get('Labels', {}).get('ubts.kit') == 'esp32-bridge':
            self.host.bridge_started(spec)

    def stop(self, cid, grace=10):
        self.calls.append(('stop', cid, grace))
        self.containers[cid]['State'] = {'Status': 'exited', 'Running': False, 'ExitCode': 0}

    def wait(self, cid, timeout):
        return self.containers[cid]['State'].get('ExitCode', 0)

    def logs(self, cid, tail=60):
        if 'kit-preflight' in self.containers[cid]['Name'] and self.preflight_exit == 0:
            return '%s {"python": "3.10.12", "firmware": "ABS-HOLD-8-REBOUND-LIMITS"}\n' % inst.MARKER
        return 'log line\n'

    def rename(self, cid, name):
        self.calls.append(('rename', cid, name))
        if self._find(name) not in (None, cid):
            raise inst.KitError('docker_api', 'name in use')
        self.containers[cid]['Name'] = '/' + name

    def set_restart(self, cid, policy):
        self.calls.append(('restart', cid, policy['Name']))
        self.containers[cid]['HostConfig']['RestartPolicy'] = policy


class FakeHost:
    def __init__(self):
        self.links = {inst.DEVICE: '/dev/ttyACM1'}
        self.chars = {'/dev/ttyACM1': (166, 1), '/dev/ttyACM0': (166, 0)}
        self.texts = {'/run/udev/data/c166:1': 'E:ID_VENDOR_ID=303a\nE:ID_MODEL_ID=1001\nE:ID_SERIAL=%s\n' % inst.IDENTITY,
                      '/run/udev/data/c166:0': 'E:ID_SERIAL=u-blox_GNSS\n'}
        self.lock = True
        self.dirs = {'/run/ubts-rover'}
        self.names = {'dockerd', 'python3'}
        self.esp_connected = True
        self.socket_reply = 'answer'

    def realpath(self, path):
        return self.links.get(path, path)

    def lexists(self, path):
        return path in self.links or path in self.chars

    def char_device(self, path):
        return self.chars.get(path)

    def read_text(self, path, limit=0):
        return self.texts.get(path)

    def isdir(self, path):
        return path in self.dirs

    def lock_free(self, path):
        return self.lock

    def process_names(self):
        return self.names

    def bridge_started(self, spec):
        env = dict(item.split('=', 1) for item in spec['Env'])
        if env.get('ROVER_ESP_KIND') == 'esp32_abs_hold':
            import time
            self.texts['/home/unist/ws/runtime/rover_bridge_status.json'] = json.dumps({
                'esp_kind': 'esp32_abs_hold', 'vehicle_profile': 'front_steer', 'updated_at': time.time(),
                'connected': self.esp_connected, 'armed': False, 'firmware': 'ABS-HOLD-8-REBOUND-LIMITS',
                'state': 'disarmed'})

    def socket_status(self, path, timeout=1.0):
        if self.socket_reply is None:
            return None
        return {'ok': True, 'vehicle_profile': 'front_steer', 'esp_kind': 'esp32_abs_hold'}


class KitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        out = Path(self.tmp.name)
        # the kit as make_bridge_kit.py lays it out, from the work tree (make_bridge_kit reads a commit)
        self.kit_dir = out / 'kit'
        files = {}
        for rel in make.PAYLOAD:
            data = (ROOT / rel).read_bytes()
            files[rel] = inst.sha256(data)
            (self.kit_dir / 'payload' / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.kit_dir / 'payload' / rel).write_bytes(data)
        (self.kit_dir / 'MANIFEST.json').write_bytes(json.dumps(
            {'schema': make.SCHEMA, 'commit': '0' * 40, 'files': files}).encode('utf-8'))
        self.host = FakeHost()
        self.docker = FakeDocker(self.host)
        self.state = out / 'state'

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *extra, apply=False):
        out = io.StringIO()
        argv = ['--kit-dir', str(self.kit_dir), '--state-dir', str(self.state), '--ready-timeout', '2', *extra]
        if apply:
            argv.append('--apply')
        code = inst.main(argv, docker=self.docker, host=self.host, out=out)
        return code, out.getvalue()

    def test_dry_run_passes_and_changes_nothing(self):
        before = json.dumps(self.docker.containers, sort_keys=True)
        code, text = self.run_tool()
        self.assertEqual(code, 0, text)
        self.assertIn('점검 통과', text)
        self.assertEqual(self.docker.calls, [])
        self.assertEqual(json.dumps(self.docker.containers, sort_keys=True), before)
        report = json.loads(text[:text.rindex('}') + 1])
        self.assertEqual(report['device']['resolved'], '/dev/ttyACM1')
        self.assertEqual(report['plan']['devices'][0]['PathOnHost'], inst.DEVICE)
        self.assertIn('exec python3 -m scripts.esp_rover_bridge', report['plan']['cmd'][-1])
        self.assertNotIn('Silicon_Labs', json.dumps(report['plan']['esp32_settings']))

    def test_refusals(self):
        cases = [
            (lambda h, d: h.links.pop(inst.DEVICE), 'device_missing'),
            (lambda h, d: h.links.__setitem__(inst.DEVICE, '/dev/ttyACM0'), 'udev_identity_mismatch'),
            (lambda h, d: h.links.__setitem__(inst.DEVICE, '/dev/ttyUSB0'), 'device_not_ttyacm'),
            (lambda h, d: setattr(h, 'lock', False), 'control_socket_in_use'),
            (lambda h, d: d.containers.pop(OLD_ID), 'old_container_missing'),
            (lambda h, d: d.create(inst.NEW_NAME, {'Image': IMAGE, 'Cmd': []}), 'new_name_in_use'),
        ]
        for change, code in cases:
            self.host, self.docker = FakeHost(), None
            self.docker = FakeDocker(self.host)
            change(self.host, self.docker)
            calls = len(self.docker.calls)
            result, text = self.run_tool(apply=True)
            self.assertEqual(result, 2, (code, text))
            self.assertIn(code, text)
            self.assertEqual(len([c for c in self.docker.calls[calls:] if c[0] != 'create']), 0, code)

    def test_tampered_kit_is_refused(self):
        path = self.kit_dir / 'payload' / 'scripts' / 'esp32_abs_hold.py'
        path.write_bytes(path.read_bytes() + b'# changed\n')
        code, text = self.run_tool()
        self.assertEqual(code, 2)
        self.assertIn('kit_file_sha256', text)

    def test_install(self):
        code, text = self.run_tool(apply=True)
        self.assertEqual(code, 0, text)
        old = self.docker.containers[OLD_ID]
        self.assertTrue(old['Name'].startswith('/%s-kept-' % inst.OLD_NAME))
        self.assertEqual(old['HostConfig']['RestartPolicy']['Name'], 'no')
        new_id = self.docker._find(inst.NEW_NAME)
        new = self.docker.containers[new_id]
        spec = new['Spec']
        self.assertEqual(spec['Image'], IMAGE, 'the old image, no build or pull')
        self.assertEqual(spec['HostConfig']['Binds'], old_inspect()['HostConfig']['Binds'])
        self.assertEqual(spec['HostConfig']['NetworkMode'], 'host')
        self.assertEqual(spec['HostConfig']['Devices'], [{'PathOnHost': inst.DEVICE, 'PathInContainer': '/dev/ubts-motor',
                                                          'CgroupPermissions': 'rwm'}])
        self.assertEqual(spec['HostConfig']['RestartPolicy']['Name'], 'unless-stopped')
        self.assertIn('&& exec python3 -m scripts.esp_rover_bridge', spec['Cmd'][-1])
        env = dict(item.split('=', 1) for item in spec['Env'])
        self.assertEqual((env['ROVER_ESP_KIND'], env['ROVER_ESP_IDENTITY'], env['ROVER_ESP32_MAX_DRIVE_PWM'],
                          env['ROVER_ESP32_DIRECTION_VERIFIED']), ('esp32_abs_hold', inst.IDENTITY, '153', '0'))
        self.assertEqual(env['ROVER_CONTROL_SOCKET'], '/run/ubts-rover/control.sock')
        self.assertNotIn('com.docker.compose.service', spec['Labels'])
        for rel in make.PAYLOAD:
            self.assertEqual(self.docker.files[(new_id, '/app/' + rel)], (ROOT / rel).read_bytes())
        receipt_path = Path((self.state / 'LATEST_RECEIPT').read_text(encoding='utf-8'))
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        self.assertEqual(receipt['result'], 'installed')
        self.assertEqual(receipt['ready']['socket'], 'answered')
        self.assertNotIn('Silicon_Labs_CP2102', json.dumps(receipt), 'environment values are not in the receipt')
        preflight = [c for c in self.docker.containers.values() if 'kit-preflight' in c['Name']][0]
        self.assertEqual(preflight['Spec']['HostConfig']['NetworkMode'], 'none')
        self.assertNotIn('Devices', preflight['Spec']['HostConfig'])
        # rollback
        out = io.StringIO()
        code = roll.main(['--state-dir', str(self.state), '--apply'], docker=self.docker, out=out)
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(self.docker.containers[OLD_ID]['Name'], '/' + inst.OLD_NAME)
        self.assertEqual(self.docker.containers[OLD_ID]['HostConfig']['RestartPolicy']['Name'], 'unless-stopped')
        self.assertTrue(self.docker.containers[new_id]['Name'].startswith('/%s-rolledback-' % inst.NEW_NAME))
        self.assertEqual(self.docker.containers[new_id]['HostConfig']['RestartPolicy']['Name'], 'no')
        self.assertIn(('stop', new_id, 10), self.docker.calls, 'SIGTERM with a grace time: the bridge sends STOP')
        self.assertNotIn(('start', OLD_ID), self.docker.calls, 'the old bridge is not started without --start-old')

    def test_files_in_the_new_container_are_the_kit_files(self):
        code, text = self.run_tool(apply=True)
        self.assertEqual(code, 0, text)
        new_id = self.docker._find(inst.NEW_NAME)
        manifest = json.loads((self.kit_dir / 'MANIFEST.json').read_text(encoding='utf-8'))
        for rel, digest in manifest['files'].items():
            self.assertEqual(inst.sha256(self.docker.files[(new_id, '/app/' + rel)]), digest)

    def test_esp_absent_fails_and_restores(self):
        self.host.esp_connected = False
        code, text = self.run_tool(apply=True)
        self.assertEqual(code, 1, text)
        self.assertIn('ready_timeout', text)
        old = self.docker.containers[OLD_ID]
        self.assertEqual(old['Name'], '/' + inst.OLD_NAME)
        self.assertEqual(old['HostConfig']['RestartPolicy']['Name'], 'unless-stopped')
        failed = [c for c in self.docker.containers.values() if '-failed-' in c['Name']]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]['State']['Running'], False)
        self.assertEqual(failed[0]['HostConfig']['RestartPolicy']['Name'], 'no')
        self.host = FakeHost()
        self.host.esp_connected = False
        self.docker.host = self.host
        code, text = self.run_tool('--allow-esp-absent', apply=True)
        self.assertEqual(code, 0, text)

    def test_preflight_failure_changes_nothing(self):
        self.docker.preflight_exit = 1
        code, text = self.run_tool(apply=True)
        self.assertEqual(code, 1, text)
        self.assertEqual(self.docker.containers[OLD_ID]['Name'], '/' + inst.OLD_NAME)
        self.assertFalse([c for c in self.docker.calls if c[0] in ('rename', 'stop', 'restart')])

    def test_no_forbidden_calls_in_the_tools(self):
        for name in ('install_esp32_bridge.py', 'rollback_esp32_bridge.py'):
            source = (KIT_SRC / name).read_text(encoding='utf-8')
            code = re.sub(r'#[^\n]*', '', re.sub(r'"""[\s\S]*?"""', '', source))
            for word in ('subprocess', 'compose', 'prune', "'DELETE'", '/build', 'images/create', 'os.system',
                         '/remove'):
                self.assertNotIn(word, code, '%s: %s' % (name, word))
            self.assertNotIn('\r', source)


if __name__ == '__main__':
    unittest.main()
