"""Pure injected stream resource tests; no child process, ROS, device or files."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import queue
import subprocess
import stat
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("stream_runner_tested", Path(__file__).with_name("stream_runner.py"))
runner_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner_module)


class FakePipe:
    def __init__(self):
        self.items = queue.Queue()
        self.remainder = b""
        self.close_calls = 0
        self.close_error = False

    def feed(self, raw):
        self.items.put(raw)

    def readline(self, limit):
        raw = self.remainder or self.items.get()
        if raw is None:
            return b""
        newline = raw.find(b"\n")
        end = min(limit, len(raw) if newline < 0 else newline + 1)
        self.remainder = raw[end:]
        return raw[:end]

    def read(self, limit):
        raw = self.remainder or self.items.get()
        if raw is None:
            return b""
        self.remainder = raw[limit:]
        return raw[:limit]

    def close(self):
        self.close_calls += 1
        if self.close_error:
            raise OSError("fake pipe close unproved")


class FakeProcess:
    def __init__(self, *, initial=True):
        self.pid = 1234
        self.stdout, self.stderr = FakePipe(), FakePipe()
        self.exit_code = None
        self.exited = threading.Event()
        self.terminate_calls = self.kill_calls = 0
        self.resist_terminate = self.resist_kill = False
        if initial:
            self.stdout.feed(b'{"kind":"begin"}\n')
            self.stdout.feed(b'{"kind":"ready"}\n')

    def poll(self):
        return self.exit_code

    def wait(self, timeout):
        if not self.exited.wait(timeout):
            raise subprocess.TimeoutExpired("fake exact process", timeout)
        return self.exit_code

    def exit(self, code=0):
        if self.exit_code is None:
            self.exit_code = code
            self.stdout.feed(None)
            self.stderr.feed(None)
            self.exited.set()

    def terminate(self):
        self.terminate_calls += 1
        if not self.resist_terminate:
            self.stdout.feed(b'{"kind":"done"}\n')
            self.exit(0)

    def kill(self):
        self.kill_calls += 1
        if not self.resist_kill:
            self.exit(-9)


class FakeAdapter:
    def __init__(self):
        self.bound = None
        self.ready = self.ended = self.stopped = False
        self.stop_calls = self.rows = self.samples = 0
        self.last_raw = None
        self.entered, self.release = threading.Event(), threading.Event()
        self.block = False

    def bind_process(self, pid, namespace):
        self.bound = (pid, namespace)

    def receive_line(self, raw):
        self.last_raw = raw
        self.rows += 1
        row = json.loads(raw)
        kind = row["kind"]
        if kind == "explode":
            raise ValueError("injected original protocol fault")
        if kind == "ready":
            self.ready = True
        if kind == "done":
            self.ended = True
        if kind != "sample" or self.stopped:
            return ()
        if self.block:
            self.entered.set()
            if not self.release.wait(2):
                raise RuntimeError("fake processing release missing")
        self.samples += 1
        return tuple(dict(diagnostic=dict(runtime_authority=False, motor_command_sent=False,
            autonomy_output_enabled=False, body_velocity_mps=None, state="HOLD",
            synthetic_value=row.get("value", 0)), evidence=dict(
                original_line_sha256=hashlib.sha256(raw).hexdigest(),
                original_callback_RAW_ns=row.get("raw_ns", 1),
                stage_timing={"synthetic": True, "stages_ns": {"fit": 1}}))
            for _ in range(row.get("pairs", 1)))

    def stop(self):
        self.stopped = True
        self.stop_calls += 1

    def finish(self, exit_code, operator_stop):
        ok = exit_code == 0 and operator_stop is True and self.ended and self.stopped
        return dict(ok=ok, owner_cleanup=ok, ready_observed=self.ready,
                    data_ready_before_stop=self.ready, error=None if ok else "incomplete fake protocol")

    def status(self):
        return dict(ready_observed=self.ready, ended=self.ended,
                    sample_rows=self.samples, line_rows=self.rows)


class BackpressureProcess(FakeProcess):
    """A bounded pipe writer cannot inspect SIGTERM during its blocked write."""
    def __init__(self):
        super().__init__(initial=False)
        self.stdout.items = queue.Queue(maxsize=2)
        self.release_burst, self.writes_done = threading.Event(), threading.Event()
        self.signal_stop, self.killed = threading.Event(), threading.Event()
        self.blocked_write = threading.Event()
        self.payload = b'{"kind":"sample","padding":"' + b"x" * 96000 + b'"}\n'
        self.writer = threading.Thread(target=self.produce, daemon=False)
        self.writer.start()

    def write_bounded(self, raw):
        while not self.killed.is_set():
            try:
                self.stdout.items.put(raw, timeout=.01)
                return True
            except queue.Full:
                self.blocked_write.set()
        return False

    def produce(self):
        for raw in (b'{"kind":"begin"}\n', b'{"kind":"ready"}\n',
                    b'{"kind":"explode"}\n'):
            if not self.write_bounded(raw):
                return
        self.release_burst.wait(2)
        for _ in range(64):
            if not self.write_bounded(self.payload):
                return
        self.writes_done.set()
        # Like the C++ callback, check the Stop flag only after writes return.
        self.signal_stop.wait(2)
        if not self.killed.is_set() and self.write_bounded(b'{"kind":"done"}\n'):
            self.exit(0)

    def terminate(self):
        self.terminate_calls += 1
        self.signal_stop.set()

    def kill(self):
        self.kill_calls += 1
        self.killed.set()
        self.exit_code = -9
        # Unblock the controlled fixture even if its reader has already failed.
        while True:
            try:
                self.stdout.items.get_nowait()
            except queue.Empty:
                break
        self.stdout.items.put_nowait(None)
        self.stderr.feed(None)
        self.exited.set()


class StreamRunnerTests(unittest.TestCase):
    def wait_for(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("fake stream condition timed out")
            time.sleep(.001)

    def fixture(self, **options):
        adapters, children = [], []

        def adapter_factory():
            adapter = FakeAdapter()
            adapters.append(adapter)
            return adapter

        def process_factory():
            child = FakeProcess()
            children.append(child)
            return child

        owner = runner_module.NativeStreamRunner(adapter_factory, process_factory,
                    identity=lambda: "time:[fake-owned]", **options)
        return owner, adapters, children

    def test_default_plan_and_constructor_are_inert(self):
        calls = []
        owner = runner_module.NativeStreamRunner(lambda: calls.append("adapter"),
                                                 lambda: calls.append("process"))
        self.assertEqual(owner.status()["owner"]["state"], "idle")
        self.assertEqual(calls, [])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(runner_module.main([]), 0)
        plan = json.loads(output.getvalue())
        self.assertFalse(plan["process_started"])
        self.assertFalse(plan["ROS_initialized"])
        self.assertFalse(plan["raw_rows_archived"])
        self.assertIsNone(plan["body_velocity_mps"])

    def test_exact_original_line_all_pairs_latest_retention_and_clean_stop(self):
        owner, adapters, children = self.fixture()
        try:
            owner.start("source-owner")
            self.wait_for(lambda: adapters[0].ready)
            self.assertEqual(adapters[0].bound, (1234, "time:[fake-owned]"))
            raw = b' {"kind":"sample", "value":2,"pairs":2,"raw_ns":16000000001}\n'
            children[0].stdout.feed(raw)
            self.wait_for(lambda: owner.status()["stream"]["publications"] == 2)
            self.assertIs(adapters[0].last_raw, raw)
            latest = owner.status()["owner"]["latest"]
            self.assertEqual(latest["evidence"]["original_callback_RAW_ns"], 16000000001)
            self.assertEqual(latest["evidence"]["original_line_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertIsNone(latest["diagnostic"]["body_velocity_mps"])
            self.assertEqual(latest["sequence"], 2)
            stopped = owner.stop(1)
            self.assertFalse(stopped.owned)
            self.assertEqual(stopped.state, "stopped")
            status = owner.status()
            self.assertEqual(status["owner"]["latest"], latest)
            self.assertTrue(status["owner"]["latest_stale"])
            self.assertTrue(status["stream"]["protocol"]["ok"])
            self.assertEqual(children[0].terminate_calls, 1)
            self.assertEqual((children[0].stdout.close_calls, children[0].stderr.close_calls), (1, 1))
            owner.stop(0)
            self.assertEqual(children[0].stdout.close_calls, 1)
        finally:
            owner.stop(1)

    def test_persistent_lifetime_exceeds_finite_15s_and_old_stdout_capture_cap(self):
        owner, _, children = self.fixture()
        try:
            owner.start("long-running")
            padding = "x" * 490000
            for index in range(70):
                raw = (json.dumps(dict(kind="sample", value=index,
                    raw_ns=20_000_000_000+index, padding=padding)) + "\n").encode()
                children[0].stdout.feed(raw)
                self.wait_for(lambda: owner.status()["stream"]["publications"] >= index + 1)
            status = owner.status()
            self.assertGreater(status["stream"]["native_stdout_bytes"], 32*1024*1024)
            self.assertIsNone(status["owner"]["first_failure_reason"])
            self.assertFalse(status["stream"]["raw_rows_archived"])
            self.assertEqual(status["stream"]["diagnostics"], 70)
            self.assertEqual(status["owner"]["latest"]["evidence"]["original_callback_RAW_ns"],
                             20_000_000_069)
        finally:
            self.assertFalse(owner.stop(1).owned)

    def test_stderr_rolls_at_64KiB_without_lifetime_capacity_failure(self):
        owner, _, children = self.fixture()
        try:
            owner.start()
            data = b"a" * (64*1024) + b"b" * 8192
            children[0].stderr.feed(data)
            self.wait_for(lambda: owner.status()["stream"]["native_stderr_bytes"] == len(data))
            status = owner.status()["stream"]
            self.assertEqual(status["stderr_retained_bytes"], 64*1024)
            self.assertEqual(status["stderr_tail"].encode(), data[-64*1024:])
            self.assertIsNone(status["first_error"])
        finally:
            owner.stop(1)

    def test_unexpected_EOF_latches_fault_and_requires_explicit_stop(self):
        owner, _, children = self.fixture()
        owner.start("A")
        self.wait_for(lambda: owner.status()["stream"]["native_lines_processed"] == 2)
        children[0].exit(0)
        self.wait_for(lambda: owner.status()["owner"]["first_failure_reason"] is not None)
        status = owner.status()["owner"]
        self.assertIn("native_stream_unexpected_EOF", status["first_failure_reason"])
        self.assertTrue(status["owned"])
        self.assertEqual(owner.start("B").generation, 1)
        first_reason = status["first_failure_reason"]
        stopped = owner.stop(1)
        self.assertFalse(stopped.owned)
        self.assertEqual(stopped.state, "failed")
        self.assertEqual(stopped.first_failure_reason, first_reason)

    def test_worker_protocol_exception_is_visible_and_holds_owner(self):
        owner, _, children = self.fixture()
        try:
            owner.start("A")
            children[0].stdout.feed(b'{"kind":"explode"}\n')
            self.wait_for(lambda: owner.status()["owner"]["first_failure_reason"] is not None)
            self.assertIn("injected original protocol fault", owner.status()["owner"]["first_failure_reason"])
            self.assertTrue(owner.start("B").owned)
            self.assertEqual(len(children), 1)
        finally:
            self.assertFalse(owner.stop(1).owned)

    def test_worker_failure_snapshots_current_adapter_timing_and_preserves_cause(self):
        for status_failure in (False, True):
            with self.subTest(status_failure=status_failure):
                owner, adapters, children = self.fixture()
                try:
                    owner.start("fault-current-status")
                    self.wait_for(lambda: adapters[0].ready)
                    original_status = adapters[0].status
                    timing = dict(stages_ns={'numeric_fit': 123}, failed_scalar_check='selected_lower_IMU')

                    def status_after_fault():
                        if adapters[0].last_raw == b'{"kind":"explode"}\n':
                            if status_failure:
                                raise ValueError('injected unavailable fault status')
                            return dict(original_status(), factory_failure_timing=timing,
                                        fault='original adapter fault')
                        return original_status()

                    adapters[0].status = status_after_fault
                    children[0].stdout.feed(b'{"kind":"explode"}\n')
                    self.wait_for(lambda: owner.resource.worker_done.is_set())
                    status = owner.status()['stream']
                    self.assertIn('injected original protocol fault', status['first_error'])
                    if status_failure:
                        self.assertIn('injected unavailable fault status', status['adapter_fault_status_error'])
                    else:
                        self.assertEqual(status['adapter_last_processed_row']['factory_failure_timing'], timing)
                        status['adapter_last_processed_row']['factory_failure_timing']['stages_ns']['numeric_fit'] = -1
                        self.assertEqual(owner.status()['stream']['adapter_last_processed_row']
                                         ['factory_failure_timing']['stages_ns']['numeric_fit'], 123)
                        self.assertIsNone(status['adapter_fault_status_error'])
                finally:
                    self.assertFalse(owner.stop(1).owned)
                self.assertIn('injected original protocol fault', owner.status()['stream']['first_error'])
                self.assertFalse(owner.status()['stream']['protocol']['ok'])

    def test_fault_drain_relieves_pipe_backpressure_without_qualifying_protocol(self):
        adapter, child = FakeAdapter(), BackpressureProcess()
        owner = runner_module.NativeStreamRunner(lambda: adapter, lambda: child,
                                                  identity=lambda: "time:[fake-owned]")
        try:
            owner.start("faulting-original-stream")
            self.wait_for(lambda: owner.status()["stream"]["first_error"] is not None)
            first_error = owner.status()["stream"]["first_error"]
            self.assertIn("injected original protocol fault", first_error)
            self.wait_for(lambda: owner.resource.worker_done.is_set())
            self.assertFalse(owner.resource.stdout_done.is_set())
            child.release_burst.set()
            # This models SIGTERM while an already-entered callback still has
            # >16 pipe writes to finish. Its flag alone cannot unblock writes.
            stopped = owner.stop(2)
            self.assertFalse(stopped.owned)
            self.assertEqual(stopped.state, "failed")
            child.writer.join(1)
            self.assertFalse(child.writer.is_alive())
            self.assertTrue(child.writes_done.is_set())
            self.assertEqual((child.poll(), child.terminate_calls, child.kill_calls), (0, 1, 0))
            final = owner.status()["stream"]
            self.assertEqual(final["first_error"], first_error)
            self.assertEqual(final["stdout_chunks_drained_after_failure"], 65)
            self.assertEqual(final["stdout_bytes_drained_after_failure"],
                             64 * len(child.payload) + len(b'{"kind":"done"}\n'))
            self.assertEqual((adapter.rows, final["diagnostics"], final["publications"]), (3, 0, 0))
            self.assertEqual(final["queue_depth"], 0)
            self.assertLessEqual(final["queue_peak"], runner_module.QUEUE_ROWS)
            self.assertTrue(final["readers_joined"])
            self.assertTrue(final["resources_closed"])
            self.assertFalse(final["protocol"]["ok"])
            self.assertFalse(final["protocol"]["protocol_qualified"])
            self.assertFalse(final["raw_rows_archived"])
            self.assertEqual((child.stdout.close_calls, child.stderr.close_calls), (1, 1))
        finally:
            child.release_burst.set()
            child.signal_stop.set()
            owner.stop(1)
            if child.writer.is_alive():
                child.kill()
            child.writer.join(1)

    def test_controlled_pipe_requires_drain_even_after_SIGTERM(self):
        child = BackpressureProcess()
        try:
            child.release_burst.set()
            self.assertTrue(child.blocked_write.wait(1))
            child.terminate()
            with self.assertRaises(subprocess.TimeoutExpired):
                child.wait(.02)
            # Only bounded reads relieve the blocked original write.
            chunks = 0
            while child.stdout.readline(runner_module.MAX_LINE + 1):
                chunks += 1
            self.assertEqual(chunks, 68)
            self.assertEqual(child.wait(1), 0)
            self.assertEqual(child.kill_calls, 0)
        finally:
            child.release_burst.set()
            child.signal_stop.set()
            if child.poll() is None:
                child.kill()
            child.writer.join(1)

    def test_oversized_line_drains_bounded_fragments_then_discards_following_rows(self):
        owner, adapters, children = self.fixture()
        try:
            owner.start("oversized-original-stream")
            self.wait_for(lambda: adapters[0].ready)
            raw = b"x" * (3 * runner_module.MAX_LINE + 17) + b"\n"
            children[0].stdout.feed(raw)
            self.wait_for(lambda: owner.status()["stream"]["stdout_bytes_drained_after_failure"] == len(raw))
            children[0].stdout.feed(b'{"kind":"sample"}\n')
            self.wait_for(lambda: owner.status()["stream"]["stdout_chunks_drained_after_failure"] == 5)
            self.assertEqual(adapters[0].rows, 2)
            self.assertEqual(owner.status()["stream"]["queue_depth"], 0)
            self.assertFalse(owner.stop(1).owned)
            final = owner.status()["stream"]
            self.assertIn("line_capacity_or_unterminated", final["first_error"])
            self.assertEqual(final["stdout_chunks_drained_after_failure"], 6)
            self.assertFalse(final["protocol"]["protocol_qualified"])
            self.assertEqual(children[0].kill_calls, 0)
        finally:
            owner.stop(1)

    def test_fault_latch_inhibits_inflight_result_before_lifecycle_cancel_dispatch(self):
        owner, adapters, children = self.fixture()
        owner.start("fault-publication-window")
        self.wait_for(lambda: adapters[0].ready)
        adapters[0].block = True
        children[0].stdout.feed(b'{"kind":"sample"}\n')
        self.assertTrue(adapters[0].entered.wait(1))
        resource = owner.resource
        fault_entered, fault_release = threading.Event(), threading.Event()
        context_type = type(resource.context)
        original_fail = context_type.fail

        def delayed_fail(context, error):
            fault_entered.set()
            if not fault_release.wait(2):
                raise RuntimeError("fake fault dispatch release missing")
            return original_fail(context, error)

        try:
            with patch.object(context_type, "fail", delayed_fail):
                children[0].stdout.feed(b"x" * (runner_module.MAX_LINE + 1))
                self.assertTrue(fault_entered.wait(1))
                self.assertTrue(resource.admission_failed.is_set())
                self.assertFalse(resource.context.cancelled)
                adapters[0].release.set()
                self.wait_for(lambda: resource.worker_done.is_set())
                self.assertEqual(resource.publications, 0)
                self.assertIsNone(owner.status()["owner"]["latest"])
                fault_release.set()
                self.wait_for(lambda: resource.context.cancelled)
        finally:
            fault_release.set()
            adapters[0].release.set()
            self.assertFalse(owner.stop(1).owned)

    def test_queue_overflow_and_oversized_line_fail_closed(self):
        for oversized in (False, True):
            with self.subTest(oversized=oversized):
                owner, adapters, children = self.fixture()
                try:
                    owner.start("A")
                    self.wait_for(lambda: adapters[0].ready)
                    if oversized:
                        children[0].stdout.feed(b"x" * (runner_module.MAX_LINE+1))
                    else:
                        adapters[0].block = True
                        children[0].stdout.feed(b'{"kind":"sample"}\n')
                        self.assertTrue(adapters[0].entered.wait(1))
                        for _ in range(17):
                            children[0].stdout.feed(b'{"kind":"sample"}\n')
                    self.wait_for(lambda: owner.status()["owner"]["first_failure_reason"] is not None)
                    status = owner.status()
                    expected = "line_capacity_or_unterminated" if oversized else "processing_capacity_exceeded"
                    self.assertIn(expected, status["owner"]["first_failure_reason"])
                    self.assertTrue(status["owner"]["owned"])
                    self.assertLessEqual(status["stream"]["queue_peak"], 16)
                    self.assertEqual(len(children), 1)
                finally:
                    adapters[0].release.set()
                    owner.stop(1)
                self.assertEqual(owner.status()["stream"]["queue_depth"], 0)
                if not oversized:
                    final = owner.status()["stream"]
                    self.assertEqual(final["native_lines_received"], final["native_lines_processed"] +
                                     final["queued_rows_discarded_after_cleanup"] +
                                     final["queued_rows_discarded_after_failure"] +
                                     final["stdout_chunks_drained_after_failure"])

    def test_stop_inhibits_late_diagnostic_before_worker_join(self):
        owner, adapters, children = self.fixture()
        owner.start("A")
        self.wait_for(lambda: adapters[0].ready)
        adapters[0].block = True
        children[0].stdout.feed(b'{"kind":"sample"}\n')
        self.assertTrue(adapters[0].entered.wait(1))
        pending = owner.stop(.01)
        self.assertTrue(pending.owned)
        self.assertEqual(children[0].stdout.close_calls, 0)
        self.assertEqual(owner.start("B").generation, 1)
        adapters[0].release.set()
        self.wait_for(lambda: owner.resource.worker_done.is_set())
        self.assertIsNone(owner.status()["owner"]["latest"])
        self.assertEqual(owner.status()["stream"]["publications"], 0)
        self.assertFalse(owner.stop(1).owned)
        owner.start("B")
        self.assertEqual(owner.status()["owner"]["generation"], 2)
        self.assertEqual(children[0].terminate_calls, 1)
        owner.stop(1)

    def test_cancelled_before_stop_hook_dispatch_still_drains_original_DONE(self):
        owner, adapters, children = self.fixture()
        owner.start("A")
        self.wait_for(lambda: adapters[0].ready)
        entered, release = threading.Event(), threading.Event()
        resource = owner.resource

        def delayed_hook():
            entered.set()
            if not release.wait(2):
                raise RuntimeError("fake Stop hook release missing")
            resource.request_stop()

        owner.lifecycle._owner.resource = runner_module.ResourceHooks(
            delayed_hook, resource.join, resource.close)
        results = []
        stopping = threading.Thread(target=lambda: results.append(owner.stop(1)))
        stopping.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(resource.context.cancelled)
            self.assertTrue(resource.explicit_stop)
            # The original producer's final row must still be consumed even
            # when lifecycle cancellation precedes request_stop dispatch.
            children[0].stdout.feed(b'{"kind":"sample"}\n')
            self.wait_for(lambda: adapters[0].ended)
            self.assertEqual(resource.publications, 0)
            release.set()
            stopping.join(2)
            self.assertFalse(stopping.is_alive())
            self.assertFalse(results[0].owned)
            self.assertEqual(results[0].state, "stopped")
            self.assertTrue(owner.status()["stream"]["protocol"]["ok"])
        finally:
            release.set()
            stopping.join(2)
            owner.stop(1)

    def test_unresponsive_process_kill_is_bounded_and_protocol_unqualified(self):
        owner, _, children = self.fixture()
        owner.start()
        self.wait_for(lambda: owner.status()["stream"]["native_lines_processed"] == 2)
        children[0].resist_terminate = True
        stopped = owner.stop(.1)
        self.assertFalse(stopped.owned)
        self.assertEqual(children[0].kill_calls, 1)
        self.assertEqual(children[0].poll(), -9)
        self.assertEqual(stopped.state, "failed")
        self.assertIn("protocol_unqualified", stopped.first_failure_reason)

    def test_unjoined_process_does_not_close_pipes_or_allow_second_owner(self):
        owner, _, children = self.fixture()
        owner.start("A")
        children[0].resist_terminate = children[0].resist_kill = True
        pending = owner.stop(.01)
        self.assertTrue(pending.owned)
        self.assertEqual(children[0].stdout.close_calls, 0)
        self.assertEqual(children[0].stderr.close_calls, 0)
        self.assertEqual(owner.start("B").generation, 1)
        children[0].exit(0)
        self.assertFalse(owner.stop(1).owned)
        self.assertEqual(children[0].stdout.close_calls, 1)

    def test_stop_during_adapter_creation_prevents_spawn_and_reconciles_late_adapter(self):
        entered, release = threading.Event(), threading.Event()
        adapter, spawn_calls = FakeAdapter(), []

        def make_adapter():
            entered.set()
            if not release.wait(2):
                raise RuntimeError("fake adapter release missing")
            return adapter

        owner = runner_module.NativeStreamRunner(make_adapter,
                    lambda: spawn_calls.append(True), identity=lambda: "time:[fake]")
        result = []
        thread = threading.Thread(target=lambda: result.append(owner.start("A")))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(owner.stop(.01).owned)
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(spawn_calls, [])
            self.assertTrue(adapter.stopped)
            self.assertFalse(result[0].owned)
            self.assertEqual(result[0].state, "stopped")
        finally:
            release.set()
            thread.join(2)
            owner.stop(1)

    def test_process_factory_exception_retains_unknown_handle_ownership(self):
        adapter = FakeAdapter()
        calls = []

        def unknown_spawn():
            calls.append(True)
            raise RuntimeError("unknown partial Popen creation")

        owner = runner_module.NativeStreamRunner(lambda: adapter, unknown_spawn,
                                                 identity=lambda: "time:[fake]")
        status = owner.start("A")
        self.assertTrue(status.owned)
        self.assertIn("unknown partial Popen", status.first_failure_reason)
        self.assertTrue(owner.status()["stream"]["creation_unknown"])
        self.assertTrue(owner.stop(0).owned)
        self.assertEqual(owner.start("B").generation, 1)
        self.assertEqual(calls, [True])

    def test_stop_before_resource_factory_entry_is_bound_to_starting_generation(self):
        owner, adapters, children = self.fixture()
        entered, release = threading.Event(), threading.Event()
        factory = owner.lifecycle._factory

        def delayed_factory(context):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("fake lifecycle factory release missing")
            return factory(context)

        owner.lifecycle._factory = delayed_factory
        answers = []
        starting = threading.Thread(target=lambda: answers.append(owner.start("A")))
        starting.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertIsNone(owner.resource)
            self.assertTrue(owner.stop(.01).owned)
            release.set()
            starting.join(2)
            self.assertFalse(starting.is_alive())
            self.assertEqual(children, [])
            self.assertTrue(adapters[0].stopped)
            self.assertFalse(answers[0].owned)
            self.assertEqual(answers[0].state, "stopped")
        finally:
            release.set()
            starting.join(2)
            owner.stop(1)

    def test_concurrent_start_creates_only_one_exact_process(self):
        owner, _, children = self.fixture()
        barrier = threading.Barrier(3)
        answers = []

        def start():
            barrier.wait()
            answers.append(owner.start())

        threads = [threading.Thread(target=start) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(children), 1)
        self.assertEqual([answer.generation for answer in answers], [1, 1])
        self.assertFalse(owner.stop(1).owned)

    def test_partial_thread_start_retains_owned_handles_and_never_closes_unjoined_pipes(self):
        class UnknownThread:
            def __init__(self, **kwargs):
                pass

            def start(self):
                raise RuntimeError("unknown partial Thread.start")

            def join(self, timeout):
                raise RuntimeError("unjoined unknown thread")

        owner, _, children = self.fixture(thread_factory=UnknownThread)
        status = owner.start("A")
        self.assertTrue(status.owned)
        self.assertIn("unknown partial Thread.start", status.first_failure_reason)
        self.assertTrue(owner.stop(.01).owned)
        self.assertEqual(children[0].terminate_calls, 1)
        self.assertEqual(children[0].stdout.close_calls, 0)
        self.assertEqual(children[0].stderr.close_calls, 0)
        self.assertEqual(owner.start("B").generation, 1)
        self.assertEqual(len(children), 1)

    def test_pipe_close_failure_is_unknown_and_not_retried(self):
        owner, _, children = self.fixture()
        owner.start("A")
        self.wait_for(lambda: owner.status()["stream"]["native_lines_processed"] == 2)
        children[0].stdout.close_error = True
        status = owner.stop(1)
        self.assertTrue(status.owned)
        self.assertTrue(status.cleanup_unknown)
        self.assertIn("fake pipe close unproved", status.first_failure_reason)
        self.assertEqual((children[0].stdout.close_calls, children[0].stderr.close_calls), (1, 1))
        owner.stop(0)
        self.assertEqual(children[0].stdout.close_calls, 1)
        self.assertEqual(owner.start("B").generation, 1)

    def manifest_fixture(self, *, contract_changes=None, manifest_changes=None):
        flags = dict(runtime_authority=False, motor_command_sent=False,
                     autonomy_output_enabled=False, mission_admission=False)
        sources = {"stream_runner.py": b"runner", "stream_rows.py": b"rows",
                   "dashboard_server/native_owner_lifecycle.py": b"lifecycle",
                   "observer-src/actual_cdr_stream.cpp": b"CPP",
                   "observer-src/CMakeLists.txt": b"CMAKE"}
        contract = dict(flags, schema="ubts.actual.native_stream.observer_contract.v1",
            source_sha256={name: hashlib.sha256(sources[name]).hexdigest() for name in
                          ("observer-src/actual_cdr_stream.cpp", "observer-src/CMakeLists.txt")})
        contract.update(contract_changes or {})
        sources["OBSERVER_CONTRACT.json"] = json.dumps(contract).encode()
        files = {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}
        args = SimpleNamespace(observer_contract_sha256=files["OBSERVER_CONTRACT.json"],
                    observer_binary_sha256="a"*64, package_manifest_sha256="b"*64)
        manifest = dict(flags, schema="ubts.actual.native_stream.build.v1", files_sha256=files,
            observer_contract_sha256=args.observer_contract_sha256,
            observer_binary_sha256=args.observer_binary_sha256,
            physical_package_manifest_sha256=args.package_manifest_sha256)
        manifest.update(manifest_changes or {})
        raw = json.dumps(manifest).encode()
        base = Path("C:/pure-stream-bundle")
        sources["BUILD_PROVENANCE.json"] = raw
        data = {str(base / name): content for name, content in sources.items()}
        return base, args, data, hashlib.sha256(raw).hexdigest()

    def verify_virtual_manifest(self, fixture):
        base, args, data, digest = fixture
        with patch.object(Path, "resolve", lambda value, strict=False: value), \
             patch.object(runner_module, "read_regular", lambda path, cap=1024*1024: data[str(path)]), \
             patch.object(runner_module, "sha_file", lambda path, cap=32*1024*1024:
                          hashlib.sha256(data[str(path)]).hexdigest()):
            return runner_module.verify_stream_manifest(base / "BUILD_PROVENANCE.json", digest, args)

    def test_stream_manifest_binds_exact_runtime_sources_contract_and_three_digests(self):
        fixture = self.manifest_fixture()
        self.assertEqual(self.verify_virtual_manifest(fixture), fixture[0])
        base, args, data, digest = fixture
        data[str(base / "stream_rows.py")] = b"changed source"
        with self.assertRaisesRegex(ValueError, "stream_build_source_changed"):
            self.verify_virtual_manifest((base, args, data, digest))
        wrong_digest = self.manifest_fixture(manifest_changes={"observer_binary_sha256": "c"*64})
        with self.assertRaisesRegex(ValueError, "stream_build_digest_binding_required"):
            self.verify_virtual_manifest(wrong_digest)

    def test_contract_wrong_source_or_authority_and_noncanonical_closure_rejected(self):
        for fixture, expected in (
            (self.manifest_fixture(contract_changes={"source_sha256": {}}), "CPP_source_binding"),
            (self.manifest_fixture(contract_changes={"runtime_authority": True}), "HOLD_required"),
            (self.manifest_fixture(manifest_changes={"runtime_authority": True}), "authority_forbidden"),
            (self.manifest_fixture(manifest_changes={"files_sha256": {"../escape": "a"*64}}), "source_closure")):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    self.verify_virtual_manifest(fixture)

    def test_manifest_strict_JSON_rejects_duplicate_and_nonfinite_values(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'\xff'):
            with self.subTest(raw=raw):
                with self.assertRaises((ValueError, UnicodeError)):
                    runner_module.strict_json(raw)

    def test_bounded_regular_json_read_checks_identity_and_ignores_access_time(self):
        fields = dict(st_mode=stat.S_IFREG | 0o600, st_nlink=1, st_size=2,
                      st_dev=1, st_ino=2, st_mtime_ns=3, st_ctime_ns=4)

        class FakePath:
            def __init__(self, raw, changes=None):
                self.raw, self.calls = raw, 0
                self.after = dict(fields, st_atime_ns=999)
                self.after.update(changes or {})

            def lstat(self):
                self.calls += 1
                return SimpleNamespace(**(dict(fields, st_atime_ns=0) if self.calls == 1 else self.after))

            def open(self, mode):
                return io.BytesIO(self.raw)

        self.assertEqual(runner_module.read_regular(FakePath(b"{}"), 2), b"{}")
        with self.assertRaisesRegex(ValueError, "changed_during_read"):
            runner_module.read_regular(FakePath(b"{}", {"st_ino": 9}), 2)
        with self.assertRaisesRegex(ValueError, "changed_during_read"):
            runner_module.read_regular(FakePath(b"xxx"), 2)


if __name__ == "__main__":
    unittest.main()
