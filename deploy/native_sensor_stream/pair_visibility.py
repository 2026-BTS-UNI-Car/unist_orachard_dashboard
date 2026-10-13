"""Original-polar denominator diagnostics. No map mutation or authority."""
from types import SimpleNamespace
import math
import numpy as np
from keyframe_registration import ScanPolar,_map_metrics,_map_ok,_full_overlap,to_child


def paired_visibility(target_wire,target,current_wire,current,pose):
    # Reuse existing C6 visibility calculation unchanged, with ORIGINAL K dense
    # points rather than a pooled/voxel map. Matching returns are included even
    # if the polar lookup marks them unobserved, as in the original C6 contract.
    t=SimpleNamespace(surf=target,k_dense=target.dense,
                      polars=[((0.,0.,0.),ScanPolar(target_wire))])
    h=ScanPolar(current_wire)
    m=_map_metrics(t,current,h,pose)
    kh=to_child(target.dense,pose)
    d=current.dense_tree.query(kh,workers=1)[0]
    back_seen=h.observable(kh)|(d<.15)
    m['backward_observed_share']=float(np.mean(back_seen))
    m['C6_fixed_visibility_tests_pass']=bool(_map_ok(m))
    m['both_observed_shares_at_least_half']=bool(m['obs']>=.5 and m['backward_observed_share']>=.5)
    full,rms=_full_overlap(target,current,dict(xy=np.asarray(pose[:2]),yaw=pose[2]))
    m['unchanged_full_return_overlap']=full
    m['unchanged_full_return_RMS']=rms
    m['full_return_70_percent_pass']=bool(min(full)>=.7 and max(rms)<=.08)
    m.update(visibility_is_not_global_uniqueness=True,qualification=False,
             runtime_authority=False,motor_command_sent=False)
    return m


def original_gyro_increment(rows,start_ns,end_ns,mode='raw_Z'):
    # Time offsets only; original integer source stamps are never rewritten.
    ordered=sorted(rows,key=lambda r:r['header_ns'])
    times=np.array([(r['header_ns']-start_ns)/1e9 for r in ordered])
    end=(end_ns-start_ns)/1e9
    assert times[0]<=0 and times[-1]>=end,'missing_original_IMU_bracket'
    rates=[]
    for row in ordered:
        w=np.asarray(row['angular_rate_rad_s'],float)
        if mode=='raw_Z':rates.append(w[2])
        else:
            q=np.asarray(row['quaternion'],float);q=q/np.linalg.norm(q);x,y,z,a=q
            earth_up_in_sensor=np.array([2*(x*z-a*y),2*(y*z+a*x),1-2*(x*x+y*y)])
            rates.append(float(np.dot(earth_up_in_sensor,w)))
    rates=np.asarray(rates)
    keep=(times>0)&(times<end)
    knots=np.r_[0.,times[keep],end]
    values=np.r_[np.interp(0.,times,rates),rates[keep],np.interp(end,times,rates)]
    return float(np.sum((values[:-1]+values[1:])*.5*np.diff(knots)))
