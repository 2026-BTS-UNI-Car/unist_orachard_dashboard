import json, struct, unittest
from types import SimpleNamespace
import numpy as np
from scripts.camera_ws_bridge import CameraState, camera_info_payload, client_settings, encode_binary_camera_frame, image_to_bgr, status_payload

class RawCameraTests(unittest.TestCase):

    def test_binary_camera_packet_removes_base64_and_preserves_metadata(self):
        metadata = {'type': 'frame', 'frame_count': 42, 'width': 640, 'height': 480}
        jpeg = b'\xff\xd8binary-jpeg\xff\xd9'
        packet = encode_binary_camera_frame(metadata, jpeg)
        self.assertEqual(packet[:4], b'UBCF')
        self.assertEqual(packet[4], 1)
        metadata_size = struct.unpack('!I', packet[5:9])[0]
        decoded = json.loads(packet[9:9 + metadata_size].decode('utf-8'))
        self.assertEqual(decoded, metadata)
        self.assertEqual(packet[9 + metadata_size:], jpeg)
        self.assertNotIn(b'base64', packet)

    def test_camera_client_settings_negotiate_binary_decode_ack(self):
        settings = client_settings('/ws/camera?resolution=640x480&fps=60&quality=70&transport=binary-v1&flow=decode-ack')
        self.assertEqual(settings['transport'], 'binary-v1')
        self.assertEqual(settings['flow'], 'decode-ack')
        self.assertEqual(settings['quality'], 70)

    def test_camera_client_settings_negotiate_two_credit_latest_ack(self):
        settings = client_settings('/ws/camera?resolution=1280x720&fps=30&transport=binary-v1&flow=latest-ack')
        self.assertEqual(settings['transport'], 'binary-v1')
        self.assertEqual(settings['flow'], 'latest-ack')

    def test_ros_camera_info_numpy_arrays_do_not_require_boolean_coercion(self):
        message = SimpleNamespace(header=SimpleNamespace(frame_id='camera_link_optical', stamp=SimpleNamespace(sec=123, nanosec=456)), width=1920, height=1080, k=np.array([1371.0, 0.0, 960.0, 0.0, 1371.0, 540.0, 0.0, 0.0, 1.0]), d=np.zeros(5), distortion_model='plumb_bob')
        payload = camera_info_payload(message)
        self.assertEqual(payload['k'], [1371.0, 0.0, 960.0, 0.0, 1371.0, 540.0, 0.0, 0.0, 1.0])
        self.assertEqual(payload['d'], [0.0] * 5)
        self.assertEqual(payload['stamp_ns'], 123000000456)

    def test_raw_rgb_padded_rows_and_no_overlay(self):
        msg = SimpleNamespace(width=1,height=2,step=4,encoding="rgb8",data=bytes([1,2,3,0,4,5,6,0]))
        bgr = image_to_bgr(msg)
        self.assertEqual(bgr.tolist(), [[[3,2,1]], [[6,5,4]]])
        state = CameraState()
        state.update_frame("camera_link_optical", 1, 2, bgr, "rgb8")
        self.assertNotIn("vision_overlay", state.snapshot())
        status = status_payload(state, image_topic="/camera/image_raw", compressed_topic="/camera/image_jpeg", camera_info_topic="/camera/camera_info")
        self.assertTrue(status["frame_topic_alive"])
        state.last_frame_at -= 5
        self.assertFalse(status_payload(state,image_topic="/camera/image_raw",compressed_topic="/camera/image_jpeg",camera_info_topic="/camera/camera_info")["frame_topic_alive"])
