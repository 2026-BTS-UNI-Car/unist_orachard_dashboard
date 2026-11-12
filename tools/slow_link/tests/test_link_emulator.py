"""The link emulator must actually produce the configured rate, delay and stalls.

Runs on 127.0.0.1 with the standard library only. Tolerances are stated next
to each check; they allow for timer granularity and a busy host.
"""
import asyncio
import sys
import time
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import link_emulator  # noqa: E402
from link_emulator import Link, StallSchedule  # noqa: E402


class StallScheduleTest(unittest.TestCase):
    def test_disabled_schedule_never_delays(self):
        schedule = StallSchedule()
        self.assertFalse(schedule.enabled)
        self.assertIsNone(schedule.window_at(5.0))
        self.assertEqual(schedule.finish_time(3.0, 0.25), 3.25)
        self.assertEqual(schedule.windows_between(0.0, 100.0), [])

    def test_windows_repeat_from_the_first_offset(self):
        schedule = StallSchedule(every_s=20.0, duration_s=1.0, first_s=10.0, origin=100.0)
        self.assertIsNone(schedule.window_at(109.99))
        self.assertEqual(schedule.window_at(110.0), (110.0, 111.0))
        self.assertEqual(schedule.window_at(110.99), (110.0, 111.0))
        self.assertIsNone(schedule.window_at(111.0))
        self.assertEqual(schedule.window_at(130.5), (130.0, 131.0))
        self.assertEqual(schedule.next_start(100.0), 110.0)
        self.assertEqual(schedule.next_start(110.0), 110.0)
        self.assertEqual(schedule.next_start(110.5), 130.0)
        self.assertEqual(schedule.windows_between(100.0, 151.0), [(110.0, 111.0), (130.0, 131.0), (150.0, 151.0)])
        self.assertEqual(schedule.windows_between(110.5, 120.0), [(110.0, 111.0)])

    def test_default_first_stall_is_half_a_period_in(self):
        self.assertEqual(StallSchedule(every_s=20.0, duration_s=2.0).first, 10.0)

    def test_transmission_pauses_inside_a_window(self):
        schedule = StallSchedule(every_s=10.0, duration_s=2.0, first_s=5.0, origin=0.0)
        self.assertAlmostEqual(schedule.finish_time(1.0, 0.5), 1.5)       # ends before the stall
        self.assertAlmostEqual(schedule.finish_time(4.9, 0.5), 7.4)       # 0.1 s sent, 2 s stalled, 0.4 s sent
        self.assertAlmostEqual(schedule.finish_time(5.5, 0.5), 7.5)       # begins inside the stall
        self.assertAlmostEqual(schedule.finish_time(4.0, 12.0), 20.0)     # crosses two stalls

    def test_stall_must_be_shorter_than_period(self):
        with self.assertRaises(ValueError):
            StallSchedule(every_s=1.0, duration_s=1.0)


class LinkSpecTest(unittest.TestCase):
    def test_profile_round_trips_through_the_command_line_form(self):
        profile = {"rate_kbps": 500, "rtt_ms": 150, "queue_bytes": 16384, "stall_every_s": 20, "stall_ms": 1000, "stall_first_s": 10}
        spec = link_emulator.link_spec("c0", 29100, profile)
        link = link_emulator.parse_link(spec, ("127.0.0.1", 1))
        self.assertEqual((link.name, link.port, link.rate_kbps, link.up_rate_kbps, link.queue_bytes), ("c0", 29100, 500.0, 500.0, 16384))
        self.assertAlmostEqual(link.rtt, 0.150)
        self.assertEqual(link.stall_args, (20.0, 1.0, 10.0))
        self.assertEqual(link.setup_rtts, 1)

    def test_unknown_field_is_refused(self):
        with self.assertRaises(ValueError):
            link_emulator.parse_link("name=c0,port=1,rate_kbps=1,rtt_ms=1,queue_bytes=1,loss=0.1", ("127.0.0.1", 1))


class LinkBehaviourTest(unittest.TestCase):
    """Each test runs its own event loop without asyncio debug mode (debug mode slows the relay by ~10 %)."""

    def setUp(self):
        self.closers = []

    def run_async(self, coroutine, timeout=30.0):
        async def bounded():
            try:
                return await asyncio.wait_for(coroutine, timeout)
            finally:
                for closer in reversed(self.closers):
                    await closer()
        return asyncio.run(bounded())

    async def upstream(self, handler):
        server = await asyncio.start_server(handler, "127.0.0.1", 0)

        async def close():
            server.close()
        self.closers.append(close)
        return server.sockets[0].getsockname()[1]

    async def link(self, upstream_port, **profile):
        link = await Link("test", 0, ("127.0.0.1", upstream_port), **profile).start()
        self.closers.append(link.close)
        return link

    async def measure_download(self, rate_kbps, total_bytes, rtt_ms=10):
        async def send_all(reader, writer):
            await reader.read(1)
            writer.write(b"x" * total_bytes)
            await writer.drain()
            writer.close()
        link = await self.link(await self.upstream(send_all), rate_kbps=rate_kbps, rtt_ms=rtt_ms, queue_bytes=16384)
        reader, writer = await asyncio.open_connection("127.0.0.1", link.port)
        writer.write(b"g")
        received = len(await reader.read(65536))
        started = time.perf_counter()  # the clock starts when the first segment has arrived: setup and delay are excluded
        timed = 0
        while data := await reader.read(65536):
            timed += len(data)
        elapsed = time.perf_counter() - started
        writer.close()
        self.assertEqual(received + timed, total_bytes)
        return timed * 8 / elapsed / 1000.0, link

    def test_download_rate_is_within_5_percent_at_2_mbit(self):
        measured, link = self.run_async(self.measure_download(2000, 300_000))  # 1.2 s nominal
        self.assertAlmostEqual(measured, 2000, delta=2000 * 0.05)
        # The bounded queue pushed back: it never held more than its size plus one segment.
        self.assertLessEqual(link.down.max_queued, 16384 + link_emulator.SEGMENT_BYTES)

    def test_download_rate_is_within_5_percent_at_150_kbit(self):
        measured, _ = self.run_async(self.measure_download(150, 30_000))  # 1.6 s nominal
        self.assertAlmostEqual(measured, 150, delta=150 * 0.05)

    def test_download_rate_is_within_5_percent_at_10_mbit(self):
        measured, _ = self.run_async(self.measure_download(10000, 1_500_000))  # 1.2 s nominal
        self.assertAlmostEqual(measured, 10000, delta=10000 * 0.05)

    def test_round_trip_and_connection_setup_delay(self):
        self.run_async(self.check_round_trip_and_connection_setup_delay())

    async def check_round_trip_and_connection_setup_delay(self):
        async def echo(reader, writer):
            while data := await reader.read(100):
                writer.write(data)
                await writer.drain()
            writer.close()
        link = await self.link(await self.upstream(echo), rate_kbps=10000, rtt_ms=100, queue_bytes=65536)
        started = time.perf_counter()
        reader, writer = await asyncio.open_connection("127.0.0.1", link.port)
        writer.write(b"a")
        await reader.readexactly(1)
        first = time.perf_counter() - started
        trips = []
        for _ in range(8):
            started = time.perf_counter()
            writer.write(b"b")
            await reader.readexactly(1)
            trips.append(time.perf_counter() - started)
        writer.close()
        # One round trip of 100 ms: never early by more than 5 ms, at most 30 ms late (median of 8).
        median = sorted(trips)[len(trips) // 2]
        self.assertGreater(median, 0.095)
        self.assertLess(median, 0.130)
        # A new connection pays one more round trip before its first exchange: 200 ms, same tolerance twice.
        self.assertGreater(first, 0.190)
        self.assertLess(first, 0.260)

    def test_periodic_stall_blocks_both_directions(self):
        self.run_async(self.check_periodic_stall_blocks_both_directions())

    async def check_periodic_stall_blocks_both_directions(self):
        arrivals_up = []

        async def ticker(reader, writer):
            async def collect():
                while data := await reader.read(100):
                    arrivals_up.append(time.perf_counter())
            collector = asyncio.ensure_future(collect())
            try:
                while True:
                    writer.write(b"t" * 50)
                    await writer.drain()
                    await asyncio.sleep(0.02)
            except (ConnectionError, OSError):
                pass
            finally:
                collector.cancel()
        link = await self.link(await self.upstream(ticker), rate_kbps=10000, rtt_ms=20, queue_bytes=65536,
                               stall_every_s=1.0, stall_ms=300, stall_first_s=0.6)
        reader, writer = await asyncio.open_connection("127.0.0.1", link.port)
        arrivals_down = []
        end = time.perf_counter() + 3.4

        async def talk():
            while time.perf_counter() < end:
                writer.write(b"u" * 50)
                await writer.drain()
                await asyncio.sleep(0.02)
        talker = asyncio.ensure_future(talk())
        while time.perf_counter() < end:
            try:
                data = await asyncio.wait_for(reader.read(4096), 0.5)
            except asyncio.TimeoutError:
                continue
            if not data:
                break
            arrivals_down.append(time.perf_counter())
        await talker
        stats = link.stats()
        writer.close()
        for name, arrivals in (("down", arrivals_down), ("up", arrivals_up)):
            gaps = [b - a for a, b in zip(arrivals, arrivals[1:])]
            long_gaps = [gap for gap in gaps if gap > 0.15]
            # Stalls begin 0.6, 1.6 and 2.6 s after the link started: three silences of 300 ms.
            # Each is seen as 300 ms plus up to one 20 ms sending period; allow 60 ms either way.
            self.assertEqual(len(long_gaps), 3, f"{name}: {[round(g, 3) for g in long_gaps]}")
            for gap in long_gaps:
                self.assertGreater(gap, 0.300 - 0.060, name)
                self.assertLess(gap, 0.320 + 0.060, name)
        self.assertEqual(len(stats["stalls_wall"]), 3)
        self.assertEqual(stats["stall_ms"], 300)

    def test_small_connection_is_not_starved_by_a_bulk_one(self):
        self.run_async(self.check_small_connection_is_not_starved_by_a_bulk_one())

    async def check_small_connection_is_not_starved_by_a_bulk_one(self):
        async def serve(reader, writer):
            kind = await reader.readexactly(1)
            if kind == b"B":
                writer.write(b"x" * 200_000)  # 4 s of traffic at 400 kbit/s
            else:
                writer.write(b"y" * 100)
            await writer.drain()
            writer.close()
        link = await self.link(await self.upstream(serve), rate_kbps=400, rtt_ms=20, queue_bytes=8192)
        bulk_reader, bulk_writer = await asyncio.open_connection("127.0.0.1", link.port)
        bulk_writer.write(b"B")

        async def drain_bulk():
            while await bulk_reader.read(65536):
                pass
        bulk = asyncio.ensure_future(drain_bulk())
        await asyncio.sleep(1.0)  # the bulk transfer now fills the queue
        started = time.perf_counter()
        reader, writer = await asyncio.open_connection("127.0.0.1", link.port)
        writer.write(b"S")
        await reader.readexactly(100)
        elapsed = time.perf_counter() - started
        writer.close()
        bulk_writer.close()
        bulk.cancel()
        # Worst case behind a full queue: (8192 + 2 segments) * 8 / 400 kbit/s = 0.22 s, plus setup and
        # round trip 0.04 s. Without ordered admission it would wait about 3 s for the bulk transfer to end.
        self.assertLess(elapsed, 0.45)
        self.assertGreater(elapsed, 0.04)


if __name__ == "__main__":
    unittest.main()
