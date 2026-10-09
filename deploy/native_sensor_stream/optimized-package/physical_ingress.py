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
from common.original_cdr import LASER_TYPE, IMU_TYPE, CLOCK_TYPE, WireImu
from common.sensor_views import ImuSample, pair_imu, MAX_IMU_BRACKET_NS

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
                 scan_topic='/scan', imu_topic='/imu/data_raw'):
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
                                         observer_contract_sha256)
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

    def _invalidate(self, reason):
        value = super()._invalidate(reason)
        if hasattr(self, 'callback_provenance'):
            self.callback_provenance.clear()
        self._callback_context = None
        return value

    def reset(self):
        value = super().reset()
        self._reset_raw = self._last_raw
        self._last_delivery_raw = self._last_delivery_source = None
        self._last_eval_raw = self._last_system = None
        self._system_sample_sequence = 0
        self._system_anchor = self._system_progress_raw = None
        self._evaluate()
        return self._outcome('physical_new_system_time_epoch_no_clock_topic')

    def _receipt(self, receipt, generation):
        require(generation == self.generation, 'stale_callback_generation')
        require(self.fault is None, self.fault or 'receiver_fault')
        require(self._configuration == (self.scan_frame, self.imu_frame,
            self.scan_topic, self.imu_topic, self.profile_revision),
            'source_profile_configuration_changed')
        require(self._physical_configuration == (self.source_clock_kind,
            self.expected_gids, self.observer_contract_sha256),
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
            try:
                require(ros_type in (LASER_TYPE, IMU_TYPE), 'physical_scan_and_imu_only')
                self._receipt(callback_receipt, generation)
                require(_ns(callback_ros_system_now_ns), 'original_callback_system_clock_required')
                expected = self.expected_gids[0 if ros_type == LASER_TYPE else 1]
                require(type(publisher_gid) is bytes and publisher_gid == expected,
                        'actual_native_callback_publisher_changed_or_missing')
                now = self._evaluate()
                require(0 <= now.receipt_ns-callback_receipt.ns < RECEIPT_AGE_NS,
                        'stale_or_future_original_callback_RAW')
                require(0 <= now.source_ns-callback_ros_system_now_ns < SOURCE_AGE_NS,
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
                return self._invalidate(str(error))
            finally:
                self._callback_context = None

    def _issue(self, cdr, ros_type, receipt, generation, publisher_gid):
        context = self._callback_context
        require(type(context) is NativeCallbackProvenance and
                context.original_receipt is receipt and context.publisher_gid == publisher_gid,
                'original_native_callback_context_required')
        value = super()._issue(cdr, ros_type, receipt, generation, publisher_gid)
        require(value.wire.header.stamp_ns <= context.independent_ros_system_now_ns,
                'source_header_future_at_original_native_callback')
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

    def prepare(self):
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
            originals = tuple(e for e in originals
                if e.wire.header.stamp_ns >= clock.source_ns-IMU_HORIZON_NS)
            samples, pairing = self._association(*pair, originals, clock)
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

    def _association(self, previous, current, originals, clock):
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
        require(pairing.reason == 'bracketed_available_imu', pairing.reason)
        require(0 <= clock.source_ns-pairing.rate_stamp_ns < SOURCE_AGE_NS and
                0 <= clock.receipt_ns-pairing.rate_receipt_ns < RECEIPT_AGE_NS,
                'stale_original_causal_imu')
        # Valid sensor-frame quaternion is not a calibrated body/world heading.
        return samples, pairing
