"""Independent synthetic SE(2) oracle; no real mounting/accuracy certification."""
from dataclasses import replace
import json
import math
import unittest

from dashboard_server.body_feedback import (BodyFeedbackObserver, FeedbackContext,
    FeedbackTiming, LaserPosePair, MeasuredLaserMount, NativeImuYaw)


def matmul(a, b):
    return [[sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def transform(x, y, yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    return [[c, -s, x], [s, c, y], [0., 0., 1.]]


def twist_exp(vx, vy, yaw_rate, dt):
    # Matrix exponential power series independent of the production SE(2) log.
    g = [[0., -yaw_rate*dt, vx*dt], [yaw_rate*dt, 0., vy*dt], [0., 0., 0.]]
    result = term = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]
    for n in range(1, 30):
        term = [[v/n for v in row] for row in matmul(term, g)]
        result = [[result[i][j]+term[i][j] for j in range(3)] for i in range(3)]
    return result


def pose(matrix):
    return matrix[0][2], matrix[1][2], math.atan2(matrix[1][0], matrix[0][0])


class BodyFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.context = FeedbackContext("synthetic-session", "synthetic-map", 1,
            "synthetic-sensor-clock", "synthetic-monotonic", "synthetic-world", "laser")
        self.mount = MeasuredLaserMount(.18, .04, .2, "synthetic-calibration", True)
        self.timing = FeedbackTiming(250_000_000, 100_000_000, 200_000_000, 50_000_000)
        self.observer = BodyFeedbackObserver(self.mount, self.timing)

    def sample(self, index=0, velocity=(.1, -.02), rate=.2, mount=None):
        mount = self.mount if mount is None else mount
        static = transform(mount.x_m, mount.y_m, mount.yaw_rad)
        previous = transform(1.2, -.4, .7)
        increment = twist_exp(*velocity, rate, .1)
        for _ in range(index):
            previous = matmul(previous, increment)
        current = matmul(previous, increment)
        start = 1_000_000_000 + index*100_000_000
        received = 10_000_000_000 + index*100_000_000
        pair = LaserPosePair(self.context, index, index+1, start, start+100_000_000,
            received, received+100_000_000, pose(matmul(previous, static)),
            pose(matmul(current, static)), accepted=True, map_eligible=True,
            independent_localization_validated=True, planar_supported=True,
            source_clock_verified=True)
        imu = NativeImuYaw(self.context, pair.source_stamp_ns-10_000_000,
            pair.receipt_ns+2_000_000, rate, native=True, gravity_projected=True,
            mount_verified=True, imu_frame="synthetic-imu")
        clocks = dict(source_now_ns=pair.source_stamp_ns+10_000_000,
            receipt_now_ns=pair.receipt_ns+10_000_000)
        return pair, imu, clocks

    def observe(self, index=0, **kwargs):
        pair, imu, clocks = self.sample(index, **kwargs)
        return self.observer.observe(pair, imu, **clocks)

    def assertHold(self, result, reason=None):
        self.assertFalse(result["valid"])
        self.assertEqual(result["state"], "HOLD")
        self.assertIsNone(result["body"])
        self.assertFalse(result["runtime_authority"])
        self.assertFalse(result["motor_command_sent"])
        self.assertFalse(result["calibration_certified"])
        json.dumps(result, allow_nan=False)
        if reason:
            self.assertEqual(result["reason"], reason)

    def test_unmeasured_or_missing_mount_retains_laser_only(self):
        pair, imu, clocks = self.sample()
        for mount in (None, replace(self.mount, measured=False),
                      replace(self.mount, x_m=None), replace(self.mount, yaw_rad=float("nan"))):
            result = BodyFeedbackObserver(mount, self.timing).observe(pair, imu, **clocks)
            self.assertHold(result, "measured_mount_required")
            self.assertTrue(result["laser"]["diagnostic_only"])
            self.assertEqual(result["laser"]["interval_ns"], 100_000_000)

    def test_priming_and_independent_matrix_exponential_body_oracle(self):
        for velocity, rate in (((.1, 0.), 0.), ((.1, -.02), .2),
            ((0., .04), -.2), ((-.1, .01), .3), ((.1, .02), 1e-8)):
            with self.subTest(velocity=velocity, rate=rate):
                self.observer = BodyFeedbackObserver(self.mount, self.timing)
                self.assertHold(self.observe(0, velocity=velocity, rate=rate), "first_pair_priming")
                result = self.observe(1, velocity=velocity, rate=rate)
                self.assertTrue(result["valid"], result)
                self.assertAlmostEqual(result["body"]["forward_m_s"], velocity[0], places=12)
                self.assertAlmostEqual(result["body"]["lateral_m_s"], velocity[1], places=12)
                self.assertEqual(result["body"]["yaw_rad_s"], rate)
                self.assertFalse(result["body"]["instantaneous_speed"])
                self.assertFalse(result["runtime_authority"])
                self.assertTrue(result["freshness_recheck_required"])

    def test_pivot_lever_arm_is_not_body_translation(self):
        self.observe(0, velocity=(0., 0.), rate=.5)
        result = self.observe(1, velocity=(0., 0.), rate=.5)
        self.assertGreater(math.hypot(*result["laser"]["delta_xy_m"]), .005)
        self.assertAlmostEqual(result["body"]["forward_m_s"], 0., places=12)
        self.assertAlmostEqual(result["body"]["lateral_m_s"], 0., places=12)

    def test_noncoincident_rotated_mount(self):
        self.mount = MeasuredLaserMount(-.3, .2, -1.2, "other-synthetic", True)
        self.observer = BodyFeedbackObserver(self.mount, self.timing)
        self.observe(0, velocity=(.12, .01), rate=-.4)
        result = self.observe(1, velocity=(.12, .01), rate=-.4)
        self.assertAlmostEqual(result["body"]["forward_m_s"], .12, places=12)
        self.assertAlmostEqual(result["body"]["lateral_m_s"], .01, places=12)

    def test_yaw_is_native_imu_rate_not_registration_difference(self):
        self.observe()
        pair, imu, clocks = self.sample(1)
        result = self.observer.observe(pair, replace(imu, yaw_rad_s=-.15), **clocks)
        self.assertAlmostEqual(result["laser"]["registration_yaw_rad_s"], .2)
        self.assertEqual(result["body"]["yaw_rad_s"], -.15)

    def test_all_qualification_declarations_fail_closed(self):
        pair, imu, clocks = self.sample()
        for field, value, reason in (
            ("accepted", False, "rejected_or_ineligible_registration"),
            ("map_eligible", False, "rejected_or_ineligible_registration"),
            ("source_clock_verified", False, "source_clock_unverified"),
            ("independent_localization_validated", False, "localization_unvalidated"),
            ("planar_supported", False, "planar_envelope_unvalidated"),
            ("motion_prior", True, "path_break_or_motion_prior"),
            ("path_break", True, "path_break_or_motion_prior")):
            self.observer.reset()
            self.assertHold(self.observer.observe(replace(pair, **{field:value}), imu,
                **clocks), reason)
        self.assertHold(BodyFeedbackObserver(self.mount).observe(pair, imu,
            **clocks), "timing_envelope_required")

    def test_rejected_or_dropped_pairs_need_a_new_qualified_baseline(self):
        self.observe()
        pair, imu, clocks = self.sample(1)
        self.assertHold(self.observer.observe(replace(pair, accepted=False), imu,
            **clocks), "rejected_or_ineligible_registration")
        self.assertHold(self.observe(2), "first_pair_priming")
        self.assertTrue(self.observe(3)["valid"])
        pair, imu, clocks = self.sample(5)
        self.assertHold(self.observer.observe(pair, imu, **clocks), "discontinuous_original_pair")
        self.assertHold(self.observe(6), "first_pair_priming")

    def test_previous_original_pose_stamp_receipt_and_sequence_must_match(self):
        for field, value in (("previous_pose", (9., 8., .1)),
                ("previous_source_stamp_ns", 1_100_000_001),
                ("previous_receipt_ns", 10_100_000_001)):
            self.observer.reset()
            self.observe()
            pair, imu, clocks = self.sample(1)
            self.assertHold(self.observer.observe(replace(pair, **{field:value}), imu,
                **clocks), "discontinuous_original_pair")
        pair, imu, clocks = self.sample()
        self.assertHold(self.observer.observe(replace(pair, sequence=3), imu,
            **clocks), "nonconsecutive_pair")

    def test_context_changes_do_not_chain_across_sessions_or_frames(self):
        for field, value in (("source_session", "other"), ("epoch", "other"),
            ("generation", 2), ("source_clock", "other"), ("receipt_clock", "other"),
            ("pose_frame", "other"), ("laser_frame", "other")):
            self.observer.reset()
            self.observe()
            pair, imu, clocks = self.sample(1)
            context = replace(self.context, **{field:value})
            self.assertHold(self.observer.observe(replace(pair, context=context),
                replace(imu, context=context), **clocks), "context_changed")

    def test_native_imu_causality_projection_mount_and_frame_are_required(self):
        pair, imu, clocks = self.sample()
        for changes, reason in ((dict(source_stamp_ns=pair.source_stamp_ns+1), "imu_not_causal_in_scan_interval"),
            (dict(source_stamp_ns=pair.previous_source_stamp_ns), "imu_not_causal_in_scan_interval"),
            (dict(source_stamp_ns=pair.source_stamp_ns-60_000_000), "imu_pair_too_old"),
            (dict(native=False), "native_gravity_yaw_unverified"),
            (dict(gravity_projected=False), "native_gravity_yaw_unverified"),
            (dict(mount_verified=False), "native_gravity_yaw_unverified"),
            (dict(imu_frame=""), "native_gravity_yaw_unverified"),
            (dict(yaw_rad_s=float("nan")), "native_gravity_yaw_unverified")):
            self.observer.reset()
            self.assertHold(self.observer.observe(pair, replace(imu, **changes),
                **clocks), reason)
        self.assertHold(self.observer.observe(pair, None, **clocks), "native_imu_context_missing_or_changed")

    def test_original_clocks_separate_and_age_does_not_renew_on_snapshot(self):
        self.observe()
        pair, imu, clocks = self.sample(1)
        result = self.observer.observe(pair, imu, **clocks)
        self.assertTrue(result["valid"])
        original_stamp = result["source_stamp_ns"]
        current = self.observer.snapshot(**clocks)
        self.assertFalse(current["freshness_recheck_required"])
        old = self.observer.snapshot(source_now_ns=pair.source_stamp_ns+101_000_000,
            receipt_now_ns=clocks["receipt_now_ns"])
        self.assertHold(old, "stale_or_future_scan")
        self.assertEqual(old["source_stamp_ns"], original_stamp)
        self.assertTrue(old["body_eligible_at_observation"])
        self.assertHold(self.observer.snapshot(**clocks), "stale_or_future_scan")
        self.observe(2)
        pair, imu, clocks = self.sample(3)
        self.observer.observe(pair, imu, **clocks)
        future = self.observer.snapshot(source_now_ns=pair.source_stamp_ns-1,
            receipt_now_ns=clocks["receipt_now_ns"])
        self.assertHold(future, "stale_or_future_scan")

    def test_yaw_wrap_or_multiple_turn_alias_is_not_guessed(self):
        pair, imu, clocks = self.sample()
        for previous_yaw, yaw in ((0., math.pi),
            (pair.previous_pose[2], pair.previous_pose[2]+2*math.pi),
            (pair.previous_pose[2], pair.previous_pose[2]-2*math.pi+.02)):
            self.observer.reset()
            changed = replace(pair, previous_pose=(pair.previous_pose[0], pair.previous_pose[1], previous_yaw),
                pose=(pair.pose[0], pair.pose[1], yaw))
            self.assertHold(self.observer.observe(changed, imu,
                **clocks), "ambiguous_or_wrapped_yaw_increment")

    def test_live_read_rejects_clock_regression_and_changed_mount(self):
        self.observe()
        pair, imu, clocks = self.sample(1)
        self.observer.observe(pair, imu, **clocks)
        changed = dict(clocks, source_now_ns=pair.source_stamp_ns+5_000_000)
        self.assertHold(self.observer.snapshot(**changed), "clock_regression")
        self.assertHold(self.observer.snapshot(**clocks), "clock_regression")
        self.observe(2)
        pair, imu, clocks = self.sample(3)
        self.observer.observe(pair, imu, **clocks)
        self.observer.mount = replace(self.mount, yaw_rad=.21)
        self.assertHold(self.observer.snapshot(**clocks), "mount_or_timing_changed")

    def test_stop_and_snapshot_copy_cannot_retain_body_eligibility(self):
        self.observe()
        result = self.observe(1)
        result["body"]["forward_m_s"] = 123.
        result["laser"]["delta_xy_m"][0] = 456.
        self.assertNotEqual(self.observer.snapshot()["body"]["forward_m_s"], 123.)
        prior_laser = self.observer.snapshot()["laser"]
        self.assertHold(self.observer.invalidate("manual_stop"), "manual_stop")
        self.assertEqual(self.observer.snapshot()["laser"], prior_laser)
        self.assertHold(self.observe(2), "first_pair_priming")

    def test_hostile_types_integer_clocks_and_nonfinite_pose_are_json_safe(self):
        pair, imu, clocks = self.sample()
        for changes in (dict(source_stamp_ns=True), dict(receipt_ns=2**63),
            dict(sequence=1.), dict(pose=(float("nan"), 0., 0.)),
            dict(pose=(1., True, 0.)), dict(pose=(10**400, 0., 0.))):
            self.observer.reset()
            self.assertHold(self.observer.observe(replace(pair, **changes), imu, **clocks))
        self.observer.reset()
        self.assertHold(self.observer.observe(pair, imu,
            **dict(clocks, receipt_now_ns=10**400)), "invalid_clock")
        self.assertHold(self.observer.observe(replace(pair,
            context=replace(self.context, generation=True)), imu, **clocks), "invalid_context")
        self.observer.reset()
        self.assertHold(self.observer.observe(replace(pair, previous_pose=(-1.7e308, 0., 0.),
            pose=(1.7e308, 0., 0.)), imu, **clocks), "nonfinite_pose_delta")


if __name__ == "__main__":
    unittest.main()
