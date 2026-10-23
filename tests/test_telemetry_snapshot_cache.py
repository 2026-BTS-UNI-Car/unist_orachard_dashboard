"""Shared /api/telemetry snapshot: single-flight, per-request rover state and non-blocking slow collectors."""
import importlib.util
import os
import socket
import threading
import time
import unittest

from dashboard_server.telemetry_snapshot import SharedTelemetry

LINUX_PROVIDER = importlib.util.find_spec('fcntl') is not None


class FakeProvider:
    def __init__(self, delay=0.2, external_rover=False):
        self.calls = 0
        self.rover_calls = 0
        self.delay = delay
        self.external_rover = external_rover
        self.lock = threading.Lock()

    def telemetry(self, include_rover=True):
        assert include_rover is False
        with self.lock:
            self.calls += 1
            n = self.calls
        time.sleep(self.delay)
        return {'build': n, 'network': {'total_rx_bps': 100.0 * n}, 'connection': {'sensors': {}},
                'rover': {'armed': 'external'} if self.external_rover else None,
                'rover_source': 'external' if self.external_rover else 'rover_bridge'}

    def rover_status(self):
        with self.lock:
            self.rover_calls += 1
            return {'connected': True, 'call': self.rover_calls}


class SharedTelemetryTests(unittest.TestCase):
    def test_concurrent_requests_build_once(self):
        provider = FakeProvider(delay=0.3)
        shared = SharedTelemetry(provider, ttl_s=1.0)
        results = []
        threads = [threading.Thread(target=lambda: results.append(shared.get())) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join(5)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(results), 8)
        self.assertEqual({r['build'] for r in results}, {1})
        # rx/tx rates come from one sample: identical for every client (no spikes).
        self.assertEqual({r['network']['total_rx_bps'] for r in results}, {100.0})

    def test_rover_state_is_read_for_every_request(self):
        provider = FakeProvider(delay=0)
        shared = SharedTelemetry(provider, ttl_s=10.0)
        first, second = shared.get(), shared.get()
        self.assertEqual(provider.calls, 1)
        self.assertEqual(provider.rover_calls, 2)
        self.assertEqual(first['rover']['call'], 1)
        self.assertEqual(second['rover']['call'], 2)
        self.assertEqual(second['connection']['rover']['call'], 2)
        # A per-request change of the connection dict never leaks into the shared snapshot.
        second['connection']['lidar_websocket'] = {'active': 1}
        self.assertNotIn('lidar_websocket', shared.get()['connection'])

    def test_external_rover_value_is_kept(self):
        shared = SharedTelemetry(FakeProvider(delay=0, external_rover=True), ttl_s=1.0)
        payload = shared.get()
        self.assertEqual(payload['rover'], {'armed': 'external'})
        self.assertEqual(payload['connection']['rover']['call'], 1)

    def test_ttl_expiry_rebuilds(self):
        now = [0.0]
        provider = FakeProvider(delay=0)
        shared = SharedTelemetry(provider, ttl_s=1.0, clock=lambda: now[0])
        shared.get(); now[0] = 0.5; shared.get()
        self.assertEqual(provider.calls, 1)
        now[0] = 1.2; payload = shared.get()
        self.assertEqual(provider.calls, 2)
        self.assertEqual(payload['snapshot_age_s'], 0.0)

    def test_three_clients_thirty_seconds_build_at_most_once_per_second(self):
        now = [0.0]
        provider = FakeProvider(delay=0)
        shared = SharedTelemetry(provider, ttl_s=1.0, clock=lambda: now[0])
        for step in range(300):  # 3 clients polling every 0.3 s for 30 s
            now[0] = step * 0.1
            shared.get()
        self.assertLessEqual(provider.calls, 31)

    def test_builder_error_propagates_and_next_request_retries(self):
        class Failing(FakeProvider):
            def telemetry(self, include_rover=True):
                self.calls += 1
                if self.calls == 1:
                    raise OSError('boom')
                return super().telemetry(include_rover)
        provider = Failing(delay=0)
        shared = SharedTelemetry(provider, ttl_s=1.0)
        with self.assertRaises(OSError):
            shared.get()
        self.assertEqual(shared.get()['build'], 3)


@unittest.skipUnless(LINUX_PROVIDER, 'TelemetryProvider imports fcntl (Linux)')
class ProviderBackgroundTests(unittest.TestCase):
    def setUp(self):
        os.environ['FAN_HOLD_INTERVAL_S'] = '0'
        from dashboard_server.telemetry import TelemetryProvider
        self.provider = TelemetryProvider()

    def test_docker_refresh_is_background_and_bounded(self):
        calls = []
        def slow_docker():
            calls.append(time.monotonic()); time.sleep(0.2)
            return {'available': True, 'containers': [], 'n': len(calls)}
        self.provider.docker = slow_docker
        self.provider._docker_refresh_s = 1.0
        started = time.perf_counter()
        first = self.provider.docker_cached()
        self.assertLess(time.perf_counter() - started, 0.1)
        self.assertTrue(first.get('pending'))
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:  # many clients polling for ~3 s
            value = self.provider.docker_cached()
            time.sleep(0.02)
        self.assertTrue(value['available'])
        self.assertLessEqual(len(calls), 4)
        # Autonomy priority stretches the period to 60 s.
        self.provider.docker_yield = lambda: True
        before = len(calls)
        for _ in range(50):
            self.provider.docker_cached(); time.sleep(0.02)
        self.assertEqual(len(calls), before)

    def test_external_ip_lookup_never_blocks_a_request(self):
        server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(1)
        port = server.getsockname()[1]
        old = {k: os.environ.get(k) for k in ('EXTERNAL_IP_URL', 'EXTERNAL_IP_TIMEOUT_S')}
        os.environ['EXTERNAL_IP_URL'] = f'http://127.0.0.1:{port}/'
        os.environ['EXTERNAL_IP_TIMEOUT_S'] = '5'
        try:
            self.provider._external_ip_next_refresh = 0.0
            started = time.perf_counter()
            self.provider._cached_external_ip()
            self.provider.network()
            self.assertLess(time.perf_counter() - started, 0.1)
            self.assertGreater(self.provider._external_ip_next_refresh, time.time())
        finally:
            server.close()
            for key, value in old.items():
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value

    def test_snapshot_rover_comes_from_rover_status_per_request(self):
        count = [0]
        def rover():
            count[0] += 1
            return {'connected': False, 'n': count[0]}
        self.provider.rover_status = rover
        self.provider.docker = lambda: {'available': False, 'containers': []}
        shared = SharedTelemetry(self.provider, ttl_s=5.0)
        a, b = shared.get(), shared.get()
        # Other collectors may read the rover state while building the shared
        # snapshot; the reported rover value is still read again per request.
        self.assertEqual(b['rover']['n'], a['rover']['n'] + 1)
        self.assertEqual(b['connection']['rover']['n'], b['rover']['n'])
        self.assertIsNone(self.provider.telemetry(include_rover=False)['connection']['rover'])


if __name__ == '__main__':
    unittest.main()
