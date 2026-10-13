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


# --- Scan-to-map bridge (C6) ------------------------------------------------------------------
# Target: the retained keyframe K plus preceding published scans of K's own continuous chain,
# placed in K's frame with their committed map poses. Acceptance is evaluated against that local
# map with line-of-sight visibility instead of K alone; the pinned fit_surfaces gates, the seed
# grids and the ambiguity rule are unchanged, and a direction-free alias probe rejects any
# separated comparable or outranking minimum. A search hint, never pose evidence.
MAP_MATCH_M, MAP_RMS_MAX, MAP_OCC_TOL, MAP_WIN, MAP_MAX_RANGE = .15, .08, .15, 2, 20.
MAP_F_MIN = MAP_B_MIN = .80
MAP_OBS_MIN, MAP_LENIENT_DROP, MAP_VIS_DELTA = .50, .10, .05
MAP_VOXEL_M, MAP_ANCHORS = .05, 4000
ALIAS_REACH_M, ALIAS_GRID_M, ALIAS_RASTER_M, ALIAS_EXCL_M, ALIAS_RHO, ALIAS_MAX_PEAKS, ALIAS_SCREEN = 4., .10, .05, .4, .5, 6, 160


def _rot(a):
    c,s=math.cos(a),math.sin(a)
    return np.array([[c,-s],[s,c]])


def to_parent(p,pose):
    return p@_rot(pose[2]).T+np.asarray(pose[:2],dtype=float)


def to_child(p,pose):
    return (p-np.asarray(pose[:2],dtype=float))@_rot(pose[2])


class ScanPolar:
    """Range image of one original scan; invalid or out-of-band returns are +inf (unknown)."""
    def __init__(self,wire):
        amin,_amax,inc,_dt,_cycle,mn,mx=map(float,wire.metadata)
        r=np.asarray([float(v) for v in wire.ranges],dtype=float)
        valid=np.isfinite(r)&(r>=max(.05,mn))&(r<min(MAP_MAX_RANGE,mx))
        self.r=np.where(valid,r,np.inf);self.amin=amin;self.inc=inc;self.n=len(r)
        self.full_turn=self.n*inc>=2*math.pi-2*inc
        self.r.setflags(write=False)

    def observable(self,pts):
        """Points (this scan's frame) inside its line of sight: nothing returned in front of them."""
        rng=np.hypot(pts[:,0],pts[:,1])
        bearing=np.mod(np.arctan2(pts[:,1],pts[:,0])-self.amin,2*math.pi)
        idx=np.rint(bearing/self.inc).astype(np.int64)
        cols=idx[:,None]+np.arange(-MAP_WIN,MAP_WIN+1)[None,:]
        if self.full_turn:
            near=self.r[np.mod(cols,self.n)]
        else:
            inside=(cols>=0)&(cols<self.n)
            near=np.where(inside,self.r[np.clip(cols,0,self.n-1)],np.inf)
        closest=near.min(axis=1)
        return (rng<MAP_MAX_RANGE)&np.isfinite(closest)&(closest>=rng-MAP_OCC_TOL)


class MapBridgeTarget:
    """Local map around K: [(original scan CDR, sha256, pose in K frame)], K first."""
    def __init__(self,members):
        require(members and members[0][2]==(0.,0.,0.),'keyframe_first_map_target_required')
        clouds,polars=[],[]
        for cdr,sha,pose in members:
            require(type(cdr) is bytes and hashlib.sha256(cdr).hexdigest()==sha,'exact_map_member_CDR_required')
            wire=decode(cdr,LASER_TYPE)
            require(type(wire) is WireScan,'original_map_member_scan_required')
            amin,_amax,inc,_dt,_cycle,mn,mx=map(float,wire.metadata)
            pts=scan_points(dict(ranges=wire.ranges,angle_min=amin,angle_increment=inc,range_min=mn,range_max=mx))
            clouds.append(to_parent(pts,pose));polars.append((pose,ScanPolar(wire)))
        raw=np.vstack(clouds)
        src=np.concatenate([np.full(len(c),j,dtype=np.int64) for j,c in enumerate(clouds)])
        keys=np.floor(raw/MAP_VOXEL_M).astype(np.int64)
        flat=(keys[:,0]+(1<<30))*(1<<31)+(keys[:,1]+(1<<30))
        keep=np.sort(np.unique(flat,return_index=True)[1])  # first occurrence wins: K first
        dense=raw[keep]
        self.surf=freeze_geometry(surfaces(dense,maximum=min(MAP_ANCHORS,len(dense))))
        self.k_dense=np.frombuffer(dense[src[keep]==0].tobytes(),dtype=float).reshape(-1,2)
        self.polars=polars;self.members=len(members);self._edt=None

    def edt(self):
        if self._edt is None:
            from scipy import ndimage
            dense=self.surf.dense;lo=dense.min(axis=0)-1.
            shape=tuple(int(v) for v in np.ceil((dense.max(axis=0)+1.-lo)/ALIAS_RASTER_M)+1)
            free=np.ones(shape,dtype=bool)
            idx=np.floor((dense-lo)/ALIAS_RASTER_M).astype(np.int64);free[idx[:,0],idx[:,1]]=False
            self._edt=(ndimage.distance_transform_edt(free).astype(np.float32)*ALIAS_RASTER_M,lo)
        return self._edt


def _map_metrics(target,h_surf,h_polar,pose):
    hk=to_parent(h_surf.dense,pose)
    d_f=target.surf.dense_tree.query(hk,workers=1)[0];m_f=d_f<MAP_MATCH_M
    seen=np.zeros(len(hk),dtype=bool)
    for mpose,polar in target.polars:
        seen|=polar.observable(to_child(hk,mpose))
    den_f=int((seen|m_f).sum())
    kh=to_child(target.k_dense,pose)
    d_b=h_surf.dense_tree.query(kh,workers=1)[0];m_b=d_b<MAP_MATCH_M
    den_b=int((h_polar.observable(kh)|m_b).sum())
    def rms(d,m):return float(np.sqrt(np.mean(d[m]**2))) if m.any() else math.inf
    return dict(F_vis=float(m_f.sum()/max(den_f,1)),obs=float(den_f/max(len(hk),1)),
                B_visK=float(m_b.sum()/max(den_b,1)),rms_f=rms(d_f,m_f),rms_bK=rms(d_b,m_b))


def _map_ok(m,drop=0.):
    return (m['F_vis']>=MAP_F_MIN-drop and m['B_visK']>=MAP_B_MIN-drop and m['obs']>=MAP_OBS_MIN and
            max(m['rms_f'],m['rms_bK'])<=MAP_RMS_MAX)


def _separated(a,b):
    return np.linalg.norm(np.asarray(a['xy'])-np.asarray(b['xy']))>.08 or abs(a['yaw']-b['yaw'])>math.radians(1)


def _comparable(other,best,mo,mb):
    rmse_ok=other['rmse_m']<=max(.015,best['rmse_m']*1.3)
    if other['coverage']>=best['coverage']-.03 and rmse_ok:return True
    return (mo is not None and mb is not None and rmse_ok and mo['F_vis']>=mb['F_vis']-MAP_VIS_DELTA and
            mo['B_visK']>=mb['B_visK']-MAP_VIS_DELTA)


def _alias_peaks(target,h_surf,pose):
    from scipy import ndimage
    edt,lo=target.edt()
    pts=to_parent(h_surf.points,pose)
    if len(pts)>ALIAS_SCREEN:pts=pts[np.linspace(0,len(pts)-1,ALIAS_SCREEN).astype(int)]
    g=np.arange(-ALIAS_REACH_M,ALIAS_REACH_M+1e-9,ALIAS_GRID_M)
    ox,oy=np.meshgrid(g,g,indexing='ij');off=np.column_stack([ox.ravel(),oy.ravel()])
    q=np.floor((pts[None,:,:]+off[:,None,:]-lo)/ALIAS_RASTER_M).astype(np.int64)
    inside=(q[...,0]>=0)&(q[...,0]<edt.shape[0])&(q[...,1]>=0)&(q[...,1]<edt.shape[1])
    hit=inside&(edt[np.clip(q[...,0],0,edt.shape[0]-1),np.clip(q[...,1],0,edt.shape[1]-1)]<MAP_MATCH_M)
    score=hit.mean(axis=1).reshape(ox.shape);centre=float(score[len(g)//2,len(g)//2])
    local=ndimage.maximum_filter(score,size=3,mode='nearest')<=score
    mask=local&(np.hypot(ox,oy)>=ALIAS_EXCL_M)&(score>=ALIAS_RHO*centre)
    cand=np.column_stack([ox[mask],oy[mask],score[mask]]);cand=cand[np.argsort(-cand[:,2],kind='stable')]
    kept=[]
    for x,y,sc in cand:
        if all(math.hypot(x-a,y-b)>=.3 for a,b,_ in kept):kept.append((float(x),float(y),float(sc)))
        if len(kept)>=ALIAS_MAX_PEAKS:break
    return [(pose[0]+x,pose[1]+y,pose[2]) for x,y,_ in kept]


def register_map(target,current,current_polar, *, cancelled=lambda:False, max_wall_s=2., seed_center=None):
    """Scan-to-map bridge of `current` (H) into K's frame; same result convention as register_keyframe."""
    require(type(max_wall_s) in (int,float) and math.isfinite(max_wall_s) and
            .05<=max_wall_s<=2.,'bounded_recovery_work_deadline_required')
    require(type(target) is MapBridgeTarget and type(current_polar) is ScanPolar,'owned_map_bridge_target_required')
    begin=time.monotonic();fits=[];failures=Counter();attempted=0
    seeds=[(yaw,xy,IDENTITY_BOUND_M,IDENTITY_BOUND_RAD) for yaw,xy in seed_grid()]
    shifted=False
    if seed_center is not None:
        require(type(seed_center) is tuple and len(seed_center)==3 and
                all(type(v) is float and math.isfinite(v) for v in seed_center),'finite_seed_center_required')
        cx,cy,ca=seed_center;reach=math.hypot(cx,cy)
        if ((reach>SEED_CENTER_MIN_M or abs(ca)>SEED_CENTER_MIN_RAD) and
                reach<=SEED_CENTER_MAX_M and abs(ca)<=SEED_CENTER_MAX_RAD):
            seeds+=[(yaw,xy,IDENTITY_BOUND_M+reach,IDENTITY_BOUND_RAD+abs(ca)) for yaw,xy in seed_grid(seed_center)]
            shifted=True
    def stopped(n):
        if cancelled():return dict(accepted=False,reason='recovery_cancelled',attempted=n,shifted_seed_grid=shifted)
        if time.monotonic()-begin>=max_wall_s:
            return dict(accepted=False,reason='recovery_work_deadline',attempted=n,shifted_seed_grid=shifted)
    for yaw,xy,max_translation,max_yaw in seeds:
        out=stopped(attempted)
        if out:return out
        diagnostic=Counter();attempted+=1
        fit=fit_surfaces(target.surf,current,yaw,np.asarray(xy,dtype=float),max_translation,max_yaw,diagnostic)
        failures.update(diagnostic)
        if fit is not None:fits.append(fit)
    cache={}
    def key(f):return (round(float(f['xy'][0]),3),round(float(f['xy'][1]),3),round(float(f['yaw']),4))
    def check(f,drop=0.):
        k=key(f)
        if k not in cache:cache[k]=_map_metrics(target,current,current_polar,(float(f['xy'][0]),float(f['xy'][1]),float(f['yaw'])))
        return _map_ok(cache[k],drop)
    ordered=sorted(fits,key=lambda f:(-f['coverage'],f['rmse_m']))
    best=next((f for f in ordered if check(f)),None)
    if best is None:
        return dict(accepted=False,reason='unmatched_native_map',attempted=attempted,failures=dict(failures),
                    shifted_seed_grid=shifted,map_members=target.members)
    for f in ordered:
        if f is not best and _separated(f,best) and check(f,MAP_LENIENT_DROP) and _comparable(f,best,cache.get(key(f)),cache.get(key(best))):
            return dict(accepted=False,reason='ambiguous_native_map',attempted=attempted,shifted_seed_grid=shifted,map_members=target.members)
    bpose=(float(best['xy'][0]),float(best['xy'][1]),float(best['yaw']))
    for sx,sy,syaw in _alias_peaks(target,current,bpose):
        out=stopped(attempted)
        if out:return out
        attempted+=1;reach=math.hypot(sx,sy)
        f=fit_surfaces(target.surf,current,syaw,np.array([sx,sy]),IDENTITY_BOUND_M+reach,IDENTITY_BOUND_RAD+abs(syaw),Counter())
        if f is None or not check(f,MAP_LENIENT_DROP):continue
        outranks=check(f) and (f['coverage'],-f['rmse_m'])>(best['coverage'],-best['rmse_m'])
        if _separated(f,best) and (outranks or _comparable(f,best,cache.get(key(f)),cache.get(key(best)))):
            return dict(accepted=False,reason='alias_outranks_native_map_best' if outranks else 'ambiguous_native_map_alias',
                        attempted=attempted,shifted_seed_grid=shifted,map_members=target.members)
    out=stopped(attempted)
    if out:return out
    m=cache[key(best)]
    require(all(np.all(np.isfinite(best[k])) for k in ('xy','yaw','coverage','rmse_m','information_eigenvalues')) and
            all(math.isfinite(v) for v in m.values()),'nonfinite_map_bridge_fit_rejected')
    return dict(accepted=True,reason='unverified_bounded_native_scan_to_map_registration',
                attempted=attempted,search_complete=True,search_global_uniqueness_verified=False,
                shifted_seed_grid=shifted,map_members=target.members,map_metrics=m,
                physical_accuracy_verified=False,rolling_scan_deskewed=False,
                elapsed_s=time.monotonic()-begin,**best)
