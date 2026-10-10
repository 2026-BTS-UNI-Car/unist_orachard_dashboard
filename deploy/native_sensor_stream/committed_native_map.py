"""Default-OFF asynchronous historical map attached to exact native commits.

Original factory math/admission/TTL is unchanged. One native worker captures a
private immutable accepted seal; one map worker owns raster/registration/encode.
No historical render completion is a fresh sensor/control/physical certificate.
"""
from dataclasses import dataclass,replace
import hashlib
import threading
import time
import uuid
import struct
from common.original_cdr import WireScan, WireImu, decode, LASER_TYPE, IMU_TYPE
from common.native_ownership import PreparedInputs
from physical_factory import PhysicalNativeShadowFactory, PreparedPhysicalDiagnostic
from native_map_owner import (NativeAcceptedMapOwner, AcceptedNativeFrame,
                              RegisteredNativeBridge, AcceptedContiguousNativeChain, require, token, digest, MAX_NS)
from keyframe_registration import scan_geometry, register_keyframe, freeze_geometry, keyframe_digest
from batched_map_owner import BatchedNativeAcceptedMapOwner


@dataclass(frozen=True)
class _CommitWitness:
    original: object
    record: object
    state: object


@dataclass(frozen=True)
class _AcceptedSeal:
    frame: AcceptedNativeFrame
    previous_cdr: bytes
    current_cdr: bytes
    lower_imu_cdr: bytes
    source_diagnostic_version: int
    record_sha256: str
    diagnostic_observed_source_ns: int
    diagnostic_observed_RAW_ns: int
    chain_token: str | None = None
    chain_pose: tuple | None = None
    chain_digest: bytes | None = None


def seal_digest(s):
    h=hashlib.sha256(digest(s.frame))
    for raw in (s.previous_cdr,s.current_cdr,s.lower_imu_cdr):
        h.update(len(raw).to_bytes(8,'big'));h.update(raw)
    h.update(repr((s.source_diagnostic_version,s.record_sha256,
                   s.diagnostic_observed_source_ns,s.diagnostic_observed_RAW_ns,
                   s.chain_token,s.chain_pose,s.chain_digest)).encode())
    return h.digest()


class CommittedNativeMapAdapter:
    def __init__(self,factory, *, enabled=False,map_epoch=None,
                 map_frame='unverified_native_laser_local',on_display_commit=None,
                 recovery_max_wall_s=1.5,accepted_native_chain_enabled=False,pose_ledger_enabled=False,**map_budgets):
        require(type(enabled) is bool,'typed_map_adapter_default_OFF_required')
        require(type(accepted_native_chain_enabled) is bool,'typed_native_chain_default_OFF_required')
        require(type(pose_ledger_enabled) is bool,'typed_pose_ledger_default_OFF_required')
        from native_pose_ledger import CommittedPoseLedger
        self.pose_ledger=CommittedPoseLedger(enabled=pose_ledger_enabled)
        self.__accepted_native_chain_enabled=accepted_native_chain_enabled
        self._capture_last=None;self._chain_token=None;self._chain_pose=None;self._chain_digest=None;self._mapped_chain=None
        require(type(factory) is PhysicalNativeShadowFactory,'exact_native_factory_required')
        require(on_display_commit is None or callable(on_display_commit),'display_callback_required')
        require(type(recovery_max_wall_s) in (int,float) and .05<=recovery_max_wall_s<=2.,
                'bounded_recovery_work_deadline_required')
        self.factory,self.ingress=factory,factory.ingress
        self._enabled=enabled;self._context=None
        if enabled:
            token(map_epoch);token(map_frame)
            self._context=self._factory_context(factory,map_epoch,map_frame)
        self._cv=threading.Condition(threading.RLock());self._fence=threading.Lock()
        self._stop=threading.Event();self._stop.set();self._thread=None;self._launch_ambiguous=False
        self._pending=None;self._working=None;self._working_digest=None
        self._recovery_work=None;self._proposal_sequence_floor=0
        self._issued_bridge=None;self._bridge_digest=None;self._keyframe=None;self._keyframe_digest=None
        self._capture_sequence=0;self._serial=0;self._fault=None;self._encoder_fault=None
        self._counts=dict(captured=0,processed=0,replaced=0,cancelled=0,bridge_attempts=0,bridges=0,accepted_native_chain_bridges=0)
        self._last_work={};self._callback=on_display_commit;self._recovery_budget=recovery_max_wall_s
        self._map=BatchedNativeAcceptedMapOwner(enabled=enabled,historical_display_only=True,
            accepted_native_validator=self._validate_frame,
            registered_bridge_validator=self._validate_bridge,**map_budgets)

    @property
    def accepted_native_chain_enabled(self):return self.__accepted_native_chain_enabled

    def _clear_chain(self):
        self._capture_last=None;self._chain_token=None;self._chain_pose=None;self._chain_digest=None

    @staticmethod
    def _inverse(pose):
        x,y,a=pose;c,s=__import__('math').cos(a),__import__('math').sin(a)
        return (-c*x-s*y,s*x-c*y,-a)

    @staticmethod
    def _factory_context(factory,epoch,frame):
        i=factory.ingress
        return (i.clock_session,i.generation,'ROS_SYSTEM_TIME',
                'CLOCK_MONOTONIC_RAW:'+i.domain,epoch,frame)

    def start(self, *, explicit=False,on_display_commit=None):
        require(self._enabled and explicit is True,'explicit_native_map_adapter_required')
        require(self.factory is not None,'native_factory_detached')
        with self.factory.lock,self.ingress.lock:
            require(self.factory.fault is None and self.ingress.fault is None and
                not self.factory.stop_intent.is_set() and self._context==
                self._factory_context(self.factory,*self._context[4:]),'native_factory_stopped_or_changed')
        with self._cv:
            require(not self._launch_ambiguous and (self._thread is None or not self._thread.is_alive()),
                    'previous_map_worker_join_required')
            if on_display_commit is not None:
                require(callable(on_display_commit),'display_callback_required');self._callback=on_display_commit
            self._fault=None;self._encoder_fault=None;self._serial+=1;self._pending=None
            self._map.start(self._context,explicit=True)
            self.pose_ledger.start_after_join(explicit=True)
            with self._fence:self._stop.clear()
            self._thread=threading.Thread(target=self._run,name='native-historical-map',daemon=False)
            try:self._thread.start()
            except BaseException:
                self._launch_ambiguous=True
                with self._fence:self._stop.set()
                self._map.stop('map_worker_start_failed');self._pending=None
                raise
            return self.status()

    def stop(self,reason='operator_stop'):
        token(reason)
        with self._fence:self._stop.set()
        self.pose_ledger.inhibit()
        self._map.stop(reason)
        with self._cv:self._serial+=1;self._pending=None;self._clear_chain();self._cv.notify_all()
        return self.status()

    def close(self,timeout=1.):
        require(type(timeout) in (int,float) and 0<=timeout<=5.,'bounded_worker_join_required')
        self.stop()
        with self._cv:thread=self._thread
        require(thread is not threading.current_thread(),'map_worker_cannot_join_itself')
        ambiguous=self._launch_ambiguous and thread is not None and thread.ident is None
        if thread is not None and not ambiguous:thread.join(timeout)
        joined=not ambiguous and (thread is None or not thread.is_alive())
        # An unknown worker is retained; never advertise detached ownership.
        if joined:
            self.pose_ledger.close_after_join()
            with self._cv:self._thread=None;self._launch_ambiguous=False;self._pending=None;self._working=None;self._working_digest=None
        return dict(joined=joined,worker_alive=thread is not None and thread.is_alive(),
                    worker_state_known=not ambiguous,owned_worker_retained=not joined,
                    publication_inhibited=self._stop.is_set(),launch_unproven=ambiguous,runtime_authority=False)

    def detach_factory(self):
        with self._cv:
            require(not self._launch_ambiguous and self._stop.is_set() and (self._thread is None or not self._thread.is_alive()),
                    'map_worker_join_before_detach_required')
            self.factory=self.ingress=None

    def attach_factory(self,factory, *, explicit=False):
        require(self._enabled and explicit is True and type(factory) is PhysicalNativeShadowFactory,'explicit_new_native_factory_required')
        with self._cv:
            require(not self._launch_ambiguous and self._stop.is_set() and (self._thread is None or not self._thread.is_alive()),
                    'map_worker_join_before_attach_required')
            self.factory,self.ingress=factory,factory.ingress
            self._context=self._factory_context(factory,*self._context[4:]);self._serial+=1
            self._map.invalidate_gap('new_native_source_requires_registration')
            return self.status()

    def new_map(self, *, explicit=False,map_epoch=None):
        with self._cv:
            require(self._enabled and explicit is True and not self._launch_ambiguous and self._stop.is_set() and
                    (self._thread is None or not self._thread.is_alive()),'explicit_joined_new_map_required')
            epoch=uuid.uuid4().hex if map_epoch is None else token(map_epoch)
            require(epoch!=self._context[4],'new_map_epoch_required')
            self._context=(*self._context[:4],epoch,self._context[5]);self._keyframe=None;self._keyframe_digest=None
            self._clear_chain();self._mapped_chain=None
            self._issued_bridge=None;self._bridge_digest=None;self._serial+=1
            return self._map.new_map(self._context,explicit=True)

    def invalidate_gap(self,reason='native_pair_gap'):
        # WAIT revokes the same generation before any publication can proceed.
        with self._fence:
            with self._cv:
                work=self._recovery_work
                # A repeated typed WAIT cannot renew input or interrupt the
                # same immutable historical registration. Other invalidations
                # and all later raster/publication phases retain cancellation.
                preserve=(reason=='native_original_pair_wait' and not self._stop.is_set() and
                    work is not None and work[0] is self._working and work[1] is self._map._state and
                    work[2] is self._keyframe and work[3]==self._serial)
                if not preserve:self._serial+=1
                self._proposal_sequence_floor=self._capture_sequence
                self._pending=None;self._clear_chain();self._cv.notify_all()
            return self._map.invalidate_gap(reason)

    def status(self):
        value=self._map.status()
        with self._cv:
            value.update(adapter='same_commit_immutable_seal_async_historical',adapter_fault=self._fault,
                committed_pose_ledger=self.pose_ledger.status(),
                accepted_native_chain_enabled=self.accepted_native_chain_enabled,
                encoder_fault=self._encoder_fault,worker_alive=self._thread is not None and self._thread.is_alive(),
                worker_launch_unproven=self._launch_ambiguous,pending_latest=int(self._pending is not None),in_flight=int(self._working is not None),
                pending_limit=1,keyframes_retained=int(self._keyframe is not None),keyframe_limit=1,
                counters=dict(self._counts),last_work=dict(self._last_work),
                independent_keyframe_bridge_implemented=True,bridge_global_uniqueness_verified=False,
                source_policy_ns=250_000_000,callback_RAW_policy_ns=200_000_000,
                render_completion_is_fresh_input=False)
        return value

    def display_snapshot(self):return self._map.display_snapshot()
    def historical_cells(self):return self._map.historical_cells()

    def _validate_frame(self,frame):
        s=self._working
        return (s is not None and frame is s.frame and seal_digest(s)==self._working_digest)

    def _validate_bridge(self,bridge,frame):
        return (bridge is self._issued_bridge and self._validate_frame(frame) and
            hashlib.sha256(repr(bridge).encode()).digest()==self._bridge_digest)

    def _make_frame(self,w):
        original,record=w.original,w.record
        before,after=original.previous,original.current
        require(type(before.wire) is WireScan and type(after.wire) is WireScan,
                'exact_original_scan_wire_required')
        lower=original.imu_originals[original.pairing.end_indices[0]]
        require(type(lower.wire) is WireImu and
                lower.wire.header.stamp_ns<=after.wire.header.stamp_ns and
                (lower.wire.header.stamp_ns,lower.receipt.ns)==
                (record.original_lower_imu_stamp_ns,record.original_lower_imu_receipt_ns),
                'exact_selected_causal_IMU_required')
        require(record.source_pair_sha256==(before.original_sha256,after.original_sha256) and
                record.imu_original_sha256==tuple(e.original_sha256 for e in original.imu_originals) and
                record.source_session==self._context[0] and record.raw_domain==self.ingress.domain and
                record.profile_revision==self.ingress.profile_revision and
                record.original_scan_stamps_ns==(before.wire.header.stamp_ns,after.wire.header.stamp_ns) and
                record.original_scan_receipts_ns==(before.receipt.ns,after.receipt.ns) and
                record.accuracy_verified is False and record.rolling_scan_deskewed is False and
                record.body_velocity_available is False,
                'exact_unverified_committed_diagnostic_required')
        require(before.publisher_gid==after.publisher_gid==self.ingress.expected_gids[0] and
                lower.publisher_gid==self.ingress.expected_gids[1],
                'original_native_publisher_changed')
        amin,_amax,inc,dt,_cycle,mn,mx=map(float,after.wire.metadata)
        return AcceptedNativeFrame(self._context,self._capture_sequence,
            record.original_scan_stamps_ns,record.original_scan_receipts_ns,
            record.source_pair_sha256,after.publisher_gid.hex(),
            lower.wire.header.stamp_ns,lower.receipt.ns,lower.original_sha256,
            lower.publisher_gid.hex(),after.wire.header.frame_id,
            tuple(float(v) for v in after.wire.ranges),amin,inc,mn,mx,dt,
            (*record.delta_xy_laser_plane_m,record.fitted_laser_plane_yaw_rad))

    def _capture(self,prepared,state,accepted_record_sha):
        require(hashlib.sha256(repr(prepared.record).encode()).hexdigest()==accepted_record_sha,
                'accepted_native_record_changed_at_capture')
        original=prepared.original_inputs;self.factory._owned(original,pending=False)
        for envelope in (original.previous,original.current,*original.imu_originals):
            self.ingress.validate_owned(envelope)
        require(self._capture_sequence<MAX_NS,'capture_sequence_exhausted')
        self._capture_sequence+=1
        frame=self._make_frame(_CommitWitness(original,prepared.record,state))
        lower=original.imu_originals[original.pairing.end_indices[0]]
        clock=prepared.record.observed_clock # Original post-fit sample, NOT final-commit/restamped time.
        require(clock.source_session==self._context[0] and clock.raw_domain==self.ingress.domain,
                'accepted_commit_clock_context_changed')
        return _AcceptedSeal(frame,bytes(original.previous.original_cdr),
            bytes(original.current.original_cdr),bytes(lower.original_cdr),state.version,
            accepted_record_sha,clock.source_ns,clock.receipt_ns)

    def commit(self,prepared):
        # One existing native commit; rendering never holds these locks.
        f=self.factory;require(f is not None,'native_factory_detached')
        if not self._enabled or self._stop.is_set():return f.commit(prepared)
        seal=None
        with f.lock,self.ingress.lock:
            before=f.snapshot()
            accepted_record_sha=(hashlib.sha256(repr(prepared.record).encode()).hexdigest()
                if type(prepared) is PreparedPhysicalDiagnostic and prepared is f._pending else None)
            answer=f.commit(prepared);after=f.snapshot()
            try:
                if not (accepted_record_sha is not None and type(prepared) is PreparedPhysicalDiagnostic and type(answer) is dict and
                    answer.get('diagnostic') is prepared.record and after is not before and
                    after.latest is prepared.record and after.version==before.version+1):
                    self.invalidate_gap('native_fit_not_committed');return answer
                require(all(answer.get(k) is False for k in ('runtime_authority','motor_command_sent',
                    'execution_eligible','autonomy_output_enabled','physical_autonomy_qualified')) and
                    answer.get('body_velocity_mps') is None,'factory_response_has_authority')
                seal=self._capture(prepared,after,accepted_record_sha)
            except Exception as error:
                self._fault=self._fault or str(error)[:128];self.stop('native_map_capture_fault')
        if seal is not None:self._enqueue(seal)
        return answer

    def _enqueue(self,seal):
        with self._cv:
            if not self._stop.is_set():
                if self._pending is not None:self._counts['replaced']+=1
                if self.accepted_native_chain_enabled:seal=self._extend_chain(seal)
                self._pending=(seal,seal_digest(seal),self._serial)
                self._counts['captured']+=1;self._cv.notify_all()

    def _extend_chain(self,seal):
        f=seal.frame;p=self._capture_last
        adjacent=(p is not None and p.context==f.context and f.sequence==p.sequence+1 and
            f.scan_stamps_ns[0]==p.scan_stamps_ns[1] and f.scan_receipts_ns[0]==p.scan_receipts_ns[1] and
            f.scan_cdr_sha256[0]==p.scan_cdr_sha256[1] and f.scan_gid==p.scan_gid and f.imu_gid==p.imu_gid and
            f.laser_frame==p.laser_frame and f.scan_stamps_ns[1]>p.scan_stamps_ns[1] and
            f.scan_receipts_ns[1]>p.scan_receipts_ns[1] and f.imu_stamp_ns>=p.imu_stamp_ns and f.imu_receipt_ns>=p.imu_receipt_ns)
        if not adjacent:
            self._chain_token=uuid.uuid4().hex;self._chain_pose=(0.,0.,0.);self._chain_digest=b''
        self._chain_pose=self._map._compose(self._chain_pose,f.current_to_previous)
        self._chain_digest=hashlib.sha256(self._chain_digest+seal_digest(seal)).digest()
        self._capture_last=f
        return replace(seal,chain_token=self._chain_token,chain_pose=self._chain_pose,chain_digest=self._chain_digest)

    def _run(self):
        while True:
            with self._cv:
                self._cv.wait_for(lambda:self._stop.is_set() or self._pending is not None)
                if self._stop.is_set():return
                seal,stamp,serial=self._pending;self._pending=None
                self._working=seal;self._working_digest=stamp
            began=time.perf_counter();cpu=time.thread_time()
            try:
                self._process(seal,serial)
            except BaseException as error:
                self._fault=self._fault or (str(error) or type(error).__name__)[:128]
                self.stop('native_historical_map_worker_fault')
            finally:
                with self._cv:
                    self._working=None;self._working_digest=None;self._recovery_work=None
                    self._issued_bridge=None;self._bridge_digest=None
                    self._counts['processed']+=1
                    self._last_work=dict(wall_ms=(time.perf_counter()-began)*1000,
                                         thread_CPU_ms=(time.thread_time()-cpu)*1000)
                    self._cv.notify_all()

    def _process(self,seal,serial):
        def cancelled():return self._stop.is_set() or serial!=self._serial
        if cancelled():self._counts['cancelled']+=1;return
        require(self._validate_frame(seal.frame),'accepted_seal_changed')
        # Re-decode the immutable snapshot, not a consumed Prepared capability.
        wire,geometry=scan_geometry(seal.current_cdr,seal.frame.scan_cdr_sha256[1])
        require(hashlib.sha256(seal.previous_cdr).hexdigest()==seal.frame.scan_cdr_sha256[0] and
            hashlib.sha256(seal.lower_imu_cdr).hexdigest()==seal.frame.imu_cdr_sha256,
            'captured_original_CDR_changed')
        imu=decode(seal.lower_imu_cdr,IMU_TYPE)
        require(wire.header.stamp_ns==seal.frame.scan_stamps_ns[1] and
            wire.header.frame_id==seal.frame.laser_frame and imu.header.stamp_ns==seal.frame.imu_stamp_ns,
            'captured_original_header_changed')
        amin,_amax,inc,dt,_cycle,mn,mx=map(float,wire.metadata)
        require((amin,inc,mn,mx,dt)==(seal.frame.angle_min,seal.frame.angle_increment,
            seal.frame.range_min,seal.frame.range_max,seal.frame.time_increment) and
            struct.pack('<'+str(wire.count)+'d',*map(float,wire.ranges))==
            struct.pack('<'+str(len(seal.frame.ranges))+'d',*seal.frame.ranges),
            'captured_projection_does_not_match_original_CDR')
        base=self._map._state;bridge=None
        needs_bridge=base.frame is not None and (not self._map.status()['pose_continuity'] or
            seal.frame.context!=base.frame.context or seal.frame.sequence!=base.frame.sequence+1 or
            seal.frame.scan_cdr_sha256[0]!=base.frame.scan_cdr_sha256[1])
        chain=self._mapped_chain
        native_chain=(needs_bridge and self.accepted_native_chain_enabled and self._map.status()['pose_continuity'] and
            chain is not None and chain[0]==seal.chain_token and seal.chain_token is not None and
            chain[1]==base.frame.sequence and chain[2]==base.frame.scan_cdr_sha256[1] and
            seal.frame.context==base.frame.context and seal.frame.sequence>chain[1])
        if native_chain:
            relative=self._map._compose(self._inverse(chain[3]),seal.chain_pose)
            pose=self._map._compose(base.pose,relative)
            bridge=AcceptedContiguousNativeChain(seal.frame.context,base.frame.scan_cdr_sha256[1],
                seal.frame.scan_cdr_sha256[1],base.version,pose,seal.chain_token,chain[1],seal.frame.sequence)
            self._issued_bridge=bridge;self._bridge_digest=hashlib.sha256(repr(bridge).encode()).digest()
        elif needs_bridge:
            self._map.invalidate_gap('asynchronous_gap')
            k=self._keyframe
            if k is None:return
            require(keyframe_digest(k)==self._keyframe_digest and
                    k[0]==base.version and k[1]==base.frame.scan_cdr_sha256[1],
                    'registered_keyframe_generation_changed')
            self._counts['bridge_attempts']+=1
            with self._cv:
                if cancelled():return
                self._recovery_work=(seal,base,k,serial)
            try:
                fit=register_keyframe(k[3],geometry,cancelled=cancelled,max_wall_s=self._recovery_budget)
            finally:
                # No exception to WAIT cancellation while preparing rays or
                # committing a map-owner ticket whose serial was revoked.
                with self._cv:self._recovery_work=None
            require(keyframe_digest(k)==self._keyframe_digest,'retained_keyframe_geometry_changed')
            if not fit['accepted']:
                self._map.invalidate_gap(fit['reason']);return
            pose=self._map._compose(k[2],(*map(float,fit['xy']),float(fit['yaw'])))
            bridge=RegisteredNativeBridge(seal.frame.context,k[1],seal.frame.scan_cdr_sha256[1],base.version,pose)
            self._issued_bridge=bridge;self._bridge_digest=hashlib.sha256(repr(bridge).encode()).digest()
        if cancelled():self._counts['cancelled']+=1;return
        # Accepted historical evidence may finish later. No clock resample,
        # no association/scalar TTL rerun, no timestamp renewal here.
        now=dict(source_now_ns=seal.diagnostic_observed_source_ns,receipt_now_ns=seal.diagnostic_observed_RAW_ns)
        ticket=self._map.prepare(seal.frame,registered_bridge=bridge,**now)
        if ticket is None or cancelled():return
        with self._fence:
            if cancelled():return
            published=self._map.commit(ticket,**now)
            if published and not cancelled():
                state=self._map._state
                chain=seal.chain_token or ('registered:'+str(serial))
                self.pose_ledger.append_committed(seal.frame,state.pose,state.version,chain,seal.current_cdr,
                    validate=lambda: not cancelled() and self._map._state is state and
                        state.frame is not None and digest(state.frame)==digest(seal.frame) and
                        self._validate_frame(seal.frame))
        if not published:return
        self._keyframe=(self._map._state.version,seal.frame.scan_cdr_sha256[1],
                        self._map._state.pose,freeze_geometry(geometry),seal.current_cdr)
        self._keyframe_digest=keyframe_digest(self._keyframe)
        if seal.chain_token is not None:
            self._mapped_chain=(seal.chain_token,seal.frame.sequence,seal.frame.scan_cdr_sha256[1],seal.chain_pose)
        else:self._mapped_chain=None
        if type(bridge) is AcceptedContiguousNativeChain:self._counts['accepted_native_chain_bridges']+=1
        elif bridge is not None:self._counts['bridges']+=1
        callback=self._callback
        if callback is not None:
            try:
                snapshot=self.display_snapshot();status=self.status()
                publisher=callback(snapshot,status) # Heavy encode outside locks.
                require(publisher is None or callable(publisher),'display_callback_publish_callable_required')
                if publisher is not None:
                    with self._fence:
                        if not cancelled() and self._map._state.version==snapshot['map_version']:
                            publisher() # Trusted bounded pointer swap, never IO.
            except Exception as error:self._encoder_fault=self._encoder_fault or str(error)[:128]

    def proposal_snapshot(self):
        if not self._enabled or self._stop.is_set() or self.factory is None:return None
        with self.factory.lock,self.ingress.lock:
            try:
                require(self.factory.fault is None and self.ingress.fault is None and
                    not self.factory.stop_intent.is_set() and self._context==
                    self._factory_context(self.factory,*self._context[4:]),'native_source_unavailable')
                clock=self.ingress._evaluate()
                with self._fence:
                    frame=self._map._state.frame
                    if frame is None or frame.sequence<=self._proposal_sequence_floor:return None
                    return self._map.proposal_snapshot(source_now_ns=clock.source_ns,receipt_now_ns=clock.receipt_ns)
            except Exception:
                self.invalidate_gap('native_source_unavailable');return None


class NativeMapMissionAdapter:
    """Explicit proposal-only planner call on each NEW complete map generation.

    A repeated read does not replan/confirm a row or reuse a cached target. This
    also prevents time-expired local cells becoming UNKNOWN from appearing to
    be a mutated original scan to the planner's same-sequence guard.
    """
    def __init__(self,map_adapter, *, enabled=False):
        require(type(enabled) is bool,'typed_mission_adapter_default_OFF_required')
        require(type(map_adapter) is CommittedNativeMapAdapter,'owned_native_map_adapter_required')
        from proposal_row_mission import ObservedRowMission
        self._enabled=enabled;self.map=map_adapter
        self._planner=ObservedRowMission(enabled=enabled)
        self._last_sequence=None
        self._stopped=threading.Event()
        self._lock=threading.RLock()

    def start(self, *, explicit=False):
        require(self._enabled and explicit is True,'explicit_mission_adapter_start_required')
        with self._lock:
            if not self.map.status()['active']:self.map.start(explicit=True)
            value=self._planner.start(self.map.status()['context'],explicit=True)
            self._last_sequence=None;self._stopped.clear()
            return value

    def stop(self,reason='operator_stop'):
        self._stopped.set();self.map.stop(reason)
        return self._planner.stop(reason)

    def status(self):
        return dict(map=self.map.status(),mission=self._planner.status(),
                    runtime_authority=False,motor_command_sent=False,
                    execution_eligible=False,global_last_row_confirmed=False)

    def proposal(self):
        with self._lock:
            if self._stopped.is_set():return self._planner._output('STOP','operator_stop')
            if not self._enabled or not self._planner.status()['active']:
                return self._planner._output('HOLD','default_off_or_not_started')
            snapshot=self.map.proposal_snapshot()
            if snapshot is None:
                state=self.map.status()
                if state['map_version'] and not state['pose_continuity']:
                    self._planner.stop('native_pose_continuity_lost')
                    return self._planner._output('STOP','native_pose_continuity_lost')
                return self._planner._output('HOLD','waiting_fresh_complete_native_map')
            sequence=snapshot['pose']['sequence']
            if sequence==self._last_sequence:
                return self._planner._output('HOLD','waiting_new_native_map_generation')
            self._last_sequence=sequence
            answer=self._planner.plan(snapshot)
            if self._stopped.is_set():return self._planner._output('STOP','operator_stop')
            return answer
