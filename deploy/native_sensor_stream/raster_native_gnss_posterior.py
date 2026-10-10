"""NEW derived complete raster from ALL retained original scans and GNSS graph.

No marker-only correction, input mutation or sensor-time renewal. Different
continuity chains require their own posterior first; partial history is refused.
This is unqualified historical weak mapping, never physical/control input.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace


def require(value,reason):
    if not value:raise ValueError(reason)


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def rerasterize(history,proposal,*,decode_scan,rays,resolution_m=.1,max_cells=250000):
    require(history.get('schema')=='ubts.ROOT.reviewed.native_lidar_imu.pose_history.v2' and
        history.get('runtime_authority') is False and history.get('contains_original_scans') is True and
        history.get('complete_retained_history') is True,'complete_original_scan_history_required')
    rows=history['rows'];require(rows and len(rows)<=4096,'bounded_original_scan_history_required')
    require(proposal.get('accepted') is True and proposal.get('runtime_authority') is False and
        proposal.get('requires_complete_retained_scan_rerasterization') is True and
        proposal.get('pose_versions')==[r['version'] for r in rows] and
        proposal.get('source_ns')==[r['source_ns'] for r in rows] and
        all(r['chain']==proposal['chain'] and r['context']==rows[0]['context'] for r in rows),
        'posterior_must_cover_every_original_retained_scan')
    xy=proposal.get('corrected_ENU_xy');angle=proposal.get('alignment_yaw_rad')
    require(type(xy) is list and len(xy)==len(rows) and type(angle) in (int,float) and math.isfinite(angle),
        'explicit_full_posterior_position_and_rigid_yaw_required')
    cells={};scan_ids=[]
    for row,position in zip(rows,xy):
        require(type(position) is list and len(position)==2 and all(type(x) in (int,float) and math.isfinite(x) for x in position),
            'finite_posterior_position_required')
        raw=bytes.fromhex(row['original_scan_CDR_hex'])
        require(hashlib.sha256(raw).hexdigest()==row['scan_sha256'],'retained_original_scan_SHA_changed')
        wire=decode_scan(raw)
        require(wire.header.stamp_ns==row['source_ns'] and wire.header.frame_id==row['original_frame'],
            'retained_original_scan_Header_changed')
        a,_b,inc,_dt,_cycle,mn,mx=map(float,wire.metadata)
        frame=SimpleNamespace(ranges=tuple(map(float,wire.ranges)),angle_min=a,angle_increment=inc,range_min=mn,range_max=mx)
        pose=(float(position[0]),float(position[1]),row['yaw_rad']+angle)
        free,hits=rays(frame,pose)
        for key in free:
            if key not in cells or cells[key][0]==0:cells[key]=(0,row['source_ns'],row['callback_RAW_ns'])
        for key in hits:cells[key]=(100,row['source_ns'],row['callback_RAW_ns'])
        require(len(cells)<=max_cells,'derived_raster_cell_capacity')
        scan_ids.append(row['scan_sha256'])
    body=dict(schema='ubts.derived.GNSS_weak_complete_map.v1',resolution_m=resolution_m,
        cells=[(x,y,*v) for (x,y),v in sorted(cells.items())],coordinate_frame='fixed_ENU_weak_GNSS',
        original_scans_rerasterized=len(rows),original_scan_SHA_sequence=scan_ids,
        original_history_sha256=hashlib.sha256(canonical(history)).hexdigest(),
        posterior_sha256=hashlib.sha256(canonical(proposal)).hexdigest(),unknown_cells_remain_unknown=True,
        original_source_and_RAW_times_unchanged=True,original_local_map_unchanged=True,
        derived_generation_requires_explicit_presentation=True,nonrigid_yaw_model_qualified=False,
        native_GNSS_pipeline_qualified=False,fused_occupancy_qualified=False,
        runtime_authority=False,motor_command_sent=False,autonomy_output_enabled=False)
    body['derived_generation_sha256']=hashlib.sha256(canonical(body)).hexdigest()
    return body


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--rerasterize',action='store_true')
    for x in ('history','history-sha256','posterior','posterior-sha256','package','package-sha256','map-manifest-sha256','output'):p.add_argument('--'+x)
    a=p.parse_args(argv)
    if not a.rerasterize:
        print(json.dumps(dict(default_inert=True,files_written=False,motor_command_sent=False)));return 0
    inputs=[]
    for name in ('history','posterior'):
        path=Path(getattr(a,name)).resolve(strict=True);raw=path.read_bytes()
        require(len(raw)<=40*1024*1024 and hashlib.sha256(raw).hexdigest()==getattr(a,name+'_sha256'),'SHA_bound_derived_inputs_required')
        from native_gnss_fusion import strict_json
        inputs.append(strict_json(raw))
    import stream_runner
    stream_runner.load_physical_package(a.package,a.package_sha256)
    from common.original_cdr import decode,LASER_TYPE
    stream_runner.load_native_map_sources(Path(__file__).parent,a.map_manifest_sha256)
    from native_map_owner import NativeAcceptedMapOwner
    oracle=NativeAcceptedMapOwner(historical_display_only=True)
    out=rerasterize(inputs[0],inputs[1].get('proposal',inputs[1]),
        decode_scan=lambda raw:decode(raw,LASER_TYPE),rays=oracle._rays)
    for name in ('history','posterior'):
        require(hashlib.sha256(Path(getattr(a,name)).read_bytes()).hexdigest()==getattr(a,name+'_sha256'),'original_derived_inputs_changed')
    with Path(a.output).open('xb') as f:f.write(canonical(out))
    print(json.dumps(dict(generation_sha256=out['derived_generation_sha256'],cells=len(out['cells']),
        runtime_authority=False,motor_command_sent=False)))
    return 0


if __name__=='__main__':raise SystemExit(main())
