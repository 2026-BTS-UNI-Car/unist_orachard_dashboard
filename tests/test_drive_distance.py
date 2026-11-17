"""Distance moves (dashboard_server/drive_distance.py, 2026-11-17): the October 2026 table, the planner (inversion,
limits, grades), the correction store and the move runner driving the real ESP32-S3 bridge code against the fake board
(tests/fake_esp32.py) on an injected clock. No hardware, no socket. The table values are the vehicle BEFORE the
steering unit; the fake board's drive is a test double: these are protocol and timing checks, not distances.
Run: python -B -m unittest tests.test_drive_distance
"""
import concurrent.futures
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from dashboard_server import drive_distance as dd
from scripts import esp32_abs_hold as esp
from tests.test_esp32_bridge import Rig

ROOT = Path(__file__).resolve().parent.parent
TABLE = dd.load_table()


class Table(unittest.TestCase):
    def test_provenance_and_values_from_the_october_tables(self):
        self.assertEqual(TABLE['version'], '202610')
        self.assertIn('before the steering unit', TABLE['source']['vehicle'])
        self.assertIn('ESP8266', TABLE['source']['vehicle'])
        keys = [t['key'] for t in TABLE['terrains']]
        self.assertEqual(keys, ['road_paved', 'orchard_row_1010', 'orchard_other_1010', 'orchard_1003'])
        for terrain in TABLE['terrains']:
            for row in terrain['speeds']:
                self.assertIn(row['grade'], ('측정', '참고'), row)
                self.assertIn(row['reference'], ('LiDAR', 'GNSS'))
                self.assertEqual(row['source'], 'T3_speed_by_ground')
                self.assertEqual(row['grade'] == '측정', row['segments'] >= 5, row)
        # spot checks against tables/T3_speed_by_ground.md of the simulation repository (2026-11-12)
        speed = {(t['key'], r['duty']): r['speed_m_s'] for t in TABLE['terrains'] for r in t['speeds']}
        self.assertEqual(speed[('road_paved', 100)], 0.942)
        self.assertEqual(speed[('orchard_row_1010', 90)], 0.706)
        self.assertEqual(speed[('orchard_other_1010', 100)], 0.896)
        self.assertEqual(speed[('orchard_1003', 70)], 0.641)

    def test_source_tables_when_the_simulation_repository_is_next_to_us(self):
        repo = Path(os.environ.get('UBTS_SIMULATION_REPO') or ROOT.parent / 'unist_orchard_simulation')
        source = repo / 'docs' / 'drive_response' / 'tables' / 'T3_speed_by_ground.md'
        if not source.is_file():
            self.skipTest('simulation repository not found (UBTS_SIMULATION_REPO)')
        text = source.read_text(encoding='utf-8')
        columns = {'road_paved': 1, 'orchard_1003': 2, 'orchard_row_1010': 3, 'orchard_other_1010': 4}
        for terrain in TABLE['terrains']:
            for row in terrain['speeds']:
                line = next(l for l in text.splitlines() if l.startswith('| %d |' % row['duty']))
                cell = line.split('|')[1 + columns[terrain['key']]].strip()
                m = re.match(r'([\d.]+) \[(측정|참고)( GNSS)?, n (\d+)\]', cell)
                self.assertIsNotNone(m, (terrain['key'], row['duty'], cell))
                self.assertEqual((float(m[1]), m[2], 'GNSS' if m[3] else 'LiDAR', int(m[4])),
                                 (row['speed_m_s'], row['grade'], row['reference'], row['segments']))


class Planner(unittest.TestCase):
    def test_start_lag_and_coast(self):
        self.assertEqual(dd.start_lag_s(TABLE), 0.61)             # 2 - 1.29 / 0.928
        self.assertEqual(dd.coast_m(TABLE, 100, 0.9), (0.55, '측정'))
        value, grade = dd.coast_m(TABLE, 40, 0.373)
        self.assertAlmostEqual(value, 0.373 * 0.25 + 0.373 ** 2 / 2.6, places=3)
        self.assertEqual(grade, '모델')

    def test_inversion(self):
        for terrain in TABLE['terrains']:
            for row in terrain['speeds']:
                for distance in (1.0, 2.0, 3.0):
                    p = dd.plan(TABLE, terrain['key'], row['duty'], distance)
                    if p['problem']:
                        continue
                    v, lag, coast = p['speed']['value'], p['lag']['value'], p['coast']['value']
                    self.assertAlmostEqual(v * (p['drive_s'] - lag) + coast, distance, places=2)
                    self.assertAlmostEqual(p['expected_m'], distance, places=2)
                    self.assertGreaterEqual(p['drive_s'], dd.DRIVE_MIN_S)
        p = dd.plan(TABLE, 'road_paved', 40, 1.0)
        self.assertAlmostEqual(p['drive_s'], 0.61 + (1.0 - p['coast']['value']) / 0.373, places=2)
        self.assertEqual((p['throttle'], p['pwm']), (40, 102))

    def test_correction_factor(self):
        base = dd.plan(TABLE, 'road_paved', 40, 1.0)
        longer = dd.plan(TABLE, 'road_paved', 40, 1.0, factor=1.25)  # the vehicle went 25 % further than the model
        self.assertLess(longer['drive_s'], base['drive_s'])
        self.assertAlmostEqual(longer['expected_m'], 1.0, places=2)
        self.assertEqual(dd.plan(TABLE, 'road_paved', 40, 1.0, factor=9)['factor'], dd.FACTOR_MAX)
        self.assertEqual(dd.plan(TABLE, 'road_paved', 40, 1.0, factor=0.1)['factor'], dd.FACTOR_MIN)

    def test_limits_and_grades(self):
        self.assertEqual(dd.plan(TABLE, 'road_paved', 100, 1.0)['problem'], 'distance_too_short_for_duty')
        self.assertEqual(dd.plan(TABLE, 'road_paved', 100, 1.0)['min_distance_m'], 1.2)
        self.assertEqual(dd.plan(TABLE, 'road_paved', 40, 3.5)['problem'], 'distance_out_of_range')
        self.assertEqual(dd.plan(TABLE, 'road_paved', 40, 0.1)['problem'], 'distance_out_of_range')
        self.assertEqual(dd.plan(TABLE, 'road_paved', 50, 1.0)['problem'], 'distance_duty_without_data',
                         'no interpolated duty is offered')
        self.assertEqual(dd.plan(TABLE, 'road_paved', 50, 1.0)['duties'], [40, 58, 88, 90, 95, 100])
        self.assertEqual(dd.plan(TABLE, 'moon', 40, 1.0)['problem'], 'distance_terrain_unknown')
        for bad in (True, '1.0', float('nan')):
            self.assertEqual(dd.plan(TABLE, 'road_paved', 40, bad)['problem'], 'distance_invalid')
        self.assertEqual(dd.plan(TABLE, 'road_paved', True, 1.0)['problem'], 'distance_duty_without_data')
        p = dd.plan(TABLE, 'orchard_row_1010', 100, 2.0)
        self.assertEqual((p['speed']['grade'], p['lag']['grade'], p['coast']['grade'], p['grade']),
                         ('측정', '측정', '측정', '측정'))
        p = dd.plan(TABLE, 'orchard_1003', 55, 1.0)
        self.assertEqual((p['speed']['grade'], p['speed']['reference'], p['lag']['grade'], p['coast']['grade'],
                          p['grade']), ('참고', 'GNSS', '가정', '모델', '가정'))
        json.dumps(p, allow_nan=False)

    def test_table_view_offers_only_duties_with_data(self):
        view = dd.table_view(TABLE)
        road = next(t for t in view['terrains'] if t['key'] == 'road_paved')
        self.assertEqual([d['duty'] for d in road['duties']], [40, 58, 88, 90, 95, 100])
        self.assertEqual(view['limits']['distance_default_m'], 1.0)
        self.assertIn('not been measured', view['not_measured'])


class Store(unittest.TestCase):
    def test_factor_median_clamp_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = dd.CorrectionStore(Path(tmp) / 'sub' / 'corr.json')
            self.assertEqual(store.factor('road_paved'), 1.0)
            for ratio in (1.2, 1.1, 1.3):
                store.add('road_paved', {'ratio': ratio})
            self.assertEqual(store.factor('road_paved'), 1.2)
            for _ in range(6):
                store.add('road_paved', {'ratio': 3.0})
            self.assertEqual(store.factor('road_paved'), dd.FACTOR_MAX, 'clamped')
            self.assertEqual(store.view()['road_paved']['samples'], 9)
            store.add('orchard_row_1010', {'ratio': 0.9})
            raw = (Path(tmp) / 'sub' / 'corr.json').read_bytes()
            self.assertNotIn(b'\r', raw)
            self.assertEqual(json.loads(raw)['schema'], dd.STORE_SCHEMA)
            store.reset('road_paved')
            self.assertEqual((store.factor('road_paved'), store.factor('orchard_row_1010')), (1.0, 0.9))
            store.reset()
            self.assertEqual(store.view(), {})
            (Path(tmp) / 'sub' / 'corr.json').write_bytes(b'not json')
            self.assertEqual(store.factor('road_paved'), 1.0, 'a damaged file reads as empty')


class MoveRig(Rig):
    """The real bridge code and fake board on the fake clock; the runner's request() goes through the bridge queue."""

    def __init__(self, fake_options=None, **motor_options):
        super().__init__(dict(dict(drive_watchdog_ms=300), **(fake_options or {})), **motor_options)
        self.runner = dd.MoveRunner(self.request, clock=self.clock, threaded=False)
        self.session = 'operator-a'

    def request(self, payload):
        if payload.get('op') in ('status', 'stop'):
            return self.bridge.request(payload)
        future = concurrent.futures.Future()
        self.bridge.pending.put_nowait((self.clock(), payload, future))
        self.bridge.step()
        return future.result(timeout=0)

    def start(self, terrain='road_paved', duty=40, distance=1.0):
        self.plan = dd.plan(TABLE, terrain, duty, distance)
        chain = {'lease': self.lease, 'ticket': self.ticket, 'seq': self.seq}
        self.drive_ms0 = self.fake.drive_on_ms
        # the page holds the steering where it is (after arm its target is the measured angle)
        return self.runner.start(self.session, self.plan, chain, self.bridge.snapshot['steering']['measured_mrad'])

    def drive(self, seconds, hold_every=0.1, release_at=None, dt=0.01):
        """Run the bridge and the runner; the page holds (heartbeat) every hold_every s (None: never)."""
        t, since = 0.0, 0.0
        reply = None
        while t < seconds - 1e-9:
            self.clock.t += dt
            t += dt
            since += dt
            self.bridge.step()
            self.runner.step(self.clock())
            m = self.runner.move
            if release_at is not None and t >= release_at and m['state'] == 'driving':
                reply = self.runner.release(self.session, m['id'])
            elif hold_every is not None and since >= hold_every - 1e-9:
                since = 0.0
                reply = self.runner.hold(self.session, m['id']) if m['state'] != 'closed' else reply
        return reply

    def drive_s(self):
        return (self.fake.drive_on_ms - self.drive_ms0) / 1000.0


class Runner(unittest.TestCase):
    def setUp(self):
        self.rig = MoveRig()
        self.rig.ready()
        self.assertTrue(self.rig.arm()['ok'])

    def test_planned_move_drives_for_the_planned_time_and_hands_the_chain_back(self):
        rig = self.rig
        reply = rig.start()
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(rig.fake.drive(), {2: 102, 3: 102}, 'duty 40 % -> PWM 102 on both drive motors')
        final = rig.drive(rig.plan['drive_s'] + 0.5)
        self.assertIn('chain', final)
        move = final['move']
        self.assertEqual((move['state'], move['reason']), ('closed', 'planned'))
        self.assertAlmostEqual(move['elapsed_s'], rig.plan['drive_s'], delta=0.03)
        self.assertAlmostEqual(rig.drive_s(), rig.plan['drive_s'], delta=0.05, msg='the board drove that long')
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})
        self.assertTrue(move['feedback_open'])
        # the page takes the chain back and keeps control with it
        chain = final['chain']
        self.assertEqual(chain['lease'], rig.lease)
        rig.ticket, rig.seq = chain['ticket'], chain['seq']
        self.assertTrue(rig.owned('keepalive')['ok'])
        self.assertFalse(rig.runner.busy())

    def test_release_stops_at_once(self):
        rig = self.rig
        rig.start(distance=2.0)
        final = rig.drive(1.5, release_at=0.6)
        self.assertEqual(final['move']['reason'], 'released')
        self.assertAlmostEqual(rig.drive_s(), 0.6, delta=0.05)
        self.assertFalse(final['move']['feedback_open'], 'a cut-short move is not a tape sample')

    def test_lost_heartbeat_stops_and_an_unclaimed_lease_ends_with_stop(self):
        rig = self.rig
        rig.start(distance=2.0)
        rig.drive(1.0, hold_every=None)
        self.assertAlmostEqual(rig.drive_s(), dd.HOLD_TIMEOUT_S, delta=0.06)
        self.assertEqual(rig.runner.move['reason'], 'heartbeat_lost')
        self.assertTrue(rig.runner.busy(), 'the chain waits for the page')
        rig.drive(dd.TAKE_TIMEOUT_S + 0.5, hold_every=None)
        self.assertFalse(rig.runner.busy())
        rig.run(0.1)
        self.assertIsNone(rig.bridge.control.lease, 'nobody took the lease back: STOP')
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})

    def test_stop_wins(self):
        rig = self.rig
        rig.start(distance=2.0)
        rig.drive(0.5)
        rig.runner.abort('operator_stop')
        rig.bridge.request({'op': 'stop', 'reason': 'operator_stop'})
        rig.drive(0.3, hold_every=None)
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})
        self.assertEqual(rig.runner.move['state'], 'stopped')
        self.assertFalse(rig.runner.busy())
        reply = rig.runner.hold(rig.session, rig.runner.move['id'])
        self.assertTrue(reply['lease_lost'])

    def test_steering_is_held_and_never_more_than_one_move(self):
        rig = self.rig
        rig.start()
        self.assertEqual(rig.runner.start(rig.session, rig.plan, {'lease': 'x', 'ticket': 'y', 'seq': 1}, 0)['error'],
                         'distance_move_running')
        rig.drive(0.5)
        drives = [line for line in rig.tr.written() if line.startswith('ANGLE')]
        self.assertEqual(drives, [], 'the measured angle is held: no new steering target')

    def test_feedback_updates_the_store(self):
        rig = self.rig
        rig.start()
        final = rig.drive(rig.plan['drive_s'] + 0.3)
        with tempfile.TemporaryDirectory() as tmp:
            store = dd.CorrectionStore(Path(tmp) / 'corr.json')
            move_id = final['move']['id']
            self.assertEqual(rig.runner.feedback('someone-else', move_id, 1.2, store)['error'], 'distance_move_unknown')
            self.assertEqual(rig.runner.feedback(rig.session, move_id, 0, store)['error'], 'distance_actual_invalid')
            reply = rig.runner.feedback(rig.session, move_id, 1.2, store)
            self.assertTrue(reply['ok'], reply)
            self.assertAlmostEqual(reply['sample']['ratio'], 1.2 / reply['sample']['model_m'], places=3)
            self.assertEqual(store.view()['road_paved']['samples'], 1)
            self.assertEqual(rig.runner.feedback(rig.session, move_id, 1.2, store)['error'], 'distance_feedback_done')
            self.assertGreater(store.factor('road_paved'), 1.0)

    def test_a_stale_chain_is_refused_by_the_bridge(self):
        rig = self.rig
        rig.ticket = 'stale'
        reply = rig.start()
        self.assertFalse(reply['ok'])
        self.assertEqual(reply['error'], 'stale_input_rearm_required')
        self.assertFalse(rig.runner.busy())
        self.assertEqual(rig.fake.drive(), {2: 0, 3: 0})


if __name__ == '__main__':
    unittest.main()
