"""Default OFF: single manifest field in a fresh derived image, never live."""
import argparse,hashlib,json,os
from pathlib import Path
K=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def rebind(root,pins):
    root=Path(root);manifest=root/'BUILD_PROVENANCE.json';before=manifest.read_bytes();m=json.loads(before)
    original=m['files_sha256'].copy()
    assert m['schema']=='ubts.actual.native_stream.build.v1'
    assert all(m[k] is False for k in ('runtime_authority','motor_command_sent','autonomy_output_enabled','mission_admission'))
    row=pins['deploy/native_sensor_stream/stream_rows.py']
    assert original['stream_rows.py']==row['source_SHA256'],'current_deployed_C0_parent_required'
    assert set(original)=={'stream_runner.py','stream_rows.py','dashboard_server/native_owner_lifecycle.py',
        'observer-src/actual_cdr_stream.cpp','observer-src/CMakeLists.txt','OBSERVER_CONTRACT.json'}
    for name,value in original.items():
        assert sha(root/name)==(row['overlay_SHA256'] if name=='stream_rows.py' else value),name
    assert sha(root/'bin/actual_cdr_stream')==m['observer_binary_sha256']
    assert sha(root/'OBSERVER_CONTRACT.json')==m['observer_contract_sha256']
    assert sha('/opt/ubts-shadow/package/HANDOFF.json')==m['physical_package_manifest_sha256']
    native=json.loads((root/'NATIVE_MAP_SOURCE.json').read_bytes())
    assert sha(root/'NATIVE_MAP_SOURCE.json')==pins['deploy/native_sensor_stream/NATIVE_MAP_SOURCE.json']['overlay_SHA256']
    for name,value in native['files_sha256'].items():assert sha(root/name)==value,name
    for name in ('stream_rows_map.py','native_observation_service.py'):
        assert sha(root/name)==pins['deploy/native_sensor_stream/'+name]['overlay_SHA256'],name
    m['files_sha256']['stream_rows.py']=row['overlay_SHA256']
    assert {k for k in original if original[k]!=m['files_sha256'][k]}=={'stream_rows.py'}
    after=(json.dumps(m,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
    with (root/'BUILD_PROVENANCE.before_postplan_comparison.json').open('xb')as f:f.write(before)
    temp=root/'BUILD_PROVENANCE.postplan_comparison.tmp'
    with temp.open('xb')as f:f.write(after)
    os.replace(temp,manifest)
    return dict(schema='ubts.C0_postplan_comparison_provenance.v1',old_SHA256=hashlib.sha256(before).hexdigest(),
        new_SHA256=sha(manifest),sole_changed_field='files_sha256.stream_rows.py',original_CDR_binary_and_physical_package_unchanged=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root-new-image-only',action='store_true');p.add_argument('--stream-root');p.add_argument('--source-pins');a=p.parse_args()
    if not a.root_new_image_only:
        print('{"default_OFF":true,"runtime_execution":false}');raise SystemExit(0)
    assert a.stream_root and a.source_pins,'explicit_new_image_inputs_required'
    print(json.dumps(rebind(a.stream_root,json.loads(Path(a.source_pins).read_bytes())),sort_keys=True))
