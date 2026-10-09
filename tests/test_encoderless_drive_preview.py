"""Synthetic measured declarations only; these tests prove no rover calibration."""
from dataclasses import replace
import unittest

from dashboard_server.encoderless_drive_preview import (
    EncoderlessDrivePreview, MeasuredProfile, NativeContext, NativeFeedback,
    SweptSpace, TimingLimits,
)


class EncoderlessDrivePreviewTests(unittest.TestCase):
    def setUp(self):
        # Entirely fake fixture: never a field speed/PWM recommendation.
        self.context = NativeContext("synthetic-session", "synthetic-epoch", 1,
            "synthetic-sensor-clock", "synthetic-monotonic", "synthetic-calibration")
        self.profile = MeasuredProfile(calibration_id=self.context.calibration_id,
            measured=True, braking_measured=True, linear_ff=80., yaw_ff=2.,
            linear_kp=40., yaw_kp=20., max_forward_m_s=.2,
            max_feedback_m_s=.3, max_yaw_rad_s=.2, max_lateral_m_s=.05,
            max_percent=20., slew_percent_per_s=100.,
            braking_test_speed_m_s=.3, stop_distance_m=.4, stop_time_s=1.)
        self.timing = TimingLimits(200_000_000, 200_000_000, 250_000_000)
        self.controller = EncoderlessDrivePreview(self.profile, self.timing)
        self.controller.reset(self.context)

    def sample(self, index=0, **feedback_changes):
        source = 1_000_000_000 + index * 100_000_000
        receipt = 10_000_000_000 + index * 100_000_000
        feedback = NativeFeedback(self.context, source, receipt, .1, 0., 0., True)
        feedback = replace(feedback, **feedback_changes)
        swept = SweptSpace(self.context, feedback.source_stamp_ns,
            feedback.receipt_ns, receipt + 5_000_000, "CLEAR", True, .1, 0., 1.)
        args = dict(desired_forward_m_s=.1, desired_yaw_rad_s=0.,
            source_now_ns=source + 10_000_000, receipt_now_ns=receipt + 10_000_000)
        return feedback, swept, args

    def step(self, index=0, **feedback_changes):
        f, s, args = self.sample(index, **feedback_changes)
        return self.controller.step(f, s, **args)

    def assertZero(self, result, reason=None):
        self.assertEqual((result.left_percent, result.right_percent), (0., 0.))
        self.assertTrue(result.latched)
        self.assertFalse(result.motor_command_sent)
        self.assertFalse(result.runtime_authority)
        if reason is not None:
            self.assertEqual(result.reason, reason)

    def test_no_default_or_unmeasured_profile_can_propose_motion(self):
        for profile in (None, MeasuredProfile(), replace(self.profile, measured=False),
                        replace(self.profile, braking_measured=False)):
            c = EncoderlessDrivePreview(profile, self.timing)
            self.assertZero(c.reset(self.context), "measured_profile_required")
            f, s, args = self.sample()
            self.assertZero(c.step(f, s, **args))

    def test_reset_and_first_sample_zero_never_grant_authority(self):
        c = EncoderlessDrivePreview(self.profile, self.timing)
        f, s, args = self.sample()
        self.assertZero(c.step(f, s, **args), "reset_required")
        self.assertEqual(c.reset(self.context).reason, "reset_zero")
        r = c.step(f, s, **args)
        self.assertEqual((r.left_percent, r.right_percent), (0., 0.))
        self.assertEqual(r.reason, "initial_sample_zero")
        self.assertFalse(r.latched)
        self.assertFalse(r.motor_command_sent)
        self.assertFalse(r.runtime_authority)
        r = self.step(0)
        r = self.step(1)
        self.assertEqual((r.left_percent, r.right_percent), (8., 8.))
        self.assertFalse(r.motor_command_sent)
        self.assertFalse(r.runtime_authority)

    def test_velocity_and_yaw_correction_use_body_feedback(self):
        self.step(0)
        slower = self.step(1, forward_m_s=.05)
        self.assertEqual((slower.left_percent, slower.right_percent), (10., 10.))
        yaw = self.step(2, forward_m_s=.1, yaw_rad_s=.1)
        self.assertGreater(yaw.left_percent, yaw.right_percent)
        self.assertEqual((yaw.left_percent, yaw.right_percent), (10., 6.))
        faster = self.step(3, forward_m_s=.15)
        self.assertEqual((faster.left_percent, faster.right_percent), (6., 6.))

    def test_cap_and_slew_apply_after_correction(self):
        self.controller = EncoderlessDrivePreview(replace(self.profile,
            linear_ff=400., slew_percent_per_s=10.), self.timing)
        self.controller.reset(self.context)
        self.step(0)
        r = self.step(1)
        self.assertEqual((r.left_percent, r.right_percent), (1., 1.))
        self.assertTrue(r.capped)
        self.assertTrue(r.slew_limited)
        for index in range(2, 25):
            r = self.step(index)
        self.assertEqual((r.left_percent, r.right_percent), (20., 20.))

    def test_stop_immediately_bypasses_slew_and_never_auto_resumes(self):
        self.step(0)
        self.assertGreater(self.step(1).left_percent, 0)
        self.assertZero(self.controller.stop(), "explicit_stop")
        self.assertZero(self.step(2), "explicit_stop")
        self.controller.reset(self.context)
        self.assertEqual(self.step(3).reason, "initial_sample_zero")

    def test_hold_manual_zero_and_unsupported_modes_latch(self):
        variants = ((dict(hold=True), "hold"),
            (dict(manual_takeover=True), "manual_takeover"),
            (dict(manual_takeover=None), "manual_takeover"),
            (dict(desired_forward_m_s=0.), "zero_speed"),
            (dict(desired_forward_m_s=-.1), "target_outside_forward_envelope"),
            (dict(mode="pivot"), "unsupported_motion_mode"),
            (dict(mode="headland"), "unsupported_motion_mode"))
        for changes, reason in variants:
            with self.subTest(changes=changes):
                self.controller.reset(self.context)
                f, s, args = self.sample()
                args.update(changes)
                self.assertZero(self.controller.step(f, s, **args), reason)
                self.assertZero(self.step(1), reason)

    def test_all_context_parts_are_bound(self):
        for field, value in (("source_session", "other-session"), ("epoch", "other-epoch"),
                            ("generation", 2), ("source_clock", "other-clock"),
                            ("receipt_clock", "other-receipt"), ("calibration_id", "other-body")):
            with self.subTest(field=field):
                self.controller.reset(self.context)
                f, s, args = self.sample()
                self.assertZero(self.controller.step(replace(f,
                    context=replace(self.context, **{field: value})), s, **args), "context_changed")
        self.controller.reset(self.context)
        f, s, args = self.sample()
        # bool compares equal to generation=1 but is not an integer generation.
        bad = replace(self.context, generation=True)
        self.assertZero(self.controller.step(replace(f, context=bad), s,
            **args), "invalid_context")

    def test_missing_unknown_and_nonfinite_feedback(self):
        f, s, args = self.sample()
        self.assertZero(self.controller.step(None, s, **args), "native_feedback_or_space_missing")
        for field in ("forward_m_s", "lateral_m_s", "yaw_rad_s"):
            for value in (None, float("nan"), float("inf"), True, 10 ** 400):
                self.controller.reset(self.context)
                self.assertZero(self.controller.step(replace(f, **{field: value}), s,
                    **args), "nonfinite_feedback_or_space")
        self.controller.reset(self.context)
        self.assertZero(self.step(independently_valid=False), "native_feedback_unconfirmed")

    def test_clock_domains_are_separate_and_stale_or_future_latch(self):
        f, s, args = self.sample()
        # Source now is ~1s; receipt now is ~10s. No cross-clock subtraction.
        self.assertEqual(self.controller.step(f, s, **args).reason, "initial_sample_zero")
        for field, value in (("source_now_ns", f.source_stamp_ns-1),
            ("source_now_ns", f.source_stamp_ns+200_000_001),
            ("receipt_now_ns", f.receipt_ns+200_000_001)):
            self.controller.reset(self.context)
            changed = dict(args, **{field: value})
            self.assertZero(self.controller.step(f, s, **changed), "feedback_stale_or_future")
        self.controller.reset(self.context)
        self.assertZero(self.controller.step(f, s, **dict(args, source_now_ns=True)), "invalid_clock_metadata")
        self.controller.reset(self.context)
        self.assertZero(self.controller.step(f, s, **dict(args, receipt_now_ns=10**400)), "invalid_clock_metadata")

    def test_duplicate_rewind_and_control_gap_latch(self):
        self.step(0)
        self.assertZero(self.step(0), "out_of_order")
        self.controller.reset(self.context)
        self.step(1)
        self.assertZero(self.step(0), "out_of_order")
        self.controller.reset(self.context)
        self.step(0)
        self.assertZero(self.step(3), "control_or_source_gap")

    def test_fresh_decision_cannot_refresh_an_old_scan_receipt(self):
        self.controller = EncoderlessDrivePreview(self.profile,
            TimingLimits(1_000_000_000, 100_000_000, 250_000_000))
        self.controller.reset(self.context)
        f, s, args = self.sample()
        s = replace(s, decision_receipt_ns=f.receipt_ns + 490_000_000)
        args.update(source_now_ns=f.source_stamp_ns + 50_000_000,
            receipt_now_ns=f.receipt_ns + 500_000_000)
        self.assertZero(self.controller.step(f, s, **args), "swept_space_stale_or_future")

    def test_swept_endpoint_command_and_braking_checks(self):
        f, s, args = self.sample()
        variants = ((dict(source_stamp_ns=s.source_stamp_ns-1), "swept_feedback_endpoint_mismatch"),
            (dict(scan_receipt_ns=s.scan_receipt_ns-1), "swept_feedback_endpoint_mismatch"),
            (dict(decision_receipt_ns=f.receipt_ns-1), "invalid_decision_order"),
            (dict(decision="UNKNOWN"), "swept_space_not_clear"),
            (dict(body_geometry_verified=False), "swept_space_not_clear"),
            (dict(forward_m_s=.2), "swept_target_mismatch"),
            (dict(clear_distance_m=.4), "insufficient_braking_space"),
            (dict(clear_distance_m=float("nan")), "nonfinite_feedback_or_space"))
        for changes, reason in variants:
            self.controller.reset(self.context)
            self.assertZero(self.controller.step(f, replace(s, **changes), **args), reason)

    def test_feedback_outside_envelope_and_no_pivot_proposal(self):
        for changes in (dict(forward_m_s=-.01), dict(forward_m_s=.31),
                        dict(lateral_m_s=.051), dict(yaw_rad_s=.201)):
            self.controller.reset(self.context)
            self.assertZero(self.step(**changes), "feedback_outside_measured_envelope")
        self.controller = EncoderlessDrivePreview(replace(self.profile, yaw_kp=100.), self.timing)
        self.controller.reset(self.context)
        self.assertZero(self.step(yaw_rad_s=.1), "proposal_not_both_wheels_forward")

    def test_calibration_and_timing_changes_require_reset(self):
        self.step(0)
        self.controller.profile = replace(self.profile, linear_ff=81.)
        self.assertZero(self.step(1), "profile_or_timing_changed")
        self.controller.profile = self.profile
        self.controller.reset(self.context)
        self.controller.timing = replace(self.timing, max_step_ns=240_000_000)
        self.assertZero(self.step(2), "profile_or_timing_changed")
        c = EncoderlessDrivePreview(self.profile)
        self.assertZero(c.reset(self.context), "timing_envelope_required")

    def test_burst_sensors_cannot_supply_extra_slew_time(self):
        self.step(0)
        f, s, args = self.sample(1)
        # New source frame, but control only 1ms later: 0.1 percentage point.
        f = replace(f, receipt_ns=10_001_000_000)
        s = replace(s, scan_receipt_ns=f.receipt_ns, decision_receipt_ns=10_010_500_000)
        args["receipt_now_ns"] = 10_011_000_000
        r = self.controller.step(f, s, **args)
        self.assertAlmostEqual(r.left_percent, .1)
        self.assertTrue(r.slew_limited)

    def test_nonfinite_target_and_intermediate_math_latch_zero(self):
        f, s, args = self.sample()
        for value in (None, True, float("nan"), float("inf"), 10**400):
            self.controller.reset(self.context)
            self.assertZero(self.controller.step(f, s,
                **dict(args, desired_forward_m_s=value)), "invalid_target")
        # Deliberately hostile synthetic gains, never a calibration suggestion.
        self.controller = EncoderlessDrivePreview(replace(self.profile,
            yaw_kp=1.7e308, max_yaw_rad_s=1.), self.timing)
        self.controller.reset(self.context)
        f = replace(f, yaw_rad_s=-1.)
        s = replace(s, yaw_rad_s=1.)
        self.assertZero(self.controller.step(f, s,
            **dict(args, desired_yaw_rad_s=1.)), "nonfinite_proposal")


if __name__ == "__main__":
    unittest.main()
