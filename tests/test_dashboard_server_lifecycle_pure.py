"""Compile actual server lifecycle source with generated base; no I/O/threads."""
import ast
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dashboard_server.autonomy_observation import AutonomyObservation


SOURCE = Path(__file__).resolve().parents[1] / "dashboard_server/app.py"


def load_server(base):
    tree = ast.parse(SOURCE.read_bytes())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DashboardServer")
    scope = {"ThreadingHTTPServer": base, "threading": threading}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                 "actual_dashboard_server_class", "exec"), scope)
    return scope["DashboardServer"]


class Base:
    def __init__(self, address, handler, bind_and_activate=True):
        self.initial_bound_resources = (self.autonomy_supervisor, self.autonomy_observation,
                                        self.native_observation_client, self.autonomy_observe_lock)
        self.base_closes = 0

    def server_close(self):
        self.base_closes += 1


class LifecycleTest(unittest.TestCase):
    def test_direct_constructor_has_real_instance_lock_and_closes_without_observer(self):
        cls = load_server(Base)
        server = cls(("generated", 0), object)
        with server.autonomy_observe_lock:
            self.assertIsNone(server.autonomy_observation)
        server.server_close()
        self.assertEqual(server.base_closes, 1)

    def test_instances_do_not_adopt_handler_class_stale_ownership(self):
        cls = load_server(Base)
        handler = SimpleNamespace(autonomy_observation=object(), autonomy_supervisor=object())
        first, second = cls(("generated", 0), handler), cls(("generated", 0), handler)
        self.assertIsNot(first.autonomy_observe_lock, second.autonomy_observe_lock)
        self.assertIsNone(first.autonomy_supervisor)
        self.assertIsNone(second.autonomy_observation)
        first.server_close(); second.server_close()

    def test_bound_resources_exist_before_base_constructor_and_cleanup_is_ordered(self):
        events = []
        supervisor = SimpleNamespace(close=lambda: (events.append("supervisor"), {"loop_joined": True})[1])
        observer = SimpleNamespace(stop=lambda **kw: events.append(("observer", kw["reason"])))
        native = SimpleNamespace(stop=lambda: events.append("native"))
        lock = threading.RLock()
        server = load_server(Base)(("generated", 0), object,
            autonomy_supervisor=supervisor, autonomy_observation=observer,
            native_observation_client=native, autonomy_observe_lock=lock)
        self.assertEqual(server.initial_bound_resources, (supervisor, observer, native, lock))
        server.server_close()
        self.assertEqual(events, ["supervisor", ("observer", "dashboard_server_closed"), "native"])
        self.assertEqual(server.base_closes, 1)

    def test_base_bind_failure_still_reaches_original_bound_cleanup(self):
        error = OSError("generated_bind_failure")
        closed, captured = [], []
        class FailedBase(Base):
            def __init__(self, *args):
                super().__init__(*args)
                captured.append(self)
                self.server_close()  # stdlib TCPServer bind/activate failure path.
                raise error
        observer = SimpleNamespace(stop=lambda **_kw: closed.append("observer"))
        native = SimpleNamespace(stop=lambda: closed.append("native"))
        with self.assertRaises(OSError) as raised:
            load_server(FailedBase)(("generated", 0), object,
                autonomy_observation=observer, native_observation_client=native)
        self.assertIs(raised.exception, error)
        self.assertEqual(closed, ["observer", "native"])
        self.assertEqual(captured[0].base_closes, 1)

    def test_observer_stop_failure_still_stops_native_and_closes_base(self):
        error = ValueError("generated_observer_failure")
        observer = SimpleNamespace(stop=Mock(side_effect=error))
        native = SimpleNamespace(stop=Mock())
        server = load_server(Base)(("generated", 0), object,
            autonomy_observation=observer, native_observation_client=native)
        with self.assertRaises(ValueError) as raised:
            server.server_close()
        self.assertIs(raised.exception, error)
        native.stop.assert_called_once()
        self.assertEqual(server.base_closes, 1)

    def test_existing_unjoined_supervisor_rule_is_not_relaxed(self):
        observer = SimpleNamespace(stop=Mock())
        server = load_server(Base)(("generated", 0), object,
            autonomy_supervisor=SimpleNamespace(close=lambda: {"loop_joined": False}),
            autonomy_observation=observer)
        with self.assertRaisesRegex(RuntimeError, "autonomy_supervisor_cleanup_unproven"):
            server.server_close()
        observer.stop.assert_not_called()
        self.assertEqual(server.base_closes, 0)

    def test_original_observation_class_fences_then_joins_and_releases_worker(self):
        threads = []
        class GeneratedThread:
            def __init__(self, *, target, **_kwargs):
                self.target, self.alive, self.joins = target, False, 0
                threads.append(self)
            def start(self):
                self.alive = True
            def is_alive(self):
                return self.alive
            def join(self, _timeout):
                # The ORIGINAL observation stop implementation must inhibit
                # publication before joining the exact generated worker.
                if not self.target.__self__.context.cancelled:
                    raise AssertionError("observation_not_fenced_before_join")
                self.joins += 1
                self.alive = False
        planner = Mock()
        observer = AutonomyObservation(planner, thread_factory=GeneratedThread)
        observer.start(session="generated-server-cleanup")
        self.assertTrue(observer.status()["owned"])
        server = load_server(Base)(("generated", 0), object, autonomy_observation=observer)
        server.server_close()
        status = observer.status()
        self.assertFalse(status["owned"] or status["active"])
        self.assertTrue(status["resources_closed"])
        self.assertEqual(status["stop_reason"], "dashboard_server_closed")
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0].joins, 1)
        self.assertFalse(threads[0].is_alive())
        planner.assert_not_called()

    def test_create_server_passes_the_same_resources_before_bind_without_start(self):
        tree = ast.parse(SOURCE.read_bytes())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "create_server")
        live = SimpleNamespace(observation_input=lambda: None)
        observer = SimpleNamespace(stop=Mock(), start=Mock())
        supervisor = SimpleNamespace(close=lambda: {"loop_joined": True}, start=Mock())
        modules = {
            "dashboard_server.source_live_mapping": SimpleNamespace(SourceLiveMapping=Mock(return_value=live)),
            "dashboard_server.mapping_operations": SimpleNamespace(ManagedArchiveMapping=Mock(), MappingOperations=Mock()),
            "dashboard_server.autonomy_settings": SimpleNamespace(AutonomySettings=Mock()),
            "dashboard_server.autonomy_observation": SimpleNamespace(AutonomyObservation=Mock(return_value=observer)),
            "dashboard_server.record_based_autonomy": SimpleNamespace(RecordBasedAutonomy=Mock()),
            "dashboard_server.native_autonomy_application": SimpleNamespace(NativeAutonomyApplication=Mock(return_value=supervisor))
        }
        handler = SimpleNamespace()
        scope = {"DashboardHandler": handler, "TelemetryProvider": Mock(return_value=SimpleNamespace(recording_archive_dir="generated")),
                 "ROOT": Path("GENERATED_ONLY"), "os": SimpleNamespace(environ={}), "threading": threading,
                 "dashboard_log": Mock(), "DashboardServer": load_server(Base), "ThreadingHTTPServer": Base,
                 "SESSION_TTL_S": 3600}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                     "actual_create_server_function", "exec"), scope)
        with patch.dict(sys.modules, modules):
            server = scope["create_server"]("generated", 0)
        self.assertIs(server.initial_bound_resources[0], supervisor)
        self.assertIs(server.initial_bound_resources[1], observer)
        self.assertIs(server.initial_bound_resources[3], handler.autonomy_observe_lock)
        self.assertIs(server.autonomy_observation, observer)
        observer.start.assert_not_called(); supervisor.start.assert_not_called()
        server.server_close()


if __name__ == "__main__":
    unittest.main()
