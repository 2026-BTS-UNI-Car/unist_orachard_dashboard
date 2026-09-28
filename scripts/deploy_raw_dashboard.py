#!/usr/bin/env python3
"""Explicit Jetson deployment: preserve devices, data and motor owner.

Run from a staged git archive under ~/workspace/ubts-dashboard-releases/<sha>.
prepare backs up code/config/images and builds using the current dependency
images; apply replaces exactly six application services; rollback restores them.
No drive/arm/recording commands are sent. Checkpoints are private on the Jetson.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import urllib.request

APP=Path('/home/unist/workspace/unist_orachard_dashboard')
WORK=APP.parent
TARGETS={'dashboard':'unist-orachard-dashboard',
         'ros2-camera-bridge':'unist-orachard-ros2-camera-bridge',
         'ros2-lidar-bridge':'unist-orachard-ros2-lidar-bridge',
         'ros2-imu-bridge':'unist-orachard-ros2-imu-bridge',
         'ros2-gps-bridge':'unist-orachard-ros2-gps-bridge',
         'ros2-data-recorder':'unist-orachard-ros2-data-recorder'}
AI='unist-orachard-vision-ai'
PRESERVE={'runtime','data','.env','.git'}


def run(*cmd,**kwargs):
    return subprocess.run(cmd,check=True,**kwargs)


def read_url(port,path):
    return json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}{path}',timeout=5))


def guard():
    rover=read_url(8080,'/api/rover/status')
    recorder=read_url(8790,'/status')
    assert rover.get('ok') and not rover.get('armed') and not rover.get('moving'), 'controller must be disarmed and idle'
    assert recorder.get('ok') and not recorder.get('active'), 'recorder must be idle'
    return recorder.get('archive_count')


def inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name]))[0]


def compose(*args,override=None):
    cmd=['docker','compose','-p','unist_orachard_dashboard','--project-directory',str(APP),'-f',str(APP/'docker-compose.yml')]
    if override:cmd+=['-f',str(override)]
    run(*cmd,*args,cwd=APP)


def code_members(path):
    return [p for p in path.rglob('*') if p.is_file() and not any(x in PRESERVE or x in {'__pycache__','.pytest_cache','.ruff_cache'} for x in p.relative_to(path).parts) and p.suffix!='.pyc']


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def target_services(baseline):
    """Honor the checkpoint's scope, including older four-service backups."""
    services=baseline.get('services')
    if services is None:
        services={service:name for service,name in TARGETS.items() if name in baseline.get('targets',{})}
    assert services and all(TARGETS.get(service)==name for service,name in services.items()), 'invalid checkpoint service scope'
    return services


def rollback_image(target):
    # A mutable tag may have been reused by a later prepare; image IDs are exact.
    return target.get('backup_image') or target['tag']


def bridge_checks(services):
    checks={}
    for fname,key in [('camera_bridge_status.json','frame_topic_alive'),('lidar_bridge_status.json','scan_topic_alive'),('imu_bridge_status.json','imu_topic_alive')]:
        d=json.loads((APP/'runtime'/fname).read_text())
        checks[fname]=bool(d.get(key)) and 0<=time.time()-d.get('updated_at',0)<5
    if 'ros2-gps-bridge' in services:
        d=json.loads((APP/'runtime/gps_bridge_status.json').read_text())
        # The real GNSS receiver is not connected yet. A fresh bridge heartbeat
        # is required; a fabricated position/fix must never be a deployment gate.
        checks['gps_bridge_status.json']=d.get('schema')=='orchard.gps.v1' and 0<=time.time()-d.get('updated_at',0)<5
    return checks


def snapshot_image(name,data,tag,backup):
    if subprocess.run(['docker','image','inspect',data['Image']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:
        run('docker','tag',data['Image'],tag)
        return
    # Export the mounted root filesystem when the old layer metadata is absent.
    # Docker export excludes all bind-mounted runtime, recordings and devices.
    # No service pause, image-store repair or daemon restart is needed.
    archive=backup/f'{name}-rootfs.tar'
    run('docker','export','-o',str(archive),name)
    cmd=['docker','import']
    config=data['Config']
    for key in ['Entrypoint','Cmd']:
        if config.get(key):cmd+=['--change',key.upper()+' '+json.dumps(config[key])]
    for key in ['WorkingDir','User']:
        if config.get(key):cmd+=['--change',('WORKDIR' if key=='WorkingDir' else 'USER')+' '+config[key]]
    for env in config.get('Env',[]):
        key,value=env.split('=',1);cmd+=['--change','ENV '+key+'='+json.dumps(value)]
    cmd+=[str(archive),tag]
    # Do not include environment-bearing arguments in exceptions/logs.
    result=subprocess.run(cmd,capture_output=True)
    if result.returncode:raise RuntimeError(f'rootfs import failed for {name}; backup tar retained')
    print('SNAPSHOT',name,archive.stat().st_size,flush=True)


def prepare(release):
    count=guard()
    revision=(release/'REVISION').read_text().strip()
    assert len(revision)==40 and all(c in '0123456789abcdef' for c in revision)
    backup=WORK/'ubts-dashboard-backups'/f'{time.strftime("%Y%m%d-%H%M%S")}-{revision[:12]}'
    backup.mkdir(parents=True,mode=0o700);os.chmod(backup,0o700)
    baseline={'revision':revision,'release':str(release),'archive_count':count,'services':dict(TARGETS),
              'source_files':[str(p.relative_to(APP)) for p in code_members(APP)],
              'new_source_files':[str(p.relative_to(release)) for p in code_members(release)],
              'targets':{},'untouched':{},'settings':{}}
    for name in [*TARGETS.values(),AI]:
        data=inspect(name)
        baseline['targets'][name]={'id':data['Id'],'image':data['Image'],'restart':data['HostConfig']['RestartPolicy']['Name'],'running':data['State']['Running']}
        # AI is stopped but its existing container is preserved for rollback.
        if name==AI:continue
        tag=f'ubts-rollback/{name}:{backup.name}'
        snapshot_image(name,data,tag,backup)
        baseline['targets'][name]['backup_image']=json.loads(subprocess.check_output(['docker','image','inspect',tag]))[0]['Id']
        baseline['targets'][name]['tag']=tag
    for name in subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines():
        if name not in baseline['targets']:baseline['untouched'][name]=inspect(name)['Id']
    for rel in ['.env','runtime/camera_settings.json']:
        if (APP/rel).exists():baseline['settings'][rel]=digest(APP/rel)
    with tarfile.open(backup/'code.tar.gz','w:gz') as archive:
        for path in code_members(APP):archive.add(path,arcname=path.relative_to(APP))
    if (APP/'.env').exists():shutil.copy2(APP/'.env',backup/'env.backup')
    (backup/'baseline.json').write_text(json.dumps(baseline,indent=2))
    # Keep resolved configuration private, including device-specific settings.
    resolved=subprocess.check_output(['docker','compose','-f',str(APP/'docker-compose.yml'),'config','--format','json'],cwd=APP)
    (backup/'compose-before.json').write_bytes(resolved)
    old_images={'services':{s:{'image':rollback_image(baseline['targets'][n])} for s,n in TARGETS.items()}}
    (backup/'rollback-images.json').write_text(json.dumps(old_images))
    image_files={
      'ros2':('ros2-data-recorder','RUN rm -rf /app/scripts /app/dashboard_server\nCOPY scripts /app/scripts\nCOPY dashboard_server /app/dashboard_server\n'),
      'web':('dashboard','RUN rm -rf /app/scripts /app/dashboard_server /app/static /app/config /app/docs\nCOPY scripts/__init__.py /app/scripts/__init__.py\nCOPY dashboard_server /app/dashboard_server\nCOPY static /app/static\nCOPY config /app/config\nCOPY docs /app/docs\n')}
    tags={}
    for kind,(service,body) in image_files.items():
        base=baseline['targets'][TARGETS[service]]['tag'];tag=f'unist-orachard-raw-{kind}:{revision[:12]}';tags[kind]=tag
        dockerfile=backup/f'Dockerfile.{kind}'
        dockerfile.write_text(f'FROM {base}\nLABEL org.opencontainers.image.revision="{revision}"\nWORKDIR /app\n'+body)
        run('docker','build','-f',str(dockerfile),'-t',tag,str(release))
    override={'services':{s:{'image':tags['web' if s=='dashboard' else 'ros2']} for s in TARGETS}}
    (backup/'new-images.json').write_text(json.dumps(override))
    # No hardware, host network or persistent recordings are exposed to tests.
    for name in ['runtime','data']:(release/name).mkdir(exist_ok=True)
    run('docker','run','--rm','--network','none','-e','FAN_HOLD_INTERVAL_S=0','-e','ROS_DOMAIN_ID=173',
        '-v',f'{release}:/workspace:ro','--tmpfs','/workspace/runtime:rw','--tmpfs','/workspace/data:rw',
        '-w','/workspace',tags['ros2'],'bash','-lc','source /opt/ros/iron/setup.bash && python3 -B -m unittest discover -s tests')
    (backup/'prepared').write_text(str(time.time()))
    print('PREPARED',backup,flush=True)


def verify(backup):
    baseline=json.loads((backup/'baseline.json').read_text())
    services=target_services(baseline)
    checks={}
    for attempt in range(45):
        try:
            health=read_url(8080,'/api/health');rec=read_url(8790,'/status')
            checks={'dashboard_live':health.get('ok') and health.get('mode')=='live',
                    'recorder_idle':rec.get('ok') and not rec.get('active'),
                    'archives_preserved':rec.get('archive_count')==baseline['archive_count']}
            checks.update(bridge_checks(services))
            checks['bridge_status_readable']=True
            if all(checks.values()):break
        except (OSError,ValueError):checks['bridge_status_readable']=False
        time.sleep(2)
    checks['ai_stopped']=not inspect(AI)['State']['Running'] and inspect(AI)['HostConfig']['RestartPolicy']['Name']=='no'
    checks['untouched_containers']=all(inspect(n)['Id']==i and inspect(n)['State']['Running'] for n,i in baseline['untouched'].items())
    checks['settings_preserved']=all(digest(APP/rel)==h for rel,h in baseline['settings'].items())
    rover=read_url(8080,'/api/rover/status');checks['motor_disarmed']=not rover.get('armed') and not rover.get('moving')
    checks['new_images']=all(inspect(n)['State']['Running'] and inspect(n)['Config'].get('Labels',{}).get('org.opencontainers.image.revision')==baseline['revision'] for n in services.values())
    (backup/'verification.json').write_text(json.dumps(checks,indent=2))
    print(json.dumps(checks,indent=2),flush=True)
    assert checks and all(checks.values()),'deployment verification failed; use rollback'


def apply(backup):
    assert (backup/'prepared').exists(),'prepare must succeed first'
    baseline=json.loads((backup/'baseline.json').read_text());release=Path(baseline['release'])
    services=target_services(baseline)
    guard()
    members=code_members(release)
    # Replace reviewed tracked source only; remove retired code from an explicit manifest.
    for relative in json.loads((release/'REMOVED_FILES.json').read_text()):
        path=(APP/relative).resolve();assert path.is_relative_to(APP) and path.relative_to(APP).parts[0] not in PRESERVE
        path.unlink(missing_ok=True)
    for src in members:
        dst=APP/src.relative_to(release);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    compose('config','--quiet')
    run('docker','update','--restart=no',AI);run('docker','stop',AI)
    compose('up','--no-deps','--no-build','--force-recreate','-d',*services,override=backup/'new-images.json')
    verify(backup)
    # Default compose up must use the deployed images too.
    overrides=json.loads((backup/'new-images.json').read_text())['services']
    original=json.loads((backup/'compose-before.json').read_text())['services']
    for service,value in overrides.items():run('docker','tag',value['image'],original[service]['image'])
    (APP/'RAW_DEPLOYMENT.json').write_text(json.dumps({'revision':baseline['revision'],'backup':str(backup),'verified_at':time.time()},indent=2))
    print('APPLIED',baseline['revision'],str(backup),flush=True)


def rollback(backup):
    baseline=json.loads((backup/'baseline.json').read_text())
    services=target_services(baseline)
    with tarfile.open(backup/'code.tar.gz') as archive:
        old_names={member.name for member in archive.getmembers() if member.isfile()}
        for member in archive.getmembers():
            target=(APP/member.name).resolve();assert target.is_relative_to(APP) and target.relative_to(APP).parts[0] not in PRESERVE
            assert member.isfile() or member.isdir(), 'backup may not restore symlinks or device nodes'
        # Remove only newly introduced reviewed release files, never runtime or
        # unrelated files that appeared while the application was running.
        for relative in baseline.get('new_source_files',[]):
            if relative in old_names:continue
            target=(APP/relative).resolve();assert target.is_relative_to(APP) and target.relative_to(APP).parts[0] not in PRESERVE
            target.unlink(missing_ok=True)
        archive.extractall(APP)
    if 'RAW_DEPLOYMENT.json' not in old_names:(APP/'RAW_DEPLOYMENT.json').unlink(missing_ok=True)
    # Rebuild from immutable IDs even for checkpoints made by an older helper.
    pinned={'services':{s:{'image':rollback_image(baseline['targets'][n])} for s,n in services.items()}}
    pinned_path=backup/'rollback-images-pinned.json';pinned_path.write_text(json.dumps(pinned))
    compose('up','--no-deps','--no-build','--force-recreate','-d',*services,override=pinned_path)
    old=baseline['targets'][AI];run('docker','update','--restart='+old['restart'],AI)
    if old['running']:run('docker','start',AI)
    original=json.loads((backup/'compose-before.json').read_text())['services']
    for service,name in services.items():run('docker','tag',rollback_image(baseline['targets'][name]),original[service]['image'])
    print('ROLLED_BACK',backup,flush=True)


if __name__=='__main__':
    os.umask(0o077)
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','apply','verify','rollback']);p.add_argument('path',type=Path);a=p.parse_args()
    path=a.path.resolve();expected=WORK/('ubts-dashboard-releases' if a.action=='prepare' else 'ubts-dashboard-backups')
    assert path.parent==expected,'path must be one direct child of the expected workspace directory'
    globals()[a.action](path)
