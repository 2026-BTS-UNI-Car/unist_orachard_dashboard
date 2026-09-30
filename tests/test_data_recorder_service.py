from __future__ import annotations
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
import numpy as np
try:
    from scripts.data_recorder_service import RecordingState
except ModuleNotFoundError as exc:
    if exc.name not in {'rclpy', 'sensor_msgs'}:
        raise
    RecordingState = None

@unittest.skipUnless(RecordingState is not None, 'ROS2 Python runtime is required')
class DataRecorderCameraPipelineTests(unittest.TestCase):

    def make_state(self, tmp: str, *, wait_s: float=0.2) -> RecordingState:
        args = SimpleNamespace(archive_dir=str(Path(tmp) / 'recordings'), status_file=str(Path(tmp) / 'status.json'), camera_preview_width=160, camera_jpeg_quality=68, preview_max_points=720, lidar_frame_id='laser', camera_vision_sync_max_delta_s=0.12, camera_vision_wait_timeout_s=wait_s, camera_archive_flush_timeout_s=0.5, timeline_hz=2.0, timeline_max_frames=10, imu_raw_topic='/imu/data_raw', imu_relay_topic='/imu/data', imu_mag_topic='/imu/mag', imu_driver_status_topic='/orchard/imu/driver_status', imu_timeline_hz=10.0, imu_timeline_max_frames=100, cmd_topic='/cmd_vel', rover_status_topic='/rover/status', odom_topic='/odom', tf_topic='/tf', tf_static_topic='/tf_static', vision_topic='/orchard/vision/tree_bearings', scan_topic='/scan', camera_topic='/camera/image_raw', camera_compressed_topic='/camera/image_jpeg', camera_info_topic='/camera/camera_info', camera_preview_info_topic='/camera/preview/camera_info')
        state = RecordingState(args)
        with state.lock:
            state.active = {'kind': 'camera', 'started_monotonic': time.time() - 1.0, 'id': 'test_camera'}
        return state

    def image_message(self, value: int=37) -> SimpleNamespace:
        image = np.full((180, 320, 3), value, dtype=np.uint8)
        return SimpleNamespace(height=180, width=320, encoding='bgr8', data=image.tobytes(), header=SimpleNamespace(frame_id='camera_link_optical', stamp=SimpleNamespace(sec=123, nanosec=456)))

    def shared_vision_message(self) -> SimpleNamespace:
        payload = {'schema': 'orchard.camera_bearings.v1', 'ok': True, 'algorithm': 'local_orchard_vision_ai_worker_v4', 'revision': 'orchard_trunk_yolo11_trt_clip_ood_v1', 'stamp': {'sec': 123, 'nanosec': 456}, 'stamp_ns': 123000000456, 'inference_input_policy': {'revision': 'source_native_preview_decoupled_jpeg_v1', 'mode': 'source_native', 'requested_width': 0, 'source_width': 320, 'source_height': 180, 'inference_width': 320, 'inference_height': 180, 'jpeg_quality': 86, 'preview_decoupled': True, 'no_upscale': True}, 'detections': [{'class_name': 'orchard_tree_trunk', 'label': 'orchard_tree_trunk', 'bbox': [120, 20, 30, 100], 'confidence': 0.9, 'tree_probability': 0.9, 'ood_score': 0.1, 'stable': True, 'stable_hits': 3, 'track_id': 'cam-tree-e0-1'}]}
        return SimpleNamespace(data=json.dumps(payload))

    def imu_message(self) -> SimpleNamespace:
        return SimpleNamespace(header=SimpleNamespace(frame_id='imu_link', stamp=SimpleNamespace(sec=123, nanosec=456)), orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0), angular_velocity=SimpleNamespace(x=0.01, y=-0.02, z=0.005), linear_acceleration=SimpleNamespace(x=0.0, y=0.0, z=9.80665), orientation_covariance=[-1.0] + [0.0] * 8, angular_velocity_covariance=[-1.0] + [0.0] * 8, linear_acceleration_covariance=[-1.0] + [0.0] * 8)

    def lidar_message(self) -> SimpleNamespace:
        return SimpleNamespace(header=SimpleNamespace(frame_id='laser', stamp=SimpleNamespace(sec=123, nanosec=456)), angle_min=-np.pi, angle_increment=np.pi / 2.0, time_increment=0.0244, scan_time=0.0976, range_min=0.15, range_max=12.0, ranges=[1.0, 2.0, 3.0, 4.0])

    def test_lidar_archive_preserves_source_scan_timing_and_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self.make_state(tmp)
            with state.lock:
                state.active['kind'] = 'both'
                state.next_lidar_sample_at = 0.0
            state.observe_scan(self.lidar_message())
            self.assertEqual(len(state.lidar_frames), 1)
            frame = state.lidar_frames[0]
            self.assertEqual(frame['stamp'], {'sec': 123, 'nanosec': 456})
            self.assertAlmostEqual(frame['metrics']['scan_time_s'], 0.0976)
            self.assertAlmostEqual(frame['metrics']['time_increment_s'], 0.0244)
            self.assertAlmostEqual(frame['metrics']['angle_min_rad'], -np.pi)
            self.assertAlmostEqual(frame['metrics']['angle_increment_rad'], np.pi / 2.0)
            self.assertEqual(frame['metrics']['point_time_direction'], -1.0)

    def test_ros_callback_only_coalesces_latest_frame_without_wait_or_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self.make_state(tmp, wait_s=1.5)
            started = time.perf_counter()
            state.observe_camera(self.image_message(11))
            with state.lock:
                state.next_camera_sample_at = 0.0
            state.observe_camera(self.image_message(22))
            callback_duration = time.perf_counter() - started
            self.assertLess(callback_duration, 0.1)
            self.assertEqual(state.camera_enqueued_count, 2)
            self.assertEqual(state.camera_coalesced_frame_count, 1)
            self.assertEqual(int(state.camera_pending_sample['data'][0]), 22)
            self.assertEqual(len(state.camera_frames), 0)

    def test_imu_native_recording_never_creates_a_reference_or_corrected_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self.make_state(tmp)
            state.active.update({'kind':'both','include_imu':True,'stopping':False})
            msg = self.imu_message()
            msg.orientation.z, msg.orientation.w = .5, float(np.sqrt(3)/2)
            msg.orientation_covariance = [0.]*9
            for _ in range(25):
                raw = state.observe_imu(msg)
            self.assertEqual(raw['orientation'],vars(msg.orientation))
            self.assertEqual(raw['angular_velocity'],vars(msg.angular_velocity))
            self.assertEqual(raw['frame_id'],'imu_link')
            self.assertFalse(hasattr(state,'imu_calibrator'))
            self.assertGreater(len(state.imu_frames),0)
            for frame in state.imu_frames:
                self.assertIsNone(frame['corrected'])
                self.assertEqual(frame['raw']['orientation'],vars(msg.orientation))
                self.assertEqual(frame['raw']['stamp'],{'sec':123,'nanosec':456})
                self.assertFalse(frame['processing']['start_reference'])
            self.assertIn('/imu/data_raw',state.topics_for('both'))
            self.assertIn('/imu/data',state.topics_for('both'))
            self.assertNotIn('/orchard/imu/calibration',state.topics_for('both'))
            timeline = state.timeline_payload(state.active,{},1.)
            self.assertEqual(timeline['imu']['processing']['mode'],'sensor_native_raw')
            self.assertNotIn('calibration',timeline['imu'])
            self.assertNotIn('mount_transform',timeline['imu'])

    def test_imu_can_be_explicitly_disabled_but_default_contract_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = self.make_state(tmp)
            self.assertNotIn('/imu/data_raw', state.topics_for('both', include_imu=False))
            self.assertIn('/imu/data_raw', state.topics_for('camera'))

    def test_retired_manual_zero_does_not_modify_state_or_require_fresh_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state=self.make_state(tmp)
            for have_sample in (False,True):
                if have_sample:state.observe_imu(self.imu_message())
                raw_before=json.dumps(state.imu_latest_raw,sort_keys=True)
                with patch.object(state,'write_status',side_effect=AssertionError('no writes')):
                    result=state.zero_imu()
                self.assertFalse(result['ok'])
                self.assertEqual(result['state'],'removed')
                self.assertEqual(json.dumps(state.imu_latest_raw,sort_keys=True),raw_before)
                self.assertFalse(hasattr(state,'imu_calibrator'))

    def test_new_archive_is_raw_only_and_historical_calibration_files_are_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state=self.make_state(tmp)
            state.active=None
            old=state.archive_dir/'historical'
            old.mkdir(parents=True)
            legacy={"imu":{"frames":[{"raw":{"stamp":{"sec":1,"nanosec":2}},
                                        "corrected":{"frame_id":"base_link","euler_deg":{"yaw":17.}}}],
                            "calibration":{"valid":True,"reference_id":"historical"}}}
            (old/'timeline.json').write_text(json.dumps(legacy))
            (old/'imu_calibration.json').write_text('{"valid":true,"legacy":true}')
            old_bytes={path.name:path.read_bytes() for path in old.iterdir()}
            with patch.object(state,'start_rosbag',return_value=None):
                self.assertTrue(state.start('lidar',include_imu=True,include_gps=False)['ok'])
            current=Path(state.active['path'])
            state.observe_imu(self.imu_message())
            self.assertTrue(state.stop()['ok'])
            metadata=json.loads((current/'metadata.json').read_text())
            timeline=json.loads((current/'timeline.json').read_text())
            self.assertEqual(metadata['timeline']['imu_processing']['mode'],'sensor_native_raw')
            self.assertNotIn('imu_corrected_frame_id',metadata['timeline'])
            self.assertFalse((current/'imu_calibration.json').exists())
            self.assertEqual(len(timeline['imu']['frames']),1)
            self.assertIsNone(timeline['imu']['frames'][0]['corrected'])
            self.assertEqual({path.name:path.read_bytes() for path in old.iterdir()},old_bytes)

if __name__ == '__main__':
    unittest.main()
