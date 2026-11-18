from pathlib import Path
import ast
import gzip
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace, ModuleType
from unittest.mock import patch
import zlib

HERE = Path(__file__).resolve().parent
PUBLIC = HERE.parent
ROOT = PUBLIC
sys.path.insert(0, str(PUBLIC))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


d = load('scripts.recording_durability', ROOT / 'scripts/recording_durability.py')
writer = load('scripts.mapping_sensor_writer', ROOT / 'scripts/mapping_sensor_writer.py')


def recorder_namespace():
    source = ROOT / 'scripts/data_recorder_service.py'
    tree = ast.parse(source.read_bytes())
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            if not any(alias.name == 'rclpy' for alias in node.names):
                selected.append(node)
        elif isinstance(node, ast.ImportFrom):
            if not node.module.startswith(('rclpy', 'sensor_msgs', 'std_msgs')):
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in ('iso_now', 'log', 'write_json_atomic', 'read_json', 'safe_id'):
            selected.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == 'RecordingState':
            selected.append(node)
    namespace = {'__file__': str(source), '__name__': 'exact_recorder_AST_no_ROS'}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


def state_args(folder):
    return SimpleNamespace(archive_dir=str(folder/'recordings'), status_file=str(folder/'status.json'),
        camera_preview_width=640, camera_jpeg_quality=68, preview_max_points=1200,
        lidar_frame_id='laser', camera_archive_flush_timeout_s=.1,
        timeline_hz=2., timeline_max_frames=1800, imu_timeline_hz=10., imu_timeline_max_frames=18000,
        imu_raw_topic='/imu/data_raw', imu_relay_topic='/imu/data', imu_mag_topic='/imu/mag',
        imu_driver_status_topic='/orchard/imu/driver_status', cmd_topic='/cmd_vel',
        rover_status_topic='/rover/status', odom_topic='/odom', tf_topic='/tf', tf_static_topic='/tf_static',
        scan_topic='/scan', camera_topic='/camera/image_raw', camera_compressed_topic='/camera/image_jpeg',
        camera_info_topic='/camera/camera_info', camera_preview_info_topic='/camera/preview/camera_info',
        disable_rosbag=True, rosbag_stop_timeout_s=8.)


class DurabilityTests(unittest.TestCase):
    def test_atomic_file_fsync_precedes_replace_then_directory_fsync(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'metadata.json';path.write_text('{"old":1}')
            order=[];real_sync=os.fsync;real_replace=os.replace
            def sync(fd):order.append('file');return real_sync(fd)
            def replace(a,b):order.append('replace');return real_replace(a,b)
            with patch.object(d.os,'fsync',sync),patch.object(d.os,'replace',replace),patch.object(d,'sync_directory',side_effect=lambda p:order.append('directory') or True):
                proof=d.atomic_json(path, {'state':'recording','original_stamp':19})
            self.assertEqual(order,['file','replace','directory'])
            self.assertEqual(json.loads(path.read_text()),{'state':'recording','original_stamp':19})
            self.assertTrue(proof['directory_fsync_completed'])
            self.assertEqual(list(Path(temp).glob('*.tmp')),[])

    def test_fsync_failure_preserves_old_metadata_and_removes_own_temporary(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'metadata.json';old=b'{"state":"recording"}';path.write_bytes(old)
            with patch.object(d.os,'fsync',side_effect=OSError('disk sync failed')):
                with self.assertRaises(OSError):d.atomic_json(path,{'state':'archived'})
            self.assertEqual(path.read_bytes(),old);self.assertEqual(list(Path(temp).glob('*.tmp')),[])

    def test_directory_failure_is_reported_without_corrupting_complete_json(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'metadata.json'
            with patch.object(d,'sync_directory',side_effect=OSError('directory fsync failed')):
                with self.assertRaises(OSError):d.atomic_json(path,{'state':'recording'})
            self.assertEqual(json.loads(path.read_bytes()),{'state':'recording'})

    def test_periodic_worker_owns_at_most_two_noninheritable_duplicate_fds(self):
        with tempfile.TemporaryDirectory() as temp:
            with (Path(temp)/'gps.jsonl').open('w') as gps,(Path(temp)/'motor.jsonl').open('w') as motor:
                worker=d.PeriodicFsync(interval=.1);worker.register('gps',gps);worker.register('motor',motor)
                self.assertEqual(worker.status()['owned_descriptors'],2)
                self.assertTrue(all(not os.get_inheritable(fd) for fd in worker.descriptors.values()))
                with self.assertRaises(ValueError):worker.register('unbounded',gps)
                with self.assertRaises(ValueError):worker.register('gps',gps)
                gps.write('{"stamp":123}\n');gps.flush();worker.start()
                self.assertTrue(worker.close());self.assertTrue(worker.close())
                self.assertGreaterEqual(worker.status()['fsync_calls_completed'],2)
                self.assertFalse(worker.status()['worker_alive']);self.assertEqual(worker.status()['owned_descriptors'],0)

    def test_slow_fsync_close_keeps_unknown_worker_and_descriptor_ownership(self):
        entered=threading.Event();release=threading.Event()
        def slow(fd):entered.set();release.wait(2);os.fsync(fd)
        with tempfile.TemporaryDirectory() as temp:
            with (Path(temp)/'gps.jsonl').open('w') as stream:
                worker=d.PeriodicFsync(interval=.1,fsync=slow);worker.register('gps',stream);worker.start()
                self.assertTrue(entered.wait(1))
                self.assertFalse(worker.close(.01));self.assertTrue(worker.status()['worker_alive'])
                self.assertEqual(worker.status()['owned_descriptors'],1)
                with self.assertRaises(ValueError):worker.start()
                release.set();self.assertTrue(worker.close())

    def test_os_error_latched_but_no_false_power_loss_or_bag_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            with (Path(temp)/'gps.jsonl').open('w') as stream:
                worker=d.PeriodicFsync(fsync=lambda fd:(_ for _ in ()).throw(OSError('no storage')))
                worker.register('gps',stream);worker.start();self.assertTrue(worker.close())
                self.assertIn('gps',worker.status()['errors'])
                self.assertFalse(worker.status()['sudden_power_loss_qualified'])
                self.assertFalse(worker.status()['rosbag_readproof'])

    def test_worker_start_failure_keeps_descriptors_closeable_without_unknown_thread(self):
        with tempfile.TemporaryDirectory() as temp:
            with (Path(temp)/'gps.jsonl').open('w') as stream:
                worker=d.PeriodicFsync();worker.register('gps',stream)
                with patch.object(d.threading.Thread,'start',side_effect=RuntimeError('cannot start')):
                    with self.assertRaises(RuntimeError):worker.start()
                self.assertIsNone(worker.thread)
                self.assertTrue(worker.close());self.assertEqual(worker.status()['owned_descriptors'],0)

    def test_mapping_finalized_full_original_fifo_gzip_and_durable_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            receipts=[(1_000_000_000+i)/1e9 for i in range(20)]
            # Preserve the existing receipt float -> integer conversion exactly;
            # this patch must not repair or restamp original recorder inputs.
            original=[dict(topic='/scan',message={'stamp':i,'ranges':[None,1.25]},bag_time_ns=int(receipts[i]*1e9)) for i in range(20)]
            w=writer.MappingSensorWriter(temp)
            for row,receipt in zip(original,receipts):w.add(row['topic'],row['message'],receipt)
            proof=w.close()
            with gzip.open(Path(temp)/'mapping_source/sensors.jsonl.gz','rt') as stream:actual=[json.loads(line) for line in stream]
            self.assertEqual(actual,original);self.assertTrue(proof['complete'])
            self.assertEqual(proof['dropped'],0);self.assertEqual(proof['topic_counts'],{'/scan':20})
            self.assertEqual(proof,json.loads((Path(temp)/'mapping_source/manifest.json').read_bytes()))
            self.assertFalse(proof['durability']['rosbag_readproof'])

    def test_mapping_periodic_fsync_when_no_new_callbacks_arrive(self):
        with tempfile.TemporaryDirectory() as temp:
            w=writer.MappingSensorWriter(temp);w.add('/scan',{'original':17},1.)
            deadline=time.monotonic()+2.
            while w.fsync_count<1 and time.monotonic()<deadline:time.sleep(.02)
            self.assertGreaterEqual(w.fsync_count,1);self.assertTrue(w.close()['complete'])

    def test_real_recorder_normal_stop_stays_archived_and_enqueues_camera_video(self):
        ns=recorder_namespace();enqueued=[]
        media=ModuleType('scripts.recording_postprocess');media.enqueue=lambda root,queue:enqueued.append(Path(root)) or {'ok':True}
        with tempfile.TemporaryDirectory() as temp,patch.dict(sys.modules,{'scripts.recording_postprocess':media}):
            folder=Path(temp);state=ns['RecordingState'](state_args(folder))
            older=state.archive_dir/'older';older.mkdir(parents=True);(older/'metadata.json').write_bytes(b'{"id":"older","state":"archived"}')
            previous=(older/'metadata.json').read_bytes()
            self.assertTrue(state.start('both',include_imu=True,include_gps=True)['ok'])
            current=Path(state.active['path']);state.record_mapping_sensor('/scan',{'header':{'stamp':{'sec':123,'nanosec':7}},'ranges':[1.2,None]},123.)
            result=state.stop();self.assertTrue(result['ok']);self.assertEqual(result['state'],'archived')
            self.assertEqual(enqueued,[current]);self.assertFalse(result['status']['active']);self.assertFalse(result['status']['finalizing'])
            metadata=json.loads((current/'metadata.json').read_bytes());self.assertEqual(metadata['state'],'archived')
            self.assertTrue(metadata['durability']['sidecar_worker_closed']);self.assertFalse(metadata['durability']['rosbag_readproof'])
            self.assertFalse(metadata['durability']['sudden_power_loss_qualified'])
            self.assertTrue((current/'timeline.json').is_file());self.assertEqual((older/'metadata.json').read_bytes(),previous)

    def test_normal_recorder_stop_with_fsync_error_keeps_integrity_explicitly_unverified(self):
        ns=recorder_namespace()
        with tempfile.TemporaryDirectory() as temp:
            state=ns['RecordingState'](state_args(Path(temp)));self.assertTrue(state.start('lidar',include_gps=False)['ok'])
            state.durability.fsync=lambda fd:(_ for _ in ()).throw(OSError('disk acknowledgment failed'))
            result=state.stop();self.assertEqual(result['state'],'archived')
            value=json.loads((Path(state.args.archive_dir)/result['recording_id']/'metadata.json').read_bytes())
            self.assertIn('motor',value['durability']['sidecar_worker']['errors'])
            self.assertFalse(value['durability']['rosbag_readproof'])

    def test_blocked_mapping_fsync_retains_writer_denies_new_start_without_partial_hash(self):
        ns=recorder_namespace();entered=threading.Event();release=threading.Event()
        real_flush=writer.flush_sync
        def blocked(stream):
            entered.set();release.wait(3);real_flush(stream)
        with tempfile.TemporaryDirectory() as temp,patch.object(writer,'flush_sync',blocked):
            state=ns['RecordingState'](state_args(Path(temp)))
            self.assertTrue(state.start('lidar',include_gps=False)['ok'])
            w=state.mapping_writer;w.add('/scan',{'stamp':9},1.)
            real_join=w.thread.join
            try:
                self.assertTrue(entered.wait(2))
                with patch.object(w.thread,'join',side_effect=lambda timeout:real_join(.01)):
                    stopped=state.stop()
                self.assertEqual(stopped['state'],'archived')  # unchanged legacy label
                self.assertIs(state.mapping_writer,w)
                self.assertTrue(stopped['status']['mapping_sidecar_cleanup_pending'])
                proof=json.loads((w.folder/'manifest.json').read_bytes())
                self.assertFalse(proof['complete']);self.assertEqual(proof['output_sha256'],{})
                self.assertFalse(proof['durability']['worker_closed'])
                self.assertEqual(state.start('camera')['state'],'durability_cleanup_pending')
            finally:
                release.set();real_join(2)
                state.stop()
            self.assertFalse(w.thread.is_alive());self.assertIsNone(state.mapping_writer)

    def test_periodic_close_racing_worker_creation_does_not_double_close_descriptors(self):
        entered=threading.Event();release=threading.Event();errors=[];outcome=[]
        real_start=d.threading.Thread.start
        with tempfile.TemporaryDirectory() as temp:
            with (Path(temp)/'gps.jsonl').open('w') as stream:
                worker=d.PeriodicFsync();worker.register('gps',stream)
                def delayed_start(thread):
                    if thread.name=='recorder-sidecar-fsync':entered.set();release.wait(2)
                    return real_start(thread)
                def create():
                    try:worker.start()
                    except BaseException as error:errors.append(error)
                def close():
                    try:outcome.append(worker.close())
                    except BaseException as error:errors.append(error)
                with patch.object(d.threading.Thread,'start',delayed_start):
                    starter=threading.Thread(target=create);starter.start();self.assertTrue(entered.wait(1))
                    closer=threading.Thread(target=close);closer.start()
                    self.assertTrue(worker.stop.wait(1));release.set();starter.join(2);closer.join(2)
                self.assertEqual(errors,[]);self.assertEqual(outcome,[True])
                self.assertEqual(worker.status()['owned_descriptors'],0)


    def run_killed_child(self, code, directory):
        process=subprocess.Popen([sys.executable,'-B','-u','-c',code,str(directory)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            self.assertEqual(process.stdout.readline().strip(),b'READY')
            process.kill();process.wait(timeout=5)
            stdout,stderr=process.communicate(timeout=2);self.assertEqual(stderr,b'')
            self.assertNotEqual(process.returncode,0)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
            process.stdout.close();process.stderr.close()

    def test_kill_after_temporary_fsync_before_replace_preserves_original_metadata(self):
        helper=str(ROOT/'scripts/recording_durability.py')
        code="import importlib.util,pathlib,sys,time\ns=importlib.util.spec_from_file_location('d',"+repr(helper)+");d=importlib.util.module_from_spec(s);s.loader.exec_module(d)\np=pathlib.Path(sys.argv[1])/'metadata.json'\ndef blocked(a,b):\n print('READY',flush=True)\n time.sleep(60)\nd.os.replace=blocked\nd.atomic_json(p,{'state':'archived'})\n"
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'metadata.json';original=b'{"state":"recording","stamp":123}';path.write_bytes(original)
            self.run_killed_child(code,temp);self.assertEqual(path.read_bytes(),original)
            partial=list(Path(temp).glob('*.tmp'));self.assertEqual(len(partial),1)
            self.assertEqual(json.loads(partial[0].read_bytes()),{'state':'archived'})

    def test_kill_keeps_fsynced_jsonl_and_gzip_prefix_but_not_fake_finalization(self):
        helper=str(ROOT/'scripts/recording_durability.py')
        code="import importlib.util,pathlib,sys,time,gzip,json\ns=importlib.util.spec_from_file_location('d',"+repr(helper)+");d=importlib.util.module_from_spec(s);s.loader.exec_module(d)\np=pathlib.Path(sys.argv[1]);d.atomic_json(p/'metadata.json',{'state':'recording','original_stamp':42})\nf=(p/'gps.jsonl').open('w');g=gzip.open(p/'sensors.jsonl.gz','wt')\nfor i in range(40):\n row=json.dumps({'sequence':i,'stamp':42+i})+'\\n';f.write(row);g.write(row)\nd.flush_sync(f);d.flush_sync(g)\nprint('READY',flush=True);time.sleep(60)\n"
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);self.run_killed_child(code,temp)
            self.assertEqual(len((folder/'gps.jsonl').read_text().splitlines()),40)
            original=(folder/'sensors.jsonl.gz').read_bytes();digest=hashlib.sha256(original).hexdigest()
            with self.assertRaises((EOFError,OSError)):
                with gzip.open(folder/'sensors.jsonl.gz','rt') as stream:stream.read()
            # Inspection is in-memory only: original truncated gzip is untouched.
            prefix=zlib.decompressobj(16+zlib.MAX_WBITS).decompress(original).decode()
            self.assertEqual([json.loads(line)['sequence'] for line in prefix.splitlines()],list(range(40)))
            self.assertEqual(hashlib.sha256((folder/'sensors.jsonl.gz').read_bytes()).hexdigest(),digest)
            self.assertEqual(json.loads((folder/'metadata.json').read_bytes())['state'],'recording')


if __name__=='__main__':unittest.main()



class RecordingSensorChoiceTests(unittest.TestCase):
    """2026-11-19: one checkbox per sensor and a manual / autonomy tag (no ROS needed: the AST namespace)."""

    def test_sensor_plan_and_legacy(self):
        RS = recorder_namespace()['RecordingState']
        self.assertEqual(RS.sensor_plan(["lidar", "imu", "executor"])[:3], ("lidar", True, False))
        self.assertEqual(RS.sensor_plan(["camera", "lidar", "gps"])[:3], ("both", False, True))
        self.assertEqual(RS.sensor_plan(["imu", "gps"])[3], "sensors_need_lidar_or_camera")
        self.assertTrue(RS.sensor_plan(["lidar", "radar"])[3].startswith("unknown_sensor"))
        self.assertEqual(RS.sensor_plan("lidar")[3], "sensors_invalid")
        self.assertIn("executor", RS.legacy_sensors("lidar"))
        self.assertNotIn("camera", RS.legacy_sensors("lidar"))

    def test_topics_tag_and_metadata(self):
        ns = recorder_namespace()
        with tempfile.TemporaryDirectory() as temp:
            state = ns['RecordingState'](state_args(Path(temp)))
            topics = state._topics_for_sensors(["lidar", "executor"])
            self.assertIn(state.args.scan_topic, topics)
            self.assertIn("/row_executor/state", topics)
            self.assertNotIn(state.args.imu_raw_topic, topics)
            self.assertEqual(state.start("lidar", sensors=["imu"])["state"], "invalid_sensors")
            self.assertEqual(state.start("lidar", tag="field")["state"], "invalid_tag")
