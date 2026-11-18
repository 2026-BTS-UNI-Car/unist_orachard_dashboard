#!/usr/bin/env python3
"""Control bridge for the ESP32-S3 'ABS-HOLD-8-REBOUND-LIMITS' firmware (ROVER_ESP_KIND=esp32_abs_hold).

One process owns the USB serial port (by-id path, udev identity, flock, TIOCEXCL; scripts/esp32_abs_hold.py) and
serves the dashboard's control socket with the same protocol as the other bridges (one JSON object line of at most
4096 B per connection, one JSON answer line, 0.4 s read timeout, socket mode 0660, a process lock next to the socket,
so the V2 bridge and this one can never run together):
  status | stop (no lease; the next loop step sends STOP) | arm {vehicle_profile: "front_steer"} |
  drive {lease, ticket, seq, deadman: true, throttle -100..100, steer_mrad (left +)} | keepalive | release
The ownership and timing rules are SteerControl / RoverControl (scripts/steer_control_core.py, unchanged): lease,
one-use ticket, seq + 1, deadman, input deadline 0.75 s, ticket 1.5 s, idle owner 30 s, no automatic re-arm.

Mapping to the firmware (esp32_abs_hold.Esp32SteerMotor):
  steer_mrad (left +)  -> 'ANGLE -deg' (the firmware's + is RIGHT), rounded to 1 command degree, at most every 0.5 s,
                          staged by counts; on the SMOOTH=1 firmware rounded to 0.25 degree, at most every 0.1 s,
                          never staged (scripts/esp32_abs_hold.py, 'Smooth hold')
  throttle             -> '2 F/R pwm' + '3 F/R pwm' (pwm = (|t| * 255 + 50) // 100, host cap), or '2 S' / '3 S'
  stop / disarm / input expiry / lease loss / shutdown / serial error / MCU reset -> 'STOP' (all three motors)
  calibrate {action: find_left | find_right | set_center | apply | default | read, left_margin_counts?,
             right_margin_counts? (int 10..120, default 30), pulse_ms? (find_left / find_right only, int 40..100,
             SMOOTH firmware: 'CAL FIND LEFT 60'; default the firmware's 100 ms)}
             -> only disarmed, no lease, not moving; STOP, then the firmware's CAL lines (needs the FW token
             CAL_RUNTIME=1, else 'calibration_needs_firmware_update'); progress and results in status
             'calibration' (and so in the status file)
The firmware has no drive timeout, so the host owns it: the drive runs only inside the drive TTL, every stop is
written again until acknowledged and zero is refreshed every second. SIGTERM, SIGINT, SIGHUP and atexit send STOP
before the port closes. If this process is killed (SIGKILL) or hangs while the board stays powered, the last drive
command keeps running: the firmware drive watchdog proposal (runs/esp32_bridge_20261116) closes that gap.

Control events (2026-11-18, field: "the Arm keeps turning off"): every arm, disarm (with the stop reason: input_expired,
stale_input, page_status_stale, page_hidden, device_unavailable, pot_invalid, ...), board link change and not-ready
reason change is kept (last CONTROL_EVENTS_KEEP in memory, the last 10 in status 'control_events', identical events at
most once per CONTROL_EVENT_REPEAT_S) and appended to ROVER_CONTROL_EVENT_LOG (default rover_control_events.jsonl next
to the status file, the last CONTROL_LOG_KEEP lines kept). The owner (a dashboard session) is never written.

ROS (optional, lazy import): /rover/status, /rover/control/requested, /rover/control/applied (2 Hz) and
/rover/steering/state (each STATUS line). The status file (ROVER_BRIDGE_STATUS_FILE) is written every 0.5 s.
Environment (defaults in brackets): ROVER_SERIAL_PORT [/dev/ubts-motor], ROVER_ESP_IDENTITY (udev ID_SERIAL, required),
ROVER_CONTROL_SOCKET [/run/ubts-rover/control.sock], ROVER_BRIDGE_STATUS_FILE, ROVER_ESP32_LEFT_FORWARD [F],
ROVER_ESP32_RIGHT_FORWARD [F], ROVER_ESP32_DIRECTION_VERIFIED [0], ROVER_ESP32_MAX_DRIVE_PWM [255],
ROVER_ESP32_DRIVE_TTL_S [0.75], ROVER_ESP32_STATUS_PERIOD_S [0.1], ROVER_ESP32_ANGLE_MIN_INTERVAL_S [0.5],
ROVER_ESP32_ANGLE_QUANTUM_DEG [1.0] (the last two for the pulse hold; the SMOOTH=1 firmware uses 0.1 s and 0.25 deg).
Run from the dashboard root: python3 -m scripts.esp32_steer_bridge --help
"""
import argparse
import atexit
import concurrent.futures
import json
import os
import queue
import signal
import socketserver
import sys
import threading
import time

from scripts import esp32_abs_hold as esp
from scripts.rover_control_core import ControlError
from scripts.steer_control_core import SteerControl

LOOP_PERIOD_S = 0.02
PUBLISH_PERIOD_S = 0.5
REQUEST_TIMEOUT_S = 0.4
REQUEST_EXPIRY_S = 0.25
MAX_REQUEST_BYTES = 4096
DEFAULT_SOCKET = '/run/ubts-rover/control.sock'
DEFAULT_DEVICE = '/dev/ubts-motor'
CONTROL_EVENTS_KEEP = 50
CONTROL_EVENT_REPEAT_S = 2.0
CONTROL_LOG_KEEP = 1000
STATE_SCHEMA = 'ubts.esp32_steer_state.v1'


class Esp32Bridge:
    """Request queue and loop step (the same rules as the V2 Bridge: a queue of 8, request expiry 0.25 s, a busy
    queue stops, status and stop bypass the queue, one request per step, a snapshot after each step)."""

    def __init__(self, motor, clock=time.monotonic, wall_clock=time.time):
        self.motor = motor
        self.clock = clock
        self.wall_clock = wall_clock
        self.control = SteerControl(motor, clock, steer_limit_left_mrad=esp.LIMIT_MRAD,
                                    steer_limit_right_mrad=esp.LIMIT_MRAD, status_extra=self._status_extra)
        self.pending = queue.Queue(maxsize=8)
        self.emergency = threading.Event()
        self.emergency_reason = 'operator_stop'
        self.internal_errors = 0
        self.control_events = []        # dicts, newest last (CONTROL_EVENTS_KEEP)
        self.events_unwritten = []      # not yet appended to the event log file
        self._event_state = None
        self._event_last = {}           # (event, reason) -> monotonic time (repeat limit)
        self.events_suppressed = 0
        self.snapshot = {'ok': True, 'connected': False, 'state': 'starting', 'armed': False,
                         'vehicle_profile': 'front_steer', 'esp_kind': esp.ESP_KIND, 'updated_at': wall_clock()}

    # socket threads
    def request(self, request, timeout=REQUEST_TIMEOUT_S):
        op = request.get('op')
        if op == 'status':
            return dict(self.snapshot)
        if op == 'stop':
            reason = request.get('reason')
            self.emergency_reason = str(reason)[:80] if isinstance(reason, str) and reason else 'operator_stop'
            self.emergency.set()
            return {'ok': True, 'state': 'stop_requested'}
        future = concurrent.futures.Future()
        try:
            self.pending.put_nowait((self.clock(), request, future))
            return future.result(timeout=timeout)
        except (queue.Full, concurrent.futures.TimeoutError):
            future.cancel()
            self.emergency_reason = 'control_busy_stopped'
            self.emergency.set()
            return {'ok': False, 'error': 'control_busy_stopped'}

    # loop thread
    def step(self):
        try:
            self._step()
        except Exception as exc:          # a defect: stop, keep the loop (no automatic re-arm)
            self.internal_errors += 1
            print('esp32_steer_bridge: step error: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
            try:
                self.motor._latch('internal_error', type(exc).__name__)
                self.control.stop('internal_error')
            except Exception:
                pass

    def _step(self):
        if self.emergency.is_set():
            self.emergency.clear()
            self.control.stop(self.emergency_reason)
            while not self.pending.empty():
                _, _, future = self.pending.get_nowait()
                if not future.done():
                    future.set_result({'ok': False, 'error': 'stop_barrier'})
        self.control.tick()
        self.motor.poll()
        reason = self.motor.take_stop_reason()
        if reason and (self.control.lease or self.control.moving):
            self.control.stop(reason)
        self.control.tick()
        if not self.pending.empty():
            received, request, future = self.pending.get_nowait()
            if not future.cancelled():
                if self.clock() - received > REQUEST_EXPIRY_S:
                    result = {'ok': False, 'error': 'request_expired'}
                elif request.get('op') == 'calibrate':
                    result = self._calibrate(request)
                else:
                    try:
                        result = self.control.dispatch(request)
                    except (ControlError, ValueError) as exc:
                        result = {'ok': False, 'error': str(exc)}
                    # A write of this request failed (serial error) or the firmware asked for a stop: stop now and
                    # do not answer ok.
                    reason = self.motor.take_stop_reason()
                    if reason:
                        if self.control.lease or self.control.moving:
                            self.control.stop(reason)
                        result = {'ok': False, 'error': reason}
                if not future.done():
                    future.set_result(result)
        status = self.control.status()
        self._track_events(status)
        self.snapshot = {**status, 'updated_at': self.wall_clock(), 'mode': 'manual',
                         'internal_errors': self.internal_errors,
                         'control_events': self.control_events[-10:], 'control_events_suppressed': self.events_suppressed}

    def _track_events(self, status):
        """Arm / disarm (with why), board link and not-ready changes as control events."""
        host = status.get('steering_host') if isinstance(status.get('steering_host'), dict) else {}
        state = {'armed': bool(status.get('armed')), 'reason': status.get('reason'), 'link': host.get('link'),
                 'not_ready': host.get('not_ready')}
        before, self._event_state = self._event_state, state
        if before is None:
            self._control_event('bridge_start', state, None)
            return
        if state['armed'] and not before['armed']:
            self._control_event('armed', state, None)
        elif before['armed'] and not state['armed']:
            self._control_event('disarmed', state, state['reason'])
        if state['link'] != before['link']:
            detail = host.get('last_open_error') if state['link'] == 'closed' else None
            self._control_event('board_link', state, detail)
        elif state['not_ready'] != before['not_ready']:
            self._control_event('not_ready' if state['not_ready'] else 'ready', state, state['not_ready'])

    def _control_event(self, event, state, reason):
        now = self.clock()
        key = (event, reason, state.get('link'))
        last = self._event_last.get(key)
        if last is not None and now - last < CONTROL_EVENT_REPEAT_S:
            self.events_suppressed += 1
            return
        self._event_last[key] = now
        item = {'at': round(self.wall_clock(), 3), 'event': event, 'reason': reason, 'armed': state['armed'],
                'link': state['link'], 'not_ready': state['not_ready']}
        self.control_events.append(item)
        del self.control_events[:-CONTROL_EVENTS_KEEP]
        self.events_unwritten.append(item)
        del self.events_unwritten[:-CONTROL_LOG_KEEP]

    def take_unwritten_events(self):
        items, self.events_unwritten = self.events_unwritten, []
        return items

    def _calibration_refusal(self):
        """Why the control side refuses a calibration action now (the motor adds its own reasons), or None."""
        if self.control.lease:
            return 'calibration_needs_disarmed'      # armed (screen or executor): never while someone owns control
        if self.control.moving:
            return 'calibration_needs_standstill'
        return None

    def _calibrate(self, request):
        """op calibrate {action, left_margin_counts?, right_margin_counts?, pulse_ms?}: only disarmed, without a lease
        and not moving. The motor adapter sends STOP first and then only CAL lines (never a motor 2/3 line)."""
        self.control.tick()
        owner = request.get('owner')
        if not isinstance(owner, str) or not owner:
            return {'ok': False, 'error': 'operator_required'}
        action = request.get('action')
        if action not in esp.CAL_ACTIONS:
            return {'ok': False, 'error': 'invalid_calibration_action'}
        refusal = self._calibration_refusal()
        if refusal:
            return {'ok': False, 'error': refusal}
        margins = {}
        for key in ('left_margin_counts', 'right_margin_counts', 'pulse_ms', 'delta_counts'):
            if key in request:
                margins[key] = request[key]
        result = self.motor.start_calibration(action, **margins)   # the owner (a session) is never stored
        result['calibration'] = self.motor.calibration_block()
        return result

    def _status_extra(self):
        extra = self.motor.status_extra()
        calibration = extra.get('calibration')
        if isinstance(calibration, dict) and calibration.get('blocker') is None:
            calibration['blocker'] = self._calibration_refusal()
        return extra

    def steering_state(self):
        """/rover/steering/state payload of the latest STATUS line."""
        steering = self.snapshot.get('steering') or {}
        return {'schema': STATE_SCHEMA, 'stamp': self.wall_clock(), 'esp_kind': esp.ESP_KIND,
                'armed': self.snapshot.get('armed'), 'requested': self.snapshot.get('requested'),
                'steering': {k: steering.get(k) for k in ('measured_mrad', 'command_equiv_deg', 'raw', 'hold',
                                                          'hold_target_command_deg', 'age_ms', 'seq', 'faults')},
                'drive': self.snapshot.get('drive')}

    def shutdown(self, reason='shutdown'):
        try:
            self.control.stop(reason)
        except Exception:
            pass
        finally:
            self.motor.close(reason)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(0.4)
        try:
            line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if len(line) > MAX_REQUEST_BYTES:
                raise ValueError('request_too_large')
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError('object_required')
            response = self.server.bridge.request(request)
        except (ValueError, OSError) as exc:
            response = {'ok': False, 'error': str(exc)}
        try:
            self.wfile.write(json.dumps(response, allow_nan=False).encode() + b'\n')
        except (OSError, ValueError):
            pass


def _server_class():
    base = getattr(socketserver, 'ThreadingUnixStreamServer', None)
    if base is None:
        raise RuntimeError('Unix socket server not available on this platform')

    class Server(base):
        daemon_threads = True
        request_queue_size = 8
    return Server


class RosPublisher:
    def __init__(self, node_name='ubts_esp32_rover_bridge'):
        import rclpy
        from std_msgs.msg import String
        self.rclpy, self.String = rclpy, String
        rclpy.init()
        self.node = rclpy.create_node(node_name)
        self.pubs = {topic: self.node.create_publisher(String, topic, 1) for topic in (
            '/rover/status', '/rover/control/requested', '/rover/control/applied', '/rover/steering/state')}

    def publish(self, topic, payload):
        msg = self.String()
        msg.data = json.dumps(payload, allow_nan=False, default=str)
        self.pubs[topic].publish(msg)

    def close(self):
        try:
            self.node.destroy_node()
        finally:
            self.rclpy.shutdown()


def _env_float(name, default):
    value = os.environ.get(name)
    return float(value) if value not in (None, '') else default


def build_parser():
    p = argparse.ArgumentParser(prog='esp32_steer_bridge', description=__doc__.splitlines()[0])
    p.add_argument('--device', default=os.environ.get('ROVER_SERIAL_PORT', DEFAULT_DEVICE))
    p.add_argument('--identity', default=os.environ.get('ROVER_ESP_IDENTITY', ''),
                   help='udev ID_SERIAL of the ESP32 (required)')
    p.add_argument('--socket', default=os.environ.get('ROVER_CONTROL_SOCKET', DEFAULT_SOCKET))
    p.add_argument('--status-file', default=os.environ.get('ROVER_BRIDGE_STATUS_FILE'))
    p.add_argument('--event-log', default=os.environ.get('ROVER_CONTROL_EVENT_LOG'),
                   help='control event log (default: rover_control_events.jsonl next to the status file)')
    p.add_argument('--left-forward', choices=('F', 'R'), default=os.environ.get('ROVER_ESP32_LEFT_FORWARD', 'F'))
    p.add_argument('--right-forward', choices=('F', 'R'), default=os.environ.get('ROVER_ESP32_RIGHT_FORWARD', 'F'))
    p.add_argument('--direction-verified', action='store_true',
                   default=os.environ.get('ROVER_ESP32_DIRECTION_VERIFIED', '0') == '1')
    p.add_argument('--max-drive-pwm', type=int, default=int(os.environ.get('ROVER_ESP32_MAX_DRIVE_PWM', '255')))
    p.add_argument('--no-drive-ramp', dest='drive_ramp', action='store_false',
                   default=os.environ.get('ROVER_ESP32_DRIVE_RAMP', '1') != '0',
                   help='write drive steps at once (default: soft start and soft release)')
    p.add_argument('--drive-ttl-s', type=float, default=_env_float('ROVER_ESP32_DRIVE_TTL_S', esp.DRIVE_TTL_S))
    p.add_argument('--status-period-s', type=float,
                   default=_env_float('ROVER_ESP32_STATUS_PERIOD_S', esp.STATUS_PERIOD_S))
    p.add_argument('--angle-min-interval-s', type=float,
                   default=_env_float('ROVER_ESP32_ANGLE_MIN_INTERVAL_S', esp.ANGLE_MIN_INTERVAL_S))
    p.add_argument('--angle-quantum-deg', type=float,
                   default=_env_float('ROVER_ESP32_ANGLE_QUANTUM_DEG', esp.ANGLE_QUANTUM_DEG))
    p.add_argument('--udev-root', default='/run/udev/data', help='udev database (tests give a fake one)')
    p.add_argument('--boot-wait-s', type=float, default=esp.BOOT_WAIT_S, help=argparse.SUPPRESS)
    p.add_argument('--lock-dir', default='/run/lock')
    p.add_argument('--no-ros', dest='ros', action='store_false', default=True)
    p.add_argument('--max-runtime-s', type=float, default=None, help='stop after this many seconds (tests)')
    return p


class Runner:
    def __init__(self, args, transport=None, clock=time.monotonic):
        if not args.identity:
            raise ValueError('ROVER_ESP_IDENTITY (udev ID_SERIAL of the ESP32) is required')
        self.args = args
        self.transport = transport or esp.UsbSerialTransport(args.device, args.identity, udev_root=args.udev_root,
                                                             lock_dir=args.lock_dir)
        self.motor = esp.Esp32SteerMotor(
            self.transport, clock, left_forward=args.left_forward, right_forward=args.right_forward,
            max_drive_pwm=args.max_drive_pwm, drive_ttl_s=args.drive_ttl_s, status_period_s=args.status_period_s,
            angle_min_interval_s=args.angle_min_interval_s, angle_quantum_deg=args.angle_quantum_deg,
            direction_verified=args.direction_verified, boot_wait_s=args.boot_wait_s,
            drive_ramp=args.drive_ramp)
        self.bridge = Esp32Bridge(self.motor, clock)
        self.server = None
        self.lock = None
        self.ros = None
        self.closed = False
        self._last_seq = None

    def start(self):
        import fcntl
        path = self.args.socket
        directory = os.path.dirname(path) or '.'
        os.makedirs(directory, exist_ok=True)
        self.lock = open(os.path.join(directory, 'bridge.lock'), 'a')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)     # never delete a live peer's socket
        if os.path.exists(path):
            os.unlink(path)
        self.server = _server_class()(path, Handler)
        os.chmod(path, 0o660)
        self.server.bridge = self.bridge
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        if self.args.ros:
            try:
                self.ros = RosPublisher()
            except Exception as exc:       # no ROS in this image: the socket and the status file still work
                print('esp32_steer_bridge: ROS not available (%s); running without topics' % exc, file=sys.stderr)
                self.ros = None

    def publish(self):
        snapshot = self.bridge.snapshot
        if self.ros is not None:
            self.ros.publish('/rover/status', snapshot)
            self.ros.publish('/rover/control/requested', snapshot.get('requested', {}))
            self.ros.publish('/rover/control/applied', snapshot.get('outputs', {}))
        target = self.args.status_file
        if target:
            os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
            tmp = target + '.tmp'
            with open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(json.dumps(snapshot, allow_nan=False, default=str))
            os.replace(tmp, target)
        self.write_events()

    def event_log_path(self):
        if self.args.event_log:
            return self.args.event_log
        return os.path.join(os.path.dirname(self.args.status_file), 'rover_control_events.jsonl') \
            if self.args.status_file else None

    def write_events(self):
        """Append the new control events to the event log (the last CONTROL_LOG_KEEP lines kept)."""
        items = self.bridge.take_unwritten_events()
        path = self.event_log_path()
        if not items or not path:
            return
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        lines = []
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                lines = [line.rstrip('\n') for line in handle if line.strip()]
        except OSError:
            pass
        lines += [json.dumps(item, ensure_ascii=False, allow_nan=False, default=str) for item in items]
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write('\n'.join(lines[-CONTROL_LOG_KEEP:]) + '\n')
        os.replace(tmp, path)

    def publish_state(self):
        seq = self.motor.status_seq
        if self.ros is not None and seq != self._last_seq:
            self._last_seq = seq
            self.ros.publish('/rover/steering/state', self.bridge.steering_state())

    def run(self, stopped):
        deadline = None if self.args.max_runtime_s is None else time.monotonic() + self.args.max_runtime_s
        published = 0.0
        next_t = time.monotonic()
        while True:
            next_t += LOOP_PERIOD_S
            delay = next_t - time.monotonic()
            if delay < -LOOP_PERIOD_S:
                next_t = time.monotonic()
                delay = 0.0
            if stopped.wait(max(0.0, delay)):
                break
            self.bridge.step()
            try:
                self.publish_state()
                if time.monotonic() - published >= PUBLISH_PERIOD_S:
                    published = time.monotonic()
                    self.publish()
            except Exception as exc:      # a ROS or status-file defect never stops the control loop
                print('esp32_steer_bridge: publish error: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
            if deadline is not None and time.monotonic() >= deadline:
                break

    def shutdown(self, reason='shutdown'):
        if self.closed:
            return
        self.closed = True
        try:
            self.bridge.shutdown(reason)
            try:
                self.publish()
            except Exception:
                pass
        finally:
            if self.server is not None:
                self.server.shutdown()
                self.server.server_close()
                if os.path.exists(self.args.socket):
                    os.unlink(self.args.socket)
            if self.ros is not None:
                try:
                    self.ros.close()
                except Exception:
                    pass
            if self.lock is not None:
                self.lock.close()


def main(argv=None, transport=None):
    args = build_parser().parse_args(argv)
    try:
        runner = Runner(args, transport=transport)
    except (ValueError, OSError) as exc:
        print('esp32_steer_bridge: %s' % exc, file=sys.stderr)
        return 2
    stopped = threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, 'SIGHUP', None)):
            if sig is not None:
                signal.signal(sig, lambda *_: stopped.set())
    atexit.register(runner.shutdown, 'atexit')     # also on an unexpected exception in this thread
    try:
        runner.start()
    except (OSError, RuntimeError) as exc:         # e.g. another bridge holds the socket lock
        print('esp32_steer_bridge: %s' % exc, file=sys.stderr)
        runner.shutdown('start_failed')
        return 2
    try:
        runner.run(stopped)
    finally:
        runner.shutdown('shutdown')
    return 0


if __name__ == '__main__':
    sys.exit(main())
