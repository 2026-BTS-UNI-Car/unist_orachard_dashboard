"""TurnAssist: hold-to-run assisted headland U-turn (CREEP_OUT -> PIVOT_1 -> TRAVERSE -> PIVOT_2 -> SETTLE).

Opt-in and DISABLED by default (config turn_assist_enabled false). Pure: no ROS, no socket, no motor
I/O. It is fed exactly like core.Runner (same ScanAdapter / ImuYawRate conversion, receipt monotonic
clock, scan stamp = receipt - scan_latency_s, vendored SensorEstimator) and returns a core.Decision
with SIGNED duties per tick. Design and evidence: claude-field-autonomy-20261022/turn/TURN_DESIGN.txt.

Run (one U-turn from an operator start at the row end; the vehicle stands still):
  PRECHECK        no output. Stillness >= turn_precheck_still_s, low vibration, IMU dt median, fresh
                  inputs; gyro bias b0 is LOCKED from this still window (|b0|, sd bounded); the row
                  axis psi_row comes from the corridor band of this run's estimator, else from the row
                  run's heading-error hint, else from the operator's 'heading = row axis' confirmation.
                  The TURN FRAME is fixed here: origin = base centre, +x = row axis (towards the
                  headland), +y = left. Every later target is absolute in this frame (psi1 = s*90 deg,
                  psi2 = s*180 deg, traverse y = s*(W - predicted PIVOT_2 shift)), so an error of one
                  step is corrected by the next.
  CREEP_OUT       forward only (heading hold on psi_row, |L-R| <= turn_drive_diff_cap_pct). Stops when
                  the base is x_c past the last return of the TURN-SIDE trunk-line band (+-turn_line_band_m,
                  followed from the PRECHECK position across gaps <= turn_line_gap_m; canopy returns
                  count, so the stop is conservative by the canopy reach along the row), the pivot disc
                  (nominal centre, any direction: covers the far line too) is clear >= turn_clear_radius_m
                  on turn_clear_scans consecutive scans and the front clearance (measured from the
                  footprint front) covers the table reach: reach - x_c + 0.3 - L/2 + stopping distance.
  PIVOT_1/PIVOT_2 (-s*d, +s*d). BREAKAWAY: d = pivot_duty_start_pct, +pivot_duty_step_pct after
                  ramp + grace without rotation, up to pivot_duty_cap_pct; no rotation at the cap
                  after the grace = pivot_stall. ROTATE: the duty that rotated is held; release when
                  the remaining angle (control heading: newest valid scan-matching heading + gyro
                  increment since its stamp; gyro alone without valid scan matching) <= rate * t_coast. COAST: 0/0 until still and quiet; then the
                  judge takes the scan-matching heading (standing still it is the better one: real 10/10
                  turns p90 2.3 deg vs gyro 4 deg) unless it differs from the gyro by more than
                  yaw_disagree_abort_deg (end); the gyro is re-anchored to it and its scale gain is
                  updated from the pivot (clamped 0.92-1.08). |error| <= accept: done; undershoot <=
                  topup_max_deg: one top-up in
                  the same direction (never an opposite pivot); overshoot <= pivot_overshoot_max_deg is
                  absorbed by the next absolute target, larger ends the run.
  TRAVERSE        forward only, heading hold on psi1, until the turn-frame lateral position (LiDAR
                  odometry) reaches the next corridor centre (-lateral0 + s*W, minus the PIVOT_2 shift
                  predicted from the C6 rotation centre) minus the stopping distance. A LiDAR gap while
                  driving can lose translation (the matcher never invents it): warning odometry_gap_*.
  SETTLE_ACQUIRE  no output. A FRESH estimator acquires the next corridor while standing:
                  READY = valid band, |heading error| <= handoff_heading_max_deg, |lateral| <=
                  handoff_lateral_max_m, width W +- handoff_width_tol_m. Then DONE (terminal; the
                  caller sends op stop). Row following is NEVER started automatically.
Hold-to-run: every step needs a NEW press made after the step boundary (vehicle still, inputs fresh
for fresh_recover_s). Releasing the hold sends 0/0 at once (PAUSED; per-step pause limit). A resumed
pivot restarts the breakaway ladder at the start duty; a resumed forward step continues to the same
absolute target.
Terminal reasons (0/0 and terminal=True; the caller waits for stillness or terminal_stop_wait_s, then
op stop): turn_guard_violation:*, pivot_stall, pivot_stall_budget, pivot_wrong_direction,
pivot_rate_excess, pivot_overshoot, pivot_undershoot, pivot_clearance_lost, pivot_start_clearance,
pivot_front_insufficient, yaw_disagreement, imu_gap_in_pivot, turn_timeout, settle_timeout,
creep_backstop, creep_front_blocked, creep_side_blocked, creep_side_drift, traverse_backstop,
traverse_front_blocked, traverse_side_blocked, odometry_invalid, uncommanded_motion,
stale_repeat_limit, stall_guard_repeat_limit, pause_limit, precheck_timeout:*, next_corridor_absent,
row_width_mismatch, clock_regression, internal_error:*, operator_stop (abort()).
Normal ends (DONE): pivot_done (TurnRequest.pivot_only: the T2 single assisted pivot, no creep / traverse;
only the pivot disc is checked before it), turn_done_ready (standing acquisition of the next corridor), turn_done_ready_odometry
(the standing acquisition never found a corridor, the PRECHECK band confirmed W and the dead-reckoned pose
in the turn frame is inside the hand-off limits; shown as such to the operator), turn_done_not_ready;
observe_done (observe mode).

Output modes (start()):
  motor_output=True   only when config.turn_assist_enabled AND motor_output_enabled AND
                      turn_feasibility()['output_allowed']; the decision carries the commands.
  motor_output=False  DRY run (default): the same state machine and guard; every decision is 0/0 and
                      summary['proposed'] holds what would have been sent.
  observe=True        T1 shadow: PRECHECK, then passive measurements while a person turns the
                      vehicle by hand (line end, pivot disc, front, gyro vs scan-matching heading per
                      rotation, acquisition of the next corridor). Never any output.
"""
from collections import deque
from dataclasses import dataclass, fields, replace
import math

import numpy as np

from .config import ConfigError, turn_drive_duty, turn_feasibility
from .core import Decision, ImuYawRate, ScanAdapter, ScanRejected, load_vendor, wrap
from .guard import FORWARD, PIVOT, TurnGuard

IDLE, PRECHECK, CREEP_OUT, PIVOT_1, TRAVERSE, PIVOT_2 = 'IDLE', 'PRECHECK', 'CREEP_OUT', 'PIVOT_1', 'TRAVERSE', 'PIVOT_2'
SETTLE_ACQUIRE, DONE, ENDED, OBSERVE = 'SETTLE_ACQUIRE', 'DONE', 'ENDED', 'OBSERVE'
MOTION_PHASES = (CREEP_OUT, PIVOT_1, TRAVERSE, PIVOT_2)
AWAIT, ACTIVE, PAUSED, STOPPING = 'await_press', 'active', 'paused', 'stopping'
BREAKAWAY, ROTATE, COAST = 'BREAKAWAY', 'ROTATE', 'COAST'
NEXT_PHASE = {CREEP_OUT: PIVOT_1, PIVOT_1: TRAVERSE, TRAVERSE: PIVOT_2, PIVOT_2: SETTLE_ACQUIRE}
RATE_TAU_S = .25                 # smoothing of the bias-corrected gyro (estimator gyro_lowpass_s)
GYRO_HISTORY_S = 4.
MAX_EVENTS = 400
BUDGET_MARGIN_S = .3             # own budgets end the run this much before the guard would
PRESS_RECOVER_STILL_S = .5
GYRO_GAIN_LIMITS = (.92, 1.08)   # real gyro scale vs scan matching per turn: p10 -3..-5 %, p90 +6..+7 %
GYRO_GAIN_MIN_TURN_DEG = 30.
BIAS_SE_MAX_DEG_S = .1           # standard error of the locked bias (sd / sqrt(n))
LIDAR_CTRL_MAX_AGE_S = .35       # control heading uses scan matching only while it is this fresh


class TurnRequestError(ValueError):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('; '.join(self.problems))


def _real(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class TurnRequest:
    """Bounded operator inputs of one turn (the operator page validates the same ranges)."""
    direction: str                       # 'left' (CCW, s=+1) or 'right' (CW, s=-1); no default
    row_width_m: float                   # W: trunk line to trunk line (tape measure), 2.8-5.5
    x_c_m: float                         # printed table (canopy class at 0.6 m), 1.2-3.0
    reach_m: float                       # printed table: farthest footprint point from the trunk line (p99)
    headland_depth_m: float              # measured free depth from the trunk line (fence, road edge, ditch)
    creep_max_m: float = 6.
    row_heading_error_deg: object = None  # row run's last band heading error (vehicle not moved since)
    operator_aligned: bool = False        # operator confirms: current heading = row axis
    allow_next_absent: bool = False       # operator override of an ABSENT next-corridor vote (logged)
    pivot_only: bool = False              # T2: PRECHECK -> PIVOT_1 -> DONE 'pivot_done' (no creep / traverse)

    @property
    def sign(self):
        return 1 if self.direction == 'left' else -1

    def problems(self, creep_limit_m=10.):
        p = []
        if self.direction not in ('left', 'right'):
            p.append('direction: left or right')
        checks = (('row_width_m', 2.8, 5.5), ('x_c_m', 1.2, 3.), ('reach_m', 2., 6.5),
                  ('headland_depth_m', 0., 50.), ('creep_max_m', .5, float(creep_limit_m)))
        for name, lo, hi in checks:
            value = getattr(self, name)
            if not _real(value) or not lo <= value <= hi:
                p.append('%s: outside [%g, %g]' % (name, lo, hi))
        if not p:
            if self.reach_m < self.x_c_m + .61:
                p.append('reach_m must be at least x_c_m + half the footprint length')
            if self.headland_depth_m < self.reach_m + .3:
                p.append('headland_depth_m %.2f below the required reach + 0.3 = %.2f (manual turn)'
                         % (self.headland_depth_m, self.reach_m + .3))
        hint = self.row_heading_error_deg
        if hint is not None and (not _real(hint) or abs(hint) > 30.):
            p.append('row_heading_error_deg: None or |value| <= 30')
        for name in ('operator_aligned', 'allow_next_absent', 'pivot_only'):
            if type(getattr(self, name)) is not bool:
                p.append('%s: must be true/false' % name)
        return p


@dataclass(frozen=True)
class TurnSettings:
    """Validated config values used by TurnAssist (one place; read once per start)."""
    pivot_start_pct: int
    pivot_cap_pct: int
    pivot_step_pct: int
    pivot_step_interval_s: float
    grace_s: float
    rotating_deg_s: float
    rotating_confirm_s: float
    rotating_lidar_deg: float
    stall_window_s: float
    stall_min_deg: float
    stall_rate_deg_s: float
    rate_max_deg_s: float
    wrong_direction_deg: float
    coast_hi_s: float
    coast_lo_s: float
    accept_deg: float
    topup_max: int
    topup_max_deg: float
    overshoot_max_deg: float
    icr: dict
    warn_deg: float
    abort_deg: float
    drive_duty_pct: int
    drive_min_pct: int
    drive_cap_pct: int
    drive_diff_cap_pct: int
    heading_kp: float
    clear_radius_m: float
    clear_during_m: float
    clear_scans: int
    creep_limit_m: float
    side_drift_max_m: float
    side_min_m: float
    side_hard_m: float
    line_band_m: float
    line_gap_m: float
    imu_gap_s: float
    imu_dt_median_s: float
    max_pauses: int
    pivot_total_s: float
    stall_loaded_total_s: float
    bias_max_deg_s: float
    bias_sd_max_deg_s: float
    precheck_still_s: float
    precheck_vibration: float
    precheck_timeout_s: float
    settle_still_s: float
    settle_vibration: float
    settle_timeout_s: float
    shape_change_extra_s: float
    shape_change_still_s: float
    uncommanded_rate_deg_s: float
    uncommanded_move_m: float
    acquire_still_s: float
    acquire_timeout_s: float
    handoff_heading_deg: float
    handoff_lateral_m: float
    handoff_width_tol_m: float
    lidar_max_age_s: float
    imu_max_age_s: float
    fresh_recover_s: float
    stall_guard_s: float
    max_stale_events: int
    max_stall_guard_pauses: int
    scan_latency_s: float
    lidar_x_in_base_m: float
    stop_time_s: float
    stop_decel_m_s2: float
    front_margin_m: float
    v_ref_m_s: float

    @classmethod
    def from_config(cls, cfg):
        overrides = cfg.controller_overrides
        start = cfg.pivot_duty_start_pct
        cap = cfg.pivot_duty_cap_pct
        # Unknown C6 values (dry / observe runs only): the design defaults 60 / 80 %.
        start = 60 if start is None else int(start)
        cap = max(start, 80 if cap is None else int(cap))
        drive = turn_drive_duty(cfg)
        inner = cfg.guard_inner_min_pct()
        diff_cap = int(cfg.turn_drive_diff_cap_pct)
        drive_cap = max(1, min(drive + diff_cap, int(cfg.duty_cap_pct)))    # never above the row duty cap
        icr = {}
        for side, s in (('left', 1), ('right', -1)):
            ahead, left = getattr(cfg, 'pivot_icr_ahead_%s_m' % side), getattr(cfg, 'pivot_icr_left_%s_m' % side)
            icr[s] = None if ahead is None or left is None else (float(ahead), float(left))
        return cls(
            pivot_start_pct=start, pivot_cap_pct=cap, pivot_step_pct=int(cfg.pivot_duty_step_pct),
            pivot_step_interval_s=float(cfg.pivot_step_interval_s), grace_s=float(cfg.pivot_breakaway_grace_s),
            rotating_deg_s=float(cfg.pivot_rotating_deg_s), rotating_confirm_s=float(cfg.pivot_rotating_confirm_s),
            rotating_lidar_deg=float(cfg.pivot_rotating_lidar_deg), stall_window_s=float(cfg.pivot_stall_window_s),
            stall_min_deg=float(cfg.pivot_stall_min_deg),
            stall_rate_deg_s=float(cfg.pivot_stall_min_deg) / float(cfg.pivot_stall_window_s),
            rate_max_deg_s=float(cfg.pivot_rate_max_deg_s),
            wrong_direction_deg=float(cfg.pivot_wrong_direction_deg), coast_hi_s=float(cfg.pivot_coast_s_hi),
            coast_lo_s=float(cfg.pivot_coast_s_lo), accept_deg=float(cfg.pivot_accept_error_deg),
            topup_max=int(cfg.pivot_topup_max), topup_max_deg=float(cfg.pivot_topup_max_deg),
            overshoot_max_deg=float(cfg.pivot_overshoot_max_deg), icr=icr,
            warn_deg=float(cfg.yaw_disagree_warn_deg), abort_deg=float(cfg.yaw_disagree_abort_deg),
            drive_duty_pct=drive, drive_min_pct=min(drive_cap, max(1, inner if inner is not None else 1)),
            drive_cap_pct=drive_cap,
            drive_diff_cap_pct=int(cfg.turn_drive_diff_cap_pct), heading_kp=float(cfg.turn_heading_kp_pct_per_deg),
            clear_radius_m=float(cfg.turn_clear_radius_m), clear_during_m=float(cfg.turn_clear_min_during_m),
            clear_scans=int(cfg.turn_clear_scans), creep_limit_m=float(cfg.turn_creep_max_m),
            side_drift_max_m=float(cfg.turn_side_drift_max_m), side_min_m=float(cfg.turn_side_min_m),
            side_hard_m=float(cfg.turn_side_hard_m), line_band_m=float(cfg.turn_line_band_m),
            line_gap_m=float(cfg.turn_line_gap_m), imu_gap_s=float(cfg.turn_imu_gap_abort_s),
            imu_dt_median_s=float(cfg.turn_imu_dt_median_max_s), max_pauses=int(cfg.turn_max_pauses_per_step),
            pivot_total_s=float(cfg.turn_pivot_total_s), stall_loaded_total_s=float(cfg.turn_stall_loaded_total_s),
            bias_max_deg_s=float(cfg.turn_bias_max_deg_s), bias_sd_max_deg_s=float(cfg.turn_bias_sd_max_deg_s),
            precheck_still_s=float(cfg.turn_precheck_still_s),
            precheck_vibration=float(cfg.turn_precheck_vibration_max_m_s2),
            precheck_timeout_s=float(cfg.turn_precheck_timeout_s), settle_still_s=float(cfg.turn_settle_still_s),
            settle_vibration=float(cfg.turn_settle_vibration_max_m_s2),
            settle_timeout_s=float(cfg.turn_settle_timeout_s),
            shape_change_extra_s=float(cfg.turn_shape_change_extra_s),
            shape_change_still_s=float(cfg.turn_shape_change_still_s),
            uncommanded_rate_deg_s=float(cfg.turn_uncommanded_rate_deg_s),
            uncommanded_move_m=float(cfg.turn_uncommanded_move_m), acquire_still_s=float(cfg.turn_acquire_still_s),
            acquire_timeout_s=float(cfg.turn_acquire_timeout_s), handoff_heading_deg=float(cfg.handoff_heading_max_deg),
            handoff_lateral_m=float(cfg.handoff_lateral_max_m), handoff_width_tol_m=float(cfg.handoff_width_tol_m),
            lidar_max_age_s=float(cfg.lidar_max_age_s), imu_max_age_s=float(cfg.imu_max_age_s),
            fresh_recover_s=float(cfg.fresh_recover_s), stall_guard_s=float(cfg.stall_guard_s),
            max_stale_events=int(cfg.max_stale_events), max_stall_guard_pauses=int(cfg.max_stall_guard_pauses),
            scan_latency_s=float(cfg.scan_latency_s), lidar_x_in_base_m=float(cfg.lidar_x_in_base_m),
            stop_time_s=float(overrides.get('reaction_s', .15)) + float(overrides.get('stop_onset_s', .25)),
            stop_decel_m_s2=float(overrides.get('stop_decel_m_s2', 1.07)),
            front_margin_m=float(overrides.get('front_margin_m', .35)),
            v_ref_m_s=float(cfg.v_measured_m_s) if cfg.v_measured_m_s is not None else .3)

    def stop_distance(self, v):
        v = max(0., v)
        return v * self.stop_time_s + v * v / (2. * self.stop_decel_m_s2)

    def coast_initial(self, duty):
        if duty >= 90:
            return self.coast_hi_s
        if duty <= 70:
            return self.coast_lo_s
        return self.coast_lo_s + (duty - 70.) / 20. * (self.coast_hi_s - self.coast_lo_s)


# --- pivot detection (shared with the real-record replay) -------------------------------------------
class PivotTracker:
    """Detection logic of ONE pivot command episode (pure; TurnAssist and turn_real_replay.py use it).

    update() inputs (signed so that + is the commanded direction s):
      rate_dps        smoothed, bias-corrected gyro yaw rate [deg/s] (raw sign; multiplied by s here)
      turned_deg      s * gyro heading change since the episode start [deg]
      lidar_deg       s * scan-matching heading change since the start, None when odometry is invalid
      duty, duty_since, ramp_s, at_cap   the command: |d|, when it was first sent, its MCU ramp time
    Returns None or an end reason: 'pivot_rate_excess', 'pivot_wrong_direction', 'pivot_stall'
    (no rotation at the cap after ramp + grace, or less than stall_min_deg per stall_window_s while
    rotating), 'pivot_too_slow' (turning at the cap, but slower than the confirmation rate). Sets .rotating / .t_rotating / .d_rotating at the confirmation (smoothed rate >= the
    threshold for rotating_confirm_s and scan-matching rotation >= rotating_lidar_deg when available).
    """

    def __init__(self, settings, direction, t0):
        self.st, self.s, self.t0 = settings, direction, float(t0)
        self.rotating = False
        self.t_rotating = None
        self.d_rotating = None
        self.above_since = None
        self.history = deque()
        self.peak_rate = 0.
        self.stall_kind = None

    def update(self, now, rate_dps, turned_deg, lidar_deg, duty, duty_since, ramp_s, at_cap):
        st = self.st
        signed = self.s * rate_dps
        self.peak_rate = max(self.peak_rate, abs(rate_dps))
        if abs(rate_dps) > st.rate_max_deg_s:
            return 'pivot_rate_excess'
        if turned_deg < -st.wrong_direction_deg or (lidar_deg is not None and lidar_deg < -st.wrong_direction_deg):
            return 'pivot_wrong_direction'
        if not self.rotating:
            if signed >= st.rotating_deg_s:
                if self.above_since is None:
                    self.above_since = now
            else:
                self.above_since = None
            lidar_ok = lidar_deg is None or lidar_deg >= st.rotating_lidar_deg
            if self.above_since is not None and now - self.above_since >= st.rotating_confirm_s - 1e-9 and lidar_ok:
                self.rotating, self.t_rotating, self.d_rotating = True, now, duty
                self.history.clear()
            elif at_cap and now - duty_since >= ramp_s + st.grace_s - 1e-9:
                if signed >= st.stall_rate_deg_s:
                    self.stall_kind = 'too_slow'
                    return 'pivot_too_slow'
                self.stall_kind = 'breakaway'
                return 'pivot_stall'
            return None
        progress = lidar_deg if lidar_deg is not None else turned_deg
        self.history.append((now, progress, lidar_deg is not None))
        while self.history and now - self.history[0][0] > st.stall_window_s + 1e-9:
            self.history.popleft()
        first, last = self.history[0], self.history[-1]
        if last[0] - first[0] >= .95 * st.stall_window_s and last[1] - first[1] < st.stall_min_deg:
            if last[2] or signed < 2.:
                self.stall_kind = 'rotating'
                return 'pivot_stall'
        return None


class LineEndTracker:
    """End of each trunk-line band ahead, in the turn frame (accumulated maximum over scans).

    The line is known to extend to the PRECHECK position (x = 0: the row run ended there, inside or at
    the end of the row), so the chain starts at x = 0 and follows the band returns ahead while the gaps
    stay <= gap_m: a missing tree beside the vehicle does not cut the line, a next block or a fence
    farther than gap_m beyond the last return is not merged. A scan says something about a line only
    when the band has a return within [-4 m, gap_m] of the start. Canopy returns in the band count (the
    end is the outermost return, conservative).
    """

    def __init__(self, band_m, gap_m, lines):
        self.band, self.gap = float(band_m), float(gap_m)
        self.lines = dict(lines)                    # name -> line y in the turn frame
        self.end = dict.fromkeys(self.lines)        # name -> max x seen (None = not observed)
        self.scans = 0

    def update(self, xs, ys, x_base=None):
        self.scans += 1
        for name, y_line in self.lines.items():
            sel = (np.abs(ys - y_line) <= self.band) & (xs >= -4.)
            if not sel.any():
                continue
            x = np.sort(xs[sel])
            if x[0] > self.gap:
                continue
            current = 0.
            for value in x:
                if value <= current:
                    continue
                if value - current > self.gap:
                    break
                current = float(value)
            if self.end[name] is None or current > self.end[name]:
                self.end[name] = current

    def line_end(self, required):
        """End of the turn-side line (the row the vehicle turns around); None while not observed.

        The far line is not used here: the pivot disc (turn_clear_radius_m about the nominal centre,
        any direction) must also be clear before a pivot, which covers a longer far line or canopy.
        """
        return self.end.get(required)


# --- the runner ------------------------------------------------------------------------------------------
class TurnAssist:
    """Not thread-safe: the caller serialises feed_*/tick/abort/pause (one lock, like core.Runner)."""

    def __init__(self, config, estimator_factory=None):
        config.validate()
        self.config = config
        self.feasibility = turn_feasibility(config)
        estimator, _ = load_vendor()
        self._estimator_module = estimator
        self.estimator_config = estimator.EstimatorConfig(**config.estimator_overrides())
        self._new_estimator = estimator_factory or (lambda: estimator.SensorEstimator(self.estimator_config))
        self.scan_adapter = ScanAdapter.from_config(config)
        self.imu = ImuYawRate(config.imu_up_tau_s, config.imu_yaw_sign)
        self.stats = dict(imu_in=0, imu_rejected=0, scans_in=0, scans_rejected={}, scan_errors=0)
        self.run_id = 0
        self.phase = IDLE
        self.mode = None
        self.reason = 'no_run'
        self.end_reason = None
        self.request = None
        self.est = None
        self.acq_est = None
        self.guard = None
        self.motor_output = False
        self.observe = False
        self.last_receipt = dict(scan=None, imu=None)
        self.st = TurnSettings.from_config(config)
        self._reset(0.)

    # --- run life cycle ---------------------------------------------------------------------------
    def start(self, t, request, motor_output=False, observe=False):
        if self.phase not in (IDLE, DONE, ENDED):
            raise RuntimeError('turn_in_progress')
        if not isinstance(request, TurnRequest):
            raise TurnRequestError(['request must be a TurnRequest'])
        if motor_output and observe:
            raise TurnRequestError(['observe mode never commands output'])
        settings = TurnSettings.from_config(self.config)
        problems = request.problems(settings.creep_limit_m)
        if problems:
            raise TurnRequestError(problems)
        if motor_output:
            feas = turn_feasibility(self.config)
            if not (self.config.turn_assist_enabled and self.config.motor_output_enabled and feas['output_allowed']):
                raise TurnRequestError(['turn output not allowed: turn_assist_enabled=%s motor_output_enabled=%s %s'
                                        % (self.config.turn_assist_enabled, self.config.motor_output_enabled,
                                           feas['problems'] + feas['unknown'])])
        self.st = settings
        self.request = request
        self.s = request.sign
        self.motor_output = bool(motor_output)
        self.observe = bool(observe)
        self.run_id += 1
        try:
            self.guard = TurnGuard.from_settings(settings, self.s)
        except ValueError as exc:
            raise TurnRequestError(['turn guard: %s' % exc]) from None
        self.est = self._new_estimator()
        self.acq_est = None
        self._reset(float(t))
        self._go(PRECHECK, None, 'precheck')
        return self.run_id

    def _reset(self, t):
        self.t_start = t
        self.end_reason = None
        self.events = []
        self.warnings = {}
        self.pivots = []
        self._pending_end = None
        self._prev_hold = True        # a press must be seen AFTER the start
        self._start_pending = False   # accepted press waiting for the shape-change fade
        self._press_valid = False
        self._fresh_since = None
        self._last_tick = None
        self._last_out = (0, 0)
        self._proposed = (0, 0)
        self.stale_events = 0
        self.forward_odom_invalid_ticks = 0
        self.stall_guard_pauses = 0
        self.step_pauses = 0
        self.total_pauses = 0
        self.presses = 0
        # gyro (own integral with the bias locked at PRECHECK)
        self._imu_t = None
        self._imu_dts = deque(maxlen=200)
        self._bias_buf = deque()
        self.b0 = None
        self._g = 0.                  # integral of (rate - b0) since the lock [rad]
        self._rate = 0.               # smoothed rate - b0 [rad/s]
        self._g_hist = deque()
        self._psi_offset = 0.         # psi_gyro = offset + g
        self.gyro_gain = 1.           # scale of the gyro vs scan matching, learnt from the pivots of this turn
        self.max_imu_gap_watch = 0.
        # LiDAR in the turn frame
        self.locked = False
        self.psi_row_odom = None
        self.origin = None
        self.row_source = None
        self.lateral0 = 0.
        self.width_obs = None
        self.lines = None
        self._lidar_cont = None       # continuous scan-matching heading in the odom frame [rad]
        self._lidar_prev = None
        self._lidar_valid = False
        self._lidar_stamp = None
        self._psi_lidar_scan = None   # scan-matching heading (turn frame) of the newest valid scan
        self.clear_streak = 0
        self.disagree_max = 0.
        self._still_ref = None        # base position / time when the vehicle was confirmed still at a step boundary
        self.step = None
        self.handoff = None
        self.observe_log = None
        self.coast_s = None

    def _go(self, phase, mode, reason):
        self.phase, self.mode, self.reason = phase, mode, reason
        if len(self.events) < MAX_EVENTS:
            t = self._last_tick if self._last_tick is not None else self.t_start
            self.events.append((round(t - self.t_start, 3), phase, mode, reason))

    def _end(self, reason):
        if self.phase in (DONE, ENDED):
            return
        self.end_reason = str(reason)
        self._go(ENDED, None, self.end_reason)
        self._last_out = (0, 0)

    def _done(self, reason):
        self.end_reason = reason
        self._go(DONE, None, reason)
        self._last_out = (0, 0)

    def _warn(self, name):
        self.warnings[name] = self.warnings.get(name, 0) + 1

    def abort(self, t, why='operator_stop'):
        """Operator / executor STOP, bridge error: end the run (0/0, terminal)."""
        if self.phase not in (IDLE, DONE, ENDED):
            self._end(str(why))

    def pause(self, t, why='stall_guard'):
        """External pause (loop-stall watchdog)."""
        if self.mode == ACTIVE:
            self._pause(float(t), str(why))

    def is_open(self):
        return self.phase not in (IDLE, DONE, ENDED)

    # --- inputs --------------------------------------------------------------------------------------
    def feed_imu(self, t, gyro_xyz, acc_xyz):
        self.stats['imu_in'] += 1
        out = self.imu.update(t, gyro_xyz, acc_xyz)
        if out is None:
            self.stats['imu_rejected'] += 1
            return False
        t = float(t)
        self.last_receipt['imu'] = t
        if not self.is_open():
            return True
        try:
            self._on_imu(t, float(out[0]), out[1])
        except Exception as exc:                     # never let a sensor callback raise into the node
            self._pending_end = self._pending_end or 'internal_error:imu:%s' % type(exc).__name__
        return True

    def _on_imu(self, t, rate, acc):
        if self._imu_t is not None and t <= self._imu_t:
            return
        dt = None if self._imu_t is None else t - self._imu_t
        if dt is not None:
            self._imu_dts.append(dt)
            if self._pivot_watch():
                self.max_imu_gap_watch = max(self.max_imu_gap_watch, dt)
                if dt > self.st.imu_gap_s:
                    self._pending_end = self._pending_end or 'imu_gap_in_pivot'
        self._imu_t = t
        self.est.on_imu(t, rate, acc)
        if self.acq_est is not None:
            self.acq_est.on_imu(t, rate, acc)
        if self.b0 is None:
            self._bias_buf.append((t, rate))
            while self._bias_buf and t - self._bias_buf[0][0] > self.st.precheck_still_s + .5:
                self._bias_buf.popleft()
            return
        step = .05 if dt is None else min(self.st.imu_gap_s, dt)     # a longer gap ends a pivot anyway
        r = (rate - self.b0) * self.gyro_gain
        self._g += r * step
        self._rate += (r - self._rate) * (1. - math.exp(-step / RATE_TAU_S))
        self._g_hist.append((t, self._g))
        while self._g_hist and t - self._g_hist[0][0] > GYRO_HISTORY_S:
            self._g_hist.popleft()

    def feed_scan(self, t, ranges, angle_min, increment, range_max, range_min=0.):
        self.stats['scans_in'] += 1
        try:
            r, amin, inc, rmax, stats = self.scan_adapter.convert(ranges, angle_min, increment, range_max, range_min)
        except ScanRejected as exc:
            rejected = self.stats['scans_rejected']
            rejected[exc.reason] = rejected.get(exc.reason, 0) + 1
            return dict(accepted=False, reason=exc.reason)
        t = float(t)
        self.last_receipt['scan'] = t
        if not self.is_open():
            return stats
        try:
            self._on_scan(t, r, amin, inc, rmax)
        except Exception as exc:
            self.stats['scan_errors'] += 1
            self._pending_end = self._pending_end or 'internal_error:scan:%s' % type(exc).__name__
        return stats

    def _on_scan(self, t, r, amin, inc, rmax):
        stamp = t - self.st.scan_latency_s
        if self.est.last_scan_stamp is not None and stamp <= self.est.last_scan_stamp:
            return
        self.est.on_scan(stamp, r, amin, inc, rmax)
        if self.acq_est is not None:
            self.acq_est.on_scan(stamp, r, amin, inc, rmax)
        pose = self.est.pose
        heading = float(pose[2])
        if self._lidar_cont is None:
            self._lidar_cont = heading
        else:
            self._lidar_cont += wrap(heading - self._lidar_prev)
        self._lidar_prev = heading
        self._lidar_valid = bool(self.est.odom_valid)
        self._lidar_stamp = stamp
        self.clear_streak = self.clear_streak + 1 if self.est.pivot_clearance >= self.st.clear_radius_m else 0
        if not self.locked:
            return
        self._psi_lidar_scan = self._psi_lidar() if self._lidar_valid else None
        if self._lidar_valid:
            # Same-stamp comparison: the gyro integral at the scan stamp (scan matching lags the gyro).
            d = math.degrees(wrap(self._psi_lidar() - self._psi_gyro_at(stamp)))
            if self._pivot_watch() or self.observe:
                self.disagree_max = max(self.disagree_max, abs(d))
                if abs(d) > self.st.warn_deg:
                    self._warn('yaw_disagree_warn_scans')
                if abs(d) > self.st.abort_deg:
                    if self.observe:
                        self._warn('yaw_disagree_abort_scans')
                    else:
                        self._pending_end = self._pending_end or 'yaw_disagreement'
            if self.lines is not None:
                angles = amin + np.arange(len(r)) * inc
                ok = np.isfinite(r)
                lx, ly = r[ok] * np.cos(angles[ok]), r[ok] * np.sin(angles[ok])
                c, s = math.cos(heading), math.sin(heading)
                ox, oy = pose[0] + c * lx - s * ly, pose[1] + s * lx + c * ly
                xs, ys = self._to_turn(ox, oy)
                bx, by = self._base_turn()
                self.lines.update(xs, ys, bx)

    # --- frames ---------------------------------------------------------------------------------------
    def _base_odom(self):
        pose = self.est.pose
        lx = self.st.lidar_x_in_base_m
        return pose[0] - lx * math.cos(pose[2]), pose[1] - lx * math.sin(pose[2])

    def _to_turn(self, x, y):
        c, s = math.cos(self.psi_row_odom), math.sin(self.psi_row_odom)
        dx, dy = np.asarray(x) - self.origin[0], np.asarray(y) - self.origin[1]
        return c * dx + s * dy, -s * dx + c * dy

    def _base_turn(self):
        bx, by = self._base_odom()
        x, y = self._to_turn(bx, by)
        return float(x), float(y)

    def _psi_gyro(self):
        return self._psi_offset + self._g

    def _psi_gyro_at(self, stamp):
        hist = self._g_hist
        if not hist or stamp >= hist[-1][0]:
            g = self._g
        elif stamp <= hist[0][0]:
            g = hist[0][1]
        else:
            g = self._g
            for k in range(len(hist) - 1, 0, -1):
                t1, g1 = hist[k]
                t0, g0 = hist[k - 1]
                if t0 <= stamp <= t1:
                    g = g0 + (g1 - g0) * (stamp - t0) / max(t1 - t0, 1e-9)
                    break
        return self._psi_offset + g

    def _psi_lidar(self):
        """Scan-matching heading in the turn frame, continuous next to the gyro heading."""
        raw = wrap(self.est.pose[2] - self.psi_row_odom)
        g = self._psi_gyro()
        return g + wrap(raw - g)

    def _psi_ctrl(self):
        """Control heading: the newest valid scan-matching heading, carried to now by the gyro increment
        since its stamp (latency compensation); the gyro alone while scan matching is invalid or old.
        Real 10/10 turns: scan matching p90 2.3 deg vs gyro 4 deg per turn, so the release and the
        heading hold follow the scan matching; the gyro bridges the 10 Hz scans and the gaps."""
        if (self._lidar_valid and self._psi_lidar_scan is not None and self._last_tick is not None
                and self._last_tick - self._lidar_stamp <= LIDAR_CTRL_MAX_AGE_S):
            return self._psi_lidar_scan + (self._psi_gyro() - self._psi_gyro_at(self._lidar_stamp))
        return self._psi_gyro()

    def _anchor_gyro_to_lidar(self):
        """Still between steps: the gyro heading takes the scan-matching heading (end beyond the abort)."""
        if not self._lidar_valid:
            self._warn('anchor_without_lidar')
            return None
        d = math.degrees(wrap(self._psi_lidar() - self._psi_gyro()))
        if abs(d) > self.st.abort_deg:
            return 'yaw_disagreement'
        if abs(d) > self.st.warn_deg:
            self._warn('anchor_disagree_warn')
        self._psi_offset = self._psi_lidar() - self._g
        return None

    def _pivot_watch(self):
        step = self.step
        return (step is not None and step.get('kind') == 'pivot' and step.get('sub') in (BREAKAWAY, ROTATE, COAST)
                and not step.get('judged'))

    # --- tick -----------------------------------------------------------------------------------------
    def tick(self, now, hold_active):
        try:
            now = float(now)
            if not math.isfinite(now):
                raise ValueError('non-finite tick time')     # review fix: ends the run (internal_error), 0/0
            return self._tick(now, hold_active is True)
        except Exception as exc:
            self._end('internal_error:%s' % type(exc).__name__)
            self._last_out = (0, 0)
            return Decision(0, 0, self.phase, self.reason, True, dict(run_id=self.run_id, error=repr(exc)[:200]))

    def _tick(self, now, hold):
        if not self.is_open():
            self._prev_hold = hold
            return self._decision(0, 0, None)
        if self._last_tick is not None and now < self._last_tick:
            self._end('clock_regression')
            return self._decision(0, 0, None)
        if self._pending_end:
            self._end(self._pending_end)
            return self._decision(0, 0, None)
        if self.mode == ACTIVE and self._last_tick is not None and now - self._last_tick > self.st.stall_guard_s:
            self._pause(now, 'stall_guard')
        dt = 0. if self._last_tick is None else now - self._last_tick
        self._last_tick = now
        s = self.est.snapshot(now)
        fresh = s['lidar_age_s'] <= self.st.lidar_max_age_s and s['imu_age_s'] <= self.st.imu_max_age_s
        if not fresh:
            self._fresh_since = None
        elif self._fresh_since is None:
            self._fresh_since = now
        rising = hold and not self._prev_hold
        self._prev_hold = hold
        if rising:
            self.presses += 1
        if not hold:
            self._press_valid = False
        if not self.is_open():
            return self._decision(0, 0, s)
        if self.phase == PRECHECK:
            left, right = self._precheck(now, s, fresh)
        elif self.phase == OBSERVE:
            left, right = self._observe(now, s)
        elif self.phase == SETTLE_ACQUIRE:
            left, right = self._acquire(now, s)
        else:
            left, right = self._motion(now, dt, s, fresh, rising, hold)
        if not self.is_open():
            return self._decision(0, 0, s)
        phase_kind = None
        if self.mode == ACTIVE and self.step is not None:
            phase_kind = PIVOT if self.step['kind'] == 'pivot' else FORWARD
        rate_dps = math.degrees(self._rate)
        out_l, out_r, violation = self.guard.apply(now, int(left), int(right), phase_kind, s.get('still_s', 0.),
                                                   rate_dps)
        if violation is not None:
            self._end('turn_guard_violation:' + violation)
            return self._decision(0, 0, s, dict(raw_left=left, raw_right=right))
        self._proposed = (out_l, out_r)
        self._last_out = (out_l, out_r)
        if not self.motor_output:
            return self._decision(0, 0, s, dict(proposed=[out_l, out_r]))
        return self._decision(out_l, out_r, s)

    def _decision(self, left, right, s, extra=None):
        summary = self._summary(s)
        if extra:
            summary.update(extra)
        terminal = self.phase in (DONE, ENDED)
        return Decision(int(left), int(right), self.phase, self.reason, terminal, summary)

    # --- PRECHECK ----------------------------------------------------------------------------------------
    def _precheck(self, now, s, fresh):
        st = self.st
        unmet = []
        if not fresh:
            unmet.append('inputs_not_fresh')
        if len(self._imu_dts) >= 10 and float(np.median(list(self._imu_dts)[-60:])) > st.imu_dt_median_s:
            unmet.append('imu_rate_low')
        if s.get('still_s', 0.) < st.precheck_still_s:
            unmet.append('not_still')
        vib = s.get('vibration_rms')
        if vib is None or vib > st.precheck_vibration:
            unmet.append('vibration')
        window = [r for tb, r in self._bias_buf if tb >= now - st.precheck_still_s]
        bias = sd = None
        covered = bool(self._bias_buf) and self._bias_buf[0][0] <= now - st.precheck_still_s + .1
        if len(window) < 10 or not covered:
            unmet.append('bias_samples')
        else:
            bias, sd = float(np.mean(window)), float(np.std(window))
            if abs(math.degrees(bias)) > st.bias_max_deg_s or math.degrees(sd) > st.bias_sd_max_deg_s or \
                    math.degrees(sd) / math.sqrt(len(window)) > BIAS_SE_MAX_DEG_S:
                unmet.append('gyro_bias')
        theta = float(self.est.pose[2])
        row = None
        req = self.request
        band = getattr(self.est, 'band', None)
        if s.get('corridor_valid') and s.get('corridor_full_updates', 0) >= 1 and band is not None:
            # band.heading: the tracked row axis in the odometry frame, oriented with travel
            row = (wrap(float(band.heading)), 'corridor', float(s['lateral_offset']), float(s['corridor_width']))
        elif req.row_heading_error_deg is not None:
            row = (wrap(theta + math.radians(req.row_heading_error_deg)), 'row_run_hint', 0., None)
        elif req.operator_aligned:
            row = (theta, 'operator_aligned', 0., None)
        else:
            unmet.append('row_axis_unknown')
        if row is not None and row[3] is not None and abs(row[3] - req.row_width_m) > 1.2:
            # The band is fitted to returns (canopy narrows it); the tape-measured W is used for the
            # geometry (a smaller difference only warns at the lock). A gross difference means a wrong
            # corridor or a wrong input.
            self._end('row_width_mismatch')
            return 0, 0
        if not self.observe:
            vote = (s.get('next_corridor') or {}).get(req.direction)
            if vote == 'ABSENT' and not req.allow_next_absent:
                self._end('next_corridor_absent')
                return 0, 0
        if unmet:
            self.reason = 'precheck:' + unmet[0]
            if now - self.t_start > st.precheck_timeout_s:
                self._end('precheck_timeout:' + unmet[0])
            return 0, 0
        # lock the turn frame and the gyro bias
        self.b0 = bias
        self.bias_sd = sd
        self.psi_row_odom, self.row_source, self.lateral0, self.width_obs = row
        if self.row_source != 'corridor':
            self._warn('row_axis_' + self.row_source)
        if self.width_obs is not None and not _width_ok(self.width_obs, req.row_width_m, st.handoff_width_tol_m):
            self._warn('row_width_differs')
        self.origin = self._base_odom()
        self.locked = True
        self._g = 0.
        self._rate = 0.
        self._g_hist.clear()
        self._psi_offset = wrap(theta - self.psi_row_odom)
        w = req.row_width_m
        self.lines = LineEndTracker(st.line_band_m, st.line_gap_m,
                                    dict(left=-self.lateral0 + w / 2., right=-self.lateral0 - w / 2.))
        self.next_vote = (s.get('next_corridor') or {}).get(req.direction)
        self.coast_s = None
        self._still_ref = (now, self._base_turn())
        if self.observe:
            self.observe_log = dict(rotations=[], creep_ready=None, acquisition=None, peak_abs_heading_deg=0.,
                                    rot=None)
            self._go(OBSERVE, None, 'observing')
        elif req.pivot_only:
            self._go(PIVOT_1, AWAIT, 'await_press')
        else:
            self._go(CREEP_OUT, AWAIT, 'await_press')
        return 0, 0

    # --- motion steps ----------------------------------------------------------------------------------------
    def _motion(self, now, dt, s, fresh, rising, hold):
        st = self.st
        mode = self.mode
        if mode == STOPPING:
            if now - self.step['stop_t'] > st.settle_timeout_s:
                self._end('settle_timeout')
            elif self._settled(s):
                self._finish_step(now, s)
            return 0, 0
        if mode == AWAIT:
            if self._uncommanded(now, s):
                return 0, 0
            if rising and not self._start_pending:
                why = self._press_blocker(now, s, fresh)
                if why is None:
                    self._start_pending = True
                else:
                    self.reason = why
            if not self._start_pending:
                return 0, 0
            if not hold:
                self._start_pending = False
                self.reason = 'await_press'
                return 0, 0
            if self._fade_wait(now):
                # forward <-> pivot: the MCU fade + BREAK of the previous shape must be over (TurnGuard)
                self.reason = 'waiting_fade'
                return 0, 0
            why = self._press_blocker(now, s, fresh)
            if why is not None:
                # Review fix: the press was accepted before the fade wait; inputs that went stale (or a
                # vehicle that moved) during the wait cancel it, and only a NEW press made while the
                # inputs are fresh again starts the step (same rule as a stale pause).
                self._start_pending = False
                self.reason = why
                self._warn('pending_start_cancelled')
                return 0, 0
            self._start_pending = False
            self._press_valid = True
            self.step_pauses = 0
            self._begin_step(now, s)
            if self.mode != ACTIVE:
                self._press_valid = False
                return 0, 0
            return self._step_output(now, dt, s)
        if mode == PAUSED:
            self._step_passive(now, s)
            if not self.is_open() or self.mode != PAUSED:
                return 0, 0
            if self.step.get('settled_in_pause') and self._uncommanded(now, s):
                return 0, 0
            if rising:
                why = self._press_blocker(now, s, fresh)
                if why is None:
                    self._press_valid = True
                    self.mode = ACTIVE
                    self._go(self.phase, ACTIVE, 'resumed')
                    self._resume_step(now, s)
                    if self.mode != ACTIVE or not self.is_open():
                        return 0, 0
                    return self._step_output(now, dt, s)
                self.reason = why
            return 0, 0
        # ACTIVE
        if not hold:
            self._pause(now, 'hold_released')
            return 0, 0
        if not fresh:
            self._pause(now, 'stale_lidar' if s['lidar_age_s'] > st.lidar_max_age_s else 'stale_imu')
            return 0, 0
        return self._step_output(now, dt, s)

    def _fade_wait(self, now):
        shape = FORWARD if self.phase in (CREEP_OUT, TRAVERSE) else PIVOT
        g = self.guard
        if g.last_shape is None or g.last_shape == shape or self._last_out != (0, 0):
            return self._last_out != (0, 0)
        return g.zero_since is None or now - g.zero_since < g.last_duty / 100. + g.shape_change_extra_s + 1e-6

    def _press_blocker(self, now, s, fresh):
        if not fresh or self._fresh_since is None or now - self._fresh_since < self.st.fresh_recover_s:
            return 'press_ignored_inputs_not_fresh'
        if s.get('still_s', 0.) < PRESS_RECOVER_STILL_S:
            return 'press_ignored_not_still'
        return None

    def _settled(self, s):
        vib = s.get('vibration_rms')
        return (s.get('still_s', 0.) >= self.st.settle_still_s and vib is not None
                and vib <= self.st.settle_vibration)

    def _uncommanded(self, now, s):
        """After a confirmed stop with zero output: rotation or translation means a person or a slide."""
        if self._still_ref is None:
            return False
        bx, by = self._base_turn()
        x0, y0 = self._still_ref[1]
        if abs(math.degrees(self._rate)) > self.st.uncommanded_rate_deg_s or \
                math.hypot(bx - x0, by - y0) > self.st.uncommanded_move_m:
            self._end('uncommanded_motion')
            return True
        return False

    def _pause(self, now, why):
        if self.mode != ACTIVE:
            return
        self._press_valid = False
        self.step_pauses += 1
        self.total_pauses += 1
        step = self.step
        if step is not None and step['kind'] == 'pivot' and step['sub'] in (BREAKAWAY, ROTATE):
            step['sub'] = COAST
            step['coast_from_pause'] = True
            step['release_t'] = now
            step['release_psi'] = self._psi_ctrl()
            step['release_rate'] = abs(math.degrees(self._rate))
        if step is not None and step['kind'] == 'forward':
            step['stopping_pause'] = True
        if step is not None:
            step['settled_in_pause'] = False
        self._go(self.phase, PAUSED, why)
        if why == 'stall_guard':
            self.stall_guard_pauses += 1
            if self.stall_guard_pauses > self.st.max_stall_guard_pauses:
                self._end('stall_guard_repeat_limit')
                return
        elif why in ('stale_lidar', 'stale_imu'):
            if why == 'stale_lidar' and step is not None and step['kind'] == 'forward':
                # the scan matcher never invents translation: motion during the gap may be lost
                self._warn('odometry_gap_' + self.phase.lower())
            self.stale_events += 1
            if self.stale_events > self.st.max_stale_events:
                self._end('stale_repeat_limit')
                return
        if self.step_pauses > self.st.max_pauses:
            self._end('pause_limit')

    def _begin_step(self, now, s):
        """New press in AWAIT: preconditions of the step, then ACTIVE."""
        phase = self.phase
        st = self.st
        why = self._anchor_gyro_to_lidar()
        if why:
            self._end(why)
            return
        bx, by = self._base_turn()
        base = dict(t0=now, active_s=0., dist0=float(s.get('distance_m') or 0.), x0=bx, y0=by,
                    odom_bad_since=None, settled_in_pause=False)
        if phase in (CREEP_OUT, TRAVERSE):
            target = 0. if phase == CREEP_OUT else self.s * math.pi / 2
            self.step = dict(base, kind='forward', heading=target, sub='DRIVE')
            if phase == TRAVERSE:
                # next corridor centre in the turn frame (the PRECHECK base was lateral0 left of its centre)
                self.step['y_target'] = -self.lateral0 + self.s * (self.request.row_width_m - self._pivot2_shift())
        else:
            if self.clear_streak < st.clear_scans:
                self._end('pivot_start_clearance')
                return
            if phase == PIVOT_1 and not self.request.pivot_only:
                if s.get('front_clearance_m', math.inf) < self._pivot_front_need():
                    self._end('pivot_front_insufficient')
                    return
            target = self.s * (math.pi / 2 if phase == PIVOT_1 else math.pi)
            self.guard.reset_step()
            self.step = dict(base, kind='pivot', target=target, topups=0, stall_loaded_s=0., pivot_active_s=0.,
                             judged=False)
            self._pivot_start(now, self.st.pivot_start_pct, 'start')
        self._go(phase, ACTIVE, 'step_started')

    def _resume_step(self, now, s):
        step = self.step
        if step['kind'] == 'forward':
            step['stopping_pause'] = False
            return
        if step['sub'] == COAST and not step.get('judged_pending'):
            # resumed before the pause coast settled: wait for it (no output) inside ACTIVE
            return
        action = step.pop('judged_pending', None)
        if action is not None:
            self._pivot_apply_action(now, action)

    def _pivot_front_need(self):
        """Free distance needed ahead of the footprint FRONT at the PIVOT_1 start: the table reach is the
        farthest footprint point from the trunk line with the base at x_c, so beyond the base it is
        reach - x_c; + 0.3 m margin; - half the footprint length (front_clearance_m is measured from the
        footprint front, not from the base centre)."""
        return self.request.reach_m - self.request.x_c_m + .3 - self.config.footprint_length_m / 2.

    def _pivot2_shift(self):
        """Predicted turn-frame y shift of the base during PIVOT_2 from the C6 rotation centre (0 = none)."""
        icr = self.st.icr.get(self.s)
        if icr is None:
            return 0.
        a, b = icr
        theta = self.s * math.pi / 2          # heading at the PIVOT_2 start
        phi = self.s * math.pi / 2            # rotation
        rx = math.cos(theta) * a - math.sin(theta) * b
        ry = math.sin(theta) * a + math.cos(theta) * b
        # displacement of the base centre = (I - R(phi)) * R(theta) r
        dy = ry - (math.sin(phi) * rx + math.cos(phi) * ry)
        return self.s * dy

    def _step_output(self, now, dt, s):
        step = self.step
        if step is None:
            return 0, 0
        step['active_s'] += dt
        if step['kind'] == 'forward':
            return self._forward_tick(now, s)
        return self._pivot_tick(now, dt, s)

    def _step_passive(self, now, s):
        """PAUSED: zero output; let a pivot coast settle and judge; note a settled forward step."""
        step = self.step
        if step is None:
            return
        if step['kind'] == 'pivot' and step['sub'] == COAST and not step.get('judged_pending'):
            self._pivot_coast(now, s, passive=True)
        elif self._settled(s) and not step.get('settled_in_pause'):
            step['settled_in_pause'] = True
            self._still_ref = (now, self._base_turn())
        if step.get('judged_pending') is not None and not step.get('settled_in_pause'):
            step['settled_in_pause'] = True
            self._still_ref = (now, self._base_turn())

    def _finish_step(self, now, s):
        """Step complete and the vehicle still: next phase (SETTLE_ACQUIRE starts by itself)."""
        nxt = NEXT_PHASE[self.phase]
        self._still_ref = (now, self._base_turn())
        if self.request.pivot_only and self.phase == PIVOT_1:
            self.step = None
            self._done('pivot_done')
            return
        self.step = None
        self._press_valid = False
        self._start_pending = False
        if nxt == SETTLE_ACQUIRE:
            why = self._anchor_gyro_to_lidar()
            if why:
                self._end(why)
                return
            self.acq_est = self._new_estimator()
            self.acq_t0 = now
            self.acq_seen_valid = False
            self._go(SETTLE_ACQUIRE, None, 'acquiring')
        else:
            self._go(nxt, AWAIT, 'await_press')

    # --- forward steps (CREEP_OUT / TRAVERSE) --------------------------------------------------------------
    def _forward_tick(self, now, s):
        st, step, req = self.st, self.step, self.request
        v = max(0., float(s.get('speed') or 0.))
        stop_d = st.stop_distance(v)
        front = s.get('front_clearance_m', math.inf)
        bx, by = self._base_turn()
        travelled = float(s.get('distance_m') or 0.) - step['dist0']
        if not s.get('odom_valid'):
            # translation is not invented while scan matching is invalid: the dead-reckoned hand-off
            # (turn_done_ready_odometry) is refused after any such tick (review fix)
            self.forward_odom_invalid_ticks += 1
            step['odom_bad_since'] = step['odom_bad_since'] or now
            if now - step['odom_bad_since'] > 1.:
                self._end('odometry_invalid')
                return 0, 0
        else:
            step['odom_bad_since'] = None
        side_l, side_r = s.get('side_clearance_left_m', math.inf), s.get('side_clearance_right_m', math.inf)
        if self.phase == CREEP_OUT:
            limit_m = req.creep_max_m
            if travelled > limit_m or step['active_s'] > 1.5 * limit_m / st.v_ref_m_s + 2.:
                self._end('creep_backstop')
                return 0, 0
            if front < stop_d + st.front_margin_m:
                self._end('creep_front_blocked')
                return 0, 0
            if min(side_l, side_r) < st.side_hard_m:
                self._end('creep_side_blocked')
                return 0, 0
            if abs(by) > st.side_drift_max_m:
                self._end('creep_side_drift')
                return 0, 0
            line_end = self.lines.line_end(req.direction)
            front_need = self._pivot_front_need() + stop_d
            step['line_end_x'] = line_end
            done = (line_end is not None and bx >= line_end + req.x_c_m and self.clear_streak >= st.clear_scans
                    and front >= front_need)
            if not done:
                # Review fix: while the pivot disc / front is not yet clear the creep went on up to creep_max
                # and could carry the footprint past a boundary the 0.6 m LiDAR plane does not see (ditch,
                # road edge). The tape-measured headland depth bounds it: the base may go at most x_c plus the
                # depth slack (depth - 0.3 - reach) beyond the line end (x = 0, the PRECHECK base, while the
                # line has not been observed). The nominal stop (done) is never later than this bound.
                x_ref = 0. if line_end is None else line_end
                limit_x = x_ref + req.x_c_m + max(0., req.headland_depth_m - .3 - req.reach_m)
                step['headland_limit_x'] = limit_x
                if bx > limit_x:
                    self._end('creep_headland_limit')
                    return 0, 0
        else:
            width = req.row_width_m
            if travelled > 1.3 * width or step['active_s'] > 1.5 * width / st.v_ref_m_s + 2.:
                self._end('traverse_backstop')
                return 0, 0
            if front < stop_d + st.front_margin_m:
                self._end('traverse_front_blocked')
                return 0, 0
            row_side, other = (side_l, side_r) if self.s > 0 else (side_r, side_l)
            if row_side < st.side_min_m or other < st.side_hard_m:
                self._end('traverse_side_blocked')
                return 0, 0
            done = self.s * by + stop_d >= self.s * step['y_target']
        if done:
            step['stop_t'] = now
            self._go(self.phase, STOPPING, 'step_complete')
            return 0, 0
        err = math.degrees(wrap(step['heading'] - self._psi_ctrl()))
        half = st.drive_diff_cap_pct // 2
        u = int(max(-half, min(half, round(st.heading_kp * err))))
        base = st.drive_duty_pct
        return base - u, base + u

    # --- pivots ----------------------------------------------------------------------------------------------
    def _pivot_start(self, now, duty, why):
        step = self.step
        step.update(sub=BREAKAWAY, d=int(duty), d_prev=0, d_since=now, judged=False, coast_from_pause=False,
                    psi0=self._psi_gyro(), lidar0=self._lidar_cont, start_kind=why,
                    tracker=PivotTracker(self.st, self.s, now), release_t=None)
        if self.coast_s is None:
            self.coast_s = self.st.coast_initial(duty)
        rec = dict(phase=self.phase, kind=why, t=round(now - self.t_start, 3), d_start=int(duty),
                   target_deg=round(math.degrees(step['target']), 1),
                   psi0_deg=round(math.degrees(step['psi0']), 2))
        self.pivots.append(rec)
        step['rec'] = rec

    def _lidar_turned(self, step):
        if not self._lidar_valid or step.get('lidar0') is None or self._lidar_cont is None:
            return None
        return self.s * math.degrees(self._lidar_cont - step['lidar0'])

    def _pivot_tick(self, now, dt, s):
        st, step = self.st, self.step
        if step['sub'] == COAST:
            self._pivot_coast(now, s, passive=False)
            if not self.is_open() or self.mode != ACTIVE or self.step is not step:
                return 0, 0
            if step['sub'] == COAST:
                return 0, 0
            return self._pivot_command(now, dt, s)
        return self._pivot_command(now, dt, s)

    def _pivot_command(self, now, dt, s):
        st, step = self.st, self.step
        if self._imu_t is None or now - self._imu_t > st.imu_gap_s:
            self._end('imu_gap_in_pivot')
            return 0, 0
        if s.get('pivot_clearance_m', math.inf) < st.clear_during_m:
            self._end('pivot_clearance_lost')
            return 0, 0
        rate_dps = math.degrees(self._rate)
        step['pivot_active_s'] += dt
        if abs(rate_dps) < st.stall_rate_deg_s:
            step['stall_loaded_s'] += dt
        if step['stall_loaded_s'] > st.stall_loaded_total_s - BUDGET_MARGIN_S:
            self._end('pivot_stall_budget')
            return 0, 0
        if self.guard.pivot_time_s + dt > st.pivot_total_s - BUDGET_MARGIN_S:
            self._end('turn_timeout')
            return 0, 0
        limit = st.pivot_start_pct / 100. + st.grace_s + 1.5 * 90. / 8. + 2.
        if step['pivot_active_s'] > limit:
            self._end('turn_timeout')
            return 0, 0
        tracker = step['tracker']
        turned = self.s * math.degrees(self._psi_gyro() - step['psi0'])
        ramp = (step['d'] - step['d_prev']) / 100.
        event = tracker.update(now, rate_dps, turned, self._lidar_turned(step), step['d'], step['d_since'], ramp,
                               step['d'] >= st.pivot_cap_pct)
        if event is not None:
            step['rec'].update(result=event, stall_kind=tracker.stall_kind, d_end=step['d'])
            self._end(event)
            return 0, 0
        # Review fix (defence in depth): still commanding although the gyro or the scan matching says the
        # rotation this pivot was started for is exceeded by more than the overshoot limit (e.g. a control
        # heading that lags, or a top-up released only after the rotation confirmation): end at once.
        planned = self.s * math.degrees(step['target'] - step['psi0'])
        lidar_turned = self._lidar_turned(step)
        if max(turned, -math.inf if lidar_turned is None else lidar_turned) - planned > st.overshoot_max_deg:
            step['rec'].update(result='pivot_overshoot_commanding', d_end=step['d'])
            self._end('pivot_overshoot')
            return 0, 0
        if not tracker.rotating:
            if now - step['d_since'] >= ramp + st.grace_s - 1e-9 and step['d'] < st.pivot_cap_pct:
                step['d_prev'] = step['d']
                step['d'] = min(st.pivot_cap_pct, step['d'] + st.pivot_step_pct)
                step['d_since'] = now
        else:
            if step['sub'] == BREAKAWAY:
                step['sub'] = ROTATE
                step['rec'].update(d_rot=tracker.d_rotating, t_rot_s=round(tracker.t_rotating - step['t0'], 3))
            remaining = self.s * math.degrees(step['target'] - self._psi_ctrl())
            coast = max(0., self.s * rate_dps) * self.coast_s
            if remaining <= coast:
                step['sub'] = COAST
                step['release_t'] = now
                step['release_psi'] = self._psi_ctrl()
                step['release_rate'] = max(1., self.s * rate_dps)
                step['rec'].update(release_remaining_deg=round(remaining, 2),
                                   release_rate_deg_s=round(self.s * rate_dps, 2),
                                   coast_s_used=round(self.coast_s, 3))
                return 0, 0
        d = step['d']
        return -self.s * d, self.s * d

    def _pivot_coast(self, now, s, passive):
        st, step = self.st, self.step
        if step['release_t'] is not None and now - step['release_t'] > st.settle_timeout_s:
            self._end('settle_timeout')
            return
        if not self._settled(s):
            return
        step['judged'] = True
        psi_g = self._psi_gyro()
        psi_c = self._psi_ctrl()
        agree = None
        if self._lidar_valid:
            agree = math.degrees(wrap(self._psi_lidar() - psi_g))
        if agree is not None and abs(agree) > st.abort_deg:
            self._end('yaw_disagreement')
            return
        if agree is not None:
            psi_end = self._psi_lidar()
            if abs(agree) > st.warn_deg:
                self._warn('judge_disagree_warn')
            gyro_turned = math.degrees(psi_g - step['psi0'])
            if abs(gyro_turned) >= GYRO_GAIN_MIN_TURN_DEG and step.get('lidar0') is not None:
                lidar_turned = math.degrees(self._lidar_cont - step['lidar0'])
                ratio = lidar_turned / gyro_turned
                self.gyro_gain = min(GYRO_GAIN_LIMITS[1], max(GYRO_GAIN_LIMITS[0],
                                                              self.gyro_gain * (.5 + .5 * ratio)))
                step['rec'].update(gyro_gain=round(self.gyro_gain, 4))
        else:
            psi_end = psi_g
            self._warn('judge_gyro_only')
        if step.get('release_t') is not None and not step.get('coast_from_pause') and step.get('release_rate'):
            observed = self.s * math.degrees(psi_c - step['release_psi']) / step['release_rate']
            self.coast_s = min(.8, max(.1, .5 * self.coast_s + .5 * observed))
            step['rec'].update(coast_deg=round(self.s * math.degrees(psi_c - step['release_psi']), 2),
                               coast_s_observed=round(observed, 3))
        err = self.s * math.degrees(psi_end - step['target'])      # + = overshoot
        step['rec'].update(error_deg=round(err, 2), lidar_minus_gyro_deg=None if agree is None else round(agree, 2))
        # the gyro takes the judged heading (scan matching when it agreed)
        self._psi_offset = psi_end - self._g
        action = self._pivot_decide(err, from_pause=step.get('coast_from_pause', False))
        if passive or not self._press_valid:
            step['judged_pending'] = action
            if action[0] in ('done', 'end'):
                self._pivot_apply_action(now, action)
            elif self.mode == ACTIVE:
                self._pause_for_press(now)
            return
        self._pivot_apply_action(now, action)

    def _pause_for_press(self, now):
        """A top-up / resume needs output but the hold was released during the coast: wait for a new press."""
        self.mode = PAUSED
        self._go(self.phase, PAUSED, 'await_new_press')
        self.step['settled_in_pause'] = True
        self._still_ref = (now, self._base_turn())

    def _pivot_decide(self, err, from_pause):
        st, step = self.st, self.step
        if abs(err) <= st.accept_deg:
            return ('done', 'pivot_ok')
        if err > 0:
            if err <= st.overshoot_max_deg:
                self._warn('pivot_overshoot_absorbed')
                return ('done', 'pivot_overshoot_absorbed')
            return ('end', 'pivot_overshoot')
        under = -err
        if from_pause and under > st.topup_max_deg:
            return ('restart', 'resume')
        if under <= st.topup_max_deg and step['topups'] < st.topup_max:
            return ('topup', 'topup')
        return ('end', 'pivot_undershoot')

    def _pivot_apply_action(self, now, action):
        kind, why = action
        step = self.step
        step.pop('judged_pending', None)
        if kind == 'end':
            step['rec'].update(result=why)
            self._end(why)
        elif kind == 'done':
            step['rec'].update(result=why)
            self._finish_step(now, None)
        elif kind == 'topup':
            step['topups'] += 1
            duty = step['tracker'].d_rotating or self.st.pivot_start_pct
            step['rec'].update(result='topup')
            self._pivot_start(now, duty, 'topup')
        else:
            step['rec'].update(result='resume')
            self._pivot_start(now, self.st.pivot_start_pct, 'resume')

    # --- SETTLE_ACQUIRE -------------------------------------------------------------------------------------
    def _acquire(self, now, s):
        if self._uncommanded(now, s):
            return 0, 0
        st, req = self.st, self.request
        if now - self.acq_t0 < st.acquire_still_s:
            self.reason = 'acquiring'
            return 0, 0
        a = self.acq_est.snapshot(now)
        ready = False
        values = dict(corridor_valid=bool(a.get('corridor_valid')), corridor_reason=a.get('corridor_reason'))
        if a.get('corridor_valid'):
            self.acq_seen_valid = True
            heading = math.degrees(a['heading_error'])
            lateral = float(a['lateral_offset'])
            width = float(a['corridor_width'])
            values.update(heading_error_deg=round(heading, 2), lateral_offset_m=round(lateral, 3),
                          corridor_width_m=round(width, 3))
            ready = (abs(heading) <= st.handoff_heading_deg and abs(lateral) <= st.handoff_lateral_m
                     and _width_ok(width, req.row_width_m, st.handoff_width_tol_m))
        bx, by = self._base_turn()
        # Dead-reckoned hand-off from the turn frame (tape-measured W, PRECHECK offset, LiDAR odometry):
        # used only when the standing acquisition does not find the corridor before the timeout.
        lateral_odom = -(by - (-self.lateral0 + self.s * req.row_width_m))     # + = left of centre, facing back
        heading_odom = math.degrees(wrap(self._psi_ctrl() - self.s * math.pi))
        values.update(t_s=round(now - self.acq_t0, 2), turn_x_m=round(bx, 3), turn_y_m=round(by, 3),
                      psi_deg=round(math.degrees(self._psi_gyro()), 2),
                      odometry_lateral_m=round(lateral_odom, 3), odometry_heading_deg=round(heading_odom, 2))
        if ready:
            self.handoff = dict(values, ready=True, source='corridor')
            self._done('turn_done_ready')
        elif now - self.acq_t0 > st.acquire_timeout_s:
            # only when the standing acquisition never saw a corridor (a seen one that fails the limits wins)
            # and the PRECHECK band confirmed W (the dead reckoning is only as good as W and lateral0)
            # Review fix: never after a LiDAR gap or invalid scan matching while driving (translation lost)
            odometry_lost = self.forward_odom_invalid_ticks > 0 or any(k.startswith('odometry_gap_')
                                                                       for k in self.warnings)
            odom_ok = (not self.acq_seen_valid and self.row_source == 'corridor' and self.width_obs is not None
                       and _width_ok(self.width_obs, req.row_width_m, st.handoff_width_tol_m)
                       and not odometry_lost
                       and abs(lateral_odom) <= st.handoff_lateral_m and abs(heading_odom) <= st.handoff_heading_deg)
            self.handoff = dict(values, ready=odom_ok, source='odometry' if odom_ok else None,
                                odometry_lost=odometry_lost)
            self._done('turn_done_ready_odometry' if odom_ok else 'turn_done_not_ready')
        else:
            self.handoff = dict(values, ready=False, source=None)
        return 0, 0

    # --- OBSERVE (T1 shadow while a person turns the vehicle) ----------------------------------------------
    def _observe(self, now, s):
        st, req, log = self.st, self.request, self.observe_log
        bx, by = self._base_turn()
        psi = self._psi_gyro()
        log['peak_abs_heading_deg'] = max(log['peak_abs_heading_deg'], abs(math.degrees(psi)))
        line_end = self.lines.line_end(req.direction)
        front = s.get('front_clearance_m', math.inf)
        if log['creep_ready'] is None and line_end is not None and bx >= line_end + req.x_c_m and \
                self.clear_streak >= st.clear_scans and front >= self._pivot_front_need():
            log['creep_ready'] = dict(t_s=round(now - self.t_start, 2), x_m=round(bx, 3), y_m=round(by, 3),
                                      line_end_x_m=round(line_end, 3), front_m=round(front, 3),
                                      pivot_clear_m=round(s.get('pivot_clearance_m', math.inf), 3))
        rate = math.degrees(self._rate)
        rot = log['rot']
        if rot is None and abs(rate) >= st.rotating_deg_s:
            rot = log['rot'] = dict(t0=now, psi0=psi, sign=1 if rate > 0 else -1, disagree_max=0.,
                                    clear_min=math.inf, vib_max=0.)
            self.disagree_max = 0.
        if rot is not None:
            rot['clear_min'] = min(rot['clear_min'], s.get('pivot_clearance_m', math.inf))
            rot['vib_max'] = max(rot['vib_max'], s.get('vibration_rms') or 0.)
            rot['disagree_max'] = max(rot['disagree_max'], self.disagree_max)
            if s.get('still_s', 0.) >= st.settle_still_s:
                if len(log['rotations']) < 50:
                    log['rotations'].append(dict(
                        t_s=round(rot['t0'] - self.t_start, 2), dur_s=round(now - rot['t0'], 2),
                        turned_deg=round(math.degrees(psi - rot['psi0']), 2),
                        lidar_minus_gyro_max_deg=round(rot['disagree_max'], 2),
                        would_abort=rot['disagree_max'] > st.abort_deg,
                        pivot_clear_min_m=None if not math.isfinite(rot['clear_min']) else round(rot['clear_min'], 3),
                        vib_max=round(rot['vib_max'], 2)))
                log['rot'] = None
        if (log['acquisition'] is None and self.acq_est is None and abs(math.degrees(psi)) >= 150.
                and s.get('still_s', 0.) >= st.acquire_still_s):
            self.acq_est = self._new_estimator()
            self.acq_t0 = now
        if self.acq_est is not None and log['acquisition'] is None and now - self.acq_t0 >= st.acquire_still_s:
            a = self.acq_est.snapshot(now)
            if a.get('corridor_valid'):
                heading, lateral = math.degrees(a['heading_error']), float(a['lateral_offset'])
                log['acquisition'] = dict(t_s=round(now - self.t_start, 2), heading_error_deg=round(heading, 2),
                                          lateral_offset_m=round(lateral, 3),
                                          corridor_width_m=round(float(a['corridor_width']), 3),
                                          ready=abs(heading) <= st.handoff_heading_deg
                                          and abs(lateral) <= st.handoff_lateral_m
                                          and _width_ok(float(a['corridor_width']), req.row_width_m,
                                                        st.handoff_width_tol_m))
            elif now - self.acq_t0 > st.acquire_timeout_s:
                log['acquisition'] = dict(t_s=round(now - self.t_start, 2), ready=False,
                                          corridor_reason=a.get('corridor_reason'))
            if log['acquisition'] is not None:
                self._done('observe_done')
        return 0, 0

    # --- reporting --------------------------------------------------------------------------------------------
    def _summary(self, s):
        out = dict(run_id=self.run_id, mode=self.mode, motor_output=self.motor_output, observe=self.observe,
                   end_reason=self.end_reason, pauses=self.total_pauses, step_pauses=self.step_pauses,
                   stale_events=self.stale_events, presses=self.presses)
        if self.request is not None:
            out['direction'] = self.request.direction
        if self.locked:
            bx, by = self._base_turn()
            out.update(turn_x_m=round(bx, 3), turn_y_m=round(by, 3),
                       psi_ctrl_deg=round(math.degrees(self._psi_ctrl()), 2),
                       psi_gyro_deg=round(math.degrees(self._psi_gyro()), 2),
                       psi_lidar_deg=round(math.degrees(self._psi_lidar()), 2),
                       lidar_valid=self._lidar_valid, rate_deg_s=round(math.degrees(self._rate), 2),
                       row_source=self.row_source, bias_deg_s=round(math.degrees(self.b0), 4),
                       line_end_x_m=None if self.lines is None else self.lines.line_end(self.request.direction),
                       clear_streak=self.clear_streak, coast_s=None if self.coast_s is None else round(self.coast_s, 3))
        step = self.step
        if step is not None:
            out['step'] = dict(kind=step['kind'], sub=step.get('sub'), d=step.get('d'),
                               target_deg=None if 'target' not in step else round(math.degrees(step['target']), 1),
                               topups=step.get('topups'), active_s=round(step['active_s'], 2))
        if s is not None:
            out.update(lidar_age_s=_r(s.get('lidar_age_s')), imu_age_s=_r(s.get('imu_age_s')),
                       still_s=_r(s.get('still_s')), speed_m_s=_r(s.get('speed')),
                       front_clearance_m=_r(s.get('front_clearance_m')),
                       pivot_clearance_m=_r(s.get('pivot_clearance_m')),
                       side_clearance_left_m=_r(s.get('side_clearance_left_m')),
                       side_clearance_right_m=_r(s.get('side_clearance_right_m')),
                       vibration_rms=_r(s.get('vibration_rms')), next_corridor=s.get('next_corridor'))
        if self.handoff is not None:
            out['handoff'] = self.handoff
        if self.warnings:
            out['warnings'] = dict(self.warnings)
        if self.guard is not None:
            out['guard'] = self.guard.summary()
        return out

    def status(self):
        return dict(phase=self.phase, mode=self.mode, reason=self.reason, end_reason=self.end_reason,
                    run_id=self.run_id, motor_output=self.motor_output, observe=self.observe,
                    config_digest=self.config.digest(), turn_feasibility=turn_feasibility(self.config),
                    stats=dict(self.stats, scans_rejected=dict(self.stats['scans_rejected'])),
                    pivots=list(self.pivots), events=list(self.events[-60:]), warnings=dict(self.warnings),
                    handoff=self.handoff, observe_log=_clean(self.observe_log),
                    guard=None if self.guard is None else self.guard.summary(),
                    max_imu_gap_in_pivot_s=round(self.max_imu_gap_watch, 3),
                    yaw_disagree_max_deg=round(self.disagree_max, 2),
                    forward_odom_invalid_ticks=self.forward_odom_invalid_ticks)


def _width_ok(width, row_width, tol):
    """Band width vs the measured W: up to tol wider, up to 2 tol narrower (canopy narrows the band)."""
    return row_width - 2. * tol <= width <= row_width + tol


def _r(value, digits=3):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if math.isfinite(value) else None


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if k != 'rot'}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


# --- on-site parameter changes (bounded; the operator page, no remote support on site) --------------------
TURN_ONSITE_KEYS = ('pivot_duty_start_pct', 'pivot_duty_cap_pct', 'pivot_surface_ok', 'pivot_coast_s_hi',
                    'pivot_coast_s_lo', 'pivot_icr_ahead_left_m', 'pivot_icr_left_left_m',
                    'pivot_icr_ahead_right_m', 'pivot_icr_left_right_m', 'turn_drive_duty_pct')


def turn_onsite_update(file_cfg, current_cfg, params):
    """current_cfg with bounded C6 turn values from the operator page; ConfigError lists every problem.

    Never enables the turn feature (turn_assist_enabled / motor_output_enabled stay as in the file),
    never raises a pivot cap above the file's cap (or above 90 % without the file's acknowledgement)
    and never raises the turn drive duty above the file value (the C3 speed was measured there).
    """
    if not isinstance(params, dict) or not params:
        raise ConfigError(['turn onsite: a non-empty object of %s is required' % (TURN_ONSITE_KEYS,)])
    problems = ['turn onsite: %s is not an on-site parameter' % k for k in sorted(set(params) - set(TURN_ONSITE_KEYS))]
    if current_cfg.turn_assist_enabled != file_cfg.turn_assist_enabled or \
            current_cfg.motor_output_enabled != file_cfg.motor_output_enabled or current_cfg.profile != file_cfg.profile:
        problems.append('turn onsite: current config does not derive from the file config')
    cap = params.get('pivot_duty_cap_pct', current_cfg.pivot_duty_cap_pct)
    file_cap = file_cfg.pivot_duty_cap_pct if file_cfg.pivot_duty_cap_pct is not None else 90
    if isinstance(cap, int) and not isinstance(cap, bool) and cap > file_cap:
        problems.append('turn onsite pivot_duty_cap_pct: at most the file value %d' % file_cap)
    drive = params.get('turn_drive_duty_pct', current_cfg.turn_drive_duty_pct)
    if drive is not None and isinstance(drive, int) and not isinstance(drive, bool) and \
            drive > turn_drive_duty(file_cfg):
        problems.append('turn onsite turn_drive_duty_pct: at most the file value %d' % turn_drive_duty(file_cfg))
    if problems:
        raise ConfigError(problems)
    names = {f.name for f in fields(current_cfg)}
    values = {k: v for k, v in params.items() if k in names}
    return replace(current_cfg, **values).validate()
