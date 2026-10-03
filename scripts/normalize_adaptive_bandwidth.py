"""Correct measured VOD metadata; never re-encode or modify source assets."""
import argparse,hashlib,json,os,re,shutil
from pathlib import Path
try:
    from scripts.hls_bandwidth import hls_bandwidth_metrics
except ModuleNotFoundError:
    from hls_bandwidth import hls_bandwidth_metrics

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()
def normalize(package,output):
    package=Path(package).resolve(strict=True);output=Path(output).resolve();path=package/'adaptive_video.json'
    m=json.loads(path.read_text(encoding='utf-8'))
    if m.get('schema')!='orchard.adaptive_video.v1' or m.get('complete') is not True:raise ValueError('completed_package_required')
    if m.get('master')!='master.m3u8' or m['master'] not in m['assets'] or m.get('fallback') not in m['assets']:raise ValueError('invalid_package_index')
    if output.exists():raise ValueError('new_output_directory_required')
    for n,v in m['assets'].items():
        if not re.fullmatch(r'(?:[0-9]{2,4}/)?[A-Za-z0-9_-]+\.(?:m3u8|m4s|mp4)',n):raise ValueError('invalid_asset_name')
        p=package/n
        if p.is_symlink() or package not in p.resolve(strict=True).parents or p.stat().st_size!=v['bytes'] or sha(p)!=v['sha256']:raise ValueError('source_asset_changed')
    variants=[]
    for v in m['variants']:
        playlist=v['playlist']
        if not re.fullmatch(r'[0-9]{2,4}/index\.m3u8',playlist) or playlist not in m['assets']:raise ValueError('invalid_variant_playlist')
        variants.append({**v,**hls_bandwidth_metrics(package/playlist.split('/')[0])})
    output.mkdir(parents=True)
    for n in m['assets']:
        p=output/n;p.parent.mkdir(parents=True,exist_ok=True)
        if n==m['master']:shutil.copyfile(package/n,p)
        else:
            try:os.link(package/n,p)
            except OSError:shutil.copyfile(package/n,p)
    master=['#EXTM3U','#EXT-X-VERSION:7','#EXT-X-INDEPENDENT-SEGMENTS']
    for v in sorted(variants,key=lambda v:v['height']):
        master.extend([f"#EXT-X-STREAM-INF:BANDWIDTH={v['bandwidth']},AVERAGE-BANDWIDTH={v['average_bandwidth']},RESOLUTION={v['width']}x{v['height']}",v['playlist']])
    (output/m['master']).write_text('\n'.join(master)+'\n',encoding='utf-8')
    m['variants']=variants;m['assets'][m['master']]=dict(bytes=(output/m['master']).stat().st_size,sha256=sha(output/m['master']))
    previous=sha(path)
    ancestry=m.get('generated_upgrade_manifest_ancestry_sha256',[])
    if not isinstance(ancestry,list) or len(ancestry)>14 or any(not re.fullmatch(r'[0-9a-f]{64}',str(v)) for v in ancestry):raise ValueError('invalid_generated_ancestry')
    parent=m.get('generated_upgrade_source_manifest_sha256')
    if parent is not None:
        if not re.fullmatch(r'[0-9a-f]{64}',str(parent)):raise ValueError('invalid_generated_parent')
        ancestry=list(dict.fromkeys(ancestry+[parent]))
    m['generated_upgrade_manifest_ancestry_sha256']=list(dict.fromkeys(ancestry+[previous]))
    m['generated_upgrade_source_manifest_sha256']=previous
    m['bandwidth_correction']=dict(bandwidth_source='RFC8216_measured_VOD_segment_windows',media_bytes_unchanged=True)
    (output/'adaptive_video.json').write_text(json.dumps(m,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return m
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('package',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    m=normalize(a.package,a.output);print(json.dumps(dict(archive_id=m['archive_id'],variants=m['variants'],media_bytes_unchanged=True)))
