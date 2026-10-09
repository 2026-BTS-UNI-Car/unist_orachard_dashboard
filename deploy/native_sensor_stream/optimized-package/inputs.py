"""Verify only local vendored modules; installable without private BASE parents."""
import hashlib,json
from pathlib import Path
HERE=Path(__file__).resolve().parent
VENDORED_INPUTS_SHA='5d6d66baeba6f033ca4699c15a5b91dd3cd9b7141f191c0ef41003c84be61a93'
OBSERVER_SHA='93cbe9221f7d0b3d06079c04e35c4633cfa454c2c80233cb893629907af97563'

def verify():
    manifest=HERE/'VENDORED_INPUTS.json'
    raw=manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=VENDORED_INPUTS_SHA:raise ValueError('vendored_manifest_bytes_changed')
    data=json.loads(raw)
    if data['schema']!='ubts.private.physical_shadow_vendored_inputs.v1':raise ValueError('vendored_manifest_schema_changed')
    for name,sha in data['files_sha256'].items():
        if hashlib.sha256((HERE/name).read_bytes()).hexdigest()!=sha:raise ValueError('vendored_file_changed:'+name)
    if data['SIM_profile_or_runtime_imports'] is not False or data['ROS_imports'] is not False:
        raise ValueError('physical_dependency_domain_changed')
    return {'local_vendored_count':len(data['files_sha256']),'vendored_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'deep_SIM_or_external_BASE_modules_needed':False}
verify()
