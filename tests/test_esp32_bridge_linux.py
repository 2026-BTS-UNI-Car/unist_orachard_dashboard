"""End to end on Linux: the ESP32 bridge process (python -m scripts.esp32_steer_bridge) on a pty whose other end is the
fake firmware (tests/fake_esp32.py), the real USB transport code (udev identity from a fake udev directory, flock,
TIOCEXCL, termios) and the real Unix control socket. Shows the stop on SIGTERM, on input expiry and the residual risk
of SIGKILL (the firmware has no drive timeout), and that the proposed firmware watchdog would close it.
Linux only (pty, fcntl, Unix sockets). Run from the repository root: python3 -B -m unittest tests.test_esp32_bridge_linux
"""
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

LINUX = sys.platform.startswith('linux')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if LINUX:
    import pty
    import select
    import tty
    from tests.fake_esp32 import FakeEsp32


class PtyBoard:
    """The fake firmware on a pty master, in real time (a thread)."""

    def __init__(self, **options):
        self.master, self.slave = pty.openpty()
        tty.setraw(self.master)
        self.path = os.ttyname(self.slave)
        self.fake = FakeEsp32(time.monotonic, reset_on_open=False, **options)
        self.fake.boot()
        self.fake.out.clear()
        self.lock = threading.Lock()
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while self.running:
            ready = select.select([self.master], [], [], 0.002)[0]
            with self.lock:
                if ready:
                    try:
                        data = os.read(self.master, 4096)
                    except OSError:
                        data = b''
                    if data:
                        self.fake.feed(data)
                self.fake.advance_to_now()
                out = self.fake.read_output()
            if out:
                try:
                    os.write(self.master, out)
                except OSError:
                    pass

    def drive(self):
        with self.lock:
            self.fake.advance_to_now()
            return self.fake.drive()

    def received(self):
        with self.lock:
            return [line for _, line in self.fake.received]

    def close(self):
        self.running = False
        self.thread.join(1)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


@unittest.skipUnless(LINUX, 'Linux only: pty, fcntl, termios, Unix sockets')
class BridgeProcess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.board = None
        self.proc = None

    def tearDown(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait(5)
            for stream in (self.proc.stdout, self.proc.stderr):
                if stream is not None:
                    stream.close()
        if self.board is not None:
            self.board.close()
        self.tmp.cleanup()

    def start(self, **board_options):
        self.board = PtyBoard(**board_options)
        info = os.stat(self.board.path)
        udev = os.path.join(self.dir, 'udev')
        os.makedirs(udev)
        with open(os.path.join(udev, 'c%d:%d' % (os.major(info.st_rdev), os.minor(info.st_rdev))), 'w') as handle:
            handle.write('E:ID_SERIAL=Fake_ESP32\n')
        self.sock = os.path.join(self.dir, 'run', 'control.sock')
        self.status_file = os.path.join(self.dir, 'runtime', 'rover_bridge_status.json')
        env = dict(os.environ, PYTHONPATH=ROOT, PYTHONDONTWRITEBYTECODE='1')
        self.proc = subprocess.Popen(
            [sys.executable, '-m', 'scripts.esp32_steer_bridge', '--device', self.board.path, '--identity',
             'Fake_ESP32', '--udev-root', udev, '--lock-dir', self.dir, '--socket', self.sock, '--status-file',
             self.status_file, '--no-ros', '--boot-wait-s', '0.3'],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if os.path.exists(self.sock):
                reply = self.request({'op': 'status'})
                if reply.get('connected'):
                    return
            if self.proc.poll() is not None:
                break
            time.sleep(0.05)
        out, err = (b'', b'')
        if self.proc.poll() is not None:
            out, err = self.proc.communicate(timeout=2)
        self.fail('bridge not ready: %r' % err[-800:])

    def request(self, payload):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(self.sock)
            client.sendall(json.dumps(payload).encode() + b'\n')
            data = b''
            while b'\n' not in data:
                part = client.recv(65536)
                if not part:
                    break
                data += part
        return json.loads(data)

    def arm_and_drive(self, throttle=40):
        reply = self.request({'op': 'arm', 'owner': 'op', 'vehicle_profile': 'front_steer'})
        self.assertTrue(reply['ok'], reply)
        reply = self.request({'op': 'drive', 'owner': 'op', 'lease': reply['lease'], 'ticket': reply['ticket'],
                              'seq': 1, 'deadman': True, 'throttle': throttle, 'steer_mrad': 0})
        self.assertTrue(reply['ok'], reply)
        self.wait_drive({2: 102, 3: 102}, 0.5)
        return reply

    def wait_drive(self, expected, limit):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            if self.board.drive() == expected:
                return time.monotonic()
            time.sleep(0.005)
        self.fail('drive %r, expected %r' % (self.board.drive(), expected))

    def test_sigterm_sends_stop(self):
        self.start()
        self.arm_and_drive()
        os.kill(self.proc.pid, signal.SIGTERM)
        self.assertEqual(self.proc.wait(5), 0)
        self.assertEqual(self.board.drive(), {2: 0, 3: 0})
        self.assertEqual(self.board.received()[-1], 'STOP')
        self.assertFalse(os.path.exists(self.sock), 'the socket is removed on shutdown')

    def test_input_expiry_stops_while_the_process_runs(self):
        self.start()
        self.arm_and_drive()
        sent = time.monotonic()
        stopped = self.wait_drive({2: 0, 3: 0}, 1.5)
        self.assertLess(stopped - sent, 0.95, 'stops within the 0.75 s input deadline plus one loop')
        status = self.request({'op': 'status'})
        self.assertEqual(status['reason'], 'input_expired')
        self.assertFalse(status['armed'])

    def test_stop_request(self):
        self.start()
        self.arm_and_drive()
        sent = time.monotonic()
        self.assertTrue(self.request({'op': 'stop', 'reason': 'operator_stop'})['ok'])
        stopped = self.wait_drive({2: 0, 3: 0}, 0.5)
        self.assertLess(stopped - sent, 0.2)

    def test_sigkill_leaves_the_drive_running_residual_risk(self):
        self.start()
        self.arm_and_drive()
        os.kill(self.proc.pid, signal.SIGKILL)
        self.proc.wait(5)
        time.sleep(1.0)
        self.assertEqual(self.board.drive(), {2: 102, 3: 102},
                         'firmware without a drive timeout: the last command keeps running')

    def test_sigkill_with_the_proposed_firmware_watchdog(self):
        self.start(drive_watchdog_ms=300)
        self.arm_and_drive()
        time.sleep(0.5)
        self.assertEqual(self.board.drive(), {2: 102, 3: 102}, 'HB keeps the watchdog fed while the host runs')
        os.kill(self.proc.pid, signal.SIGKILL)
        self.proc.wait(5)
        killed = time.monotonic()
        stopped = self.wait_drive({2: 0, 3: 0}, 1.0)
        self.assertLess(stopped - killed, 0.45)

    def test_second_bridge_is_refused(self):
        self.start()
        env = dict(os.environ, PYTHONPATH=ROOT, PYTHONDONTWRITEBYTECODE='1')
        other = subprocess.run(
            [sys.executable, '-m', 'scripts.esp32_steer_bridge', '--device', '/nonexistent', '--identity', 'X',
             '--socket', self.sock, '--no-ros', '--max-runtime-s', '1'],
            cwd=ROOT, env=env, capture_output=True, timeout=10)
        self.assertEqual(other.returncode, 2, other.stderr)
        self.assertTrue(self.request({'op': 'status'})['connected'], 'the first bridge keeps its socket')


if __name__ == '__main__':
    unittest.main()
