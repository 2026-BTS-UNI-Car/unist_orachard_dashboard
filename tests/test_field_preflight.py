import json
from pathlib import Path
import tempfile
import unittest
from dashboard_server.field_preflight import report


class FieldPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'profile.json'
        self.profile=json.loads((Path(__file__).resolve().parents[1]/'config/field_autonomy_preparation.json').read_text())

    def tearDown(self):self.tmp.cleanup()

    def check(self,profile=Ellipsis,live=None):
        self.path.write_text(json.dumps(self.profile if profile is Ellipsis else profile),encoding='utf8')
        return report(self.path,live)

    def test_requested_preparation_does_not_invent_braking_or_extrinsics(self):
        r=self.check()
        for name in ('sensor_extrinsics','braking','physical_emergency_stop',
                     'current_vehicle_localization_validated','work_boundary_defined'):
            self.assertIn(name,r['blocking'])
        self.assertFalse(r['runtime_authority']);self.assertFalse(r['motor_command'])

    def test_filled_profile_never_grants_motor_authority(self):
        p=self.profile;p['body']['dimensions_measured']=True
        p['mount'].update(extrinsics_measured=True,planar_envelope_validated=True,
            laser_xy_in_base_m=[.2,0.],laser_yaw_in_base_rad=0.,imu_to_laser_rotation_rad=[0.,0.,0.])
        p['test_preferences'].update(body_velocity_feedback_calibrated=True,actuator_percent_limit=20)
        p['braking'].update(measured_stop_distance_m=.3,measured_stop_time_s=1.,
            tested_speed_m_s=.2,physical_emergency_stop_verified=True)
        p['qualification']={k:True for k in p['qualification']}
        live=dict(active=True,state='drawing',last_progress_age_s=.05,queue_scans=0,diagnostic=dict(accepted=True))
        r=self.check(live=live);self.assertEqual(r['blocking'],[])
        self.assertEqual(r['state'],'OBSERVATION_CHECKS_COMPLETE')
        self.assertFalse(r['autonomy_output_enabled']);self.assertFalse(r['runtime_authority'])
        p['braking']['tested_speed_m_s']=.1
        self.assertIn('braking',self.check(live=live)['blocking'])

    def test_expired_or_queued_map_is_not_current_feedback(self):
        for live in (dict(active=False),dict(active=True,state='drawing',last_progress_age_s=1.,queue_scans=0),
                     dict(active=True,state='drawing',last_progress_age_s=.01,queue_scans=4,diagnostic=dict(accepted=True))):
            self.assertIn('live_mapping_fresh',self.check(live=live)['blocking'])

    def test_missing_corrupt_and_bad_types_are_visible_without_crashing(self):
        self.assertIn('profile',report(self.path)['blocking'])
        self.path.write_text('{broken');self.assertIn('profile',report(self.path)['blocking'])
        for value in ([],True,None,dict(body=[],mount=True,test_preferences='x',braking=[1],qualification=42)):
            r=self.check(profile=value)
            self.assertIn('profile',r['blocking']);self.assertFalse(r['runtime_authority'])
        self.profile['mount']['extrinsics_measured']=True
        self.profile['mount']['laser_xy_in_base_m']=[float('nan'),0.]
        self.assertIn('sensor_extrinsics',self.check()['blocking'])


if __name__=='__main__':unittest.main()
