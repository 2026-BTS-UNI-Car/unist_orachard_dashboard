"""Native committed-input adapter; default OFF, no IO/actuator or calibration.

Operational declarations are supplied through trusted constructor bindings.
This component checks consistency; it cannot certify mount/braking/accuracy.
Historical replay never yields an operational MeasuredControlFrame.
"""
from dataclasses import dataclass,asdict
import copy,hashlib,json,math,threading
from common.original_cdr import WireScan,WireImu
from physical_ingress import PhysicalOriginalIngress,SOURCE_AGE_NS,RECEIPT_AGE_NS,require
from physical_factory import PhysicalNativeShadowFactory,PreparedPhysicalDiagnostic
from dashboard_server.body_feedback import (BodyFeedbackObserver,FeedbackContext,
    LaserPosePair,NativeImuYaw,MeasuredLaserMount,FeedbackTiming)
from dashboard_server.encoderless_drive_preview import (
    MeasuredProfile,TimingLimits,NativeContext,NativeFeedback,SweptSpace)
from dashboard_server.native_autonomy_supervisor import ActivationEvidence,MeasuredControlFrame

MAX_NS=(1<<63)-1

def finite(v):
    try:return type(v) in (int,float) and math.isfinite(v)
    except (OverflowError,ValueError):return False
def stamp(v):return type(v) is int and 0<=v<=MAX_NS

def fingerprint(v):
    return hashlib.sha256(json.dumps(v,allow_nan=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

@dataclass(frozen=True)
class MeasuredFootprint:
    # Conservative circumradius of the measured body, not wheel separation.
    radius_m:float
    calibration_id:str
    measured:bool=False
    planar_coverage_validated:bool=False
    def valid(self):
        return (finite(self.radius_m) and 0<self.radius_m<=10 and
            type(self.calibration_id) is str and 0<len(self.calibration_id)<=128 and
            self.measured is True and self.planar_coverage_validated is True)

class NativeControlInputAdapter:
    def __init__(self,factory,*,enabled=False,mount=None,profile=None,timing=None,
                 feedback_timing=None,footprint=None,evidence_provider=None,
                 record_replay=False):
        require(type(factory) is PhysicalNativeShadowFactory and type(factory.ingress) is PhysicalOriginalIngress,
                'exact_native_factory_required')
        require(type(enabled) is bool and type(record_replay) is bool,'typed_private_options_required')
        self.factory,self.ingress=factory,factory.ingress
        self.enabled,self.record_replay=enabled,record_replay
        self.mount,self.profile,self.timing,self.footprint=mount,profile,timing,footprint
        self.feedback_timing=feedback_timing;self.evidence_provider=evidence_provider
        self._configuration=(enabled,record_replay,mount,profile,timing,feedback_timing,footprint,evidence_provider)
        self._configuration_digest=hashlib.sha256(repr(self._configuration).encode()).hexdigest()
        self._source=(self.ingress.clock_session,self.ingress.generation,self.ingress.domain,
                      self.ingress.profile_revision,self.ingress.expected_gids)
        self._lock=threading.RLock();self._stop=threading.Event();self._epoch=None
        self._body=BodyFeedbackObserver(mount,feedback_timing)
        self._accepted=self._last_endpoint=None;self._pose=(0.,0.,0.)
        self._sequence=0;self._reason='default_OFF' if not enabled else 'waiting_native_commit'
        self._last_diagnostic=None;self._captures=0;self._comparison=None
    def bind_epoch(self,epoch):
        require(type(epoch) is str and len(epoch)==32 and all(c in '0123456789abcdef' for c in epoch),
                'original_observer_process_epoch_required')
        with self._lock:
            require(self._epoch is None or self._epoch==epoch,'observer_epoch_changed')
            self._epoch=epoch
    def _context(self):
        return (self.ingress.clock_session,self.ingress.generation,self.ingress.domain,
                self.ingress.profile_revision,self.ingress.expected_gids)
    def invalidate_gap(self,reason='native_original_pair_wait'):
        with self._lock:
            self._accepted=self._last_endpoint=None;self._pose=(0.,0.,0.)
            self._last_diagnostic=self._body.invalidate(reason);self._reason=reason
    def stop(self):
        self._stop.set();self.invalidate_gap('operator_stop')
    def status(self):
        with self._lock:
            return dict(schema='ubts.native_control_input.v1',enabled=self.enabled,stopped=self._stop.is_set(),
                state='HOLD',reason=self._reason,accepted_commits=self._captures,
                latest=copy.deepcopy(self._last_diagnostic),latest_is_historical=True,physical_autonomy_qualified=False,
                calibration_certified=False,runtime_authority=False,motor_command_sent=False,
                operational_frame_available=False)
    def _configured(self):
        require(self._configuration==(self.enabled,self.record_replay,self.mount,self.profile,self.timing,
                 self.feedback_timing,self.footprint,self.evidence_provider) and
                hashlib.sha256(repr(self._configuration).encode()).hexdigest()==self._configuration_digest,
                'control_bindings_changed')
        require(self._context()==self._source and self.ingress.fault is None and
                self.factory.fault is None and not self.factory.stop_intent.is_set() and
                not self._stop.is_set(),'native_context_stopped_or_changed')
    def commit(self,prepared,*,map_adapter=None):
        # Default branch has precisely one parent commit and no extra admission.
        if map_adapter is not None:
            from committed_native_map import CommittedNativeMapAdapter
            require(type(map_adapter) is CommittedNativeMapAdapter and map_adapter.factory is self.factory,
                    'same_native_map_commit_owner_required')
        delegate=self.factory.commit if map_adapter is None else map_adapter.commit
        if not self.enabled:return delegate(prepared)
        with self.factory.lock,self.ingress.lock:
            before=self.factory.snapshot()
            exact=(type(prepared) is PreparedPhysicalDiagnostic and prepared is self.factory._pending)
            seal=hashlib.sha256(repr(prepared.record).encode()).hexdigest() if exact else None
            answer=delegate(prepared);after=self.factory.snapshot()
            try:
                require(exact and type(answer) is dict and answer.get('diagnostic') is prepared.record and
                    after is not before and after.version==before.version+1 and after.latest is prepared.record,
                    'exact_new_native_commit_required')
                require(all(answer.get(k) is False for k in ('runtime_authority','motor_command_sent',
                    'execution_eligible','autonomy_output_enabled','physical_autonomy_qualified')) and
                    answer.get('body_velocity_mps') is None,'unverified_native_commit_response_required')
                self._capture(prepared,seal)
            except Exception as error:
                # Control inhibition must never convert a rejected fit to motion
                # or poison an already admitted native map/factory result.
                self.invalidate_gap(str(error)[:128])
            return answer
    def _capture(self,prepared,seal):
        self._configured();require(self._epoch is not None,'original_observer_epoch_not_bound')
        record=prepared.record;original=prepared.original_inputs
        require(hashlib.sha256(repr(record).encode()).hexdigest()==seal,'committed_record_mutated')
        self.factory._owned(original,pending=False)
        for envelope in (original.previous,original.current,*original.imu_originals):
            self.ingress.validate_owned(envelope)
        before,current=original.previous,original.current
        lower=original.imu_originals[original.pairing.end_indices[0]]
        require(type(before.wire) is WireScan and type(current.wire) is WireScan and type(lower.wire) is WireImu,
                'original_scan_IMU_wire_required')
        require(record.source_pair_sha256==(before.original_sha256,current.original_sha256) and
            record.imu_original_sha256==tuple(v.original_sha256 for v in original.imu_originals) and
            record.original_scan_stamps_ns==(before.wire.header.stamp_ns,current.wire.header.stamp_ns) and
            record.original_scan_receipts_ns==(before.receipt.ns,current.receipt.ns) and
            (record.original_lower_imu_stamp_ns,record.original_lower_imu_receipt_ns)==
            (lower.wire.header.stamp_ns,lower.receipt.ns) and
            record.source_session==self.ingress.clock_session and record.raw_domain==self.ingress.domain and
            record.profile_revision==self.ingress.profile_revision and
            before.publisher_gid==current.publisher_gid==self.ingress.expected_gids[0] and
            lower.publisher_gid==self.ingress.expected_gids[1], 'committed_original_association_mismatch')
        require(record.accuracy_verified is False and record.body_velocity_available is False,
                'sensor_plane_diagnostic_expected')
        interval=current.wire.header.stamp_ns-before.wire.header.stamp_ns
        require(0<interval<=200000000 and before.receipt.ns<current.receipt.ns and
            before.wire.header.stamp_ns<lower.wire.header.stamp_ns<=current.wire.header.stamp_ns and
            current.wire.header.stamp_ns-lower.wire.header.stamp_ns<=60000000,'causal_interval_required')
        dx,dy=record.delta_xy_laser_plane_m;yaw=record.fitted_laser_plane_yaw_rad
        require(all(finite(v) for v in (dx,dy,yaw)) and abs(yaw)<math.pi,'unambiguous_native_delta_required')
        with self._lock:
            self._configured();require(self._sequence<MAX_NS,'control_sequence_exhausted')
            endpoint=(before.original_sha256,before.wire.header.stamp_ns,before.receipt.ns)
            if self._last_endpoint is not None and endpoint!=self._last_endpoint:
                self._body.invalidate('discontinuous_original_pair');self._pose=(0.,0.,0.)
            previous=self._pose;c,z=math.cos(previous[2]),math.sin(previous[2])
            pose=(previous[0]+c*dx-z*dy,previous[1]+z*dx+c*dy,previous[2]+yaw)
            self._sequence+=1;self._captures+=1
            context=dict(source_session=self.ingress.clock_session,epoch=self._epoch,generation=self.ingress.generation,
                source_clock_domain='ROS_SYSTEM_TIME',receipt_clock_domain='CLOCK_MONOTONIC_RAW:'+self.ingress.domain)
            amin,_amax,inc,_dt,_cycle,mn,mx=map(float,current.wire.metadata)
            q,_qc,angular,_ac,_linear,_lc=lower.wire.groups
            norm=math.sqrt(sum(float(v)*float(v) for v in q));require(.9<norm<1.1,'original_IMU_quaternion_required')
            x,y,z,w=(float(v)/norm for v in q);gx,gy,gz=map(float,angular)
            vertical=2*(x*z-w*y)*gx+2*(y*z+w*x)*gy+(1-2*(x*x+y*y))*gz
            require(finite(vertical),'finite_original_gravity_yaw_required')
            source=dict(context=context,
                scan=dict(stamp_ns=current.wire.header.stamp_ns,receipt_ns=current.receipt.ns,
                    sequence=self._sequence,frame_id=current.wire.header.frame_id,
                    ranges=[float(v) if math.isfinite(float(v)) else None for v in current.wire.ranges],
                    angle_min=amin,angle_increment=inc,range_min=mn,range_max=mx,
                    original_native=not self.record_replay,nonfinite_ranges_as_unknown=True,
                    original_CDR_sha256=current.original_sha256,publisher_gid_hex=current.publisher_gid.hex()),
                motion=dict(previous_scan_stamp_ns=before.wire.header.stamp_ns,
                    current_scan_stamp_ns=current.wire.header.stamp_ns,receipt_ns=current.receipt.ns,
                    dx_m=dx,dy_m=dy,dyaw_rad=yaw,accepted=True,map_eligible=True,path_break=False,motion_prior=False),
                imu=dict(stamp_ns=lower.wire.header.stamp_ns,receipt_ns=lower.receipt.ns,
                    yaw_rate_rad_s=vertical,original_native=not self.record_replay,
                    original_CDR_sha256=lower.original_sha256,publisher_gid_hex=lower.publisher_gid.hex()),
                source_now_ns=record.observed_clock.source_ns,receipt_now_ns=record.observed_clock.receipt_ns)
            e=None
            if not self.record_replay and callable(self.evidence_provider):
                e=self.evidence_provider(copy.deepcopy(source))
            admitted=self._evidence(e,source,strict=False)
            fc=FeedbackContext(context['source_session'],context['epoch'],context['generation'],
                context['source_clock_domain'],context['receipt_clock_domain'],'native_contiguous_laser_pose',current.wire.header.frame_id)
            pair=LaserPosePair(fc,self._sequence-1,self._sequence,before.wire.header.stamp_ns,
                current.wire.header.stamp_ns,before.receipt.ns,current.receipt.ns,previous,pose,
                accepted=True,map_eligible=True,independent_localization_validated=admitted,
                planar_supported=admitted,source_clock_verified=admitted)
            imu=NativeImuYaw(fc,lower.wire.header.stamp_ns,lower.receipt.ns,vertical,native=not self.record_replay,
                gravity_projected=True,mount_verified=admitted,imu_frame=lower.wire.header.frame_id)
            observation=self._body.observe(pair,imu,source_now_ns=source['source_now_ns'],receipt_now_ns=source['receipt_now_ns'])
            self._configured()  # Stop/context changes during an injected verifier cannot publish.
            self._accepted=(source,fingerprint({k:source[k] for k in ('context','scan','motion','imu')}),e,
                            hashlib.sha256(repr(e).encode()).hexdigest())
            recorded=(source['source_now_ns'],source['receipt_now_ns'])
            self._comparison=(recorded if self._comparison is None else
                tuple(max(a,b) for a,b in zip(self._comparison,recorded)))
            self._last_endpoint=(current.original_sha256,current.wire.header.stamp_ns,current.receipt.ns);self._pose=pose
            self._last_diagnostic=observation;self._reason=observation['reason']
    def _evidence(self,e,source,*,strict):
        valid=(not self.record_replay and type(e) is ActivationEvidence and type(e.context) is NativeContext and
            e.context.valid() and type(self.mount) is MeasuredLaserMount and self.mount.valid() and
            type(self.profile) is MeasuredProfile and self.profile.valid() and
            type(self.timing) is TimingLimits and self.timing.valid() and
            type(self.feedback_timing) is FeedbackTiming and self.feedback_timing.valid() and
            type(self.footprint) is MeasuredFootprint and self.footprint.valid() and
            self.mount.calibration_id==self.profile.calibration_id==self.footprint.calibration_id==e.context.calibration_id and
            (e.context.source_session,e.context.epoch,e.context.generation,e.context.source_clock,e.context.receipt_clock)==
            tuple(source['context'][k] for k in ('source_session','epoch','generation','source_clock_domain','receipt_clock_domain')) and
            all(getattr(e,k) is True for k in ('body_mount_verified','native_body_velocity_validated','braking_verified',
                'emergency_stop_verified','source_pipeline_validated','swept_provider_validated','motor_configuration_reviewed')) and
            all(type(getattr(e,k)) is str and 0<len(getattr(e,k))<=128 for k in
                ('body_evidence_id','braking_evidence_id','emergency_stop_evidence_id')))
        if strict:require(valid,'genuine_supplied_physical_bindings_required')
        return valid
    def input(self,*,source_now_ns,receipt_now_ns):
        # Comparison clocks are supplied from the independent original clock
        # owner; sensor Header/receipts and IMU selection are never replaced.
        with self._lock:
            self._configured()
            if self._accepted is None:return None
            value=copy.deepcopy(self._accepted[0]);value.update(source_now_ns=source_now_ns,receipt_now_ns=receipt_now_ns)
            try:
                self._comparison_fence(source_now_ns,receipt_now_ns)
                ages=self._ages(value)
                require(all(v>=0 for v in ages),'original_control_input_future')
                if any(v>=limit for v,limit in zip(ages,
                        (SOURCE_AGE_NS,SOURCE_AGE_NS,RECEIPT_AGE_NS,RECEIPT_AGE_NS,RECEIPT_AGE_NS))):
                    self.invalidate_gap('original_control_input_expired');return None
                return value
            except Exception:
                self.invalidate_gap('invalid_control_comparison_clock');raise
    def _comparison_fence(self,source_now_ns,receipt_now_ns):
        require(stamp(source_now_ns) and stamp(receipt_now_ns),'typed_comparison_clocks_required')
        require(self._comparison is None or
            (source_now_ns>=self._comparison[0] and receipt_now_ns>=self._comparison[1]),
            'comparison_clock_rewind')
        self._comparison=(source_now_ns,receipt_now_ns)
    @staticmethod
    def _ages(s):
        return tuple(s['source_now_ns']-s[k]['stamp_ns'] for k in ('scan','imu'))+tuple(
            s['receipt_now_ns']-s[k]['receipt_ns'] for k in ('scan','imu','motion'))
    @staticmethod
    def _fresh(s):
        require(stamp(s['source_now_ns']) and stamp(s['receipt_now_ns']),'typed_comparison_clocks_required')
        require(all(0<=s['source_now_ns']-s[k]['stamp_ns']<SOURCE_AGE_NS for k in ('scan','imu')) and
            all(0<=s['receipt_now_ns']-s[k]['receipt_ns']<RECEIPT_AGE_NS for k in ('scan','imu','motion')),
            'original_control_input_stale_or_future')
    def control_provider(self,s,decision,profile):
        with self._lock:
            self._configured();require(self.enabled and not self.record_replay and self._accepted is not None,
                'native_control_adapter_not_operational')
            try:
                self._comparison_fence(s['source_now_ns'],s['receipt_now_ns']);self._fresh(s)
            except Exception:
                self.invalidate_gap('invalid_or_expired_control_clock');raise
            source,digest,e,e_digest=self._accepted
            require(fingerprint({k:s[k] for k in ('context','scan','motion','imu')})==digest,
                    'exact_current_committed_input_required')
            require(profile is self.profile and callable(self.evidence_provider),'frozen_measured_profile_required')
            current=self.evidence_provider(copy.deepcopy(s));require(current==e and
                hashlib.sha256(repr(current).encode()).hexdigest()==e_digest,'physical_evidence_changed')
            self._evidence(current,s,strict=True)
            observation=self._body.snapshot(source_now_ns=s['source_now_ns'],receipt_now_ns=s['receipt_now_ns'])
            require(observation['valid'] is True and observation['body'] is not None,'BODY_HOLD:'+observation['reason'])
            require(type(decision) is dict and decision.get('decision')=='PROPOSE_FORWARD' and
                decision.get('record_replay_estimate') is False and decision.get('source_provenance_unverified') is False,
                'original_native_forward_decision_required')
            left,right=(decision.get('proposed_'+k+'_percent') for k in ('left','right'))
            require(all(finite(v) and 0<v<=self.profile.max_percent for v in (left,right)),
                    'forward_proposal_within_measured_profile_required')
            # This selects a target through measured FF. It never measures body
            # velocity from PWM; measured feedback comes only from native pose.
            forward=(left+right)*.5/self.profile.linear_ff;diff=(right-left)*.5
            require(diff==0 or self.profile.yaw_ff>0,'measured_yaw_feedforward_required')
            yaw=0. if diff==0 else diff/self.profile.yaw_ff
            require(0<forward<=self.profile.max_forward_m_s and abs(yaw)<=self.profile.max_yaw_rad_s,
                    'target_outside_measured_forward_envelope')
            body=observation['body'];fb=NativeFeedback(e.context,s['scan']['stamp_ns'],s['scan']['receipt_ns'],
                body['forward_m_s'],body['lateral_m_s'],body['yaw_rad_s'],True)
            require(0<=fb.forward_m_s<=self.profile.max_feedback_m_s and
                abs(fb.lateral_m_s)<=self.profile.max_lateral_m_s and
                abs(fb.yaw_rad_s)<=self.profile.max_yaw_rad_s and
                max(forward,fb.forward_m_s)<=self.profile.braking_test_speed_m_s,
                'feedback_outside_measured_braking_envelope')
            age=max(s['source_now_ns']-s['scan']['stamp_ns'],s['receipt_now_ns']-s['scan']['receipt_ns'])*1e-9
            required=self.profile.stop_distance_m+max(forward,fb.forward_m_s)*age
            distance=self._clearance(s['scan'],forward_m_s=forward,yaw_rad_s=yaw,path_length_m=required)
            require(distance>=required,'swept_obstacle_or_insufficient_braking_clearance')
            self._configured()
            return MeasuredControlFrame(fb,SweptSpace(e.context,s['scan']['stamp_ns'],s['scan']['receipt_ns'],
                s['receipt_now_ns'],'CLEAR',True,forward,yaw,distance),forward,yaw)
    def _clearance(self,scan,*,forward_m_s=None,yaw_rad_s=0.,path_length_m=None):
        if forward_m_s is None and path_length_m is None:
            return self._all_direction_clearance(scan)
        require(all(finite(v) for v in (forward_m_s,yaw_rad_s,path_length_m)) and
            forward_m_s>0 and path_length_m>0,'explicit_finite_forward_sweep_required')
        ranges=scan['ranges'];n=len(ranges);inc=scan['angle_increment']
        require(120<=n<=4096 and finite(inc) and 0<inc<=.1 and
            min(abs(n*inc-2*math.pi),abs((n-1)*inc-2*math.pi))<=2e-4,
            'swept_full_angular_coverage_unknown')
        radius=self.footprint.radius_m;mx,my=self.mount.x_m,self.mount.y_m
        require(math.hypot(mx,my)<radius,'laser_origin_outside_measured_footprint_envelope')
        curvature=yaw_rad_s/forward_m_s;angle=curvature*path_length_m
        require(finite(angle) and abs(angle)<=32*math.pi,'swept_trajectory_work_budget')
        if curvature==0:
            centers=[(0.,0.),(path_length_m,0.)]
        else:
            steps=max(1,math.ceil(abs(angle)/(math.pi/4)))
            centers=[(0.,0.)]
            # The endpoint+tangent-intersection triangle contains each complete
            # circular center arc. No trajectory point sampling is trusted as
            # continuous coverage, and no original beam is dropped.
            for i in range(steps):
                a=angle*i/steps;b=angle*(i+1)/steps
                first=(math.sin(a)/curvature,(1-math.cos(a))/curvature)
                tangent=math.tan((b-a)/2)/curvature
                centers.extend(((first[0]+math.cos(a)*tangent,first[1]+math.sin(a)*tangent),
                    (math.sin(b)/curvature,(1-math.cos(b))/curvature)))
        # Circumscribed polygon contains the entire measured footprint disk.
        # Its finite convex hull overfills the curved sweep conservatively.
        r=radius/math.cos(math.pi/32)
        body=[(r*math.cos(2*math.pi*i/32+math.pi/32),
               r*math.sin(2*math.pi*i/32+math.pi/32)) for i in range(32)]
        hull=self._hull([(c[0]+b[0],c[1]+b[1]) for c in centers for b in body])
        angles=[scan['angle_min']+self.mount.yaw_rad+i*inc for i in range(n)]
        def valid(v):return finite(v) and scan['range_min']<=v<=scan['range_max']
        for i in range(n):
            j=(i+1)%n;a=angles[i];b=angles[j] if j else angles[0]+2*math.pi
            span=b-a
            if span<=0:continue  # repeated full-circle endpoint, not a gap
            require(span<=.1,'unobserved_scan_angular_gap')
            u=(math.cos(a),math.sin(a));v=(math.cos(b),math.sin(b))
            if not (valid(ranges[i]) and valid(ranges[j])):
                require(self._unknown_outside_new_sweep(a,b,centers,radius,(mx,my)),
                    'swept_unknown_inside_corridor:'+str(i))
                continue
            clipped=self._clip(hull,lambda q:u[0]*(q[1]-my)-u[1]*(q[0]-mx))
            clipped=self._clip(clipped,lambda q:-v[0]*(q[1]-my)+v[1]*(q[0]-mx))
            A=(mx+ranges[i]*u[0],my+ranges[i]*u[1])
            B=(mx+ranges[j]*v[0],my+ranges[j]*v[1]);dx,dy=B[0]-A[0],B[1]-A[1]
            require(dx*dx+dy*dy>0,'degenerate_original_free_sector')
            # Known adjacent endpoints form the original free-sector boundary.
            # Every clipped hull vertex must remain strictly on its origin side.
            require(all(dx*(q[1]-A[1])-dy*(q[0]-A[0])>0 for q in clipped),
                'swept_obstacle_inside_corridor:'+str(i))
        return path_length_m  # report no distance beyond the tested request
    @staticmethod
    def _hull(points):
        points=sorted(set(points))
        def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        lower=[];upper=[]
        for q in points:
            while len(lower)>1 and cross(lower[-2],lower[-1],q)<=0:lower.pop()
            lower.append(q)
        for q in reversed(points):
            while len(upper)>1 and cross(upper[-2],upper[-1],q)<=0:upper.pop()
            upper.append(q)
        return lower[:-1]+upper[:-1]
    @staticmethod
    def _clip(points,side):
        out=[]
        if not points:return out
        a=points[-1];sa=side(a)
        for b in points:
            sb=side(b)
            if (sa>=0)!=(sb>=0):
                t=sa/(sa-sb);out.append((a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])))
            if sb>=0:out.append(b)
            a,sa=b,sb
        return out
    @staticmethod
    def _unknown_outside_new_sweep(a,b,centers,radius,mount):
        # Only an independently known-clear current footprint envelope may be
        # excluded. A measured circumradius alone does NOT establish this:
        # the supplied swept-provider binding must cover initial clearance and
        # the executed/braking trajectory, not just static mount calibration.
        # Unknown environment beyond the envelope is never converted to free.
        # These are absent real deployment prerequisites, not certificates here.
        mx,my=mount
        def on_arc(direction,start,end):return (direction-start)%(2*math.pi)<=end-start
        exits=[]
        for angle in (a,b):
            ux,uy=math.cos(angle),math.sin(angle);dot=mx*ux+my*uy
            length=-dot+math.sqrt(dot*dot+radius*radius-mx*mx-my*my)
            exits.append((mx+length*ux,my+length*uy))
        beta0=math.atan2(exits[0][1],exits[0][0])
        beta1=beta0+(math.atan2(exits[1][1],exits[1][0])-beta0)%(2*math.pi)
        for cx,cy in centers:
            if cx==cy==0:continue
            direction=math.atan2(cy,cx)
            ray=max(cx*math.cos(a)+cy*math.sin(a),cx*math.cos(b)+cy*math.sin(b))
            edge=max(cx*qx+cy*qy for qx,qy in exits)
            if on_arc(direction,a,b) or on_arc(direction,beta0,beta1):return False
            # For every exterior ray point q, c dot q<=0 proves
            # |q-c|^2 >= |q|^2+|c|^2 > footprint_radius^2.
            # Strict negativity rejects uncertain/tangent sector boundaries.
            if not (ray<0 and edge<0):return False
        return True
    def _all_direction_clearance(self,scan):
        # Conservative all-direction enclosing disk: covers the complete forward
        # arc up to its path length, not merely the front beam. The measured
        # planar coverage binding must authorize adjacent-ray free sectors.
        ranges=scan['ranges'];n=len(ranges);inc=scan['angle_increment']
        require(120<=n<=4096 and finite(inc) and inc>0 and
                min(abs(n*inc-2*math.pi),abs((n-1)*inc-2*math.pi))<=2e-4,
                'swept_full_angular_coverage_unknown')
        require(all(finite(v) and scan['range_min']<=v<=scan['range_max'] for v in ranges),
                'swept_unknown_beam')
        c,z=math.cos(self.mount.yaw_rad),math.sin(self.mount.yaw_rad)
        points=[]
        for i,r in enumerate(ranges):
            a=scan['angle_min']+i*inc;x,y=r*math.cos(a),r*math.sin(a)
            points.append((self.mount.x_m+c*x-z*y,self.mount.y_m+z*x+c*y))
        winding=0;clear=math.inf
        for a,b in zip(points,points[1:]+points[:1]):
            ax,ay=a;bx,by=b;dx,dy=bx-ax,by-ay;den=dx*dx+dy*dy
            t=0. if den==0 else max(0.,min(1.,-(ax*dx+ay*dy)/den))
            clear=min(clear,math.hypot(ax+t*dx,ay+t*dy))
            cross=ax*by-ay*bx
            if ay<=0<by and cross>0:winding+=1
            elif by<=0<ay and cross<0:winding-=1
        require(winding!=0,'body_origin_outside_known_scan_region')
        return max(0.,clear-self.footprint.radius_m)
