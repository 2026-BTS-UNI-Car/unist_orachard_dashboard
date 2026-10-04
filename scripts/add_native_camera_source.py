"""Add a separate recorded camera source; keep its real FPS and clock offset."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
from scripts.hls_bandwidth import hls_bandwidth_metrics


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def add(existing,original,output):
    existing=Path(existing);original=Path(original);output=Path(output)
    primary=json.loads((existing/'adaptive_video.json').read_text())
    native=json.loads((original/'adaptive_video.json').read_text())
    if (primary['archive_id']!=native['archive_id'] or not primary.get('complete') or not native.get('complete')
            or native['source_height']<1080):raise ValueError('invalid_original_camera_source')
    for name,sha in primary['source_sha256'].items():
        if name in native['source_sha256'] and sha!=native['source_sha256'][name]:raise ValueError('different_source_archive')
    if output.exists():raise ValueError('new_generated_directory_required')
    output.mkdir(parents=True)
    for folder,manifest,prefix in ((existing,primary,''),(original,native,'1080-original/')):
        for name,asset in manifest['assets'].items():
            file=folder/name
            if digest(file)!=asset['sha256']:raise ValueError('generated_asset_hash_mismatch')
            destination=output/(prefix+name);destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(file,destination)
            if prefix:primary['assets'][prefix+name]=asset
    source={k:native[k] for k in ('source_topic','source_frames','source_width','source_height','fps','duration_s','timeline_offset_s','frame_timing','variants')}
    source.update(id='native_1080',label='1080p 원본',master='1080-original/'+native['master'],fallback='1080-original/'+native['fallback'])
    primary['alternative_sources']=[source]
    primary['generated_upgrade_source_manifest_sha256']=digest(existing/'adaptive_video.json')
    primary['generated_upgrade_manifest_ancestry_sha256']=list(primary.get('generated_upgrade_manifest_ancestry_sha256',[]))
    primary['generated_upgrade_manifest_ancestry_sha256'].append(digest(existing/'adaptive_video.json'))
    (output/'adaptive_video.json').write_text(json.dumps(primary,indent=2))
    return dict(archive=primary['archive_id'],original_fps=native['fps'],original_frames=native['source_frames'],original_height=native['source_height'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('existing',type=Path);parser.add_argument('original',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(add(args.existing,args.original,args.output)))
