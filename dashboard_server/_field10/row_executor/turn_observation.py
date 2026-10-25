"""SHADOW-only TurnAssist adapter. No bridge, lease, arm or signed-output path.

The executor swaps its inactive row Runner for this adapter, so only one estimator is fed.
This adapter can never authorize TurnAssist MOTOR output; even a non-zero underlying
decision is clamped to zero and ends the observation as an invariant violation.
"""
from .core import ACTIVE, ENDED, PAUSED, Decision
from .turn_assist import DONE, OBSERVE, TurnAssist, TurnRequest, TurnRequestError

RUN_KIND = 'turn_observe'
REQUEST_KEYS = frozenset(('direction', 'row_width_m', 'x_c_m', 'reach_m',
                          'headland_depth_m', 'creep_max_m', 'operator_aligned'))


class TurnObservationRunner:
    """Minimal Runner interface for the existing loop. Always non-actuating."""
    run_kind = RUN_KIND
    backstop = None
    calib = None
    end_note = None

    def __init__(self, cfg, now, params, *, run_id, vendor_commit, assist_factory=TurnAssist):
        if cfg.profile != 'field':
            raise ValueError('turn_observe_requires_field_profile')
        if not isinstance(params, dict) or set(params) - REQUEST_KEYS:
            raise ValueError('invalid_turn_observe_parameters')
        self.config = cfg
        self.run_id = run_id
        self.vendor_commit = vendor_commit
        self.assist = assist_factory(cfg)
        try:
            request = TurnRequest(**params)
            self.assist.start(now, request, motor_output=False, observe=True)
        except (TypeError, TurnRequestError) as exc:
            raise ValueError('invalid_turn_observe_request: %s' % exc) from None
        self._reason = 'precheck'
        self._summary = {}

    @property
    def state(self):
        if not self.assist.is_open():
            return ENDED
        return ACTIVE if self.assist.phase == OBSERVE else PAUSED

    @property
    def end_reason(self):
        return self.assist.end_reason

    def feed_imu(self, receipt, gyro, acc):
        return self.assist.feed_imu(receipt, gyro, acc)

    def feed_scan(self, receipt, ranges, angle_min, increment, range_max, range_min=0., stamp=None):
        # TurnAssist owns its established fixed scan-latency convention; do not apply
        # the row Runner's header offset a second time. receipt remains monotonic.
        return self.assist.feed_scan(receipt, ranges, angle_min, increment, range_max, range_min)

    def end_run(self, now, reason):
        self.assist.abort(now, reason)

    def pause(self, now, reason):
        # A passive observation interrupted by the loop watchdog must be explicitly
        # restarted. It never becomes a resumable motor-capable run.
        self.assist.abort(now, 'observation_' + str(reason))

    def tick(self, now, hold):
        d = self.assist.tick(now, False)
        if (d.left, d.right) != (0, 0) or self.assist.motor_output or not self.assist.observe:
            self.assist.abort(now, 'observation_output_invariant')
            self._reason = 'observation_output_invariant'
        else:
            self._reason = d.reason
        s = dict(d.summary)
        self._summary = s
        s.update(run_id=self.run_id, run_kind=RUN_KIND, motor_output=False,
                 observation_only=True, turn_observe=self.status())
        return Decision(0, 0, self.state, self._reason, self.state == ENDED, s)

    def status(self):
        s = self.assist.status()
        # Status must not expose turn_feasibility.output_allowed as an installed
        # permission: this integration is INFO_ONLY regardless of measurements.
        s.pop('turn_feasibility', None)
        s.update(run_id=self.run_id, classification='INFO_ONLY', motor_output=False,
                 output_allowed=False, vehicle_control='none', arm_available=False,
                 stop_effect='observation_only', mode='observe', observe=True,
                 completed=self.assist.phase == DONE)
        s['measurements'] = {k: self._summary.get(k) for k in (
            'psi_gyro_deg', 'psi_lidar_deg', 'rate_deg_s', 'lidar_age_s', 'imu_age_s',
            'pivot_clearance_m', 'front_clearance_m', 'row_source', 'bias_deg_s')}
        return s
