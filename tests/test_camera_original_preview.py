"""Tiny original-JPEG contracts; generated JPEG is not real camera evidence."""
import hashlib
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from scripts.camera_original_preview import OriginalPreviewState, original_jpeg_metadata, original_subscription_policy
from scripts.camera_ws_bridge import CameraState, camera_source_snapshot, camera_stream_budget, client_settings, network_preview


class OriginalPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jpeg = cv2.imencode('.jpg', np.zeros((24, 32, 3), np.uint8))[1].tobytes()
        cls.format = 'jpeg; original_camera_mjpeg; rotation=180; width=32; height=24'

    def test_SOF_format_and_rotation_fail_closed(self):
        m = original_jpeg_metadata(self.jpeg, self.format)
        self.assertEqual((m['source_width'], m['source_height'], m['display_rotation_deg']), (32, 24, 180))
        for jpeg, fmt in [(self.jpeg[:-1], self.format), (self.jpeg, self.format.replace('32', '33')),
                          (self.jpeg, self.format.replace('180', '90')), (self.jpeg, self.format+'; width=32'),
                          (self.jpeg, self.format.replace('original_camera_mjpeg', 'unknown'))]:
            with self.assertRaises(ValueError): original_jpeg_metadata(jpeg, fmt)

    def test_original_bytes_no_decode_or_reencode_and_legacy_budget_unchanged(self):
        state = CameraState(); state.original.adjust_clients(1)
        state.original.update('camera', 1, 0, self.jpeg, self.format)
        snap = camera_source_snapshot(state, 'original')
        settings = client_settings('/ws/camera?transport=binary-v1&quality_mode=original&resolution=1920x1080')
        budget = camera_stream_budget('auto', ack_window=3)
        with patch('scripts.camera_ws_bridge.cv2.imdecode', side_effect=AssertionError('must not decode')), patch('scripts.camera_ws_bridge.cv2.imencode', side_effect=AssertionError('must not reencode')):
            result = network_preview(snap, settings, budget, 'original')
        self.assertIs(result[0], self.jpeg)
        self.assertEqual(hashlib.sha256(result[0]).hexdigest(), snap['source_jpeg_sha256'])
        self.assertEqual(result[1:], (0., 32, 24, 32, 24))
        self.assertEqual(budget.ceiling, 4_000_000)
        self.assertEqual(camera_stream_budget('detail').values[:2], (1280, 720))

    def test_on_demand_and_last_close_release_payload(self):
        state = OriginalPreviewState()
        self.assertFalse(state.wanted())
        self.assertFalse(state.update('camera',1,0,self.jpeg,self.format))
        state.adjust_clients(1); self.assertTrue(state.wanted())
        state.update('camera',1,0,self.jpeg,self.format); self.assertIsNotNone(state.snapshot()['jpeg'])
        state.adjust_clients(-1)
        self.assertIsNone(state.snapshot()['jpeg']); self.assertIsNone(state.snapshot()['last_frame_age_sec'])
        self.assertFalse(state.update('camera',2,0,self.jpeg,self.format))
        self.assertEqual(original_subscription_policy(1,1,True),(True,False))
        self.assertEqual(original_subscription_policy(2,1,True),(True,True))
        self.assertEqual(original_subscription_policy(1,1,False),(True,True))
        self.assertEqual(original_subscription_policy(0,0,False),(False,True))

    def test_explicit_native_binary_only_and_numeric_quality_not_inferred(self):
        for url in ('/ws/camera','/ws/camera?quality_mode=original','/ws/camera?quality_mode=original&transport=binary-v1&resolution=1280x720'):
            self.assertEqual(client_settings(url)['quality_mode'],'target')
        self.assertEqual(original_jpeg_metadata(self.jpeg,self.format.replace('original_camera_mjpeg','reencoded_bgr_q90'))['jpeg_provenance'],'reencoded_bgr_q90')


if __name__ == '__main__': unittest.main()
