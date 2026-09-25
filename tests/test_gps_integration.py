"""Explicitly opt-in: run in a network-isolated ROS container, never live domain 0."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request


@unittest.skipUnless(os.environ.get('UBTS_GPS_INTEGRATION_TEST') == '1', 'opt-in isolated ROS integration test')
class GpsRecordingIntegration(unittest.TestCase):
    def test_four_sensor_rosbag_and_timeline(self):
        self.assertGreaterEqual(int(os.environ.get('ROS_DOMAIN_ID', '0')), 100, 'Use an isolated test domain')
        import rclpy
        from rclpy.node import Node
        from sensor_msgs.msg import LaserScan, Image, Imu, NavSatFix
        from std_msgs.msg import String
        import yaml
        from scripts.gps_ros2_driver import make_navsat
        from dashboard_server.gps import GPS_SCHEMA
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
            log=(root/'recorder.log').open('w')
            process=subprocess.Popen([sys.executable,'-m','scripts.data_recorder_service','--port',str(port),
                '--archive-dir',str(root/'recordings'),'--status-file',str(root/'status.json'),
                '--camera-preview-width','160','--camera-vision-wait-timeout-s','0'],stdout=log,stderr=subprocess.STDOUT)
            node=None
            def api(route, payload=None):
                request=urllib.request.Request(f'http://127.0.0.1:{port}{route}',
                    data=json.dumps(payload).encode() if payload is not None else None,
                    headers={'Content-Type':'application/json'})
                return json.load(urllib.request.urlopen(request,timeout=15))
            try:
                deadline=time.monotonic()+15
                while True:
                    try: api('/status'); break
                    except OSError:
                        if time.monotonic()>deadline: self.fail((root/'recorder.log').read_text())
                        time.sleep(.1)
                rclpy.init(); node=Node('synthetic_gps_recording_validation')
                specs=[('/scan',LaserScan),('/camera/image_raw',Image),('/imu/data_raw',Imu),('/gps/data',String),('/gps/fix',NavSatFix)]
                publishers={topic:node.create_publisher(cls,topic,10) for topic,cls in specs}
                result=api('/start',{'kind':'both'})
                self.assertTrue(result['ok'])
                recording_id=result['status']['recording_id']
                deadline=time.monotonic()+7
                while time.monotonic()<deadline:
                    stamp=node.get_clock().now().to_msg()
                    scan=LaserScan(); scan.header.stamp=stamp; scan.header.frame_id='laser'
                    scan.angle_min=-1.; scan.angle_increment=.1; scan.range_min=.1; scan.range_max=20.; scan.ranges=[5.]*20
                    camera=Image(); camera.header.stamp=stamp; camera.header.frame_id='camera_link_optical'
                    camera.width=160; camera.height=120; camera.encoding='bgr8'; camera.step=480; camera.data=bytes([30,80,40])*160*120
                    imu=Imu(); imu.header.stamp=stamp; imu.header.frame_id='imu_link'; imu.orientation.w=1.; imu.linear_acceleration.z=9.80665
                    gps=dict(schema=GPS_SCHEMA,valid=True,quality=1,latitude=35.57,longitude=129.19,
                        received_at=time.time(),stamp={'sec':stamp.sec,'nanosec':stamp.nanosec})
                    for topic,message in [('/scan',scan),('/camera/image_raw',camera),('/imu/data_raw',imu),
                                          ('/gps/data',String(data=json.dumps(gps))),('/gps/fix',make_navsat(gps,NavSatFix))]:
                        publishers[topic].publish(message)
                    rclpy.spin_once(node,timeout_sec=.01); time.sleep(.04)
                self.assertTrue(api('/stop',{})['ok'])
                archive=root/'recordings'/recording_id
                timeline=json.loads((archive/'timeline.json').read_text())
                counts={sensor:len(timeline[sensor]['frames']) for sensor in ('lidar','camera','imu','gps')}
                for sensor,count in counts.items(): self.assertGreater(count,0,sensor)
                for sensor in counts:
                    self.assertTrue(all(0<=frame['t_s']<=timeline['duration_s']+.1 for frame in timeline[sensor]['frames']))
                bag=yaml.safe_load((archive/'rosbag2/metadata.yaml').read_text())['rosbag2_bagfile_information']
                topics={item['topic_metadata']['name']:item['message_count'] for item in bag['topics_with_message_count']}
                for topic,_ in specs: self.assertGreater(topics.get(topic,0),0,topic)
                rows=(archive/'gps.jsonl').read_text().splitlines()
                self.assertEqual(len(rows),counts['gps'])
                print('ISOLATED_FOUR_SENSOR_RECORDING',json.dumps({'timeline':counts,'rosbag':topics}),flush=True)
            finally:
                process.send_signal(signal.SIGINT)
                try: process.wait(timeout=12)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=3)
                log.close()
                if node is not None: node.destroy_node(); rclpy.shutdown()


if __name__=='__main__': unittest.main()
