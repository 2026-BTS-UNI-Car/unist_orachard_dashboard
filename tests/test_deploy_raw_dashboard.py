"""Deployment scope and rollback contracts. No Docker/SSH/hardware is used."""
import io
import json
from pathlib import Path
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import deploy_raw_dashboard as deploy


class DeploymentScopeTest(unittest.TestCase):
    def test_six_apps_exclude_all_device_owners(self):
        self.assertEqual(set(deploy.TARGETS), {'dashboard', 'ros2-camera-bridge', 'ros2-lidar-bridge',
            'ros2-imu-bridge', 'ros2-gps-bridge', 'ros2-data-recorder'})
        self.assertFalse(any('driver' in name or 'esp' in name for name in deploy.TARGETS))

    def test_legacy_four_service_checkpoint_keeps_original_scope(self):
        saved={name:{} for service,name in deploy.TARGETS.items() if service not in {'ros2-imu-bridge','ros2-gps-bridge'}}
        saved[deploy.AI]={}
        result=deploy.target_services({'targets':saved})
        self.assertEqual(len(result),4)
        self.assertNotIn('ros2-gps-bridge',result)
        self.assertNotIn('ros2-imu-bridge',result)

    def test_explicit_scope_cannot_expand_into_a_driver(self):
        with self.assertRaises(AssertionError):
            deploy.target_services({'services':{'ros2-gps-driver':'unist-orachard-ros2-gps-driver'}})

    def test_immutable_image_wins_over_reused_tag(self):
        self.assertEqual(deploy.rollback_image({'backup_image':'sha256:old','tag':'reused:new'}),'sha256:old')

    def test_gps_heartbeat_passes_without_fix_but_stale_heartbeat_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            app=Path(temporary);(app/'runtime').mkdir()
            for name,key in [('camera_bridge_status.json','frame_topic_alive'),('lidar_bridge_status.json','scan_topic_alive'),('imu_bridge_status.json','imu_topic_alive')]:
                (app/'runtime'/name).write_text(json.dumps({key:True,'updated_at':time.time()}))
            path=app/'runtime/gps_bridge_status.json'
            path.write_text(json.dumps({'schema':'orchard.gps.v1','updated_at':time.time(),'valid':False,'connected':False}))
            with patch.object(deploy,'APP',app):
                self.assertTrue(all(deploy.bridge_checks(deploy.TARGETS).values()))
                path.write_text(json.dumps({'schema':'orchard.gps.v1','updated_at':time.time()-20,'valid':True}))
                self.assertFalse(deploy.bridge_checks(deploy.TARGETS)['gps_bridge_status.json'])

    def test_rollback_pins_original_scope_and_removes_only_new_release_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);app=root/'app';backup=root/'backup'
            app.mkdir();backup.mkdir();(app/'runtime').mkdir()
            (app/'runtime/keep').write_text('sensor data')
            (app/'new.py').write_text('new release')
            (app/'operator-note.txt').write_text('unrelated file')
            (app/'RAW_DEPLOYMENT.json').write_text('new revision marker')
            targets={name:{'tag':f'tag:{service}','backup_image':f'sha256:{service}'} for service,name in deploy.TARGETS.items() if service not in {'ros2-imu-bridge','ros2-gps-bridge'}}
            targets[deploy.AI]={'restart':'no','running':False}
            baseline={'targets':targets,'new_source_files':['new.py','old.py']}
            (backup/'baseline.json').write_text(json.dumps(baseline))
            services=deploy.target_services(baseline)
            (backup/'compose-before.json').write_text(json.dumps({'services':{service:{'image':f'{service}:latest'} for service in services}}))
            with tarfile.open(backup/'code.tar.gz','w:gz') as archive:
                data=b'old revision';info=tarfile.TarInfo('old.py');info.size=len(data)
                archive.addfile(info,io.BytesIO(data))
            with patch.object(deploy,'APP',app),patch.object(deploy,'compose') as compose,patch.object(deploy,'run') as run:
                deploy.rollback(backup)
                self.assertEqual(set(compose.call_args.args[5:]),set(services))
                self.assertNotIn('ros2-gps-bridge',compose.call_args.args)
                tags=[call.args for call in run.call_args_list if call.args[:2]==('docker','tag')]
                self.assertTrue(all(command[2].startswith('sha256:') for command in tags))
            self.assertFalse((app/'new.py').exists())
            self.assertEqual((app/'old.py').read_text(),'old revision')
            self.assertTrue((app/'runtime/keep').exists())
            self.assertTrue((app/'operator-note.txt').exists())
            self.assertFalse((app/'RAW_DEPLOYMENT.json').exists())

    def test_verification_missing_sensor_files_cannot_pass_on_partial_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);app=root/'app';app.mkdir();backup=root/'backup';backup.mkdir()
            baseline={'revision':'a'*40,'archive_count':0,'services':{'dashboard':deploy.TARGETS['dashboard']},
                      'targets':{deploy.TARGETS['dashboard']:{}},'untouched':{},'settings':{}}
            (backup/'baseline.json').write_text(json.dumps(baseline))
            def inspect(name):
                return {'State':{'Running':name!=deploy.AI},'HostConfig':{'RestartPolicy':{'Name':'no'}},
                        'Config':{'Labels':{'org.opencontainers.image.revision':'a'*40}}}
            def read_url(port,path):
                return {'ok':True,'mode':'live','archive_count':0,'active':False,'armed':False,'moving':False}
            with patch.object(deploy,'APP',app),patch.object(deploy,'inspect',inspect),patch.object(deploy,'read_url',read_url),patch.object(deploy.time,'sleep'):
                with self.assertRaises(AssertionError):deploy.verify(backup)
            self.assertFalse(json.loads((backup/'verification.json').read_text())['bridge_status_readable'])


if __name__=='__main__':unittest.main()
