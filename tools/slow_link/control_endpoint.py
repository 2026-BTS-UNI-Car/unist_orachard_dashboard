#!/usr/bin/env python3
"""Recording stand-in for the rover control bridge. No serial port, no motor, no ROS.

It answers the dashboard's control requests the way scripts/rover_control_bridge.py
does and writes down what it receives:

* the real RoverControl class (scripts/rover_control_core.py of the source
  under test) decides ownership, tickets, sequence numbers and input expiry,
  with its own unchanged time limits;
* the request path mirrors the bridge: STOP and status are answered at once,
  every other request waits for the 20 ms control step (one request per step,
  0.4 s answer limit, 0.25 s request age limit, 8 waiting requests);
* the motor is replaced by RecordingMotor, which only appends to a log file.

The dashboard reaches it over 127.0.0.1 TCP through shim/sitecustomize.py
(the real bridge listens on a Unix socket; one JSON line in, one JSON line out).

  control_endpoint.py --app <exported source> --events <file.jsonl> --proof <file.json> [--port 0]
"""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import queue
import signal
import socketserver
import threading
import time
from pathlib import Path

# The numbers below are copied from scripts/rover_control_bridge.py; a unit test compares them with that file.
STEP_S = 0.02
ANSWER_LIMIT_S = 0.4
REQUEST_AGE_LIMIT_S = 0.25
PENDING_MAX = 8
CONNECTION_TIMEOUT_S = 0.4
LISTEN_BACKLOG = 8
STATUS_FILE_PERIOD_S = 0.5
EVENT_LOG_LIMIT_BYTES = 64 * 1024 * 1024


def load_control_core(app):
    source = Path(app) / "scripts/rover_control_core.py"
    spec = importlib.util.spec_from_file_location("slow_link_control_core", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, source


class RecordingMotor:
    """The motor interface the control core and bridge use; every call is only written down."""
    firmware = "UBTS_WHEEL_PWM_V2"
    error = None
    ready = True
    port = "bench:no-device"
    identity = "slow_link_bench_recording_motor"
    fd = 0

    def __init__(self, record):
        self.record = record
        self.outputs = {"left": 0, "right": 0, "enabled": False}

    @property
    def stopped(self):
        return self.outputs["left"] == 0 and self.outputs["right"] == 0

    def _note(self, op):
        self.record(dict(kind="motor", op=op, outputs=dict(self.outputs)))

    def poll(self):
        pass

    def hard_stop(self):
        self.outputs.update(left=0, right=0, enabled=False)
        self._note("hard_stop")

    def enable(self):
        self.outputs["enabled"] = True
        self._note("enable")

    def drive(self, left, right):
        self.outputs.update(left=left, right=right)
        self._note("drive")

    def release(self):
        self.outputs.update(left=0, right=0)
        self._note("release")


class Endpoint:
    """Same request handling as Bridge in scripts/rover_control_bridge.py."""

    def __init__(self, core_module, record):
        self.record = record
        self.motor = RecordingMotor(record)
        self.control = core_module.RoverControl(self.motor)
        self.control_error = core_module.ControlError
        self.pending = queue.Queue(maxsize=PENDING_MAX)
        self.emergency = threading.Event()
        self.snapshot = {"ok": True, "connected": False, "state": "starting"}
        self.last_reason = None

    def request(self, request):
        op = request.get("op")
        received = time.time()
        if op == "status":
            return dict(self.snapshot)
        if op == "stop":
            self.emergency.set()
            self.record(dict(kind="request", op="stop", t=received, reason=str(request.get("reason", ""))[:80], answer="stop_requested"))
            return {"ok": True, "state": "stop_requested"}
        future = concurrent.futures.Future()
        try:
            self.pending.put_nowait((time.monotonic(), request, future))
            result = future.result(timeout=ANSWER_LIMIT_S)
        except (queue.Full, concurrent.futures.TimeoutError):
            future.cancel()
            self.emergency.set()
            result = {"ok": False, "error": "control_busy_stopped"}
        self.record(dict(kind="request", op=op, t=received, seq=request.get("seq"), left=request.get("left"), right=request.get("right"),
                         ok=bool(result.get("ok")), error=result.get("error"), answered=time.time()))
        return result

    def step(self):
        if self.emergency.is_set():
            self.control.stop("operator_stop")
            while not self.pending.empty():
                _, _, future = self.pending.get_nowait()
                if not future.done():
                    future.set_result({"ok": False, "error": "stop_barrier"})
            self.emergency.clear()
        self.control.tick()
        self.motor.poll()
        self.control.tick()
        if not self.pending.empty():
            received, request, future = self.pending.get_nowait()
            if not future.cancelled():
                if time.monotonic() - received > REQUEST_AGE_LIMIT_S:
                    result = {"ok": False, "error": "request_expired"}
                else:
                    try:
                        result = self.control.dispatch(request)
                    except (self.control_error, ValueError) as exc:
                        result = {"ok": False, "error": str(exc)}
                if not future.done():
                    future.set_result(result)
        status = self.control.status()
        if status.get("reason") != self.last_reason:
            # Why the core last stopped or armed (operator_stop, input_expired, stale_input ...).
            self.last_reason = status.get("reason")
            self.record(dict(kind="state", reason=self.last_reason, armed=status.get("armed"), moving=status.get("moving")))
        self.snapshot = {**status, "updated_at": time.time(), "transport": "bench_recording", "serial_port": self.motor.port,
                         "serial_open": True, "mode": "manual", "device_identity": self.motor.identity}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(CONNECTION_TIMEOUT_S)
        try:
            line = self.rfile.readline(4097)
            if len(line) > 4096:
                raise ValueError("request_too_large")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("object_required")
            response = self.server.endpoint.request(request)
        except (ValueError, OSError) as exc:
            response = {"ok": False, "error": str(exc)}
        try:
            self.wfile.write(json.dumps(response, allow_nan=False).encode() + b"\n")
        except OSError:
            pass


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = LISTEN_BACKLOG


def serve(app, events_path, proof_path, port=0, status_file=None, stopped=None):
    core_module, core_source = load_control_core(app)
    stopped = stopped or threading.Event()
    lock = threading.Lock()
    written = [0]
    with open(events_path, "x", encoding="utf-8", newline="\n") as events:
        def record(row):
            row.setdefault("t", time.time())
            line = json.dumps(row, separators=(",", ":")) + "\n"
            with lock:
                if written[0] + len(line) > EVENT_LOG_LIMIT_BYTES:
                    return
                written[0] += len(line)
                events.write(line)
                events.flush()

        endpoint = Endpoint(core_module, record)
        server = Server(("127.0.0.1", port), Handler)
        server.endpoint = endpoint
        threading.Thread(target=server.serve_forever, daemon=True).start()
        control = endpoint.control
        proof = dict(schema="ubts.slow_link.control_endpoint", version=1, pid=os.getpid(), host="127.0.0.1",
                     port=server.server_address[1], recording_only=True, hardware_imports=False,
                     core_sha256=hashlib.sha256(core_source.read_bytes()).hexdigest(),
                     core_limits=dict(input_ttl_s=control.INPUT_TTL, ticket_ttl_s=control.TICKET_TTL, owner_ttl_s=control.OWNER_TTL),
                     bridge_limits=dict(step_s=STEP_S, answer_limit_s=ANSWER_LIMIT_S, request_age_limit_s=REQUEST_AGE_LIMIT_S,
                                        pending_max=PENDING_MAX, connection_timeout_s=CONNECTION_TIMEOUT_S))
        Path(proof_path).write_bytes((json.dumps(proof) + "\n").encode("utf-8"))
        published = 0.0
        try:
            while not stopped.wait(STEP_S):
                endpoint.step()
                if status_file and time.monotonic() - published >= STATUS_FILE_PERIOD_S:
                    tmp = Path(status_file).with_suffix(".tmp")
                    tmp.write_bytes(json.dumps(endpoint.snapshot).encode("utf-8"))
                    tmp.replace(status_file)
                    published = time.monotonic()
        finally:
            endpoint.control.stop("shutdown")
            server.shutdown()
            server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", required=True, help="exported dashboard source (for scripts/rover_control_core.py)")
    parser.add_argument("--events", required=True, help="new JSON-lines file of received requests and motor calls")
    parser.add_argument("--proof", required=True, help="JSON file written once the endpoint listens (pid, port, limits)")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--status-file", help="rover bridge status file the dashboard reads")
    args = parser.parse_args()
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    serve(args.app, args.events, args.proof, args.port, args.status_file, stopped)


if __name__ == "__main__":
    main()
