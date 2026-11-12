"""The recording control endpoint: same limits as the product, no device, everything written down."""
import json
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO = TOOL_DIR.parents[1]
sys.path.insert(0, str(TOOL_DIR))

import control_endpoint  # noqa: E402

NEWLINE = b"\x0a"


def ask(port, request, timeout=2.0):
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as client:
        client.sendall(json.dumps(request).encode() + NEWLINE)
        data = b""
        while NEWLINE not in data:
            part = client.recv(4096)
            if not part:
                break
            data += part
    return json.loads(data)


class LimitsTest(unittest.TestCase):
    def test_bridge_numbers_equal_the_product_bridge(self):
        source = (REPO / "scripts/rover_control_bridge.py").read_text(encoding="utf-8")

        def number(pattern):
            found = re.search(pattern, source)
            self.assertIsNotNone(found, pattern)
            return float(found.group(1))
        self.assertEqual(number(r"stopped\.wait\(([0-9.]+)\)"), control_endpoint.STEP_S)
        self.assertEqual(number(r"future\.result\(timeout=([0-9.]+)\)"), control_endpoint.ANSWER_LIMIT_S)
        self.assertEqual(number(r"time\.monotonic\(\) - received > ([0-9.]+)"), control_endpoint.REQUEST_AGE_LIMIT_S)
        self.assertEqual(number(r"queue\.Queue\(maxsize=([0-9]+)\)"), control_endpoint.PENDING_MAX)
        self.assertEqual(number(r"self\.connection\.settimeout\(([0-9.]+)\)"), control_endpoint.CONNECTION_TIMEOUT_S)
        self.assertEqual(number(r"request_queue_size = ([0-9]+)"), control_endpoint.LISTEN_BACKLOG)
        self.assertEqual(number(r"time\.monotonic\(\) - published >= ([0-9.]+)"), control_endpoint.STATUS_FILE_PERIOD_S)

    def test_control_core_is_the_product_class_with_its_own_limits(self):
        module, source = control_endpoint.load_control_core(REPO)
        self.assertEqual(source, REPO / "scripts/rover_control_core.py")
        endpoint = control_endpoint.Endpoint(module, lambda row: None)
        self.assertIs(type(endpoint.control), module.RoverControl)
        # The bench sets no limit on the instance: the class values are what runs.
        for name in ("INPUT_TTL", "TICKET_TTL", "OWNER_TTL"):
            self.assertNotIn(name, vars(endpoint.control))
        self.assertEqual((endpoint.control.INPUT_TTL, endpoint.control.TICKET_TTL, endpoint.control.OWNER_TTL), (0.75, 1.5, 30.0))

    def test_no_device_module_is_used(self):
        text = (TOOL_DIR / "control_endpoint.py").read_text(encoding="utf-8")
        for name in ("serial", "termios", "fcntl", "rclpy"):
            self.assertIsNone(re.search(rf"^\s*(import|from)\s+{name}\b", text, re.M), name)
        self.assertIsNone(re.search(r"^\s*(import|from)\s+scripts\b", text, re.M))


class EndpointBehaviourTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        folder = Path(self.temp.name)
        self.events, self.proof, self.status = folder / "events.jsonl", folder / "proof.json", folder / "status.json"
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=control_endpoint.serve, args=(REPO, self.events, self.proof, 0, self.status, self.stopped), daemon=True)
        self.thread.start()
        deadline = time.time() + 5
        while not self.proof.is_file() and time.time() < deadline:
            time.sleep(0.02)
        self.port = json.loads(self.proof.read_text(encoding="utf-8"))["port"]

    def tearDown(self):
        self.stopped.set()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def rows(self):
        return [json.loads(line) for line in self.events.read_text(encoding="utf-8").splitlines()]

    def test_proof_names_a_recording_endpoint_on_loopback(self):
        proof = json.loads(self.proof.read_text(encoding="utf-8"))
        self.assertEqual((proof["host"], proof["recording_only"], proof["hardware_imports"]), ("127.0.0.1", True, False))
        self.assertEqual(proof["core_limits"], {"input_ttl_s": 0.75, "ticket_ttl_s": 1.5, "owner_ttl_s": 30.0})

    def test_arm_drive_and_stop_are_answered_and_recorded(self):
        armed = ask(self.port, {"op": "arm", "owner": "tester"})
        self.assertTrue(armed["ok"], armed)
        driven = ask(self.port, {"op": "drive", "owner": "tester", "lease": armed["lease"], "ticket": armed["ticket"], "seq": 1,
                                 "left": 30, "right": 30, "deadman": True})
        self.assertTrue(driven["ok"], driven)
        stopped = ask(self.port, {"op": "stop", "reason": "operator_stop"})
        self.assertEqual(stopped, {"ok": True, "state": "stop_requested"})
        time.sleep(0.7)
        status = ask(self.port, {"op": "status"})
        self.assertEqual((status["state"], status["armed"], status["reason"]), ("disarmed", False, "operator_stop"))
        rows = self.rows()
        self.assertEqual([row["op"] for row in rows if row["kind"] == "request"], ["arm", "drive", "stop"])
        drive = next(row for row in rows if row.get("op") == "drive" and row["kind"] == "request")
        self.assertEqual((drive["seq"], drive["left"], drive["right"], drive["ok"]), (1, 30, 30, True))
        motor = [row["op"] for row in rows if row["kind"] == "motor"]
        self.assertEqual(motor[:3], ["hard_stop", "enable", "drive"])
        self.assertEqual(motor[-1], "hard_stop")
        # The status file the dashboard reads is written like the product bridge writes it.
        self.assertEqual(json.loads(self.status.read_text(encoding="utf-8"))["transport"], "bench_recording")

    def test_input_expires_after_the_product_limit_without_any_help(self):
        armed = ask(self.port, {"op": "arm", "owner": "tester"})
        started = time.time()
        ask(self.port, {"op": "drive", "owner": "tester", "lease": armed["lease"], "ticket": armed["ticket"], "seq": 1,
                        "left": 20, "right": 20, "deadman": True})
        time.sleep(1.0)  # longer than the 0.75 s input limit, no further command
        status = ask(self.port, {"op": "status"})
        self.assertEqual((status["armed"], status["reason"]), (False, "input_expired"))
        expired = next(row for row in self.rows() if row["kind"] == "state" and row["reason"] == "input_expired")
        # The stop comes 0.75 s after the command was applied; allow the control step and scheduling on a busy host.
        self.assertGreater(expired["t"] - started, 0.74)
        self.assertLess(expired["t"] - started, 0.95)
        late = ask(self.port, {"op": "drive", "owner": "tester", "lease": armed["lease"], "ticket": armed["ticket"], "seq": 2,
                               "left": 20, "right": 20, "deadman": True})
        self.assertEqual((late["ok"], late["error"]), (False, "control_not_owned"))

    def test_unowned_command_is_refused(self):
        answer = ask(self.port, {"op": "drive", "owner": "nobody", "lease": "x", "ticket": "y", "seq": 1, "left": 10, "right": 10, "deadman": True})
        self.assertEqual((answer["ok"], answer["error"]), (False, "control_not_owned"))


if __name__ == "__main__":
    unittest.main()
