"""Default-OFF original-pair visibility proposal; no motor/control authority.

This changes the supplemental consistency denominator, not numeric thresholds.
Original kernel support/information gates and C1b+G weak-normal/alias proposals
remain. Best visibility is .80/.50/.08; competitors use existing lenient .70.
Original complete scan rays are unchanged and never replaced by these surfaces.
"""
from collections import Counter
import hashlib
import math
import time
import numpy as np
from common.original_cdr import decode,LASER_TYPE,WireScan
from native_map_owner import require
from keyframe_registration import (scan_geometry,geometry_digest,seed_grid,fit_surfaces,
    _weak_normal,_screen_peaks,_fit_free,_DEGENERATE,IDENTITY_BOUND_M,IDENTITY_BOUND_RAD,
    SEED_CENTER_MIN_M,SEED_CENTER_MIN_RAD,SEED_CENTER_MAX_M,SEED_CENTER_MAX_RAD)
from pair_visibility import paired_visibility


def register_original_pair(target_cdr,current_cdr,*,enabled=False,target_sha256=None,
        current_sha256=None,cancelled=lambda:False,max_wall_s=1.5,seed_center=None):
    require(type(enabled) is bool,'typed_paired_visibility_default_OFF_required')
    off=dict(accepted=False,reason='paired_visibility_default_OFF',runtime_authority=False,
             motor_command_sent=False,physical_accuracy_verified=False)
    if not enabled:return off
    require(type(max_wall_s) in (float,int) and math.isfinite(max_wall_s) and .05<=max_wall_s<=2.,'bounded_recovery_work_deadline_required')
    began=time.monotonic()
    for raw,sha in ((target_cdr,target_sha256),(current_cdr,current_sha256)):
        require(type(raw) is bytes and hashlib.sha256(raw).hexdigest()==sha,'original_pair_CDR_SHA_changed')
    target_wire,target=scan_geometry(target_cdr,target_sha256)
    current_wire,current=scan_geometry(current_cdr,current_sha256)
    require(target_wire.header.frame_id==current_wire.header.frame_id and
            current_wire.header.stamp_ns>target_wire.header.stamp_ns,'same_original_frame_forward_pair_required')
    require(all(w.count>=60 and float(w.metadata[2])>0 and
                len(g.points)>=60 and len(g.dense)>=60 for w,g in
                ((target_wire,target),(current_wire,current))),
            'valid_original_polar_and_supported_surfaces_required')
    seals=(geometry_digest(target),geometry_digest(current))
    results=[];attempted=0;failures=Counter()

    def expired():return cancelled() or time.monotonic()-began>=max_wall_s
    def end(reason,**fields):
        return dict(accepted=False,reason=reason,attempted=attempted,failures=dict(failures),
                    runtime_authority=False,motor_command_sent=False,physical_accuracy_verified=False,**fields)
    def visibility(fit):
        return paired_visibility(target_wire,target,current_wire,current,
            (*map(float,fit['xy']),float(fit['yaw'])))
    def finite_fit(fit,original_information_required=True):
        fields=('xy','yaw','coverage','rmse_m')
        if original_information_required:fields+=('information_eigenvalues',)
        require(all(k in fit and np.all(np.isfinite(fit[k])) for k in fields),
                'nonfinite_original_pair_fit_rejected')
    def permitted(m,lenient=False):
        threshold=.70 if lenient else .80
        return (min(m['F_vis'],m['B_visK'])>=threshold and
                min(m['obs'],m['backward_observed_share'])>=.50 and max(m['rms_f'],m['rms_bK'])<=.08)
    def separated(a,b):
        dyaw=abs(math.atan2(math.sin(a['yaw']-b['yaw']),math.cos(a['yaw']-b['yaw'])))
        return np.linalg.norm(a['xy']-b['xy'])>.08 or dyaw>math.radians(1)
    def comparable(a,b):
        rms=a['rmse_m']<=max(.015,b['rmse_m']*1.3)
        return rms and (a['coverage']>=b['coverage']-.03 or
            (a['pair_visibility']['F_vis']>=b['pair_visibility']['F_vis']-.05 and
             a['pair_visibility']['B_visK']>=b['pair_visibility']['B_visK']-.05))
    def far(a,b):
        return (np.linalg.norm(a['xy']-b['xy'])>.25 or
                abs(math.atan2(math.sin(a['yaw']-b['yaw']),math.cos(a['yaw']-b['yaw'])))>math.radians(2.5))

    seeds=[(yaw,xy,IDENTITY_BOUND_M,IDENTITY_BOUND_RAD) for yaw,xy in seed_grid()]
    shifted=False
    if seed_center is not None:
        require(type(seed_center) is tuple and len(seed_center)==3 and
                all(type(x) is float and math.isfinite(x) for x in seed_center),'finite_seed_center_required')
        cx,cy,ca=seed_center;reach=math.hypot(cx,cy)
        if ((reach>SEED_CENTER_MIN_M or abs(ca)>SEED_CENTER_MIN_RAD) and
            reach<=SEED_CENTER_MAX_M and abs(ca)<=SEED_CENTER_MAX_RAD):
            seeds += [(yaw,xy,IDENTITY_BOUND_M+reach,IDENTITY_BOUND_RAD+abs(ca))
                      for yaw,xy in seed_grid(seed_center)]
            shifted=True
    for yaw,xy,bm,ba in seeds:
        if expired():return end('pair_visibility_cancelled_or_work_deadline')
        diagnostic=Counter();attempted+=1
        fit=fit_surfaces(target,current,yaw,np.asarray(xy),bm,ba,diagnostic)
        failures.update(diagnostic)
        if fit is None:continue
        finite_fit(fit)
        m=visibility(fit)
        if not permitted(m,lenient=True):
            failures['pair_visibility_inconsistent']+=1;continue
        # Keep lenient competitors even when they would fail the best's weak
        # normal gate. They may still make a purported strong best ambiguous.
        results.append(dict(fit,pair_visibility=m,weak_normal=_weak_normal(target,current,fit)))
    if expired():return end('pair_visibility_cancelled_or_work_deadline')
    eligible=[f for f in results if permitted(f['pair_visibility']) and f['weak_normal']>=.12]
    if not eligible:return end('unmatched_original_pair_visibility')
    eligible.sort(key=lambda f:(-f['coverage'],f['rmse_m']))
    best=eligible[0]
    for other in results:
        if other is not best and ((permitted(other['pair_visibility']) and far(other,best)) or
                                  (separated(other,best) and comparable(other,best))):
            return end('ambiguous_original_pair_visibility')
    # Same C1b+G translation/rotation alias seed screen. All competing fits use
    # paired visibility; none are silently discarded by the old raw denominator.
    for sx,sy,sa in _screen_peaks(target,current,(*map(float,best['xy']),float(best['yaw']))):
        if expired():return end('pair_visibility_cancelled_or_work_deadline')
        diagnostic=Counter();attempted+=1;reach=math.hypot(sx,sy)
        bounds=(IDENTITY_BOUND_M+reach,IDENTITY_BOUND_RAD+abs(sa))
        fit=fit_surfaces(target,current,sa,np.array((sx,sy)),*bounds,diagnostic)
        original_information_required=True
        if fit is None and any(diagnostic.get(d) for d in _DEGENERATE):
            fit=_fit_free(target,current,sa,np.array((sx,sy)),*bounds)
            original_information_required=False
        if fit is None:continue
        finite_fit(fit,original_information_required)
        m=visibility(fit)
        if not permitted(m,lenient=True):continue
        other=dict(fit,pair_visibility=m)
        if (permitted(m) and far(other,best)) or (separated(other,best) and comparable(other,best)):
            return end('ambiguous_screened_original_pair_visibility')
    if expired():return end('pair_visibility_cancelled_or_work_deadline')
    require((geometry_digest(target),geometry_digest(current))==seals,'original_pair_geometry_changed')
    require(hashlib.sha256(target_cdr).hexdigest()==target_sha256 and
            hashlib.sha256(current_cdr).hexdigest()==current_sha256,'original_pair_CDR_SHA_changed')
    return dict(best,accepted=True,reason='unverified_original_pair_visibility_registration',
                target_original_CDR_sha256=target_sha256,current_original_CDR_sha256=current_sha256,
                source_stamps_ns=(target_wire.header.stamp_ns,current_wire.header.stamp_ns),
                attempted=attempted,shifted_seed_grid=shifted,search_complete=True,
                search_global_uniqueness_verified=False,rolling_scan_deskewed=False,
                supplemental_denominator_changed=True,original_full_return_metrics_unchanged_and_reported=True,
                supplemental_full_return_gate_replaced_by_paired_visibility=True,
                runtime_qualified=False,physical_accuracy_verified=False,runtime_authority=False,
                motor_command_sent=False,elapsed_s=time.monotonic()-began)
