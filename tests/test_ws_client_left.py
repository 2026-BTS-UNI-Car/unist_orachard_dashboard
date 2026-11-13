"""The WebSocket proxy notices a browser that leaves while no bridge is connected (close frame or EOF)."""
import os
import socket
import struct
import time
import unittest

try:
    from dashboard_server import app
except ImportError as exc:  # fcntl and other Linux-only modules
    app = None
    REASON = str(exc)


def client_frame(opcode, payload=b"", mask=b"\x01\x02\x03\x04"):
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return head + mask + body


class Fake:
    def __init__(self, sock):
        self.connection = sock


@unittest.skipIf(app is None, "dashboard_server.app does not import here")
class ClientLeftTest(unittest.TestCase):
    def setUp(self):
        self.server, self.browser = socket.socketpair()
        self.fake = Fake(self.server)

    def tearDown(self):
        for s in (self.server, self.browser):
            try:
                s.close()
            except OSError:
                pass

    def left(self, wait):
        self.fake._ws_read_client_frame = app.DashboardHandler._ws_read_client_frame
        return app.DashboardHandler._ws_client_left(self.fake, wait)

    def test_close_frame_is_noticed_at_once(self):
        self.browser.sendall(client_frame(0x8, b"\x03\xe8"))
        t = time.monotonic()
        self.assertTrue(self.left(2.0))
        self.assertLess(time.monotonic() - t, 1.0)

    def test_eof_is_noticed(self):
        self.browser.shutdown(socket.SHUT_WR)
        self.assertTrue(self.left(2.0))

    def test_quiet_browser_waits_the_full_time(self):
        t = time.monotonic()
        self.assertFalse(self.left(0.3))
        self.assertGreaterEqual(time.monotonic() - t, 0.25)

    def test_other_frames_are_read_whole_and_dropped(self):
        self.browser.sendall(client_frame(0x1, b'{"ack": 1}') + client_frame(0x2, os.urandom(300)))
        self.assertFalse(self.left(0.3))
        # the next frame starts on a boundary: a close after them is still recognised
        self.browser.sendall(client_frame(0x8))
        self.assertTrue(self.left(1.0))

    def test_oversized_frame_counts_as_gone(self):
        self.browser.sendall(bytes([0x82, 0x80 | 127]) + struct.pack(">Q", 1 << 40))
        self.assertTrue(self.left(1.0))


if __name__ == "__main__":
    unittest.main()
