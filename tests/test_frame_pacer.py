"""Camera cadence under delayed timers, ACK stalls, and changing byte budgets."""
import unittest
from scripts.stream_policy import FramePacer, StreamBudget


class Clock:
    def __init__(self, overshoot=0):
        self.now = 100.0
        self.overshoot = overshoot
        self.sleeps = []
    def __call__(self):
        return self.now
    async def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay + self.overshoot


class FramePacerTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_delayed_wakes_do_not_reduce_30hz_cadence(self):
        clock = Clock(overshoot=.004)
        pacer = FramePacer(clock=clock, sleep=clock.sleep)
        sent = []
        for _ in range(121):
            await pacer.wait(30, 5_000_000)
            clock.now += .003  # JPEG preparation plus local send overhead.
            sent.append(clock.now)
            pacer.sent(clock.now, 50_000)
            clock.now += .001  # decode ACK.
        delivered = (len(sent)-1)/(sent[-1]-sent[0])
        # Original per-iteration relative sleep loses 4 ms on every frame.
        original_rate = 1/(1/30+.004)
        self.assertAlmostEqual(delivered, 30, delta=.1)
        self.assertGreater(delivered, original_rate+2)

    async def test_late_ack_skips_deadlines_without_catchup_burst(self):
        clock = Clock()
        pacer = FramePacer(clock=clock, sleep=clock.sleep)
        await pacer.wait(30, 5_000_000)
        pacer.sent(clock.now, 50_000)
        clock.now += .8  # Browser/connection stalls with one frame outstanding.
        self.assertEqual(await pacer.wait(30, 5_000_000), 0)
        pacer.sent(clock.now, 50_000)
        last = clock.now
        await pacer.wait(30, 5_000_000)
        self.assertAlmostEqual(clock.now-last, 1/30)

    async def test_actual_packet_spacing_respects_every_real_profile_and_downgrade(self):
        for profile in ('tiny', 'slow', 'balanced', 'detail'):
            with self.subTest(profile=profile):
                clock = Clock(overshoot=.004)
                pacer = FramePacer(clock=clock, sleep=clock.sleep)
                budget = StreamBudget(profile)
                fps, byte_rate = budget.values[2], budget.values[4]
                for size in (47_000, 90_000, 4_000, 31_000):
                    await pacer.wait(fps, byte_rate)
                    pacer.sent(clock.now, size)
                    before = clock.now
                    await pacer.wait(fps, byte_rate)
                    self.assertGreaterEqual(clock.now-before+1e-10, size/byte_rate)
                # An adaptive downgrade immediately applies the slower limit.
                pacer.sent(clock.now, 20_000)
                before = clock.now
                await pacer.wait(2, 3_000)
                self.assertGreaterEqual(clock.now-before, 20_000/3_000)


if __name__ == '__main__':
    unittest.main()
