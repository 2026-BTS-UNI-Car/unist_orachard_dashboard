#!/usr/bin/env python3
"""Extend a verified generated VOD package; native/high-quality assets stay byte-identical."""
import argparse,hashlib,json,os,re,shutil,subprocess
from pathlib import Path

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()

def extend(package,output,ffmpeg='ffmpeg'):
    package=package.resolve(strict=True);output=output.resolve()
    manifest=package/'adaptive_video.json';m=json.loads(manifest.read_text(encoding='utf-8'))
    if m.get('schema')!='orchard.adaptive_video.v1' or not m.get('complete') or m.get('source_height',0)<240:
        raise ValueError('verified_native_package_required')
    if any(v['height']==240 for v in m['variants']):raise ValueError('low_variant_already_present')
    if output.exists():raise ValueError('new_output_directory_required')
    for name,row in m['assets'].items():
        if not re.fullmatch(r'(?:[0-9]{2,4}/)?[A-Za-z0-9_-]+\.(?:m3u8|m4s|mp4)',name):raise ValueError('invalid_asset_name')
        p=(package/name).resolve(strict=True)
        if package not in p.parents or p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:raise ValueError('source_asset_changed')
    output.mkdir(parents=True)
    for name in m['assets']:
        p=output/name;p.parent.mkdir(parents=True,exist_ok=True)
        if name==m['master']:shutil.copyfile(package/name,p)
        else:
            try:os.link(package/name,p)
            except OSError:shutil.copyfile(package/name,p)
    folder=output/'240';folder.mkdir();width=(int(m['source_width']*240/m['source_height'])//2)*2
    args=[ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-n','-i',str(output/m['fallback']),'-an',
        '-vf',f'scale={width}:240','-c:v','libx264','-preset','fast','-threads','2','-crf','28',
        '-maxrate','180000','-bufsize','360000','-pix_fmt','yuv420p','-fps_mode','vfr',
        '-enc_time_base','1:1000','-level:v','4.1','-force_key_frames','expr:gte(t,n_forced*2)',
        '-f','hls','-hls_time','4','-hls_playlist_type','vod','-hls_segment_type','fmp4',
        '-hls_flags','independent_segments','-hls_fmp4_init_filename','init.mp4',
        '-hls_segment_filename','segment-%05d.m4s','index.m3u8']
    with (output/'low-encoder.log').open('wb') as log:subprocess.run(args,cwd=folder,stdout=log,stderr=subprocess.STDOUT,check=True)
    names=[p.relative_to(output).as_posix() for p in folder.iterdir() if p.is_file()]
    peak=max((output/n).stat().st_size*8/4 for n in names if n.endswith('.m4s'))
    m['variants'].append(dict(height=240,width=width,bandwidth=max(180000,int(peak*1.15)),playlist='240/index.m3u8'))
    master=['#EXTM3U','#EXT-X-VERSION:7','#EXT-X-INDEPENDENT-SEGMENTS']
    for v in sorted(m['variants'],key=lambda v:v['height']):
        master.extend([f"#EXT-X-STREAM-INF:BANDWIDTH={v['bandwidth']},RESOLUTION={v['width']}x{v['height']}",v['playlist']])
    (output/m['master']).write_text('\n'.join(master)+'\n')
    for name in names+[m['master']]:m['assets'][name]=dict(bytes=(output/name).stat().st_size,sha256=sha(output/name))
    m['generated_upgrade_source_manifest_sha256']=sha(manifest)
    m['low_bandwidth_variant']=dict(height=240,maxrate_bits_s=180000,native_assets_unchanged=True)
    (output/'adaptive_video.json').write_text(json.dumps(m,indent=2,allow_nan=False)+'\n')
    return m

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('package',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--ffmpeg',default='ffmpeg');a=p.parse_args();m=extend(a.package,a.output,a.ffmpeg)
    print(json.dumps(dict(archive_id=m['archive_id'],variants=m['variants'],native_assets_unchanged=True)),flush=True)
