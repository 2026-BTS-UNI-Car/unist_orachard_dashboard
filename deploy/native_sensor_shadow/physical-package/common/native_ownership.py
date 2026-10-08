"""Vendored original-CDR ownership primitives; physical subclass provides clocks.

Extracted from frozen original_ingress without SIM/profile/endpoint/clock-topic
implementations. Original byte/digest, bounded ownership, causal commit and
receiver generation logic remain. No IO except explicit Linux RAW clock reader.
"""
from collections import OrderedDict, deque
from dataclasses import dataclass, field
import hashlib, os, re, threading, time, uuid
from pathlib import Path
from .original_cdr import decode, cdr_sha, WireScan, WireImu, LASER_TYPE, IMU_TYPE, CLOCK_TYPE, MAX_NS
from .sensor_views import NativeScan
SCHEMA='ubts.original_cdr_ingress.v1'
RAW_SCHEMA='linux.clock_monotonic_raw.integer_ns.v1'
MAX_IMU_WINDOW=64
IMU_HORIZON_NS=500_000_000
MAX_OWNERS=2*MAX_IMU_WINDOW+8
SOURCE_AGE_NS=250_000_000
RECEIPT_AGE_NS=200_000_000
def _ns(value):
    return type(value) is int and 0 <= value <= MAX_NS

def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None

@dataclass(frozen=True)
class RawReceipt:
    ns: int
    domain: str
    schema: str = RAW_SCHEMA

    def __post_init__(self):
        if not _ns(self.ns) or not _sha(self.domain) or self.schema != RAW_SCHEMA:
            raise ValueError('integer_linux_RAW_receipt_domain_required')

class LinuxRawClock:
    """No wall clock / monotonic float fallback; unsupported means HOLD."""
    def __init__(self):
        import sys
        if not sys.platform.startswith('linux') or not hasattr(time, 'CLOCK_MONOTONIC_RAW'):
            raise ValueError('Linux_CLOCK_MONOTONIC_RAW_unavailable')
        boot = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
        if str(uuid.UUID(boot)) != boot:
            raise ValueError('invalid_linux_boot_identity')
        self.domain = hashlib.sha256((boot+':CLOCK_MONOTONIC_RAW').encode('ascii')).hexdigest()

    def sample(self):
        return RawReceipt(time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW), self.domain)

@dataclass(frozen=True)
class Envelope:
    schema: str
    topic: str
    ros_type: str
    representation: str
    endianness: str
    original_cdr: bytes
    original_sha256: str
    wire: object
    receipt: RawReceipt
    process_session: str
    process_id: int
    source_clock_session: str
    source_sequence: int
    receiver_generation: int
    profile_revision: str
    publisher_gid: bytes | None
    original_byte_origin_verified: bool = field(default=False, init=False)

@dataclass(frozen=True)
class PreparedInputs:
    generation: int
    previous: Envelope
    current: Envelope
    imu_originals: tuple
    imu_samples: tuple
    pairing: object
    evaluated_clock: object
    first_pair_in_epoch: bool

@dataclass(frozen=True)
class Outcome:
    reason: str
    generation: int
    envelope: Envelope | None = None
    prepared: PreparedInputs | None = None
    input_ready: bool = False
    state: str = field(default='HOLD', init=False)
    execution_eligible: bool = field(default=False, init=False)
    autonomy_output_enabled: bool = field(default=False, init=False)
    proposed_linear_mps: float = field(default=0., init=False)
    proposed_yaw_rate_rad_s: float = field(default=0., init=False)
    last_row: str = field(default='UNKNOWN', init=False)
    original_byte_origin_verified: bool = field(default=False, init=False)

def _envelope_digest(e):
    # Hash decoded provenance too, so object.__setattr__ cannot change wire data.
    fields = (e.schema, e.topic, e.ros_type, e.representation, e.endianness, e.original_sha256,
        e.wire, e.receipt, e.process_session, e.process_id, e.source_clock_session,
        e.source_sequence, e.receiver_generation, e.profile_revision, e.publisher_gid,
        e.original_byte_origin_verified)
    return hashlib.sha256(repr(fields).encode()+e.original_cdr).hexdigest()

class OriginalIngress:
    """Single-process owned envelopes, bounded64 IMUs and pending ONE pair.

    This is an input preparation boundary, NOT NativePairRegistration creation.
    All results hold zero; even input_ready requires a missing reviewed typed
    localizer factory before use by the existing mission observer.
    """
    def __init__(self, *, raw_clock, source_now_ns, scan_frame, imu_frame,
                 profile_revision, scan_topic='/scan', imu_topic='/imu/data_raw'):
        if not callable(source_now_ns) or not callable(getattr(raw_clock, 'sample', None)):
            raise ValueError('independent_source_now_and_RAW_providers_required')
        for label in (scan_frame, imu_frame, scan_topic, imu_topic):
            if type(label) is not str or not label.strip() or len(label.encode()) > 128:
                raise ValueError('bounded_explicit_native_source_labels_required')
        if scan_topic == imu_topic or not _sha(profile_revision):
            raise ValueError('explicit_separate_topics_and_profile_revision_required')
        self.raw, self.source_now = raw_clock, source_now_ns
        self.scan_frame, self.imu_frame = scan_frame, imu_frame
        self.scan_topic, self.imu_topic = scan_topic, imu_topic
        self.profile_revision = profile_revision
        self._configuration = (scan_frame, imu_frame, scan_topic, imu_topic, profile_revision)
        self.process_session, self.pid = uuid.uuid4().hex, os.getpid()
        self.lock = threading.RLock()
        self.generation, self.sequence = 0, 0
        self.fault, self.clock_session = 'not_initialized', None
        self._used_sessions = set()
        self._owners = OrderedDict()
        self.imus = deque(maxlen=MAX_IMU_WINDOW)
        self.previous = self.pending = self.prepared = self._active = None
        self._prepared_digest = None
        self._last_raw = self._last_evaluation_source = None
        self._clock_source = self._clock_receipt = self._clock_progress = None
        self._clock_sequence = 0
        self._first_pair = True
        self.max_pending_seen = 0

    def _outcome(self, reason, **kwargs):
        return Outcome(reason, self.generation, **kwargs)

    def _invalidate(self, reason):
        self.generation += 1
        self.fault = str(reason)[:256]
        self.previous = self.pending = self.prepared = self._active = None
        self._prepared_digest = None
        self.imus.clear()
        self._owners.clear()
        return self._outcome(self.fault)

    def stop(self):
        with self.lock:
            return self._invalidate('STOP_latched')

    def reset(self):
        # Explicit recovery creates a new source session; old handles stay invalid.
        entry = self.raw.sample()
        if type(entry) is not RawReceipt:
            raise ValueError('original_RAW_receipt_required')
        with self.lock:
            if len(self._used_sessions) >= 1024:
                raise ValueError('source_session_bound_exhausted')
            self._invalidate('reset')
            self.clock_session = uuid.uuid4().hex
            self._used_sessions.add(self.clock_session)
            self.domain, self._last_raw = entry.domain, entry.ns
            self._last_evaluation_source = None
            self._clock_source = self._clock_receipt = self._clock_progress = None
            self._clock_sequence = 0
            self._first_pair, self.fault = True, None
            return self._outcome('new_source_epoch_needs_clock')

    def _receipt(self, receipt, generation):
        if generation != self.generation:
            raise ValueError('stale_callback_generation')
        if self.fault is not None:
            raise ValueError(self.fault)
        if self._configuration != (self.scan_frame, self.imu_frame,
                self.scan_topic, self.imu_topic, self.profile_revision):
            raise ValueError('source_profile_configuration_changed')
        if type(receipt) is not RawReceipt or receipt.domain != self.domain:
            raise ValueError('RAW_boot_domain_changed')
        if receipt.ns < self._last_raw:
            raise ValueError('RAW_receipt_clock_regressed')
        self._last_raw = receipt.ns

    def _entry_failure(self, generation, error):
        with self.lock:
            if generation != self.generation:
                return self._outcome('late_callback_discarded')
            if self.fault is not None:
                return self._outcome(self.fault)
            return self._invalidate(str(error))

    

    def _consume(self, ros_type, cdr, receipt, generation, publisher_gid):
        if ros_type == IMU_TYPE:
            return self._consume_imu(cdr, receipt, generation, publisher_gid)
        return self._consume_scan(cdr, receipt, generation, publisher_gid)

    

    

    

    

    def _issue(self, cdr, ros_type, receipt, generation, publisher_gid):
        wire = decode(cdr, ros_type)
        if publisher_gid is not None and (type(publisher_gid) is not bytes
                or not 1 <= len(publisher_gid) <= 24):
            raise ValueError('middleware_publisher_gid_or_explicit_unavailable_required')
        with self.lock:
            self._receipt(receipt, generation)
            if self.sequence >= MAX_NS:
                raise ValueError('source_sequence_bound_exhausted')
            self.sequence += 1
            topic = self.scan_topic if ros_type == LASER_TYPE else self.imu_topic
            envelope = Envelope(SCHEMA, topic, ros_type, 'original_received_CDR1', wire.endianness, cdr,
                cdr_sha(cdr), wire, receipt, self.process_session, self.pid,
                self.clock_session, self.sequence, generation, self.profile_revision, publisher_gid)
            self._owners[id(envelope)] = (envelope, _envelope_digest(envelope))
            while len(self._owners) > MAX_OWNERS:
                self._owners.popitem(last=False)
            return envelope

    def validate_owned(self, envelope):
        with self.lock:
            owned = self._owners.get(id(envelope))
            if (type(envelope) is not Envelope or owned is None or owned[0] is not envelope
                    or self.pid != os.getpid() or envelope.process_id != self.pid
                    or envelope.process_session != self.process_session
                    or envelope.receiver_generation != self.generation
                    or envelope.source_clock_session != self.clock_session
                    or envelope.profile_revision != self.profile_revision
                    or envelope.receipt.domain != self.domain
                    or envelope.original_sha256 != cdr_sha(envelope.original_cdr)
                    or owned[1] != _envelope_digest(envelope)
                    or envelope.wire != decode(envelope.original_cdr, envelope.ros_type)):
                raise ValueError('unowned_or_changed_original_envelope')
            return envelope

    

    def _consume_imu(self, cdr, receipt, generation, publisher_gid):
        envelope = None
        try:
            envelope = self._issue(cdr, IMU_TYPE, receipt, generation, publisher_gid)
            clock = self._evaluate()
            with self.lock:
                self.validate_owned(envelope)
                wire = envelope.wire
                if wire.header.frame_id != self.imu_frame:
                    raise ValueError('original_imu_frame_mismatch')
                if wire.header.stamp_ns > clock.source_ns:
                    raise ValueError('imu_not_causally_available')
                if self.imus and wire.header.stamp_ns <= self.imus[-1].wire.header.stamp_ns:
                    raise ValueError('original_imu_source_sequence_regressed')
                if self.imus and envelope.publisher_gid != self.imus[-1].publisher_gid:
                    raise ValueError('original_imu_publisher_changed')
                cutoff = clock.source_ns-IMU_HORIZON_NS
                while self.imus and self.imus[0].wire.header.stamp_ns < cutoff:
                    self.imus.popleft()
                if wire.header.stamp_ns >= cutoff:
                    self.imus.append(envelope)
                return self._outcome('original_imu_retained', envelope=envelope)
        except Exception as error:
            with self.lock:
                if generation != self.generation:
                    return self._outcome('late_callback_discarded', envelope=envelope)
                if self.fault is not None:
                    return self._outcome(self.fault, envelope=envelope)
                result = self._invalidate(str(error))
                return Outcome(result.reason, result.generation, envelope=envelope)

    def _native_scan(self, envelope):
        self.validate_owned(envelope)
        if type(envelope.wire) is not WireScan:
            raise ValueError('original_scan_required')
        wire = envelope.wire
        if wire.header.frame_id != self.scan_frame:
            raise ValueError('original_scan_frame_mismatch')
        a, b, inc, dt, scan_time, minimum, maximum = wire.metadata
        # Derived view only: original byte slots/header/timing stay in envelope.
        return NativeScan(self.clock_session, wire.header.frame_id, wire.header.stamp_ns,
            wire.ranges, a, inc, minimum, maximum, dt, b, scan_time, wire.intensities)

    def _scan_fresh(self, envelope, clock):
        self.validate_owned(envelope)
        if not 0 <= clock.source_ns-envelope.wire.header.stamp_ns < SOURCE_AGE_NS:
            raise ValueError('stale_or_future_native_source')
        if not 0 <= clock.receipt_ns-envelope.receipt.ns < RECEIPT_AGE_NS:
            raise ValueError('stale_or_future_native_receipt')

    

    

    

    def prepare(self):
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
            prepared = PreparedInputs(generation, *pair, originals, samples,
                                      pairing, clock, first)
            # Reevaluate after work; no completion-time freshness fiction.
            end_clock = self._evaluate()
            self._association(*pair, originals, end_clock)
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

    def _digest_prepared(self, prepared):
        envelopes = (prepared.previous, prepared.current)+prepared.imu_originals
        return hashlib.sha256(repr((prepared.generation, tuple(_envelope_digest(e)
            for e in envelopes), prepared.imu_samples, prepared.pairing,
            prepared.evaluated_clock, prepared.first_pair_in_epoch)).encode()).hexdigest()

    def commit(self, prepared):
        generation = self.generation
        try:
            with self.lock:
                if (type(prepared) is not PreparedInputs or prepared is not self.prepared
                        or prepared.generation != generation
                        or self._prepared_digest != self._digest_prepared(prepared)):
                    return self._outcome('unowned_or_stale_prepared_discarded')
            clock = self._evaluate()
            samples, pairing = self._association(prepared.previous, prepared.current,
                prepared.imu_originals, clock)
            end_clock = self._evaluate()
            self._scan_fresh(prepared.current, end_clock)
            if not 0 <= end_clock.source_ns-pairing.rate_stamp_ns < SOURCE_AGE_NS:
                raise ValueError('stale_causal_imu_source')
            if not 0 <= end_clock.receipt_ns-pairing.rate_receipt_ns < RECEIPT_AGE_NS:
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
        except Exception as error:
            with self.lock:
                if generation != self.generation:
                    return self._outcome('late_commit_discarded')
                return self._invalidate(str(error))
