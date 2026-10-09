"""Pure real-loop observation tests; synthetic inputs, no hardware/API IO."""
import json
import threading
import time
import unittest

from dashboard_server.autonomy_observation import AutonomyObservation, bounded_copy
from dashboard_server.record_based_autonomy import RecordBasedAutonomy


def frame(sequence=1, *, motion=True, imu=True):
    stamp, receipt = 1_000_000_000+sequence*20_000_000, 100_000_000_000+sequence*20_000_000
    return dict(context=dict(source_session="synthetic-native-session", generation=1,
        source_clock_domain="synthetic-SYSTEM", receipt_clock_domain="synthetic-RAW"),
        scan=dict(stamp_ns=stamp, receipt_ns=receipt, sequence=sequence, frame_id="laser",
            ranges=[2.] * 24, angle_min=-1., angle_increment=.1,
            range_min=.1, range_max=10., original_native=True),
        imu=None if not imu else dict(stamp_ns=stamp-1_000_000, receipt_ns=receipt-1_000_000,
            yaw_rate_rad_s=0., original_native=True),
        motion=None if not motion else dict(previous_scan_stamp_ns=stamp-20_000_000,
            current_scan_stamp_ns=stamp, receipt_ns=receipt, dx_m=.01, dy_m=0., dyaw_rad=0.,
            accepted=True, map_eligible=True, path_break=False, motion_prior=False))


class SyntheticClock:
    def __init__(self):
        self.lock = threading.Lock()
        self.value = None

    def set(self, row, age=1_000_000):
        with self.lock:
            self.value = dict(source_now_ns=row["scan"]["stamp_ns"]+age,
                receipt_now_ns=row["scan"]["receipt_ns"]+age,
                source_clock_domain=row["context"]["source_clock_domain"],
                receipt_clock_domain=row["context"]["receipt_clock_domain"])

    def __call__(self):
        with self.lock:
            return dict(self.value)


class LatestSource:
    def __init__(self, clock):
        self.lock, self.clock = threading.Lock(), clock
        self.row, self.calls = None, 0

    def put(self, row):
        self.clock.set(row)
        with self.lock:
            self.row = bounded_copy(row)

    def __call__(self):
        with self.lock:
            self.calls += 1
            row = None if self.row is None else bounded_copy(self.row)
        if row is not None:
            current = self.clock()
            row.update(source_now_ns=current["source_now_ns"], receipt_now_ns=current["receipt_now_ns"])
        return row


class FakePlanner:
    def __init__(self):
        self.resets, self.plans, self.stops = [], [], []
        self.manual_calls = 0
        self.block = False
        self.entered, self.release = threading.Event(), threading.Event()
        self.error = None
        self.overrides = {}

    def __call__(self, snapshot, preference):
        return self.plan(snapshot, cruise_percent=preference)

    def reset(self, context):
        self.resets.append(context)

    def plan(self, snapshot, *, cruise_percent):
        self.plans.append((bounded_copy(snapshot), cruise_percent))
        if self.block:
            self.entered.set()
            if not self.release.wait(2):
                raise RuntimeError("synthetic plan release missing")
        if self.error:
            raise self.error
        answer = dict(decision="PROPOSE_FORWARD", reason="synthetic_observation",
            proposed_left_percent=min(cruise_percent, 30.), proposed_right_percent=min(cruise_percent, 30.),
            applied_left_percent=0., applied_right_percent=0.,
            trajectory=[dict(x_m=0., y_m=0., yaw_rad=0.), dict(x_m=.1, y_m=0., yaw_rad=0.)],
            runtime_authority=False, motor_command_sent=False, autonomy_output_enabled=False,
            physical_autonomy_qualified=False, execution_eligible=False)
        answer.update(self.overrides)
        return answer

    def stop(self, reason="operator_stop"):
        self.stops.append(reason)

    def manual_takeover(self):
        self.manual_calls += 1


class ObservationTests(unittest.TestCase):
    def wait_for(self, predicate, timeout=2):
        deadline = time.monotonic()+timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("synthetic observation condition timed out")
            time.sleep(.001)

    def fixture(self, *, polling=False):
        planner, clock = FakePlanner(), SyntheticClock()
        source = LatestSource(clock)
        observer = AutonomyObservation(planner, input_provider=source if polling else None,
            clock=None if polling else clock, poll_interval_s=.001)
        return observer, planner, clock, source

    def send(self, observer, clock, row):
        clock.set(row)
        self.assertTrue(observer.submit(row, **observer.input_token()))

    def test_constructor_is_inert_default_unconfigured_and_explicit_start_required(self):
        observer, planner, _, source = self.fixture(polling=True)
        self.assertEqual(observer.status()["state"], "idle")
        self.assertEqual((source.calls, planner.resets, planner.plans), (0, [], []))
        self.assertFalse(observer.status()["active"])
        self.assertFalse(observer.status()["runtime_authority"])
        self.assertIsNone(observer.status()["calibrated_speed_m_s"])
        with self.assertRaisesRegex(ValueError, "not_configured"):
            AutonomyObservation().start()
        observer.start()
        self.wait_for(lambda: source.calls > 0)
        self.assertEqual(observer.status()["current_decision"]["reason"], "WAIT_INPUT")
        self.assertTrue(observer.status()["active"])
        self.assertFalse(observer.stop()["owned"])

    def test_real_source_loop_reads_plans_and_preserves_original_stamps(self):
        observer, planner, _, source = self.fixture(polling=True)
        row = frame()
        source.put(row)
        try:
            observer.start(cruise_percent=15.)
            self.wait_for(lambda: observer.status()["counters"]["decisions"] == 1)
            status = observer.status()
            self.assertEqual(status["state"], "observing")
            self.assertEqual(status["current_decision"]["proposed_left_percent"], 15.)
            self.assertEqual(status["applied_left_percent"], 0.)
            self.assertEqual(status["current_decision"]["applied_right_percent"], 0.)
            planned = planner.plans[0][0]
            self.assertEqual(planned["scan"], row["scan"])
            self.assertEqual(planned["imu"], row["imu"])
            self.assertEqual(status["source"]["context"], row["context"])
            self.assertEqual(status["input_age"]["scan_receipt_ns"], 1_000_000)
            self.assertEqual(len(status["trajectory"]), 2)
            self.assertFalse(status["source"]["source_qualification_asserted"])
            self.assertTrue(all(status[key] is False for key in
                ("motor_command_sent", "runtime_authority", "autonomy_output_enabled", "execution_eligible")))
            self.assertNotIn("ranges", json.dumps(status))
        finally:
            observer.stop()

    def test_cached_repeats_do_not_replan_and_expiry_waits_for_new_fresh_sequence(self):
        observer, planner, clock, source = self.fixture(polling=True)
        row = frame()
        source.put(row)
        try:
            observer.start()
            self.wait_for(lambda: observer.status()["counters"]["duplicates"] >= 2)
            self.assertEqual((len(planner.resets), len(planner.plans)), (1, 1))
            clock.set(row, age=300_000_000)
            self.wait_for(lambda: observer.status()["current_decision"]["reason"] == "WAIT_STALE_INPUT")
            self.assertIsNone(observer.status()["first_failure_reason"])
            self.assertTrue(observer.status()["active"])
            self.assertEqual(observer.status()["current_decision"]["decision"], "HOLD")
            self.assertEqual(observer.status()["current_decision"]["proposed_left_percent"], 0.)
            self.assertEqual(observer.status()["trajectory"], [])
            self.assertEqual(observer.status()["applied_left_percent"], 0.)
            self.assertEqual(len(planner.plans), 1)
            source.put(frame(20))  # Fresh clocks advance; no old stamp is repaired.
            self.wait_for(lambda: observer.status()["counters"]["decisions"] == 2)
            self.assertEqual(observer.status()["source"]["scan_sequence"], 20)
            self.assertEqual(observer.status()["applied_left_percent"], 0.)
        finally:
            observer.stop()

    def test_post_plan_positive_expiry_discards_then_new_fresh_input_recovers_zero(self):
        observer, planner, clock, _ = self.fixture()
        planner.block = True
        row = frame()
        try:
            observer.start()
            self.send(observer, clock, row)
            self.assertTrue(planner.entered.wait(1))
            clock.set(row, age=300_000_000)
            planner.release.set()
            self.wait_for(lambda: observer.status()["counters"]["stale_results"] == 1)
            status = observer.status()
            self.assertTrue(status["active"])
            self.assertIsNone(status["first_failure_reason"])
            self.assertEqual(status["counters"]["decisions"], 0)
            self.assertEqual(status["current_decision"]["reason"], "WAIT_STALE_INPUT")
            self.assertEqual(status["current_decision"]["proposed_left_percent"], 0.)
            self.assertEqual(status["current_decision"]["trajectory"], [])
            self.assertTrue(all(json.loads(r.diagnostic_json)["proposed_left_percent"] == 0
                                for r in observer.evidence()))
            planner.block = False
            self.send(observer, clock, frame(20))
            self.wait_for(lambda: observer.status()["counters"]["decisions"] == 1)
            self.assertEqual(observer.status()["source"]["scan_sequence"], 20)
            self.assertEqual(observer.status()["current_decision"]["applied_left_percent"], 0.)
            self.assertFalse(observer.status()["motor_command_sent"])
        finally:
            planner.release.set()
            observer.stop()

    def test_initial_expiry_never_calls_planner_and_corrupt_expired_context_remains_fatal(self):
        observer, planner, clock, _ = self.fixture()
        row = frame()
        try:
            observer.start()
            clock.set(row, age=300_000_000)
            self.assertTrue(observer.submit(row, **observer.input_token()))
            self.wait_for(lambda: observer.status()["current_decision"]["reason"] == "WAIT_STALE_INPUT")
            self.assertEqual(planner.plans, [])
            self.assertEqual(planner.resets, [])
            bad = frame(20)
            bad["context"]["source_session"] = "unexpected-reconnect"
            clock.set(bad, age=300_000_000)
            self.assertFalse(observer.submit(bad, **observer.input_token()))
            self.assertIn("context_changed", observer.status()["first_failure_reason"])
            self.assertEqual(observer.status()["current_decision"]["decision"], "STOP")
        finally:
            observer.stop()

    def test_post_plan_getter_absence_discards_without_fabricated_clock_or_reconnect(self):
        observer, planner, _, source = self.fixture(polling=True)
        planner.block = True
        source.put(frame())
        try:
            observer.start()
            self.assertTrue(planner.entered.wait(1))
            with source.lock:
                source.row = None
            planner.release.set()
            self.wait_for(lambda: observer.status()["current_decision"]["reason"] == "WAIT_INPUT")
            self.assertIsNone(observer.status()["first_failure_reason"])
            self.assertEqual(observer.status()["counters"]["decisions"], 0)
            self.assertEqual(observer.status()["current_decision"]["proposed_left_percent"], 0.)
        finally:
            planner.release.set()
            observer.stop()

    def test_real_planner_reanchor_recovers_expiry_but_never_clears_existing_STOP(self):
        for stopped in (False, True):
            with self.subTest(stopped=stopped):
                clock, planner = SyntheticClock(), RecordBasedAutonomy()
                observer = AutonomyObservation(planner, clock=clock, poll_interval_s=.001)
                first = frame()
                first["scan"].update(ranges=[10.] * 24, range_max=30.)
                try:
                    observer.start()
                    self.send(observer, clock, first)
                    self.wait_for(lambda: observer.status()["counters"]["decisions"] == 1)
                    clock.set(first, age=300_000_000)
                    self.wait_for(lambda: observer.status()["current_decision"]["reason"] == "WAIT_STALE_INPUT")
                    if stopped:
                        planner.stop("operator_stop")
                    newer = frame(20)
                    newer["scan"].update(ranges=[10.] * 24, range_max=30.)
                    self.send(observer, clock, newer)
                    self.wait_for(lambda: observer.status()["counters"]["decisions"] == 2)
                    decision = observer.status()["current_decision"]
                    self.assertEqual(decision["decision"], "STOP" if stopped else "HOLD")
                    self.assertEqual(decision["reason"], "operator_stop" if stopped else "row_end_or_two_sided_support_unknown")
                    self.assertEqual(decision["applied_left_percent"], 0.)
                    self.assertIsNone(observer.status()["first_failure_reason"])
                    self.assertEqual(clock()["source_now_ns"], newer["scan"]["stamp_ns"]+1_000_000)
                finally:
                    observer.stop()

    def test_expired_clock_jump_reversal_and_future_do_not_become_waiting(self):
        for case in ("jump", "reversal", "future"):
            with self.subTest(case=case):
                observer, _, clock, source = self.fixture(polling=True)
                row = frame()
                source.put(row)
                try:
                    observer.start()
                    self.wait_for(lambda: observer.status()["counters"]["decisions"] == 1)
                    with clock.lock:
                        if case == "jump":
                            clock.value["source_now_ns"] += 300_000_000
                        elif case == "reversal":
                            clock.value["source_now_ns"] -= 500_000
                            clock.value["receipt_now_ns"] -= 500_000
                        else:
                            clock.value["receipt_now_ns"] = row["scan"]["receipt_ns"]-1
                    self.wait_for(lambda: observer.status()["first_failure_reason"] is not None)
                    self.assertEqual(observer.status()["current_decision"]["decision"], "STOP")
                finally:
                    observer.stop()

    def test_polling_None_then_fresh_skipped_sequence_uses_guarded_first_zero(self):
        from test_record_based_autonomy import native_snapshot
        clock = SyntheticClock()
        source = LatestSource(clock)
        planner = RecordBasedAutonomy()
        observer = AutonomyObservation(planner, input_provider=source, poll_interval_s=.001)
        source.put(native_snapshot(1))
        try:
            observer.start()
            self.wait_for(lambda: observer.status()["counters"]["decisions"] == 1)
            self.assertEqual(observer.status()["current_decision"]["reason"], "first_zero_required")
            with source.lock:
                source.row = None  # Getter withholds unavailable/expired cache.
            self.wait_for(lambda: observer.status()["current_decision"]["reason"] == "WAIT_INPUT")
            self.assertEqual(observer.status()["current_decision"]["proposed_left_percent"], 0.)
            source.put(native_snapshot(4))  # Real adjacent pair3→4; planner last saw1.
            self.wait_for(lambda: observer.status()["counters"]["decisions"] == 2)
            self.assertEqual(observer.status()["current_decision"]["reason"], "first_zero_required")
            self.assertEqual(observer.status()["source"]["scan_sequence"], 4)
            self.assertEqual(observer.status()["current_decision"]["applied_left_percent"], 0.)
            self.assertIsNone(observer.status()["first_failure_reason"])
        finally:
            observer.stop()

    def test_missing_motion_and_IMU_wait_without_inventing_feedback(self):
        for motion, imu, expected in ((False, True, "WAIT_LOCALIZATION"), (False, False, "WAIT_IMU")):
            with self.subTest(expected=expected):
                observer, planner, clock, _ = self.fixture()
                try:
                    observer.start()
                    self.send(observer, clock, frame(motion=motion, imu=imu))
                    self.wait_for(lambda: observer.status()["counters"]["decisions"] == 1)
                    status = observer.status()
                    self.assertEqual(status["current_decision"]["reason"], expected)
                    self.assertTrue(status["active"])
                    self.assertEqual(planner.plans, [])
                    self.assertEqual(status["input_age"]["scan_source_ns"], 1_000_000)
                    self.assertIsNone(status["current_decision"].get("body_velocity_mps"))
                finally:
                    observer.stop()

    def test_getter_absence_during_priming_or_inactive_mapping_is_WAIT_zero(self):
        observer, _, _, source = self.fixture(polling=True)
        source.put(frame())
        try:
            observer.start()
            self.wait_for(lambda: observer.status()["counters"]["decisions"] == 1)
            with source.lock:
                source.row = None
            self.wait_for(lambda: observer.status()["current_decision"]["reason"] == "WAIT_INPUT")
            self.assertTrue(observer.status()["active"])
            self.assertTrue(observer.status()["latest_stale"])
            self.assertEqual(observer.status()["trajectory"], [])
            self.assertIsNone(observer.status()["first_failure_reason"])
        finally:
            observer.stop()

    def test_context_reconnect_sequence_rewind_and_same_sequence_mutation_latch(self):
        for case in ("context", "sequence", "changed"):
            with self.subTest(case=case):
                observer, _, clock, _ = self.fixture()
                try:
                    observer.start()
                    first = frame(2)
                    self.send(observer, clock, first)
                    self.wait_for(lambda: observer.status()["counters"]["decisions"] == 1)
                    row = frame(3 if case == "context" else 1 if case == "sequence" else 2)
                    if case == "context":
                        row["context"]["reconnect_id"] = "new-connection"
                    elif case == "changed":
                        row["scan"]["ranges"][0] = 8.
                    clock.set(row)
                    accepted = observer.submit(row, **observer.input_token())
                    if case == "context":
                        self.assertFalse(accepted)
                    self.wait_for(lambda: observer.status()["first_failure_reason"] is not None)
                    self.assertFalse(observer.status()["active"])
                    self.assertEqual(observer.status()["current_decision"]["decision"], "STOP")
                finally:
                    observer.stop()

    def test_clock_domain_future_and_motion_endpoint_cannot_publish(self):
        for case in ("clock", "future", "endpoint"):
            with self.subTest(case=case):
                observer, planner, clock, _ = self.fixture()
                try:
                    observer.start()
                    row = frame()
                    clock.set(row, age=-1 if case == "future" else 1_000_000)
                    if case == "clock":
                        clock.value["receipt_clock_domain"] = "different-domain"
                    elif case == "endpoint":
                        row["motion"]["current_scan_stamp_ns"] -= 1
                    observer.submit(row, **observer.input_token())
                    self.wait_for(lambda: observer.status()["first_failure_reason"] is not None)
                    self.assertEqual(observer.status()["counters"]["decisions"], 0)
                    self.assertEqual(planner.plans, [])
                finally:
                    observer.stop()

    def test_manual_takeover_retains_reason_and_rejects_late_generation_inputs(self):
        observer, planner, clock, _ = self.fixture()
        observer.start(session="explicit-session")
        old = observer.input_token()
        self.send(observer, clock, frame())
        self.wait_for(lambda: observer.status()["counters"]["decisions"] == 1)
        stopped = observer.manual_takeover()
        self.assertEqual(stopped["stop_reason"], "manual_takeover")
        self.assertEqual(stopped["current_decision"]["reason"], "manual_takeover")
        self.assertEqual(stopped["current_decision"]["proposed_left_percent"], 0.)
        self.assertFalse(stopped["owned"])
        self.assertEqual(planner.manual_calls, 1)
        observer.start(session="new-session")
        self.assertFalse(observer.submit(frame(2), **old))
        self.assertEqual(observer.status()["counters"]["received"], 0)
        observer.stop()

    def test_slow_planner_stop_timeout_holds_owner_and_discards_late_result(self):
        observer, planner, clock, _ = self.fixture()
        planner.block = True
        observer.start()
        self.send(observer, clock, frame())
        self.assertTrue(planner.entered.wait(1))
        try:
            pending = observer.stop(timeout=.01)
            self.assertTrue(pending["owned"])
            self.assertEqual(pending["state"], "stopping")
            self.assertFalse(pending["active"])
            self.assertEqual(observer.start()["generation"], pending["generation"])
            self.assertEqual(len(planner.resets), 1)
        finally:
            planner.release.set()
        self.wait_for(lambda: observer.status()["counters"]["late_results"] == 1)
        self.assertIsNone(observer.status()["latest_decision"])
        self.assertFalse(observer.stop()["owned"])

    def test_cruise_percent_change_discards_inflight_result_then_next_scan_uses_it(self):
        observer, planner, clock, _ = self.fixture()
        planner.block = True
        observer.start(cruise_percent=10.)
        self.send(observer, clock, frame())
        self.assertTrue(planner.entered.wait(1))
        try:
            changed = observer.set_speed(20.)
            self.assertEqual(changed["cruise_percent"], 20.)
            self.assertIsNone(changed["calibrated_speed_m_s"])
            planner.release.set()
            self.wait_for(lambda: observer.status()["counters"]["late_results"] == 1)
            planner.block = False
            self.send(observer, clock, frame(2))
            self.wait_for(lambda: observer.status()["counters"]["decisions"] == 1)
            self.assertEqual(planner.plans[-1][1], 20.)
            self.assertEqual(observer.status()["current_decision"]["proposed_left_percent"], 20.)
        finally:
            planner.release.set()
            observer.stop()

    def test_queue_overflow_is_visible_no_drop_or_second_owner(self):
        observer, planner, clock, _ = self.fixture()
        planner.block = True
        observer.start()
        first = frame()
        self.send(observer, clock, first)
        self.assertTrue(planner.entered.wait(1))
        try:
            token = observer.input_token()
            for sequence in range(2, 10):
                self.assertTrue(observer.submit(frame(sequence), **token))
            self.assertFalse(observer.submit(frame(10), **token))
            status = observer.status()
            self.assertIn("queue_overflow", status["first_failure_reason"])
            self.assertEqual(status["queue"]["depth"], 8)
            self.assertEqual(observer.start()["generation"], token["generation"])
        finally:
            planner.release.set()
            observer.stop()
        self.assertEqual(observer.status()["queue"]["depth"], 0)

    def test_planner_errors_or_authority_output_are_visible_and_applied_always_zero(self):
        for override in ({"runtime_authority": True}, {"applied_left_percent": 1.}, {"trajectory": [0]}):
            with self.subTest(override=override):
                observer, planner, clock, _ = self.fixture()
                planner.overrides = override
                try:
                    observer.start()
                    self.send(observer, clock, frame())
                    self.wait_for(lambda: observer.status()["first_failure_reason"] is not None)
                    self.assertEqual(observer.status()["counters"]["decisions"], 0)
                    self.assertEqual(observer.status()["applied_left_percent"], 0.)
                    self.assertFalse(observer.status()["runtime_authority"])
                finally:
                    observer.stop()

    def test_private_input_copy_and_bounded_immutable_evidence(self):
        observer, planner, clock, _ = self.fixture()
        try:
            observer.start()
            for sequence in range(1, 135):
                row = frame(sequence)
                self.send(observer, clock, row)
                row["scan"]["ranges"][0] = 999.
                self.wait_for(lambda: observer.status()["counters"]["decisions"] == sequence)
            status = observer.status()
            self.assertEqual(status["retained_evidence"], 128)
            self.assertEqual(status["evicted_evidence"], 6)
            self.assertLessEqual(status["retained_evidence_bytes"], 1024*1024)
            self.assertEqual(planner.plans[0][0]["scan"]["ranges"][0], 2.)
            evidence = observer.evidence()
            self.assertIsInstance(evidence, tuple)
            self.assertIsInstance(evidence[-1].diagnostic_json, bytes)
            status["current_decision"]["trajectory"][0]["x_m"] = 99.
            self.assertEqual(observer.status()["current_decision"]["trajectory"][0]["x_m"], 0.)
        finally:
            observer.stop()

    def test_concurrent_Starts_deduplicate_one_worker_and_bounds_reject(self):
        observer, planner, _, _ = self.fixture()
        barrier, answers = threading.Barrier(3), []

        def start():
            barrier.wait()
            answers.append(observer.start())

        threads = [threading.Thread(target=start) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(1)
            self.assertFalse(thread.is_alive())
        self.assertEqual([answer["generation"] for answer in answers], [1, 1])
        self.assertEqual(planner.resets, [])
        for bad in (True, -1, 31, float("nan"), "10"):
            with self.assertRaises(ValueError):
                observer.set_speed(bad)
        observer.stop()

    def test_actual_record_planner_seam_uses_percent_and_unverified_input_HOLD(self):
        clock = SyntheticClock()
        observer = AutonomyObservation(RecordBasedAutonomy(), clock=clock, poll_interval_s=.001)
        row = frame()
        row["scan"]["original_native"] = False  # never repair a WS provenance declaration
        try:
            observer.start(cruise_percent=12.)
            self.send(observer, clock, row)
            self.wait_for(lambda: observer.status()["counters"]["decisions"] == 1)
            decision = observer.status()["current_decision"]
            self.assertEqual(decision["decision"], "HOLD")
            self.assertEqual(decision["reason"], "original_native_provenance_unverified")
            self.assertEqual((decision["applied_left_percent"], decision["applied_right_percent"]), (0., 0.))
            self.assertFalse(decision["motor_command_sent"])
        finally:
            self.assertFalse(observer.stop()["owned"])

    def test_reconnect_during_slow_plan_cannot_publish_old_source(self):
        observer, planner, _, source = self.fixture(polling=True)
        planner.block = True
        source.put(frame())
        try:
            observer.start()
            self.assertTrue(planner.entered.wait(1))
            newer = frame(2)
            newer["context"]["source_session"] = "reconnected-synthetic-session"
            source.put(newer)
            planner.release.set()
            self.wait_for(lambda: observer.status()["first_failure_reason"] is not None)
            self.assertIn("reconnected_during_plan", observer.status()["first_failure_reason"])
            self.assertIsNone(observer.status()["latest_decision"])
            self.assertEqual(observer.status()["counters"]["decisions"], 0)
        finally:
            planner.release.set()
            observer.stop()

    def test_stop_before_factory_entry_retains_reason_without_creating_worker(self):
        observer, planner, _, _ = self.fixture()
        entered, release = threading.Event(), threading.Event()
        factory = observer.lifecycle._factory

        def delayed(context):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("synthetic factory release missing")
            return factory(context)

        observer.lifecycle._factory = delayed
        results = []
        thread = threading.Thread(target=lambda: results.append(observer.start()))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(observer.stop(timeout=.01)["owned"])
            release.set()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertFalse(results[0]["owned"])
            self.assertEqual(results[0]["stop_reason"], "operator_stop")
            self.assertEqual(results[0]["current_decision"]["decision"], "STOP")
            self.assertEqual(planner.resets, [])
            self.assertIsNone(observer.resource.thread)
        finally:
            release.set()
            thread.join(2)
            observer.stop()


if __name__ == "__main__":
    unittest.main()
