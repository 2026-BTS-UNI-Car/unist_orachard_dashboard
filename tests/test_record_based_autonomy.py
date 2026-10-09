"""Finite field-style native fixtures, not recorded physical calibration.

Rays are synthetic full arrays; original_ns/RAW and interval motion remain distinct.
No ROS, motor, process or actual archived data are imported or exercised.
"""
from copy import deepcopy
from dataclasses import replace
import math
import unittest
from unittest.mock import patch

from dashboard_server.record_based_autonomy import RecordBasedAutonomy, ObservationEstimates, SCHEMA


CONTEXT = dict(source_session='actual-original-test', generation=1,
               source_clock_domain='original-RCL-system', receipt_clock_domain='test-RAW')
SOURCE = 1_791_550_000_000_000_000
RAW = 55_000_000_000
PERIOD = 100_000_000


def scene(*, center=0., heading=0., left=True, right=True, wall=None,
          branch=False, outliers=False, reverse_angles=False):
    """Two native-angle walls + distant finite background; no implicit free NaNs."""
    count = 721
    start, inc = -math.pi, 2*math.pi/(count-1)
    slope = math.tan(heading)
    ranges = []
    for i in range(count):
        angle = start+i*inc
        c, s = math.cos(angle), math.sin(angle)
        candidates = [25.]
        denominator = s-slope*c
        for present, intercept in ((left, center+1.), (right, center-1.)):
            if present and abs(denominator) > 1e-9:
                r = intercept/denominator
                if r > .05:
                    candidates.append(r)
        if wall is not None and c > 1e-9:
            candidates.append(wall/c)
        r = min(candidates)
        if outliers and i % 11 == 0 and abs(angle) > .55:
            r = 25.  # finite background interruption outside front rectangle
        ranges.append(r)
    if branch:
        ranges[count//2] = .9
    if reverse_angles:
        ranges.reverse()
        start, inc = math.pi, -inc
    return dict(ranges=ranges, angle_min=start, angle_increment=inc,
                range_min=.05, range_max=40.)


def native_snapshot(i, *, raw_scene=None, dx=.01, dy=0., dyaw=0., imu_yaw=None):
    stamp = SOURCE+i*PERIOD
    receipt = RAW+i*PERIOD
    return dict(context=dict(CONTEXT), source_now_ns=stamp+10_000_000,
        receipt_now_ns=receipt+5_000_000,
        scan=dict(stamp_ns=stamp, receipt_ns=receipt, sequence=i, frame_id='original-laser',
                  original_native=True, **(raw_scene or scene())),
        motion=dict(previous_scan_stamp_ns=stamp-PERIOD, current_scan_stamp_ns=stamp,
            receipt_ns=receipt, dx_m=dx, dy_m=dy, dyaw_rad=dyaw,
            accepted=True, map_eligible=True, path_break=False, motion_prior=False),
        imu=dict(stamp_ns=stamp-10_000_000, receipt_ns=receipt-5_000_000,
            yaw_rate_rad_s=dyaw/(PERIOD/1e9) if imu_yaw is None else imu_yaw,
            original_native=True))


class RecordBasedAutonomyTests(unittest.TestCase):
    def setUp(self):
        self.controller = RecordBasedAutonomy()
        self.controller.reset(CONTEXT)

    def proposals(self, controller=None, **options):
        controller = controller or self.controller
        return [controller.plan(native_snapshot(i, **options)) for i in range(12)]

    def zero(self, result):
        self.assertEqual(result['proposed_left_percent'], 0.)
        self.assertEqual(result['proposed_right_percent'], 0.)
        self.assertEqual(result['applied_left_percent'], 0.)
        self.assertEqual(result['applied_right_percent'], 0.)

    def test_initial_zero_and_three_source_scans_persist_before_forward_proposal(self):
        results = self.proposals()
        self.assertEqual(results[0]['reason'], 'first_zero_required')
        self.zero(results[0]); self.zero(results[1])
        self.assertEqual(results[2]['decision'], 'PROPOSE_FORWARD')
        self.assertGreater(results[-1]['proposed_left_percent'], 0)
        self.assertAlmostEqual(results[-1]['proposed_left_percent'], results[-1]['proposed_right_percent'])
        self.assertLessEqual(len(results[-1]['trajectory']), 16)

    def test_all_results_remain_observe_only_and_unverified_even_valid_native_scene(self):
        result = self.proposals()[-1]
        self.assertEqual(result['schema'], SCHEMA)
        self.assertEqual(result['mode'], 'OBSERVE_ONLY')
        for key in ('runtime_authority', 'motor_command_sent', 'autonomy_output_enabled',
                    'physical_autonomy_qualified', 'execution_eligible', 'wheel_speed_observed', 'PWM_used_as_odometry'):
            self.assertIs(result[key], False)
        for key in ('calibrated', 'mount_verified', 'motor_response_verified', 'braking_verified',
                    'body_velocity_verified', 'timing_physically_validated'):
            self.assertIs(result['estimates'][key], False)
        self.assertFalse(result['laser_plane_motion']['body_validated'])
        self.assertEqual(result['applied_left_percent'], 0.)
        self.assertEqual(result['applied_right_percent'], 0.)

    def test_equal_forward_but_biased_sensor_yaw_is_damped_without_claiming_body_speed(self):
        results = self.proposals(dyaw=.006)
        result = results[-1]
        self.assertEqual(result['decision'], 'PROPOSE_FORWARD')
        self.assertGreater(result['proposed_left_percent'], result['proposed_right_percent'])
        self.assertAlmostEqual(result['laser_plane_motion']['yaw_rad_s'], .06)
        self.assertFalse(result['laser_plane_motion']['body_validated'])

    def test_weak_differential_response_keeps_bounded_feedback_error_correction(self):
        weak, responsive = RecordBasedAutonomy(), RecordBasedAutonomy()
        weak.reset(CONTEXT); responsive.reset(CONTEXT)
        angled = scene(center=.12, heading=.08)
        w = self.proposals(weak, raw_scene=angled, dyaw=0.)[-1]
        r = self.proposals(responsive, raw_scene=angled, dyaw=.008)[-1]
        self.assertGreater(w['proposed_right_percent']-w['proposed_left_percent'],
                           r['proposed_right_percent']-r['proposed_left_percent'])
        for result in (w, r):
            self.assertTrue(0 <= result['proposed_left_percent'] <= 30)
            self.assertTrue(0 <= result['proposed_right_percent'] <= 30)

    def test_sparse_wall_outliers_robustly_preserve_sides_but_front_branch_always_stops(self):
        result = self.proposals(raw_scene=scene(outliers=True))[-1]
        self.assertEqual(result['decision'], 'PROPOSE_FORWARD')
        blocked = self.controller.plan(native_snapshot(12, raw_scene=scene(branch=True)))
        self.assertEqual(blocked['reason'], 'front_return_inside_estimated_swept_space')
        self.assertEqual(blocked['decision'], 'STOP'); self.zero(blocked)
        self.zero(self.controller.plan(native_snapshot(13)))

    def test_front_wall_and_unknown_row_end_do_not_invent_turns_or_exit_space(self):
        wall = self.controller.plan(native_snapshot(0, raw_scene=scene(wall=1.2)))
        self.assertEqual(wall['decision'], 'STOP'); self.zero(wall)
        self.controller.reset(CONTEXT)
        for i in range(4):
            end = self.controller.plan(native_snapshot(i, raw_scene=scene(left=False, right=False)))
            self.assertEqual(end['reason'], 'row_end_or_two_sided_support_unknown')
            self.zero(end)
            self.assertEqual(end['trajectory'], [])

    def test_one_side_is_not_a_row_and_transient_dropout_requires_new_persistence(self):
        self.proposals()
        missing = self.controller.plan(native_snapshot(12, raw_scene=scene(left=False)))
        self.assertEqual(missing['decision'], 'HOLD'); self.zero(missing)
        recovery = [self.controller.plan(native_snapshot(i)) for i in range(13, 16)]
        self.zero(recovery[0]); self.zero(recovery[1])
        self.assertEqual(recovery[2]['decision'], 'PROPOSE_FORWARD')
        self.assertLessEqual(recovery[2]['proposed_left_percent'], 2.)

    def test_invalid_or_unknown_front_scan_never_reuses_previous_forward_output(self):
        for kind in ('nan_front', 'max_clamp_front', 'bool', 'oversize', 'zero_increment', 'huge_angle'):
            with self.subTest(kind=kind):
                self.setUp(); self.proposals()
                row = native_snapshot(12)
                if kind in ('nan_front', 'max_clamp_front'):
                    for i in range(300, 421):
                        row['scan']['ranges'][i] = float('nan') if kind == 'nan_front' else 40.
                elif kind == 'bool': row['scan']['ranges'][0] = True
                elif kind == 'oversize': row['scan']['ranges'] = [25.]*4097
                elif kind == 'zero_increment': row['scan']['angle_increment'] = 0.
                else: row['scan']['angle_min'] = 1e308
                result = self.controller.plan(row)
                self.assertEqual(result['decision'], 'HOLD'); self.zero(result)

    def test_geometry_jump_resets_persistence_instead_of_steering_to_new_branch(self):
        self.proposals()
        jump = self.controller.plan(native_snapshot(12, raw_scene=scene(center=.35)))
        self.assertEqual(jump['reason'], 'row_geometry_not_temporally_stable')
        self.zero(jump)

    def test_single_unknown_front_beam_or_missing_front_edge_is_not_free_space(self):
        for case in ('unknown_beam', 'missing_edge'):
            with self.subTest(case=case):
                self.setUp()
                row = native_snapshot(0)
                if case == 'unknown_beam':
                    row['scan']['ranges'][360] = float('inf')
                else:
                    # Retain both distant sides, but crop a narrow part of the
                    # frontal field: absence of rays is not cleared geometry.
                    cut = 329
                    row['scan']['ranges'] = row['scan']['ranges'][cut:]
                    row['scan']['angle_min'] += cut*row['scan']['angle_increment']
                result = self.controller.plan(row)
                self.assertEqual(result['reason'], 'front_space_unobserved')
                self.zero(result)

    def test_manual_takeover_during_geometric_fit_cannot_publish_or_resume_proposal(self):
        self.proposals()
        import dashboard_server.record_based_autonomy as module
        original = module._fit_side
        def takeover(points):
            result = original(points)
            self.controller.manual_takeover()
            return result
        with patch.object(module, '_fit_side', side_effect=takeover):
            stopped = self.controller.plan(native_snapshot(12))
        self.assertEqual(stopped['decision'], 'STOP')
        self.assertEqual(stopped['reason'], 'manual_takeover')
        self.zero(stopped)
        self.zero(self.controller.plan(native_snapshot(13)))

    def test_reversed_scan_array_and_angle_increment_keep_geometry_and_proposal_exact(self):
        reverse = RecordBasedAutonomy(); reverse.reset(CONTEXT)
        a = self.proposals()[-1]
        b = self.proposals(reverse, raw_scene=scene(reverse_angles=True))[-1]
        self.assertEqual(a['decision'], b['decision'])
        self.assertAlmostEqual(a['row_geometry']['center_m'], b['row_geometry']['center_m'], places=12)
        self.assertAlmostEqual(a['row_geometry']['heading_rad'], b['row_geometry']['heading_rad'], places=12)
        self.assertAlmostEqual(a['proposed_left_percent'], b['proposed_left_percent'], places=12)

    def test_reversal_pair_gap_duplicate_and_context_change_latch_until_explicit_reset(self):
        for case in ('reverse', 'pair_gap', 'duplicate', 'context', 'wrong_endpoint', 'frame'):
            with self.subTest(case=case):
                self.setUp(); self.proposals()
                row = native_snapshot(12)
                if case == 'reverse': row['motion']['dx_m'] = -.01
                elif case == 'pair_gap': row['motion']['previous_scan_stamp_ns'] -= PERIOD
                elif case == 'duplicate': row = native_snapshot(11)
                elif case == 'context': row['context']['generation'] = 2
                elif case == 'wrong_endpoint': row['motion']['current_scan_stamp_ns'] += 1
                else: row['scan']['frame_id'] = 'another-laser'
                result = self.controller.plan(row)
                self.assertEqual(result['decision'], 'STOP'); self.zero(result)
                self.zero(self.controller.plan(native_snapshot(13)))
                self.controller.reset(CONTEXT)
                self.assertEqual(self.controller.plan(native_snapshot(14))['reason'], 'first_zero_required')

    def test_original_source_receipt_and_causal_IMU_timing_are_not_repaired(self):
        for case in ('stale_scan', 'stale_receipt', 'future_scan', 'future_IMU', 'stale_IMU', 'bool_stamp'):
            with self.subTest(case=case):
                self.setUp()
                row = native_snapshot(0)
                before = deepcopy(row)
                if case == 'stale_scan': row['source_now_ns'] += 300_000_000
                elif case == 'stale_receipt': row['receipt_now_ns'] += 300_000_000
                elif case == 'future_scan': row['source_now_ns'] = row['scan']['stamp_ns']-1
                elif case == 'future_IMU': row['imu']['stamp_ns'] = row['scan']['stamp_ns']+1
                elif case == 'stale_IMU': row['imu']['stamp_ns'] -= 200_000_000
                else: row['scan']['stamp_ns'] = True
                frozen = deepcopy(row)
                result = self.controller.plan(row)
                self.assertEqual(result['decision'], 'STOP'); self.zero(result)
                self.assertEqual(row, frozen)
                self.assertNotEqual(row, before)

    def test_same_domain_clock_jump_is_rejected_even_when_individual_ages_are_fresh(self):
        self.controller.plan(native_snapshot(0))
        row = native_snapshot(1)
        row['source_now_ns'] += 70_000_000
        result = self.controller.plan(row)
        self.assertEqual(result['reason'], 'source_receipt_clock_reversal_or_jump')
        self.zero(result)

    def test_motion_prior_or_invalid_fit_cannot_supply_velocity_or_row_progress(self):
        for change in ({'motion_prior': True}, {'accepted': False}, {'map_eligible': False}, {'path_break': True}):
            with self.subTest(change=change):
                self.setUp()
                row = native_snapshot(0); row['motion'].update(change)
                result = self.controller.plan(row)
                self.assertEqual(result['reason'], 'accepted_geometric_native_pair_required')
                self.zero(result)
                self.assertIsNone(result['laser_plane_motion'])

    def test_unverified_WS_native_flag_remains_hold_not_physical_feedback(self):
        row = native_snapshot(0)
        row['scan']['original_native'] = False
        result = self.controller.plan(row)
        self.assertEqual(result['reason'], 'original_native_provenance_unverified')
        self.zero(result)

    def test_explicit_record_replay_context_allows_estimates_without_claiming_native(self):
        context = dict(CONTEXT, source_clock_domain='archive_ros_header_unverified',
                       receipt_clock_domain='synthetic_offline_replay')
        replay = RecordBasedAutonomy(allow_record_replay_estimates=True)
        replay.reset(context)
        for i in range(12):
            row = native_snapshot(i)
            row['context'] = dict(context)
            row['replay_estimate'] = True
            row['scan']['original_native'] = row['imu']['original_native'] = False
            row['scan']['ranges'][0] = None  # preserved archived unknown, outside front
            result = replay.plan(row)
            self.assertIsNone(row['scan']['ranges'][0])
        self.assertEqual(result['decision'], 'PROPOSE_FORWARD')
        self.assertTrue(result['record_replay_estimate'])
        self.assertTrue(result['source_provenance_unverified'])
        self.assertGreater(result['proposed_left_percent'], 0.)
        self.assertEqual(result['applied_left_percent'], 0.)
        for flag in ('runtime_authority', 'motor_command_sent', 'autonomy_output_enabled',
                     'execution_eligible', 'physical_autonomy_qualified'):
            self.assertIs(result[flag], False)

    def test_native_null_marker_preserves_rear_unknown_but_never_clears_front(self):
        baseline = self.proposals()[-1]
        for location, marker, native in (('rear', True, True), ('front', True, True),
                                          ('rear', None, True), ('rear', 1, True),
                                          ('rear', True, False)):
            with self.subTest(location=location, marker=marker, native=native):
                controller = RecordBasedAutonomy()
                controller.reset(CONTEXT)
                for i in range(12):
                    row = native_snapshot(i)
                    index = 0 if location == 'rear' else len(row['scan']['ranges'])//2
                    row['scan']['ranges'][index] = None
                    row['scan']['original_native'] = native
                    if marker is not None:
                        row['scan']['nonfinite_ranges_as_unknown'] = marker
                    result = controller.plan(row)
                    self.assertIsNone(row['scan']['ranges'][index])
                if location == 'rear' and marker is True and native:
                    self.assertEqual(result['decision'], 'PROPOSE_FORWARD')
                    self.assertEqual(result['proposed_left_percent'], baseline['proposed_left_percent'])
                    self.assertEqual(result['proposed_right_percent'], baseline['proposed_right_percent'])
                    self.assertEqual(result['row_geometry'], baseline['row_geometry'])
                else:
                    reason = ('original_native_provenance_unverified' if not native else
                              'front_space_unobserved' if location == 'front' else 'invalid_full_raw_scan')
                    self.assertEqual(result['decision'], 'HOLD')
                    self.assertEqual(result['reason'], reason)
                    self.zero(result)
                self.assertEqual(result['applied_left_percent'], 0.)
                self.assertEqual(result['applied_right_percent'], 0.)
                self.assertIs(result['runtime_authority'], False)

    def test_replay_optin_needs_exact_marker_and_both_explicit_clock_domains(self):
        for case in ('default_disabled', 'missing_marker', 'numeric_marker', 'source_clock', 'receipt_clock'):
            with self.subTest(case=case):
                context = dict(CONTEXT, source_clock_domain='archive_ros_header_unverified',
                               receipt_clock_domain='synthetic_offline_replay')
                if case == 'source_clock': context['source_clock_domain'] = 'WEB_unverified'
                if case == 'receipt_clock': context['receipt_clock_domain'] = 'original_RAW_claim'
                replay = RecordBasedAutonomy(allow_record_replay_estimates=case != 'default_disabled')
                replay.reset(context)
                row = native_snapshot(0)
                row['context'] = context
                row['replay_estimate'] = 1 if case == 'numeric_marker' else case != 'missing_marker'
                row['scan']['original_native'] = row['imu']['original_native'] = False
                result = replay.plan(row)
                self.assertEqual(result['reason'], 'original_native_provenance_unverified')
                self.assertFalse(result['record_replay_estimate'])
                self.assertTrue(result['source_provenance_unverified'])
                self.zero(result)
        with self.assertRaises(ValueError): RecordBasedAutonomy(allow_record_replay_estimates=1)

    def test_expiry_reanchor_accepts_fresh_next_pair_only_after_first_zero_and_persistence(self):
        self.assertEqual(self.proposals()[-1]['decision'], 'PROPOSE_FORWARD')
        self.assertFalse(self.controller.reanchor_after_expiry(dict(CONTEXT, generation=2)))
        continuous = self.controller.plan(native_snapshot(12))
        self.assertEqual(continuous['decision'], 'PROPOSE_FORWARD')
        # Rows 13..20 were discarded by the caller's positive-expiry gate, not
        # restamped or fed to this controller. The fresh pair is original 20->21.
        self.assertTrue(self.controller.reanchor_after_expiry(CONTEXT))
        recovered = [self.controller.plan(native_snapshot(i)) for i in range(21, 24)]
        self.assertEqual(recovered[0]['reason'], 'first_zero_required')
        self.assertEqual(recovered[1]['reason'], 'two_sided_support_persistence_pending')
        self.zero(recovered[0]); self.zero(recovered[1])
        self.assertEqual(recovered[2]['decision'], 'PROPOSE_FORWARD')
        self.assertGreater(recovered[2]['proposed_left_percent'], 0.)
        self.assertLessEqual(recovered[2]['proposed_left_percent'], 2.)
        for result in recovered:
            self.assertEqual(result['applied_left_percent'], 0.)
            self.assertEqual(result['applied_right_percent'], 0.)
            self.assertIs(result['runtime_authority'], False)
        strict = RecordBasedAutonomy()
        strict.reset(CONTEXT)
        self.proposals(strict)
        self.assertEqual(strict.plan(native_snapshot(21))['reason'],
                         'source_order_or_native_pair_discontinuity')

    def test_expiry_reanchor_never_clears_operator_geometric_or_timing_stop(self):
        self.assertFalse(RecordBasedAutonomy().reanchor_after_expiry(CONTEXT))
        for stop in ('operator', 'manual', 'geometric', 'timing'):
            with self.subTest(stop=stop):
                controller = RecordBasedAutonomy()
                controller.reset(CONTEXT)
                if stop == 'operator': result = controller.stop()
                elif stop == 'manual': result = controller.manual_takeover()
                elif stop == 'geometric': result = controller.plan(native_snapshot(0, raw_scene=scene(branch=True)))
                else:
                    row = native_snapshot(0)
                    row['source_now_ns'] += controller.estimates.source_age_ns+1
                    result = controller.plan(row)
                self.assertEqual(result['decision'], 'STOP')
                reason = result['reason']
                self.assertFalse(controller.reanchor_after_expiry(CONTEXT))
                self.assertTrue(controller._stopping.is_set())
                held = controller.plan(native_snapshot(21))
                self.assertEqual(held['decision'], 'STOP')
                self.assertEqual(held['reason'], reason)
                self.zero(held)

    def test_preference_adjustment_slew_and_zero_are_bounded_without_motor_permission(self):
        results = []
        for i in range(16):
            results.append(self.controller.plan(native_snapshot(i), cruise_percent=30.))
        for a, b in zip(results[2:], results[3:]):
            self.assertLessEqual(abs(b['proposed_left_percent']-a['proposed_left_percent']), 2.+1e-12)
        zero = self.controller.plan(native_snapshot(16), cruise_percent=0.)
        self.zero(zero)
        invalid = self.controller.plan(native_snapshot(17), cruise_percent=100.)
        self.assertEqual(invalid['decision'], 'STOP'); self.zero(invalid)

    def test_callable_preference_is_an_estimate_and_matches_direct_percent_interface(self):
        peer = RecordBasedAutonomy(); peer.reset(CONTEXT)
        for i in range(6):
            a = self.controller(native_snapshot(i), .15)
            b = peer.plan(native_snapshot(i), cruise_percent=15.)
            self.assertEqual(a, b)

    def test_operator_stop_manual_takeover_and_returned_mutation_cannot_resume(self):
        result = self.proposals()[-1]
        result['row_geometry']['center_m'] = 999.
        result['trajectory'].clear()
        next_result = self.controller.plan(native_snapshot(12))
        self.assertAlmostEqual(next_result['row_geometry']['center_m'], 0.)
        self.assertEqual(len(next_result['trajectory']), 16)
        for stop in (self.controller.stop, self.controller.manual_takeover):
            stopped = stop(); self.zero(stopped)
            self.assertEqual(stopped['decision'], 'STOP')
            self.zero(self.controller.plan(native_snapshot(13)))

    def test_no_usable_estimate_can_claim_calibration_or_overflow_numeric_bounds(self):
        for settings in (replace(ObservationEstimates(), max_percent=100),
                         replace(ObservationEstimates(), slew_percent_per_s=float('nan')),
                         replace(ObservationEstimates(), source_age_ns=True)):
            with self.assertRaises(ValueError): RecordBasedAutonomy(settings)
        result = self.controller(native_snapshot(0), 10**10000)
        self.assertEqual(result['decision'], 'STOP'); self.zero(result)


if __name__ == '__main__':
    unittest.main()
