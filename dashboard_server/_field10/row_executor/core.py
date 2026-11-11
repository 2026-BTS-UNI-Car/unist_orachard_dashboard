"""Executor core shared by the live node and the offline replay (no ROS, no motor I/O).

Pipeline per tick (single thread, deterministic for a given input order):
  feed_imu / feed_scan (receipt monotonic time t)  ->  vendored SensorEstimator
  tick(now, hold_active)  ->  snapshot(now)  ->  Backstop  ->  hold / freshness rule
                          ->  RowController.step  ->  OutputGuard  ->  Decision(left, right, ...)

Input conversion (same as the validated replay, resume-audit-20261013/sim-review/sim2real/replay.py):
  * scan: angle_min += lidar_yaw_offset_deg (travel axis is -179.08 deg in the scan frame; the
    array is not reordered), wrapped to (-pi, pi]; 0 / NaN / < range_min / >= range_max -> inf;
    near returns inside the per-beam body mask -> inf; the scan is trimmed by a few tail beams
    when n * inc exceeds 2 pi + 1.01 inc (scan_corridor would refuse the whole scan otherwise).
  * IMU: yaw rate = sign * gyro . up, up = normalised EMA (tau 1 s) of the specific force; the
    raw acceleration is passed unchanged (vibration RMS).
  * clocks: every stamp is the receipt time.monotonic(); scan stamp = receipt - scan_latency_s,
    IMU stamp = receipt. Header stamps are diagnostics only (a GNSS time step of the system
    clock can not move the estimator clock).

Run life cycle (one run = one row pass from an operator Start):
  start_run(t, row_len_m)  new estimator, Backstop armed, state PAUSED ('await_hold')
  PAUSED  -> ACTIVE  only on a NEW hold press (rising edge) at a moment when both inputs have
                     been fresh for >= fresh_recover_s. A new RowController is built at every
                     resume (so a pause can never be read as 'drive_no_response').
  ACTIVE  -> PAUSED  hold released, stale input, loop stall (stall_guard) or an external pause();
                     corridor lost (see below).
  any     -> ENDED   terminal controller HOLD (row_end_candidate, front_blocked, side_blocked,
                     corridor_*, drive_no_response, ...), guard violation, backstop, too many
                     stale events / stall-guard pauses, clock regression, operator end_run().
                     An ENDED run never restarts; the operator must Start a new run.

Terminal decisions are never lost (review S1 / F1): the vendored controller decides a terminal stop
(row_end_candidate, front_blocked, side_blocked, drive_no_response, stop_repeat_limit, ...) by
entering STOP_REQUESTED with a pending ('hold' | 'front', reason) and only reaches HOLD after it saw
the vehicle stop (0.6-3 s later). A pause request in that window (hold released, stale input, loop
stall, watchdog) ENDS the run with the pending reason (end_note 'terminal_stop_pending_at_<why>')
instead of pausing, so the next press can not build a fresh controller that drives on. Any other
pending kind except 'wait_corridor' (and STOP_REQUESTED without a pending decision) is treated as
terminal too (fail closed).

Corridor lost (review S2 / F7): the vendored controller would stop, wait in REACQUIRE and drive on by
itself once the corridor is back while the button is still held. The Runner instead PAUSES (reason
'corridor_lost', controller discarded) the moment the controller starts that stop (STOP_REQUESTED with
pending 'wait_corridor'; any REACQUIRE other than the initial 'start' is handled the same way). A
resume then needs a NEW press made while the inputs are fresh AND the estimator reports standstill
(still for still_confirm_s); a press while the vehicle still coasts is ignored
('press_ignored_not_standstill'). The controller's limit is kept: a corridor loss when
max_stops_per_window (3) losses already happened within turn_window_m (5 m) of counted distance ENDS
the run ('corridor_lost_repeat_limit'; the controller's own count would restart with every new
controller).

Bench interlock (review S4): a bench run (wheels off the ground, profile 'bench' only) ENDS at once
('bench_motion_detected') when the estimator reports motion (|speed| > 0.1 m/s, or more than 0.2 m of
distance counted while it reports motion) and ('bench_front_blocked') when the front clearance is
below the stop model (vendored required_front(speed)), in PAUSED as well as ACTIVE.

Rolling inner-wheel threshold (review F2, only with rolling_breakaway_duty_pct measured): while the
vehicle has not been seen moving since the resume (estimator not still, odometry valid, speed >= 0.10
m/s; dropped again at still / invalid odometry / speed < 0.05 m/s) the inner wheel must reach the
static breakaway + margin: the Runner lowers the differential symmetrically (inner raised, outer
lowered, mean kept) before the OutputGuard checks it with the static minimum; once moving the
controller's output passes unchanged and the guard checks the rolling minimum.

Calibration runs (calib_runs.py, field profile, MOTOR only): start_calibration(t, plan) begins a run of kind
'arc' (C5), 'rolldown' (C3b) or 'stop' (C7): a bounded duty table instead of the row controller. Same Start -> press ->
fresh-input rule; the first press starts the pattern and ANY pause after it (hold released, stale input, loop
stall) ENDS the run ('calib_<why>'), so a calibration never restarts from standstill in the middle of its table.
Every output passes the run's own OutputGuard (caps, forward only, explicit minimum duty = measured breakaway or
40 instead of the inner-wheel rule); the run ends on its terminal checks (front stop model, side rule, overspeed,
yaw rate, heading change, no response, odometry, distance/time caps; calib_runs docstring). After a calibration run
ended, every tick hands the snapshot to CalibRun.observe_stop: the stop that follows the run's own 0/0 (C7).

Day-2 review fixes (review_safety_day2):
  N9  repeated short presses can not push a stuck vehicle without end: commanded non-zero output while the sensors see
      no motion (the vendored drive_no_response test: still for still_confirm_s, or odometry valid and speed below
      drive_min_speed_m_s) accumulates over the whole row run, across pauses and new controllers, and is reset only by
      observed motion; above NO_RESPONSE_CUMULATIVE_FACTOR x (mcu_ramp + drive_grace + drive_no_response) = 6 s the run
      ENDS ('drive_no_response_repeat'). (Each new controller restarts its own 3 s test at every resume.)
  N6  bench: while ACTIVE, odometry invalid (LiDAR scan matching failed) for more than BENCH_ODOM_LOST_S ends the run
      ('bench_odometry_lost'): the motion interlock needs scan matching (a featureless place hid 6.7 m of motion).

Stale-input rule (single rule, FEASIBILITY_CRITIQUE H4e): LiDAR older than lidar_max_age_s or
IMU older than imu_max_age_s while ACTIVE -> output 0/0 and PAUSED; motion restarts only after a
NEW hold press made while the inputs have been fresh for fresh_recover_s. Holding the button
through the recovery does not restart anything. The vendored controller's own stale recovery
(REACQUIRE after 1 s fresh + still) is never reached: the controller is not stepped with stale
input, and a recoverable HOLD it might still return is handled by this same rule. More than
max_stale_events stale pauses in one run end it ('stale_repeat_limit').

drive_no_response: the vendored controller declares it ~3 s after the first forward command if
the sensors see no motion (MCU ramp 1 s + grace 1 s + 1 s), then stops (terminal).

Executor-level rules added after the C1 gate (open-loop replay of the real 10/10 records; the
vendored estimator/controller are unchanged, gate/C1_GATE_REPORT.txt section 5):
  NearConfirmFilter (P1)  per run: a return nearer than near_confirm_range_m reaches the estimator
                          only if each of the previous near_confirm_scans processed scans had a return
                          within near_confirm_tol_m (+ tol_step per older scan) in the same rotated frame.
  geometry guard (P3)     while the controller drives with a valid band: |heading_error| >
                          guard_heading_deg -> END 'guard_heading'; |lateral_offset| > corridor_width/2 -
                          footprint_width/2 + guard_lateral_margin_m -> END 'guard_lateral'.
  ScanStamper (P4)        estimator scan stamps on the receipt clock from the header spacing (used by the
                          caller, e.g. executor_main; Runner.feed_scan(stamp=...)).
"""
import collections
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
import sys
import time

import numpy as np

from . import calib_runs as CR
from .guard import Backstop, MotionOdometer, OutputGuard, sensor_reports_motion

VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor')
VENDOR_PACKAGES = ('closed_loop', 'orchard_perception')
IDLE, PAUSED, ACTIVE, ENDED = 'IDLE', 'PAUSED', 'ACTIVE', 'ENDED'
SCAN_MAX_POINTS = 4096          # orchard_perception.scan_corridor CorridorConfig.max_scan_points
SCAN_MAX_RANGE_M = 30.          # scan_corridor refuses range_max > 30
DRIVING_CONTROLLER_STATES = ('FOLLOW', 'REDUCE')
# review S4: a bench run (wheels off the ground) ends when the estimator sees the vehicle move
BENCH_MAX_SPEED_M_S = .1
BENCH_MAX_DISTANCE_M = .2
BENCH_ODOM_LOST_S = 1.          # review_safety_day2 N6
NO_RESPONSE_CUMULATIVE_FACTOR = 2.   # review_safety_day2 N9: x the single-press budget (ramp + grace + no response)
# review F2: sustained-motion detection for the rolling inner-wheel threshold (hysteresis)
ROLLING_ENTER_SPEED_M_S = .10
ROLLING_EXIT_SPEED_M_S = .05
CALIB_KINDS = CR.KINDS          # calibration run kinds 'arc' (C5) / 'rolldown' (C3b), calib_runs.py


class VendorError(ImportError):
    pass


def _inside(path, root):
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:
        return False


def vendor_manifest(vendor_dir=VENDOR_DIR):
    with open(os.path.join(vendor_dir, 'SOURCE_MANIFEST.json'), 'r', encoding='utf-8') as fh:
        return json.load(fh)


def verify_vendor(vendor_dir=VENDOR_DIR):
    """Recompute every vendored file's sha256; VendorError on any mismatch or missing file."""
    manifest = vendor_manifest(vendor_dir)
    if manifest.get('modified') is not False:
        raise VendorError('vendor manifest does not declare unmodified sources')
    if not manifest.get('files'):
        raise VendorError('vendor manifest lists no files')
    for entry in manifest['files']:
        path = os.path.join(vendor_dir, *entry['vendored_path'].split('/'))
        try:
            with open(path, 'rb') as fh:
                data = fh.read()
        except OSError as exc:
            raise VendorError('vendored file missing: %s (%s)' % (entry['vendored_path'], exc)) from None
        if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise VendorError('vendored file modified: %s' % entry['vendored_path'])
    return manifest


def load_vendor():
    """Import the vendored estimator/controller after verifying their hashes; (estimator, controllers)."""
    manifest = verify_vendor()
    for name, module in list(sys.modules.items()):
        if name.split('.')[0] in VENDOR_PACKAGES:
            origin = getattr(module, '__file__', None)
            if origin is None or not _inside(origin, VENDOR_DIR):
                raise VendorError('module %s already imported from outside the vendor dir (%s)' % (name, origin))
    if VENDOR_DIR not in sys.path:
        sys.path.insert(0, VENDOR_DIR)
    import closed_loop.controllers as controllers   # noqa: E402 (vendored, hash-verified)
    import closed_loop.estimator as estimator       # noqa: E402
    import orchard_perception.scan_corridor as corridor   # noqa: E402
    for module in (controllers, estimator, corridor):
        if not _inside(module.__file__, VENDOR_DIR):
            raise VendorError('vendored module resolved outside the vendor dir: %s' % module.__file__)
    estimator.VENDOR_COMMIT = manifest['source_commit']
    return estimator, controllers


def verified_modules(estimator, controllers):
    """Accept ALREADY IMPORTED estimator/controllers modules (e.g. the SIM tree's, for the SIM-in-the-loop
    test where the SIM harness owns the names closed_loop/orchard_perception) only if their files and the
    scan_corridor module the estimator uses are byte-identical to the vendored manifest. Field runs never
    use this: they import the vendored copies through load_vendor()."""
    manifest = verify_vendor()
    want = {e['vendored_path']: e['sha256'] for e in manifest['files']}
    corridor = sys.modules.get(getattr(estimator, 'observe_scan_corridor').__module__)
    for module, key in ((estimator, 'closed_loop/estimator.py'), (controllers, 'closed_loop/controllers.py'),
                        (corridor, 'orchard_perception/scan_corridor.py')):
        path = getattr(module, '__file__', None)
        if path is None:
            raise VendorError('module without a file for %s' % key)
        with open(path, 'rb') as fh:
            if hashlib.sha256(fh.read()).hexdigest() != want[key]:
                raise VendorError('module %s is not byte-identical to the vendored %s' % (path, key))
    estimator.VENDOR_COMMIT = manifest['source_commit']
    return estimator, controllers


def wrap(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


def _finite_or_none(value, digits=4):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if math.isfinite(value) else None


# --- scan ---------------------------------------------------------------------------------------
class ScanRejected(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class ScanAdapter:
    """Raw LaserScan fields -> estimator scan (see module docstring). Never edits the caller's array."""

    def __init__(self, yaw_offset_deg=179.08, body_mask=(), grid_count=3240,
                 grid_angle_min_rad=-3.1415927410125732, grid_increment_rad=.0019398535368964076,
                 mask_limit_m=1.2, range_min_floor_m=.05, max_trim_beams=4):
        self.offset = math.radians(float(yaw_offset_deg))
        if type(grid_count) is not int or grid_count < 3 or not grid_increment_rad > 0:
            raise ValueError('invalid beam grid')
        self.grid_count = grid_count
        self.grid_min = float(grid_angle_min_rad)
        self.grid_inc = float(grid_increment_rad)
        table = np.zeros(grid_count, dtype=np.float64)
        for first, last, max_range in body_mask:
            if not (0 <= first <= last < grid_count and 0 < max_range <= mask_limit_m):
                raise ValueError('invalid body mask segment %r' % ((first, last, max_range),))
            table[first:last + 1] = np.maximum(table[first:last + 1], float(max_range))
        self.mask_table = table
        self.range_min_floor = float(range_min_floor_m)
        self.max_trim = int(max_trim_beams)
        self._index_key = None
        self._index = None

    @classmethod
    def from_config(cls, cfg):
        return cls(cfg.lidar_yaw_offset_deg, cfg.body_mask, cfg.beam_grid_count, cfg.beam_grid_angle_min_rad,
                   cfg.beam_grid_increment_rad, cfg.body_mask_limit_m, cfg.range_min_floor_m,
                   cfg.max_scan_trim_beams)

    def _grid_index(self, n, angle_min, increment):
        key = (n, angle_min, increment)
        if key != self._index_key:
            angles = angle_min + np.arange(n, dtype=np.float64) * increment
            index = np.rint((angles - self.grid_min) / self.grid_inc).astype(np.int64) % self.grid_count
            self._index_key, self._index = key, index
        return self._index

    def convert(self, ranges, angle_min, increment, range_max, range_min=0.):
        try:
            amin, inc, rmax, rmin = float(angle_min), float(increment), float(range_max), float(range_min)
        except (TypeError, ValueError):
            raise ScanRejected('invalid_scan_geometry') from None
        if not all(math.isfinite(v) for v in (amin, inc, rmax, rmin)) or inc <= 0 or abs(amin) > 2 * math.pi:
            raise ScanRejected('invalid_scan_geometry')
        try:
            r = np.array(ranges, dtype=np.float64)
        except (TypeError, ValueError):
            raise ScanRejected('invalid_ranges') from None
        if r.ndim != 1:
            raise ScanRejected('invalid_ranges')
        n = int(r.shape[0])
        if n > SCAN_MAX_POINTS:
            raise ScanRejected('scan_capacity_exceeded')
        limit = 2 * math.pi + inc * 1.01        # the scan_corridor geometry rule
        keep = min(n, int(math.floor(limit / inc)))
        while keep > 0 and keep * inc > limit:
            keep -= 1
        trimmed = n - keep
        if keep < 3:
            raise ScanRejected('too_few_beams')
        if trimmed > self.max_trim:
            raise ScanRejected('invalid_scan_geometry')
        r = r[:keep]
        rmax_eff = min(rmax, SCAN_MAX_RANGE_M)
        rmin_eff = max(rmin, self.range_min_floor)
        if not rmin_eff < rmax_eff:
            raise ScanRejected('invalid_scan_geometry')
        with np.errstate(invalid='ignore'):
            valid = np.isfinite(r) & (r >= rmin_eff) & (r < rmax_eff)
        r[~valid] = np.inf
        mask_r = self.mask_table[self._grid_index(keep, amin, inc)]
        masked = valid & (r <= mask_r)
        r[masked] = np.inf
        stats = dict(beams_in=n, beams=keep, trimmed=trimmed, invalid=int(keep - np.count_nonzero(valid)),
                     masked=int(np.count_nonzero(masked)),
                     near_0p5=int(np.count_nonzero(np.isfinite(r) & (r < .5))))
        return r, wrap(amin + self.offset), inc, rmax_eff, stats


class NearConfirmFilter:
    """C1 gate P1 (same algorithm as replay_adapters.NearTransientFilter, the evaluated variant).

    A return nearer than near_m survives only if EACH of the previous confirm_scans scans (unfiltered
    near returns, newest first) had a return within tol_m + age * tol_step_m of it, in the same rotated
    laser frame. The 10/10 false front stops were one-scan clusters of 5-14 identical near returns.
    Real obstacles persist (0.1 s per scan); a new one is accepted confirm_scans scans later. Edits r in
    place (-> inf) and returns the number removed. One instance per run (state = recent scans).
    """

    def __init__(self, confirm_scans=2, near_m=2., tol_m=.35, tol_step_m=.1):
        from scipy.spatial import cKDTree       # scipy is already an estimator dependency
        self._tree = cKDTree
        self.confirm_scans, self.near_m = int(confirm_scans), float(near_m)
        self.tol_m, self.tol_step_m = float(tol_m), float(tol_step_m)
        self.history = []
        self.removed = 0

    @classmethod
    def from_config(cls, cfg):
        if cfg.near_confirm_scans <= 0:
            return None
        return cls(cfg.near_confirm_scans, cfg.near_confirm_range_m, cfg.near_confirm_tol_m,
                   cfg.near_confirm_tol_step_m)

    def apply(self, r, angle_min, increment):
        idx = np.nonzero(np.isfinite(r) & (r < self.near_m))[0]
        a = angle_min + idx * increment
        pts = np.column_stack((r[idx] * np.cos(a), r[idx] * np.sin(a)))
        removed = 0
        if len(self.history) >= self.confirm_scans and len(idx):
            keep = np.ones(len(idx), dtype=bool)
            for age, prev in enumerate(self.history[:self.confirm_scans]):
                if not len(prev):
                    keep[:] = False
                    break
                d, _ = self._tree(prev).query(pts, distance_upper_bound=self.tol_m + age * self.tol_step_m)
                keep &= np.isfinite(d)
            removed = int((~keep).sum())
            r[idx[~keep]] = np.inf
        # (fewer than confirm_scans scans so far: every near return is kept, as in the evaluated variant)
        self.history.insert(0, pts)
        del self.history[self.confirm_scans:]
        self.removed += removed
        return removed


class ScanStamper:
    """Estimator scan stamps on the receipt (time.monotonic) clock.

    'receipt'        stamp = receipt - latency (WEEKEND_PLAN 1.3).
    'header_offset'  (C1 gate P4) stamp = header + median over the last `window` scans of
                     (receipt - header) - latency, never later than the receipt. The header keeps the
                     driver's regular 0.1 s spacing, so a burst of late deliveries (several scans received
                     within a few ms) does not produce near-zero estimator dt. A system-clock step of the
                     header (GNSS time sync) changes the offset at once: |offset - median| > reset_s
                     restarts the median at the new offset (counted). A missing/non-finite header falls
                     back to receipt - latency (counted).
    Every received scan must be observed (also the ones the caller then drops), in receipt order.
    """

    def __init__(self, mode='header_offset', latency_s=.08, window=50, reset_s=.5):
        if mode not in ('receipt', 'header_offset'):
            raise ValueError('unknown scan stamp mode')
        self.mode, self.latency_s = mode, float(latency_s)
        self.window, self.reset_s = int(window), float(reset_s)
        self.offsets = collections.deque(maxlen=self.window)
        self.counts = dict(stamped=0, resets=0, header_missing=0, capped=0)
        self.last_offset = None

    @classmethod
    def from_config(cls, cfg):
        return cls(cfg.scan_stamp_mode, cfg.scan_latency_s, cfg.scan_stamp_window, cfg.scan_stamp_reset_s)

    def median(self):
        return float(np.median(self.offsets)) if self.offsets else None

    def stamp(self, receipt, header=None):
        receipt = float(receipt)
        self.counts['stamped'] += 1
        if self.mode == 'receipt':
            return receipt - self.latency_s
        try:
            header = None if header is None else float(header)
        except (TypeError, ValueError):
            header = None
        if header is None or not math.isfinite(header):
            self.counts['header_missing'] += 1
            return receipt - self.latency_s
        offset = receipt - header
        self.last_offset = offset
        if self.offsets and abs(offset - self.median()) > self.reset_s:
            self.offsets.clear()
            self.counts['resets'] += 1
        self.offsets.append(offset)
        stamp = header + self.median() - self.latency_s
        if stamp > receipt:
            self.counts['capped'] += 1
            stamp = receipt
        return stamp

    def summary(self):
        median = self.median()
        return dict(mode=self.mode, median_offset_s=None if median is None else round(median, 4),
                    last_offset_s=None if self.last_offset is None else round(self.last_offset, 4), **self.counts)


# --- IMU ----------------------------------------------------------------------------------------
class ImuYawRate:
    """Gravity-projected yaw rate (causal EMA 'up'), identical to the replay's gravity_projected_yaw."""

    def __init__(self, tau_s=1., sign=1):
        if sign not in (-1, 1) or not tau_s > 0:
            raise ValueError('invalid IMU yaw settings')
        self.tau_s, self.sign = float(tau_s), int(sign)
        self.state = None
        self.last_t = None
        self.rejected = 0

    def update(self, t, gyro_xyz, acc_xyz):
        """(yaw_rate [rad/s], (ax, ay, az)) or None for an unusable sample (counted in .rejected)."""
        try:
            t = float(t)
            g = tuple(float(v) for v in gyro_xyz)
            a = tuple(float(v) for v in acc_xyz)
        except (TypeError, ValueError):
            self.rejected += 1
            return None
        if len(g) != 3 or len(a) != 3 or not all(math.isfinite(v) for v in g + a + (t,)):
            self.rejected += 1
            return None
        if self.last_t is not None and t < self.last_t:
            self.rejected += 1
            return None
        dt = .05 if self.state is None else min(.2, max(1e-3, t - self.last_t))
        if self.state is None:
            self.state = list(a)
        k = 1 - math.exp(-dt / self.tau_s)
        self.state = [s + (v - s) * k for s, v in zip(self.state, a)]
        norm = math.sqrt(sum(v * v for v in self.state))
        self.last_t = t
        if not norm > 1e-6:
            self.rejected += 1
            return None
        yaw = self.sign * sum(gv * sv for gv, sv in zip(g, self.state)) / norm
        return yaw, a


# --- runner -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Decision:
    left: int
    right: int
    state: str          # Runner state: IDLE / PAUSED / ACTIVE / ENDED
    reason: str
    terminal: bool      # True once the run has ended (ENDED)
    summary: dict = field(default_factory=dict)


class Runner:
    """Not thread-safe: the caller serialises feed_*/tick/pause/end_run (one lock in the node)."""

    def __init__(self, config, estimator_factory=None, controller_factory=None, vendor_modules=None):
        config.validate()
        self.config = config
        estimator, controllers = load_vendor() if vendor_modules is None else verified_modules(*vendor_modules)
        self.estimator_config = estimator.EstimatorConfig(**config.estimator_overrides())
        mode, overrides = config.controller_settings()
        self.controller_config = controllers.config_for(mode, **overrides)
        if self.controller_config.mode != 'continuous' or self.controller_config.pivot_failure_fallback:
            raise ValueError('controller must be the forward-only continuous mode')
        if self.controller_config.side_clearance_check != (mode == 'continuous_side'):
            raise ValueError('side clearance rule does not match the configured mode')
        self.terminal_holds = tuple(controllers.TERMINAL_HOLDS)
        self.recoverable_holds = tuple(controllers.RECOVERABLE_HOLDS)
        # The vendored stop model (stopping_distance / required_front) for the bench interlock and the
        # backstop report; this instance is never stepped.
        self.stop_model = controllers.RowController(self.controller_config)
        self.vendor_commit = estimator.VENDOR_COMMIT
        self._new_estimator = estimator_factory or (lambda: estimator.SensorEstimator(self.estimator_config))
        self._new_controller = controller_factory or (lambda: controllers.RowController(self.controller_config))
        self.scan_adapter = ScanAdapter.from_config(config)
        self.imu = ImuYawRate(config.imu_up_tau_s, config.imu_yaw_sign)
        self.guard = OutputGuard.from_config(config)
        self.stats = dict(imu_in=0, imu_rejected=0, imu_stamp_drop=0, scans_in=0, scans_rejected={},
                          scan_stamp_drop=0, last_scan_proc_s=None, max_scan_proc_s=0.)
        self.run_id = 0
        self.state = IDLE
        self.run_kind = None
        self.estimator = None
        self.near_filter = None
        self.controller = None
        self.backstop = None
        self.calib = None
        self.last_snapshot = None
        self.reason = 'no_run'
        self.end_reason = None
        self.end_note = None
        self.last_receipt = dict(scan=None, imu=None)
        self._reset_run_state()

    # --- run state ----------------------------------------------------------------------------
    def _reset_run_state(self):
        self._prev_hold = True          # a press must be seen AFTER the run starts
        self._fresh_since = None
        self._last_tick = None
        self._last_output = (0, 0)
        self._last_imu_stamp = None
        self._last_scan_stamp = None
        self.stale_events = 0
        self.stall_guard_pauses = 0
        self.pauses = 0
        self.resumes = 0
        self.controllers_built = 0
        self.last_command = None
        self.last_violation = None
        self._bench_elapsed = 0.
        self._bench_last = None
        self._bench_odometer = None
        self._controller_t0 = None
        # review fixes (Fix A)
        self._press_needs_standstill = False      # corridor-lost pause: a resume press needs standstill
        self._corridor_lost_at = []               # counted distance at every corridor-lost pause of the run
        self.corridor_lost_pauses = 0
        self._rolling = False                     # sustained motion since the resume (rolling threshold)
        self.inner_limited_ticks = 0
        # day-2 review fixes
        self.no_motion_drive_s = 0.               # N9: commanded output without observed motion, whole run
        self._bench_odom_bad_since = None         # N6

    def _begin(self, t, kind):
        self.run_id += 1
        self.run_kind = kind
        self.estimator = self._new_estimator()
        self.near_filter = NearConfirmFilter.from_config(self.config)
        self.controller = None
        self.calib = None
        self.last_snapshot = None
        self.end_reason = None
        self.end_note = None
        self._reset_run_state()
        self._last_tick = float(t)
        self.state, self.reason = PAUSED, 'await_hold'
        return self.run_id

    def start_run(self, t, row_len_m):
        if self.state in (PAUSED, ACTIVE):
            raise RuntimeError('run_in_progress')
        backstop = Backstop.from_config(self.config)
        backstop.start(t, 0., row_len_m, self.config.v_measured_m_s)   # raises before any state change
        run_id = self._begin(t, 'row')
        self.backstop = backstop
        return run_id

    def start_bench(self, t):
        if self.config.profile != 'bench' or not self.config.bench_pattern:
            raise RuntimeError('bench_pattern_requires_bench_profile')
        if self.state in (PAUSED, ACTIVE):
            raise RuntimeError('run_in_progress')
        self.backstop = None
        return self._begin(t, 'bench')

    def start_calibration(self, t, plan):
        """A calibration run (calib_runs.CalibPlan from make_plan; field profile only)."""
        if self.config.profile != 'field':
            raise RuntimeError('calibration_needs_field_profile')
        if not isinstance(plan, CR.CalibPlan) or plan.kind not in CALIB_KINDS:
            raise RuntimeError('calibration_plan_required')
        if self.state in (PAUSED, ACTIVE):
            raise RuntimeError('run_in_progress')
        self.backstop = None
        run_id = self._begin(t, plan.kind)
        self.calib = CR.CalibRun(plan, self.stop_model, self.controller_config)
        return run_id

    def _end(self, reason, note=None):
        pending = self._pending_terminal()
        if note is None and pending is not None and pending != reason:
            note = 'controller_pending:' + pending       # e.g. a backstop ended the run during a terminal stop
        self.state, self.reason, self.end_reason = ENDED, reason, reason
        self.end_note = note
        self.controller = None
        if self.calib is not None:
            self.calib.end(reason)          # idempotent: keeps a reason the run decided itself
        self._last_output = (0, 0)

    def end_run(self, t, why='operator_end'):
        if self.state in (PAUSED, ACTIVE):
            self._end(str(why))

    def _pending_terminal(self):
        """The reason of a terminal stop the controller has decided but not yet completed, else None.

        STOP_REQUESTED with pending ('hold' | 'front', reason): terminal ('front' ends in HOLD front_blocked in
        the continuous modes). 'wait_corridor' is the corridor-lost stop (handled as a pause). Anything else,
        including a missing pending decision, is treated as terminal (fail closed).
        """
        c = self.controller
        if c is None or getattr(c, 'state', None) != 'STOP_REQUESTED':
            return None
        pending = getattr(c, 'pending', None)
        if not pending:
            return 'stop_without_decision'
        kind, reason = pending[0], pending[1]
        if kind == 'wait_corridor':
            return None
        return str(reason)

    def _pause(self, why):
        if self.state != ACTIVE:
            return
        if self.run_kind in CALIB_KINDS:
            # a calibration run never resumes mid-table: any pause ends it (the results so far are kept)
            self._end('calib_' + str(why))
            return
        pending = self._pending_terminal()
        if pending is not None:
            # review S1 / F1: a terminal decision outranks any pause (never PAUSED, never lost)
            self._end(pending, note='terminal_stop_pending_at_' + str(why))
            return
        self.state, self.reason = PAUSED, why
        self.controller = None
        self._last_output = (0, 0)
        self.pauses += 1
        if why == 'stall_guard':
            self.stall_guard_pauses += 1
            if self.stall_guard_pauses > self.config.max_stall_guard_pauses:
                self._end('stall_guard_repeat_limit')
        elif why in ('stale_lidar', 'stale_imu'):
            self.stale_events += 1
            if self.stale_events > self.config.max_stale_events:
                self._end('stale_repeat_limit')

    def pause(self, t, why):
        """External pause (e.g. the loop-stall watchdog with why='stall_guard')."""
        self._pause(str(why))

    def _resume(self, now):
        self.state, self.reason = ACTIVE, 'resumed'
        self.resumes += 1
        self._press_needs_standstill = False
        self._rolling = False
        if self.run_kind == 'row':
            self.controller = self._new_controller()
            self._controller_t0 = now
            self.controllers_built += 1
        elif self.run_kind in CALIB_KINDS:
            self.calib.begin(now)
        else:
            self._bench_last = now

    # --- inputs -------------------------------------------------------------------------------
    def _run_open(self):
        # The estimator keeps running after the run ENDED (until the next Start replaces it), so the stop
        # after an end decision is observed (stillness for the bridge stop, overrun distance in the log).
        return self.state in (PAUSED, ACTIVE, ENDED) and self.estimator is not None

    def feed_imu(self, t, gyro_xyz, acc_xyz):
        self.stats['imu_in'] += 1
        out = self.imu.update(t, gyro_xyz, acc_xyz)
        if out is None:
            self.stats['imu_rejected'] += 1
            return False
        self.last_receipt['imu'] = float(t)
        if not self._run_open():
            return True
        if self._last_imu_stamp is not None and t <= self._last_imu_stamp:
            self.stats['imu_stamp_drop'] += 1
            return False
        self._last_imu_stamp = float(t)
        self.estimator.on_imu(float(t), float(out[0]), out[1])
        return True

    def feed_scan(self, t, ranges, angle_min, increment, range_max, range_min=0., stamp=None):
        """t = receipt time; stamp = estimator stamp (default t - scan_latency_s, see ScanStamper)."""
        self.stats['scans_in'] += 1
        try:
            r, amin, inc, rmax, stats = self.scan_adapter.convert(ranges, angle_min, increment, range_max, range_min)
        except ScanRejected as exc:
            rejected = self.stats['scans_rejected']
            rejected[exc.reason] = rejected.get(exc.reason, 0) + 1
            return dict(accepted=False, reason=exc.reason)
        self.last_receipt['scan'] = float(t)
        stats['accepted'] = True
        if not self._run_open():
            return stats
        stamp = float(t) - self.config.scan_latency_s if stamp is None else float(stamp)
        if not math.isfinite(stamp) or stamp > float(t):
            self.stats['scan_stamp_drop'] += 1
            stats['accepted'] = False
            stats['reason'] = 'stamp_invalid'
            return stats
        if self._last_scan_stamp is not None and stamp <= self._last_scan_stamp:
            self.stats['scan_stamp_drop'] += 1
            stats['accepted'] = False
            stats['reason'] = 'stamp_not_increasing'
            return stats
        self._last_scan_stamp = stamp
        c0 = time.perf_counter()
        if self.near_filter is not None:
            stats['near_unconfirmed'] = self.near_filter.apply(r, amin, inc)
        self.estimator.on_scan(stamp, r, amin, inc, rmax)
        proc = time.perf_counter() - c0
        self.stats['last_scan_proc_s'] = proc
        self.stats['max_scan_proc_s'] = max(self.stats['max_scan_proc_s'], proc)
        stats['proc_s'] = proc
        return stats

    # --- tick ---------------------------------------------------------------------------------
    def _decision(self, left, right, snapshot=None, extra=None):
        summary = self._summary(snapshot)
        if extra:
            summary.update(extra)
        return Decision(int(left), int(right), self.state, self.reason, self.state == ENDED, summary)

    def tick(self, now, hold_active):
        decision = self._tick(now, hold_active)
        if self.calib is not None and self.state == ENDED and self.last_snapshot is not None:
            self.calib.observe_stop(float(now), self.last_snapshot)      # C7: the stop after the run's 0/0
            if decision.summary.get('calib') is not None:
                decision.summary['calib'] = self.calib.status()
        return decision

    def _tick(self, now, hold_active):
        now = float(now)
        hold = hold_active is True
        if self.state == ENDED and self.estimator is not None:
            self._prev_hold = hold
            if self._last_tick is None or now >= self._last_tick:
                self._last_tick = now
                self.last_snapshot = self.estimator.snapshot(now)
            return self._decision(0, 0, self.last_snapshot)
        if self.state in (IDLE, ENDED) or self.estimator is None:
            self._prev_hold = hold
            return self._decision(0, 0)
        if self._last_tick is not None and now < self._last_tick:
            self._end('clock_regression')
            return self._decision(0, 0)
        if self.state == ACTIVE and self._last_tick is not None and now - self._last_tick > self.config.stall_guard_s:
            self._pause('stall_guard')
        dt_tick = 0. if self._last_tick is None else max(0., now - self._last_tick)
        self._last_tick = now
        s = self.estimator.snapshot(now)
        self.last_snapshot = s
        fresh = (s['lidar_age_s'] <= self.config.lidar_max_age_s and s['imu_age_s'] <= self.config.imu_max_age_s)
        if not fresh:
            self._fresh_since = None
        elif self._fresh_since is None:
            self._fresh_since = now
        if self.state == ENDED:
            return self._decision(0, 0, s)
        if self.run_kind == 'bench':
            why = self._bench_interlock(s, now)
            if why is not None:
                self._prev_hold = hold
                self._end(why)
                return self._decision(0, 0, s)
        if self.backstop is not None:
            moving = self._last_output != (0, 0) or not s.get('still', False)
            reason = self.backstop.check(now, s.get('distance_m'), moving, sensor_reports_motion(s))
            if reason is not None:
                self._end(reason)
                return self._decision(0, 0, s)
        rising = hold and not self._prev_hold
        self._prev_hold = hold
        if self.state == PAUSED and rising:
            if not (fresh and now - self._fresh_since >= self.config.fresh_recover_s):
                # Ignored: the operator must release and press again once the inputs are fresh.
                self.reason = 'press_ignored_inputs_not_fresh'
            elif self._press_needs_standstill and not self._standstill(s):
                # corridor-lost pause: the press must come after the vehicle was seen at rest
                self.reason = 'press_ignored_not_standstill'
            else:
                self._resume(now)
        if self.state == PAUSED:
            self._last_output = (0, 0)
            return self._decision(0, 0, s)
        # ACTIVE
        if not hold:
            self._pause('hold_released')
            return self._decision(0, 0, s)
        if not fresh:
            self._pause('stale_lidar' if s['lidar_age_s'] > self.config.lidar_max_age_s else 'stale_imu')
            return self._decision(0, 0, s)
        rolling = False
        guard = self.guard
        if self.run_kind in CALIB_KINDS:
            left, right, why = self.calib.step(now, s)
            if why is not None:
                self._end(why)
                return self._decision(0, 0, s)
            ctrl_state, ctrl_reason = 'CALIB', 'calib_%s_%s' % (self.run_kind, self.calib.phase)
            guard = self.calib.guard        # explicit minimum duty instead of the inner-wheel rule
        elif self.run_kind == 'bench':
            left, right, why = self._bench_command(now)
            if why is not None:
                self._end(why)
                return self._decision(0, 0, s)
            ctrl_state, ctrl_reason = 'BENCH', 'bench_pattern'
        else:
            # The vendored RowController starts with since = 0.0 (SIM episodes start at t = 0):
            # stepped on the raw monotonic clock its REACQUIRE timeout (6 s) would fire at once.
            # It therefore runs on a clock that starts at 0 at its construction (resume). The
            # snapshot holds only ages/durations, so nothing else depends on the time origin.
            command = self.controller.step(now - self._controller_t0, s)
            self.last_command = command
            ctrl_state, ctrl_reason = command.state, command.reason
            ctrl = dict(controller_state=ctrl_state, controller_reason=ctrl_reason)
            if command.state == 'HOLD':
                if command.reason in self.recoverable_holds:
                    self._pause(command.reason)      # same single stale rule
                else:
                    self._end(command.reason)        # terminal holds (and unknown reasons) end the run
                return self._decision(0, 0, s, ctrl)
            left, right = command.left, command.right
            if (left or right) and command.state not in DRIVING_CONTROLLER_STATES:
                self.last_violation = 'output_in_non_driving_state'
                self._end('guard_violation:output_in_non_driving_state')
                return self._decision(0, 0, s)
            if self._controller_waits_for_corridor(command):
                self._corridor_lost(s, command)
                return self._decision(0, 0, s, ctrl)
            if command.state in DRIVING_CONTROLLER_STATES:
                why = self.geometry_guard(s)
                if why is not None:
                    self._end(why)
                    return self._decision(0, 0, s, dict(ctrl, raw_left=left, raw_right=right))
                rolling = self._update_rolling(s)
                left, right = self._inner_wheel_limit(left, right, rolling)
            why = self._no_response_budget(s, dt_tick)
            if why is not None:
                self._end(why)
                return self._decision(0, 0, s, ctrl)
        out_left, out_right, violation = guard.apply(left, right, rolling=rolling)
        if violation is not None:
            if guard is not self.guard:
                self.guard.violations += 1
            self.last_violation = violation
            self._end('guard_violation:' + violation)
            return self._decision(0, 0, s, dict(raw_left=_int_or_repr(left), raw_right=_int_or_repr(right)))
        self.reason = ctrl_reason
        self._last_output = (out_left, out_right)
        return self._decision(out_left, out_right, s, dict(controller_state=ctrl_state, controller_reason=ctrl_reason,
                                                           rolling=rolling))

    def geometry_guard(self, s):
        """C1 gate P3: None, 'guard_heading' or 'guard_lateral' for a driving tick with a valid band."""
        cfg = self.config
        if not s.get('corridor_valid'):
            return None
        heading, lateral, width = s.get('heading_error'), s.get('lateral_offset'), s.get('corridor_width')
        if cfg.guard_heading_deg is not None and heading is not None and math.isfinite(heading):
            if abs(math.degrees(heading)) > cfg.guard_heading_deg:
                return 'guard_heading'
        if (cfg.guard_lateral_margin_m is not None and lateral is not None and width is not None
                and math.isfinite(lateral) and math.isfinite(width)):
            half = .5 * width - .5 * cfg.footprint_width_m
            if abs(lateral) > half + cfg.guard_lateral_margin_m:
                return 'guard_lateral'
        return None

    # --- review fixes (Fix A) --------------------------------------------------------------------
    def _standstill(self, s):
        """Sensor-observed stillness for the controller's still_confirm_s (missing still_s = not still)."""
        still_s = s.get('still_s', 0.)
        return (s.get('still') is True and isinstance(still_s, (int, float)) and math.isfinite(still_s)
                and still_s >= self.controller_config.still_confirm_s)

    def _controller_waits_for_corridor(self, command):
        """True when the controller started its corridor-lost stop (STOP_REQUESTED, pending 'wait_corridor') or is in
        any REACQUIRE other than the initial 'start' (from which it would drive on without a new press)."""
        if command.state == 'STOP_REQUESTED':
            pending = getattr(self.controller, 'pending', None)
            return bool(pending) and pending[0] == 'wait_corridor'
        return command.state == 'REACQUIRE' and command.reason != 'start'

    def _corridor_lost(self, s, command):
        """review S2 / F7: pause (a NEW press after standstill resumes) or end at the repeat limit."""
        cfg = self.controller_config
        position = self.backstop.travelled_m if self.backstop is not None else float(s.get('distance_m') or 0.)
        recent = [p for p in self._corridor_lost_at if position - p <= cfg.turn_window_m]
        self._corridor_lost_at.append(position)
        self.corridor_lost_pauses += 1
        if len(recent) >= cfg.max_stops_per_window:
            self._end('corridor_lost_repeat_limit',
                      note='%d corridor losses within %g m' % (len(recent) + 1, cfg.turn_window_m))
            return
        why = 'corridor_lost' if command.state == 'STOP_REQUESTED' or command.reason == 'corridor_lost_wait' \
            else 'reacquire_needs_press'
        self._pause(why)                # the pending 'wait_corridor' is dropped with the controller
        self._press_needs_standstill = True

    def _no_response_budget(self, s, dt):
        """review_safety_day2 N9: None, or 'drive_no_response_repeat' when the commanded output (previous tick non-zero)
        has seen no motion for more than the cumulative budget since the last observed motion (whole run)."""
        cc = self.controller_config
        speed = s.get('speed')
        still = s.get('still') is True and isinstance(s.get('still_s'), (int, float)) and s['still_s'] >= cc.still_confirm_s
        slow = (s.get('odom_valid', True) is True and isinstance(speed, (int, float)) and math.isfinite(speed)
                and speed < cc.drive_min_speed_m_s)
        if not (still or slow):
            self.no_motion_drive_s = 0.
            return None
        if self._last_output != (0, 0):
            self.no_motion_drive_s += dt
        budget = NO_RESPONSE_CUMULATIVE_FACTOR * (cc.mcu_ramp_s + cc.drive_grace_s + cc.drive_no_response_s)
        if self.no_motion_drive_s > budget:
            return 'drive_no_response_repeat'
        return None

    def _bench_interlock(self, s, now=None):
        """review S4: None, 'bench_motion_detected', 'bench_odometry_invalid' or 'bench_front_blocked';
        day-2 N6: 'bench_odometry_lost' (ACTIVE and scan matching invalid for more than BENCH_ODOM_LOST_S)."""
        if self.state == ACTIVE and now is not None and s.get('odom_valid') is not True:
            if self._bench_odom_bad_since is None:
                self._bench_odom_bad_since = float(now)
            elif float(now) - self._bench_odom_bad_since > BENCH_ODOM_LOST_S:
                return 'bench_odometry_lost'
        else:
            self._bench_odom_bad_since = None
        if self._bench_odometer is None:
            distance0 = s.get('distance_m')
            try:
                self._bench_odometer = MotionOdometer(0. if distance0 is None else distance0)
            except (TypeError, ValueError):
                return 'bench_odometry_invalid'
        if not self._bench_odometer.update(s.get('distance_m', self._bench_odometer.last_m),
                                           sensor_reports_motion(s)):
            return 'bench_odometry_invalid'
        speed = s.get('speed', 0.)
        speed = 0. if speed is None else speed
        if not (isinstance(speed, (int, float)) and math.isfinite(speed)) or abs(speed) > BENCH_MAX_SPEED_M_S:
            return 'bench_motion_detected'
        if self._bench_odometer.counted_m > BENCH_MAX_DISTANCE_M:
            return 'bench_motion_detected'
        front = s.get('front_clearance_m')
        if front is not None and not front >= self.stop_model.required_front(max(0., speed)):
            return 'bench_front_blocked'
        return None

    def _update_rolling(self, s):
        """review F2: sustained motion since the resume, with hysteresis (fail-safe: anything odd = not rolling)."""
        speed = s.get('speed')
        ok = (s.get('still') is False and s.get('odom_valid', True) is True
              and isinstance(speed, (int, float)) and math.isfinite(speed))
        if not ok:
            self._rolling = False
        elif self._rolling:
            self._rolling = speed >= ROLLING_EXIT_SPEED_M_S
        else:
            self._rolling = speed >= ROLLING_ENTER_SPEED_M_S
        return self._rolling

    def _inner_wheel_limit(self, left, right, rolling):
        """review F2 (only when a rolling threshold is configured): before sustained motion the inner wheel must
        reach the static breakaway + margin; lower the differential symmetrically (sum kept). Anything that does
        not fit (non-int, negative, mean below the minimum) is left to the OutputGuard."""
        if self.guard.rolling_inner_min_pct is None or rolling:
            return left, right
        need = self.guard.inner_min_pct
        if (need is None or type(left) is not int or type(right) is not int or left < 0 or right < 0
                or not (left or right) or min(left, right) >= need or left + right < 2 * need):
            return left, right
        self.inner_limited_ticks += 1
        total = left + right
        return (need, total - need) if left < right else (total - need, need)

    def _bench_command(self, now):
        steps = self.config.bench_pattern
        if self._bench_last is not None:
            self._bench_elapsed += max(0., now - self._bench_last)
        self._bench_last = now
        elapsed = 0.
        for duration, left, right in steps:
            elapsed += duration
            if self._bench_elapsed < elapsed:
                return left, right, None
        return 0, 0, 'bench_pattern_complete'

    # --- reporting ----------------------------------------------------------------------------
    def _summary(self, s):
        out = dict(run_id=self.run_id, run_kind=self.run_kind, stale_events=self.stale_events,
                   stall_guard_pauses=self.stall_guard_pauses, pauses=self.pauses, resumes=self.resumes,
                   controllers_built=self.controllers_built, corridor_lost_pauses=self.corridor_lost_pauses)
        if self.end_note:
            out['end_note'] = self.end_note
        if self.inner_limited_ticks:
            out['inner_limited_ticks'] = self.inner_limited_ticks
        if self.no_motion_drive_s:
            out['no_motion_drive_s'] = round(self.no_motion_drive_s, 2)
        if self._fresh_since is not None and self._last_tick is not None:
            out['fresh_for_s'] = round(self._last_tick - self._fresh_since, 3)
        if self.backstop is not None:
            out['backstop'] = self.backstop_summary()
        if self.calib is not None:
            out['calib'] = self.calib.status()
            if self.state == ACTIVE and self.calib.last_tick:
                out['calib_tick'] = dict(self.calib.last_tick)
        if self.last_violation:
            out['guard_violation'] = self.last_violation
        if s is None:
            return out
        side_l = s.get('side_clearance_left_m')
        side_r = s.get('side_clearance_right_m')
        heading = s.get('heading_error')
        out.update(lidar_age_s=_finite_or_none(s.get('lidar_age_s'), 3),
                   imu_age_s=_finite_or_none(s.get('imu_age_s'), 3),
                   still=bool(s.get('still')), speed_m_s=_finite_or_none(s.get('speed'), 3),
                   distance_m=_finite_or_none(s.get('distance_m'), 3),
                   yaw_rate_deg_s=_finite_or_none(math.degrees(s.get('yaw_rate', 0.)), 3),
                   corridor_valid=bool(s.get('corridor_valid')), corridor_reason=s.get('corridor_reason'),
                   lateral_offset_m=_finite_or_none(s.get('lateral_offset'), 3),
                   heading_error_deg=None if heading is None else _finite_or_none(math.degrees(heading), 2),
                   corridor_width_m=_finite_or_none(s.get('corridor_width'), 3),
                   row_end_ahead_m=_finite_or_none(s.get('row_end_ahead_m'), 3),
                   front_clearance_m=_finite_or_none(s.get('front_clearance_m'), 3),
                   side_clearance_left_m=_finite_or_none(side_l, 3),
                   side_clearance_right_m=_finite_or_none(side_r, 3),
                   vibration_rms=_finite_or_none(s.get('vibration_rms'), 3))
        if self.controller is not None:
            out['controller_state'] = self.controller.state
            out['controller_reason'] = self.controller.reason
        return out

    def backstop_summary(self):
        """Backstop.summary() + expected_rest_m: where the vehicle comes to rest after the distance rule ends the
        run, from the Start pose = limit + vendored stopping distance at v_measured_m_s (None if unknown)."""
        out = self.backstop.summary()
        v = self.config.v_measured_m_s
        out['expected_rest_m'] = (None if v is None or out.get('limit_m') is None
                                  else round(out['limit_m'] + self.stop_model.stopping_distance(v), 3))
        return out

    def status(self):
        return dict(state=self.state, reason=self.reason, end_reason=self.end_reason, end_note=self.end_note,
                    run_id=self.run_id,
                    run_kind=self.run_kind, config_digest=self.config.digest(),
                    vendor_commit=self.vendor_commit, feasibility=self.config.feasibility(),
                    stats=dict(self.stats, scans_rejected=dict(self.stats['scans_rejected'])),
                    guard_violations=self.guard.violations)


def _int_or_repr(value):
    return value if type(value) is int else repr(value)
