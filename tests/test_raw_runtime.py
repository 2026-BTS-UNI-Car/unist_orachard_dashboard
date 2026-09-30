from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]


class RawRuntimeTests(unittest.TestCase):
    def test_compose_raw_bridge_arguments_exist(self):
        text=(ROOT/'docker-compose.yml').read_text(encoding='utf-8')
        for service,script in [('ros2-camera-bridge','camera_ws_bridge'),('ros2-lidar-bridge','lidar_ws_bridge'),('ros2-imu-bridge','imu_ws_bridge'),('ros2-data-recorder','data_recorder_service')]:
            block=re.search(r'\n  '+service+r':.*?(?=\n  [a-z][a-z0-9-]*:|\Z)',text,re.S).group()
            command=block.split('    command:',1)[1]
            used=set(re.findall(r'--[a-z][a-z0-9-]*',command))
            source=(ROOT/'scripts'/f'{script}.py').read_text(encoding='utf-8')
            defined=set(re.findall(r'"(--[a-z][a-z0-9-]*)"',source))
            self.assertFalse(used-defined,(service,used-defined))
        self.assertNotIn('vision-ai-worker',text)
        self.assertNotIn('ORCHARD_VISION',text)
        self.assertNotIn('IMU_BODY_YAW_OFFSET',text)
        self.assertNotIn('IMU_CALIBRATION',text)
        self.assertNotIn('IMU_STATIONARY',text)
        self.assertNotIn('--corrected-topic',text)

    def test_real_mode_ui_remains_the_default(self):
        html=(ROOT/'static/index.html').read_text(encoding='utf-8')
        self.assertIn('data-runtime-mode="live"',html)
        self.assertIn('<title>UBTS Dashboard</title>',html)
        self.assertIn('<div hidden="" class="simulation-banner"',html)
        self.assertIn('id="roverArmButton"',html)
        self.assertNotIn('id="roverImuZeroButton"',html)
        self.assertNotIn('id="roverOrchardMapCanvas"',html)


if __name__=='__main__':unittest.main()
