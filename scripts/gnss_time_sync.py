#!/usr/bin/env python3
"""Step the host clock to validated GNSS receiver UTC, only while idle.

Field boots without network restore the last saved wall clock (hours/days
stale). This service compares NAV-PVT receiver UTC with the driver's host
receipt time and steps CLOCK_REALTIME once, but only when

* the kernel reports the clock unsynchronized (STA_UNSYNC), so NTP is never
  overridden;
* at least MIN_EPOCHS distinct NAV-PVT epochs with valid/fully resolved UTC
  and tAcc <= 50 ms agree within MAX_SPREAD_S, without a host clock jump
  between them;
* the error is at least MIN_STEP_S; and
* nothing time-sensitive runs: recorder idle, rover neither armed nor moving
  (fresh status), no live/recorded mapping, no native observation owner.
  Gates are evaluated at decision time and again immediately before the step.

There is no slewing and no step during recording/control/mapping. Receiver
tAcc does not cover USB/driver transport latency (tens of ms, unmeasured).
Old recordings are never rewritten; see recording_clock.json for display.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
from pathlib import Path
import socket
import statistics
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dashboard_server.recording_clock import clock_epoch, clock_sample  # noqa: E402

SCHEMA = "ubts.gnss_time_sync.v1"
MIN_EPOCHS = 5
MAX_SPREAD_S = 0.1
MAX_HOST_JUMP_S = 0.25
WINDOW_S = 120.0
MIN_STEP_S = 2.0
MAX_STEP_S = 400 * 86400.0
RESIDUAL_OK_S = 0.5
STEP_COOLDOWN_S = 600.0
MAX_STEPS_PER_BOOT = 3
STA_UNSYNC = 0x0040


class Timex(ctypes.Structure):
    """glibc struct timex (Linux, LP64)."""
    _fields_ = [("modes", ctypes.c_uint), ("offset", ctypes.c_long), ("freq", ctypes.c_long),
                ("maxerror", ctypes.c_long), ("esterror", ctypes.c_long), ("status", ctypes.c_int),
                ("constant", ctypes.c_long), ("precision", ctypes.c_long), ("tolerance", ctypes.c_long),
                ("time_sec", ctypes.c_long), ("time_usec", ctypes.c_long), ("tick", ctypes.c_long),
                ("ppsfreq", ctypes.c_long), ("jitter", ctypes.c_long), ("shift", ctypes.c_int),
                ("stability", ctypes.c_long), ("jitcnt", ctypes.c_long), ("calcnt", ctypes.c_long),
                ("errcnt", ctypes.c_long), ("stbcnt", ctypes.c_long), ("tai", ctypes.c_int),
                ("_pad", ctypes.c_int * 11)]


def kernel_clock_state():
    """Read-only adjtimex: synchronized means NTP (or similar) disciplines it."""
    tx = Timex()
    libc = ctypes.CDLL(None, use_errno=True)
    state = libc.adjtimex(ctypes.byref(tx))
    if state < 0:
        return dict(available=False, errno=ctypes.get_errno())
    return dict(available=True, synchronized=not (tx.status & STA_UNSYNC), status=tx.status,
                state=state, maxerror_us=tx.maxerror)


class ClockEvidence:
    """Distinct NAV-PVT epochs: receiver UTC minus driver host receipt."""

    def __init__(self):
        self.rows = []  # (receiver_epoch, received_at, local_monotonic)
        self.rejected = {}

    def reset(self):
        self.rows.clear()

    def observe(self, sample, local_monotonic):
        if not isinstance(sample, dict) or sample.get("protocol") != "UBX_NAV_PVT":
            return False
        epoch = clock_epoch(clock_sample(sample))
        received = sample.get("received_at")
        if epoch is None or type(received) not in (int, float) or not math.isfinite(received):
            self.rejected["invalid_receiver_time"] = self.rejected.get("invalid_receiver_time", 0) + 1
            return False
        if self.rows and epoch <= self.rows[-1][0]:
            return False  # repeated/older epoch cannot renew evidence
        self.rows.append((epoch, float(received), float(local_monotonic)))
        self.rows = [row for row in self.rows if local_monotonic - row[2] <= WINDOW_S][-2 * MIN_EPOCHS:]
        return True

    def estimate(self, now_monotonic):
        rows = [row for row in self.rows if now_monotonic - row[2] <= WINDOW_S]
        if len(rows) < MIN_EPOCHS:
            return dict(ok=False, reason="insufficient_epochs", epochs=len(rows))
        for (e0, r0, m0), (e1, r1, m1) in zip(rows, rows[1:]):
            if abs((r1 - r0) - (m1 - m0)) > MAX_HOST_JUMP_S:
                return dict(ok=False, reason="host_clock_jump_inside_window", epochs=len(rows))
            if abs((e1 - e0) - (r1 - r0)) > MAX_SPREAD_S:
                return dict(ok=False, reason="receiver_host_rate_mismatch", epochs=len(rows))
        offsets = [e - r for e, r, _ in rows]
        spread = max(offsets) - min(offsets)
        if spread > MAX_SPREAD_S:
            return dict(ok=False, reason="offset_spread_too_large", epochs=len(rows), spread_s=spread)
        return dict(ok=True, offset_s=statistics.median(offsets), spread_s=spread, epochs=len(rows),
                    newest_age_s=now_monotonic - rows[-1][2],
                    first_receiver_utc=rows[0][0], last_receiver_utc=rows[-1][0])


def _json_get(url, timeout=0.5):
    with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as response:
        return json.loads(response.read(256 * 1024))


def activity_gates(runtime, recorder_url, mapping_url, native_socket):
    """Every gate must be explicitly idle; unavailable status means not idle."""
    gates = {}
    try:
        status = _json_get(recorder_url)
        gates["recorder_idle"] = (status.get("state") == "idle" and not status.get("active")
                                  and not status.get("finalizing"))
    except (OSError, ValueError, TypeError):
        gates["recorder_idle"] = False
    try:
        rover = json.loads((Path(runtime) / "rover_bridge_status.json").read_text())
        fresh = isinstance(rover.get("updated_at"), (int, float)) and 0 <= time.time() - rover["updated_at"] <= 3
        gates["rover_inactive"] = bool(fresh and not rover.get("armed") and not rover.get("moving"))
    except (OSError, ValueError, TypeError):
        gates["rover_inactive"] = False
    try:
        mapping = _json_get(mapping_url)
        live = next((job for job in mapping.get("jobs", []) if job.get("id") == "live"), None)
        gates["mapping_idle"] = bool(mapping.get("ok") and live is not None and isinstance(live.get("tracking"), bool)
                                     and not live["tracking"] and not live.get("active") and not live.get("processing")
                                     and not any(job.get("processing") for job in mapping.get("jobs", [])))
    except (OSError, ValueError, TypeError, AttributeError):
        gates["mapping_idle"] = False
    gates["native_owner_idle"] = native_owner_idle(native_socket)
    return gates


def native_owner_idle(path):
    path = Path(path)
    if not path.exists():
        return True  # no service, no owner
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(1.0)
    try:
        client.connect(str(path))
    except ConnectionRefusedError:
        client.close()
        return True  # leftover socket, nobody listening
    except OSError:
        client.close()
        return False
    try:
        client.sendall(b'{"op":"status"}\n')
        data = b""
        while not data.endswith(b"\n") and len(data) < 512 * 1024:
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
        reply = json.loads(data)
        owner = (reply.get("status") or {}).get("owner") or {}
        return reply.get("ok") is True and owner.get("owned") is False
    except (OSError, ValueError, TypeError):
        return False
    finally:
        client.close()


class Decider:
    """Pure decision state; the caller supplies clock/kernel/gates and stepping."""

    def __init__(self, mode, boot_id):
        self.mode = mode
        self.boot_id = boot_id
        self.steps = 0
        self.last_step_monotonic = None
        self.pending_verify = None

    def decide(self, estimate, kernel, gates, now_monotonic):
        if self.pending_verify is not None and estimate.get("ok"):
            residual = estimate["offset_s"]
            verified = abs(residual) <= RESIDUAL_OK_S
            self.pending_verify = None
            return dict(action="verified" if verified else "residual_after_step", residual_s=residual)
        if not kernel.get("available"):
            return dict(action="hold", reason="kernel_clock_state_unavailable")
        if kernel.get("synchronized"):
            return dict(action="none", reason="kernel_clock_synchronized")
        if not estimate.get("ok"):
            return dict(action="hold", reason=estimate.get("reason"))
        offset = estimate["offset_s"]
        if abs(offset) < MIN_STEP_S:
            return dict(action="none", reason="within_tolerance", offset_s=offset)
        if abs(offset) > MAX_STEP_S:
            return dict(action="hold", reason="offset_out_of_bounds", offset_s=offset)
        if self.mode != "step":
            return dict(action="observe_only", reason="mode_observe", offset_s=offset)
        if self.steps >= MAX_STEPS_PER_BOOT:
            return dict(action="hold", reason="step_limit_reached", offset_s=offset)
        if self.last_step_monotonic is not None and now_monotonic - self.last_step_monotonic < STEP_COOLDOWN_S:
            return dict(action="hold", reason="step_cooldown", offset_s=offset)
        closed = sorted(name for name, ok in gates.items() if not ok)
        if closed:
            return dict(action="defer", reason="activity_gates_closed", closed_gates=closed, offset_s=offset)
        return dict(action="step", offset_s=offset)

    def stepped(self, now_monotonic):
        self.steps += 1
        self.last_step_monotonic = now_monotonic
        self.pending_verify = True


def step_clock(offset):
    before = time.clock_gettime(time.CLOCK_REALTIME)
    time.clock_settime(time.CLOCK_REALTIME, before + offset)
    after = time.clock_gettime(time.CLOCK_REALTIME)
    return dict(host_before=before, host_after=after, applied_offset_s=offset)


class Output:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.last_event_key = None

    def status(self, value):
        temp = self.root / f".status.{uuid.uuid4().hex}.tmp"
        temp.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
        temp.replace(self.root / "status.json")

    def event(self, value, key):
        if key == self.last_event_key and value.get("action") not in ("step", "verified", "residual_after_step"):
            return
        self.last_event_key = key
        path = self.root / "events.jsonl"
        if path.exists() and path.stat().st_size > 1024 * 1024:
            path.replace(self.root / "events.1.jsonl")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("observe", "step"), default=os.environ.get("UBTS_GNSS_TIME_SYNC_MODE", "observe"))
    parser.add_argument("--runtime", default=os.environ.get("UBTS_RUNTIME_DIR", "/app/runtime"))
    parser.add_argument("--recorder-url", default="http://127.0.0.1:8790/status")
    parser.add_argument("--mapping-url", default="http://127.0.0.1:8080/api/live/mapping-jobs")
    parser.add_argument("--native-socket", default="/run/ubts-native-stream/observe.sock")
    parser.add_argument("--period-s", type=float, default=2.0)
    args = parser.parse_args(argv)

    import rclpy
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String

    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    evidence = ClockEvidence()
    decider = Decider(args.mode, boot_id)
    out = Output(Path(args.runtime) / "clock_sync")

    def on_gps(msg):
        try:
            evidence.observe(json.loads(msg.data), time.monotonic())
        except (ValueError, TypeError):
            pass

    def tick():
        now = time.monotonic()
        estimate = evidence.estimate(now)
        kernel = kernel_clock_state()
        gates = activity_gates(args.runtime, args.recorder_url, args.mapping_url, args.native_socket) \
            if estimate.get("ok") and not kernel.get("synchronized") else {}
        decision = decider.decide(estimate, kernel, gates, now)
        record = dict(schema=SCHEMA, at=time.time(), monotonic=now, boot_id=boot_id, mode=args.mode,
                      kernel=kernel, estimate=estimate, gates=gates, **decision,
                      transport_latency_measured=False)
        if decision["action"] == "step":
            # Re-check every gate immediately before changing the clock.
            gates = activity_gates(args.runtime, args.recorder_url, args.mapping_url, args.native_socket)
            closed = sorted(name for name, ok in gates.items() if not ok)
            if closed or kernel_clock_state().get("synchronized"):
                record.update(action="defer", reason="recheck_failed", closed_gates=closed, gates=gates)
            else:
                record["step"] = step_clock(decision["offset_s"])
                decider.stepped(time.monotonic())
                evidence.reset()  # receipts before the step are on the old clock
        out.event(record, (record["action"], record.get("reason"), tuple(record.get("closed_gates", ()))))
        out.status(dict(record, steps_this_boot=decider.steps))

    rclpy.init()
    node = Node("ubts_gnss_time_sync")
    qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=10, reliability=ReliabilityPolicy.BEST_EFFORT,
                     durability=DurabilityPolicy.VOLATILE)
    node.create_subscription(String, "/gps/data", on_gps, qos)
    node.create_timer(args.period_s, tick)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
