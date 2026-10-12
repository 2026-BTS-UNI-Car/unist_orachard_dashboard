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
    SOURCE_AGE_NS, RECEIPT_AGE_NS, _ValidatedConsumedCausalRawExpiry, _ValidatedFinalScalarCausalRawExpiry,
    _ValidatedPreparationCausalRawExpiry)
from kernel.raw_localization import scan_points, surfaces, registration, ExactSurfaceCache
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


@dataclass(frozen=True)
class _ConsumedCausalRawExpiryWait:
    reason: str
    generation: int
    _owner: object
    diagnostic_version: int
    input_ready: bool = False


@dataclass(frozen=True)
class _ReceiverCommitCausalRawExpiryWait:
    reason: str
    generation: int
    _owner: object
    diagnostic_version: int
    input_ready: bool = False


@dataclass(frozen=True)
class _FinalScalarCausalRawExpiryWait:
    reason: str
    generation: int
    _owner: object
    diagnostic_version: int
    input_ready: bool = False


@dataclass(frozen=True)
class _PreparationCausalRawExpiryWait:
    reason: str
    generation: int
    _owner: object
    diagnostic_version: int
    input_ready: bool = False


class _ValidatedRegistrationRejection(Exception):
    """V21: exact kernel rejection of an owned, unpublished prepared pair."""


@dataclass(frozen=True)
class _RegistrationRejectionWait:
    reason: str
    generation: int
    _owner: object
    diagnostic_version: int
    input_ready: bool = False


# V21 exact allowlist of normal kernel/bound outcomes. Any other exception or
# result (LinAlgError, invalid angles, oversized scans, unknown reasons, extra
# result keys, non-finite fits) keeps the existing fatal _fail path.
REGISTRATION_REJECTION_SCAN_ERRORS = ('insufficient_returns', 'insufficient_observed_surfaces')
REGISTRATION_REJECTION_RESULTS = ('unsupported_or_inconsistent_registration', 'ambiguous_registration')
REGISTRATION_REJECTION_BOUND = 'bounded_sensor_frame_fit_required'
REGISTRATION_REJECTION_REASONS = REGISTRATION_REJECTION_SCAN_ERRORS+REGISTRATION_REJECTION_RESULTS+(
    REGISTRATION_REJECTION_BOUND,)
REGISTRATION_REJECTION_RESULT_KEYS = frozenset(('accepted', 'reason', 'fit_rejection_counts',
    'current_supported_points', 'sampled_point_denominator'))
REGISTRATION_KERNEL_FAILURES = frozenset(('iteration_insufficient_correspondences', 'search_translation_bound',
    'search_rotation_bound', 'final_insufficient_correspondences', 'final_insufficient_overlap',
    'translation_geometrically_degenerate', 'pose_information_degenerate',
    'excessive_point_to_line_residual', 'IMU_relative_rotation_disagreement'))
REGISTRATION_REJECTION_WAIT_REASON = 'waiting_for_next_scan_after_rejected_native_registration'


class PhysicalNativeShadowFactory:
    """Compute one internally fitted diagnostic, then final ownership fences.

    The immutable diagnostic pointer is published only after receiver consume
    and a fresh final system-clock/RAW/STOP/epoch check. There is no mission
    admission, endpoint-grid publication or transport. Fault preserves latest
    diagnostic and invalidates receiver continuity until an external new epoch.
    """
    def __init__(self, ingress, contract, *, exact_surface_cache_enabled=False,
                 postconsumed_causal_raw_expiry_reanchor_enabled=False,
                 receiver_commit_causal_raw_expiry_reanchor_enabled=False,
                 final_scalar_causal_raw_expiry_reanchor_enabled=False,
                 preparation_causal_raw_expiry_reanchor_enabled=False,
                 registration_rejection_reanchor_enabled=False):
        require(type(registration_rejection_reanchor_enabled) is bool,
                'typed_registration_rejection_reanchor_option_required')
        self.__registration_rejection_reanchor_enabled = registration_rejection_reanchor_enabled
        self._registration_rejection_configuration = registration_rejection_reanchor_enabled
        self._registration_rejection_attempt = self._registration_rejection_proof = None
        self._registration_rejection_wait = self._registration_rejection_anchor = None
        self.registration_rejection_wait_count = 0
        self.registration_rejection_wait_streak = 0
        self.registration_rejection_wait_max_streak = 0
        self.registration_rejection_reason_counts = dict.fromkeys(REGISTRATION_REJECTION_REASONS, 0)
        self.registration_rejection_wait_timing = None
        require(type(preparation_causal_raw_expiry_reanchor_enabled) is bool,
                'typed_preparation_causal_RAW_expiry_option_required')
        self.__preparation_causal_raw_expiry_reanchor_enabled = preparation_causal_raw_expiry_reanchor_enabled
        self._preparation_expiry_configuration = preparation_causal_raw_expiry_reanchor_enabled
        self._preparation_anchor = self._preparation_attempt = self._preparation_wait = None
        self.preparation_raw_expiry_wait_count = 0
        self.preparation_raw_expiry_wait_timing = None
        require(type(final_scalar_causal_raw_expiry_reanchor_enabled) is bool,
                'typed_final_scalar_causal_RAW_expiry_option_required')
        self.__final_scalar_causal_raw_expiry_reanchor_enabled = final_scalar_causal_raw_expiry_reanchor_enabled
        self._final_expiry_configuration = final_scalar_causal_raw_expiry_reanchor_enabled
        self._final_attempt = self._final_wait = self._final_pending_seal = None
        self.final_scalar_raw_expiry_wait_count = 0
        self.final_scalar_raw_expiry_wait_timing = None
        require(type(receiver_commit_causal_raw_expiry_reanchor_enabled) is bool,
                'typed_receiver_commit_causal_RAW_expiry_option_required')
        self.__receiver_commit_causal_raw_expiry_reanchor_enabled = receiver_commit_causal_raw_expiry_reanchor_enabled
        self._receiver_expiry_configuration = receiver_commit_causal_raw_expiry_reanchor_enabled
        self._receiver_attempt = self._receiver_clock = self._receiver_wait = None
        self.receiver_commit_raw_expiry_wait_count = 0
        self.receiver_commit_raw_expiry_wait_timing = None
        require(type(postconsumed_causal_raw_expiry_reanchor_enabled) is bool,
                'typed_postconsumed_causal_RAW_expiry_option_required')
        require(type(exact_surface_cache_enabled) is bool,
                'typed_constructor_surface_cache_option_required')
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
        self.__exact_surface_cache_enabled = exact_surface_cache_enabled
        self._exact_surface_cache = ExactSurfaceCache(enabled=exact_surface_cache_enabled)
        self.__postconsumed_causal_raw_expiry_reanchor_enabled = postconsumed_causal_raw_expiry_reanchor_enabled
        self._consumed_expiry_configuration = postconsumed_causal_raw_expiry_reanchor_enabled
        self._consumed_attempt = self._consumed_wait = None
        self.consumed_raw_expiry_wait_count = 0
        self.consumed_raw_expiry_wait_timing = None

    @property
    def preparation_causal_raw_expiry_reanchor_enabled(self):
        return self.__preparation_causal_raw_expiry_reanchor_enabled

    @property
    def registration_rejection_reanchor_enabled(self):
        return self.__registration_rejection_reanchor_enabled

    @property
    def exact_surface_cache_enabled(self):
        return self.__exact_surface_cache_enabled

    @property
    def postconsumed_causal_raw_expiry_reanchor_enabled(self):
        return self.__postconsumed_causal_raw_expiry_reanchor_enabled

    @property
    def receiver_commit_causal_raw_expiry_reanchor_enabled(self):
        return self.__receiver_commit_causal_raw_expiry_reanchor_enabled

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

    def _validate_final_expiry_attempt(self, attempt, original, clock):
        self._owned(original, pending=False)
        seal = self._final_pending_seal
        require(type(seal) is tuple and len(seal) == 5 and seal[0] is self._pending and
                type(self._pending) is PreparedPhysicalDiagnostic and
                seal[1] is self._pending._capability and seal[2] is self._state and
                seal[3] == self._state.version and seal[4] is self._state.latest,
                'issued_final_scalar_factory_capability_required')
        require(type(self) is PhysicalNativeShadowFactory and
                self.final_scalar_causal_raw_expiry_reanchor_enabled and
                type(attempt) is tuple and attempt is self._final_attempt and len(attempt) == 7 and
                attempt[1] is self._pending and attempt[2] is original and attempt[3] is clock and
                type(self._pending) is PreparedPhysicalDiagnostic and self._pending._owner is self and
                attempt[4] is self._pending._capability and attempt[5] == self._pending_digest and
                attempt[6] is self._state and type(self._state.version) is int and
                0 <= self._state.version < (1<<63)-1 and
                self._pending.original_inputs is original and self.ingress.prepared is None and
                self.ingress._prepared_digest is None and self.ingress.pending is None and
                self.ingress._active is None,
                'owned_successfully_consumed_final_scalar_attempt_required')
        require(hashlib.sha256(repr(self._pending.record).encode()+
                self.ingress._digest_prepared(original).encode()).hexdigest() == self._pending_digest,
                'original_physical_diagnostic_changed')
        require(type(clock) is PhysicalClockReading and clock.source_session == self._session and
                clock.raw_domain == self.ingress.domain and clock.source_ns == self.ingress._last_system and
                clock.receipt_ns == self.ingress._last_eval_raw and
                clock.sample_sequence == self.ingress._system_sample_sequence,
                'exact_existing_final_scalar_clock_required')

    def _abandon_final_scalar_raw_expiry(self, error):
        attempt = self._final_attempt
        require(attempt is not None, 'owned_final_scalar_factory_attempt_required')
        original, clock = attempt[2], attempt[3]
        self._validate_final_expiry_attempt(attempt, original, clock)
        require(type(self.final_scalar_raw_expiry_wait_count) is int and
                0 <= self.final_scalar_raw_expiry_wait_count < (1<<63)-1,
                'final_scalar_RAW_expiry_counter_exhausted')
        lower = original.imu_originals[original.pairing.end_indices[0]]
        timing = dict(schema='ubts.native.final_scalar_causal_RAW_expiry_wait.v1',
            stage='commit_final_scalar_fence', source_sequence=lower.source_sequence,
            comparison=dict(source_ns=clock.source_ns,RAW_ns=clock.receipt_ns),
            lower_header_ns=lower.wire.header.stamp_ns, lower_callback_RAW_ns=lower.receipt.ns,
            lower_SOURCE_age_ns=clock.source_ns-lower.wire.header.stamp_ns,
            lower_RAW_age_ns=clock.receipt_ns-lower.receipt.ns,
            SOURCE_TTL_ns=SOURCE_AGE_NS,RAW_TTL_ns=RECEIPT_AGE_NS,
            diagnostic_version=self._state.version,data_qualified=False)
        import json
        require(len(json.dumps(timing,allow_nan=False,separators=(',',':')).encode()) <= 1024,
                'final_scalar_RAW_expiry_timing_capacity')
        self.ingress._quarantine_final_scalar_raw_expiry(error, self, attempt)
        # Quarantine ran full guards after the typed-signal handoff. No latch is
        # cleared, and no fitted record is published on this path.
        require(not self.stop_intent.is_set() and self.fault is None and self.ingress.fault is None,
                'physical_shadow_STOP_latched')
        self._validate_final_expiry_attempt(attempt, original, clock)
        self._pending = self._pending_digest = self._final_attempt = self._final_pending_seal = None
        self._receiver_wait = self._consumed_wait = None
        self.final_scalar_raw_expiry_wait_count += 1
        self.final_scalar_raw_expiry_wait_timing = timing
        self._final_wait = _FinalScalarCausalRawExpiryWait(
            'waiting_for_fresh_scan_after_final_scalar_causal_RAW_expiry', self._generation,self,self._state.version)
        return self._final_wait
    def is_owned_final_scalar_raw_expiry_wait(self, value):
        with self.lock, self.ingress.lock:
            return (type(value) is _FinalScalarCausalRawExpiryWait and value is self._final_wait and
                value._owner is self and value.generation == self._generation == self.ingress.generation and
                self.ingress.clock_session == self._session and self._pid == os.getpid() and
                self.ingress.profile_revision == self._revision == revision(self.contract) and
                value.diagnostic_version == self._state.version and
                self.final_scalar_causal_raw_expiry_reanchor_enabled is True and
                self._final_expiry_configuration is True and self.fault is None and
                self.ingress.fault is None and not self.stop_intent.is_set() and
                self._pending is None and self._pending_digest is None and self._final_attempt is None and
                self._final_pending_seal is None and
                self.ingress.previous is None and self.ingress.pending is None and
                self.ingress.prepared is None and self.ingress._active is None and
                self.ingress._prepared_digest is None and self.ingress._final_expiry_proof is None and
                not self.ingress._owners and not self.ingress.imus and not self.ingress.callback_provenance)
    def _raise_final_scalar_raw_expiry(self, original, clock):
        with self.ingress.lock:
            attempt = self._final_attempt = (object(),self._pending,original,clock,
                self._pending._capability,self._pending_digest,self._state)
            self._validate_final_expiry_attempt(attempt, original, clock)
            # Failed leaf only: same already-sampled final clock, full originals.
            # Healthy final fences perform no extra SHA/decode/clock work.
            self.ingress._association(original.previous,original.current,original.imu_originals,clock,
                _final_scalar_raw_expiry=(self,attempt,original))
            raise ValueError('exact_positive_final_scalar_RAW_leaf_required')

    def _scalar_margins(self, original, final_clock):
        # Full owned associations have already checked all of these exact
        # frozen originals. This last scalar work never rehashes/retimestamps.
        require(type(final_clock) is PhysicalClockReading and
                final_clock.source_session == self._session and
                final_clock.raw_domain == self.ingress.domain and
                self.ingress.generation == self._generation,
                'final_physical_clock_epoch_changed')
        if self.preparation_causal_raw_expiry_reanchor_enabled:
            self._check_positive_age_expiry(original,final_clock,'commit_final_scalar_fence',pending=False)
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
            if (name == 'selected_lower_IMU' and self.final_scalar_causal_raw_expiry_reanchor_enabled and
                    receipt_age >= RECEIPT_AGE_NS):
                self._raise_final_scalar_raw_expiry(original, final_clock)
            require(0 <= receipt_age < RECEIPT_AGE_NS,
                    'final_physical_native_receipt_deadline')

    def snapshot(self):
        with self.lock:
            return self._state

    @property
    def final_scalar_causal_raw_expiry_reanchor_enabled(self):
        return self.__final_scalar_causal_raw_expiry_reanchor_enabled

    def _owned(self, original, *, pending=True):
        require(type(self.registration_rejection_reanchor_enabled) is bool and
                self.registration_rejection_reanchor_enabled is self._registration_rejection_configuration,
                'physical_shadow_registration_recovery_configuration_changed')
        require(type(self.preparation_causal_raw_expiry_reanchor_enabled) is bool and
                self.preparation_causal_raw_expiry_reanchor_enabled is self._preparation_expiry_configuration,
                'physical_shadow_preparation_recovery_configuration_changed')
        require(type(self.final_scalar_causal_raw_expiry_reanchor_enabled) is bool and
                self.final_scalar_causal_raw_expiry_reanchor_enabled is self._final_expiry_configuration,
                'physical_shadow_final_scalar_recovery_configuration_changed')
        require(type(self.receiver_commit_causal_raw_expiry_reanchor_enabled) is bool and
                self.receiver_commit_causal_raw_expiry_reanchor_enabled is self._receiver_expiry_configuration,
                'physical_shadow_receiver_recovery_configuration_changed')
        require(type(self.postconsumed_causal_raw_expiry_reanchor_enabled) is bool and
                self.postconsumed_causal_raw_expiry_reanchor_enabled is self._consumed_expiry_configuration,
                'physical_shadow_recovery_configuration_changed')
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
        # _clock_and_pair immediately performs _association under this same
        # ingress lock. That independently validates every original envelope
        # with its full SHA/provenance digest/CDR decode; repeating that pass
        # here adds latency without checking another publication boundary.

    def _clock_and_pair(self, original, *, pending=True, preparation_stage=None):
        self._owned(original, pending=pending)
        clock = self.ingress._evaluate()
        require(type(clock) is PhysicalClockReading, 'physical_system_clock_required')
        if self.preparation_causal_raw_expiry_reanchor_enabled:
            stage = preparation_stage or ('commit_consumed_association' if not pending else
                                          'commit_initial_association')
            self._check_positive_age_expiry(original,clock,stage,pending=pending)
        if preparation_stage is not None and self.preparation_causal_raw_expiry_reanchor_enabled:
            require(pending and preparation_stage in ('prepare_association','postfit_association','commit_initial_association'),
                    'exact_factory_preparation_stage_required')
            attempt = self._preparation_attempt = (object(),self._preparation_anchor,original,clock,preparation_stage)
            samples, pairing = self.ingress._association(original.previous, original.current,
                original.imu_originals, clock, _preparation_raw_expiry=(self,attempt,original))
            self._preparation_attempt = None
        elif not pending and self.postconsumed_causal_raw_expiry_reanchor_enabled:
            attempt = self._consumed_attempt = (object(), self._pending, original, clock,
                                               self._pending._capability)
            # Complete proof/digest work is required only if the unchanged
            # association identifies the narrow positive RAW-expiry leaf.
            samples, pairing = self.ingress._association(original.previous, original.current,
                original.imu_originals, clock, _consumed_raw_expiry=(self,attempt,original))
            self._consumed_attempt = None
        else:
            samples, pairing = self.ingress._association(original.previous, original.current,
                original.imu_originals, clock)
        require(samples == original.imu_samples and pairing == original.pairing,
                'original_causal_sensor_IMU_changed')
        return clock, pairing

    def _check_positive_age_expiry(self, original, clock, stage, *, pending):
        if not self.ingress._has_positive_age_expiry(self.ingress._positive_pair_ages(original,clock)):
            return
        self._owned(original,pending=pending)
        if self._preparation_anchor is None:
            require(self._pending is not None,'owned_positive_age_diagnostic_required')
            self._preparation_anchor = (original,self.ingress._prepared_digest,
                self._state,self._state.version,self._state.latest,
                (self._pending,self._pending._capability,self._pending_digest))
        attempt = self._preparation_attempt = (object(),self._preparation_anchor,original,clock,stage)
        self.ingress._raise_preparation_raw_expiry(self,attempt,original,
                                                  original.imu_samples,original.pairing,clock)

    def _validate_preparation_expiry_attempt(self, attempt, original, clock):
        require(type(attempt) is tuple and len(attempt) == 5,'owned_factory_preparation_attempt_required')
        receiver_prepare = attempt[4] in ('receiver_prepare_association','receiver_prepare_end_association')
        consumed = attempt[4] in ('commit_consumed_association','commit_final_scalar_fence')
        self._owned(original,pending=not (consumed or receiver_prepare))
        anchor = self._preparation_anchor
        require(type(self) is PhysicalNativeShadowFactory and
                self.preparation_causal_raw_expiry_reanchor_enabled is True and
                type(anchor) is tuple and len(anchor) == 6 and anchor[0] is original and
                 (receiver_prepare or anchor[1] == self.ingress._prepared_digest) and anchor[2] is self._state and
                anchor[3] == self._state.version and anchor[4] is self._state.latest and
                type(attempt) is tuple and len(attempt) == 5 and attempt is self._preparation_attempt and
                attempt[1] is anchor and attempt[2] is original and attempt[3] is clock and
                 attempt[4] in ('prepare_association','postfit_association','commit_initial_association',
                     'commit_receiver_association','commit_receiver_end_scalar',
                     'commit_consumed_association','commit_final_scalar_fence',
                     'receiver_prepare_association','receiver_prepare_end_association'),
                'owned_factory_preparation_attempt_required')
        diagnostic = anchor[5]
        if receiver_prepare:
            require(type(diagnostic) is tuple and len(diagnostic) == 4 and
                    diagnostic[0] is self.ingress._active and diagnostic[0] is not None and
                    diagnostic[1][0] is original.previous and diagnostic[1][1] is original.current and
                    diagnostic[2] == original.imu_originals and
                    diagnostic[3] == anchor[1] == self.ingress._digest_prepared(original) and
                    self.ingress.previous is original.current and self.ingress.prepared is None and
                    self._pending is None and self._pending_digest is None,
                    'owned_receiver_preparation_positive_age_attempt_required')
        elif attempt[4].startswith('commit_'):
            require(type(diagnostic) is tuple and len(diagnostic) == 3 and
                    type(diagnostic[0]) is PreparedPhysicalDiagnostic and diagnostic[0] is self._pending and
                    diagnostic[0]._owner is self and diagnostic[0].original_inputs is original and
                    diagnostic[0]._capability is diagnostic[1] and diagnostic[2] == self._pending_digest and
                    type(self._final_pending_seal) is tuple and len(self._final_pending_seal) == 5 and
                    self._final_pending_seal[0] is diagnostic[0] and
                    self._final_pending_seal[1] is diagnostic[1] and
                    self._final_pending_seal[2] is self._state and
                    self._final_pending_seal[3] == self._state.version and
                    self._final_pending_seal[4] is self._state.latest,
                    'owned_commit_initial_diagnostic_attempt_required')
            require(hashlib.sha256(repr(diagnostic[0].record).encode()+
                self.ingress._digest_prepared(original).encode()).hexdigest() == diagnostic[2],
                'original_physical_diagnostic_changed')
            if consumed:
                require(self.ingress.prepared is None and self.ingress._prepared_digest is None and
                        self.ingress._first_pair is False and self.ingress.previous is original.current,
                        'exact_consumed_original_pair_required')
        else:
            require(diagnostic is None and self._pending is None and self._pending_digest is None,
                    'one_clean_factory_preparation_attempt_required')

    def _abandon_preparation_raw_expiry(self, error):
        attempt = self._preparation_attempt
        require(attempt is not None, 'owned_factory_preparation_attempt_required')
        original, clock, stage = attempt[2], attempt[3], attempt[4]
        self._validate_preparation_expiry_attempt(attempt, original, clock)
        require(type(self.preparation_raw_expiry_wait_count) is int and
                0 <= self.preparation_raw_expiry_wait_count < (1<<63)-1,
                'preparation_RAW_expiry_counter_exhausted')
        lower = original.imu_originals[original.pairing.end_indices[0]]
        timing = dict(schema='ubts.native.owned_positive_age_expiry_wait.v1',
            stage=stage, source_sequence=lower.source_sequence,
            comparison=dict(source_ns=clock.source_ns,RAW_ns=clock.receipt_ns),
            lower_header_ns=lower.wire.header.stamp_ns,lower_callback_RAW_ns=lower.receipt.ns,
            lower_SOURCE_age_ns=clock.source_ns-lower.wire.header.stamp_ns,
            lower_RAW_age_ns=clock.receipt_ns-lower.receipt.ns,
            SOURCE_TTL_ns=SOURCE_AGE_NS,RAW_TTL_ns=RECEIPT_AGE_NS,
            diagnostic_version=self._state.version,data_qualified=False,
            checked_ages=self.ingress._positive_pair_ages(original,clock))
        import json
        require(len(json.dumps(timing,allow_nan=False,separators=(',',':')).encode()) <= 2048,
                'preparation_RAW_expiry_timing_capacity')
        self.ingress._quarantine_preparation_raw_expiry(error,self,attempt)
        require(not self.stop_intent.is_set() and self.fault is None and self.ingress.fault is None,
                'physical_shadow_STOP_latched')
        self._pending = self._pending_digest = self._final_pending_seal = None
        self._preparation_anchor = self._preparation_attempt = None
        self._consumed_attempt = self._receiver_attempt = self._receiver_clock = self._final_attempt = None
        self.preparation_raw_expiry_wait_count += 1
        self.preparation_raw_expiry_wait_timing = timing
        self._preparation_wait = _PreparationCausalRawExpiryWait(
            'waiting_for_fresh_two_scan_pair_after_positive_original_age_expiry',
            self._generation,self,self._state.version)
        return self._preparation_wait

    def is_owned_preparation_raw_expiry_wait(self, value):
        with self.lock, self.ingress.lock:
            return (type(value) is _PreparationCausalRawExpiryWait and value is self._preparation_wait and
                value._owner is self and value.generation == self._generation == self.ingress.generation and
                self.ingress.clock_session == self._session and self._pid == os.getpid() and
                self.ingress.profile_revision == self._revision == revision(self.contract) and
                value.diagnostic_version == self._state.version and
                self.preparation_causal_raw_expiry_reanchor_enabled is True and
                self._preparation_expiry_configuration is True and self.fault is None and self.ingress.fault is None and
                not self.stop_intent.is_set() and self._pending is None and self._pending_digest is None and
                self._preparation_anchor is None and self._preparation_attempt is None and
                self.ingress.previous is None and self.ingress.pending is None and self.ingress.prepared is None and
                self.ingress._active is None and self.ingress._prepared_digest is None and
                self.ingress._preparation_expiry_proof is None and not self.ingress._owners and
                not self.ingress.imus and not self.ingress.callback_provenance)

    def _validate_consumed_expiry_attempt(self, attempt, original, clock):
        self._owned(original, pending=False)
        require(type(self) is PhysicalNativeShadowFactory and
                self.postconsumed_causal_raw_expiry_reanchor_enabled and
                type(attempt) is tuple and attempt is self._consumed_attempt and len(attempt) == 5 and
                attempt[1] is self._pending and attempt[2] is original and attempt[3] is clock and
                type(self._pending) is PreparedPhysicalDiagnostic and self._pending._owner is self and
                attempt[4] is self._pending._capability and
                self._pending.original_inputs is original and self.ingress.prepared is None and
                self.ingress._prepared_digest is None and self.ingress.pending is None and
                self.ingress._active is None,
                'owned_successfully_consumed_factory_attempt_required')
        require(hashlib.sha256(repr(self._pending.record).encode()+
                self.ingress._digest_prepared(original).encode()).hexdigest() == self._pending_digest,
                'original_physical_diagnostic_changed')

    def _validate_receiver_commit_expiry_attempt(self, attempt, original, clock, *, quarantined=False):
        self._owned(original, pending=not quarantined)
        require(type(self) is PhysicalNativeShadowFactory and
                self.receiver_commit_causal_raw_expiry_reanchor_enabled is True and
                type(attempt) is tuple and len(attempt) == 5 and attempt is self._receiver_attempt and
                attempt[1] is self._pending and attempt[2] is original and
                type(self._pending) is PreparedPhysicalDiagnostic and self._pending._owner is self and
                attempt[3] is self._pending._capability and attempt[4] == self._pending_digest and
                self._pending.original_inputs is original,
                'owned_receiver_commit_factory_attempt_required')
        require(hashlib.sha256(repr(self._pending.record).encode()+
                self.ingress._digest_prepared(original).encode()).hexdigest() == self._pending_digest,
                'original_physical_diagnostic_changed')
        if clock is not None:
            require(type(clock) is PhysicalClockReading and
                    clock.source_session == self._session and clock.raw_domain == self.ingress.domain,
                    'exact_receiver_commit_expiry_clock_required')
            if self._receiver_clock is None:
                self._receiver_clock = clock
            require(clock is self._receiver_clock, 'one_receiver_commit_expiry_clock_required')
        if quarantined:
            require(self.ingress.is_owned_receiver_commit_raw_wait(
                self.ingress._receiver_expiry_wait, self, attempt),
                'exact_receiver_commit_quarantine_required')

    def _abandon_receiver_commit_raw_expiry(self, outcome):
        attempt, clock = self._receiver_attempt, self._receiver_clock
        require(attempt is not None and clock is not None and
                self.ingress.is_owned_receiver_commit_raw_wait(outcome, self, attempt),
                'owned_receiver_commit_RAW_wait_required')
        original = attempt[2]
        self._validate_receiver_commit_expiry_attempt(attempt, original, clock, quarantined=True)
        require(type(self.receiver_commit_raw_expiry_wait_count) is int and
                0 <= self.receiver_commit_raw_expiry_wait_count < (1<<63)-1,
                'receiver_commit_RAW_expiry_counter_exhausted')
        lower = original.imu_originals[original.pairing.end_indices[0]]
        timing = dict(schema='ubts.native.receiver_commit_causal_RAW_expiry_wait.v1',
            stage='commit_receiver_association', source_sequence=lower.source_sequence,
            comparison=dict(source_ns=clock.source_ns,RAW_ns=clock.receipt_ns),
            lower_header_ns=lower.wire.header.stamp_ns, lower_callback_RAW_ns=lower.receipt.ns,
            lower_SOURCE_age_ns=clock.source_ns-lower.wire.header.stamp_ns,
            lower_RAW_age_ns=clock.receipt_ns-lower.receipt.ns,
            SOURCE_TTL_ns=SOURCE_AGE_NS,RAW_TTL_ns=RECEIPT_AGE_NS,
            diagnostic_version=self._state.version,data_qualified=False)
        import json
        require(len(json.dumps(timing,allow_nan=False,separators=(',',':')).encode()) <= 1024,
                'receiver_commit_RAW_expiry_timing_capacity')
        self._validate_receiver_commit_expiry_attempt(attempt, original, clock, quarantined=True)
        self._pending = self._pending_digest = self._receiver_attempt = self._receiver_clock = None
        # Drop the transient proof, which contains original/fit capabilities.
        # The returned factory WAIT retains only owner/generation/version.
        self.ingress._receiver_expiry_wait = None
        self.receiver_commit_raw_expiry_wait_count += 1
        self.receiver_commit_raw_expiry_wait_timing = timing
        self._receiver_wait = _ReceiverCommitCausalRawExpiryWait(
            'waiting_for_fresh_scan_after_receiver_commit_causal_RAW_expiry',
            self._generation,self,self._state.version)
        return self._receiver_wait

    def is_owned_receiver_commit_raw_expiry_wait(self, value):
        with self.lock, self.ingress.lock:
            return (type(value) is _ReceiverCommitCausalRawExpiryWait and value is self._receiver_wait and
                value._owner is self and value.generation == self._generation == self.ingress.generation and
                self.ingress.clock_session == self._session and self._pid == os.getpid() and
                self.ingress.profile_revision == self._revision == revision(self.contract) and
                value.diagnostic_version == self._state.version and
                self.receiver_commit_causal_raw_expiry_reanchor_enabled is True and
                self._receiver_expiry_configuration is True and self.fault is None and self.ingress.fault is None and
                not self.stop_intent.is_set() and self._pending is None and self._pending_digest is None and
                self._receiver_attempt is None and self._receiver_clock is None and
                self.ingress.previous is None and self.ingress.pending is None and self.ingress.prepared is None and
                self.ingress._active is None and self.ingress._prepared_digest is None and
                self.ingress._receiver_expiry_proof is None and self.ingress._receiver_expiry_wait is None and
                not self.ingress._owners and not self.ingress.imus and
                not self.ingress.callback_provenance)

    def _abandon_consumed_raw_expiry(self, error):
        attempt = self._consumed_attempt
        require(attempt is not None, 'owned_consumed_factory_attempt_required')
        original, clock = attempt[2], attempt[3]
        self._validate_consumed_expiry_attempt(attempt, original, clock)
        require(type(self.consumed_raw_expiry_wait_count) is int and
                0 <= self.consumed_raw_expiry_wait_count < (1<<63)-1,
                'consumed_RAW_expiry_counter_exhausted')
        lower = original.imu_originals[original.pairing.end_indices[0]]
        timing = dict(schema='ubts.native.postconsumed_causal_RAW_expiry_wait.v1',
            stage='commit_consumed_association', source_sequence=lower.source_sequence,
            comparison=dict(source_ns=clock.source_ns,RAW_ns=clock.receipt_ns),
            lower_header_ns=lower.wire.header.stamp_ns, lower_callback_RAW_ns=lower.receipt.ns,
            lower_SOURCE_age_ns=clock.source_ns-lower.wire.header.stamp_ns,
            lower_RAW_age_ns=clock.receipt_ns-lower.receipt.ns,
            SOURCE_TTL_ns=SOURCE_AGE_NS,RAW_TTL_ns=RECEIPT_AGE_NS,
            diagnostic_version=self._state.version,data_qualified=False)
        import json
        require(len(json.dumps(timing,allow_nan=False,separators=(',',':')).encode()) <= 1024,
                'consumed_RAW_expiry_timing_capacity')
        self.ingress._quarantine_consumed_raw_expiry(error, self, attempt)
        # Quarantine ran full guards after the typed-signal handoff. No latch is
        # cleared, and no fitted record is published on this path.
        require(not self.stop_intent.is_set() and self.fault is None and self.ingress.fault is None,
                'physical_shadow_STOP_latched')
        self._pending = self._pending_digest = self._consumed_attempt = None
        self.consumed_raw_expiry_wait_count += 1
        self.consumed_raw_expiry_wait_timing = timing
        self._consumed_wait = _ConsumedCausalRawExpiryWait(
            'waiting_for_fresh_scan_after_consumed_causal_RAW_expiry', self._generation,self,self._state.version)
        return self._consumed_wait

    def is_owned_consumed_raw_expiry_wait(self, value):
        with self.lock, self.ingress.lock:
            return (type(value) is _ConsumedCausalRawExpiryWait and value is self._consumed_wait and
                value._owner is self and value.generation == self._generation == self.ingress.generation and
                self.ingress.clock_session == self._session and self._pid == os.getpid() and
                self.ingress.profile_revision == self._revision == revision(self.contract) and
                value.diagnostic_version == self._state.version and
                self.postconsumed_causal_raw_expiry_reanchor_enabled is True and
                self._consumed_expiry_configuration is True and self.fault is None and
                self.ingress.fault is None and not self.stop_intent.is_set() and
                self._pending is None and self._pending_digest is None and self._consumed_attempt is None and
                self.ingress.previous is None and self.ingress.pending is None and
                self.ingress.prepared is None and self.ingress._active is None and
                not self.ingress._owners and not self.ingress.imus and not self.ingress.callback_provenance)

    # ---- V21 (a): exact kernel rejection -> owned WAIT, newest validated scan kept ----

    def _registration_rejection_detail(self, *, error=None, result=None, bounded=None):
        """Return (reason, bounded detail) for an exact allowlisted outcome, else None."""
        if error is not None:
            # Exact type: numpy LinAlgError and other ValueError subclasses stay fatal.
            if (type(error) is ValueError and len(error.args) == 1 and
                    error.args[0] in REGISTRATION_REJECTION_SCAN_ERRORS):
                return error.args[0], None
            return None
        if result is not None:
            if not (type(result) is dict and set(result) <= REGISTRATION_REJECTION_RESULT_KEYS and
                    result.get('accepted') is False and result.get('reason') in REGISTRATION_REJECTION_RESULTS):
                return None
            counts = result.get('fit_rejection_counts', {})
            if not (type(counts) is dict and len(counts) <= len(REGISTRATION_KERNEL_FAILURES) and
                    all(type(k) is str and k in REGISTRATION_KERNEL_FAILURES and type(v) is int and 0 < v <= 1024
                        for k, v in counts.items())):
                return None
            extra = {k: result[k] for k in ('current_supported_points', 'sampled_point_denominator') if k in result}
            if not all(type(v) is int and 0 <= v <= 4096 for v in extra.values()):
                return None
            return result['reason'], dict(fit_rejection_counts=dict(sorted(counts.items())), **extra)
        if bounded is not None:
            xy, yaw = bounded
            # Non-finite or malformed fits are not normal geometry outcomes: fatal.
            if not (type(xy) is tuple and len(xy) == 2 and
                    all(type(v) is float and math.isfinite(v) for v in (*xy, yaw))):
                return None
            if math.hypot(*xy) <= .6 and abs(yaw) < math.pi:
                return None
            return REGISTRATION_REJECTION_BOUND, dict(translation_m=math.hypot(*xy), yaw_rad=yaw)
        return None

    def _raise_registration_rejection(self, original, original_digest, state, *, error=None,
                                      result=None, bounded=None):
        """Phase 1. Returns (caller re-raises/keeps fatal) unless the outcome is allowlisted."""
        if self.registration_rejection_reanchor_enabled is not True:
            return
        detail = self._registration_rejection_detail(error=error, result=result, bounded=bounded)
        if detail is None:
            return
        reason, kernel_detail = detail
        with self.ingress.lock:
            attempt = self._registration_rejection_attempt = (object(), original, original_digest, state,
                state.version, state.latest, reason, kernel_detail)
            self._validate_registration_rejection_attempt(attempt)
            signal = _ValidatedRegistrationRejection(reason)
            self._registration_rejection_proof = (signal, attempt)
            raise signal

    def _validate_registration_rejection_attempt(self, attempt):
        require(type(attempt) is tuple and len(attempt) == 8 and attempt is self._registration_rejection_attempt,
                'owned_registration_rejection_attempt_required')
        _token, original, digest, state, version, latest, reason, _detail = attempt
        require(type(self) is PhysicalNativeShadowFactory and
                self.registration_rejection_reanchor_enabled is True and
                reason in REGISTRATION_REJECTION_REASONS, 'owned_registration_rejection_attempt_required')
        # Stop, faults, configuration, pid/epoch/profile and exact prepared identity+digest.
        self._owned(original, pending=True)
        require(type(digest) is str and self.ingress._digest_prepared(original) == digest ==
                self.ingress._prepared_digest, 'original_bytes_changed_during_diagnostic_fit')
        for value in (original.previous, original.current)+original.imu_originals+tuple(self.ingress.imus):
            self.ingress.validate_owned(value)
        self.ingress._native_scan(original.previous)
        self.ingress._native_scan(original.current)
        # Nothing was published: same immutable state/version/latest, no pending fit.
        require(state is self._state and type(version) is int and version == state.version and
                0 <= version < (1<<63)-1 and latest is state.latest and
                self._pending is None and self._pending_digest is None and self._final_pending_seal is None,
                'unpublished_registration_rejection_required')
        require(self.ingress.previous is original.current and self.ingress.pending is None and
                self.ingress._active is None and self.ingress.fault is None and
                original.previous.publisher_gid == original.current.publisher_gid == self.ingress.expected_gids[0],
                'exact_rejected_pair_anchor_required')

    def _abandon_registration_rejection(self, error):
        """Phase 2: proof identity, full guards again, then keep-newest-anchor WAIT."""
        proof = self._registration_rejection_proof
        require(type(error) is _ValidatedRegistrationRejection and type(proof) is tuple and len(proof) == 2 and
                proof[0] is error and proof[1] is self._registration_rejection_attempt,
                'owned_registration_rejection_proof_required')
        attempt = proof[1]
        self._validate_registration_rejection_attempt(attempt)
        _token, original, digest, _state, version, _latest, reason, kernel_detail = attempt
        require(type(self.registration_rejection_wait_count) is int and
                0 <= self.registration_rejection_wait_count < (1<<63)-1 and
                type(self.registration_rejection_wait_streak) is int and
                0 <= self.registration_rejection_wait_streak < (1<<63)-1 and
                type(self.registration_rejection_reason_counts.get(reason)) is int and
                0 <= self.registration_rejection_reason_counts[reason] < (1<<63)-1,
                'registration_rejection_counter_exhausted')
        previous, current = original.previous, original.current
        timing = dict(schema='ubts.native.registration_rejection_wait.v1', stage='factory_prepare_fit',
            reason=reason, kernel_detail=kernel_detail,
            scan_headers_ns=[previous.wire.header.stamp_ns, current.wire.header.stamp_ns],
            scan_receipts_ns=[previous.receipt.ns, current.receipt.ns],
            pair_interval_ns=current.wire.header.stamp_ns-previous.wire.header.stamp_ns,
            imu_original_count=len(original.imu_originals), diagnostic_version=version,
            anchor='newest_validated_current_scan_retained', consecutive=self.registration_rejection_wait_streak+1,
            SOURCE_TTL_ns=SOURCE_AGE_NS, RAW_TTL_ns=RECEIPT_AGE_NS, published=False, data_qualified=False)
        import json
        require(len(json.dumps(timing, allow_nan=False, separators=(',', ':')).encode()) <= 2048,
                'registration_rejection_timing_capacity')
        anchor = self.ingress._release_rejected_registration_keep_anchor(self, attempt, original, digest)
        require(not self.stop_intent.is_set() and self.fault is None and self.ingress.fault is None,
                'physical_shadow_STOP_latched')
        require(anchor is current and self.ingress.previous is current and self.ingress.prepared is None,
                'exact_rejected_pair_anchor_required')
        self._pending = self._pending_digest = self._final_pending_seal = None
        self._preparation_anchor = self._preparation_attempt = None
        self._consumed_attempt = self._receiver_attempt = self._receiver_clock = self._final_attempt = None
        self._registration_rejection_attempt = self._registration_rejection_proof = None
        self.registration_rejection_wait_count += 1
        self.registration_rejection_wait_streak += 1
        self.registration_rejection_wait_max_streak = max(self.registration_rejection_wait_max_streak,
                                                           self.registration_rejection_wait_streak)
        self.registration_rejection_reason_counts[reason] += 1
        self.registration_rejection_wait_timing = timing
        self._registration_rejection_anchor = current
        self._registration_rejection_wait = _RegistrationRejectionWait(REGISTRATION_REJECTION_WAIT_REASON,
            self._generation, self, self._state.version)
        return self._registration_rejection_wait

    def is_owned_registration_rejection_wait(self, value):
        with self.lock, self.ingress.lock:
            try:
                anchor = self._registration_rejection_anchor
                if not (type(value) is _RegistrationRejectionWait and value is self._registration_rejection_wait and
                        value._owner is self and value.reason == REGISTRATION_REJECTION_WAIT_REASON and
                        value.input_ready is False and
                        value.generation == self._generation == self.ingress.generation and
                        self.ingress.clock_session == self._session and self._pid == os.getpid() and
                        self.ingress.profile_revision == self._revision == revision(self.contract) and
                        value.diagnostic_version == self._state.version and
                        self.registration_rejection_reanchor_enabled is True and
                        self._registration_rejection_configuration is True and
                        self.fault is None and self.ingress.fault is None and not self.stop_intent.is_set() and
                        self._pending is None and self._pending_digest is None and self._final_pending_seal is None and
                        self._registration_rejection_attempt is None and self._registration_rejection_proof is None and
                        self._preparation_anchor is None and self._preparation_attempt is None and
                        anchor is not None and self.ingress.previous is anchor and self.ingress.pending is None and
                        self.ingress.prepared is None and self.ingress._active is None and
                        self.ingress._prepared_digest is None and self.ingress._first_pair is True):
                    return False
                self.ingress._native_scan(anchor)
                for imu in tuple(self.ingress.imus):
                    self.ingress.validate_owned(imu)
                return True
            except Exception:
                return False

    def _fail(self, reason):
        self.fault = self.fault or str(reason)[:160]
        self.stop_intent.set()
        self.ingress.stop()
        self._pending = self._pending_digest = None
        self._registration_rejection_attempt = self._registration_rejection_proof = None
        self._registration_rejection_wait = self._registration_rejection_anchor = None
        self._consumed_attempt = self._consumed_wait = None
        self._preparation_anchor = self._preparation_attempt = self._preparation_wait = None
        self._receiver_attempt = self._receiver_clock = self._receiver_wait = None
        self._final_attempt = self._final_wait = self._final_pending_seal = None
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
                if self.preparation_causal_raw_expiry_reanchor_enabled:
                    self._preparation_anchor = (original,self.ingress._prepared_digest,
                        self._state,self._state.version,self._state.latest,None)
                with self.ingress.lock:
                    _clock, pairing = self._timed('prepare_association', self._clock_and_pair, original, preparation_stage='prepare_association')
                    self._timing['imu_count'] = len(original.imu_originals)
                    original_digest = self.ingress._digest_prepared(original)
                fit_start = time.perf_counter_ns()
                entry_state = self._state  # factory.lock is held: nothing can publish meanwhile
                try:
                    before = surfaces(scan_points(self._view(original.previous)), surface_cache=self._exact_surface_cache)
                    after = surfaces(scan_points(self._view(original.current)), surface_cache=self._exact_surface_cache)
                except ValueError as error:
                    # V21 OFF or unlisted: returns and the original exception is re-raised.
                    self._raise_registration_rejection(original, original_digest, entry_state, error=error)
                    raise
                dt = (original.current.wire.header.stamp_ns-original.previous.wire.header.stamp_ns)*1e-9
                require(0 < dt <= .2, 'bounded_original_physical_pair_interval_required')
                result = registration(before, after, abs(pairing.yaw_delta_rad), dt, None)
                if not (type(result) is dict and result.get('accepted') is True):
                    self._raise_registration_rejection(original, original_digest, entry_state, result=result)
                require(type(result) is dict and result.get('accepted') is True,
                        'diagnostic_native_fit_rejected:'+str(result.get('reason', 'invalid_result')))
                xy, yaw = tuple(float(v) for v in result['xy']), float(result['yaw'])
                if not (len(xy) == 2 and all(math.isfinite(v) for v in (*xy, yaw)) and
                        math.hypot(*xy) <= .6 and abs(yaw) < math.pi):
                    self._raise_registration_rejection(original, original_digest, entry_state, bounded=(xy, yaw))
                require(len(xy) == 2 and all(math.isfinite(v) for v in (*xy, yaw)) and
                        math.hypot(*xy) <= .6 and abs(yaw) < math.pi, 'bounded_sensor_frame_fit_required')
                self._timing['stages_ns']['numeric_fit'] = time.perf_counter_ns()-fit_start
                with self.ingress.lock:
                    clock, pairing = self._timed('postfit_association', self._clock_and_pair, original, preparation_stage='postfit_association')
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
                    if (self.final_scalar_causal_raw_expiry_reanchor_enabled or
                            self.preparation_causal_raw_expiry_reanchor_enabled):
                        self._final_pending_seal = (self._pending,self._pending._capability,
                            self._state,self._state.version,self._state.latest)
                    return self._pending
            except _ValidatedPreparationCausalRawExpiry as error:
                try:
                    with self.ingress.lock:
                        return self._abandon_preparation_raw_expiry(error)
                except Exception as invalid:
                    return self._fail(invalid)
            except _ValidatedRegistrationRejection as error:
                try:
                    with self.ingress.lock:
                        return self._abandon_registration_rejection(error)
                except Exception as invalid:
                    return self._fail(invalid)
            except Exception as error:
                return self._fail(error)
            finally:
                self._preparation_anchor = self._preparation_attempt = None
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
                    if self.preparation_causal_raw_expiry_reanchor_enabled:
                        self._preparation_anchor = (original,self.ingress._prepared_digest,
                            self._state,self._state.version,self._state.latest,
                            (prepared,prepared._capability,self._pending_digest))
                        self._timed('commit_initial_association', self._clock_and_pair, original,
                            preparation_stage='commit_initial_association')
                        self._preparation_anchor = self._preparation_attempt = None
                    else:
                        self._timed('commit_initial_association', self._clock_and_pair, original)
                    require(hashlib.sha256(repr(prepared.record).encode()+
                        self.ingress._digest_prepared(original).encode()).hexdigest() == self._pending_digest,
                        'original_physical_diagnostic_changed')
                    next_state = DiagnosticState(self._state.version+1, prepared.record)
                    response = hold('unverified_physical_REP145_extrinsics_braking_HOLD',
                                    diagnostic=prepared.record)
                    if self.receiver_commit_causal_raw_expiry_reanchor_enabled:
                        attempt = self._receiver_attempt = (object(),self._pending,original,
                            self._pending._capability,self._pending_digest)
                        outcome = self._timed('receiver_consume', self.ingress.commit, original,
                            _receiver_causal_raw_expiry=(self,attempt))
                        if self.ingress.is_owned_receiver_commit_raw_wait(outcome,self,attempt):
                            return self._abandon_receiver_commit_raw_expiry(outcome)
                        self._receiver_attempt = self._receiver_clock = None
                    else:
                        outcome = self._timed('receiver_consume', self.ingress.commit, original)
                    require(self.ingress.fault is None and self.ingress.prepared is None and
                            outcome.reason in ('first_pair_new_epoch',
                            'inputs_only_reviewed_typed_localizer_factory_missing'), outcome.reason)
                    # Receiver consumption is not publication. Recheck after it.
                    try:
                        self._timed('commit_consumed_association', self._clock_and_pair, original, pending=False)
                    except _ValidatedConsumedCausalRawExpiry as error:
                        return self._abandon_consumed_raw_expiry(error)
                    # Full association checks above can take time. Sample only
                    # after that work; then bound original scalar stamps without
                    # another full hash/decode or rewriting the prepared record.
                    response['diagnostic_stage_timing'] = self.timing_snapshot()
                    final_clock = self.ingress._evaluate()
                    try:
                        self._timed('commit_final_scalar_fence', self._scalar_margins, original, final_clock)
                    except _ValidatedFinalScalarCausalRawExpiry as error:
                        return self._abandon_final_scalar_raw_expiry(error)
                    require(not self.stop_intent.is_set(), 'physical_shadow_STOP_latched')
                    self._state = next_state  # one final immutable pointer swap
                    self._pending = self._pending_digest = self._final_pending_seal = None
                    if self.registration_rejection_reanchor_enabled:
                        self.registration_rejection_wait_streak = 0  # V21: a published fit ends the streak
                    return response
            except _ValidatedPreparationCausalRawExpiry as error:
                try:
                    with self.ingress.lock:
                        return self._abandon_preparation_raw_expiry(error)
                except Exception as invalid:
                    return self._fail(invalid)
            except Exception as error:
                return self._fail(error)
            finally:
                self._preparation_anchor = self._preparation_attempt = None
                self._timing['stages_ns']['commit_total'] = time.perf_counter_ns()-total_start
