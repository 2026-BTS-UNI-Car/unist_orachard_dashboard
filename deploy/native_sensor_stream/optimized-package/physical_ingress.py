"""Original native CDR observer ingress, explicit physical ROS system clock.

No /clock health is fabricated. Local system-time observations and original
native callback stamps are separate. This receiver never authorizes motion.
"""
import inputs
from bisect import bisect_left
from dataclasses import dataclass, replace
import time
import hashlib
import json
import math
import os

from common.native_ownership import (OriginalIngress, RawReceipt, Outcome, _ns, _sha,
    MAX_OWNERS, IMU_HORIZON_NS, PreparedInputs)
from common.original_cdr import LASER_TYPE, IMU_TYPE, CLOCK_TYPE, WireImu, WireScan, decode
from common.sensor_views import ImuSample, pair_imu, MAX_IMU_BRACKET_NS
from common.sensor_contract import _imu_group

SYSTEM_CLOCK = 'ROS_SYSTEM_TIME'
PHYSICAL_DOMAIN = 'physical.ros_system_time.original_callback.v1'
# Diagnostic ingestion bounds, NOT a physical command/safety envelope.
SOURCE_AGE_NS = 250_000_000
RECEIPT_AGE_NS = 200_000_000
PAIR_INTERVAL_NS = 200_000_000
SYSTEM_RAW_JUMP_NS = 50_000_000


def require(value, reason):
    if not value:
        raise ValueError(reason)


@dataclass(frozen=True)
class PhysicalClockReading:
    source_ns: int
    receipt_ns: int
    source_session: str
    raw_domain: str
    sample_sequence: int
    clock_kind: str = SYSTEM_CLOCK
    provenance_domain: str = PHYSICAL_DOMAIN


@dataclass(frozen=True)
class NativeCallbackProvenance:
    original_receipt: RawReceipt
    independent_ros_system_now_ns: int
    publisher_gid: bytes
    observer_contract_sha256: str
    receipt_origin: str = 'native_rmw_callback_linux_RAW'
    # Verified installed callback origin remains a separate root preflight.
    installed_origin_verified: bool = False


class _ValidatedImuGap(Exception):
    """Private signal; only the exact receiver's pending identity is accepted."""


class _ValidatedPreparationCausalRawExpiry(Exception):
    """Exact factory preparation association, before any diagnostic exists."""


class _ValidatedConsumedCausalRawExpiry(Exception):
    """Only the exact consumed factory attempt may abandon this expired pair."""


class _ValidatedFinalScalarCausalRawExpiry(Exception):
    """Exact already-sampled final selected-lower RAW expiry, before latch."""


class _ValidatedReceiverCommitCausalRawExpiry(Exception):
    """Exact initial receiver-commit association, before its generic latch."""


@dataclass(frozen=True)
class _ReceiverCommitCausalRawExpiryWait:
    reason: str
    generation: int
    _owner: object
    _factory: object
    _attempt: object
    input_ready: bool = False


@dataclass(frozen=True)
class _ImuGapWait:
    reason: str
    generation: int
    _owner: object
    prepared: object = None
    input_ready: bool = False


@dataclass(frozen=True)
class _ScanExpiryWait:
    reason: str
    generation: int
    _owner: object
    prepared: object = None
    input_ready: bool = False
    envelope: object = None


@dataclass(frozen=True)
class _OriginalCallbackExpiryWait:
    reason: str
    generation: int
    _owner: object
    input_ready: bool = False
    prepared: object = None
    envelope: object = None


class PhysicalOriginalIngress(OriginalIngress):
    """Bounded original ownership, causal IMUs and ONE pending native scan pair.

    consume_original is for the reviewed native observer only. Python raw=True
    message-info cannot supply callback GIDs on the observed rclpy versions.
    Native callback receipt may precede local pipe-processing RAW; it is never
    replaced by arrival time. Receipt ordering is tracked separately from local
    evaluation time, so delayed-but-fresh delivery does not fake freshness.
    """
    def __init__(self, *, raw_clock, source_now_ns, source_clock_kind,
                 scan_frame, imu_frame, profile_revision,
                 scan_publisher_gid, imu_publisher_gid, observer_contract_sha256,
                 scan_topic='/scan', imu_topic='/imu/data_raw', imu_gap_reanchor_enabled=False,
                 startup_expiry_discard_enabled=False, current_expiry_reanchor_enabled=False):
        require(type(imu_gap_reanchor_enabled) is bool, 'typed_IMU_gap_reanchor_option_required')
        self.__imu_gap_reanchor_enabled = imu_gap_reanchor_enabled
        require(type(startup_expiry_discard_enabled) is bool and type(current_expiry_reanchor_enabled) is bool,
                'typed_scan_expiry_options_required')
        self.__startup_expiry_discard_enabled = startup_expiry_discard_enabled
        self.__current_expiry_reanchor_enabled = current_expiry_reanchor_enabled
        require(callable(source_clock_kind), 'independent_ROS_clock_kind_provider_required')
        for gid in (scan_publisher_gid, imu_publisher_gid):
            require(type(gid) is bytes and len(gid) == 16 and any(gid),
                    'exact_actual_native_16_byte_publisher_gid_required')
        require(_sha(observer_contract_sha256), 'reviewed_native_observer_contract_SHA_required')
        super().__init__(raw_clock=raw_clock, source_now_ns=source_now_ns,
            scan_frame=scan_frame, imu_frame=imu_frame, profile_revision=profile_revision,
            scan_topic=scan_topic, imu_topic=imu_topic)
        self.source_clock_kind = source_clock_kind
        self.expected_gids = (scan_publisher_gid, imu_publisher_gid)
        self.observer_contract_sha256 = observer_contract_sha256
        self._physical_configuration = (source_clock_kind, self.expected_gids,
                                         observer_contract_sha256, imu_gap_reanchor_enabled,
                                         startup_expiry_discard_enabled, current_expiry_reanchor_enabled)
        self.callback_provenance = {}
        self._callback_context = None
        self._last_delivery_raw = self._last_delivery_source = None
        self._last_eval_raw = self._last_system = None
        self._system_sample_sequence = 0
        self._system_anchor = self._system_progress_raw = None
        self.admission_failure_timing = None
        self.admission_failure_timing_error = None
        self.imu_gap_failure_timing = None
        self.imu_gap_failure_timing_error = None
        self.receiver_end_scalar_failure_timing = None
        self.receiver_end_scalar_failure_timing_error = None
        self.causal_imu_failure_timing = None
        self.causal_imu_failure_timing_error = None
        self._gap_exception = self._gap_wait_outcome = None
        self._gap_scan_floor = self._gap_imu_floor = None
        self.gap_reanchor_count = 0
        self._observed_scan_header_floor = self._observed_imu_header_floor = None
        self._expiry_wait_outcome = None
        self.scan_expiry_wait_count = 0
        self.scan_expiry_wait_timing = None
        self._callback_expiry_wait = None
        self.original_callback_expiry_wait_count = 0
        self.original_callback_expiry_wait_timing = None
        self._preparation_expiry_proof = None
        self._consumed_expiry_proof = None
        self._receiver_expiry_proof = self._receiver_expiry_wait = None
        self._final_expiry_proof = None

    @property
    def imu_gap_reanchor_enabled(self):
        return self.__imu_gap_reanchor_enabled

    @property
    def startup_expiry_discard_enabled(self):
        return self.__startup_expiry_discard_enabled

    @property
    def current_expiry_reanchor_enabled(self):
        return self.__current_expiry_reanchor_enabled

    def _invalidate(self, reason):
        value = super()._invalidate(reason)
        if hasattr(self, 'callback_provenance'):
            self.callback_provenance.clear()
        self._callback_context = None
        self._gap_exception = self._gap_wait_outcome = None
        self._expiry_wait_outcome = None
        self._callback_expiry_wait = None
        self._preparation_expiry_proof = None
        self._consumed_expiry_proof = None
        self._receiver_expiry_proof = self._receiver_expiry_wait = None
        self._final_expiry_proof = None
        return value

    def reset(self):
        value = super().reset()
        self._reset_raw = self._last_raw
        self._last_delivery_raw = self._last_delivery_source = None
        self._last_eval_raw = self._last_system = None
        self._system_sample_sequence = 0
        self._system_anchor = self._system_progress_raw = None
        self._gap_scan_floor = self._gap_imu_floor = None
        self._observed_scan_header_floor = self._observed_imu_header_floor = None
        self._evaluate()
        return self._outcome('physical_new_system_time_epoch_no_clock_topic')

    def _receipt(self, receipt, generation):
        require(generation == self.generation, 'stale_callback_generation')
        require(self.fault is None, self.fault or 'receiver_fault')
        require(self._configuration == (self.scan_frame, self.imu_frame,
            self.scan_topic, self.imu_topic, self.profile_revision),
            'source_profile_configuration_changed')
        require(self._physical_configuration == (self.source_clock_kind,
            self.expected_gids, self.observer_contract_sha256, self.imu_gap_reanchor_enabled,
            self.startup_expiry_discard_enabled, self.current_expiry_reanchor_enabled),
            'physical_source_provenance_configuration_changed')
        require(type(receipt) is RawReceipt and receipt.domain == self.domain,
                'RAW_boot_domain_changed')
        require(receipt.ns >= self._reset_raw, 'native_callback_predates_receiver_epoch')

    def _evaluate(self):
        receipt = self.raw.sample()
        kind = self.source_clock_kind()
        source = self.source_now()
        with self.lock:
            self._receipt(receipt, self.generation)
            require(kind == SYSTEM_CLOCK, 'physical_configured_ROS_SYSTEM_TIME_required')
            require(_ns(source), 'independent_integer_ROS_system_now_required')
            if self._last_eval_raw is not None:
                dr, ds = receipt.ns-self._last_eval_raw, source-self._last_system
                require(dr >= 0, 'local_RAW_clock_regressed')
                require(ds >= 0, 'ROS_system_time_regressed')
                require(abs(ds-dr) <= SYSTEM_RAW_JUMP_NS, 'ROS_system_time_RAW_discontinuity')
            if self._system_anchor is None:
                self._system_anchor = (source, receipt.ns)
                self._system_progress_raw = receipt.ns
            elif source > self._last_system:
                self._system_progress_raw = receipt.ns
            require(receipt.ns-self._system_progress_raw < 100_000_000,
                    'independent_ROS_system_clock_stalled')
            source_delta = source-self._system_anchor[0]
            raw_delta = receipt.ns-self._system_anchor[1]
            require(abs(source_delta-raw_delta) <= SYSTEM_RAW_JUMP_NS,
                    'anchored_ROS_system_time_RAW_discontinuity')
            self._last_eval_raw, self._last_system = receipt.ns, source
            self._system_sample_sequence += 1
            return PhysicalClockReading(source, receipt.ns, self.clock_session,
                self.domain, self._system_sample_sequence)

    def on_clock(self, *_):
        raise ValueError('physical_system_time_does_not_receive_or_mint_clock_topic')

    def raw_callback(self, *_):
        raise ValueError('native_rmw_callback_original_CDR_and_actual_GID_required')

    def on_scan(self, *_args, **_kwargs):
        raise ValueError('use_original_native_callback_not_Python_arrival_retimestamp')

    on_imu = on_scan

    def consume_original(self, ros_type, original_cdr, *, callback_receipt,
                         callback_ros_system_now_ns, publisher_gid):
        """Preserve original callback clocks/CDR; no inferred timestamp/GID.

        Independently sampled node clock/RAW provider is still checked during
        processing. No callback metadata is authenticated by this Python seal.
        """
        with self.lock:
            generation = self.generation
            now = None
            try:
                require(ros_type in (LASER_TYPE, IMU_TYPE), 'physical_scan_and_imu_only')
                self._receipt(callback_receipt, generation)
                require(_ns(callback_ros_system_now_ns), 'original_callback_system_clock_required')
                expected = self.expected_gids[0 if ros_type == LASER_TYPE else 1]
                require(type(publisher_gid) is bytes and publisher_gid == expected,
                        'actual_native_callback_publisher_changed_or_missing')
                now = self._evaluate()
                if ros_type == LASER_TYPE and (self.startup_expiry_discard_enabled or
                                              self.current_expiry_reanchor_enabled):
                    _wire, ages = self._validated_observed_scan(original_cdr, callback_receipt,
                        callback_ros_system_now_ns, publisher_gid, now)
                    if self._positive_scan_expiry(ages) and self._scan_expiry_option():
                        return self._quarantine_scan_expiry(original_cdr, callback_receipt,
                            callback_ros_system_now_ns, publisher_gid, now)
                callback_ages = (now.receipt_ns-callback_receipt.ns,
                                 now.source_ns-callback_ros_system_now_ns)
                if ros_type == IMU_TYPE and self._scan_expiry_option() and (
                        callback_ages[0] >= RECEIPT_AGE_NS or callback_ages[1] >= SOURCE_AGE_NS):
                    return self._quarantine_imu_callback_expiry(original_cdr, callback_receipt,
                        callback_ros_system_now_ns, publisher_gid, now, stage='callback_admission')
                if not (0 <= callback_ages[0] < RECEIPT_AGE_NS and
                        0 <= callback_ages[1] < SOURCE_AGE_NS):
                    self._retain_callback_failure_timing(ros_type, original_cdr, callback_receipt,
                        callback_ros_system_now_ns, now, 'callback_admission')
                require(0 <= callback_ages[0] < RECEIPT_AGE_NS,
                        'stale_or_future_original_callback_RAW')
                require(0 <= callback_ages[1] < SOURCE_AGE_NS,
                        'stale_or_future_original_callback_system_time')
                if self._last_delivery_raw is not None:
                    require(callback_receipt.ns >= self._last_delivery_raw and
                            callback_ros_system_now_ns >= self._last_delivery_source,
                            'original_native_callback_delivery_regressed')
                self._last_delivery_raw = callback_receipt.ns
                self._last_delivery_source = callback_ros_system_now_ns
                self._callback_context = NativeCallbackProvenance(callback_receipt,
                    callback_ros_system_now_ns, publisher_gid, self.observer_contract_sha256)
                return self._consume(ros_type, original_cdr, callback_receipt,
                                     generation, publisher_gid)
            except Exception as error:
                if now is not None:
                    self._retain_callback_failure_timing(ros_type,original_cdr,callback_receipt,
                        callback_ros_system_now_ns,now,'callback_admission')
                return self._invalidate(str(error))
            finally:
                self._callback_context = None

    def _callback_timing(self, ros_type, cdr, receipt, callback_system, clock, stage):
        wire = decode(cdr, ros_type)
        return dict(schema='ubts.native.original_callback_age.v1', stage=stage, ros_type=ros_type,
            original_CDR_sha256=hashlib.sha256(cdr).hexdigest(), header_ns=wire.header.stamp_ns,
            callback_RAW_ns=receipt.ns, callback_SYSTEM_ns=callback_system,
            comparison=dict(source_ns=clock.source_ns, RAW_ns=clock.receipt_ns,
                source_session=clock.source_session, raw_domain=clock.raw_domain,
                sample_sequence=clock.sample_sequence),
            ages=dict(Header_age_ns=clock.source_ns-wire.header.stamp_ns,
                callback_RAW_age_ns=clock.receipt_ns-receipt.ns,
                callback_SYSTEM_age_ns=clock.source_ns-callback_system),
            SOURCE_TTL_ns=SOURCE_AGE_NS, RAW_TTL_ns=RECEIPT_AGE_NS, data_qualified=False)

    def _retain_callback_failure_timing(self, ros_type, cdr, receipt, callback_system, clock, stage):
        if self.admission_failure_timing is not None or self.admission_failure_timing_error is not None:
            return
        try:
            value = self._callback_timing(ros_type,cdr,receipt,callback_system,clock,stage)
            raw = json.dumps(value,allow_nan=False,separators=(',',':'))
            require(len(raw.encode()) <= 2048,'admission_failure_timing_capacity')
            self.admission_failure_timing=json.loads(raw)
        except Exception as error:
            self.admission_failure_timing_error=str(error)[:128]

    def _validated_observed_imu(self, cdr, receipt, callback_system, gid, clock, *, envelope=None):
        self._receipt(receipt,self.generation)
        require(type(clock) is PhysicalClockReading and clock.source_session == self.clock_session and
                clock.raw_domain == self.domain and clock.source_ns == self._last_system and
                clock.receipt_ns == self._last_eval_raw and clock.sample_sequence == self._system_sample_sequence and
                _ns(clock.source_ns) and _ns(clock.receipt_ns) and _ns(clock.sample_sequence) and
                clock.clock_kind == SYSTEM_CLOCK and clock.provenance_domain == PHYSICAL_DOMAIN,
                'exact_existing_observed_imu_clock_required')
        require(type(gid) is bytes and gid == self.expected_gids[1] and _ns(callback_system),
                'exact_original_imu_callback_required')
        wire=decode(cdr,IMU_TYPE)
        require(type(wire) is WireImu and wire.header.frame_id == self.imu_frame,'original_imu_frame_mismatch')
        require(wire.header.stamp_ns <= callback_system,'source_header_future_at_original_native_callback')
        q,qc,angular,ac,_linear,_lc=(tuple(float(x) for x in group) for group in wire.groups)
        groups=dict(quaternion=q,orientation_covariance=qc,angular_velocity=angular,
                    angular_velocity_covariance=ac)
        for name,value in (('orientation',_imu_group(groups,'quaternion','orientation_covariance',4)),
                           ('angular_velocity',_imu_group(groups,'angular_velocity','angular_velocity_covariance',3))):
            require(value.valid and value.available,'imu_'+name+'_'+value.reason)
        floor=self._observed_imu_header_floor
        if envelope is None:
            require(floor is None or wire.header.stamp_ns > floor,'observed_original_Header_rewind')
        else:
            self.validate_owned(envelope)
            require(envelope.original_cdr == cdr and envelope.receipt is receipt and
                    envelope.publisher_gid == gid and wire.header.stamp_ns == floor,
                    'exact_current_observed_imu_required')
        ages=(clock.receipt_ns-receipt.ns,clock.source_ns-callback_system,clock.source_ns-wire.header.stamp_ns)
        require(all(type(v) is int and v >= 0 for v in ages),'future_observed_original_imu_or_callback')
        if self._last_delivery_raw is not None:
            require(receipt.ns >= self._last_delivery_raw and callback_system >= self._last_delivery_source,
                    'original_native_callback_delivery_regressed')
        if self.imus:
            require(wire.header.stamp_ns > self.imus[-1].wire.header.stamp_ns and
                    gid == self.imus[-1].publisher_gid,'original_imu_order_or_publisher_changed')
        return wire,ages

    def _quarantine_imu_callback_expiry(self, cdr, receipt, callback_system, gid, clock,
                                      *, stage, envelope=None):
        with self.lock:
            wire,ages=self._validated_observed_imu(cdr,receipt,callback_system,gid,clock,envelope=envelope)
            require(self._scan_expiry_option() and (ages[0] >= RECEIPT_AGE_NS or ages[1] >= SOURCE_AGE_NS),
                    'typed_positive_original_imu_callback_expiry_required')
            require(self._active is None,'active_prepare_cannot_be_expiry_quarantined')
            for owner,_digest in tuple(self._owners.values()):self.validate_owned(owner)
            if self.prepared is not None:
                require(self._digest_prepared(self.prepared) == self._prepared_digest,
                        'original_prepared_ownership_changed')
            require(type(self.original_callback_expiry_wait_count) is int and _ns(self.original_callback_expiry_wait_count+1),'original_callback_wait_counter_exhausted')
            timing=self._callback_timing(IMU_TYPE,cdr,receipt,callback_system,clock,stage)
            require(len(json.dumps(timing,allow_nan=False,separators=(',',':')).encode()) <= 2048,
                    'original_callback_wait_timing_capacity')
            self._observed_imu_header_floor=self._gap_imu_floor=wire.header.stamp_ns
            self._gap_scan_floor=self._observed_scan_header_floor
            self._last_delivery_raw,self._last_delivery_source=receipt.ns,callback_system
            self.previous=self.pending=self.prepared=self._active=None
            self._prepared_digest=None
            self.imus.clear();self._owners.clear();self.callback_provenance.clear()
            self._gap_exception=self._gap_wait_outcome=None
            self._expiry_wait_outcome=None
            self._preparation_expiry_proof=self._consumed_expiry_proof=None
            self._receiver_expiry_proof=self._receiver_expiry_wait=self._final_expiry_proof=None
            self._first_pair=True
            self.original_callback_expiry_wait_count+=1
            self.original_callback_expiry_wait_timing=timing
            result=_OriginalCallbackExpiryWait('waiting_for_fresh_pair_after_positive_original_imu_callback_expiry',
                                               self.generation,self)
            self._callback_expiry_wait=result
            return result

    def is_owned_original_callback_expiry_wait(self,value):
        with self.lock:
            return (type(value) is _OriginalCallbackExpiryWait and value is self._callback_expiry_wait and
                value._owner is self and value.generation == self.generation and self.fault is None and
                value.reason == 'waiting_for_fresh_pair_after_positive_original_imu_callback_expiry' and
                value.input_ready is False and value.prepared is None and value.envelope is None and
                self.previous is None and self.pending is None and self.prepared is None and
                self._active is None and not self._owners and not self.imus)

    def _consume_imu(self,cdr,receipt,generation,publisher_gid):
        # Default OFF retains the parent implementation. Enabled mode repeats
        # callback age at the parent's existing second sample, never restamps.
        if not self._scan_expiry_option():
            return super()._consume_imu(cdr,receipt,generation,publisher_gid)
        envelope=None
        try:
            envelope=self._issue(cdr,IMU_TYPE,receipt,generation,publisher_gid)
            clock=self._evaluate()
            with self.lock:
                self.validate_owned(envelope)
                callback=self.callback_provenance[id(envelope)][1]
                raw_age=clock.receipt_ns-receipt.ns
                system_age=clock.source_ns-callback.independent_ros_system_now_ns
                if raw_age >= RECEIPT_AGE_NS or system_age >= SOURCE_AGE_NS:
                    return self._quarantine_imu_callback_expiry(cdr,receipt,
                        callback.independent_ros_system_now_ns,publisher_gid,clock,
                        stage='imu_retention',envelope=envelope)
                require(raw_age >= 0 and system_age >= 0,'future_observed_original_imu_or_callback')
                wire=envelope.wire
                if wire.header.frame_id != self.imu_frame:raise ValueError('original_imu_frame_mismatch')
                if wire.header.stamp_ns > clock.source_ns:raise ValueError('imu_not_causally_available')
                if self.imus and wire.header.stamp_ns <= self.imus[-1].wire.header.stamp_ns:
                    raise ValueError('original_imu_source_sequence_regressed')
                if self.imus and publisher_gid != self.imus[-1].publisher_gid:
                    raise ValueError('original_imu_publisher_changed')
                cutoff=clock.source_ns-IMU_HORIZON_NS
                while self.imus and self.imus[0].wire.header.stamp_ns < cutoff:self.imus.popleft()
                if wire.header.stamp_ns >= cutoff:self.imus.append(envelope)
                return self._outcome('original_imu_retained',envelope=envelope)
        except Exception as error:
            with self.lock:
                if generation != self.generation:return self._outcome('late_callback_discarded',envelope=envelope)
                if self.fault is not None:return self._outcome(self.fault,envelope=envelope)
                result=self._invalidate(str(error))
                return Outcome(result.reason,result.generation,envelope=envelope)

    def _issue(self, cdr, ros_type, receipt, generation, publisher_gid):
        context = self._callback_context
        require(type(context) is NativeCallbackProvenance and
                context.original_receipt is receipt and context.publisher_gid == publisher_gid,
                'original_native_callback_context_required')
        value = super()._issue(cdr, ros_type, receipt, generation, publisher_gid)
        require(value.wire.header.stamp_ns <= context.independent_ros_system_now_ns,
                'source_header_future_at_original_native_callback')
        floor = self._gap_scan_floor if ros_type == LASER_TYPE else self._gap_imu_floor
        if floor is not None:
            require(value.wire.header.stamp_ns > floor,
                    'original_Header_rewind_after_IMU_gap')
            if ros_type == LASER_TYPE:
                self._gap_scan_floor = value.wire.header.stamp_ns
            else:
                self._gap_imu_floor = value.wire.header.stamp_ns
        observed = self._observed_scan_header_floor if ros_type == LASER_TYPE else self._observed_imu_header_floor
        require(observed is None or value.wire.header.stamp_ns > observed,
                'observed_original_Header_rewind')
        if ros_type == LASER_TYPE:
            self._observed_scan_header_floor = value.wire.header.stamp_ns
        else:
            self._observed_imu_header_floor = value.wire.header.stamp_ns
        self.callback_provenance[id(value)] = (value, context,
            hashlib.sha256(repr(context).encode()).hexdigest())
        self.callback_provenance = {k: v for k, v in self.callback_provenance.items()
                                    if k in self._owners}
        return value

    def validate_owned(self, envelope):
        value = super().validate_owned(envelope)
        extra = self.callback_provenance.get(id(envelope))
        require(extra is not None and extra[0] is envelope and
                extra[1].original_receipt is envelope.receipt and
                extra[1].publisher_gid == envelope.publisher_gid and
                extra[1].observer_contract_sha256 == self.observer_contract_sha256 and
                hashlib.sha256(repr(extra[1]).encode()).hexdigest() == extra[2],
                'original_physical_callback_provenance_changed')
        return value

    def _scan_fresh(self, envelope, clock):
        self.validate_owned(envelope)
        require(type(clock) is PhysicalClockReading and
                clock.source_session == self.clock_session and clock.raw_domain == self.domain,
                'physical_system_clock_epoch_required')
        try:
            require(0 <= clock.source_ns-envelope.wire.header.stamp_ns < SOURCE_AGE_NS,
                    'stale_or_future_native_source')
            require(0 <= clock.receipt_ns-envelope.receipt.ns < RECEIPT_AGE_NS,
                    'stale_or_future_native_receipt')
        except Exception:
            # These are the failed attempt's original comparison clocks, not
            # a later sample. Keep the first bounded measurement after owned
            # envelope and clock-epoch validation, before _invalidate clears it.
            if self.admission_failure_timing is None and self.admission_failure_timing_error is None:
                try:
                    callback = self.callback_provenance[id(envelope)][1]
                    header_ns = envelope.wire.header.stamp_ns
                    source_age = clock.source_ns-header_ns
                    receipt_age = clock.receipt_ns-envelope.receipt.ns
                    callback_age = clock.source_ns-callback.independent_ros_system_now_ns
                    header_to_callback = callback.independent_ros_system_now_ns-header_ns
                    value = dict(schema='ubts.native.admission_failure_timing.v1',
                        stage='owned_scan_freshness', source_sequence=envelope.source_sequence,
                        source_now_ns=clock.source_ns, receipt_now_ns=clock.receipt_ns,
                        callback_raw_ns=envelope.receipt.ns,
                        callback_system_ns=callback.independent_ros_system_now_ns,
                        header_ns=header_ns, header_age_ns=source_age,
                        callback_raw_age_ns=receipt_age, callback_system_age_ns=callback_age,
                        header_to_callback_ns=header_to_callback,
                        RAW_TTL_ns=RECEIPT_AGE_NS, SOURCE_TTL_ns=SOURCE_AGE_NS,
                        checks=dict(header_fresh=0 <= source_age < SOURCE_AGE_NS,
                            raw_fresh=0 <= receipt_age < RECEIPT_AGE_NS,
                            system_fresh=0 <= callback_age < SOURCE_AGE_NS,
                            header_causal=header_to_callback >= 0), data_qualified=False)
                    raw = json.dumps(value, allow_nan=False, separators=(',', ':'))
                    require(len(raw.encode('utf-8')) <= 1024, 'admission_failure_timing_capacity')
                    self.admission_failure_timing = json.loads(raw)
                except Exception as error:
                    self.admission_failure_timing_error = str(error)[:128]
            raise

    def _consume_scan(self, cdr, receipt, generation, publisher_gid):
        envelope = None
        try:
            envelope = self._issue(cdr, LASER_TYPE, receipt, generation, publisher_gid)
            clock = self._evaluate()
            scan = self._native_scan(envelope)
            a, b, inc, dt, scan_time, minimum, maximum = envelope.wire.metadata
            require(120 <= len(scan.ranges) <= 4096 and 0 < float(inc) <= .1 and
                    0 <= float(dt) and 0 <= float(scan_time) <= .5 and
                    0 <= float(minimum) < float(maximum) <= 100 and
                    abs(float(b)-(float(a)+(len(scan.ranges)-1)*float(inc))) <= 2e-4 and
                    (len(scan.ranges)-1)*float(dt) <= .2,
                    'bounded_original_physical_scan_geometry_required')
            callback = self.callback_provenance[id(envelope)][1]
            last_beam_ns = scan.stamp_ns + math.ceil((len(scan.ranges)-1)*float(dt)*1e9)
            require(last_beam_ns <= callback.independent_ros_system_now_ns,
                    'rolling_scan_wire_time_future_at_original_callback')
            if self._scan_expiry_option():
                _wire, ages = self._validated_observed_scan(cdr, receipt,
                    callback.independent_ros_system_now_ns, publisher_gid, clock, envelope=envelope)
                if self._positive_scan_expiry(ages):
                    return self._quarantine_scan_expiry(cdr, receipt,
                        callback.independent_ros_system_now_ns, publisher_gid, clock, envelope=envelope)
            self._scan_fresh(envelope, clock)
            require(self.pending is None and self._active is None and self.prepared is None,
                    'pending_pair_replaced_continuity_lost')
            previous = self.previous
            if previous is not None:
                interval = scan.stamp_ns-previous.wire.header.stamp_ns
                require(0 < interval <= PAIR_INTERVAL_NS and
                        previous.receipt.ns <= envelope.receipt.ns,
                        'missing_or_reordered_adjacent_native_pair')
                require(previous.publisher_gid == envelope.publisher_gid,
                        'original_scan_publisher_changed')
                self.pending = (previous, envelope)
                self.max_pending_seen = max(self.max_pending_seen, 1)
            self.previous = envelope
            return self._outcome('pending_physical_original_pair' if previous else
                                 'first_physical_original_scan', envelope=envelope)
        except Exception as error:
            return self._invalidate(str(error))

    def _scan_expiry_option(self):
        return (self.startup_expiry_discard_enabled if self.previous is None
                else self.current_expiry_reanchor_enabled)

    @staticmethod
    def _positive_scan_expiry(ages):
        return (ages['Header_age_ns'] >= SOURCE_AGE_NS or
                ages['callback_RAW_age_ns'] >= RECEIPT_AGE_NS or
                ages['callback_SYSTEM_age_ns'] >= SOURCE_AGE_NS)

    def _validated_observed_scan(self, cdr, receipt, callback_system, gid, clock, *, envelope=None):
        self._receipt(receipt, self.generation)
        require(type(clock) is PhysicalClockReading and clock.source_session == self.clock_session and
                clock.raw_domain == self.domain and clock.source_ns == self._last_system and
                clock.receipt_ns == self._last_eval_raw and
                clock.sample_sequence == self._system_sample_sequence and
                _ns(clock.source_ns) and _ns(clock.receipt_ns) and _ns(clock.sample_sequence) and
                clock.clock_kind == SYSTEM_CLOCK and clock.provenance_domain == PHYSICAL_DOMAIN,
                'exact_existing_observed_scan_clock_required')
        require(type(gid) is bytes and gid == self.expected_gids[0],
                'actual_native_callback_publisher_changed_or_missing')
        require(_ns(callback_system), 'original_callback_system_clock_required')
        wire=decode(cdr,LASER_TYPE)
        require(type(wire) is WireScan and wire.header.frame_id == self.scan_frame,
                'original_scan_frame_mismatch')
        a,b,inc,dt,cycle,minimum,maximum=map(float,wire.metadata)
        require(all(math.isfinite(v) for v in (a,b,inc,dt,cycle,minimum,maximum)) and
                120 <= wire.count <= 4096 and 0 < inc <= .1 and dt >= 0 and 0 <= cycle <= .5 and
                0 <= minimum < maximum <= 100 and abs(b-(a+(wire.count-1)*inc)) <= 2e-4 and
                (wire.count-1)*dt <= .2, 'bounded_original_physical_scan_geometry_required')
        require(wire.header.stamp_ns <= callback_system,
                'source_header_future_at_original_native_callback')
        require(wire.header.stamp_ns+math.ceil((wire.count-1)*dt*1e9) <= callback_system,
                'rolling_scan_wire_time_future_at_original_callback')
        floor=self._observed_scan_header_floor
        if envelope is None:
            require(floor is None or wire.header.stamp_ns > floor,'observed_original_Header_rewind')
        else:
            self.validate_owned(envelope)
            require(envelope.original_cdr == cdr and envelope.receipt is receipt and
                    envelope.publisher_gid == gid and wire.header.stamp_ns == floor,
                    'exact_current_observed_scan_required')
        ages=dict(Header_age_ns=clock.source_ns-wire.header.stamp_ns,
                  callback_RAW_age_ns=clock.receipt_ns-receipt.ns,
                  callback_SYSTEM_age_ns=clock.source_ns-callback_system)
        require(all(type(age) is int and age >= 0 for age in ages.values()),
                'future_observed_original_scan_or_callback')
        if self._last_delivery_raw is not None:
            require(receipt.ns >= self._last_delivery_raw and callback_system >= self._last_delivery_source,
                    'original_native_callback_delivery_regressed')
        return wire,ages

    def observe_priming_scan(self, cdr, *, callback_receipt, callback_ros_system_now_ns,
                             publisher_gid, clock):
        """Validate a real unadmitted scan and retain Header/delivery floors only."""
        with self.lock:
            require(self.previous is None and self.pending is None and self.prepared is None and
                    self._active is None,'one_empty_receiver_before_priming_scan_required')
            wire,ages=self._validated_observed_scan(cdr,callback_receipt,
                callback_ros_system_now_ns,publisher_gid,clock)
            if self._positive_scan_expiry(ages):
                require(self.startup_expiry_discard_enabled,'positive_priming_scan_expiry_not_enabled')
                return self._quarantine_scan_expiry(cdr,callback_receipt,
                    callback_ros_system_now_ns,publisher_gid,clock)
            self._observed_scan_header_floor=wire.header.stamp_ns
            self._last_delivery_raw=callback_receipt.ns
            self._last_delivery_source=callback_ros_system_now_ns
            return self._outcome('validated_unadmitted_priming_scan')

    def _quarantine_scan_expiry(self, cdr, receipt, callback_system, gid, clock, *, envelope=None):
        with self.lock:
            wire,ages=self._validated_observed_scan(cdr,receipt,callback_system,gid,clock,envelope=envelope)
            require(self._scan_expiry_option() and self._positive_scan_expiry(ages),
                    'typed_positive_observed_scan_expiry_required')
            # Never discard unknown or mutated ownership state under a freshness label.
            require(self._active is None,'active_prepare_cannot_be_expiry_quarantined')
            for owner,_digest in tuple(self._owners.values()):self.validate_owned(owner)
            if self.prepared is not None:
                require(self._digest_prepared(self.prepared) == self._prepared_digest,
                        'original_prepared_ownership_changed')
            require(type(self.scan_expiry_wait_count) is int and _ns(self.scan_expiry_wait_count+1),
                    'scan_expiry_wait_counter_exhausted')
            stage='startup' if self.previous is None else 'current'
            timing=dict(schema='ubts.native.positive_scan_expiry_wait.v1',stage=stage,
                header_ns=wire.header.stamp_ns,callback_RAW_ns=receipt.ns,callback_SYSTEM_ns=callback_system,
                comparison=dict(source_ns=clock.source_ns,RAW_ns=clock.receipt_ns),ages=ages,
                SOURCE_TTL_ns=SOURCE_AGE_NS,RAW_TTL_ns=RECEIPT_AGE_NS,
                data_qualified=False,original_geometry_validated=True)
            require(len(json.dumps(timing,allow_nan=False,separators=(',',':')).encode()) <= 1024,
                    'scan_expiry_wait_timing_capacity')
            self._observed_scan_header_floor=wire.header.stamp_ns
            self._gap_scan_floor=wire.header.stamp_ns
            if self.imus:self._gap_imu_floor=self.imus[-1].wire.header.stamp_ns
            self._last_delivery_raw,self._last_delivery_source=receipt.ns,callback_system
            self.previous=self.pending=self.prepared=self._active=None
            self._prepared_digest=None
            self.imus.clear();self._owners.clear();self.callback_provenance.clear()
            self._gap_exception=self._gap_wait_outcome=None
            self._first_pair=True
            self.scan_expiry_wait_count+=1
            self.scan_expiry_wait_timing=timing
            result=_ScanExpiryWait('waiting_for_fresh_scan_after_positive_'+stage+'_expiry',self.generation,self)
            self._expiry_wait_outcome=result
            return result

    def is_owned_scan_expiry_wait(self,value):
        with self.lock:
            return (type(value) is _ScanExpiryWait and value is self._expiry_wait_outcome and
                    value._owner is self and value.generation == self.generation and self.fault is None and
                    self.previous is None and self.pending is None and self.prepared is None and
                    self._active is None and not self._owners and not self.imus)

    def _check_receiver_prepare_positive_age(self, pair, originals, clock, token, first, owner, stage):
        if owner is None:
            return
        from physical_factory import PhysicalNativeShadowFactory
        require(type(owner) is PhysicalNativeShadowFactory and owner.ingress is self and
                owner.preparation_causal_raw_expiry_reanchor_enabled is True,
                'exact_positive_age_receiver_factory_required')
        current = pair[1]
        lower = next((v for v in reversed(originals)
                      if v.wire.header.stamp_ns <= current.wire.header.stamp_ns),None)
        values = (current,) if lower is None or not originals else (current,lower,originals[-1])
        if not any(clock.source_ns-v.wire.header.stamp_ns >= SOURCE_AGE_NS or
                   clock.receipt_ns-v.receipt.ns >= RECEIPT_AGE_NS for v in values):
            return
        # Failure-only provisional descriptor: original envelopes are retained,
        # but this is NEVER installed/admitted as a PreparedInputs capability.
        samples = tuple(ImuSample(v.wire.header.stamp_ns,v.receipt.ns,v.wire.header.frame_id,
            tuple(float(x) for x in v.wire.groups[0]),tuple(float(x) for x in v.wire.groups[2]),
            tuple(float(x) for x in v.wire.groups[1]),tuple(float(x) for x in v.wire.groups[3]))
            for v in originals)
        pairing = pair_imu(samples,pair[0].wire.header.stamp_ns,current.wire.header.stamp_ns,
            frame_id=self.imu_frame,receipt_now_ns=clock.receipt_ns,max_roll_pitch_rad=None)
        require(pairing.reason == 'bracketed_available_imu',pairing.reason)
        original = PreparedInputs(self.generation,*pair,originals,samples,pairing,clock,first)
        with self.lock:
            digest = self._digest_prepared(original)
            owner._preparation_anchor = (original,digest,owner._state,owner._state.version,
                owner._state.latest,(token,pair,originals,digest))
            owner._check_positive_age_expiry(original,clock,stage,pending=False)

    def prepare(self, *, positive_age_factory=None):
        """Freeze only original interpolation brackets plus newest context.

        The500ms horizon remains the initial retention/selection bound. An
        unused historical sample is not an enduring association dependency.
        Exact bracket samples, lower-end gyro and original callback clocks are
        preserved; a fresh current/newest sample cannot replace a missing pair.
        """
        generation = self.generation
        try:
            with self.lock:
                if self.fault is not None:
                    return self._outcome(self.fault)
                pair, self.pending = self.pending, None
                if pair is None:
                    return self._outcome('no_pending_original_pair')
                token = self._active = object()
                originals = tuple(self.imus)
                first = self._first_pair
            clock = self._evaluate()
            self._check_receiver_prepare_positive_age(pair,originals,clock,token,first,
                positive_age_factory,'receiver_prepare_association')
            originals = tuple(e for e in originals
                if e.wire.header.stamp_ns >= clock.source_ns-IMU_HORIZON_NS)
            samples, pairing = self._association(*pair, originals, clock,
                allow_expected_gap=self.imu_gap_reanchor_enabled)
            # Keep exact association endpoints and independently bound latest
            # original context; never synthesize a sample or change its stamp.
            indices = sorted(set(pairing.start_indices+pairing.end_indices+(len(originals)-1,)))
            selected = tuple(originals[i] for i in indices)
            selected_samples = tuple(samples[i] for i in indices)
            remap = {old: new for new, old in enumerate(indices)}
            selected_pairing = replace(pairing,
                start_indices=tuple(remap[i] for i in pairing.start_indices),
                end_indices=tuple(remap[i] for i in pairing.end_indices))
            prepared = PreparedInputs(generation, *pair, selected, selected_samples,
                                      selected_pairing, clock, first)
            end_clock = self._evaluate()
            self._check_receiver_prepare_positive_age(pair,selected,end_clock,token,first,
                positive_age_factory,'receiver_prepare_end_association')
            check_samples, check_pairing = self._association(*pair, selected, end_clock)
            require(check_samples == selected_samples and check_pairing == selected_pairing,
                    'minimal_original_brackets_changed')
            with self.lock:
                if generation != self.generation or token is not self._active:
                    return self._outcome('late_prepare_discarded')
                self.prepared, self._active = prepared, None
                self._prepared_digest = self._digest_prepared(prepared)
                return self._outcome('prepared_original_inputs_no_motion_factory',
                    prepared=prepared, input_ready=not first)
        except _ValidatedPreparationCausalRawExpiry as error:
            try:
                with self.lock:
                    require(positive_age_factory is not None,'owned_receiver_positive_age_factory_required')
                    return positive_age_factory._abandon_preparation_raw_expiry(error)
            except Exception as invalid:
                return self._invalidate(str(invalid))
        except _ValidatedImuGap as error:
            with self.lock:
                if generation != self.generation:
                    return self._outcome('late_prepare_discarded')
                try:
                    return self._quarantine_expected_gap(error, token)
                except Exception as invalid:
                    return self._invalidate(str(invalid))
        except Exception as error:
            with self.lock:
                if generation != self.generation:
                    return self._outcome('late_prepare_discarded')
                return self._invalidate(str(error))

    def _retain_imu_gap_failure_timing(self, previous, current, originals, samples, clock):
        # pair_imu has already validated window ordering/frames/receipt
        # causality. Retain its FIRST failed boundary without interpolation,
        # numeric-group qualification or a replacement comparison clock.
        if self.imu_gap_failure_timing is not None or self.imu_gap_failure_timing_error is not None:
            return
        try:
            stamps = tuple(s.stamp_ns for s in samples)
            failed = None
            for boundary, at in (('previous', previous.wire.header.stamp_ns),
                                 ('current', current.wire.header.stamp_ns)):
                index = bisect_left(stamps, at)
                if index < len(stamps) and stamps[index] == at:
                    lo = hi = index
                elif 0 < index < len(stamps):
                    lo, hi = index-1, index
                else:
                    raise ValueError('failed_gap_bracket_unavailable')
                if stamps[hi]-stamps[lo] > MAX_IMU_BRACKET_NS:
                    failed = boundary, lo, hi
                    break
            require(failed is not None, 'failed_gap_not_found')
            boundary, lo, hi = failed
            def endpoint(index):
                value = originals[index]
                return dict(source_sequence=value.source_sequence,
                    header_ns=value.wire.header.stamp_ns, callback_RAW_ns=value.receipt.ns,
                    source_age_ns=clock.source_ns-value.wire.header.stamp_ns,
                    RAW_age_ns=clock.receipt_ns-value.receipt.ns)
            value = dict(schema='ubts.native.imu_gap_failure_timing.v1',
                stage='association_bracket', scan_boundary=boundary,
                scan_headers_ns=[previous.wire.header.stamp_ns, current.wire.header.stamp_ns],
                comparison=dict(source_ns=clock.source_ns, RAW_ns=clock.receipt_ns),
                bracket_indices=[lo, hi], lower=endpoint(lo), upper=endpoint(hi),
                gap_ns=stamps[hi]-stamps[lo], gap_limit_ns=MAX_IMU_BRACKET_NS,
                SOURCE_TTL_ns=SOURCE_AGE_NS, RAW_TTL_ns=RECEIPT_AGE_NS,
                numeric_groups_validated=False, data_qualified=False)
            raw = json.dumps(value, allow_nan=False, separators=(',', ':'))
            require(len(raw.encode('utf-8')) <= 1024, 'imu_gap_failure_timing_capacity')
            self.imu_gap_failure_timing = json.loads(raw)
        except Exception as error:
            self.imu_gap_failure_timing_error = str(error)[:128]

    def _gap_endpoint_guards(self, previous, current, originals, clock):
        self.validate_owned(previous)
        self._native_scan(previous)
        self._native_scan(current)
        self._scan_fresh(current, clock)
        require(0 < current.wire.header.stamp_ns-previous.wire.header.stamp_ns <= PAIR_INTERVAL_NS
                and previous.receipt.ns <= current.receipt.ns,
                'missing_or_reordered_adjacent_native_pair')
        stamps, receipts = [], []
        for value in originals:
            self.validate_owned(value)
            require(type(value.wire) is WireImu and value.wire.header.frame_id == self.imu_frame,
                    'original_imu_window_required')
            stamps.append(value.wire.header.stamp_ns)
            receipts.append(value.receipt.ns)
            require(clock.source_ns-IMU_HORIZON_NS <= stamps[-1] <= clock.source_ns and
                    receipts[-1] <= clock.receipt_ns, 'imu_not_causally_available')
        require(1 <= len(originals) <= 64 and all(a < b for a, b in zip(stamps, stamps[1:]))
                and all(a <= b for a, b in zip(receipts, receipts[1:])), 'reordered_imu_window')
        brackets, gaps = [], []
        for at in (previous.wire.header.stamp_ns, current.wire.header.stamp_ns):
            index = bisect_left(stamps, at)
            if index < len(stamps) and stamps[index] == at:
                lo = hi = index
            elif 0 < index < len(stamps):
                lo, hi = index-1, index
            else:
                raise ValueError('imu_outside_retained_span')
            brackets.append((lo, hi))
            gaps.append(stamps[hi]-stamps[lo])
        # Validate BOTH boundaries before treating a gap as expected data loss.
        # No yaw interpolation/angles/registration is performed over that gap.
        for index in sorted(set(brackets[0]+brackets[1])):
            q, qc, angular, ac, _linear, _lc = tuple(tuple(float(v) for v in group)
                for group in originals[index].wire.groups)
            values = dict(quaternion=q, orientation_covariance=qc,
                          angular_velocity=angular, angular_velocity_covariance=ac)
            for name, group in (
                    ('orientation', _imu_group(values, 'quaternion', 'orientation_covariance', 4)),
                    ('angular_velocity', _imu_group(values, 'angular_velocity', 'angular_velocity_covariance', 3))):
                require(group.valid and group.available, 'imu_'+name+'_'+group.reason)
        lower = originals[brackets[1][0]]
        if not (0 <= clock.source_ns-lower.wire.header.stamp_ns < SOURCE_AGE_NS and
                0 <= clock.receipt_ns-lower.receipt.ns < RECEIPT_AGE_NS):
            self._retain_causal_imu_failure_timing(lower, clock)
        require(0 <= clock.source_ns-lower.wire.header.stamp_ns < SOURCE_AGE_NS and
                0 <= clock.receipt_ns-lower.receipt.ns < RECEIPT_AGE_NS,
                'stale_original_causal_imu')
        newest = originals[-1]
        require(0 <= clock.source_ns-newest.wire.header.stamp_ns < SOURCE_AGE_NS,
                'final_physical_native_source_deadline')
        require(0 <= clock.receipt_ns-newest.receipt.ns < RECEIPT_AGE_NS,
                'final_physical_native_receipt_deadline')
        require(any(g > MAX_IMU_BRACKET_NS for g in gaps), 'owned_expected_IMU_gap_required')

    def _raise_expected_gap(self, previous, current, originals, clock):
        with self.lock:
            require(self.imu_gap_reanchor_enabled and self.fault is None,
                    self.fault or 'IMU_gap_reanchor_disabled')
            self._gap_endpoint_guards(previous, current, originals, clock)
            error = _ValidatedImuGap('imu_bracket_gap_exceeded')
            self._gap_exception = (error, previous, current, originals, clock,
                                   self.generation, self.clock_session)
            raise error

    def _quarantine_expected_gap(self, error, token):
        proof = self._gap_exception
        require(type(error) is _ValidatedImuGap and proof is not None and proof[0] is error and
                proof[5] == self.generation and proof[6] == self.clock_session and
                self.fault is None and self._active is token and self.prepared is None and
                self.imu_gap_reanchor_enabled, 'owned_current_IMU_gap_proof_required')
        _, previous, current, originals, clock, _, _ = proof
        # Recheck seals/groups against mutation during the exception handoff;
        # classification still uses the exact original sampled clock.
        self._gap_endpoint_guards(previous, current, originals, clock)
        for value in self.imus:
            self.validate_owned(value)
        require(type(self.gap_reanchor_count) is int and _ns(self.gap_reanchor_count+1),
                'IMU_gap_reanchor_counter_exhausted')
        self._gap_scan_floor = current.wire.header.stamp_ns
        self._gap_imu_floor = self.imus[-1].wire.header.stamp_ns if self.imus else None
        self.previous = self.pending = self.prepared = self._active = None
        self._prepared_digest = None
        self.imus.clear()
        self._owners.clear()
        self.callback_provenance.clear()
        self._gap_exception = None
        self._first_pair = True
        self.gap_reanchor_count += 1
        result = _ImuGapWait('waiting_for_fresh_scan_after_original_IMU_gap', self.generation, self)
        self._gap_wait_outcome = result
        return result

    def is_owned_gap_wait(self, value):
        with self.lock:
            return (type(value) is _ImuGapWait and value is self._gap_wait_outcome and
                    value._owner is self and value.generation == self.generation and
                    self.fault is None and self.imu_gap_reanchor_enabled and
                    self.previous is None and self.pending is None and self.prepared is None
                    and self._active is None and not self._owners and not self.imus)

    def _retain_causal_imu_failure_timing(self, lower, clock):
        # Existing failed association clock and owned selected-lower original;
        # never resample, renew its receipt, recover or modify the failed gate.
        if self.causal_imu_failure_timing is not None or self.causal_imu_failure_timing_error is not None:
            return
        try:
            callback = self.callback_provenance[id(lower)][1]
            source_age = clock.source_ns-lower.wire.header.stamp_ns
            raw_age = clock.receipt_ns-lower.receipt.ns
            value = dict(schema='ubts.native.causal_imu_failure_timing.v1',
                stage='association_selected_lower', source_sequence=lower.source_sequence,
                comparison=dict(source_ns=clock.source_ns, RAW_ns=clock.receipt_ns),
                lower=dict(header_ns=lower.wire.header.stamp_ns, callback_RAW_ns=lower.receipt.ns,
                    callback_SYSTEM_ns=callback.independent_ros_system_now_ns,
                    source_age_ns=source_age, RAW_age_ns=raw_age,
                    callback_SYSTEM_age_ns=clock.source_ns-callback.independent_ros_system_now_ns,
                    header_to_callback_ns=callback.independent_ros_system_now_ns-lower.wire.header.stamp_ns),
                SOURCE_TTL_ns=SOURCE_AGE_NS, RAW_TTL_ns=RECEIPT_AGE_NS,
                checks=dict(source_fresh=0 <= source_age < SOURCE_AGE_NS,
                            RAW_fresh=0 <= raw_age < RECEIPT_AGE_NS),
                data_qualified=False, recovery_permitted=False)
            raw = json.dumps(value, allow_nan=False, separators=(',', ':'))
            require(len(raw.encode('utf-8')) <= 1024, 'causal_imu_failure_timing_capacity')
            self.causal_imu_failure_timing = json.loads(raw)
        except Exception as error:
            self.causal_imu_failure_timing_error = str(error)[:128]

    def _retain_receiver_end_scalar_failure_timing(self, lower, clock):
        # Existing failed receiver-end scalar clock and owned selected-lower original;
        # never resample, renew its receipt, recover or modify the failed gate.
        if self.receiver_end_scalar_failure_timing is not None or self.receiver_end_scalar_failure_timing_error is not None:
            return
        try:
            callback = self.callback_provenance[id(lower)][1]
            source_age = clock.source_ns-lower.wire.header.stamp_ns
            raw_age = clock.receipt_ns-lower.receipt.ns
            value = dict(schema='ubts.native.receiver_end_scalar_failure_timing.v1',
                stage='receiver_commit_end_scalar', source_sequence=lower.source_sequence,
                comparison=dict(source_ns=clock.source_ns, RAW_ns=clock.receipt_ns),
                lower=dict(header_ns=lower.wire.header.stamp_ns, callback_RAW_ns=lower.receipt.ns,
                    callback_SYSTEM_ns=callback.independent_ros_system_now_ns,
                    source_age_ns=source_age, RAW_age_ns=raw_age,
                    callback_SYSTEM_age_ns=clock.source_ns-callback.independent_ros_system_now_ns,
                    header_to_callback_ns=callback.independent_ros_system_now_ns-lower.wire.header.stamp_ns),
                SOURCE_TTL_ns=SOURCE_AGE_NS, RAW_TTL_ns=RECEIPT_AGE_NS,
                checks=dict(source_fresh=0 <= source_age < SOURCE_AGE_NS,
                            RAW_fresh=0 <= raw_age < RECEIPT_AGE_NS),
                data_qualified=False, recovery_permitted=False)
            raw = json.dumps(value, allow_nan=False, separators=(',', ':'))
            require(len(raw.encode('utf-8')) <= 1024, 'receiver_end_scalar_failure_timing_capacity')
            self.receiver_end_scalar_failure_timing = json.loads(raw)
        except Exception as error:
            self.receiver_end_scalar_failure_timing_error = str(error)[:128]

    def _positive_pair_ages(self, original, clock):
        lower = original.imu_originals[original.pairing.end_indices[0]]
        newest = original.imu_originals[-1]
        values = [('scan',original.current,SOURCE_AGE_NS,RECEIPT_AGE_NS),
                  ('selected_lower_IMU',lower,SOURCE_AGE_NS,RECEIPT_AGE_NS),
                  ('captured_newest_IMU',newest,SOURCE_AGE_NS,RECEIPT_AGE_NS)]
        values += [('captured_IMU_'+str(i),v,IMU_HORIZON_NS+1,None)
                   for i,v in enumerate(original.imu_originals)]
        return tuple((name,clock.source_ns-v.wire.header.stamp_ns,
                      clock.receipt_ns-v.receipt.ns,source_limit,raw_limit)
                     for name,v,source_limit,raw_limit in values)

    @staticmethod
    def _has_positive_age_expiry(ages):
        return any(source_age >= source_limit or
                   (raw_limit is not None and raw_age >= raw_limit)
                   for _,source_age,raw_age,source_limit,raw_limit in ages)

    def _consumed_raw_expiry_guards(self, original, samples, pairing, clock, *, positive_age=False):
        # This is a classification of the EXISTING association sample, not a
        # second clock observation or a weaker association. Check every other
        # boundary before the single positive selected-lower RAW exception.
        self._receipt(original.current.receipt, self.generation)
        require(type(clock) is PhysicalClockReading and clock.source_session == self.clock_session and
                clock.raw_domain == self.domain and clock.source_ns == self._last_system and
                clock.receipt_ns == self._last_eval_raw and
                clock.sample_sequence == self._system_sample_sequence and
                _ns(clock.source_ns) and _ns(clock.receipt_ns) and _ns(clock.sample_sequence) and
                clock.clock_kind == SYSTEM_CLOCK and clock.provenance_domain == PHYSICAL_DOMAIN,
                'exact_existing_consumed_association_clock_required')
        require(type(original) is PreparedInputs and original.generation == self.generation and
                samples == original.imu_samples and pairing == original.pairing and
                pairing.reason == 'bracketed_available_imu', 'original_causal_sensor_IMU_changed')
        previous, current, originals = original.previous, original.current, original.imu_originals
        require(0 < current.wire.header.stamp_ns-previous.wire.header.stamp_ns <= PAIR_INTERVAL_NS and
                previous.receipt.ns <= current.receipt.ns, 'missing_or_reordered_adjacent_native_pair')
        for owner, _digest in tuple(self._owners.values()):
            self.validate_owned(owner)
        for value in (previous, current)+originals:
            self.validate_owned(value)
            callback = self.callback_provenance[id(value)][1]
            require(type(callback) is NativeCallbackProvenance and
                    callback.publisher_gid == self.expected_gids[0 if value.ros_type == LASER_TYPE else 1] and
                    callback.observer_contract_sha256 == self.observer_contract_sha256 and
                    value.wire.header.stamp_ns <= callback.independent_ros_system_now_ns <= clock.source_ns and
                    value.receipt.ns <= clock.receipt_ns, 'noncausal_consumed_original_callback')
        for value in (previous, current):
            scan = self._native_scan(value)
            a,b,inc,dt,cycle,minimum,maximum = map(float,value.wire.metadata)
            require(all(math.isfinite(v) for v in (a,b,inc,dt,cycle,minimum,maximum)) and
                    120 <= len(scan.ranges) <= 4096 and 0 < inc <= .1 and dt >= 0 and
                    0 <= cycle <= .5 and 0 <= minimum < maximum <= 100 and
                    abs(b-(a+(len(scan.ranges)-1)*inc)) <= 2e-4 and
                    (len(scan.ranges)-1)*dt <= .2, 'bounded_original_physical_scan_geometry_required')
            callback = self.callback_provenance[id(value)][1]
            require(scan.stamp_ns+math.ceil((len(scan.ranges)-1)*dt*1e9) <=
                    callback.independent_ros_system_now_ns, 'rolling_scan_wire_time_future_at_original_callback')
        # All retained/captured groups must be valid, including groups which
        # were not selected as interpolation endpoints. No malformed group is
        # hidden under a freshness label.
        retained_and_captured = {id(value): value for value in (*originals,*tuple(self.imus))}
        for value in retained_and_captured.values():
            self.validate_owned(value)
            require(type(value.wire) is WireImu and value.wire.header.frame_id == self.imu_frame,
                    'original_imu_window_required')
            callback = self.callback_provenance[id(value)][1]
            require(value.wire.header.stamp_ns <= callback.independent_ros_system_now_ns <= clock.source_ns and
                    value.receipt.ns <= clock.receipt_ns, 'noncausal_consumed_original_callback')
            q,qc,angular,ac,_linear,_lc = tuple(tuple(float(v) for v in group) for group in value.wire.groups)
            groups = dict(quaternion=q, orientation_covariance=qc,
                          angular_velocity=angular, angular_velocity_covariance=ac)
            for name, group in (('orientation', _imu_group(groups,'quaternion','orientation_covariance',4)),
                    ('angular_velocity', _imu_group(groups,'angular_velocity','angular_velocity_covariance',3))):
                require(group.valid and group.available, 'imu_'+name+'_'+group.reason)
        if positive_age:
            # This branch abandons custody, never admits expired inputs. Only
            # age ceilings change classification; all original gates above and
            # exact interpolation/group equality remain required.
            actual_samples = tuple(ImuSample(v.wire.header.stamp_ns,v.receipt.ns,
                v.wire.header.frame_id,tuple(float(x) for x in v.wire.groups[0]),
                tuple(float(x) for x in v.wire.groups[2]),
                tuple(float(x) for x in v.wire.groups[1]),
                tuple(float(x) for x in v.wire.groups[3])) for v in originals)
            actual_pairing = pair_imu(actual_samples,previous.wire.header.stamp_ns,
                current.wire.header.stamp_ns,frame_id=self.imu_frame,
                receipt_now_ns=clock.receipt_ns,max_roll_pitch_rad=None)
            require(actual_samples == samples and actual_pairing == pairing and
                    pairing.reason == 'bracketed_available_imu',
                    'original_causal_sensor_IMU_changed')
            ages = self._positive_pair_ages(original,clock)
            require(all(source_age >= 0 and raw_age >= 0
                        for _,source_age,raw_age,_,_ in ages),
                    'future_original_cannot_be_positive_age_wait')
            require(self._has_positive_age_expiry(ages),
                    'exact_positive_owned_pair_age_expiry_required')
            return
        self._scan_fresh(current, clock)
        require(0 <= clock.source_ns-self.callback_provenance[id(current)][1].independent_ros_system_now_ns
                < SOURCE_AGE_NS, 'stale_or_future_original_callback_system_time')
        lower, newest = originals[pairing.end_indices[0]], originals[-1]
        require(0 <= clock.source_ns-lower.wire.header.stamp_ns < SOURCE_AGE_NS and
                clock.receipt_ns-lower.receipt.ns >= RECEIPT_AGE_NS,
                'typed_positive_selected_lower_RAW_expiry_required')
        require(0 <= clock.source_ns-newest.wire.header.stamp_ns < SOURCE_AGE_NS and
                0 <= clock.receipt_ns-newest.receipt.ns < RECEIPT_AGE_NS,
                'captured_newest_IMU_must_remain_fresh')

    def _raise_preparation_raw_expiry(self, owner, attempt, original, samples, pairing, clock):
        from physical_factory import PhysicalNativeShadowFactory
        require(type(owner) is PhysicalNativeShadowFactory and owner.ingress is self,
                'exact_preparation_physical_factory_required')
        owner._validate_preparation_expiry_attempt(attempt, original, clock)
        self._consumed_raw_expiry_guards(original, samples, pairing, clock, positive_age=True)
        owner._validate_preparation_expiry_attempt(attempt, original, clock)
        error = _ValidatedPreparationCausalRawExpiry('selected_lower_causal_RAW_positive_expiry')
        self._preparation_expiry_proof = (error, owner, attempt, original, samples, pairing, clock)
        raise error

    def _quarantine_preparation_raw_expiry(self, error, owner, attempt):
        proof = self._preparation_expiry_proof
        require(type(error) is _ValidatedPreparationCausalRawExpiry and proof is not None and
                proof[0] is error and proof[1] is owner and proof[2] is attempt,
                'owned_preparation_RAW_expiry_proof_required')
        _,_,_,original,samples,pairing,clock = proof
        owner._validate_preparation_expiry_attempt(attempt, original, clock)
        self._consumed_raw_expiry_guards(original, samples, pairing, clock, positive_age=True)
        owner._validate_preparation_expiry_attempt(attempt, original, clock)
        # Preserve every observed Header floor; older captured originals must
        # never lower floors advanced by valid unadmitted priming observations.
        scan_floor = max(v for v in (self._observed_scan_header_floor, self._gap_scan_floor,
                         original.current.wire.header.stamp_ns) if v is not None)
        imu_floor = max(v for v in (self._observed_imu_header_floor, self._gap_imu_floor,
                        *(v.wire.header.stamp_ns for v in self.imus)) if v is not None)
        self._observed_scan_header_floor = self._gap_scan_floor = scan_floor
        self._observed_imu_header_floor = self._gap_imu_floor = imu_floor
        self.previous = self.pending = self.prepared = self._active = None
        self._prepared_digest = None
        self.imus.clear(); self._owners.clear(); self.callback_provenance.clear()
        self._gap_exception = self._gap_wait_outcome = self._expiry_wait_outcome = None
        self._preparation_expiry_proof = None
        self._first_pair = True

    def _raise_consumed_raw_expiry(self, owner, attempt, original, samples, pairing, clock):
        from physical_factory import PhysicalNativeShadowFactory
        require(type(owner) is PhysicalNativeShadowFactory and owner.ingress is self,
                'exact_consumed_physical_factory_required')
        owner._validate_consumed_expiry_attempt(attempt, original, clock)
        self._consumed_raw_expiry_guards(original, samples, pairing, clock)
        owner._validate_consumed_expiry_attempt(attempt, original, clock)
        error = _ValidatedConsumedCausalRawExpiry('selected_lower_causal_RAW_positive_expiry')
        self._consumed_expiry_proof = (error, owner, attempt, original, samples, pairing, clock)
        raise error

    def _quarantine_consumed_raw_expiry(self, error, owner, attempt):
        proof = self._consumed_expiry_proof
        require(type(error) is _ValidatedConsumedCausalRawExpiry and proof is not None and
                proof[0] is error and proof[1] is owner and proof[2] is attempt,
                'owned_consumed_RAW_expiry_proof_required')
        _,_,_,original,samples,pairing,clock = proof
        owner._validate_consumed_expiry_attempt(attempt, original, clock)
        self._consumed_raw_expiry_guards(original, samples, pairing, clock)
        owner._validate_consumed_expiry_attempt(attempt, original, clock)
        # Preserve every observed Header floor; older captured originals must
        # never lower floors advanced by valid unadmitted priming observations.
        scan_floor = max(v for v in (self._observed_scan_header_floor, self._gap_scan_floor,
                         original.current.wire.header.stamp_ns) if v is not None)
        imu_floor = max(v for v in (self._observed_imu_header_floor, self._gap_imu_floor,
                        *(v.wire.header.stamp_ns for v in self.imus)) if v is not None)
        self._observed_scan_header_floor = self._gap_scan_floor = scan_floor
        self._observed_imu_header_floor = self._gap_imu_floor = imu_floor
        self.previous = self.pending = self.prepared = self._active = None
        self._prepared_digest = None
        self.imus.clear(); self._owners.clear(); self.callback_provenance.clear()
        self._gap_exception = self._gap_wait_outcome = self._expiry_wait_outcome = None
        self._preparation_expiry_proof = None
        self._consumed_expiry_proof = None
        self._first_pair = True

    def _raise_receiver_commit_raw_expiry(self, owner, attempt, original, samples, pairing, clock):
        from physical_factory import PhysicalNativeShadowFactory
        require(type(owner) is PhysicalNativeShadowFactory and owner.ingress is self,
                'exact_receiver_commit_physical_factory_required')
        owner._validate_receiver_commit_expiry_attempt(attempt, original, clock)
        self._consumed_raw_expiry_guards(original, samples, pairing, clock)
        owner._validate_receiver_commit_expiry_attempt(attempt, original, clock)
        error = _ValidatedReceiverCommitCausalRawExpiry('selected_lower_receiver_commit_RAW_positive_expiry')
        self._receiver_expiry_proof = (error, owner, attempt, original, samples, pairing, clock)
        raise error

    def _quarantine_receiver_commit_raw_expiry(self, error, owner, attempt):
        proof = self._receiver_expiry_proof
        require(type(error) is _ValidatedReceiverCommitCausalRawExpiry and proof is not None and
                proof[0] is error and proof[1] is owner and proof[2] is attempt,
                'owned_receiver_commit_RAW_expiry_proof_required')
        _,_,_,original,samples,pairing,clock = proof
        owner._validate_receiver_commit_expiry_attempt(attempt, original, clock)
        self._consumed_raw_expiry_guards(original, samples, pairing, clock)
        owner._validate_receiver_commit_expiry_attempt(attempt, original, clock)
        scan_floor = max(v for v in (self._observed_scan_header_floor, self._gap_scan_floor,
                         original.current.wire.header.stamp_ns) if v is not None)
        imu_floor = max(v for v in (self._observed_imu_header_floor, self._gap_imu_floor,
                        *(v.wire.header.stamp_ns for v in self.imus)) if v is not None)
        self._observed_scan_header_floor = self._gap_scan_floor = scan_floor
        self._observed_imu_header_floor = self._gap_imu_floor = imu_floor
        self.previous = self.pending = self.prepared = self._active = None
        self._prepared_digest = None
        self.imus.clear(); self._owners.clear(); self.callback_provenance.clear()
        self._gap_exception = self._gap_wait_outcome = self._expiry_wait_outcome = None
        self._consumed_expiry_proof = self._receiver_expiry_proof = None
        self._first_pair = True
        self._receiver_expiry_wait = _ReceiverCommitCausalRawExpiryWait(
            'waiting_for_fresh_scan_after_receiver_commit_causal_RAW_expiry',
            self.generation, self, owner, attempt)
        return self._receiver_expiry_wait

    def is_owned_receiver_commit_raw_wait(self, value, owner, attempt):
        with self.lock:
            return (type(value) is _ReceiverCommitCausalRawExpiryWait and
                value is self._receiver_expiry_wait and value._owner is self and
                value._factory is owner and value._attempt is attempt and
                value.generation == self.generation and self.fault is None and
                self.previous is None and self.pending is None and self.prepared is None and
                self._active is None and self._prepared_digest is None and
                not self._owners and not self.imus and not self.callback_provenance)

    def commit(self, prepared, *, _receiver_causal_raw_expiry=None):
        # OFF is the exact inherited implementation, including all its latches.
        if _receiver_causal_raw_expiry is None:
            return super().commit(prepared)
        generation = self.generation
        try:
            require(type(_receiver_causal_raw_expiry) is tuple and len(_receiver_causal_raw_expiry) == 2,
                    'exact_receiver_commit_factory_attempt_required')
            owner, attempt = _receiver_causal_raw_expiry
            from physical_factory import PhysicalNativeShadowFactory
            require(type(owner) is PhysicalNativeShadowFactory and owner.ingress is self and
                    owner.receiver_commit_causal_raw_expiry_reanchor_enabled is True and
                    attempt is owner._receiver_attempt,
                    'exact_receiver_commit_factory_attempt_required')
            with self.lock:
                if (type(prepared) is not PreparedInputs or prepared is not self.prepared
                        or prepared.generation != generation
                        or self._prepared_digest != self._digest_prepared(prepared)):
                    return self._outcome('unowned_or_stale_prepared_discarded')
            clock = self._evaluate()
            if owner.preparation_causal_raw_expiry_reanchor_enabled:
                owner._check_positive_age_expiry(prepared,clock,'commit_receiver_association',pending=True)
            try:
                samples, pairing = self._association(prepared.previous, prepared.current,
                    prepared.imu_originals, clock,
                    _receiver_commit_raw_expiry=(owner,attempt,prepared))
            except _ValidatedReceiverCommitCausalRawExpiry as error:
                with self.lock:
                    return self._quarantine_receiver_commit_raw_expiry(error, owner, attempt)
            end_clock = self._evaluate()
            if owner.preparation_causal_raw_expiry_reanchor_enabled:
                owner._check_positive_age_expiry(prepared,end_clock,'commit_receiver_end_scalar',pending=True)
            self._scan_fresh(prepared.current, end_clock)
            if not 0 <= end_clock.source_ns-pairing.rate_stamp_ns < SOURCE_AGE_NS:
                raise ValueError('stale_causal_imu_source')
            if not 0 <= end_clock.receipt_ns-pairing.rate_receipt_ns < RECEIPT_AGE_NS:
                self._retain_receiver_end_scalar_failure_timing(
                    prepared.imu_originals[pairing.end_indices[0]], end_clock)
                if end_clock.receipt_ns-pairing.rate_receipt_ns >= RECEIPT_AGE_NS:
                    # Failed leaf only. Reuse this already sampled end clock and
                    # exact original attempt; complete guards precede typed WAIT.
                    try:
                        self._association(prepared.previous, prepared.current,
                            prepared.imu_originals, end_clock,
                            _receiver_commit_raw_expiry=(owner,attempt,prepared))
                    except _ValidatedReceiverCommitCausalRawExpiry as error:
                        with self.lock:
                            return self._quarantine_receiver_commit_raw_expiry(error, owner, attempt)
                raise ValueError('stale_causal_imu_receipt')
            with self.lock:
                if generation != self.generation or prepared is not self.prepared:
                    return self._outcome('late_commit_discarded')
                for envelope in (prepared.previous, prepared.current)+prepared.imu_originals:
                    self.validate_owned(envelope)
                if samples != prepared.imu_samples or pairing != prepared.pairing:
                    raise ValueError('original_prepared_association_changed')
                self.prepared = None
                self._prepared_digest = None
                self._first_pair = False
                return self._outcome('first_pair_new_epoch' if prepared.first_pair_in_epoch
                    else 'inputs_only_reviewed_typed_localizer_factory_missing',
                    input_ready=not prepared.first_pair_in_epoch)
        except _ValidatedPreparationCausalRawExpiry:
            raise  # exact factory-owned proof is classified before receiver latch
        except Exception as error:
            with self.lock:
                if generation != self.generation:
                    return self._outcome('late_commit_discarded')
                return self._invalidate(str(error))

    def _raise_final_scalar_raw_expiry(self, owner, attempt, original, samples, pairing, clock):
        from physical_factory import PhysicalNativeShadowFactory
        require(type(owner) is PhysicalNativeShadowFactory and owner.ingress is self,
                'exact_final_scalar_physical_factory_required')
        owner._validate_final_expiry_attempt(attempt, original, clock)
        self._consumed_raw_expiry_guards(original, samples, pairing, clock)
        owner._validate_final_expiry_attempt(attempt, original, clock)
        error = _ValidatedFinalScalarCausalRawExpiry('selected_lower_causal_RAW_positive_expiry')
        self._final_expiry_proof = (error, owner, attempt, original, samples, pairing, clock)
        raise error

    def _quarantine_final_scalar_raw_expiry(self, error, owner, attempt):
        proof = self._final_expiry_proof
        require(type(error) is _ValidatedFinalScalarCausalRawExpiry and proof is not None and
                proof[0] is error and proof[1] is owner and proof[2] is attempt,
                'owned_final_scalar_RAW_expiry_proof_required')
        _,_,_,original,samples,pairing,clock = proof
        owner._validate_final_expiry_attempt(attempt, original, clock)
        self._consumed_raw_expiry_guards(original, samples, pairing, clock)
        owner._validate_final_expiry_attempt(attempt, original, clock)
        # Preserve every observed Header floor; older captured originals must
        # never lower floors advanced by valid unadmitted priming observations.
        scan_floor = max(v for v in (self._observed_scan_header_floor, self._gap_scan_floor,
                         original.current.wire.header.stamp_ns) if v is not None)
        imu_floor = max(v for v in (self._observed_imu_header_floor, self._gap_imu_floor,
                        *(v.wire.header.stamp_ns for v in self.imus)) if v is not None)
        self._observed_scan_header_floor = self._gap_scan_floor = scan_floor
        self._observed_imu_header_floor = self._gap_imu_floor = imu_floor
        self.previous = self.pending = self.prepared = self._active = None
        self._prepared_digest = None
        self.imus.clear(); self._owners.clear(); self.callback_provenance.clear()
        self._gap_exception = self._gap_wait_outcome = self._expiry_wait_outcome = None
        self._preparation_expiry_proof = None
        self._final_expiry_proof = self._consumed_expiry_proof = None
        self._receiver_expiry_proof = self._receiver_expiry_wait = None
        self._first_pair = True

    def _association(self, previous, current, originals, clock, *, allow_expected_gap=False,
                     _consumed_raw_expiry=None, _receiver_commit_raw_expiry=None,
                     _final_scalar_raw_expiry=None, _preparation_raw_expiry=None):
        self.validate_owned(previous)
        self._scan_fresh(current, clock)
        samples = []
        for envelope in originals:
            self.validate_owned(envelope)
            require(type(envelope.wire) is WireImu, 'original_imu_window_required')
            wire = envelope.wire
            require(clock.source_ns-IMU_HORIZON_NS <= wire.header.stamp_ns <= clock.source_ns
                    and envelope.receipt.ns <= clock.receipt_ns, 'imu_not_causally_available')
            q, qc, angular, ac, _linear, _lc = tuple(tuple(float(v) for v in group)
                                                    for group in wire.groups)
            samples.append(ImuSample(wire.header.stamp_ns, envelope.receipt.ns,
                wire.header.frame_id, q, angular, qc, ac))
        samples = tuple(samples)
        pairing = pair_imu(samples, previous.wire.header.stamp_ns,
            current.wire.header.stamp_ns, frame_id=self.imu_frame,
            receipt_now_ns=clock.receipt_ns, max_roll_pitch_rad=None)
        if pairing.reason == 'imu_bracket_gap_exceeded':
            self._retain_imu_gap_failure_timing(previous, current, originals, samples, clock)
            if allow_expected_gap:
                self._raise_expected_gap(previous, current, originals, clock)
        require(pairing.reason == 'bracketed_available_imu', pairing.reason)
        if not (0 <= clock.source_ns-pairing.rate_stamp_ns < SOURCE_AGE_NS and
                0 <= clock.receipt_ns-pairing.rate_receipt_ns < RECEIPT_AGE_NS):
            self._retain_causal_imu_failure_timing(originals[pairing.end_indices[0]], clock)
            if (_preparation_raw_expiry is not None and
                    0 <= clock.source_ns-pairing.rate_stamp_ns < SOURCE_AGE_NS and
                    clock.receipt_ns-pairing.rate_receipt_ns >= RECEIPT_AGE_NS):
                owner, attempt, original = _preparation_raw_expiry
                self._raise_preparation_raw_expiry(owner, attempt, original, samples, pairing, clock)
            if (_receiver_commit_raw_expiry is not None and
                    0 <= clock.source_ns-pairing.rate_stamp_ns < SOURCE_AGE_NS and
                    clock.receipt_ns-pairing.rate_receipt_ns >= RECEIPT_AGE_NS):
                owner, attempt, original = _receiver_commit_raw_expiry
                self._raise_receiver_commit_raw_expiry(owner, attempt, original, samples, pairing, clock)
            if (_consumed_raw_expiry is not None and
                    0 <= clock.source_ns-pairing.rate_stamp_ns < SOURCE_AGE_NS and
                    clock.receipt_ns-pairing.rate_receipt_ns >= RECEIPT_AGE_NS):
                owner, attempt, original = _consumed_raw_expiry
                self._raise_consumed_raw_expiry(owner, attempt, original, samples, pairing, clock)
            if (_final_scalar_raw_expiry is not None and
                    0 <= clock.source_ns-pairing.rate_stamp_ns < SOURCE_AGE_NS and
                    clock.receipt_ns-pairing.rate_receipt_ns >= RECEIPT_AGE_NS):
                owner, attempt, original = _final_scalar_raw_expiry
                self._raise_final_scalar_raw_expiry(owner, attempt, original, samples, pairing, clock)
        require(0 <= clock.source_ns-pairing.rate_stamp_ns < SOURCE_AGE_NS and
                0 <= clock.receipt_ns-pairing.rate_receipt_ns < RECEIPT_AGE_NS,
                'stale_original_causal_imu')
        # Valid sensor-frame quaternion is not a calibrated body/world heading.
        return samples, pairing
