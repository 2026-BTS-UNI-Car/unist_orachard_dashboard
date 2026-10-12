"""Bounded display-only integer raster. No IO, clocks, localization or authority.

Endpoints use the oracle's scalar libm/floor path. Only grid enumeration and
within-batch duplicate removal use NumPy. Unknown/max returns emit no free ray.
"""
import math
import numpy as np

MAX_BATCH_VISITS=65536
MAX_BEAMS=4096
MAX_VISITS=4096000
MAX_CELLS=250000
_I32_MIN=-(1<<31)
_I32_MAX=(1<<31)-1

class RayCapacityReached(Exception): pass
class RayRasterCancelled(Exception): pass


def _cancel(cancelled):
    if cancelled is not None and cancelled(): raise RayRasterCancelled()


def _scalar_rays(frame,pose,resolution,max_cells,max_visits,cancelled):
    # Exact oracle fallback for coordinates beyond the packed signed32 domain.
    ox,oy=math.floor(pose[0]/resolution),math.floor(pose[1]/resolution)
    free=set();hits=set();seen=set();visits=0
    def observed(key):
        seen.add(key)
        if len(seen)>max_cells:raise RayCapacityReached()
        return key
    for i,d in enumerate(frame.ranges):
        _cancel(cancelled)
        if not math.isfinite(d) or not max(.05,frame.range_min)<=d<frame.range_max:continue
        a=pose[2]+frame.angle_min+i*frame.angle_increment
        ex,ey=math.floor((pose[0]+d*math.cos(a))/resolution),math.floor((pose[1]+d*math.sin(a))/resolution)
        dx,dy=abs(ex-ox),abs(ey-oy);major=max(dx,dy)
        visits+=major+1
        if visits>max_visits:raise ValueError('native_ray_work_budget')
        sx,sy=(1 if ex>=ox else -1),(1 if ey>=oy else -1)
        for n in range(major):
            if n%MAX_BATCH_VISITS==0:_cancel(cancelled)
            m=(2*n*min(dx,dy)+major)//(2*major)
            free.add(observed((ox+sx*(n if dx>=dy else m),oy+sy*(m if dx>=dy else n))))
        hits.add(observed((ex,ey)))
    _cancel(cancelled);free.difference_update(hits)
    return free,hits


def _keys(codes):
    result=set()
    for value in codes:
        x,y=value>>32,value&0xffffffff
        if x>_I32_MAX:x-=1<<32
        if y>_I32_MAX:y-=1<<32
        result.add((x,y))
    return result


def _decode(codes):
    x=(codes>>np.uint64(32)).astype(np.uint32).view(np.int32).astype(np.int64)
    y=(codes&np.uint64(0xffffffff)).astype(np.uint32).view(np.int32).astype(np.int64)
    return np.column_stack([x,y])


def raster_rays(frame,pose,*,resolution_m,max_cells,max_ray_visits,cancelled=None,
                batch_visits=MAX_BATCH_VISITS):
    """Return exact (free,hits) grid sets; see raster_ray_cells."""
    free,hits=raster_ray_cells(frame,pose,resolution_m=resolution_m,max_cells=max_cells,
        max_ray_visits=max_ray_visits,cancelled=cancelled,batch_visits=batch_visits)
    return set(map(tuple,free.tolist())),set(map(tuple,hits.tolist()))


def raster_ray_cells(frame,pose,*,resolution_m,max_cells,max_ray_visits,cancelled=None,
                     batch_visits=MAX_BATCH_VISITS):
    """Return exact (free,hits) grid sets for every valid original beam.

    max_ray_visits counts major+1 per valid beam exactly as the Python oracle.
    A capacity/work failure returns no partial result. Cancellation is cooperative
    between bounded batches. Up to batch_visits temporary cells, not lifetime
    visits, are materialized. No geometry acceptance or native proof is invented.
    """
    if type(batch_visits) is not int or not 1<=batch_visits<=MAX_BATCH_VISITS:
        raise ValueError('bounded_ray_batch_required')
    if type(max_cells) is not int or not 1<=max_cells<=MAX_CELLS or type(max_ray_visits) is not int or not 1<=max_ray_visits<=MAX_VISITS:
        raise ValueError('bounded_map_work_required')
    if type(resolution_m) not in (int,float) or not math.isfinite(resolution_m) or not .02<=resolution_m<=1:
        raise ValueError('bounded_resolution_required')
    if cancelled is not None and not callable(cancelled):raise ValueError('ray_cancellation_callable_required')
    if type(frame.ranges) is not tuple or len(frame.ranges)>MAX_BEAMS:
        raise ValueError('full_bounded_original_ranges_required')
    _cancel(cancelled)
    r=resolution_m;ox,oy=math.floor(pose[0]/r),math.floor(pose[1]/r)
    # Any valid endpoint is within range_max/r of its origin. Outside this safe
    # packing domain use the unchanged Python arithmetic instead of wrapping.
    pad=math.ceil(frame.range_max/r)+1
    if min(ox,oy)-pad<_I32_MIN or max(ox,oy)+pad>_I32_MAX:
        free,hits=_scalar_rays(frame,pose,r,max_cells,max_ray_visits,cancelled)
        return (np.array(sorted(free),dtype=np.int64).reshape(-1,2),
                np.array(sorted(hits),dtype=np.int64).reshape(-1,2))
    empty=np.zeros(0,dtype=np.uint64)
    state=dict(seen=empty,hits=empty);endpoints=set();visits=0;batch=[];batch_size=0
    def render(rows):
        _cancel(cancelled)
        if not rows:return
        # Rows are dx,dy,sx,sy,start,end. Splitting long rays preserves their
        # original n/major rounding rather than restarting at a batch boundary.
        data=np.asarray(rows,dtype=np.int64)
        lengths=data[:,5]-data[:,4]
        ids=np.repeat(np.arange(len(rows),dtype=np.int64),lengths)
        starts=np.cumsum(lengths)-lengths
        n=np.arange(int(lengths.sum()),dtype=np.int64)-np.repeat(starts,lengths)+data[ids,4]
        dx,dy=data[ids,0],data[ids,1]
        major=np.maximum(dx,dy);minor=np.minimum(dx,dy)
        denominator=np.where(major==0,1,2*major)
        m=(2*n*minor+major)//denominator
        x=ox+data[ids,2]*np.where(dx>=dy,n,m)
        y=oy+data[ids,3]*np.where(dx>=dy,m,n)
        codes=(x.astype(np.uint64)<<np.uint64(32))|(y.astype(np.uint64)&np.uint64(0xffffffff))
        # Sorted unique code arrays replace the Python sets; the cumulative
        # capacity test still runs after every batch, exactly as before.
        seen=np.union1d(state['seen'],codes)
        if len(seen)>max_cells:raise RayCapacityReached()
        state['seen']=seen
        hit=n==major
        state['hits']=np.union1d(state['hits'],codes[hit])
        _cancel(cancelled)
    for i,d in enumerate(frame.ranges):
        if not math.isfinite(d) or not max(.05,frame.range_min)<=d<frame.range_max:continue
        a=pose[2]+frame.angle_min+i*frame.angle_increment
        ex,ey=math.floor((pose[0]+d*math.cos(a))/r),math.floor((pose[1]+d*math.sin(a))/r)
        dx,dy=abs(ex-ox),abs(ey-oy);major=max(dx,dy)
        if visits+major+1>max_ray_visits:
            render(batch)
            raise ValueError('native_ray_work_budget')
        visits+=major+1
        # Every valid beam still pays its original full visit admission above.
        # Identical endpoints from this origin have exactly identical integer
        # paths; their free/hit union is already scheduled or complete.
        endpoint=(ex,ey)
        if endpoint in endpoints:continue
        endpoints.add(endpoint)
        sx,sy=(1 if ex>=ox else -1),(1 if ey>=oy else -1)
        start=0
        while start<major+1:
            end=min(major+1,start+batch_visits-batch_size)
            batch.append((dx,dy,sx,sy,start,end));batch_size+=end-start;start=end
            if batch_size==batch_visits:
                render(batch);batch=[];batch_size=0
    render(batch);_cancel(cancelled)
    hits=state['hits']
    answer=(_decode(np.setdiff1d(state['seen'],hits,assume_unique=True)),_decode(hits));_cancel(cancelled)
    return answer
