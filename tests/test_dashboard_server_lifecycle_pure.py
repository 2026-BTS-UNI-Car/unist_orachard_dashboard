"""Compile the actual server class and create_server() with generated stand-ins; no I/O, sockets or threads.

Since 2026-11-22 the server owns no autonomy worker: the old differential supervisor and observation bundle left the
repository (autonomy runs in the row executor, relayed by /api/executor/*). These checks keep it that way.
"""
import ast
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / "dashboard_server/app.py"
REMOVED_MODULES = ("autonomy_settings", "autonomy_observation", "record_based_autonomy", "native_autonomy_application",
                   "native_autonomy_supervisor", "native_observation_client", "native_owner_lifecycle", "field10_shadow",
                   "field10_shadow_preflight", "field_preflight", "encoderless_drive_preview", "_field10")


def load_server(base):
    tree = ast.parse(SOURCE.read_bytes())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DashboardServer")
    scope = {"ThreadingHTTPServer": base, "threading": threading}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                 "actual_dashboard_server_class", "exec"), scope)
    return scope["DashboardServer"]


class Base:
    def __init__(self, address, handler, bind_and_activate=True):
        self.address, self.handler = address, handler
        self.base_closes = 0

    def server_close(self):
        self.base_closes += 1


class LifecycleTest(unittest.TestCase):
    def test_server_keeps_daemon_threads_and_closes_once(self):
        cls = load_server(Base)
        self.assertTrue(cls.daemon_threads)
        self.assertTrue(cls.allow_reuse_address)
        server = cls(("generated", 0), object)
        server.server_close()
        self.assertEqual(server.base_closes, 1)

    def test_app_imports_no_removed_autonomy_module(self):
        tree = ast.parse(SOURCE.read_bytes())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.rsplit(".", 1)[-1])
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
        self.assertEqual(sorted(imported & set(REMOVED_MODULES)), [])
        text = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("/api/autonomy", text)

    def test_create_server_builds_the_server_without_starting_anything(self):
        tree = ast.parse(SOURCE.read_bytes())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "create_server")
        live = SimpleNamespace()
        modules = {
            "dashboard_server.extensions": SimpleNamespace(
                DashboardSettings=SimpleNamespace(from_env=lambda env: SimpleNamespace(ignored=())),
                load_extension=Mock(return_value=None), routes_of=Mock(return_value="routes"),
                page_filter=Mock(return_value=None), load_provider=Mock(return_value=None)),
            "dashboard_server.executor_proxy": SimpleNamespace(executor_from_env=Mock(return_value=None)),
            "dashboard_server.steer_tuning": SimpleNamespace(TuningLog=Mock()),
            "dashboard_server.drive_distance": SimpleNamespace(CorrectionStore=Mock(), MoveRunner=Mock()),
            "dashboard_server.rover_api": SimpleNamespace(request=Mock()),
            "dashboard_server.source_live_mapping": SimpleNamespace(SourceLiveMapping=Mock(return_value=live)),
            "dashboard_server.mapping_operations": SimpleNamespace(ManagedArchiveMapping=Mock(), MappingOperations=Mock()),
            "dashboard_server.run_recording": SimpleNamespace(RunRecordings=Mock()),
        }
        package = SimpleNamespace(**{name.rsplit(".", 1)[-1]: module for name, module in modules.items()})
        handler = SimpleNamespace()
        scope = {"DashboardHandler": handler,
                 "TelemetryProvider": Mock(return_value=SimpleNamespace(recording_archive_dir="generated")),
                 "ROOT": Path("GENERATED_ONLY"), "os": SimpleNamespace(environ={}), "threading": threading,
                 "dashboard_log": Mock(), "DashboardServer": load_server(Base), "ThreadingHTTPServer": Base,
                 "SESSION_TTL_S": 3600, "executor_status_raw": Mock(), "stop_recording_now": Mock()}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                     "actual_create_server_function", "exec"), scope)
        with patch.dict(sys.modules, {"dashboard_server": package, **modules}):
            server = scope["create_server"]("generated", 0)
        self.assertEqual(server.address, ("generated", 0))
        self.assertIs(server.handler, handler)
        self.assertIs(handler.live_mapping, live)
        for name in ("autonomy_supervisor", "autonomy_observation", "native_observation_client", "autonomy_settings"):
            self.assertFalse(hasattr(handler, name), name)
        modules["dashboard_server.run_recording"].RunRecordings.return_value.start_thread.assert_not_called()
        server.server_close()
        self.assertEqual(server.base_closes, 1)


if __name__ == "__main__":
    unittest.main()
