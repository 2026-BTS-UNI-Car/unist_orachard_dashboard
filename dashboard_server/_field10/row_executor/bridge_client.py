"""Client for the existing ESP bridge socket (no change to the bridge, dashboard or firmware).

Protocol facts this client relies on (DASH = dashboard-live-raw, read-only reference):
  * /run/ubts-rover/control.sock, one JSON object per line, ONE request per connection: the
    bridge Handler reads a single line, answers it and closes (scripts/rover_control_bridge.py
    Handler.handle). Every call here opens a new connection.
  * status / stop never need a lease; op stop works for any client and clears the current lease
    (scripts/rover_control_core.py dispatch, Bridge.request emergency path).
  * arm {owner} -> {lease, ticket, seq=0}; refused with control_in_use while another client
    (e.g. dashboard manual driving) holds the lease, or verified_esp_v2_required when the ESP is
    not ready.
  * drive {owner, lease, ticket, seq=previous+1, deadman=true, left, right} -> new ticket
    (TTL 1.5 s). deadman MUST be true on EVERY drive, including 0/0: a drive without it is an
    invalid_drive (hard stop + lease removed). 0/0 keeps the lease: moving is False so the 0.75 s
    input TTL does not expire, and every drive renews the 30 s owner TTL. "Hold to run" is the
    executor's own operator signal and is NOT the bridge deadman field.
  * a non-zero drive must be renewed within 0.75 s or the bridge stops (input_expired) and
    removes the lease.

Client states: DISARMED -> arm() -> ARMED -> stop() -> DISARMED; any error -> LATCHED.
LATCHED on any error (transport error/timeout, malformed answer, ok:false such as
control_not_owned after somebody else's STOP, stale_input_rearm_required, invalid_sequence,
request_expired, control_busy_stopped, stop_barrier, a bridge status that is not ok). When the
client held a lease at that moment it sends a best-effort op stop, EXCEPT for the answers that
say the lease is already gone (LEASE_GONE_ERRORS: control_not_owned, stop_barrier): somebody
else's STOP (dashboard, observer phone, input expiry) already stopped the motors and removed our
lease, and the next lease may already belong to the human who armed manual driving on the
dashboard; an op stop from us would wipe that new lease (review S7). The client NEVER re-arms by
itself: arm() refuses while LATCHED until acknowledge_latch() is called from an explicit
operator action, and drive() never sends anything unless ARMED.

Forward-only by design: drive() refuses negative or non-integer duties (that is a bug upstream;
it latches and stops).

Recovery (field): see RECOVERY_PROCEDURE and recovery_hint(status).
"""
import json
import socket
import threading
import time
import uuid

FIRMWARE = 'UBTS_WHEEL_PWM_V2'
DISARMED, ARMED, LATCHED = 'DISARMED', 'ARMED', 'LATCHED'
MAX_REQUEST_BYTES = 4096        # the bridge Handler refuses longer lines
MAX_RESPONSE_BYTES = 65536
BRIDGE_CONTAINER = 'unist-orachard-ros2-esp-rover-bridge'   # container_name in the dashboard compose file
BRIDGE_FIND = "docker ps -a --filter name=esp-rover-bridge --format '{{.Names}} {{.Status}}'"
# ok:false answers after which the bridge no longer holds OUR lease (it was removed by an op stop / expiry / other
# owner). Latching on them must not send an op stop: it could only hit a lease the human took in the meantime.
LEASE_GONE_ERRORS = ('control_not_owned', 'stop_barrier')

RECOVERY_PROCEDURE = """\
ESP bridge recovery (Jetson, operator with SSH or a local terminal; motors must be safe first):
 1. Keep the vehicle stopped: physical E-stop pressed or wheels off the ground. Expect a possible
    short output glitch while the bridge re-opens the serial port (ESP reset, unverified).
 2. Find the ACTUAL bridge container name and state (expected %(c)s; use the name printed here):
      %(find)s
    and cat ~/unist_orachard_dashboard/runtime/rover_bridge_status.json
 3. Restart ONLY that bridge container:  docker restart <name from step 2>
    NEVER use docker compose up/down/restart for any dashboard service: the compose files pin an
    old dashboard image and recreate containers (CLAUDE.md 2026-10-13 table).
 4. Confirm: status shows connected=true, firmware=%(f)s, state=disarmed, last_error=null.
 5. Executor: acknowledge the latch in the operator page and press Start again (never automatic).
Why a restart is required:
 - MCU watchdog event: EspMotor.error = 'mcu_watchdog_rearm_required' is never cleared in-process
   (scripts/esp_motor.py event/ready) -> the bridge reports connected=false and refuses arm.
 - RuntimeError from the wheel controller (e.g. 'PC heartbeat expired; wheels disabled') escapes
   Bridge.step's except (ControlError, ValueError) and puts the bridge loop into state 'fault'
   for good (scripts/rover_control_bridge.py main loop): requests then time out as
   control_busy_stopped.
 - If the restart cannot open the device (USB re-enumerated after an unplug), do not recreate
   the container with compose; stop and escalate (device mapping is fixed at container creation).
""" % dict(c=BRIDGE_CONTAINER, f=FIRMWARE, find=BRIDGE_FIND)


class BridgeError(RuntimeError):
    def __init__(self, reason, response=None):
        super().__init__(reason)
        self.reason = reason
        self.response = response


class TransportError(BridgeError):
    pass


class UnixSocketTransport:
    """One request per connection, newline-terminated JSON both ways, bounded by one deadline."""

    def __init__(self, path, timeout=.6):
        self.path = str(path)
        self.timeout = float(timeout)
        self.connections = 0

    def __call__(self, request):
        data = json.dumps(request, allow_nan=False, separators=(',', ':')).encode('utf-8') + b'\n'
        if len(data) > MAX_REQUEST_BYTES:
            raise TransportError('request_too_large')
        deadline = time.monotonic() + self.timeout
        buffer = b''
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except (AttributeError, OSError) as exc:
            raise TransportError('socket_unavailable:%s' % exc) from None
        try:
            sock.settimeout(self.timeout)
            sock.connect(self.path)
            self.connections += 1
            sock.sendall(data)
            while b'\n' not in buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError('timeout')
                sock.settimeout(remaining)
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_RESPONSE_BYTES:
                    raise TransportError('response_too_large')
        except socket.timeout:
            raise TransportError('timeout') from None
        except OSError as exc:
            raise TransportError('socket_error:%s' % (exc.strerror or exc)) from None
        finally:
            sock.close()
        line = buffer.split(b'\n', 1)[0]
        if not line:
            raise TransportError('empty_response')
        try:
            response = json.loads(line)
        except ValueError:
            raise TransportError('malformed_response') from None
        if not isinstance(response, dict):
            raise TransportError('malformed_response')
        return response


class BridgeClient:
    def __init__(self, path='/run/ubts-rover/control.sock', owner=None, timeout=.6, transport=None,
                 snapshot_max_age_s=2.):
        self.owner = owner or 'row-executor:%s' % uuid.uuid4().hex[:12]
        if not isinstance(self.owner, str) or not self.owner:
            raise ValueError('owner must be a non-empty string')
        self.transport = transport if transport is not None else UnixSocketTransport(path, timeout)
        self.snapshot_max_age_s = float(snapshot_max_age_s)
        self._lock = threading.RLock()
        self.state = DISARMED
        self.lease = self.ticket = None
        self.seq = 0
        self.latch_reason = None
        self.last_status = None
        self.last_response = None
        self.last_ok_monotonic = None
        self.counts = dict(requests=0, drives=0, errors=0, latches=0, stops=0, latch_stop_skipped=0)

    # --- internals -----------------------------------------------------------------------------
    def _send(self, request):
        self.counts['requests'] += 1
        response = self.transport(request)
        if not isinstance(response, dict):
            raise TransportError('malformed_response')
        self.last_response = response
        return response

    def _latch(self, reason, send_stop):
        """Enter LATCHED; best-effort op stop if a lease was held (and send_stop). Never re-arms."""
        held = self.lease is not None
        self.state = LATCHED
        self.latch_reason = str(reason)
        self.counts['latches'] += 1
        self.lease = self.ticket = None
        if held and not send_stop:
            self.counts['latch_stop_skipped'] += 1
        if send_stop and held:
            try:
                self.counts['stops'] += 1
                self._send({'op': 'stop', 'reason': ('row_executor_latched:' + str(reason))[:80]})
            except BridgeError:
                pass

    def _fail(self, reason, response=None, send_stop=True):
        self.counts['errors'] += 1
        self._latch(reason, send_stop)
        raise BridgeError(reason, response)

    # --- operations ----------------------------------------------------------------------------
    def status(self):
        """Bridge snapshot dict. Transport errors or ok:false latch (stop sent if a lease was held)."""
        with self._lock:
            try:
                response = self._send({'op': 'status'})
            except BridgeError as exc:
                self._fail('status_failed:' + exc.reason)
            self.last_status = response
            if response.get('ok') is not True:
                self._fail('bridge_status_not_ok:%s:%s' % (response.get('state'), response.get('last_error')),
                           response)
            return response

    def arm(self):
        """Explicit operator action only. Precheck the bridge, then take the lease."""
        with self._lock:
            if self.state == LATCHED:
                raise BridgeError('latched:%s' % self.latch_reason)
            if self.state == ARMED:
                raise BridgeError('already_armed')
            status = self.status()
            problem = arm_precheck(status, self.snapshot_max_age_s)
            if problem:
                self._fail('arm_precheck:' + problem, status, send_stop=False)
            try:
                response = self._send({'op': 'arm', 'owner': self.owner})
            except BridgeError as exc:
                self._fail('arm_failed:' + exc.reason, send_stop=False)
            if response.get('ok') is not True:
                # e.g. control_in_use: we never held the lease, so no stop is sent to the other owner.
                self._fail('arm_refused:%s' % response.get('error'), response, send_stop=False)
            lease, ticket, seq = response.get('lease'), response.get('ticket'), response.get('seq')
            if not isinstance(lease, str) or not lease or not isinstance(ticket, str) or not ticket or seq != 0 \
                    or type(seq) is not int:
                self.lease = lease if isinstance(lease, str) and lease else None
                self._fail('arm_malformed_response', response)
            self.lease, self.ticket, self.seq = lease, ticket, 0
            self.state = ARMED
            self.latch_reason = None
            self.last_ok_monotonic = time.monotonic()
            return response

    def drive(self, left, right):
        """Send one drive with deadman:true (also for 0/0). Requires ARMED; never sends otherwise."""
        with self._lock:
            if self.state != ARMED:
                raise BridgeError('not_armed:%s' % self.state)
            for value in (left, right):
                if type(value) is not int or not 0 <= value <= 100:
                    self._fail('invalid_drive_values:%r/%r' % (left, right))
            seq = self.seq + 1
            request = {'op': 'drive', 'owner': self.owner, 'lease': self.lease, 'ticket': self.ticket,
                       'seq': seq, 'deadman': True, 'left': left, 'right': right}
            try:
                response = self._send(request)
            except BridgeError as exc:
                self._fail('drive_failed:' + exc.reason)
            if response.get('ok') is not True:
                error = response.get('error')
                # lease already gone (somebody else's STOP / expiry): latch silently, never stop a newer lease
                self._fail('drive_refused:%s' % error, response, send_stop=error not in LEASE_GONE_ERRORS)
            ticket = response.get('ticket')
            if (not isinstance(ticket, str) or not ticket or response.get('seq') != seq
                    or type(response.get('seq')) is not int or response.get('lease') != self.lease):
                self._fail('drive_malformed_response', response)
            self.ticket, self.seq = ticket, seq
            self.counts['drives'] += 1
            self.last_ok_monotonic = time.monotonic()
            return response

    def stop(self, reason='row_executor_stop', latch=False):
        """op stop (lease not needed). Normal end -> DISARMED; latch=True or any error -> LATCHED.

        Returns True when the bridge acknowledged the stop request. The acknowledgement means
        'stop requested', not 'outputs are zero': confirm with status()['outputs'].
        """
        with self._lock:
            self.counts['stops'] += 1
            self.lease = self.ticket = None
            try:
                response = self._send({'op': 'stop', 'reason': str(reason)[:80]})
            except BridgeError as exc:
                self.counts['errors'] += 1
                self._latch('stop_failed:' + exc.reason, send_stop=False)
                return False
            if response.get('ok') is not True:
                self.counts['errors'] += 1
                self._latch('stop_not_ok:%s' % response.get('error'), send_stop=False)
                return False
            if latch:
                self._latch('operator_stop:' + str(reason), send_stop=False)
            elif self.state != LATCHED:
                self.state = DISARMED
            return True

    def acknowledge_latch(self):
        """Explicit operator acknowledgement: LATCHED -> DISARMED. Does not arm."""
        with self._lock:
            if self.state == LATCHED:
                self.state = DISARMED
                self.latch_reason = None
            return self.state

    def snapshot(self):
        with self._lock:
            return dict(state=self.state, owner=self.owner, seq=self.seq, has_lease=self.lease is not None,
                        latch_reason=self.latch_reason, counts=dict(self.counts),
                        last_ok_monotonic=self.last_ok_monotonic)


def arm_precheck(status, snapshot_max_age_s=2., now=None):
    """None when the bridge snapshot allows an arm, else the first problem found."""
    if not isinstance(status, dict) or status.get('ok') is not True:
        return 'status_not_ok'
    if status.get('connected') is not True:
        return 'esp_not_connected'
    if status.get('firmware') != FIRMWARE:
        return 'firmware_not_v2'
    if status.get('last_error'):
        return 'bridge_error:%s' % status.get('last_error')
    if status.get('armed') is not False or status.get('state') != 'disarmed':
        return 'bridge_armed_by_other_client'
    if status.get('moving') is not False:
        return 'bridge_reports_moving'
    updated = status.get('updated_at')
    if updated is not None:
        now = time.time() if now is None else now
        if not isinstance(updated, (int, float)) or abs(now - updated) > snapshot_max_age_s:
            return 'bridge_snapshot_stale'
    return None


def recovery_hint(status=None, error=None):
    """Short operator hint for a failed status/arm/drive; the full text is RECOVERY_PROCEDURE."""
    restart = ('restart the ESP bridge: docker restart %s (never docker compose; check the actual name first: '
               'docker ps -a --filter name=esp-rover-bridge)' % BRIDGE_CONTAINER)
    if error is not None and str(error).startswith(('status_failed', 'drive_failed', 'arm_failed', 'stop_failed')):
        return 'bridge socket unreachable or timing out: check docker ps; ' + restart
    if not isinstance(status, dict):
        return 'no bridge status: check docker ps; ' + restart
    if status.get('state') == 'fault' or status.get('ok') is False:
        return 'bridge loop in fault state (%s): %s' % (status.get('last_error'), restart)
    if status.get('last_error') == 'mcu_watchdog_rearm_required':
        return 'MCU watchdog latched the ESP (permanent until restart): ' + restart
    if status.get('connected') is not True:
        return 'ESP not connected/verified: check USB and power; ' + restart
    if status.get('armed'):
        return 'another client holds the lease (dashboard manual?): press STOP on the dashboard, then Start again'
    return 'bridge looks ready: acknowledge the latch and press Start (never automatic)'
