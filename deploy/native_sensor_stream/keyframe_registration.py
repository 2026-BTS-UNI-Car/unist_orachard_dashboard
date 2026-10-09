"""Bounded map-only geometry recovery; no dt/IMU prior or motion certificate.

Uses the pinned original full-surface solver, including its support/information
and residual gates. Local multistart search is not global uniqueness proof.
Historical CDR acquisition clocks are never comparison clocks or fresh inputs.
"""
from collections import Counter
import hashlib
import math
import time
import numpy as np
from common.original_cdr import decode, LASER_TYPE, WireScan
from kernel.raw_localization import scan_points, surfaces, fit_surfaces, rotate, Surfaces
from scipy.spatial import cKDTree
from native_map_owner import require


def scan_geometry(cdr, expected_sha):
    require(type(cdr) is bytes and hashlib.sha256(cdr).hexdigest()==expected_sha,
            'exact_keyframe_CDR_required')
    wire=decode(cdr,LASER_TYPE)
    require(type(wire) is WireScan,'original_keyframe_scan_required')
    amin,amax,inc,dt,cycle,mn,mx=map(float,wire.metadata)
    require(all(math.isfinite(v) for v in (amin,amax,inc,dt,cycle,mn,mx)) and
            0<=dt and (wire.count-1)*dt<=.2 and 0<=mn<mx<=100,
            'original_keyframe_geometry_required')
    message=dict(ranges=wire.ranges,angle_min=amin,angle_increment=inc,
                 range_min=mn,range_max=mx)
    return wire, surfaces(scan_points(message))


def freeze_geometry(value):
    """Private trees backed by owned immutable arrays, never exposed via status."""
    def frozen(a):return np.frombuffer(a.tobytes(),dtype=a.dtype).reshape(a.shape)
    points,normals,dense=(frozen(a) for a in (value.points,value.normals,value.dense))
    return Surfaces(points,normals,cKDTree(points),value.denominator,dense,cKDTree(dense))


def keyframe_digest(keyframe):
    version,sha,pose,g,raw=keyframe
    h=hashlib.sha256(repr((version,sha,pose,g.denominator)).encode())
    require(hashlib.sha256(raw).hexdigest()==sha,'retained_keyframe_CDR_changed')
    h.update(raw)
    for a in (g.points,g.normals,g.dense,g.tree.data,g.dense_tree.data):
        h.update(repr((a.dtype.str,a.shape)).encode());h.update(a.tobytes())
    return h.digest()


def _full_overlap(target,current,fit):
    moved=current.dense@rotate(fit['yaw']).T+fit['xy']
    forward=target.dense_tree.query(moved,workers=1)[0]
    inverse=(target.dense-fit['xy'])@rotate(fit['yaw'])
    backward=current.dense_tree.query(inverse,workers=1)[0]
    # Supplemental full-return consistency; never change original fit gates.
    fractions=tuple(float(np.mean(d<.15)) for d in (forward,backward))
    rms=tuple(float(np.sqrt(np.mean(d[d<.15]**2))) if np.any(d<.15) else math.inf
              for d in (forward,backward))
    return fractions,rms


def register_keyframe(target,current, *, cancelled=lambda:False, max_wall_s=1.5):
    require(type(max_wall_s) in (int,float) and math.isfinite(max_wall_s) and
            .05<=max_wall_s<=2.,'bounded_recovery_work_deadline_required')
    begin=time.monotonic();results=[];failures=Counter();attempted=0
    # Independent geometry seeds, not a predicted velocity/course/IMU heading.
    # Completeness is required within this explicit +/-0.4m, +/-15deg seed set.
    seeds=[(math.radians(a),(x,y)) for a in (-15,0,15)
           for x in (-.4,0,.4) for y in (-.4,0,.4)]
    for yaw,xy in seeds:
        if cancelled():return dict(accepted=False,reason='recovery_cancelled',attempted=attempted)
        if time.monotonic()-begin>=max_wall_s:
            return dict(accepted=False,reason='recovery_work_deadline',attempted=attempted)
        diagnostic=Counter();attempted+=1
        fit=fit_surfaces(target,current,yaw,np.asarray(xy),.8,math.radians(30),diagnostic)
        failures.update(diagnostic)
        if fit is None:continue
        overlap,rms=_full_overlap(target,current,fit)
        if min(overlap)<.70 or max(rms)>.08:
            failures['full_return_bidirectional_inconsistency']+=1;continue
        fit=dict(fit,full_overlap=overlap,full_return_RMSE_m=rms)
        require(all(np.all(np.isfinite(fit[k])) for k in ('xy','yaw','coverage','rmse_m',
                    'information_eigenvalues','full_overlap','full_return_RMSE_m')),
                'nonfinite_keyframe_fit_rejected')
        results.append(fit)
    if cancelled() or time.monotonic()-begin>=max_wall_s:
        return dict(accepted=False,reason='recovery_cancelled_or_work_deadline',attempted=attempted)
    if not results:return dict(accepted=False,reason='unmatched_native_keyframe',
                               attempted=attempted,failures=dict(failures))
    results.sort(key=lambda f:(-f['coverage'],f['rmse_m']));best=results[0]
    for other in results[1:]:
        separated=np.linalg.norm(other['xy']-best['xy'])>.08 or abs(other['yaw']-best['yaw'])>math.radians(1)
        comparable=other['coverage']>=best['coverage']-.03 and other['rmse_m']<=max(.015,best['rmse_m']*1.3)
        if separated and comparable:
            return dict(accepted=False,reason='ambiguous_native_keyframe',attempted=attempted)
    return dict(accepted=True,reason='unverified_bounded_native_keyframe_registration',
                attempted=attempted,search_complete=True,search_global_uniqueness_verified=False,
                physical_accuracy_verified=False,rolling_scan_deskewed=False,
                elapsed_s=time.monotonic()-begin,**best)
