"""Adversarial safety review fixes of TurnAssist (2026-10-22): one test per fix, plus no-regression checks.

  R1 a press accepted before the forward <-> pivot fade wait no longer starts the step when the inputs went
     stale (or the vehicle moved) during the wait; a NEW press with fresh inputs is required.
  R2 CREEP_OUT is bounded by the tape-measured headland depth (the 0.6 m LiDAR plane does not see a ditch or
     a road edge): while the pivot disc / front is not clear the creep no longer runs on to creep_max.
  R3 a body mask that hides returns outside the footprint makes the pivot disc partly blind: turn output is
     refused (turn_feasibility problem); a mask inside the footprint stays allowed.
  R4 the dead-reckoned hand-off (turn_done_ready_odometry) is refused after a LiDAR gap / invalid scan
     matching while driving.
  R5 TurnGuard refuses the first tick of a new pivot once a budget is exhausted.
  R6 a pivot that is still commanded beyond its planned rotation + pivot_overshoot_max_deg ends at once.
  R7 a non-finite tick time ends the run with 0/0.
"""
import math
import unittest
from dataclasses import replace

import rowexec_testlib as L
import turn_testlib as T
from row_executor.config import ConfigError, body_mask_blind_beams, turn_feasibility
from row_executor.guard import PIVOT, TurnGuard
from row_executor.turn_assist import ACTIVE, AWAIT, DONE, ENDED, TurnAssist, TurnRequest, TurnRequestError

REQ = TurnRequest(direction='left', row_width_m=3.5, x_c_m=1.5, reach_m=3.2, headland_depth_m=6.)


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


def ended(rig):
    return rig.assist.phase in (DONE, ENDED)


def beam(body_deg):
    """Raw grid beam that looks at this base-frame angle (mount offset 179.08 deg)."""
    return int(round((math.radians(body_deg - L.OFFSET_DEG) - L.RAW_AMIN) / L.RAW_INC)) % L.BEAMS


class PendingStart(unittest.TestCase):
    def reach_traverse_await(self, rig):
        op = T.Operator(release_s=.05)
        self.assertTrue(run_until(rig, lambda r: r.assist.phase == 'TRAVERSE' and r.assist.mode == AWAIT, hold=op))

    def test_stale_imu_during_the_fade_wait_cancels_the_press(self):
        # a long shape-change wait makes the window visible (default: |d|/100 + 0.32 s)
        cfg = T.turn_config(pivot_duty_start_pct=80, pivot_duty_cap_pct=90, turn_shape_change_extra_s=2.)
        rig = start(make(cfg, plant=T.Plant(x=-.5, pivot_min=75)))
        self.reach_traverse_await(rig)
        rig.run(.05, False)
        rig.run(.05, True)                          # accepted, waiting for the fade
        self.assertTrue(rig.assist._start_pending)
        self.assertEqual(rig.assist.reason, 'waiting_fade')
        rig.imu_on = False                          # the IMU stops right after the press
        out = rig.run(2., True)
        self.assertEqual([(d.left, d.right) for _, d in out if d.left or d.right], [])
        self.assertFalse(rig.assist._start_pending)
        self.assertEqual(rig.assist.reason, 'press_ignored_inputs_not_fresh')
        self.assertEqual(rig.assist.warnings.get('pending_start_cancelled'), 1)
        rig.imu_on = True
        out = rig.run(2., True)                     # fresh again, still holding: nothing starts
        self.assertEqual([(d.left, d.right) for _, d in out if d.left or d.right], [])
        self.assertEqual((rig.assist.phase, rig.assist.mode), ('TRAVERSE', AWAIT))
        rig.run(.05, False)
        out = rig.run(3.5, True)                    # a NEW press after recovery starts the traverse
        self.assertTrue(any(d.left > 0 and d.right > 0 for _, d in out))

    def test_short_fade_wait_still_starts(self):
        cfg = T.turn_config(pivot_duty_start_pct=80, pivot_duty_cap_pct=90, turn_shape_change_extra_s=2.)
        rig = start(make(cfg, plant=T.Plant(x=-.5, pivot_min=75)))
        self.reach_traverse_await(rig)
        rig.run(.05, False)
        out = rig.run(4., True)
        self.assertTrue(any(d.left > 0 and d.right > 0 for _, d in out))
        self.assertNotIn('pending_start_cancelled', rig.assist.warnings)


class HeadlandLimit(unittest.TestCase):
    # posts parallel to the travel 1.15 m right of the path keep the pivot disc (1.28 m) from clearing;
    # the headland boundary (a ditch) is invisible to the scan and was tape-measured 4.5 m from the trunk line
    POSTS = [(2. + .3 * k, -1.15, .05) for k in range(40)]

    def run_case(self, x0):
        req = replace(REQ, headland_depth_m=4.5, creep_max_m=6.)
        rig = start(make(obstacles=self.POSTS, x=x0), req)
        front = []

        def hold(r, op=T.Operator()):
            front.append(r.plant.x + T.HALF_L)
            return op(r)
        self.assertTrue(run_until(rig, ended, hold=hold, limit=60.))
        return rig, max(front) - 2.0             # beyond the last trunk (x = 2.0)

    def test_creep_stops_inside_the_measured_depth(self):
        for x0 in (-.5, 1., 2.3):                # PRECHECK inside the row, near the end, just past it
            with self.subTest(x0=x0):
                rig, beyond = self.run_case(x0)
                self.assertEqual(rig.assist.end_reason, 'creep_headland_limit')
                self.assertLess(beyond, 4.5)
                self.assertTrue(all(d.left >= 0 and d.right >= 0 for _, d in rig.decisions))

    def test_minimum_depth_turn_still_completes(self):
        req = replace(REQ, headland_depth_m=REQ.reach_m + .3)        # zero slack
        rig = start(make(), req)
        self.assertTrue(run_until(rig, ended))
        self.assertEqual(rig.assist.end_reason, 'turn_done_ready', rig.assist.events)


class BodyMask(unittest.TestCase):
    def test_far_mask_is_refused_when_the_config_loads(self):
        # merge with the day-1 hardening (S5, claude/field-weekend-20261022): a mask that reaches beyond the footprint
        # boundary + 0.05 m invalidates the whole config, so the turn's blind-beam rule is no longer the only guard
        lo, hi = sorted((beam(-115.), beam(-65.)))
        with self.assertRaises(ConfigError) as ctx:
            T.turn_config(body_mask=((lo, hi, 1.2),))
        self.assertTrue(any('body_mask[0]' in p for p in ctx.exception.problems), ctx.exception.problems)

    def test_mask_beyond_the_footprint_refuses_output(self):
        # inside the S5 band (<= exit range + 0.05 m: the row config loads) but outside the turn's stricter footprint
        # rectangle + 0.02 m: the pivot disc is partly blind, so turn output stays refused
        lo, hi = sorted((beam(-100.), beam(-80.)))
        cfg = T.turn_config(body_mask=((lo, hi, .39),))
        self.assertTrue(body_mask_blind_beams(cfg))
        feas = turn_feasibility(cfg)
        self.assertFalse(feas['output_allowed'])
        self.assertTrue(any('body_mask' in p for p in feas['problems']))
        rig = make(cfg)
        rig.run(1.)
        with self.assertRaises(TurnRequestError):
            rig.assist.start(rig.t, REQ, motor_output=True)
        rig.assist.start(rig.t, REQ, motor_output=False)          # dry / observe runs stay possible

    def test_mask_inside_the_footprint_is_allowed(self):
        lo, hi = sorted((beam(165.), beam(-165.)))                 # straight back, 0.5 m: the body
        if hi - lo > 1000:                                         # the +-180 deg seam: two segments
            segments = ((0, lo, .5), (hi, L.BEAMS - 1, .5))
        else:
            segments = ((lo, hi, .5),)
        cfg = T.turn_config(body_mask=segments)
        self.assertEqual(body_mask_blind_beams(cfg), [])
        self.assertTrue(turn_feasibility(cfg)['output_allowed'])

    def test_default_config_has_no_blind_beams(self):
        self.assertEqual(body_mask_blind_beams(T.turn_config()), [])


class OdometryHandoff(unittest.TestCase):
    def test_no_odometry_ready_after_a_lidar_gap(self):
        rig = start(make())
        op = T.Operator()
        state = dict(t=None)

        def hold(r):
            a = r.assist
            if a.phase == 'PIVOT_2':
                r.band_enabled = False             # the standing acquisition will not find the corridor
            if state['t'] is None and a.phase == 'TRAVERSE' and a.mode == ACTIVE and a.step['active_s'] > 1.:
                state['t'] = r.t
            r.scan_on = not (state['t'] is not None and state['t'] <= r.t < state['t'] + .8)
            return op(r)
        self.assertTrue(run_until(rig, ended, hold=hold))
        self.assertIn('odometry_gap_traverse', rig.assist.warnings)
        self.assertEqual(rig.assist.end_reason, 'turn_done_not_ready')
        self.assertFalse(rig.assist.handoff['ready'])
        self.assertTrue(rig.assist.handoff['odometry_lost'])

    def test_no_odometry_ready_after_invalid_scan_matching_while_driving(self):
        rig = make()
        start(rig)
        op = T.Operator()

        def hold(r):
            a = r.assist
            if a.phase == 'PIVOT_2':
                r.band_enabled = False
            return op(r)
        stamps = dict(t=None)

        def odom_valid(stamp):
            a = rig.assist
            if stamps['t'] is None and a.phase == 'CREEP_OUT' and a.mode == ACTIVE:
                stamps['t'] = stamp
            return not (stamps['t'] is not None and stamps['t'] + .5 <= stamp < stamps['t'] + .8)
        rig.odom_valid = odom_valid
        self.assertTrue(run_until(rig, ended, hold=hold))
        self.assertGreater(rig.assist.forward_odom_invalid_ticks, 0)
        self.assertEqual(rig.assist.end_reason, 'turn_done_not_ready')
        self.assertTrue(rig.assist.handoff['odometry_lost'])


class GuardBudgets(unittest.TestCase):
    def guard(self, **kw):
        args = dict(pivot_min_pct=60, pivot_cap_pct=80, drive_min_pct=20, drive_cap_pct=65, drive_diff_cap_pct=10,
                    pivot_total_s=5., stall_loaded_total_s=1.)
        args.update(kw)
        return TurnGuard(1, **args)

    def test_new_pivot_refused_after_the_time_budget(self):
        g = self.guard()
        t = 0.
        violation = None
        while violation is None and t < 10.:
            _, _, violation = g.apply(t, -60, 60, PIVOT, 1., 20.)
            t += .05
        self.assertEqual(violation, 'pivot_time_budget')
        g.apply(t, 0, 0, None)
        self.assertEqual(g.check(t + 1., -60, 60, PIVOT, 1., 20.), 'pivot_time_budget')

    def test_new_pivot_refused_after_the_stall_budget(self):
        g = self.guard()
        t = 0.
        violation = None
        while violation is None and t < 10.:
            _, _, violation = g.apply(t, -60, 60, PIVOT, 1., 0.)
            t += .05
        self.assertEqual(violation, 'stall_loaded_budget')
        self.assertEqual(g.check(t + 1., -60, 60, PIVOT, 1., 0.), 'stall_loaded_budget')
        g.reset_step()
        self.assertIsNone(g.check(t + 1., -60, 60, PIVOT, 1., 0.))


class TickTime(unittest.TestCase):
    def test_non_finite_tick_time_ends_with_zero(self):
        rig = start(make())
        d = rig.assist.tick(float('nan'), True)
        self.assertEqual((d.left, d.right), (0, 0))
        self.assertTrue(d.terminal)
        self.assertTrue(rig.assist.end_reason.startswith('internal_error'))


class OvershootWhileCommanding(unittest.TestCase):
    def test_lagging_control_heading_ends_the_pivot(self):
        rig = start(make())
        a = rig.assist
        op = T.Operator()
        self.assertTrue(run_until(rig, lambda r: r.assist.phase == 'PIVOT_1' and r.assist.mode == ACTIVE, hold=op))
        frozen = a._psi_ctrl()
        a._psi_ctrl = lambda: frozen                # the release never sees the rotation
        yaw0 = rig.plant.yaw
        self.assertTrue(run_until(rig, ended, hold=op))
        self.assertEqual(a.end_reason, 'pivot_overshoot')
        turned = math.degrees(T.wrap(rig.plant.yaw - yaw0))
        self.assertLess(turned, 90. + 20. + 15.)     # + one tick and the coast of the plant


if __name__ == '__main__':
    unittest.main()
