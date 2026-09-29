import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from dashboard_server.motor_pwm import (
    MOTOR_DIRECTION_PINS, MOTOR_POWER_PINS, MOTOR_PIN_GPIO,
    MOTOR_TIMELINE_SCHEMA, MotorTimeline, pin_metadata, sanitize_status, with_recorded_motor,
)


def motor_status(**changes):
    return {
        "updated_at": 100.0, "ok": True, "state": "armed_manual",
        "connected": True, "armed": True, "moving": True,
        "firmware": "UBTS_WHEEL_PWM_V2", "requested": {"left": 50, "right": -20},
        "outputs": {"D8": 81, "D5": 0, "D3": 0, "D0": 33,
                    "D1": 255, "D2": 255, "D6": 255, "D7": 255}, **changes,
    }


class MotorPwmTests(unittest.TestCase):
    def test_declarative_pin_metadata_matches_authoritative_controller(self):
        from scripts.esp_wheel_controller import PIN_GPIO, WheelController
        self.assertEqual(MOTOR_DIRECTION_PINS, WheelController.DIRECTION_PINS)
        self.assertEqual(MOTOR_POWER_PINS, WheelController.POWER_PINS)
        self.assertEqual(MOTOR_PIN_GPIO, {pin: PIN_GPIO[pin] for pin in MOTOR_PIN_GPIO})

    def test_pin_roles_requested_derivation_and_reported_ramp_remain_distinct(self):
        sample = sanitize_status(motor_status(), 100.1)
        roles = {row["pin"]: (row["gpio"], row["role"]) for row in pin_metadata()}
        self.assertEqual(roles["D8"], (15, "left_forward"))
        self.assertEqual(roles["D5"], (14, "left_reverse"))
        self.assertEqual(roles["D3"], (0, "right_forward"))
        self.assertEqual(roles["D0"], (16, "right_reverse"))
        self.assertEqual(sample["pins"]["D8"]["requested_pwm_derived"], 128)
        self.assertEqual(sample["pins"]["D8"]["reported_pwm"], 81)
        self.assertEqual(sample["pins"]["D0"]["requested_pwm_derived"], 51)
        self.assertEqual(sample["pins"]["D3"]["requested_pwm_derived"], 0)
        self.assertIsNone(sample["pins"]["D1"]["requested_pwm_derived"])
        self.assertFalse(sample["source_stale"])
        self.assertIsNone(sample["pins"]["D8"]["target_pwm"])
        self.assertIsNone(sample["pins"]["D8"]["reported_age_s"])
        self.assertIsNone(sample["pins"]["D8"]["simulated_output_pwm"])
        self.assertFalse(sample["status"]["simulated"])

    def test_simulation_outputs_are_separate_from_any_mcu_report(self):
        for marker in ({"simulated": True}, {"firmware": "SIMULATED_GAZEBO_SKID"},
                       {"transport": "simulation"}, {"mode": "simulation"}):
            sample = sanitize_status(motor_status(**marker), 100.1)
            self.assertTrue(sample["status"]["simulated"])
            self.assertEqual(sample["source_kind"], "simulation")
            self.assertTrue(all(pin["reported_pwm"] is None for pin in sample["pins"].values()))
            self.assertEqual(sample["pins"]["D8"]["simulated_output_pwm"], 81)
            self.assertEqual(sample["pins"]["D8"]["requested_pwm_derived"], 128)

    def test_descriptor_labels_recorded_simulation_actual_and_mixed_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder = MotorTimeline("/rover/status")
            recorder.start(Path(temporary), 10, 100)
            self.assertEqual(recorder.descriptor()["source_kind"], "unknown")
            self.assertIsNone(recorder.descriptor()["sample_hz"])
            recorder.observe(json.dumps(motor_status(simulated=True)), received_at=100, received_steady=10)
            self.assertEqual(recorder.descriptor()["source_kind"], "simulation")
            self.assertEqual(recorder.descriptor()["sample_hz"], 4)
            recorder.observe(json.dumps(motor_status(updated_at=101)), received_at=101, received_steady=11)
            self.assertEqual(recorder.descriptor()["source_kind"], "mixed")
            self.assertIsNone(recorder.descriptor()["sample_hz"])
            recorder.close()
            recorder.start(Path(temporary), 20, 200)
            recorder.observe(json.dumps(motor_status(updated_at=200)), received_at=200, received_steady=20)
            self.assertEqual(recorder.descriptor()["source_kind"], "mcu_status")
            self.assertEqual(recorder.descriptor()["sample_hz"], 2)
            recorder.close()

    def test_disarmed_power_and_disconnected_cached_reports_are_not_forged_zero(self):
        sample = sanitize_status(motor_status(state="disarmed", armed=False, connected=False,
                                            requested={"left": 0, "right": 0}), 100.2)
        self.assertEqual(sample["pins"]["D1"]["reported_pwm"], 255)
        self.assertEqual(sample["pins"]["D8"]["reported_pwm"], 81)
        self.assertFalse(sample["status"]["connected"])
        self.assertIsNone(sample["pins"]["D1"]["requested_pwm_derived"])

    def test_missing_and_invalid_values_are_unknown_not_zero_and_secrets_are_excluded(self):
        sample = sanitize_status(motor_status(
            outputs={"D8": 256, "D5": -1, "D3": True, "D0": "22", "D4": 200, "lease": "secret"},
            requested={"left": float("nan"), "right": False, "ticket": "secret"},
            ticket="secret", lease="secret", owner="secret", reason="secret", last_error="secret",
        ), 100.1)
        self.assertNotIn("secret", json.dumps(sample, allow_nan=False))
        self.assertNotIn("D4", sample["pins"])
        self.assertTrue(all(pin["reported_pwm"] is None for pin in sample["pins"].values()))
        self.assertEqual(sample["requested"], {"left": None, "right": None})

    def test_source_stale_missing_future_invalid_and_overflow_timestamps(self):
        for updated in (None, "100", float("nan"), 10**400, 110, 98):
            with self.subTest(updated=repr(updated)[:50]):
                sample = sanitize_status(motor_status(updated_at=updated), 100.0)
                self.assertTrue(sample["source_stale"])
                json.dumps(sample, allow_nan=False)

    def test_streaming_full_history_bounded_tail_idle_and_closed_do_not_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recorder = MotorTimeline("/rover/status", max_frames=2)
            recorder.observe(json.dumps(motor_status()), received_at=100, received_steady=9)
            self.assertEqual(recorder.frame_count, 0)
            recorder.start(root, 10, 100)
            for i in range(5):
                recorder.observe(json.dumps(motor_status(updated_at=100+i/2)),
                                 received_at=100+i/2, received_steady=10+i/2)
            recorder.close()
            recorder.observe(json.dumps(motor_status()), received_at=103, received_steady=13)
            rows = [json.loads(line) for line in (root / "motor.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 5)
            self.assertEqual([row["t_s"] for row in recorder.frames], [1.5, 2.0])
            self.assertEqual(recorder.frame_count, 5)
            self.assertEqual(recorder.descriptor()["schema"], MOTOR_TIMELINE_SCHEMA)
            self.assertFalse(recorder.status(15)["topic_alive"])

    def test_no_motor_source_stays_empty_and_malformed_messages_do_not_create_samples(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder = MotorTimeline("/rover/status")
            recorder.start(Path(temporary), 1, 99)
            for data in ("invalid", "[]", "{}", "[" * 2000, "x" * 65537):
                recorder.observe(data, received_at=100, received_steady=2)
            recorder.close()
            self.assertEqual(recorder.frame_count, 0)
            self.assertEqual(recorder.descriptor()["frames"], [])
            self.assertFalse(recorder.status(3)["topic_alive"])
            self.assertEqual((Path(temporary) / "motor.jsonl").read_text(), "")

    def test_write_failure_is_visible_and_does_not_claim_saved_frame(self):
        recorder = MotorTimeline("/rover/status")
        recorder.started_steady = 0
        recorder.started_at = 99
        recorder.log = Mock()
        recorder.log.write.side_effect = OSError("sensitive path")
        recorder.observe(json.dumps(motor_status()), received_at=100, received_steady=1)
        self.assertEqual(recorder.frame_count, 0)
        self.assertEqual(recorder.error, "motor_sidecar_write_failed")
        self.assertNotIn("sensitive", json.dumps(recorder.descriptor()))

    def test_wall_playhead_alignment_keeps_steady_provenance_and_skips_clock_regression(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder = MotorTimeline("/rover/status")
            recorder.start(Path(temporary), 10, 100)
            for wall, steady in ((101, 11), (105, 12), (99, 13), (104, 14), (106, 15)):
                recorder.observe(json.dumps(motor_status(updated_at=wall)), received_at=wall, received_steady=steady)
            recorder.close()
            rows = list(recorder.frames)
            self.assertEqual([row["t_s"] for row in rows], [1, 5, 6])
            self.assertEqual([row["elapsed_monotonic_s"] for row in rows], [1, 2, 5])
            self.assertEqual([row["clock_offset_s"] for row in rows], [0, 3, 1])
            self.assertEqual(recorder.dropped_time_frames, 2)
            self.assertEqual(recorder.error, "motor_recording_clock_regressed")

    def test_window_sidecar_overrides_tail_and_carries_prior_sample_without_future(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [{"t_s": i/2, "pins": {"D1": {"reported_pwm": 255}}} for i in range(15)]
            (root / "motor.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
            timeline = {"motor": {"schema": MOTOR_TIMELINE_SCHEMA, "frames": rows[-2:]}}
            result = with_recorded_motor(root, timeline, 1.2, .8)
            self.assertEqual([row["t_s"] for row in result["motor"]["frames"]], [1, 1.5, 2])
            self.assertEqual(timeline["motor"]["frames"], rows[-2:])
            after = with_recorded_motor(root, timeline, 10, 1)
            self.assertEqual([row["t_s"] for row in after["motor"]["frames"]], [7])

    def test_legacy_missing_and_partially_truncated_archives_are_truthful(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = {"imu": {"frames": []}}
            self.assertIs(with_recorded_motor(root, old, 0, 4), old)
            new = {"motor": {"schema": MOTOR_TIMELINE_SCHEMA, "frames": []}}
            self.assertEqual(with_recorded_motor(root, new, 0, 4)["motor"]["sidecar_read_error"],
                             "motor_sidecar_unavailable")
            (root / "motor.jsonl").write_text('{"t_s":0,"pins":{}}\n{"t_s":')
            result = with_recorded_motor(root, new, 0, 4)
            self.assertEqual(len(result["motor"]["frames"]), 1)
            self.assertEqual(result["motor"]["sidecar_malformed_rows"], 1)


if __name__ == "__main__":
    unittest.main()
