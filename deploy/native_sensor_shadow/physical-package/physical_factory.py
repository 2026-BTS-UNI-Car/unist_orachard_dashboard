"""Physical-domain original sensor diagnostics, never actuator/mission authority.

It does not reuse a modeled SIM profile, fabricate /clock or silently deskew
rolling scans. A sensor-frame fit is not a calibrated body velocity.
"""
import inputs
from dataclasses import dataclass
import hashlib
import math
import os
import threading
import time

from common.native_ownership import PreparedInputs
from physical_ingress import (PhysicalOriginalIngress, PhysicalClockReading, require,
    SOURCE_AGE_NS, RECEIPT_AGE_NS)
from kernel.raw_localization import scan_points, surfaces, registration
from kernel.fit_workers import WORKERS


@dataclass(frozen=True)
class PhysicalShadowContract:
    scan_frame: str
    imu_frame: str
    rep145_ready: bool = False
    extrinsics_verified: bool = False
    braking_verified: bool = False
    # This candidate only supports the explicitly unverified physical profile.
    domain: str = 'physical_observation_unverified'

    def __post_init__(self):
        require(all(type(s) is str and 0 < len(s.encode()) <= 128 and s.strip()
                    for s in (self.scan_frame, self.imu_frame)), 'explicit_physical_frames_required')
        require(self.domain == 'physical_observation_unverified' and
                self.rep145_ready is False and self.extrinsics_verified is False and
                self.braking_verified is False, 'no_verified_physical_operating_contract_exists_here')


def revision(contract):
    contract.__post_init__()
    return hashlib.sha256(repr(contract).encode()).hexdigest()


def hold(reason, *, diagnostic=None):
    return dict(state='HOLD', reason=str(reason)[:160], last_row='UNKNOWN',
        applied_linear_mps=0., applied_yaw_rate_rad_s=0., proposed_linear_mps=0.,
        proposed_yaw_rate_rad_s=0., motor_command_sent=False, runtime_authority=False,
        execution_eligible=False, autonomy_output_enabled=False,
        original_live_callback_verified=False, physical_braking_verified=False,
        physical_extrinsics_verified=False, rep145_ready=False,
        physical_autonomy_qualified=False, body_velocity_mps=None,
        diagnostic=diagnostic)


@dataclass(frozen=True)
class PhysicalDiagnosticMotion:
    source_pair_sha256: tuple
    imu_original_sha256: tuple
    source_session: str
    raw_domain: str
    profile_revision: str
    original_scan_stamps_ns: tuple
    original_scan_receipts_ns: tuple
    original_scan_time_increment_s: tuple
    delta_xy_laser_plane_m: tuple
    fitted_laser_plane_yaw_rad: float
    laser_plane_velocity_mps: tuple
    paired_imu_sensor_yaw_delta_rad: float
    original_lower_imu_gyro_z_rad_s: float
    original_lower_imu_stamp_ns: int
    original_lower_imu_receipt_ns: int
    fit_coverage: float
    fit_rmse_m: float
    observed_clock: PhysicalClockReading
    accuracy_verified: bool = False
    rolling_scan_deskewed: bool = False
    body_velocity_available: bool = False


@dataclass(frozen=True)
class PreparedPhysicalDiagnostic:
    original_inputs: object
    record: PhysicalDiagnosticMotion
    _owner: object
    _capability: object


@dataclass(frozen=True)
class DiagnosticState:
    version: int
    latest: PhysicalDiagnosticMotion | None


class PhysicalNativeShadowFactory:
    """Compute one internally fitted diagnostic, then final ownership fences.

    The immutable diagnostic pointer is published only after receiver consume
    and a fresh final system-clock/RAW/STOP/epoch check. There is no mission
    admission, endpoint-grid publication or transport. Fault preserves latest
    diagnostic and invalidates receiver continuity until an external new epoch.
    """
    def __init__(self, ingress, contract):
        require(type(ingress) is PhysicalOriginalIngress, 'exact_physical_original_receiver_required')
        require(type(contract) is PhysicalShadowContract, 'explicit_unverified_physical_contract_required')
        contract.__post_init__()
        require(ingress.fault is None and ingress.profile_revision == revision(contract)
                and (ingress.scan_frame, ingress.imu_frame) == (contract.scan_frame, contract.imu_frame),
                'physical_receiver_contract_mismatch')
        require(WORKERS == 1, 'bounded_private_single_fit_worker_required')
        self.ingress, self.contract = ingress, contract
        self._revision = revision(contract)
        self._generation, self._session, self._pid = ingress.generation, ingress.clock_session, os.getpid()
        self.lock = threading.RLock()
        self.stop_intent = threading.Event()
        self.fault = None
        self._pending = self._pending_digest = None
        self._state = DiagnosticState(0, None)
        self._timing = {'stages_ns': {}, 'selected_TTL_margins_ns': {}}

    def _timed(self, name, function, *args, **kwargs):
        begin = time.perf_counter_ns()
        try:
            return function(*args, **kwargs)
        finally:
            self._timing['stages_ns'][name] = time.perf_counter_ns()-begin

    def timing_snapshot(self):
        with self.lock:
            return dict(schema='ubts.physical.shadow.bounded_stage_timing.v1',
                measurement_clock='python.perf_counter_elapsed_only_not_authority',
                stages_ns=dict(self._timing['stages_ns']),
                selected_TTL_margins_ns=dict(self._timing['selected_TTL_margins_ns']),
                current_original_imu_count=self._timing.get('imu_count'),
                failed_scalar_check=self._timing.get('failed_scalar_check'),
                diagnostic_version=self._state.version,
                HOLD_zero_not_accuracy_or_motor_authority=True)

    def _scalar_margins(self, original, final_clock):
        # Full owned associations have already checked all of these exact
        # frozen originals. This last scalar work never rehashes/retimestamps.
        require(type(final_clock) is PhysicalClockReading and
                final_clock.source_session == self._session and
                final_clock.raw_domain == self.ingress.domain and
                self.ingress.generation == self._generation,
                'final_physical_clock_epoch_changed')
        newest = original.imu_originals[-1]
        checks = (
            ('scan', original.current.wire.header.stamp_ns, original.current.receipt.ns),
            ('selected_lower_IMU', original.pairing.rate_stamp_ns, original.pairing.rate_receipt_ns),
            ('captured_newest_IMU', newest.wire.header.stamp_ns, newest.receipt.ns))
        margins = self._timing['selected_TTL_margins_ns']
        for name, stamp, receipt in checks:
            source_age = final_clock.source_ns-stamp
            receipt_age = final_clock.receipt_ns-receipt
            margins[name+'_source_age'] = source_age
            margins[name+'_receipt_age'] = receipt_age
            margins[name+'_source_remaining'] = SOURCE_AGE_NS-source_age
            margins[name+'_receipt_remaining'] = RECEIPT_AGE_NS-receipt_age
            if not 0 <= source_age < SOURCE_AGE_NS:self._timing['failed_scalar_check'] = name
            require(0 <= source_age < SOURCE_AGE_NS,
                    'final_physical_native_source_deadline')
            if not 0 <= receipt_age < RECEIPT_AGE_NS:self._timing['failed_scalar_check'] = name
            require(0 <= receipt_age < RECEIPT_AGE_NS,
                    'final_physical_native_receipt_deadline')

    def snapshot(self):
        with self.lock:
            return self._state

    def _owned(self, original, *, pending=True):
        require(not self.stop_intent.is_set(), 'physical_shadow_STOP_latched')
        require(self.fault is None and self.ingress.fault is None,
                self.fault or self.ingress.fault or 'physical_receiver_fault')
        require(self._pid == os.getpid() and self.ingress.generation == self._generation
                and self.ingress.clock_session == self._session and
                self.ingress.profile_revision == self._revision == revision(self.contract),
                'physical_shadow_epoch_process_or_profile_changed')
        require(type(original) is PreparedInputs and original.generation == self._generation,
                'owned_physical_original_prepared_inputs_required')
        if pending:
            require(original is self.ingress.prepared and self.ingress._prepared_digest ==
                    self.ingress._digest_prepared(original), 'original_prepared_ownership_changed')
        for envelope in (original.previous, original.current)+original.imu_originals:
            self.ingress.validate_owned(envelope)

    def _clock_and_pair(self, original, *, pending=True):
        self._owned(original, pending=pending)
        clock = self.ingress._evaluate()
        require(type(clock) is PhysicalClockReading, 'physical_system_clock_required')
        samples, pairing = self.ingress._association(original.previous, original.current,
            original.imu_originals, clock)
        require(samples == original.imu_samples and pairing == original.pairing,
                'original_causal_sensor_IMU_changed')
        return clock, pairing

    def _fail(self, reason):
        self.fault = self.fault or str(reason)[:160]
        self.stop_intent.set()
        self.ingress.stop()
        self._pending = self._pending_digest = None
        answer = hold(self.fault)
        answer['diagnostic_stage_timing'] = self.timing_snapshot()
        return answer

    def stop(self):
        self.stop_intent.set()  # visible before waiting for numeric/owner mutex
        with self.lock:
            self.fault = 'operator_stop'
            return self._fail(self.fault)

    @staticmethod
    def _view(envelope):
        a, _b, inc, _dt, _st, minimum, maximum = envelope.wire.metadata
        return dict(ranges=envelope.wire.ranges, angle_min=float(a), angle_increment=float(inc),
                    range_min=float(minimum), range_max=float(maximum))

    def prepare(self, original):
        with self.lock:
            if self.fault or self.stop_intent.is_set():
                return hold(self.fault or 'operator_stop')
            total_start = time.perf_counter_ns()
            self._timing = {'stages_ns': {}, 'selected_TTL_margins_ns': {}}
            try:
                require(self._pending is None, 'one_physical_diagnostic_in_flight_required')
                with self.ingress.lock:
                    _clock, pairing = self._timed('prepare_association', self._clock_and_pair, original)
                    self._timing['imu_count'] = len(original.imu_originals)
                    original_digest = self.ingress._digest_prepared(original)
                fit_start = time.perf_counter_ns()
                before = surfaces(scan_points(self._view(original.previous)))
                after = surfaces(scan_points(self._view(original.current)))
                dt = (original.current.wire.header.stamp_ns-original.previous.wire.header.stamp_ns)*1e-9
                require(0 < dt <= .2, 'bounded_original_physical_pair_interval_required')
                result = registration(before, after, abs(pairing.yaw_delta_rad), dt, None)
                require(type(result) is dict and result.get('accepted') is True,
                        'diagnostic_native_fit_rejected:'+str(result.get('reason', 'invalid_result')))
                xy, yaw = tuple(float(v) for v in result['xy']), float(result['yaw'])
                require(len(xy) == 2 and all(math.isfinite(v) for v in (*xy, yaw)) and
                        math.hypot(*xy) <= .6 and abs(yaw) < math.pi, 'bounded_sensor_frame_fit_required')
                self._timing['stages_ns']['numeric_fit'] = time.perf_counter_ns()-fit_start
                with self.ingress.lock:
                    clock, pairing = self._timed('postfit_association', self._clock_and_pair, original)
                    require(self.ingress._digest_prepared(original) == original_digest,
                            'original_bytes_changed_during_diagnostic_fit')
                    record = PhysicalDiagnosticMotion(
                        (original.previous.original_sha256, original.current.original_sha256),
                        tuple(e.original_sha256 for e in original.imu_originals),
                        self._session, self.ingress.domain, self._revision,
                        (original.previous.wire.header.stamp_ns, original.current.wire.header.stamp_ns),
                        (original.previous.receipt.ns, original.current.receipt.ns),
                        (float(original.previous.wire.metadata[3]), float(original.current.wire.metadata[3])),
                        xy, yaw, tuple(v/dt for v in xy), pairing.yaw_delta_rad,
                        pairing.yaw_rate_rad_s, pairing.rate_stamp_ns, pairing.rate_receipt_ns,
                        float(result['coverage']), float(result['rmse_m']), clock)
                    require(0 < record.fit_coverage <= 1 and 0 <= record.fit_rmse_m <= .13,
                            'native_diagnostic_quality_bounds_required')
                    self._pending = PreparedPhysicalDiagnostic(original, record, self, object())
                    self._pending_digest = hashlib.sha256(repr(record).encode()+original_digest.encode()).hexdigest()
                    return self._pending
            except Exception as error:
                return self._fail(error)
            finally:
                self._timing['stages_ns']['prepare_total'] = time.perf_counter_ns()-total_start

    def commit(self, prepared):
        with self.lock:
            if self.fault or self.stop_intent.is_set():
                return hold(self.fault or 'operator_stop')
            total_start = time.perf_counter_ns()
            try:
                with self.ingress.lock:
                    require(type(prepared) is PreparedPhysicalDiagnostic and prepared is self._pending
                            and prepared._owner is self, 'owned_physical_diagnostic_capability_required')
                    original = prepared.original_inputs
                    self._timed('commit_initial_association', self._clock_and_pair, original)
                    require(hashlib.sha256(repr(prepared.record).encode()+
                        self.ingress._digest_prepared(original).encode()).hexdigest() == self._pending_digest,
                        'original_physical_diagnostic_changed')
                    next_state = DiagnosticState(self._state.version+1, prepared.record)
                    response = hold('unverified_physical_REP145_extrinsics_braking_HOLD',
                                    diagnostic=prepared.record)
                    outcome = self._timed('receiver_consume', self.ingress.commit, original)
                    require(self.ingress.fault is None and self.ingress.prepared is None and
                            outcome.reason in ('first_pair_new_epoch',
                            'inputs_only_reviewed_typed_localizer_factory_missing'), outcome.reason)
                    # Receiver consumption is not publication. Recheck after it.
                    self._timed('commit_consumed_association', self._clock_and_pair, original, pending=False)
                    # Full association checks above can take time. Sample only
                    # after that work; then bound original scalar stamps without
                    # another full hash/decode or rewriting the prepared record.
                    response['diagnostic_stage_timing'] = self.timing_snapshot()
                    final_clock = self.ingress._evaluate()
                    self._timed('commit_final_scalar_fence', self._scalar_margins, original, final_clock)
                    require(not self.stop_intent.is_set(), 'physical_shadow_STOP_latched')
                    self._state = next_state  # one final immutable pointer swap
                    self._pending = self._pending_digest = None
                    return response
            except Exception as error:
                return self._fail(error)
            finally:
                self._timing['stages_ns']['commit_total'] = time.perf_counter_ns()-total_start
