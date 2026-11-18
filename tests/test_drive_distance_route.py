"""The distance move routes of this dashboard (GET /api/rover/distance, POST /api/rover/distance/{plan,start,hold,
release,feedback,reset}) with the ESP32-S3 bridge code and the fake board in one process, in real time
(tests/test_esp32_calibration_route.Stack: rover_api.request is the bridge's own request() with a JSON round trip).
Checks: the session and Origin rules, a refusal without the Arm or for another session, the distance and drive-cap
limits, a held move that stops at the planned time and hands the lease back, a lost heartbeat, STOP winning, the page's
own drive requests refused while the server owns the lease, and the tape feedback. Temporary folders only.
Run in its own process: python -B -m unittest tests.test_drive_distance_route
"""
import sys
import time
import unittest

sys.dont_write_bytecode = True
from tests.test_esp32_calibration_route import Stack  # noqa: E402
from tests.test_steer_routes import Client  # noqa: E402


class DistanceRoutes(unittest.TestCase):
    MOTOR = {}

    def setUp(self):
        self.stack = Stack(pot=1925.0, motor_options=dict(self.MOTOR))
        self.addCleanup(self.stack.close)
        self.page = Client(self.stack.base)
        status, _ = self.page.get('/static/steer/drive-distance.mjs')       # the session cookie
        self.assertEqual(status, 200)

    def arm(self, client=None):
        status, reply = (client or self.page).post('/api/rover/arm', {'vehicle_profile': 'front_steer'})
        self.assertTrue(reply and reply['ok'], reply)
        return reply

    def start(self, chain, terrain='road_paved', duty=40, distance=1.0, client=None, direction=None):
        body = {'terrain': terrain, 'duty': duty, 'distance_m': distance, 'lease': chain['lease'],
                'ticket': chain['ticket'], 'seq': chain['seq'], 'steer_mrad': 0}
        if direction is not None:
            body['direction'] = direction
        return (client or self.page).post('/api/rover/distance/start', body)

    def hold_until_done(self, move_id, limit_s=8.0):
        deadline = time.monotonic() + limit_s
        while time.monotonic() < deadline:
            status, reply = self.page.post('/api/rover/distance/hold', {'move_id': move_id})
            if reply and 'chain' in reply:
                return reply
            time.sleep(0.08)
        raise AssertionError('move did not end')

    def drive_ms(self):
        with self.stack.lock:
            return self.stack.fake.drive_on_ms


class Routes(DistanceRoutes):
    def test_table_and_plan(self):
        status, reply = self.page.get_json('/api/rover/distance')
        self.assertEqual(status, 200)
        self.assertEqual([t['key'] for t in reply['table']['terrains']][:3],
                         ['road_paved', 'orchard_row_1010', 'orchard_other_1010'])
        status, reply = self.page.post('/api/rover/distance/plan', {'terrain': 'road_paved', 'duty': 40, 'distance_m': 1})
        self.assertEqual(status, 200)
        self.assertTrue(reply['ok'], reply)
        self.assertEqual((reply['bridge']['refusal'], reply['bridge']['max_pwm'], reply['plan']['pwm']), (None, 255, 102))
        self.assertEqual(reply['bridge']['max_duty'], 100)
        status, reply = self.page.post('/api/rover/distance/plan', {'terrain': 'road_paved', 'duty': 40, 'distance_m': 3.5})
        self.assertEqual(reply['plan']['problem'], 'distance_out_of_range')
        self.assertFalse(reply['ok'])
        self.assertEqual(self.stack.relayed, [], 'planning sends nothing but status reads')

    def test_the_speed_field(self):
        road = next(t for t in self.page.get_json('/api/rover/distance')[1]['table']['terrains'] if t['key'] == 'road_paved')
        self.assertEqual((road['duty_min'], road['duty_max'], road['default_duty']), (40, 100, 40))
        plan = lambda body: self.page.post('/api/rover/distance/plan', {'terrain': 'road_paved', 'distance_m': 1, **body})[1]
        default, forty = plan({}), plan({'duty': 40})
        self.assertTrue(default['ok'], default)
        self.assertEqual(default['plan'], forty['plan'], 'no duty: the lowest table duty, as the 11-17 page')
        reply = plan({'duty': 64})
        self.assertTrue(reply['ok'], reply)
        self.assertEqual((reply['plan']['duty'], reply['plan']['pwm'], reply['plan']['speed']['between']), (64, 163, [58, 88]))
        self.assertLess(reply['plan']['drive_s'], forty['plan']['drive_s'])
        for bad, problem in ((39, 'distance_duty_out_of_range'), (101, 'distance_duty_out_of_range'),
                             (40.5, 'distance_duty_invalid'), ('60', 'distance_duty_invalid'), (True, 'distance_duty_invalid')):
            reply = plan({'duty': bad})
            self.assertFalse(reply['ok'], bad)
            self.assertEqual(reply['plan']['problem'], problem, bad)
        status, reply = self.start({'lease': 'x', 'ticket': 'y', 'seq': 0}, duty=101)
        self.assertEqual(reply['error'], 'distance_duty_out_of_range')
        self.assertEqual(self.drive_ms(), 0)

    def test_session_origin_and_arm_rules(self):
        stranger = Client(self.stack.base)
        status, _ = stranger.post('/api/rover/distance/start', {'terrain': 'road_paved', 'duty': 40, 'distance_m': 1})
        self.assertEqual(status, 403)
        status, _ = self.page.post('/api/rover/distance/plan', {}, origin='http://evil.example')
        self.assertEqual(status, 403)
        status, reply = self.start({'lease': 'x', 'ticket': 'y', 'seq': 0})
        self.assertEqual(reply['error'], 'distance_needs_arm')
        other = Client(self.stack.base)
        other.get('/static/steer/drive-distance.mjs')
        armed = self.arm(other)
        status, reply = self.start(armed)                        # this page holds no Arm; the chain is another's
        self.assertEqual(reply['error'], 'control_not_owned')
        self.assertEqual(self.drive_ms(), 0)

    def test_held_move_stops_at_the_planned_time_and_returns_the_lease(self):
        armed = self.arm()
        status, reply = self.start(armed)
        self.assertTrue(reply['ok'], reply)
        planned = reply['plan']['drive_s']
        # the page's own drive requests wait while the server owns the lease
        status, _ = self.page.post('/api/rover/command', {'lease': armed['lease'], 'ticket': armed['ticket'],
                                                          'seq': armed['seq'] + 1, 'deadman': True, 'throttle': 60,
                                                          'steer_mrad': 0})
        self.assertEqual(status, 409)
        done = self.hold_until_done(reply['move']['id'])
        move = done['move']
        self.assertEqual(move['reason'], 'planned')
        self.assertAlmostEqual(move['elapsed_s'], planned, delta=0.12)
        self.assertAlmostEqual(self.drive_ms() / 1000.0, planned, delta=0.15)
        with self.stack.lock:
            self.assertEqual(self.stack.fake.drive(), {2: 0, 3: 0})
            self.assertLessEqual(self.stack.fake.motors[1]['pwm'], 102)
        chain = done['chain']
        status, reply = self.page.post('/api/rover/keepalive', {**{k: chain[k] for k in ('lease', 'ticket')},
                                                                'seq': chain['seq'] + 1})
        self.assertTrue(reply['ok'], 'the page drives on with the returned chain')
        status, reply = self.page.post('/api/rover/distance/feedback', {'move_id': move['id'], 'actual_m': 1.1})
        self.assertTrue(reply['ok'], reply)
        self.assertEqual((reply['correction']['from_duty'], reply['correction']['exact']), (40, True))
        status, view = self.page.get_json('/api/rover/distance')
        self.assertEqual(view['corrections']['road_paved']['samples'], 1)
        self.assertEqual([d['duty'] for d in view['corrections']['road_paved']['duties']], [40])
        # the tape sample of 40 % is this speed's own correction and is borrowed (and named) by another speed
        for duty, exact in ((40, True), (58, False)):
            status, reply = self.page.post('/api/rover/distance/plan', {'terrain': 'road_paved', 'duty': duty, 'distance_m': 1})
            self.assertEqual((reply['plan']['correction']['from_duty'], reply['plan']['correction']['exact']), (40, exact))
            self.assertGreater(reply['plan']['factor'], 1.0)
        status, reply = self.page.post('/api/rover/distance/reset', {'terrain': 'road_paved'})
        self.assertEqual(reply['corrections'], {})

    def test_backward_move(self):
        """2026-11-19: direction 'backward' plans and drives in reverse, at most 2 m, with its own tape corrections."""
        plan = lambda body: self.page.post('/api/rover/distance/plan', {'terrain': 'road_paved', 'duty': 40, **body})[1]
        reply = plan({'distance_m': 1.0, 'direction': 'backward'})
        self.assertTrue(reply['ok'], reply)
        self.assertEqual((reply['plan']['direction'], reply['plan']['throttle'], reply['plan']['pwm']), ('backward', -40, 102))
        self.assertEqual(plan({'distance_m': 2.5, 'direction': 'backward'})['plan']['problem'], 'distance_out_of_range_backward')
        self.assertTrue(plan({'distance_m': 2.5})['ok'], 'forward keeps its 3 m')
        self.assertEqual(plan({'distance_m': 1.0, 'direction': 'up'})['plan']['problem'], 'distance_direction_invalid')
        status, reply = self.start({'lease': 'x', 'ticket': 'y', 'seq': 0}, distance=2.5, direction='backward')
        self.assertEqual(reply['error'], 'distance_out_of_range_backward')
        status, reply = self.start({'lease': 'x', 'ticket': 'y', 'seq': 0}, direction='backward')
        self.assertEqual(reply['error'], 'distance_needs_arm', 'Arm first, as forward')
        armed = self.arm()
        status, reply = self.start(armed, direction='backward')
        self.assertTrue(reply['ok'], reply)
        time.sleep(0.3)
        with self.stack.lock:
            self.assertEqual(self.stack.fake.drive(), {2: -102, 3: -102}, 'reverse')
        done = self.hold_until_done(reply['move']['id'])
        self.assertEqual((done['move']['reason'], done['move']['direction']), ('planned', 'backward'))
        status, reply = self.page.post('/api/rover/distance/feedback', {'move_id': done['move']['id'], 'actual_m': 0.9})
        self.assertTrue(reply['ok'], reply)
        status, view = self.page.get_json('/api/rover/distance')
        corr = view['corrections']['road_paved']
        self.assertEqual(([d['duty'] for d in corr['backward_duties']], corr['duties']), ([40], []))
        self.assertEqual(plan({'distance_m': 1.0})['plan']['correction']['samples'], 0, 'forward does not borrow it')
        self.assertEqual(plan({'distance_m': 1.0, 'direction': 'backward'})['plan']['correction']['samples'], 1)
        status, reply = self.page.post('/api/rover/distance/reset', {'terrain': 'road_paved', 'direction': 'sideways'})
        self.assertEqual(reply['error'], 'distance_direction_invalid')
        status, reply = self.page.post('/api/rover/distance/reset', {'terrain': 'road_paved', 'direction': 'backward'})
        self.assertEqual(reply['corrections'], {})

    def test_lost_heartbeat_and_stop(self):
        armed = self.arm()
        status, reply = self.start(armed, distance=2.0)
        self.assertTrue(reply['ok'], reply)
        time.sleep(1.0)                                           # no hold heartbeat at all
        self.assertLess(self.drive_ms(), 700, 'stopped after the 0.4 s heartbeat limit (bridge TTL 0.75 s behind it)')
        status, reply = self.page.post('/api/rover/distance/hold', {'move_id': reply['move']['id']})
        self.assertEqual(reply['move']['reason'], 'heartbeat_lost')
        chain = reply['chain']
        status, reply = self.start(chain, distance=2.0)
        self.assertTrue(reply['ok'], reply)
        time.sleep(0.3)
        status, _ = self.page.post('/api/rover/stop', {'reason': 'operator_stop'})
        time.sleep(0.15)
        with self.stack.lock:
            self.assertEqual(self.stack.fake.drive(), {2: 0, 3: 0})
            self.assertIsNone(self.stack.bridge.control.lease)
        status, reply = self.page.post('/api/rover/distance/hold', {'move_id': reply['move']['id']})
        self.assertTrue(reply['lease_lost'])


class CappedBridge(DistanceRoutes):
    MOTOR = {'max_drive_pwm': 120}

    def test_never_above_the_bridge_cap(self):
        armed = self.arm()
        status, reply = self.start(armed, duty=88, distance=1.0)  # PWM 225 > 120: the table speed would not come
        self.assertEqual(reply['error'], 'distance_duty_above_cap')
        self.assertEqual(self.drive_ms(), 0)
        status, reply = self.page.post('/api/rover/distance/plan', {'terrain': 'road_paved', 'duty': 40, 'distance_m': 1})
        self.assertEqual((reply['ok'], reply['bridge']['max_pwm'], reply['bridge']['max_duty']), (True, 120, 47))
        status, reply = self.page.post('/api/rover/distance/plan', {'terrain': 'road_paved', 'duty': 47, 'distance_m': 1})
        self.assertEqual((reply['ok'], reply['plan']['pwm']), (True, 120), 'the highest speed within the cap')
        status, reply = self.page.post('/api/rover/distance/plan', {'terrain': 'road_paved', 'duty': 48, 'distance_m': 1})
        self.assertEqual((reply['ok'], reply['bridge']['refusal']), (False, 'distance_duty_above_cap'))


class ZeroCapBridge(DistanceRoutes):
    MOTOR = {'max_drive_pwm': 0}

    def test_cap_zero_refuses(self):
        armed = self.arm()
        status, reply = self.start(armed)
        self.assertEqual(reply['error'], 'distance_drive_cap_zero')
        self.assertEqual(self.drive_ms(), 0)


if __name__ == '__main__':
    unittest.main()
