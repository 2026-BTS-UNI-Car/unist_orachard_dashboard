"""Pure lifecycle tests: injected fake resources, no ROS/process/socket IO."""
from dataclasses import FrozenInstanceError
import json
import threading
import unittest
from unittest.mock import patch

from dashboard_server import native_owner_lifecycle as lifecycle


class FakeResource:
    def __init__(self):
        self.stop_calls = self.join_calls = self.close_calls = 0
        self.joined = self.closed = True
        self.stop_error = self.join_error = self.close_error = None

    def request_stop(self):
        self.stop_calls += 1
        if self.stop_error:
            raise self.stop_error

    def join(self, timeout):
        self.join_calls += 1
        if self.join_error:
            raise self.join_error
        return self.joined

    def close(self):
        self.close_calls += 1
        if self.close_error:
            raise self.close_error
        return self.closed

    def hooks(self):
        return lifecycle.ResourceHooks(self.request_stop, self.join, self.close)


class LifecycleTests(unittest.TestCase):
    def fixture(self, **limits):
        contexts, resources = [], []

        def factory(context):
            contexts.append(context)
            resource = FakeResource()
            resources.append(resource)
            return resource.hooks()

        return lifecycle.NativeOwnerLifecycle(factory, **limits), contexts, resources

    def launch(self, function):
        result, errors = [], []

        def target():
            try:
                result.append(function())
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=target)
        thread.start()
        return thread, result, errors

    def joined(self, invocation):
        thread, result, errors = invocation
        thread.join(2)
        self.assertFalse(thread.is_alive(), "fake thread did not finish")
        self.assertEqual(errors, [])
        return result[0]

    def test_constructor_is_inert_and_running_is_only_factory_activation(self):
        owner, contexts, resources = self.fixture()
        self.assertEqual((contexts, resources), ([], []))
        status = owner.status()
        self.assertEqual(status.state, "idle")
        self.assertFalse(status.owned)
        self.assertTrue(status.integration_pending)
        self.assertFalse(status.runtime_authority)
        self.assertFalse(status.motor_command_sent)
        self.assertFalse(status.autonomy_output_enabled)
        started = owner.start("caller-session")
        self.assertEqual((started.state, started.session), ("running", "caller-session"))
        self.assertEqual(owner.start("different-request"), started)
        self.assertEqual(len(contexts), 1)
        stopped = owner.stop()
        self.assertEqual(stopped.state, "stopped")
        self.assertFalse(stopped.owned)
        self.assertFalse(stopped.cleanup_active)
        self.assertEqual(resources[0].close_calls, 1)

    def test_concurrent_start_deduplicates_and_stop_during_factory_retains_owner(self):
        entered, release = threading.Event(), threading.Event()
        contexts, resource = [], FakeResource()

        def factory(context):
            contexts.append(context)
            entered.set()
            if not release.wait(2):
                raise RuntimeError("fake factory release missing")
            return resource.hooks()

        owner = lifecycle.NativeOwnerLifecycle(factory)
        first = self.launch(lambda: owner.start("first"))
        try:
            self.assertTrue(entered.wait(1))
            second = self.launch(lambda: owner.start("second"))
            self.assertEqual(self.joined(second).session, "first")
            stopped = owner.stop(.01)
            self.assertEqual(stopped.state, "stopping")
            self.assertTrue(stopped.owned)
            self.assertTrue(stopped.creating)
            self.assertTrue(contexts[0].cancelled)
            self.assertTrue(contexts[0].wait_cancel(0))
            self.assertEqual(owner.start("third").session, "first")
            self.assertEqual(len(contexts), 1)
        finally:
            release.set()
        self.assertFalse(self.joined(first).owned)
        self.assertEqual((resource.stop_calls, resource.join_calls, resource.close_calls),
                         (1, 1, 1))

    def test_late_cancelled_start_never_stops_new_owner(self):
        entered, release_factory = threading.Event(), threading.Event()
        late_cleanup, release_cleanup = threading.Event(), threading.Event()
        resources = []

        def factory(context):
            resource = FakeResource()
            resources.append(resource)
            if context.generation == 1:
                entered.set()
                if not release_factory.wait(2):
                    raise RuntimeError("fake factory release missing")
            return resource.hooks()

        owner = lifecycle.NativeOwnerLifecycle(factory)
        stop_bound = owner._stop_owner
        starter = []

        def delayed_stop(captured, deadline):
            if starter and threading.current_thread() is starter[0]:
                late_cleanup.set()
                if not release_cleanup.wait(2):
                    raise RuntimeError("fake cleanup release missing")
            return stop_bound(captured, deadline)

        owner._stop_owner = delayed_stop
        first = self.launch(lambda: owner.start("A"))
        starter.append(first[0])
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(owner.stop(.01).owned)
            release_factory.set()
            self.assertTrue(late_cleanup.wait(1))
            self.assertFalse(owner.stop().owned)
            newer = owner.start("B")
            self.assertEqual(newer.generation, 2)
            release_cleanup.set()
            self.joined(first)
            self.assertEqual(owner.status().state, "running")
            self.assertEqual(owner.status().session, "B")
            self.assertEqual(resources[1].stop_calls, 0)
            self.assertFalse(owner.status().cancelled)
        finally:
            release_factory.set()
            release_cleanup.set()
            first[0].join(2)
            owner.stop()

    def test_unjoined_timeout_blocks_restart_then_late_join_closes_once(self):
        owner, contexts, resources = self.fixture()
        owner.start("A")
        resource = resources[0]
        resource.joined = False
        status = owner.stop(0)
        self.assertTrue(status.owned)
        self.assertEqual(status.state, "stopping")
        self.assertFalse(status.cleanup_active)
        self.assertFalse(contexts[0].publish({"late": True}))
        self.assertEqual(owner.start("B").generation, 1)
        self.assertEqual(len(resources), 1)
        self.assertEqual(resource.close_calls, 0)
        resource.joined = True
        self.assertFalse(owner.stop().owned)
        owner.stop()
        self.assertEqual((resource.stop_calls, resource.join_calls, resource.close_calls),
                         (1, 2, 1))
        self.assertEqual(owner.start("B").generation, 2)
        owner.stop()

    def test_concurrent_stops_do_not_duplicate_close_or_release_inflight_owner(self):
        owner, _, resources = self.fixture()
        owner.start("A")
        entered, release = threading.Event(), threading.Event()

        def join(timeout):
            resources[0].join_calls += 1
            entered.set()
            return release.wait(2)

        resources[0].join = join
        # ResourceHooks bound the original method: replace with exact fake hooks
        # before starting cleanup, without changing the lifecycle implementation.
        owner._owner.resource = resources[0].hooks()
        first = self.launch(lambda: owner.stop(1))
        try:
            self.assertTrue(entered.wait(1))
            pending = owner.stop(.01)
            self.assertTrue(pending.owned)
            self.assertTrue(pending.cleanup_active)
            self.assertEqual(owner.start("B").session, "A")
            self.assertEqual(resources[0].close_calls, 0)
        finally:
            release.set()
        self.assertFalse(self.joined(first).owned)
        self.assertEqual((resources[0].stop_calls, resources[0].join_calls,
                          resources[0].close_calls), (1, 1, 1))

    def test_worker_exception_visible_after_cleanup_and_first_reason_survives(self):
        owner, contexts, resources = self.fixture()
        owner.start("A")
        context = contexts[0]

        def broken_worker(current):
            self.assertTrue(current.publish({"diagnostic": "before fault"}))
            raise RuntimeError("injected worker failure")

        self.assertFalse(context.run(broken_worker))
        self.assertTrue(context.cancelled)
        first = owner.status().first_failure_reason
        self.assertEqual(first, "RuntimeError: injected worker failure")
        self.assertFalse(context.publish({"diagnostic": "after fault"}))
        resources[0].stop_error = OSError("later cleanup failure")
        status = owner.stop()
        self.assertEqual(status.state, "failed")
        self.assertFalse(status.owned)
        self.assertEqual(status.first_failure_reason, first)
        self.assertTrue(status.latest_stale)
        self.assertEqual(resources[0].close_calls, 1)
        self.assertFalse(context.fail("stale failure"))
        self.assertEqual(owner.status().first_failure_reason, first)
        owner.start("B")
        self.assertIsNone(owner.status().first_failure_reason)
        owner.stop()

    def test_factory_exception_retains_unknown_partial_ownership(self):
        calls = []

        def partial_factory(context):
            calls.append(context)
            raise RuntimeError("partial allocation unproved")

        owner = lifecycle.NativeOwnerLifecycle(partial_factory)
        status = owner.start("A")
        self.assertEqual(status.state, "stopping")
        self.assertTrue(status.owned)
        self.assertTrue(status.cleanup_unknown)
        self.assertFalse(status.creating)
        self.assertIn("partial allocation unproved", status.first_failure_reason)
        self.assertTrue(owner.stop(0).owned)
        self.assertEqual(owner.start("B").session, "A")
        self.assertEqual(len(calls), 1)

    def test_close_false_or_exception_is_unknown_and_never_repeated(self):
        for error in (None, OSError("close failed")):
            with self.subTest(error=error):
                owner, _, resources = self.fixture()
                owner.start("A")
                resource = resources[0]
                resource.closed = False
                resource.close_error = error
                status = owner.stop()
                self.assertTrue(status.owned)
                self.assertTrue(status.cleanup_unknown)
                self.assertFalse(status.cleanup_active)
                self.assertIsNotNone(status.first_failure_reason)
                owner.stop()
                owner.start("B")
                self.assertEqual(len(resources), 1)
                self.assertEqual((resource.stop_calls, resource.join_calls,
                                  resource.close_calls), (1, 1, 1))

    def test_join_exception_is_visible_and_later_exact_join_can_reconcile(self):
        owner, _, resources = self.fixture()
        owner.start("A")
        resources[0].join_error = RuntimeError("join failed")
        self.assertTrue(owner.stop().owned)
        self.assertEqual(owner.status().first_failure_reason, "RuntimeError: join failed")
        self.assertEqual(resources[0].close_calls, 0)
        resources[0].join_error = None
        status = owner.stop()
        self.assertFalse(status.owned)
        self.assertEqual(status.state, "failed")
        self.assertEqual(resources[0].close_calls, 1)

    def test_stop_hook_error_still_attempts_join_and_close(self):
        owner, _, resources = self.fixture()
        owner.start("A")
        resources[0].stop_error = OSError("stop signal failed")
        stopped = owner.stop()
        self.assertEqual(stopped.state, "failed")
        self.assertFalse(stopped.owned)
        self.assertEqual(stopped.first_failure_reason, "OSError: stop signal failed")
        self.assertEqual((resources[0].stop_calls, resources[0].join_calls,
                          resources[0].close_calls), (1, 1, 1))

    def test_retained_record_is_deep_immutable_and_stop_preserves_it(self):
        owner, contexts, _ = self.fixture()
        owner.start("A")
        diagnostic, evidence = {"values": [1, 2]}, {"opaque_source_label": "unverified"}
        self.assertTrue(contexts[0].publish(diagnostic, evidence))
        record = owner.status().latest
        diagnostic["values"].append(3)
        evidence["opaque_source_label"] = "changed"
        self.assertEqual(json.loads(record.diagnostic_json), {"values": [1, 2]})
        self.assertEqual(json.loads(record.evidence_json),
                         {"opaque_source_label": "unverified"})
        with self.assertRaises(FrozenInstanceError):
            record.sequence = 9
        with self.assertRaises(FrozenInstanceError):
            owner.status().runtime_authority = True
        self.assertIsInstance(owner.evidence(), tuple)
        owner.stop()
        self.assertIs(owner.status().latest, record)
        self.assertTrue(owner.status().latest_stale)
        self.assertEqual(owner.evidence(), (record,))

    def test_count_and_logical_byte_retention_caps_evict_explicitly(self):
        owner, contexts, _ = self.fixture(max_records=2, max_bytes=100,
                                          max_record_bytes=90)
        owner.start("A")
        for index in range(3):
            self.assertTrue(contexts[0].publish({"index": index}))
        self.assertEqual([r.sequence for r in owner.evidence()], [2, 3])
        self.assertEqual(owner.status().evicted_records, 1)
        owner.stop()
        owner, contexts, _ = self.fixture(max_records=10, max_bytes=50,
                                          max_record_bytes=50)
        owner.start("A")
        for _ in range(2):
            self.assertTrue(contexts[0].publish({"v": "x" * 20}))
        status = owner.status()
        self.assertEqual(status.retained_records, 1)
        self.assertLessEqual(status.retained_bytes, 50)
        self.assertEqual(status.evicted_records, 1)
        owner.stop()

    def test_invalid_or_oversized_record_does_not_replace_latest(self):
        owner, contexts, _ = self.fixture(max_bytes=128, max_record_bytes=64)
        owner.start("A")
        contexts[0].publish({"valid": True})
        latest = owner.status().latest
        recursive = []
        recursive.append(recursive)
        invalid = [float("nan"), {1: "bad key"}, (1, 2), recursive,
                   "x" * 65, {"runtime_authority": True},
                   {"nested": {"motor_command_sent": 0}}, 1 << 257]
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(ValueError):
                    contexts[0].publish(value)
                self.assertIs(owner.status().latest, latest)
        with self.assertRaisesRegex(ValueError, "diagnostic_record_capacity"):
            contexts[0].publish("x" * 35, "y" * 35)
        self.assertIs(owner.status().latest, latest)
        self.assertTrue(contexts[0].publish({"runtime_authority": False}))
        owner.stop()

    def test_serialization_uses_validated_snapshot_not_later_caller_mutations(self):
        owner, contexts, _ = self.fixture()
        owner.start("A")
        payload = {"runtime_authority": False, "values": [1]}
        encode = json.JSONEncoder.iterencode

        def caller_changes(encoder, snapshot, *args, **kwargs):
            payload["runtime_authority"] = True
            payload["values"].append(2)
            return encode(encoder, snapshot, *args, **kwargs)

        with patch.object(json.JSONEncoder, "iterencode", caller_changes):
            self.assertTrue(contexts[0].publish(payload))
        self.assertEqual(json.loads(owner.status().latest.diagnostic_json),
                         {"runtime_authority": False, "values": [1]})
        owner.stop()

    def test_old_generation_cannot_publish_or_fail_even_with_reused_session(self):
        owner, contexts, _ = self.fixture()
        owner.start("same-label")
        old = contexts[0]
        old.publish({"old": True})
        owner.stop()
        owner.start("same-label")
        current = contexts[1]
        self.assertEqual(current.generation, old.generation + 1)
        self.assertTrue(owner.status().latest_stale)
        self.assertTrue(current.publish({"current": True}))
        latest = owner.status().latest
        self.assertFalse(old.publish({"late": True}))
        self.assertFalse(old.fail("late fault"))
        self.assertIs(owner.status().latest, latest)
        self.assertIsNone(owner.status().first_failure_reason)
        self.assertEqual(owner.status().rejected_publications, 1)
        self.assertFalse(owner.status().latest_stale)
        owner.stop()

    def test_stop_during_serialization_rechecks_generation_before_commit(self):
        owner, contexts, _ = self.fixture()
        owner.start("A")
        entered, release = threading.Event(), threading.Event()
        serialize = lifecycle._json_bytes

        def slow_encode(value, cap):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("fake encode release missing")
            return serialize(value, cap)

        with patch.object(lifecycle, "_json_bytes", slow_encode):
            pending = self.launch(lambda: contexts[0].publish({"old": True}))
            try:
                self.assertTrue(entered.wait(1))
                owner.stop()
                owner.start("B")
                self.assertFalse(contexts[1].publish({"contended": True}))
                release.set()
                self.assertFalse(self.joined(pending))
                self.assertIsNone(owner.status().latest)
            finally:
                release.set()
                pending[0].join(2)
        self.assertTrue(contexts[1].publish({"current": True}))
        self.assertEqual(owner.status().latest.session, "B")
        self.assertEqual(owner.status().rejected_publications, 2)
        owner.stop()

    def test_cancelled_worker_wrapper_never_runs_injected_work(self):
        owner, contexts, _ = self.fixture()
        owner.start("A")
        owner.stop()
        calls = []
        self.assertTrue(contexts[0].run(lambda current: calls.append(current)))
        self.assertEqual(calls, [])

    def test_exact_hook_results_and_input_bounds_are_required(self):
        owner, _, resources = self.fixture()
        owner.start("A")
        resources[0].joined = 1  # truthy is not proven True
        self.assertTrue(owner.stop().owned)
        self.assertEqual(resources[0].close_calls, 0)
        resources[0].joined = True
        resources[0].closed = 1
        self.assertTrue(owner.stop().cleanup_unknown)
        for timeout in (True, -1, 61, float("inf"), float("nan"), "1"):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    owner.stop(timeout)
        for session in ("", " leading", "x" * 129, 1):
            with self.assertRaises(ValueError):
                owner.start(session)
        with self.assertRaises(ValueError):
            lifecycle.NativeOwnerLifecycle(None)
        with self.assertRaises(ValueError):
            lifecycle.NativeOwnerLifecycle(lambda context: None, max_records=True)
        with self.assertRaises(ValueError):
            lifecycle.NativeOwnerLifecycle(lambda context: None,
                                           max_bytes=8388609)


class HostileTimeoutTests(unittest.TestCase):
    def test_large_integer_timeout_rejects_without_creating_or_stopping_owner(self):
        calls=[]
        owner=lifecycle.NativeOwnerLifecycle(lambda context:calls.append(context))
        before=owner.status()
        for value in (10**400,-10**400,float('inf'),float('nan'),True):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(ValueError,'bounded_timeout_required'):
                    owner.stop(value)
                self.assertEqual(owner.status(),before)
        self.assertEqual(calls,[])


if __name__ == "__main__":
    unittest.main()
