"""Linux: native observation socket ownership across normal/abnormal exits."""
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import native_observation_service as service

HERE = Path(__file__).resolve().parent
LISTENER = """
import socket, sys, time
sys.path.insert(0, {here!r})
path, mode = sys.argv[1], sys.argv[2]
if mode == 'owned':
    import native_observation_service as service
    server, lock_fd, _ = service.bind_observation_socket(path)
else:  # legacy owner without the lock file
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
server.listen(4)
print('ready', flush=True)
while True:
    conn, _ = server.accept()
    conn.close()
"""


def connectable(path):
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1)
    try:
        probe.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        probe.close()


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux AF_UNIX/flock semantics')
class ObservationSocketOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='uobs'))
        self.path = self.dir / 'observe.sock'
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            if child.stdout:
                child.stdout.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def spawn(self, mode):
        child = subprocess.Popen([sys.executable, '-c', LISTENER.format(here=str(HERE)), str(self.path), mode],
                                 stdout=subprocess.PIPE, text=True)
        self.children.append(child)
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        return child

    def test_fresh_bind_creates_private_lock_and_no_recovery(self):
        server, lock_fd, recovery = service.bind_observation_socket(self.path)
        try:
            self.assertIsNone(recovery)
            self.assertTrue(stat.S_ISSOCK(self.path.lstat().st_mode))
            self.assertEqual(stat.S_IMODE((self.dir / 'observe.sock.lock').stat().st_mode), 0o600)
        finally:
            server.close()
            os.close(lock_fd)

    def test_socket_left_by_sigkill_is_recovered_and_served(self):
        child = self.spawn('owned')
        old_inode = self.path.lstat().st_ino
        child.kill()
        child.wait(timeout=5)
        self.assertTrue(self.path.exists())  # leftover like power loss / SIGKILL
        self.assertFalse(connectable(self.path))
        server, lock_fd, recovery = service.bind_observation_socket(self.path)
        try:
            self.assertEqual((recovery['stale_socket_removed'], recovery['inode']), (True, old_inode))
            server.listen(1)
            self.assertTrue(connectable(self.path))
        finally:
            server.close()
            os.close(lock_fd)

    def test_running_locked_owner_is_never_touched(self):
        self.spawn('owned')
        inode = self.path.lstat().st_ino
        with self.assertRaisesRegex(ValueError, 'native_socket_owner_already_running'):
            service.bind_observation_socket(self.path)
        self.assertEqual(self.path.lstat().st_ino, inode)
        self.assertTrue(connectable(self.path))

    def test_live_legacy_listener_without_lock_is_never_touched(self):
        self.spawn('legacy')
        inode = self.path.lstat().st_ino
        with self.assertRaisesRegex(ValueError, 'native_socket_live_listener_present'):
            service.bind_observation_socket(self.path)
        self.assertEqual(self.path.lstat().st_ino, inode)
        self.assertTrue(connectable(self.path))
        # The failed attempt released its lock: the legacy owner keeps running.
        self.assertFalse(any(child.poll() is not None for child in self.children))

    def test_non_socket_path_and_symlinked_lock_are_refused(self):
        self.path.write_text('not a socket')
        with self.assertRaisesRegex(ValueError, 'native_socket_path_not_a_socket'):
            service.bind_observation_socket(self.path)
        self.assertEqual(self.path.read_text(), 'not a socket')
        self.path.unlink()
        (self.dir / 'observe.sock.lock').unlink()
        (self.dir / 'elsewhere').write_text('')
        os.symlink(self.dir / 'elsewhere', self.dir / 'observe.sock.lock')
        with self.assertRaises(OSError):
            service.bind_observation_socket(self.path)
        self.assertFalse(self.path.exists())

    def test_socket_replaced_during_recovery_is_refused(self):
        child = self.spawn('owned')
        child.kill()
        child.wait(timeout=5)
        real = self.path.lstat()
        replaced = SimpleNamespace(st_mode=real.st_mode, st_ino=real.st_ino + 1, st_mtime=real.st_mtime)
        with patch.object(Path, 'lstat', side_effect=[real, replaced]):
            with self.assertRaisesRegex(ValueError, 'native_socket_changed_during_recovery'):
                service.bind_observation_socket(self.path)
        self.assertEqual(self.path.lstat().st_ino, real.st_ino)

    def test_normal_shutdown_then_restart_needs_no_recovery(self):
        server, lock_fd, _ = service.bind_observation_socket(self.path)
        inode = self.path.lstat().st_ino
        # Mirror main(): close, unlink only our own inode, then release the lock.
        server.close()
        if self.path.lstat().st_ino == inode:
            self.path.unlink()
        os.close(lock_fd)
        server, lock_fd, recovery = service.bind_observation_socket(self.path)
        try:
            self.assertIsNone(recovery)
        finally:
            server.close()
            os.close(lock_fd)


if __name__ == '__main__':
    unittest.main()
