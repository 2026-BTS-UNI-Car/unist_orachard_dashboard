"""Pure deterministic sockets/clocks: no socket creation, HTTP, ROS or devices."""
import ast
from pathlib import Path
import unittest

from dashboard_server.ws_relay import MAX_PENDING_BYTES, relay_sensor_stream


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class Socket:
    def __init__(self, incoming=b"", *, eof=False, writable=True, send_limit=4096, timeout=None):
        self.incoming = bytearray(incoming)
        self.output = bytearray()
        self.eof, self.writable, self.send_limit = eof, writable, send_limit
        self.timeout = timeout
        self.recv_requests = []
        self.send_again = 0
        self.recv_again = 0

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value

    def setblocking(self, value):
        self.timeout = None if value else 0.0

    def recv(self, size):
        self.recv_requests.append(size)
        if self.recv_again:
            self.recv_again -= 1
            raise BlockingIOError()
        if self.incoming:
            chunk = bytes(self.incoming[:size])
            del self.incoming[:size]
            return chunk
        if self.eof:
            return b""
        raise BlockingIOError()

    def send(self, data):
        if self.send_again:
            self.send_again -= 1
            raise BlockingIOError()
        if not self.writable:
            raise BlockingIOError()
        count = min(len(data), self.send_limit)
        self.output.extend(data[:count])
        return count

    def sendall(self, _data):
        raise AssertionError("blocking_sendall_must_not_be_used")


class Ready:
    def __init__(self, clock, hook=None):
        self.clock, self.hook, self.calls = clock, hook, 0

    def __call__(self, readers, writers, _errors, timeout):
        self.calls += 1
        if self.calls > 10000:
            raise AssertionError("unbounded_poll_loop")
        if self.hook:
            self.hook(self, readers, writers)
        readable = [sock for sock in readers if sock.incoming or sock.eof]
        writable = [sock for sock in writers if sock.writable]
        self.clock.value += min(.001, timeout) if readable or writable else timeout
        return readable, writable, []


def run(client, upstream, **options):
    clock = options.pop("clock", Clock())
    ready = options.pop("select_ready", Ready(clock))
    return relay_sensor_stream(client, upstream, clock=clock, wall_clock=clock,
                               select_ready=ready, **options), clock, ready


class SensorRelayTest(unittest.TestCase):
    def test_blocked_viewer_still_delivers_reverse_ack_and_bounds_memory(self):
        client = Socket(b"fixture-ack", writable=False)
        upstream = Socket(b"x" * (4 * MAX_PENDING_BYTES))
        result, clock, _ = run(client, upstream)
        self.assertEqual(result.reason, "write_stalled")
        self.assertEqual(upstream.output, b"fixture-ack")
        self.assertEqual(result.peak_to_client_bytes, MAX_PENDING_BYTES)
        self.assertEqual(result.to_client_bytes, 0)
        self.assertLessEqual(clock.value, 8.01)
        self.assertEqual(len(upstream.incoming), 3 * MAX_PENDING_BYTES)

    def test_partial_writes_keep_large_opaque_frame_byte_order(self):
        payload = bytes(range(256)) * 1000
        client = Socket(send_limit=3072)
        upstream = Socket(payload, eof=True)
        result, _, _ = run(client, upstream)
        self.assertEqual(result.reason, "peer_closed")
        self.assertEqual(client.output, payload)
        self.assertEqual(result.to_client_bytes, len(payload))
        self.assertLessEqual(result.peak_to_client_bytes, MAX_PENDING_BYTES)
        self.assertTrue(all(0 < size <= MAX_PENDING_BYTES for size in upstream.recv_requests))

    def test_initial_upgrade_tail_precedes_later_bytes(self):
        client, upstream = Socket(send_limit=2), Socket(b"after", eof=True)
        result, _, _ = run(client, upstream, initial_upstream=b"tail-")
        self.assertEqual(client.output, b"tail-after")
        self.assertEqual(result.reason, "peer_closed")

    def test_spurious_read_and_write_readiness_does_not_disconnect(self):
        client, upstream = Socket(), Socket(b"latest", eof=True)
        client.send_again = upstream.recv_again = 1
        result, _, _ = run(client, upstream)
        self.assertEqual(client.output, b"latest")
        self.assertEqual(result.reason, "peer_closed")

    def test_session_deadline_preempts_stall_without_inserting_close_in_payload(self):
        client, upstream = Socket(writable=False), Socket(b"not-yet-delivered")
        result, clock, _ = run(client, upstream, session_deadline=.25)
        self.assertEqual(result.reason, "session_expired")
        self.assertAlmostEqual(clock.value, .25)
        self.assertEqual(client.output, b"")

    def test_expired_idle_connection_does_not_inject_any_bytes(self):
        client, upstream = Socket(writable=False), Socket()
        result, clock, _ = run(client, upstream, session_deadline=0)
        self.assertEqual(result.reason, "session_expired")
        self.assertEqual(clock.value, 0)
        self.assertEqual(client.output, b"")

    def test_reverse_progress_cannot_reset_stalled_viewer_deadline(self):
        client, upstream, clock = Socket(writable=False), Socket(b"frame"), Clock()
        def keep_reverse_busy(ready, _readers, _writers):
            ready.clock.value += .5
            client.incoming.extend(b"ack")
        result, _, _ = run(client, upstream, clock=clock, select_ready=Ready(clock, keep_reverse_busy))
        self.assertEqual(result.reason, "write_stalled")
        self.assertGreater(result.to_upstream_bytes, 0)
        self.assertLess(clock.value, 9)

    def test_select_error_restores_original_socket_timeouts(self):
        client, upstream = Socket(timeout=1.25), Socket(timeout=None)
        def failure(*_args):
            raise OSError("generated selector failure")
        result, _, _ = run(client, upstream, select_ready=failure)
        self.assertEqual(result.reason, "io_error")
        self.assertEqual(client.timeout, 1.25)
        self.assertIsNone(upstream.timeout)

    def test_send_zero_is_closed_and_settings_are_restored(self):
        client, upstream = Socket(send_limit=0, timeout=2), Socket(b"frame", timeout=3)
        result, _, _ = run(client, upstream)
        self.assertEqual(result.reason, "peer_closed")
        self.assertEqual((client.timeout, upstream.timeout), (2, 3))

    def test_invalid_initial_buffer_rejected_before_socket_mutation(self):
        client, upstream = Socket(timeout=2), Socket(timeout=3)
        for options in ({"initial_upstream": b"x" * (MAX_PENDING_BYTES + 1)},
                        {"write_stall_s": float("nan")}, {"write_stall_s": 0},
                        {"write_stall_s": True}, {"session_deadline": float("nan")},
                        {"session_deadline": float("inf")}, {"session_deadline": True},
                        {"session_deadline": "not-a-deadline"}):
            with self.assertRaisesRegex(ValueError, "invalid_bounded_sensor_relay_input"):
                run(client, upstream, **options)
        self.assertEqual((client.timeout, upstream.timeout), (2, 3))

    def test_split_websocket_payload_across_idle_interval_is_exact(self):
        clock, client, upstream = Clock(), Socket(send_limit=2), Socket()
        payload = b"\x82\x7e\x04\x00" + bytes(range(256)) * 4
        upstream.incoming.extend(payload[:500])
        resumed = False
        def resume_after_idle(ready, _readers, _writers):
            nonlocal resumed
            if not resumed and ready.clock.value >= 30:
                resumed = True
                upstream.incoming.extend(payload[500:])
                upstream.eof = True
        result, _, _ = run(client, upstream, clock=clock,
                           select_ready=Ready(clock, resume_after_idle))
        self.assertEqual(result.reason, "peer_closed")
        self.assertEqual(client.output, payload)
        self.assertGreaterEqual(clock.value, 30)

    def test_healthy_other_viewer_has_independent_queues(self):
        slow, _, _ = run(Socket(writable=False), Socket(b"slow"))
        client = Socket()
        fast, _, _ = run(client, Socket(b"fast", eof=True))
        self.assertEqual(slow.reason, "write_stalled")
        self.assertEqual(fast.reason, "peer_closed")
        self.assertEqual(client.output, b"fast")


if __name__ == "__main__":
    unittest.main()
