"""Signed drive for the assisted headland turn: an opt-in subclass of the forward-only BridgeClient.

bridge_client.BridgeClient.drive() stays forward-only and unchanged (a negative duty latches it).
SignedBridgeClient adds drive_signed(left, right, guard_check) for TurnAssist pivots only:
  * refused (latch + best-effort op stop) unless the client was constructed with allow_signed=True;
    the default is False, so a client built the usual way can never send a pivot;
  * guard_check is called first (defence in depth, e.g. TurnGuard.shape_problem): any non-None answer
    latches before anything is sent;
  * duties must be ints in [-100, 100] (the bridge core accepts exactly that with deadman:true,
    DASH/scripts/rover_control_core.py dispatch);
  * otherwise the request, deadman, sequence, ticket and response checks are the ones of drive().
The ESP bridge, dashboard and firmware are not changed.
"""
import time

from .bridge_client import ARMED, BridgeClient, BridgeError


class SignedBridgeClient(BridgeClient):
    def __init__(self, *args, allow_signed=False, **kwargs):
        super().__init__(*args, **kwargs)
        if type(allow_signed) is not bool:
            raise ValueError('allow_signed must be true/false')
        self.allow_signed = allow_signed
        self.counts['signed_drives'] = 0

    def drive_signed(self, left, right, guard_check):
        """One drive with deadman:true and signed duties. Requires ARMED and allow_signed."""
        with self._lock:
            if self.state != ARMED:
                raise BridgeError('not_armed:%s' % self.state)
            if not self.allow_signed:
                self._fail('signed_drive_not_allowed')
            if not callable(guard_check):
                self._fail('signed_drive_without_guard')
            for value in (left, right):
                if type(value) is not int or not -100 <= value <= 100:
                    self._fail('invalid_signed_drive_values:%r/%r' % (left, right))
            try:
                problem = guard_check(left, right)
            except Exception as exc:       # a broken guard must stop, never pass
                problem = 'guard_error:%s' % type(exc).__name__
            if problem is not None:
                self._fail('turn_guard:%s' % problem)
            if left >= 0 and right >= 0:
                response = self.drive(left, right)
            else:
                response = self._send_signed(left, right)
            self.counts['signed_drives'] += 1
            return response

    def _send_signed(self, left, right):
        seq = self.seq + 1
        request = {'op': 'drive', 'owner': self.owner, 'lease': self.lease, 'ticket': self.ticket,
                   'seq': seq, 'deadman': True, 'left': left, 'right': right}
        try:
            response = self._send(request)
        except BridgeError as exc:
            self._fail('drive_failed:' + exc.reason)
        if response.get('ok') is not True:
            self._fail('drive_refused:%s' % response.get('error'), response)
        ticket = response.get('ticket')
        if (not isinstance(ticket, str) or not ticket or response.get('seq') != seq
                or type(response.get('seq')) is not int or response.get('lease') != self.lease):
            self._fail('drive_malformed_response', response)
        self.ticket, self.seq = ticket, seq
        self.counts['drives'] += 1
        self.last_ok_monotonic = time.monotonic()
        return response
