"""Explicit read-only original rosbag GNSS replay into retained native poses.

No ROS init/publication/subscriber, archive change, motor or live clock sampling.
The SHA-bound pose file is a trusted reviewed input, not a self-certificate.
The resulting weak global proposal is NOT installed as a local raster.
"""
import argparse
import hashlib
import json
from pathlib import Path
import native_gnss_fusion as n
import gnss_constraints as math_backend


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def build(rows, poses, ctx, capacity=100000):
    """Also usable with an injected original rosbag iterator in pure tests.

    rows yields (topic, original_serialized_bytes, original_bag_log_ns).
    Source SHA is verified by main before/after; a caller must bind its iterator.
    """
    ctx = n.context(ctx)
    seals = {id(p): repr(p) for p in poses}
    require_pose = lambda p: seals.get(id(p)) == repr(p)
    bridge = n.WeakGNSSMapFactors(math_backend, enabled=True,
        validate_original=lambda p: p.context == ctx, validate_pose=require_pose, capacity=capacity)
    bridge.start(ctx, explicit=True)
    for p in poses: bridge.add_pose(p)
    collector = n.OriginalGPSPairs(ctx)
    result_counts = {}
    for topic, raw, log_ns in rows:
        if topic not in ('/gps/data', '/gps/fix'): continue
        pair = collector.receive(topic, raw, log_ns=log_ns)
        if pair is not None:
            result = bridge.add_original(pair)
            reason = result['reason'];result_counts[reason] = result_counts.get(reason, 0) + 1
    proposal = bridge.global_proposal()
    return dict(schema='ubts.private.original_GNSS_native_map_weak_replay.v1',
        collector=dict(collector.counts), reasons=result_counts, factor_status=bridge.status(),
        proposal=proposal, original_bag_and_pose_files_changed=False,
        native_pose_input_is_ROOT_reviewed_not_self_certified=True,
        native_GNSS_pipeline_qualified=False, physical_accuracy_validated=False,
        runtime_authority=False, motor_command_sent=False, autonomy_output_enabled=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--read-only-replay', action='store_true')
    p.add_argument('--bag');p.add_argument('--bag-sha256')
    p.add_argument('--poses');p.add_argument('--poses-sha256')
    p.add_argument('--output');p.add_argument('--retained-original-GPS',action='store_true')
    a = p.parse_args()
    if not a.read_only_replay:
        print(json.dumps(dict(default_inert=True, ROS_initialized=False, files_written=False,
                             motor_command_sent=False)));return
    bag = None if a.retained_original_GPS else Path(a.bag).resolve(strict=True)
    pose_file = Path(a.poses).resolve(strict=True)
    if bag is not None:
        n.require(bag.is_file() and bag.suffix == '.mcap' and sha(bag) == a.bag_sha256,
                  'exact_original_MCAP_SHA_required')
    n.require(pose_file.is_file() and pose_file.stat().st_size <= 64 * 1024 * 1024
              and sha(pose_file) == a.poses_sha256, 'exact_reviewed_pose_history_SHA_required')
    d = n.strict_json(pose_file.read_bytes())
    if d.get('schema')=='ubts.ROOT.reviewed.native_lidar_imu.pose_history.v2':
        n.require(d.get('runtime_authority') is False and d.get('complete_retained_history') is True,
                  'complete_original_pose_history_required')
        rows=d['rows'];n.require(rows and len(rows)<=4096,'bounded_committed_pose_history_required')
        keys=('source_session','generation','source_clock_domain','receipt_clock_domain','map_epoch','map_frame')
        ctx=tuple(rows[0]['context'][k] for k in keys)
        n.require(all(tuple(r['context'][k] for k in keys)==ctx for r in rows),
                  'one_exact_native_source_context_required')
    else:
        n.require(d.get('schema')=='ubts.ROOT.reviewed.native_lidar_imu.pose_history.v1'
                  and d.get('runtime_authority') is False,'reviewed_original_pose_history_required')
        ctx=tuple(d['context']);rows=d['poses']
    poses=[n.PoseWitness(ctx,r['chain'],r['version'],r['source_ns'],tuple(r['xy']),r['scan_sha256']) for r in rows]
    # Same original-reader primitive as tools/field_data/extract_rosbag.py;
    # converter empty strings retain original serialization. No deserialize/
    # serialize roundtrip occurs on this GNSS ingress path.
    if a.retained_original_GPS:
        def originals():
            for r in d.get('original_GNSS_pairs',[]):
                n.require(tuple(r['context'])==ctx and r.get('runtime_authority') is False,'retained_original_GPS_context_required')
                for topic,field,sha_field in (('/gps/data','data_CDR_hex','data_CDR_sha256'),('/gps/fix','fix_CDR_hex','fix_CDR_sha256')):
                    raw=bytes.fromhex(r[field]);n.require(hashlib.sha256(raw).hexdigest()==r[sha_field],'retained_original_GPS_SHA_required')
                    yield topic,raw,None  # No rosbag log or callback clock is invented.
    else:
        import rosbag2_py
        reader=rosbag2_py.SequentialReader()
        reader.open(rosbag2_py.StorageOptions(uri=str(bag),storage_id='mcap'),rosbag2_py.ConverterOptions('',''))
        reader.set_filter(rosbag2_py.StorageFilter(topics=['/gps/data','/gps/fix']))
        def originals():
            while reader.has_next():
                topic,raw,log_ns=reader.read_next();yield topic,bytes(raw),log_ns
    result = build(originals(), poses, ctx)
    n.require((bag is None or sha(bag) == a.bag_sha256) and sha(pose_file) == a.poses_sha256,
              'original_inputs_changed_during_replay')
    result['original_MCAP_sha256'] = a.bag_sha256
    result['reviewed_pose_history_sha256'] = a.poses_sha256
    output = Path(a.output).resolve()
    n.require(not output.exists() and output not in (bag, pose_file), 'new_private_output_required')
    with output.open('xb') as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False).encode('utf8'))
    print(json.dumps(dict(output=str(output), factors=result['factor_status']['retained_GNSS_factors'],
                         proposal_accepted=bool((result['proposal'] or {}).get('accepted')))))


if __name__ == '__main__': main()
