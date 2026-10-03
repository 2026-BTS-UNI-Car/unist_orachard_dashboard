#!/usr/bin/env python3
"""Validate and atomically install a generated sidecar; preserve raw archives."""
import argparse,hashlib,json,re,shutil,uuid,ctypes,sys
from pathlib import Path


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()


def exchange_generated_directories(first,second):
    if sys.platform!='linux':raise ValueError('atomic_generated_upgrade_requires_linux')
    libc=ctypes.CDLL(None,use_errno=True)
    exchange=getattr(libc,'renameat2',None)
    if exchange is None:raise ValueError('atomic_exchange_unavailable')
    if exchange(-100,os_bytes(first),-100,os_bytes(second),2)!=0:
        raise OSError(ctypes.get_errno(),'atomic_generated_exchange_failed')

def os_bytes(path):return str(path).encode(sys.getfilesystemencoding())

def install(package,root,replace_manifest_sha256=None):
    package=package.resolve(strict=True);root=root.resolve(strict=True)
    m=json.loads((package/'adaptive_video.json').read_text());name=m['archive_id']
    if not m.get('complete') or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',name):raise ValueError('invalid_adaptive_package')
    archive=(root/name).resolve(strict=True)
    if archive.parent!=root or (root/name).is_symlink():raise ValueError('invalid_archive_path')
    protected={n:sha(archive/n) for n in ('metadata.json','gps.jsonl','motor.jsonl') if (archive/n).is_file()}
    if any(m['source_sha256'].get(n)!=h for n,h in protected.items()):raise ValueError('source_sidecar_changed')
    for n,sig in m['source_signatures'].items():
        p=(archive/n).resolve(strict=True)
        if archive not in p.parents or [p.stat().st_size,p.stat().st_mtime_ns]!=sig:raise ValueError('source_bag_changed')
    target=archive/'adaptive_video'
    if target.is_symlink():raise ValueError('adaptive_sidecar_symlink')
    if target.exists():
        old=json.loads((target/'adaptive_video.json').read_text())
        if old==m and all(sha(target/n)==v['sha256'] for n,v in m['assets'].items()):return dict(archive_id=name,state='already_installed')
        # Resuming an older batch must preserve a verified newer rendition.
        ancestry=old.get('generated_upgrade_manifest_ancestry_sha256',[])
        if not isinstance(ancestry,list) or len(ancestry)>16 or any(not re.fullmatch(r'[0-9a-f]{64}',str(v)) for v in ancestry):
            raise ValueError('invalid_generated_ancestry')
        if sha(package/'adaptive_video.json') in ancestry+[old.get('generated_upgrade_source_manifest_sha256')] and all(
            n==m.get('master') or old.get('assets',{}).get(n)==v for n,v in m['assets'].items()) and all(
            sha(target/n)==v['sha256'] for n,v in old['assets'].items()):
            return dict(archive_id=name,state='already_installed_upgraded')
        if not re.fullmatch(r'[0-9a-f]{64}',str(replace_manifest_sha256 or '')) or sha(target/'adaptive_video.json')!=replace_manifest_sha256:
            raise ValueError('existing_adaptive_sidecar_requires_review')
        if m.get('generated_upgrade_source_manifest_sha256')!=replace_manifest_sha256:raise ValueError('generated_upgrade_baseline_mismatch')
        if any(sha(target/n)!=v['sha256'] for n,v in old['assets'].items()):raise ValueError('generated_baseline_asset_changed')
        backup=archive/('.adaptive_video-backup-'+replace_manifest_sha256[:16])
        if backup.exists():raise ValueError('generated_backup_already_exists')
    staging=archive/('.adaptive_video-'+uuid.uuid4().hex+'.partial');staging.mkdir()
    for n,v in m['assets'].items():
        if not re.fullmatch(r'(?:[0-9]{2,4}/)?[A-Za-z0-9_-]+\.(?:m3u8|m4s|mp4)',n):raise ValueError('invalid_asset_name')
        p=(package/n).resolve(strict=True)
        if package not in p.parents or p.stat().st_size!=v['bytes'] or sha(p)!=v['sha256']:raise ValueError('invalid_asset_hash')
        dest=staging/n;dest.parent.mkdir(exist_ok=True);shutil.copyfile(p,dest)
    shutil.copyfile(package/'adaptive_video.json',staging/'adaptive_video.json')
    if any(sha(archive/n)!=h for n,h in protected.items()):raise ValueError('raw_sidecar_drift_during_install')
    if target.exists():
        if sha(target/'adaptive_video.json')!=replace_manifest_sha256:raise ValueError('generated_baseline_drift_during_install')
        exchange_generated_directories(staging,target);staging.rename(backup)
        state='upgraded'
    else:staging.rename(target);state='installed'
    return dict(archive_id=name,state=state,source_frames=m['source_frames'],assets=len(m['assets']),raw_sidecars_preserved=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('package',type=Path);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--replace-generated-sha256')
    a=p.parse_args();print(json.dumps(install(a.package,a.root,a.replace_generated_sha256)),flush=True)
