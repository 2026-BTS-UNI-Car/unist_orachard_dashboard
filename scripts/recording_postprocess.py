"""Resumable per-archive native video preparation, without touching source bags."""
import json
import os
from pathlib import Path
import threading
from datetime import datetime, timezone

GATE=threading.Lock()


def process(archive):
    from scripts.export_recording_camera import export
    from scripts.build_adaptive_recording import build
    from scripts.install_adaptive_recording import install
    from dashboard_server.occupancy_map import atomic_json
    archive=Path(archive)
    status=archive/'video_preparation.json'
    workspace=archive/'generated_video_work';workspace.mkdir(exist_ok=True)
    def report(state,**fields):
        atomic_json(status,dict(state=state,updated_utc=datetime.now(timezone.utc).isoformat(),**fields))
    with GATE:
        try:
            if (archive/'adaptive_video/adaptive_video.json').is_file():report('complete');return
            report('exporting_original_frames')
            source=workspace/'source.zip'
            if not source.is_file():export(archive,source)
            report('encoding_adaptive_video')
            package=workspace/'package'
            if not (package/'adaptive_video.json').is_file():
                # Failed/incomplete directories stay as evidence. A retry uses
                # another directory; never recursively delete archive data.
                if package.exists():package=workspace/('package-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
                build(source,package,'ffmpeg',archive/'metadata.json')
            report('verifying_generated_video')
            result=install(package,archive.parent)
            report('complete',result=result)
        except Exception as error:report('failed',error=str(error)[:300])


def enqueue(archive):
    if os.environ.get('RECORDER_ADAPTIVE_VIDEO_AUTO','1')!='1':return
    path=Path(archive)
    if not (path/'rosbag2/metadata.yaml').is_file():return
    thread=threading.Thread(target=process,args=(path,),name='recording-adaptive-video',daemon=True)
    thread.start()


def resume(root):
    # Only unfinished jobs created by this pipeline resume. Legacy recordings
    # are not automatically re-encoded or replaced at service startup.
    for archive in Path(root).iterdir() if Path(root).is_dir() else []:
        status=archive/'video_preparation.json'
        if not status.is_file():continue
        try:state=json.loads(status.read_text()).get('state')
        except (OSError,ValueError):continue
        if state!='complete':enqueue(archive)
