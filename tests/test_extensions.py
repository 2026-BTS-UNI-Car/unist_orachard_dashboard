"""Extension points of the dashboard (D-12): settings, extra routes, page filter and a server started with a
simulation module from a folder outside the repository. With nothing set the dashboard behaves as before."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from dashboard_server import extensions
from dashboard_server.extensions import DashboardSettings, RouteTable, SettingsError, page_filter
from dashboard_server.static_assets import StaticAssets

STATIC = Path(__file__).resolve().parents[1] / "static"


class SettingsTest(unittest.TestCase):
    def test_defaults_are_the_current_dashboard(self):
        settings = DashboardSettings.from_env({})
        self.assertEqual((settings.mode, settings.vehicle_kind), ("live", "differential"))
        self.assertEqual((settings.provider, settings.extra_static, settings.extension, settings.ignored), (None, None, None, ()))
        self.assertIsNone(page_filter(settings))
        self.assertIsNone(extensions.load_provider(settings, None))
        self.assertIsNone(extensions.load_extension(settings))
        self.assertEqual(len(extensions.routes_of(None)), 0)
        self.assertEqual(settings.public(), {"runtime_mode": "live", "vehicle_kind": "differential", "provider": None,
                                             "extension": None, "extra_static": False})

    def test_live_mode_ignores_the_simulation_variables(self):
        env = {"DASHBOARD_PROVIDER": "x.y:Z", "DASHBOARD_EXTENSION": "x.y", "DASHBOARD_EXTRA_STATIC": "/nowhere"}
        settings = DashboardSettings.from_env(env)
        self.assertEqual((settings.provider, settings.extension, settings.extra_static), (None, None, None))
        self.assertEqual(sorted(settings.ignored), sorted(env))
        self.assertIsNone(page_filter(settings))

    def test_bad_values_stop_the_start(self):
        for env in ({"DASHBOARD_MODE": "sim"}, {"DASHBOARD_VEHICLE": "steer"},
                    {"DASHBOARD_MODE": "simulation", "DASHBOARD_PROVIDER": "no_class"},
                    {"DASHBOARD_MODE": "simulation", "DASHBOARD_PROVIDER": "a.b:1x"},
                    {"DASHBOARD_MODE": "simulation", "DASHBOARD_EXTENSION": "a/b"},
                    {"DASHBOARD_MODE": "simulation", "DASHBOARD_EXTRA_STATIC": "/no/such/folder/here"}):
            with self.subTest(env=env), self.assertRaises(SettingsError):
                DashboardSettings.from_env(env)

    def test_vehicle_kind_is_read_in_both_modes(self):
        self.assertEqual(DashboardSettings.from_env({"DASHBOARD_VEHICLE": "front_steer"}).vehicle_kind, "front_steer")
        settings = DashboardSettings.from_env({"DASHBOARD_MODE": "simulation", "DASHBOARD_VEHICLE": "front_steer"})
        self.assertEqual((settings.mode, settings.vehicle_kind), ("simulation", "front_steer"))


class RouteTableTest(unittest.TestCase):
    def test_exact_prefix_and_head(self):
        a, b, c = (lambda *x: "a"), (lambda *x: "b"), (lambda *x: "c")
        table = RouteTable([("GET", "/api/sim/map", a), ("post", "/api/sim/*", b), ("GET", "/api/sim/map/*", c)])
        self.assertIs(table.find("GET", "/api/sim/map"), a)
        self.assertIs(table.find("HEAD", "/api/sim/map"), a)
        self.assertIs(table.find("GET", "/api/sim/map/history"), c)
        self.assertIs(table.find("POST", "/api/sim/map/start"), b)
        self.assertIsNone(table.find("GET", "/api/sim/other"))
        self.assertIsNone(table.find("DELETE", "/api/sim/map"))

    def test_bad_entries(self):
        for entry in (("PUT", "/x", len), ("GET", "x", len), ("GET", "/x", None)):
            with self.subTest(entry=entry), self.assertRaises(SettingsError):
                RouteTable([entry])


class PageFilterTest(unittest.TestCase):
    PAGE = '<html lang="ko" data-runtime-mode="live">\n<div hidden="" class="simulation-banner" role="status">S</div>'

    def test_simulation_and_vehicle(self):
        sim = page_filter(DashboardSettings(mode="simulation"))(self.PAGE)
        self.assertIn('data-runtime-mode="simulation"', sim)
        self.assertIn('<div class="simulation-banner"', sim)
        steer = page_filter(DashboardSettings(vehicle_kind="front_steer"))(self.PAGE)
        self.assertTrue(steer.startswith('<html data-vehicle-kind="front_steer" lang="ko" data-runtime-mode="live">'))

    def test_extension_filter_only_in_simulation(self):
        class Ext:
            @staticmethod
            def page_filter(text):
                return text + "<!--ext-->"
        self.assertIsNone(page_filter(DashboardSettings(), Ext))
        self.assertTrue(page_filter(DashboardSettings(mode="simulation"), Ext)(self.PAGE).endswith("<!--ext-->"))

    def test_static_assets_page_filter_round_trip(self):
        assets = StaticAssets(STATIC)
        before = assets.serve("index.html", None, False)
        assets.set_page_filter(page_filter(DashboardSettings(vehicle_kind="front_steer")))
        steered = assets.serve("index.html", None, False)
        self.assertIn(b'data-vehicle-kind="front_steer"', steered.body)
        self.assertNotEqual(before.version, steered.version)
        assets.set_page_filter(None)
        again = assets.serve("index.html", None, False)
        self.assertEqual((again.body, again.version), (before.body, before.version))
        # other files never pass through the page filter
        assets.set_page_filter(lambda text: "changed")
        self.assertEqual(assets.serve("app/02-shell.js", None, False).body, (STATIC / "app/02-shell.js").read_bytes())


try:
    from dashboard_server import app as app_module  # needs Linux (fcntl)
except ImportError:  # pragma: no cover - Windows
    app_module = None

SIM_MODULE = textwrap.dedent('''
    """A simulation module outside the dashboard repository (test fixture)."""
    import json
    from dashboard_server.telemetry import TelemetryProvider

    class Provider(TelemetryProvider):
        def health(self):
            return {"ok": True, "provider": "outside-sim"}

    def hello(handler, parsed):
        handler._send_json({"ok": True, "route": parsed.path})

    def health_takeover(handler, parsed):
        handler._send_json({"ok": True, "takeover": True})

    ROUTES = [("GET", "/api/sim/hello", hello), ("GET", "/api/sim/deep/*", hello), ("GET", "/api/health", health_takeover)]

    def page_filter(text):
        return text.replace("</body>", '<script src="/ext-static/sim-extra.js"></script></body>', 1)
''')


@unittest.skipIf(app_module is None, "the dashboard server imports only on Linux")
class ServerTest(unittest.TestCase):
    def setUp(self):
        handler = app_module.DashboardHandler
        self.saved = {name: getattr(handler, name, None)
                      for name in ("provider", "dashboard_settings", "extension_routes", "page_filter", "executor_client", "tuning_log")}
        self.tmp = tempfile.TemporaryDirectory(prefix="ubts-ext-")
        root = Path(self.tmp.name)
        (root / "outside_sim_ext.py").write_bytes(SIM_MODULE.encode("utf-8"))
        (root / "static").mkdir()
        (root / "static" / "sim-extra.js").write_bytes(b"window.SIM_EXTRA = 1;\n")
        sys.path.insert(0, str(root))
        self.root = root

    def tearDown(self):
        handler = app_module.DashboardHandler
        for name, value in self.saved.items():
            setattr(handler, name, value)
        app_module.STATIC_ASSETS.set_page_filter(None)
        sys.path.remove(str(self.root))
        sys.modules.pop("outside_sim_ext", None)
        self.tmp.cleanup()

    def serve(self, env):
        base = {key: value for key, value in os.environ.items() if not key.startswith("DASHBOARD_")}
        base.update(env, DASHBOARD_LIVE_MAP_WORKER="0")
        with mock.patch.dict(os.environ, base, clear=True):
            server = app_module.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        return server.server_address[1]

    def get(self, port, path):
        try:
            # the first page after a filter change versions every static file again: a slow, busy machine needs time
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, b""

    def sim_env(self, mode):
        return {"DASHBOARD_MODE": mode, "DASHBOARD_PROVIDER": "outside_sim_ext:Provider",
                "DASHBOARD_EXTENSION": "outside_sim_ext", "DASHBOARD_EXTRA_STATIC": str(self.root / "static")}

    def test_simulation_module_from_outside_the_repository(self):
        port = self.serve(self.sim_env("simulation"))
        status, body = self.get(port, "/api/sim/hello")
        self.assertEqual((status, json.loads(body)["route"]), (200, "/api/sim/hello"))
        self.assertEqual(json.loads(self.get(port, "/api/sim/deep/x")[1])["route"], "/api/sim/deep/x")
        self.assertEqual(self.get(port, "/ext-static/sim-extra.js"), (200, b"window.SIM_EXTRA = 1;\n"))
        self.assertEqual(self.get(port, "/ext-static/../outside_sim_ext.py")[0], 403)
        # the provider answers; an extension route never replaces a dashboard route
        self.assertEqual(json.loads(self.get(port, "/api/health")[1]), {"ok": True, "provider": "outside-sim"})
        page = self.get(port, "/rover-control")[1].decode("utf-8")
        self.assertIn('data-runtime-mode="simulation"', page)
        self.assertIn('<div class="simulation-banner"', page)
        self.assertIn('/ext-static/sim-extra.js', page)
        config = json.loads(self.get(port, "/api/dashboard/config")[1])
        self.assertEqual((config["runtime_mode"], config["provider"], config["extension"], config["extra_static"]),
                         ("simulation", "Provider", "outside_sim_ext", True))

    def test_live_mode_ignores_the_module_and_the_page_is_unchanged(self):
        reference = StaticAssets(STATIC).serve("index.html", None, False).body
        port = self.serve(self.sim_env("live"))
        self.assertEqual(self.get(port, "/api/sim/hello")[0], 404)
        self.assertEqual(self.get(port, "/ext-static/sim-extra.js")[0], 404)
        self.assertNotEqual(json.loads(self.get(port, "/api/health")[1]).get("provider"), "outside-sim")
        self.assertEqual(self.get(port, "/")[1], reference)
        config = json.loads(self.get(port, "/api/dashboard/config")[1])
        self.assertEqual((config["runtime_mode"], config["vehicle_kind"], config["provider"]), ("live", "differential", None))

    def test_front_steer_vehicle_marks_the_page(self):
        port = self.serve({"DASHBOARD_VEHICLE": "front_steer"})
        self.assertIn(b'<html data-vehicle-kind="front_steer" lang="ko"', self.get(port, "/")[1])
        self.assertEqual(json.loads(self.get(port, "/api/dashboard/config")[1])["vehicle_kind"], "front_steer")


if __name__ == "__main__":
    unittest.main()
