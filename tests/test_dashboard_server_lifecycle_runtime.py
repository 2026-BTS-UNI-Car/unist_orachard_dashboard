"""Root-only actual observer/thread and owned local server lifecycle checks."""
import os
import unittest
from unittest.mock import Mock


@unittest.skipUnless(os.environ.get("UBTS_TEST_DASHBOARD_LIFECYCLE") == "1",
                     "Root must explicitly enable owned server/thread checks")
class ServerLifecycleRuntimeTest(unittest.TestCase):
    def observer(self):
        from dashboard_server.autonomy_observation import AutonomyObservation
        planner = Mock(side_effect=AssertionError("no sensor input/planning in lifecycle fixture"))
        observer = AutonomyObservation(planner)
        observer.start(session="generated-server-lifecycle")
        self.addCleanup(lambda: observer.stop(reason="generated_fixture_cleanup"))
        self.assertTrue(observer.resource.thread.is_alive())
        return observer, planner, observer.resource.thread

    def test_server_close_joins_original_observation_worker_and_closes_socket(self):
        from dashboard_server.app import DashboardHandler, DashboardServer
        observer, planner, worker = self.observer()
        server = DashboardServer(("127.0.0.1", 0), DashboardHandler,
                                 autonomy_observation=observer)
        try:
            server.server_close()
            self.assertFalse(worker.is_alive())
            self.assertFalse(observer.status()["owned"])
            self.assertTrue(observer.status()["resources_closed"])
            self.assertEqual(server.socket.fileno(), -1)
            planner.assert_not_called()
        finally:
            server.server_close()

    def test_bind_failure_keeps_original_error_and_stops_bound_observation(self):
        from dashboard_server.app import DashboardHandler, DashboardServer
        first = DashboardServer(("127.0.0.1", 0), DashboardHandler)
        observer, planner, worker = self.observer()
        try:
            with self.assertRaises(OSError):
                DashboardServer(first.server_address, DashboardHandler,
                                autonomy_observation=observer)
            self.assertFalse(worker.is_alive())
            self.assertFalse(observer.status()["owned"])
            self.assertTrue(observer.status()["resources_closed"])
            self.assertNotEqual(first.socket.fileno(), -1, "unrelated original server remains owned and open")
            planner.assert_not_called()
        finally:
            first.server_close()


if __name__ == "__main__":
    unittest.main()
