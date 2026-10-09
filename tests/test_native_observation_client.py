"""Pure local-RPC contract tests. No Unix socket, ROS or native process runs."""
import json
import socket
import unittest
from unittest.mock import patch

from dashboard_server import native_observation_client as module


class ReplySocket:
    def __init__(self, chunks=(), *, connect_error=None, recv_error=None):
        self.chunks = list(chunks)
        self.connect_error, self.recv_error = connect_error, recv_error
        self.timeout = self.path = None
        self.sent, self.read_sizes, self.timeouts, self.closed = [], [], [], False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def settimeout(self, value):
        self.timeout = value
        self.timeouts.append(value)

    def connect(self, path):
        self.path = path
        if self.connect_error:
            raise self.connect_error

    def sendall(self, raw):
        self.sent.append(raw)

    def recv(self, count):
        self.read_sizes.append(count)
        if self.recv_error:
            raise self.recv_error
        if not self.chunks:
            return b''
        raw = self.chunks.pop(0)
        if len(raw) > count:
            self.chunks.insert(0, raw[count:])
            raw = raw[:count]
        return raw


def reply(**fields):
    value = dict(ok=True, state='idle', runtime_authority=False,
                 motor_command_sent=False, autonomy_output_enabled=False)
    value.update(fields)
    return (json.dumps(value, allow_nan=False, separators=(',', ':'))+'\n').encode()


class NativeObservationClientTests(unittest.TestCase):
    PATH = '/run/ubts-native-stream/observe.sock'

    def setUp(self):
        # Windows Python may omit AF_UNIX. The Linux protocol is explicitly
        # mocked here; no platform socket support or execution is claimed.
        family = patch.object(module.socket, 'AF_UNIX', 0xCAFE, create=True)
        family.start()
        self.addCleanup(family.stop)

    def request(self, method, raw=None, **socket_options):
        channel = ReplySocket((reply() if raw is None else raw,), **socket_options)
        with patch.object(module.socket, 'socket', return_value=channel) as factory:
            value = getattr(module.NativeObservationClient(self.PATH), method)()
        factory.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)
        self.assertTrue(channel.closed)
        self.assertEqual(channel.path, self.PATH)
        limit = 3.5 if method == 'stop' else .6
        self.assertTrue(all(0 < timeout <= limit for timeout in channel.timeouts))
        self.assertTrue(all(0 < size <= 65536 for size in channel.read_sizes))
        return value, channel

    def unavailable(self, value):
        self.assertIs(value['ok'], False)
        self.assertEqual(value['state'], 'unavailable')
        self.assertIsNone(value['input'])
        self.assertIs(value['runtime_authority'], False)
        self.assertIs(value['motor_command_sent'], False)
        self.assertIs(value['autonomy_output_enabled'], False)

    def test_constructor_inert_and_absolute_byte_bounded_path(self):
        with patch.object(module.socket, 'socket') as factory:
            client = module.NativeObservationClient(self.PATH)
            self.assertEqual(client.socket_path, self.PATH)
            for invalid in (None, 42, '', 'relative.sock', '/'+'x'*100,
                            '/'+'\uac00'*34):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    module.NativeObservationClient(invalid)
            factory.assert_not_called()

    def test_exact_operation_only_no_sensor_or_motor_payload(self):
        for method in ('start', 'stop', 'status', 'input'):
            with self.subTest(method=method):
                raw = reply(input=None) if method == 'input' else reply()
                value, channel = self.request(method, raw)
                self.assertEqual(json.loads(channel.sent[0]), {'op': method})
                self.assertEqual(len(channel.sent), 1)
                if method == 'input':self.assertIsNone(value)
                else:self.assertIs(value['runtime_authority'], False)

    def test_fragmented_json_returns_unmodified_reply_and_closes(self):
        expected = dict(ok=True, runtime_authority=False, motor_command_sent=False,
                        state='priming', error='awaiting_original_IMU')
        raw = (json.dumps(expected)+'\n').encode()
        channel = ReplySocket((raw[:3], raw[3:19], raw[19:]))
        with patch.object(module.socket, 'socket', return_value=channel):
            value = module.NativeObservationClient(self.PATH).status()
        self.assertEqual(value, expected)
        self.assertTrue(channel.closed)
        self.assertEqual(len(channel.read_sizes), 3)

    def test_unknown_range_none_preserved_no_free_or_receipt_rewrite(self):
        snapshot = dict(scan=dict(ranges=[1., None, 2.], stamp_ns=123,
                                 receipt_ns=456, original_native=True),
                        body_mount_verified=False, accuracy_verified=False)
        value, _ = self.request('input', reply(input=snapshot))
        self.assertEqual(value, snapshot)
        self.assertIsNone(value['scan']['ranges'][1])
        self.assertIs(value['body_mount_verified'], False)

    def test_failed_input_response_cannot_release_retained_snapshot(self):
        value, _ = self.request('input', reply(ok=False, input={'scan': 'retained'}))
        self.assertIsNone(value)

    def test_connection_timeout_and_eof_are_unavailable(self):
        for options in ({'connect_error': FileNotFoundError('missing')},
                        {'connect_error': PermissionError('denied')},
                        {'recv_error': socket.timeout('late')}, {}):
            with self.subTest(options=options):
                value, _ = self.request('status', b'', **options)
                self.unavailable(value)

    def test_framing_invalid_json_utf8_and_nonfinite_rejected(self):
        for raw in (b'{}', b'{}\n{}\n', reply()+b'trailing', b'\xff\n',
                    b'not-json\n', b'[]\n', b'null\n',
                    b'{"runtime_authority":false,"motor_command_sent":false,"value":NaN}\n'):
            with self.subTest(raw=raw):
                value, _ = self.request('status', raw)
                self.unavailable(value)

    def test_authority_requires_exact_false_not_missing_or_numeric_zero(self):
        for field in ('runtime_authority', 'motor_command_sent'):
            for invalid in (True, 0, None, 'false'):
                with self.subTest(field=field, invalid=invalid):
                    value, _ = self.request('status', reply(**{field: invalid}))
                    self.unavailable(value)
        value, _ = self.request('status', b'{"ok":true}\n')
        self.unavailable(value)

    def test_reply_capacity_includes_newline_at_exact_boundary(self):
        base = reply(padding='')
        exact = reply(padding='x'*(262144-len(base)))
        self.assertEqual(len(exact), 262144)
        value, _ = self.request('status', exact)
        self.assertIs(value['ok'], True)
        value, _ = self.request('status', exact[:-1]+b'x\n')
        self.unavailable(value)

    def test_socket_creation_error_also_returns_unavailable(self):
        with patch.object(module.socket, 'socket', side_effect=OSError('unavailable')):
            value = module.NativeObservationClient(self.PATH).status()
        self.unavailable(value)

    def test_drip_reply_stops_at_whole_deadline_without_wall_sleep(self):
        channel = ReplySocket((b'{', b'"', b'ok":true}\n'))
        # deadline=.6; two partial receives succeed, then total time reaches .61.
        with patch.object(module.socket, 'socket', return_value=channel), \
             patch.object(module.time, 'monotonic', side_effect=[0., 0., .2, .4, .61]) as clock:
            value = module.NativeObservationClient(self.PATH).status()
        self.unavailable(value)
        self.assertEqual(clock.call_count, 5)
        self.assertEqual(len(channel.read_sizes), 2)
        self.assertEqual(channel.chunks, [b'ok":true}\n'])
        self.assertTrue(channel.closed)
        self.assertEqual(len(channel.timeouts), 3)
        self.assertAlmostEqual(channel.timeouts[-1], .2)

    def test_stop_has_bounded_cleanup_deadline_not_short_input_bound(self):
        channel = ReplySocket((reply(),))
        with patch.object(module.socket, 'socket', return_value=channel), \
             patch.object(module.time, 'monotonic', side_effect=[10., 10., 13.]) as clock:
            value = module.NativeObservationClient(self.PATH).stop()
        self.assertIs(value['ok'], True)
        self.assertEqual(clock.call_count, 3)
        self.assertEqual(channel.timeouts, [3.5, .5])
        self.assertTrue(channel.closed)


if __name__ == '__main__':
    unittest.main()
