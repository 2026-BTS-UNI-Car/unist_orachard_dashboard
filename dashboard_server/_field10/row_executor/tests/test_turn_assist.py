"""TurnAssist state machine on the fast rig (kinematic plant, real-format scans, scripted estimator): V-T1.

Every state / transition and every failure mode of TURN_DESIGN 3: the full left/right turn, hold-to-run
and new presses at step boundaries, pauses and resume, the breakaway ladder, stall (breakaway / mid
rotation / too slow), wrong direction, rate excess, overshoot (absorbed / end), undershoot (top-up /
end), yaw disagreement, IMU gap, stale LiDAR, clearance (start / during), creep front / backstop,
traverse side, uncommanded motion, precheck failures, clock regression, internal errors, operator stop,
guard violation, dry run, settle not ready and the observe (T1 shadow) mode.
"""
import math
import unittest
from dataclasses import replace

import rowexec_testlib  # noqa: F401  (path setup)
import turn_testlib as T
from row_executor.turn_assist import (ACTIVE, AWAIT, DONE, ENDED, PAUSED, LineEndTracker, PivotTracker, TurnAssist,
                                      TurnRequest, TurnSettings)

REQ = TurnRequest(direction='left', row_width_m=3.5, x_c_m=1.5, reach_m=3.2, headland_depth_m=6.)
RIGHT = replace(REQ, direction='right')


def make(cfg=None, plant=None, **kw):
    cfg = cfg or T.turn_config()
    return T.Rig(lambda factory: TurnAssist(cfg, estimator_factory=factory), plant=plant, **kw)


def start(rig, req=REQ, warm=1., **kw):
    rig.run(warm)
    rig.assist.start(rig.t, req, **dict(dict(motor_output=True), **kw))
    return rig


def run_until(rig, predicate, limit=80., hold=None):
    hold = hold or T.Operator()
    end = rig.t + limit
    while rig.t < end:
        rig.run(.05, hold)
        if predicate(rig):
            return True
    return False


def nonzero(decisions):
    return [(d.left, d.right, d.state) for _, d in decisions if d.left or d.right]


def phase_is(name, mode=None):
    return lambda rig: rig.assist.phase == name and (mode is None or rig.assist.mode == mode)


def ended(rig):
    return rig.assist.phase in (DONE, ENDED)


class FullTurn(unittest.TestCase):
    def check_shapes(self, rig, sign):
        for left, right, state in nonzero(rig.decisions):
            if left > 0 and right > 0:
                self.assertIn(state, ('CREEP_OUT', 'TRAVERSE'))
                self.assertLessEqual(abs(left - right), 10)
                self.assertTrue(20 <= min(left, right) and max(left, right) <= 65, (left, right))
            else:
                self.assertIn(state, ('PIVOT_1', 'PIVOT_2'))
                self.assertEqual((left, right), (-sign * abs(left), sign * abs(left)))
                self.assertTrue(60 <= abs(left) <= 80)

    def test_left_turn_completes(self):
        rig = start(make())
        self.assertTrue(run_until(rig, ended))
        a = rig.assist
        self.assertEqual(a.end_reason, 'turn_done_ready', a.events)
        self.check_shapes(rig, 1)
        self.assertEqual([p['result'] for p in a.pivots], ['pivot_ok', 'pivot_ok'])
        self.assertLess(abs(math.degrees(T.wrap(rig.plant.yaw - math.pi))), 10.)
        self.assertLess(abs(rig.plant.y - 3.5), .5)
        self.assertGreater(rig.plant.x, 2.0 + 1.5 - .1)       # base beyond the line end + x_c
        self.assertTrue(a.handoff['ready'])
        d = rig.decisions[-1][1]
        self.assertTrue(d.terminal)
        self.assertEqual((d.left, d.right), (0, 0))
        self.assertEqual(a.guard.violations, 0)
        self.assertTrue(all(phase in [e[1] for e in a.events] for phase in
                            ('PRECHECK', 'CREEP_OUT', 'PIVOT_1', 'TRAVERSE', 'PIVOT_2', 'SETTLE_ACQUIRE', 'DONE')))

    def test_right_turn_mirrored(self):
        rig = start(make(y=3.5), RIGHT)
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'turn_done_ready', rig.assist.events)
        self.check_shapes(rig, -1)
        self.assertLess(abs(rig.plant.y - 0.), .5)

    def test_each_step_needs_a_new_press(self):
        rig = start(make())
        rig.run(6., True)                    # held since before the start: no rising edge, nothing starts
        self.assertEqual((rig.assist.phase, rig.assist.mode), ('CREEP_OUT', AWAIT))
        self.assertEqual(nonzero(rig.decisions), [])
        rig.run(.3, False)
        rig.run(25., True)                   # one press, held: creep runs and stops, PIVOT_1 waits
        self.assertEqual((rig.assist.phase, rig.assist.mode), ('PIVOT_1', AWAIT))
        last = rig.decisions[-40:]
        self.assertTrue(all((d.left, d.right) == (0, 0) for _, d in last))
        self.assertTrue(any(d.left > 0 for _, d in rig.decisions))

    def test_traverse_aims_at_the_next_corridor_centre(self):
        rig = start(make(y=.25))                 # PRECHECK 0.25 m left of the corridor centre
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'turn_done_ready')
        self.assertAlmostEqual(rig.assist.lateral0, .25, delta=.02)
        self.assertLess(abs(rig.plant.y - 3.5), .2)

    def test_gyro_scale_error_is_learnt_from_the_first_pivot(self):
        rig = make()
        rig.gyro_scale = 1.07                    # real gyro vs scan matching: up to +6..7 % (p90)
        start(rig)
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'turn_done_ready')
        gain = rig.assist.pivots[0]['gyro_gain']
        self.assertLess(gain, .99)
        self.assertLess(abs(rig.assist.pivots[1]['error_deg']), 8.)
        self.assertLess(abs(math.degrees(T.wrap(rig.plant.yaw - math.pi))), 8.)

    def test_pivot_only(self):
        rig = start(make(trunks=[], x=0.), replace(REQ, pivot_only=True, operator_aligned=True))
        rig.band_enabled = False
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'pivot_done')
        shapes = nonzero(rig.decisions)
        self.assertTrue(shapes and all(left == -right and left < 0 for left, right, _ in shapes))
        self.assertLess(abs(math.degrees(T.wrap(rig.plant.yaw - math.pi / 2))), 8.)
        self.assertEqual([p['phase'] for p in rig.assist.pivots], ['PIVOT_1'])

    def test_dry_run_outputs_zero(self):
        rig = start(make(), motor_output=False)
        rig.run(4.)
        rig.run(.3, False)
        rig.run(3., True)
        self.assertTrue(all((d.left, d.right) == (0, 0) for _, d in rig.decisions))
        proposed = [d.summary.get('proposed') for _, d in rig.decisions if d.summary.get('proposed')]
        self.assertTrue(any(p[0] > 0 for p in proposed))


class HoldAndPause(unittest.TestCase):
    def test_release_during_creep_pauses_and_resumes(self):
        rig = start(make())
        op = T.Operator()
        state = dict(done=False)

        def release(r):
            a = r.assist
            if not state['done'] and a.phase == 'CREEP_OUT' and a.mode == ACTIVE and a.step['active_s'] > 1.:
                state['done'] = True
                state['t'] = r.t
                return True
            return False
        op.forced_release = release
        self.assertTrue(run_until(rig, ended, hold=op))
        t_rel = state['t']
        after = [(t, d) for t, d in rig.decisions if t > t_rel][:1]
        self.assertEqual((after[0][1].left, after[0][1].right), (0, 0))
        self.assertEqual(rig.assist.end_reason, 'turn_done_ready')
        self.assertIn(('CREEP_OUT', PAUSED, 'hold_released'), [tuple(e[1:]) for e in rig.assist.events])

    def test_pause_limit(self):
        rig = start(make())
        op = T.Operator()
        op.forced_release = lambda r: (r.assist.phase == 'CREEP_OUT' and r.assist.mode == ACTIVE
                                       and r.assist.step['active_s'] > .3 and r.t % 1. < .5)
        self.assertTrue(run_until(rig, ended, hold=op))
        self.assertEqual(rig.assist.end_reason, 'pause_limit')

    def test_release_mid_pivot_restarts_ladder(self):
        rig = start(make())
        op = T.Operator()
        state = dict(done=False)

        def release(r):
            st = r.assist.step
            if not state['done'] and st is not None and st.get('sub') == 'ROTATE' and r.plant.pivot_rotated > .5:
                state['done'] = True
                return True
            return False
        op.forced_release = release
        self.assertTrue(run_until(rig, ended, hold=op))
        kinds = [(p['phase'], p['kind'], p['d_start']) for p in rig.assist.pivots]
        self.assertIn(('PIVOT_1', 'resume', 60), kinds)
        self.assertEqual(rig.assist.end_reason, 'turn_done_ready')


class Pivots(unittest.TestCase):
    def pivot_duties(self, rig, phase='PIVOT_1'):
        out = []
        for t, d in rig.decisions:
            if d.state == phase and d.left < 0 < d.right:
                if not out or out[-1][1] != d.right:
                    out.append((t, d.right))
        return out

    def test_breakaway_ladder(self):
        rig = start(make(plant=T.Plant(-.5, 0., 0., pivot_min=75)))
        run_until(rig, lambda r: r.assist.phase in ('TRAVERSE', DONE, ENDED))
        duties = self.pivot_duties(rig)
        self.assertEqual([d for _, d in duties], [60, 70, 80])
        self.assertGreaterEqual(duties[1][0] - duties[0][0], 1.6 - 1e-6)   # ramp 0.6 + grace 1.0
        self.assertGreaterEqual(duties[2][0] - duties[1][0], 1.0 - 1e-6)
        self.assertEqual(rig.assist.pivots[0]['d_rot'], 80)
        self.assertEqual(rig.assist.phase, 'TRAVERSE')

    def test_stall_at_cap(self):
        rig = start(make(plant=T.Plant(-.5, 0., 0., pivot_min=95)))
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'pivot_stall')
        self.assertEqual(rig.assist.pivots[0]['stall_kind'], 'breakaway')
        self.assertEqual((rig.decisions[-1][1].left, rig.decisions[-1][1].right), (0, 0))
        loaded = sum(.05 for _, d in rig.decisions if d.left < 0)
        self.assertLess(loaded, 4.5)

    def test_too_slow(self):
        rig = start(make(plant=T.Plant(-.5, 0., 0., rate_per_pct=.05)))     # 3-4 deg/s
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'pivot_too_slow')

    def test_stall_while_rotating(self):
        plant = T.Plant(-.5, 0., 0.)
        plant.w_hook = lambda t, w, p: 0. if p.pivot_rotated > math.radians(30) else w
        rig = start(make(plant=plant))
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'pivot_stall')
        self.assertEqual(rig.assist.pivots[0]['stall_kind'], 'rotating')

    def test_wrong_direction(self):
        plant = T.Plant(-.5, 0., 0.)
        plant.w_hook = lambda t, w, p: -w
        rig = start(make(plant=plant))
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'pivot_wrong_direction')

    def test_rate_excess(self):
        rig = start(make(plant=T.Plant(-.5, 0., 0., rate_per_pct=1.)))     # 60 deg/s
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'pivot_rate_excess')

    def coast_push(self, rig, extra_deg, phases=('PIVOT_1',), times=1):
        """External rotation right after the release (a slide after the pivot), extra_deg in the pivot sign."""
        state = dict(n=0, t0=None)
        rate = math.radians(20.) * (1 if extra_deg > 0 else -1)
        dur = abs(extra_deg) / 20.

        def push(t):
            if state['t0'] is not None and state['t0'] <= t < state['t0'] + dur:
                return (0., 0., rate)
            return (0., 0., 0.)
        rig.plant.push = push

        def hold(r):
            st = r.assist.step
            if (st is not None and r.assist.phase in phases and st.get('sub') == 'COAST' and st.get('release_t')
                    and state['n'] < times and st['release_t'] != state.get('release_t')):
                state['release_t'] = st['release_t']
                state['n'] += 1
                state['t0'] = r.plant.t
            return op(r)
        op = T.Operator()
        return hold

    def test_overshoot_absorbed_then_completes(self):
        rig = start(make())
        hold = self.coast_push(rig, 12.)
        self.assertTrue(run_until(rig, ended, hold=hold))
        self.assertEqual(rig.assist.pivots[0]['result'], 'pivot_overshoot_absorbed')
        self.assertEqual(rig.assist.end_reason, 'turn_done_ready')
        self.assertEqual(rig.assist.warnings.get('pivot_overshoot_absorbed'), 1)

    def test_overshoot_ends(self):
        rig = start(make())
        hold = self.coast_push(rig, 30.)
        self.assertTrue(run_until(rig, ended, hold=hold))
        self.assertEqual(rig.assist.end_reason, 'pivot_overshoot')
        self.assertFalse(any(d.left > 0 and d.right < 0 for _, d in rig.decisions))   # never an opposite pivot

    def test_undershoot_topup(self):
        rig = start(make())
        hold = self.coast_push(rig, -12.)
        self.assertTrue(run_until(rig, ended, hold=hold))
        kinds = [(p['phase'], p['kind'], p.get('result')) for p in rig.assist.pivots]
        self.assertIn(('PIVOT_1', 'topup', 'pivot_ok'), kinds)
        self.assertEqual(rig.assist.end_reason, 'turn_done_ready')

    def test_second_undershoot_ends(self):
        rig = start(make())
        hold = self.coast_push(rig, -14., times=2)
        self.assertTrue(run_until(rig, ended, hold=hold))
        self.assertEqual(rig.assist.end_reason, 'pivot_undershoot')


class Sensors(unittest.TestCase):
    def test_yaw_disagreement(self):
        rig = start(make())

        def err(stamp):
            st = rig.assist.step
            return math.radians(12.) if st is not None and st.get('kind') == 'pivot' and st.get('sub') == 'ROTATE' \
                else 0.
        rig.lidar_heading_error = err
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'yaw_disagreement')

    def gap_in_rotation(self, duration):
        rig = start(make())
        op = T.Operator()
        state = dict(t=None)

        def hold(r):
            st = r.assist.step
            if state['t'] is None and st is not None and st.get('sub') == 'ROTATE' and r.plant.pivot_rotated > .3:
                state['t'] = r.t
            r.imu_on = not (state['t'] is not None and state['t'] <= r.t < state['t'] + duration)
            return op(r)
        self.assertTrue(run_until(rig, ended, hold=hold))
        return rig

    def test_imu_gap_in_pivot(self):
        rig = self.gap_in_rotation(.6)        # stale IMU (0.3 s) pauses at once; the 0.6 s gap then ends the turn
        self.assertEqual(rig.assist.end_reason, 'imu_gap_in_pivot')
        self.assertTrue(all((d.left, d.right) == (0, 0) for t, d in rig.decisions if d.state == 'ENDED'))

    def test_short_imu_gap_is_bridged(self):
        rig = self.gap_in_rotation(.25)       # below imu_max_age_s and turn_imu_gap_abort_s
        self.assertEqual(rig.assist.end_reason, 'turn_done_ready')
        self.assertGreater(rig.assist.max_imu_gap_watch, .2)

    def test_stale_lidar_pauses_then_resumes(self):
        rig = start(make())
        op = T.Operator()
        state = dict(t=None)

        def hold(r):
            a = r.assist
            if state['t'] is None and a.phase == 'CREEP_OUT' and a.mode == ACTIVE and a.step['active_s'] > 1.:
                state['t'] = r.t
            r.scan_on = not (state['t'] is not None and state['t'] <= r.t < state['t'] + .8)
            return op(r)
        self.assertTrue(run_until(rig, ended, hold=hold))
        self.assertIn('stale_lidar', [e[3] for e in rig.assist.events])
        self.assertEqual(rig.assist.stale_events, 1)
        self.assertEqual(rig.assist.end_reason, 'turn_done_ready')

    def test_stale_repeat_limit(self):
        rig = start(make())
        op = T.Operator()

        def hold(r):
            a = r.assist
            r.scan_on = not (a.phase == 'CREEP_OUT' and a.mode == ACTIVE and a.step['active_s'] > .5)
            return op(r)
        self.assertTrue(run_until(rig, ended, hold=hold))
        self.assertEqual(rig.assist.end_reason, 'stale_repeat_limit')


class Geometry(unittest.TestCase):
    def test_clearance_lost_during_pivot(self):
        rig = start(make())
        op = T.Operator()

        def hold(r):
            st = r.assist.step
            if st is not None and st.get('sub') == 'ROTATE' and not r.obstacles:
                r.obstacles.append((r.plant.x + .8 * math.cos(r.plant.yaw + 2.), r.plant.y + .8 * math.sin(r.plant.yaw + 2.),
                                    .1))
            return op(r)
        self.assertTrue(run_until(rig, ended, hold=hold))
        self.assertEqual(rig.assist.end_reason, 'pivot_clearance_lost')

    def test_pivot_start_clearance(self):
        rig = start(make())
        op = T.Operator()

        def hold(r):
            if r.assist.phase == 'PIVOT_1' and r.assist.mode == AWAIT and not r.obstacles:
                r.obstacles.append((r.plant.x, r.plant.y - 1.1, .1))
            return op(r)
        self.assertTrue(run_until(rig, ended, hold=hold))
        self.assertEqual(rig.assist.end_reason, 'pivot_start_clearance')

    def test_creep_front_blocked(self):
        rig = start(make(obstacles=[(2.4, 0., .2)]))
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'creep_front_blocked')
        self.assertTrue(all(d.left >= 0 and d.right >= 0 for _, d in rig.decisions))

    def test_creep_backstop_when_the_line_never_ends(self):
        rig = start(make(trunks=T.orchard(3.5, x_end=40.)), replace(REQ, creep_max_m=2.))
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'creep_backstop')
        self.assertLess(rig.plant.x, -.5 + 2.6)

    def test_traverse_side_blocked(self):
        rig = start(make())
        op = T.Operator()

        def hold(r):
            a = r.assist
            if a.phase == 'TRAVERSE' and a.mode == ACTIVE and a.step['active_s'] > 1. and not r.obstacles:
                # row side of a left turn is the vehicle's left (towards -x of the turn frame)
                r.obstacles.append((r.plant.x - .6, r.plant.y, .1))     # 0.15 m beside the left side
            return op(r)
        self.assertTrue(run_until(rig, ended, hold=hold))
        self.assertEqual(rig.assist.end_reason, 'traverse_side_blocked')

    def test_uncommanded_motion(self):
        rig = start(make())
        op = T.Operator(max_presses=1)

        def hold(r):
            if r.assist.phase == 'PIVOT_1' and r.assist.mode == AWAIT:
                r.plant.push = lambda t: (.3, 0., 0.)
            return op(r)
        self.assertTrue(run_until(rig, ended, hold=hold))
        self.assertEqual(rig.assist.end_reason, 'uncommanded_motion')

    def test_line_end_tracker(self):
        lt = LineEndTracker(.6, 4., dict(left=1.75, right=-1.75))
        import numpy as np
        xs = np.array([-3., -.5, 2., 2.3, 8.5, 10.] + [-2.5, 6.])     # left: trunks to 2.3, next block 8.5
        ys = np.array([1.75] * 6 + [-1.75] * 2)                       # right: missing tree at 0..5 (gap 8.5)
        lt.update(xs, ys)
        self.assertAlmostEqual(lt.line_end('left'), 2.3)
        self.assertAlmostEqual(lt.end['right'], 0.)          # nothing within 4 m ahead of the start
        lt.update(np.array([3.5]), np.array([1.75]))          # within 4 m of the start: extends
        self.assertAlmostEqual(lt.line_end('left'), 3.5)
        lt2 = LineEndTracker(.6, 4., dict(left=1.75))
        lt2.update(np.array([-2., 3.9, 7.5]), np.array([1.75, 1.75, 1.75]))   # missing tree beside the vehicle
        self.assertAlmostEqual(lt2.line_end('left'), 7.5)


class Precheck(unittest.TestCase):
    def test_not_still_times_out(self):
        rig = make()
        rig.vibration = 1.
        start(rig)
        self.assertTrue(run_until(rig, ended, limit=15.))
        self.assertEqual(rig.assist.end_reason, 'precheck_timeout:vibration')

    def test_gyro_bias(self):
        rig = make()
        rig.gyro_bias = math.radians(.6)
        start(rig)
        self.assertTrue(run_until(rig, ended, limit=15.))
        self.assertEqual(rig.assist.end_reason, 'precheck_timeout:gyro_bias')

    def test_small_bias_is_removed(self):
        rig = make()
        rig.gyro_bias = math.radians(.2)
        start(rig)
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'turn_done_ready')
        self.assertAlmostEqual(math.degrees(rig.assist.b0), .2, delta=.02)

    def test_next_corridor_absent(self):
        rig = make()
        rig.next_votes['left'] = 'ABSENT'
        start(rig)
        self.assertTrue(run_until(rig, ended, limit=10.))
        self.assertEqual(rig.assist.end_reason, 'next_corridor_absent')
        rig2 = make()
        rig2.next_votes['left'] = 'ABSENT'
        start(rig2, replace(REQ, allow_next_absent=True))
        self.assertTrue(run_until(rig2, phase_is('CREEP_OUT'), limit=10.))

    def test_row_axis_sources(self):
        rig = make()
        rig.band_enabled = False
        start(rig)
        self.assertTrue(run_until(rig, ended, limit=15.))
        self.assertEqual(rig.assist.end_reason, 'precheck_timeout:row_axis_unknown')
        for req, source in ((replace(REQ, operator_aligned=True), 'operator_aligned'),
                            (replace(REQ, row_heading_error_deg=0.), 'row_run_hint')):
            rig = make()
            rig.band_enabled = False
            start(rig, req)
            self.assertTrue(run_until(rig, phase_is('CREEP_OUT'), limit=10.))
            self.assertEqual(rig.assist.row_source, source)
            self.assertEqual(rig.assist.warnings.get('row_axis_' + source), 1)

    def test_row_width(self):
        rig = make()
        rig.band_width = 5.
        start(rig)
        self.assertTrue(run_until(rig, ended, limit=10.))
        self.assertEqual(rig.assist.end_reason, 'row_width_mismatch')
        rig = make()
        rig.band_width = 2.4                 # 1.1 m narrower: no end, but W not confirmed (warning once)
        start(rig)
        self.assertTrue(run_until(rig, phase_is('CREEP_OUT'), limit=10.))
        self.assertEqual(rig.assist.warnings.get('row_width_differs'), 1)
        rig = make()
        rig.band_width = 3.0                 # canopy-narrowed band within 2 tol: confirmed, no warning
        start(rig)
        self.assertTrue(run_until(rig, phase_is('CREEP_OUT'), limit=10.))
        self.assertIsNone(rig.assist.warnings.get('row_width_differs'))


class Robustness(unittest.TestCase):
    def test_clock_regression(self):
        rig = start(make())
        rig.run(3.)
        d = rig.assist.tick(rig.t - 1., False)
        self.assertTrue(d.terminal)
        self.assertEqual(rig.assist.end_reason, 'clock_regression')

    def test_internal_error_ends_with_zero(self):
        rig = start(make())
        rig.run(4.)
        rig.run(.3, False)
        rig.run(1.5, True)
        self.assertEqual(rig.assist.mode, ACTIVE)

        def boom(now):
            raise RuntimeError('estimator failure')
        rig.assist.est.snapshot = boom
        d = rig.assist.tick(rig.t + .05, True)
        self.assertTrue(d.terminal)
        self.assertEqual((d.left, d.right), (0, 0))
        self.assertEqual(rig.assist.end_reason, 'internal_error:RuntimeError')

    def test_sensor_callback_error_ends_at_next_tick(self):
        rig = start(make())
        rig.run(4.)

        def boom(*a):
            raise ValueError('bad scan')
        rig.assist.est.on_scan = boom
        rig.run(.3)
        self.assertEqual(rig.assist.end_reason, 'internal_error:scan:ValueError')

    def test_operator_stop(self):
        rig = start(make())
        rig.run(4.)
        rig.run(.3, False)
        rig.run(1.5, True)
        rig.assist.abort(rig.t, 'operator_stop')
        d = rig.assist.tick(rig.t + .05, True)
        self.assertTrue(d.terminal)
        self.assertEqual((d.left, d.right), (0, 0))
        self.assertEqual(rig.assist.end_reason, 'operator_stop')

    def test_guard_violation_ends(self):
        rig = start(make())
        rig.assist._forward_tick = lambda now, s: (-60, 60)
        self.assertTrue(run_until(rig, ended, limit=10.))
        self.assertEqual(rig.assist.end_reason, 'turn_guard_violation:shape_not_allowed_in_phase')
        self.assertFalse(any(d.left < 0 for _, d in rig.decisions))

    def test_restart_after_end(self):
        rig = start(make())
        rig.assist.abort(rig.t)
        rig.run(.1)
        rig.assist.start(rig.t, REQ, motor_output=True)
        self.assertEqual(rig.assist.phase, 'PRECHECK')
        with self.assertRaises(RuntimeError):
            rig.assist.start(rig.t, REQ, motor_output=True)


class Settle(unittest.TestCase):
    def drop_band_before_settle(self, rig):
        op = T.Operator()

        def hold(r):
            if r.assist.phase == 'PIVOT_2':
                r.band_enabled = False
            return op(r)
        return hold

    def test_odometry_fallback_when_the_corridor_is_not_acquired(self):
        rig = start(make())
        self.assertTrue(run_until(rig, ended, hold=self.drop_band_before_settle(rig)))
        self.assertEqual(rig.assist.end_reason, 'turn_done_ready_odometry')
        self.assertEqual(rig.assist.handoff['source'], 'odometry')
        self.assertTrue(rig.assist.handoff['ready'])
        self.assertLess(abs(rig.assist.handoff['odometry_lateral_m']), .4)

    def test_not_ready_when_w_was_not_confirmed(self):
        rig = make()
        rig.band_width = 2.4                 # PRECHECK band narrower than W - 2 tol: W not confirmed
        start(rig, replace(REQ, row_width_m=3.5))
        self.assertTrue(run_until(rig, ended, hold=self.drop_band_before_settle(rig)))
        self.assertEqual(rig.assist.warnings.get('row_width_differs'), 1)
        self.assertEqual(rig.assist.end_reason, 'turn_done_not_ready')
        self.assertFalse(rig.assist.handoff['ready'])


class Observe(unittest.TestCase):
    def test_observe_logs_a_manual_turn(self):
        rig = make()
        script = [(3., (0, 0)), (9.5, (55, 55)), (1.5, (0, 0)), (7.2, (-60, 60)), (1.5, (0, 0)), (7.5, (55, 55)),
                  (1.5, (0, 0)), (7.2, (-60, 60)), (10., (0, 0))]
        state = dict(t0=None)

        def manual(r):
            if state['t0'] is None:
                state['t0'] = r.t
            t = r.t - state['t0']
            for dur, cmd in script:
                if t < dur:
                    return cmd
                t -= dur
            return (0, 0)
        rig.manual = manual
        start(rig, observe=True, motor_output=False)
        rig.run(52., True)
        a = rig.assist
        self.assertTrue(all((d.left, d.right) == (0, 0) for _, d in rig.decisions))
        log = a.status()['observe_log']
        self.assertGreaterEqual(len(log['rotations']), 2)
        self.assertFalse(any(rot['would_abort'] for rot in log['rotations']))
        self.assertIsNotNone(log['creep_ready'])
        self.assertIsNotNone(log['acquisition'])
        self.assertEqual(a.end_reason, 'observe_done')


class Tracker(unittest.TestCase):
    def settings(self):
        return TurnSettings.from_config(T.turn_config())

    def test_confirmation_needs_duration_and_lidar(self):
        tr = PivotTracker(self.settings(), 1, 0.)
        self.assertIsNone(tr.update(.1, 10., 1., .5, 60, 0., .6, False))
        self.assertIsNone(tr.update(.4, 10., 4., .5, 60, 0., .6, False))     # 0.3 s, but LiDAR < 1.5 deg
        self.assertFalse(tr.rotating)
        self.assertIsNone(tr.update(.45, 10., 4.5, 2., 60, 0., .6, False))
        self.assertTrue(tr.rotating)
        self.assertEqual(tr.d_rotating, 60)

    def test_gyro_only_when_odometry_invalid(self):
        tr = PivotTracker(self.settings(), -1, 0.)
        tr.update(.1, -10., 1., None, 60, 0., .6, False)
        tr.update(.4, -10., 4., None, 60, 0., .6, False)
        self.assertTrue(tr.rotating)

    def test_stall_and_too_slow_at_cap(self):
        st = self.settings()
        tr = PivotTracker(st, 1, 0.)
        self.assertIsNone(tr.update(1., .5, 0., 0., 80, 0., .1, True))
        self.assertEqual(tr.update(1.1, .5, 0., 0., 80, 0., .1, True), 'pivot_stall')
        tr = PivotTracker(st, 1, 0.)
        self.assertEqual(tr.update(1.1, 3., 2., 2., 80, 0., .1, True), 'pivot_too_slow')

    def test_mid_rotation_stall_window(self):
        tr = PivotTracker(self.settings(), 1, 0.)
        tr.update(.1, 10., 1., 2., 60, 0., .6, False)
        tr.update(.4, 10., 4., 2., 60, 0., .6, False)
        result = None
        for k in range(30):
            result = tr.update(.45 + .05 * k, 10., 4. + .01 * k, 2. + .01 * k, 60, 0., .6, False)
            if result:
                break
        self.assertEqual(result, 'pivot_stall')


if __name__ == '__main__':
    unittest.main()
