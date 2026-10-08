"""Portable byte verification only. Defaultplan does not read sensor/runtime IO."""
import argparse
import hashlib
import json
from pathlib import Path
import stat

HERE=Path(__file__).resolve().parent
SOURCE_SHA='caec4f76da7356fb6eb03887949b5be56017b81d3603e4ef240fd5d11f53f842'
ADAPTER_SHA='176fccd67c8e524455fb16953add422458ddaff74dd7a41a5f6cc2bfce5c912a'


def stable_sha(path,cap=2*1024*1024):
    before=path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size>cap:raise ValueError('bounded_regular_source_required')
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(65536),b''):h.update(b)
    after=path.lstat()
    fields=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns)
    if fields(before)!=fields(after):raise ValueError('source_changed_during_verification')
    return h.hexdigest()


def verify(root=HERE):
    root=Path(root).resolve(strict=True)
    if stable_sha(root/'SOURCE.json')!=SOURCE_SHA:raise ValueError('reviewed_source_manifest_changed')
    m=json.loads((root/'SOURCE.json').read_bytes())
    if m['schema']!='ubts.physical_sensor_shadow.source_package.v1' or m['build_file_count']!=19 or len(m['build_files_sha256'])!=19:
        raise ValueError('exact_source19_required')
    if m['runtime_authority'] is not False or m['motor_transport'] is not False:raise ValueError('observer_only_required')
    for name,sha in m['build_files_sha256'].items():
        rel=Path(name)
        if rel.is_absolute() or '..' in rel.parts or any((root/Path(*rel.parts[:i])).is_symlink() for i in range(1,len(rel.parts)+1)):
            raise ValueError('local_regular_source_only')
        if stable_sha(root/rel)!=sha:raise ValueError('source_bytes_changed:'+name)
    if stable_sha(root/'Dockerfile.adapter')!=ADAPTER_SHA:raise ValueError('reviewed_owned_base_adapter_changed')
    return dict(schema='ubts.physical_sensor_shadow.source_verification.v1',ok=True,
        source_manifest_sha256=SOURCE_SHA,adapter_sha256=ADAPTER_SHA,build_files_verified=19,
        exact_clone_bytes_required=True,ROS_initialized=False,source_only=True,
        installed_live_factory_qualified=False,runtime_authority=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--verify-source',action='store_true')
    args=parser.parse_args()
    if not args.verify_source:
        print(json.dumps(dict(mode='plan',ROS_initialized=False,image_built=False,runtime_changed=False)))
    else:print(json.dumps(verify(),sort_keys=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
