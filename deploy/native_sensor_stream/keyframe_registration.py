"""Bounded map-only geometry recovery; no motion certificate.

Uses the pinned original full-surface solver, including its support/information
and residual gates. Local multistart search is not global uniqueness proof.
Historical CDR acquisition clocks are never comparison clocks or fresh inputs.
An optional seed_center (a LiDAR-fit constant-twist extrapolation across a
short in-session gap) adds a second identical seed grid whose solver bound
reaches it; acceptance gates and the ambiguity test run over every result.

With gates=True (C1b+G, default OFF), reject-only gates after the unchanged search, selection and ambiguity test:
* separated acceptable alternative: any result that itself passes every gate
  (solver + full-return consistency) and lies beyond the false-accept scale
  from the best (> GATE_SEPARATED_M or > GATE_SEPARATED_RAD) rejects,
  whatever its coverage/rmse (coverage favours poses keeping H inside K's
  view; rmse grows with the range of the overlapping part).
* degeneracy margin: the solver's own final correspondence normals at the
  best pose must carry >= GATE_MIN_WEAK_NORMAL of their scatter across the
  least-constrained translation direction (solver floor .06).
* alias screen (GATE_SCREEN): share of H anchors within .15 m of K over a
  +-4 m translation square (yaw 0 on a .1 m grid, yaw offsets -10..10 deg on a
  .2 m grid) around the best; up to 6+6 peaks are refined with
  the same solver from that seed; a refinement failing only a degeneracy gate
  is re-solved with those gates reported (_fit_free) and used ONLY as a
  competitor; competitors passing every gate and separated beyond the
  false-accept scale reject; competitors at the lenient full-return level
  (GATE_LENIENT_OVERLAP) that are separated and comparable reject.
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

GATE_SEPARATED_M,GATE_SEPARATED_RAD=.25,math.radians(2.5)
GATE_MIN_WEAK_NORMAL=.12
GATE_SCREEN=True
GATE_SCREEN_REACH_M,GATE_SCREEN_POINTS=4.,160
GATE_SCREEN_YAWS=tuple(math.radians(a) for a in range(-10,11))
GATE_SCREEN_RHO,GATE_SCREEN_PEAKS,GATE_SCREEN_EXCL_M,GATE_SCREEN_EXCL_RAD=.5,6,.4,math.radians(2.5)
GATE_LENIENT_OVERLAP=.60
_DEGENERATE=('translation_geometrically_degenerate','pose_information_degenerate')


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


def _weak_normal(target,current,fit):
    """Smallest eigenvalue of the solver's final correspondence normal scatter."""
    moved=current.points@rotate(fit['yaw']).T+fit['xy']
    distance,index=target.tree.query(moved,workers=1);normals=target.normals[index]
    residual=np.einsum('ni,ni->n',target.points[index]-moved,normals)
    aligned=np.abs(np.einsum('ni,ni->n',current.normals@rotate(fit['yaw']).T,normals))>.7
    good=(distance<.25)&(np.abs(residual)<.12)&aligned
    if good.sum()<3:return 0.
    n=normals[good];return float(np.linalg.eigvalsh(n.T@n/len(n))[0])


def _fit_free(target,current,theta,initial_xy,max_translation,max_yaw):
    """fit_surfaces iteration and gates, degeneracy gates not applied (competitor only)."""
    xy=np.asarray(initial_xy,dtype=float).copy()
    for _ in range(15):
        moved=current.points@rotate(theta).T+xy
        distance,index=target.tree.query(moved,workers=1)
        normals=target.normals[index];difference=target.points[index]-moved
        residual=np.einsum('ni,ni->n',difference,normals)
        aligned=np.abs(np.einsum('ni,ni->n',current.normals@rotate(theta).T,normals))>.7
        good=(distance<.30)&(np.abs(residual)<.15)&aligned
        if good.sum()<60:return None
        p=moved[good];n=normals[good];r=residual[good]
        jac=np.column_stack((n[:,0],n[:,1],-n[:,0]*p[:,1]+n[:,1]*p[:,0]))
        weight=np.minimum(1.,.04/np.maximum(np.abs(r),1e-9))
        delta=np.linalg.lstsq(jac*np.sqrt(weight[:,None]),r*np.sqrt(weight),rcond=1e-6)[0]
        theta+=float(delta[2]);xy=rotate(float(delta[2]))@xy+delta[:2]
        if np.linalg.norm(xy)>max_translation or abs(theta)>max_yaw:return None
        if np.linalg.norm(delta[:2])<.0005 and abs(delta[2])<.0001:break
    moved=current.points@rotate(theta).T+xy
    distance,index=target.tree.query(moved,workers=1);normals=target.normals[index]
    residual=np.einsum('ni,ni->n',target.points[index]-moved,normals)
    aligned=np.abs(np.einsum('ni,ni->n',current.normals@rotate(theta).T,normals))>.7
    good=(distance<.25)&(np.abs(residual)<.12)&aligned
    rmse=float(np.sqrt(np.mean(residual[good]**2))) if good.any() else math.inf
    if good.sum()<60 or good.sum()/current.denominator<.4 or rmse>.08:return None
    return dict(xy=xy,yaw=theta,coverage=int(good.sum())/current.denominator,rmse_m=rmse)


def _near_raster(dense,pts):
    """.05 m cells within .15 m of a keyframe point, over where the screened points can land."""
    from scipy import ndimage
    lo=np.maximum(pts.min(axis=0)-GATE_SCREEN_REACH_M-.3,dense.min(axis=0)-.25)
    hi=np.maximum(np.minimum(pts.max(axis=0)+GATE_SCREEN_REACH_M+.3,dense.max(axis=0)+.25),lo+.05)
    d=dense[np.all((dense>=lo-.2)&(dense<=hi+.2),axis=1)]
    shape=tuple(int(v) for v in np.ceil((hi-lo)/.05)+1)
    free=np.ones(shape,dtype=bool);idx=np.floor((d-lo)/.05).astype(np.int64)
    ok=np.all((idx>=0)&(idx<np.array(shape)),axis=1);free[idx[ok,0],idx[ok,1]]=False
    return ndimage.distance_transform_edt(free)*.05<.15,lo


def _peaks(scores,g,yaws,centre):
    """Local maxima in (yaw,x,y) >= GATE_SCREEN_RHO x centre, away from the centre, strongest first."""
    from scipy import ndimage
    ox,oy=np.meshgrid(g,g,indexing='ij')
    far=((np.hypot(ox,oy)[None]>=GATE_SCREEN_EXCL_M)|
         (np.abs(np.asarray(yaws))[:,None,None]>=GATE_SCREEN_EXCL_RAD-1e-9))
    mask=(ndimage.maximum_filter(scores,size=3,mode='nearest')<=scores)&far&(scores>=GATE_SCREEN_RHO*centre)
    jj,xx,yy=np.nonzero(mask);order=np.argsort(-scores[jj,xx,yy],kind='stable');kept=[]
    for o in order:
        c=(float(g[xx[o]]),float(g[yy[o]]),float(yaws[jj[o]]))
        if all(math.hypot(c[0]-k[0],c[1]-k[1])>=.3 or abs(c[2]-k[2])>=math.radians(2) for k in kept):kept.append(c)
        if len(kept)>=GATE_SCREEN_PEAKS:break
    return kept


def _screen_peaks(target,current,pose):
    """Share of H anchors within .15 m of K around pose: yaw 0 on a .1 m grid, then all yaw offsets on
    a .2 m grid (rotated repeats); returns seeds (x,y,yaw), fine peaks first."""
    pts=current.points
    if len(pts)>GATE_SCREEN_POINTS:pts=pts[np.linspace(0,len(pts)-1,GATE_SCREEN_POINTS).astype(int)]
    near,lo=_near_raster(target.dense,np.vstack([pts@rotate(pose[2]+dy).T+np.asarray(pose[:2])
                                                for dy in GATE_SCREEN_YAWS]))
    hn,wn=near.shape;flat=near.ravel()

    def score(yaws,grid):
        step=int(round(grid/.05));nh=int(round(GATE_SCREEN_REACH_M/grid))
        offs=np.arange(-nh,nh+1,dtype=np.int64)*step;g=np.arange(-nh,nh+1)*grid
        out=np.empty((len(yaws),len(g),len(g)))
        for j,dy in enumerate(yaws):
            base=np.floor((pts@rotate(pose[2]+dy).T+np.asarray(pose[:2])-lo)/.05).astype(np.int64)
            ix=base[:,0][None,:]+offs[:,None];iy=base[:,1][None,:]+offs[:,None]
            vx=(ix>=0)&(ix<hn);vy=(iy>=0)&(iy<wn)
            idx=np.clip(ix,0,hn-1)[:,None,:]*wn+np.clip(iy,0,wn-1)[None,:,:]
            out[j]=(flat[idx]&vx[:,None,:]&vy[None,:,:]).sum(axis=2)/len(pts)
        return out,g,nh
    fine,gf,nf=score((0.,),.1)
    kept=_peaks(fine,gf,(0.,),fine[0,nf,nf])
    swept,gs,_=score(GATE_SCREEN_YAWS,.2)
    for c in _peaks(swept,gs,GATE_SCREEN_YAWS,fine[0,nf,nf]):
        if all(math.hypot(c[0]-k[0],c[1]-k[1])>=.3 or abs(c[2]-k[2])>=math.radians(2) for k in kept):kept.append(c)
    return [(pose[0]+x,pose[1]+y,pose[2]+a) for x,y,a in kept]


def _separated_beyond_tolerance(a,b):
    return (np.linalg.norm(np.asarray(a['xy'])-np.asarray(b['xy']))>GATE_SEPARATED_M or
            abs(math.atan2(math.sin(a['yaw']-b['yaw']),math.cos(a['yaw']-b['yaw'])))>GATE_SEPARATED_RAD)


def register_keyframe(target,current, *, cancelled=lambda:False, max_wall_s=1.5, seed_center=None, gates=False):
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
        if gates and _separated_beyond_tolerance(other,best):
            return dict(accepted=False,reason='ambiguous_acceptable_alternative',attempted=attempted,shifted_seed_grid=shifted)
    if gates:
        rejected=_bridge_gates(target,current,best,cancelled,begin,max_wall_s,attempted,shifted)
        if rejected is not None:return rejected
    return dict(accepted=True,reason='unverified_bounded_native_keyframe_registration',
                attempted=attempted,search_complete=True,search_global_uniqueness_verified=False,
                shifted_seed_grid=shifted,
                physical_accuracy_verified=False,rolling_scan_deskewed=False,
                elapsed_s=time.monotonic()-begin,**best)


def _bridge_gates(target,current,best,cancelled,begin,max_wall_s,attempted,shifted):
    """C1b+G reject-only tail: degeneracy margin, screened alternatives, final deadline. None keeps the result."""
    weak=_weak_normal(target,current,best)
    if weak<GATE_MIN_WEAK_NORMAL:
        return dict(accepted=False,reason='translation_weakly_constrained',weak_normal=weak,attempted=attempted,
                    shifted_seed_grid=shifted)
    if GATE_SCREEN:
        for sx,sy,sa in _screen_peaks(target,current,(float(best['xy'][0]),float(best['xy'][1]),float(best['yaw']))):
            if cancelled() or time.monotonic()-begin>=max_wall_s:
                return dict(accepted=False,reason='recovery_cancelled_or_work_deadline',attempted=attempted,
                            shifted_seed_grid=shifted)
            reach=math.hypot(sx,sy);bound=(IDENTITY_BOUND_M+reach,IDENTITY_BOUND_RAD+abs(sa))
            diagnostic=Counter();attempted+=1
            fit=fit_surfaces(target,current,sa,np.array((sx,sy)),*bound,diagnostic)
            if fit is None and any(diagnostic.get(d) for d in _DEGENERATE):
                fit=_fit_free(target,current,sa,np.array((sx,sy)),*bound)
            if fit is None:continue
            overlap,rms=_full_overlap(target,current,fit)
            if min(overlap)<GATE_LENIENT_OVERLAP or max(rms)>.08:continue
            if min(overlap)>=.70 and _separated_beyond_tolerance(fit,best):
                return dict(accepted=False,reason='ambiguous_screened_alternative',attempted=attempted,
                            shifted_seed_grid=shifted)
            separated=np.linalg.norm(fit['xy']-best['xy'])>.08 or abs(fit['yaw']-best['yaw'])>math.radians(1)
            comparable=fit['coverage']>=best['coverage']-.03 and fit['rmse_m']<=max(.015,best['rmse_m']*1.3)
            if separated and comparable:
                return dict(accepted=False,reason='ambiguous_screened_alias',attempted=attempted,shifted_seed_grid=shifted)
    if cancelled() or time.monotonic()-begin>=max_wall_s:
        return dict(accepted=False,reason='recovery_cancelled_or_work_deadline',attempted=attempted,shifted_seed_grid=shifted)
    return None


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
