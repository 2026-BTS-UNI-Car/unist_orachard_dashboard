"""Bounded map-only geometry recovery; no motion certificate.

Uses the pinned original full-surface solver, including its support/information
and residual gates. Local multistart search is not global uniqueness proof.
Historical CDR acquisition clocks are never comparison clocks or fresh inputs.
An optional seed_center (a LiDAR-fit constant-twist extrapolation across a
short in-session gap) adds a second identical seed grid whose solver bound
reaches it; acceptance gates and the ambiguity test run over every result.
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


def geometry_digest(g):
    """Seal of the exact surfaces passed to the solver (arrays, trees, denominator)."""
    h=hashlib.sha256(repr(g.denominator).encode())
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


SEED_CENTER_MIN_M,SEED_CENTER_MIN_RAD=.2,math.radians(5)
SEED_CENTER_MAX_M,SEED_CENTER_MAX_RAD=3.,math.radians(90)
IDENTITY_BOUND_M,IDENTITY_BOUND_RAD=.8,math.radians(30)


def seed_grid(center=(0.,0.,0.)):
    cx,cy,ca=center
    return [(ca+math.radians(a),(cx+x,cy+y)) for a in (-15,0,15)
            for x in (-.4,0,.4) for y in (-.4,0,.4)]


def register_keyframe(target,current, *, cancelled=lambda:False, max_wall_s=1.5, seed_center=None):
    """Identity seed grid, plus the same grid around seed_center when given.

    seed_center is a search hint only (a LiDAR-fit twist extrapolated across a
    short in-session gap). The identity grid keeps the original 0.8 m / 30 deg
    solver bound; the shifted grid's bound reaches its centre (0.8 m + |c_xy|,
    30 deg + |c_yaw|) so its seeds can converge. Every result from both grids
    passes the unchanged solver gates and full-return consistency, and the
    ambiguity test runs over all of them, so a second separated comparable
    minimum anywhere in the searched set still rejects the bridge.
    """
    require(type(max_wall_s) in (int,float) and math.isfinite(max_wall_s) and
            .05<=max_wall_s<=2.,'bounded_recovery_work_deadline_required')
    begin=time.monotonic();results=[];failures=Counter();attempted=0
    seeds=[(yaw,xy,IDENTITY_BOUND_M,IDENTITY_BOUND_RAD) for yaw,xy in seed_grid()]
    shifted=False
    if seed_center is not None:
        require(type(seed_center) is tuple and len(seed_center)==3 and
                all(type(v) is float and math.isfinite(v) for v in seed_center),'finite_seed_center_required')
        cx,cy,ca=seed_center
        reach=math.hypot(cx,cy)
        if ((reach>SEED_CENTER_MIN_M or abs(ca)>SEED_CENTER_MIN_RAD) and
                reach<=SEED_CENTER_MAX_M and abs(ca)<=SEED_CENTER_MAX_RAD):
            bound=(IDENTITY_BOUND_M+reach,IDENTITY_BOUND_RAD+abs(ca))
            seeds+=[(yaw,xy,*bound) for yaw,xy in seed_grid(seed_center)];shifted=True
    for yaw,xy,max_translation,max_yaw in seeds:
        if cancelled():return dict(accepted=False,reason='recovery_cancelled',attempted=attempted,shifted_seed_grid=shifted)
        if time.monotonic()-begin>=max_wall_s:
            return dict(accepted=False,reason='recovery_work_deadline',attempted=attempted,shifted_seed_grid=shifted)
        diagnostic=Counter();attempted+=1
        fit=fit_surfaces(target,current,yaw,np.asarray(xy),max_translation,max_yaw,diagnostic)
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
        return dict(accepted=False,reason='recovery_cancelled_or_work_deadline',attempted=attempted,shifted_seed_grid=shifted)
    if not results:return dict(accepted=False,reason='unmatched_native_keyframe',
                               attempted=attempted,failures=dict(failures),shifted_seed_grid=shifted)
    results.sort(key=lambda f:(-f['coverage'],f['rmse_m']));best=results[0]
    for other in results[1:]:
        separated=np.linalg.norm(other['xy']-best['xy'])>.08 or abs(other['yaw']-best['yaw'])>math.radians(1)
        comparable=other['coverage']>=best['coverage']-.03 and other['rmse_m']<=max(.015,best['rmse_m']*1.3)
        if separated and comparable:
            return dict(accepted=False,reason='ambiguous_native_keyframe',attempted=attempted,shifted_seed_grid=shifted)
    return dict(accepted=True,reason='unverified_bounded_native_keyframe_registration',
                attempted=attempted,search_complete=True,search_global_uniqueness_verified=False,
                shifted_seed_grid=shifted,
                physical_accuracy_verified=False,rolling_scan_deskewed=False,
                elapsed_s=time.monotonic()-begin,**best)
