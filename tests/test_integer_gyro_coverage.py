"""Original-header coverage boundaries, integration equality and all fallbacks."""
import copy
import importlib.util
import json
import math
import os
import sys
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import numpy as np
from scipy.spatial.transform import Rotation

candidate=__import__('importlib').import_module(os.environ.get('UBTS_LOCALIZATION_MODULE',
    'dashboard_server.raw_mapping.continuous_localization'))
GOLDEN={0: [['0x0.0p+0', '0x0.0p+0', '0x1.6666666666666p-1', '0x0.0p+0'], ['0x1.24dd2e5616179p-5', '0x1.99999886b8db2p-5', '0x1.75c28f5c28f5cp-1', '0x1.99999886b8db2p-5'], ['0x1.2b020b81112f5p-4', '0x1.99999886b8db2p-4', '0x1.851eb851eb852p-1', '0x1.99999886b8db2p-4'], ['0x1.7a5e344191ccfp-4', '0x1.fffffea86711ep-4', '0x1.947ae147ae147p-1', '0x1.fffffea86711ep-4'], ['0x1.7a5e344191ccfp-4', '0x1.fffffea86711ep-4', '0x1.a3d70a3d70a3dp-1', '0x1.999999aac7a59p-2'], ['0x1.12b02045a6159p-3', '0x1.666665ba99ef5p-3', '0x1.b333333333333p-1', '0x1.ccccccddfad8cp-2']], 53058706210985: [['0x0.0p+0', '0x0.0p+0', '0x1.6666666666666p-1', '0x1.9e8569947c795p+15'], ['0x1.24dd2e5648f5cp-5', '0x1.9999988700000p-5', '0x1.75c28f5c28f5cp-1', '0x1.9e85832e1601cp+15'], ['0x1.2b020b80e5c29p-4', '0x1.9999988680000p-4', '0x1.851eb851eb852p-1', '0x1.9e859cc7af8a2p+15'], ['0x1.7a5e3441a5c29p-4', '0x1.fffffea880000p-4', '0x1.947ae147ae147p-1', '0x1.9e85a9947c4e6p+15'], ['0x1.7a5e3441a5c29p-4', '0x1.fffffea880000p-4', '0x1.a3d70a3d70a3dp-1', '0x1.9e863661494ebp+15'], ['0x1.12b02045c570ap-3', '0x1.666665bac0000p-3', '0x1.b333333333333p-1', '0x1.9e864ffae2e85p+15']], 1791444500123456789: [['0x0.0p+0', '0x0.0p+0', '0x1.6666666666666p-1', '0x1.ab1d18507e6b7p+30'], ['0x1.24dd1cccccccdp-5', '0x1.9999800000000p-5', '0x1.75c28f5c28f5cp-1', '0x1.ab1d1850b19eap+30'], ['0x1.2b022947ae148p-4', '0x1.9999c00000000p-4', '0x1.851eb851eb852p-1', '0x1.ab1d1850e4d1ep+30'], ['0x1.7a5e347ae147bp-4', '0x1.0000000000000p-3', '0x1.947ae147ae147p-1', '0x1.ab1d1850fe6b7p+30'], ['0x1.7a5e347ae147bp-4', '0x1.0000000000000p-3', '0x1.a3d70a3d70a3dp-1', '0x1.ab1d185218051p+30'], ['0x1.12b01b0a3d70ap-3', '0x1.6666600000000p-3', '0x1.b333333333333p-1', '0x1.ab1d18524b384p+30']]}
EXACT200={0: ['0x1.1eb851eb851ebp-3', '0x1.999999999999ap-3'], 53058706210985: ['0x1.1eb851eb73333p-3', '0x1.9999999980000p-3'], 1791444500123456789: ['0x0.0p+0', '0x0.0p+0']}

def imu(ns,rate=.7,tilt=.4):
    rotation=Rotation.from_euler('x',tilt)
    w=rotation.inv().apply([0.,0.,rate])
    return dict(header=dict(stamp=dict(sec=ns//10**9,nanosec=ns%10**9),frame_id='imu_link'),
        orientation=dict(zip('xyzw',rotation.as_quat())),angular_velocity=dict(zip('xyz',w)),
        orientation_covariance=[0.]*9,angular_velocity_covariance=[0.]*9,
        linear_acceleration=dict(x=0.,y=0.,z=9.80665))

def scan(ns):
    return dict(header=dict(stamp=dict(sec=ns//10**9,nanosec=ns%10**9),frame_id='laser'),
        ranges=[5.]*3200,angle_min=-math.pi,angle_increment=2*math.pi/3200,
        range_min=.05,range_max=30.,scan_time=.1,time_increment=0.)

def fit(yaw=0.,strong=True):
    return dict(accepted=True,xy=np.array([.08,.01]),yaw=yaw,coverage=.9 if strong else .6,
        rmse_m=.01,inliers=200,information_eigenvalues=[.01,.1,.2])

SURFACE=types.SimpleNamespace(points=np.zeros((240,2)),dense=np.zeros((3200,2)),denominator=240)

def current_stamp(engine,ns):
    engine._scan_header_ns=ns
    engine._scan_stamp_for_ns=ns//10**9+(ns%10**9)*1e-9
    return engine._scan_stamp_for_ns

def original_interval(module=candidate,start=53_058_706_210_985,covered=99_999_996,interval=124_999_995):
    engine=module.ContinuousLocalization();engine.imu(imu(start))
    reference=dict(stamp=module.source_stamp(scan(start)),gyro=engine.gyro_integral,
        gyro_valid=engine.gyro_valid_s,stamp_ns=start,gyro_valid_ns=getattr(engine,'gyro_valid_ns',0),
        q=module.quaternion(imu(start)),pose=np.zeros(3),surfaces=SURFACE)
    engine.imu(imu(start+covered//2));engine.imu(imu(start+covered))
    stamp=current_stamp(engine,start+interval)
    return engine,reference,stamp

class IntegerCoverageTests(unittest.TestCase):
    def test_actual_exact_80_percent_corrects_float_false_negative(self):
        engine,ref,stamp=original_interval()
        self.assertLess(engine.gyro_valid_s-ref['gyro_valid'],(stamp-ref['stamp'])*.8)
        self.assertEqual(5*(engine.gyro_valid_ns-ref['gyro_valid_ns']),4*(engine._scan_header_ns-ref['stamp_ns']))
        self.assertTrue(engine._gyro_covered(ref,stamp))

    def test_one_native_nanosecond_below_boundary_is_not_covered(self):
        engine,ref,stamp=original_interval(covered=99_999_995)
        self.assertFalse(engine._gyro_covered(ref,stamp))

    def test_real_gap_above_200ms_does_not_count(self):
        engine=candidate.ContinuousLocalization();start=1_000_000_000
        engine.imu(imu(start));engine.imu(imu(start+200_000_001))
        self.assertEqual(engine.gyro_valid_ns,0)
        self.assertEqual(engine.gyro_valid_s,0.)

    def test_duplicate_reversed_and_large_gap_are_not_counted_as_elapsed_span(self):
        e=candidate.ContinuousLocalization();start=10**9
        for offset in (0,50_000_000,50_000_000,49_000_000,350_000_001,400_000_001):
            e.imu(imu(start+offset))
        self.assertEqual(e.gyro_valid_ns,100_000_000)
        self.assertNotEqual(e.gyro_valid_ns,e.gyro_time_ns-start)

    def test_unknown_header_interval_is_not_inferred_from_surrounding_good_headers(self):
        e=candidate.ContinuousLocalization();start=10**9;e.imu(imu(start))
        message=imu(start+50_000_000);message['header']['stamp']['sec']=1.0;e.imu(message)
        e.imu(imu(start+100_000_000));e.imu(imu(start+150_000_000))
        self.assertEqual(e.gyro_valid_ns,50_000_000)
        self.assertEqual(e.gyro_time_ns,start+150_000_000)

    def test_exact_200ms_preserves_original_float_integration_gate(self):
        for start in (0,53_058_706_210_985,1_791_444_500_123_456_789):
            new=candidate.ContinuousLocalization()
            for ns in (start,start+200_000_000):new.imu(imu(ns))
            self.assertEqual([new.gyro_integral.hex(),new.gyro_valid_s.hex()],EXACT200[start])
            self.assertEqual(new.gyro_valid_ns,200_000_000 if float.fromhex(EXACT200[start][1]) else 0)

    def test_large_UTC_exact_header_is_never_rebuilt_from_float(self):
        start=1_791_444_500_123_456_789
        engine,ref,stamp=original_interval(start=start)
        self.assertNotEqual(round(ref['stamp']*10**9),start)
        self.assertEqual(engine.gyro_valid_ns,99_999_996)
        self.assertTrue(engine._gyro_covered(ref,stamp))

    def test_numeric_projected_rate_and_trapezoid_match_legacy_outputs(self):
        for start in (0,53_058_706_210_985,1_791_444_500_123_456_789):
            new=candidate.ContinuousLocalization()
            originals=[]
            for i,offset in enumerate((0,49_999_998,99_999_996,124_999_995,400_000_001,450_000_001)):
                message=imu(start+offset,rate=.7+i*.03,tilt=.4)
                originals.append(copy.deepcopy(message));new.imu(message)
                values=[getattr(new,key).hex() for key in ('gyro_integral','gyro_valid_s','gyro_rate','gyro_time')]
                # SciPy rotation kernels differ by an ULP between platforms.
                # Native time/coverage remain exact; rate/integral use a tight
                # numerical baseline. Same-platform bitwise parity is checked
                # separately against the original implementation.
                for index in (1,3):self.assertEqual(values[index],GOLDEN[start][i][index])
                for index in (0,2):
                    self.assertAlmostEqual(float.fromhex(values[index]),
                        float.fromhex(GOLDEN[start][i][index]),delta=1e-14)
                self.assertEqual(originals[-1],message)

    def test_invalid_quaternion_rate_covariance_reversed_do_not_advance(self):
        e=candidate.ContinuousLocalization();start=10**9;e.imu(imu(start))
        for field,value in (('orientation',dict(x=0.,y=0.,z=0.,w=0.)),
                            ('angular_velocity',dict(x=float('nan'),y=0.,z=0.)),
                            ('angular_velocity_covariance',[-1.]+[0.]*8)):
            message=imu(start+50_000_000);message[field]=value;e.imu(message)
            self.assertEqual((e.gyro_time_ns,e.gyro_valid_ns),(start,0))
        e.imu(imu(start-1));self.assertEqual((e.gyro_time_ns,e.gyro_valid_ns),(start,0))

    def test_missing_or_malformed_header_cannot_gain_integer_coverage(self):
        e=candidate.ContinuousLocalization();e.imu(imu(1_000_000_000))
        bad=imu(1_050_000_000);bad['header']['stamp']['sec']=1.0;e.imu(bad)
        self.assertIsNone(e.gyro_time_ns);self.assertEqual(e.gyro_valid_ns,0)
        e.imu(imu(1_100_000_000));self.assertEqual(e.gyro_valid_ns,0)
        stamp=current_stamp(e,1_125_000_000)
        ref=dict(stamp=1.,gyro_valid=0.,stamp_ns=1_000_000_000,gyro_valid_ns=0)
        self.assertFalse(e._gyro_covered(ref,stamp))
        for partial in (dict(stamp=1.,gyro_valid=0.,stamp_ns=1_000_000_000),
                        dict(stamp=1.,gyro_valid=0.,gyro_valid_ns=0),dict(stamp=1.,gyro_valid=0.)):
            self.assertFalse(e._gyro_covered(partial,stamp))

    def test_entirely_metadata_absent_manual_helpers_keep_legacy_float(self):
        e=candidate.ContinuousLocalization();e.gyro_valid_s=.8
        self.assertTrue(e._gyro_covered(dict(stamp=0.,gyro_valid=0.),1.))
        e.gyro_valid_s=.7999999999
        self.assertFalse(e._gyro_covered(dict(stamp=0.,gyro_valid=0.),1.))

    def test_add_reference_retains_exact_ns_and_counter_even_trace_off(self):
        e=candidate.ContinuousLocalization();start=1_791_444_500_123_456_789;m=imu(start)
        with patch.object(candidate,'surfaces',return_value=SURFACE):e.imu(m);e.add(scan(start),m)
        self.assertEqual(e.previous['stamp_ns'],start)
        self.assertEqual(e.previous['gyro_valid_ns'],0)
        self.assertNotIn('scalar_trace',e.previous)

    def test_fit_prior_signed_check_and_bridge_use_exact_same_boundary(self):
        e,ref,stamp=original_interval();e.previous=ref;e.direction=np.array([1.,0.])
        e.gps_speed=(stamp,1.);e.last_geometry_stamp=stamp
        gyro=e.gyro_integral-ref['gyro']
        with patch.object(candidate,'fit_surfaces',return_value=fit(gyro)) as solve:
            self.assertIsNotNone(e._fit(ref,SURFACE,ref['q'],stamp))
        prior=solve.call_args.kwargs['yaw_prior'];self.assertIsNotNone(prior)
        self.assertEqual(prior[0],gyro)
        self.assertEqual(prior[1],math.radians(.35+.3*min(stamp-ref['stamp'],10)))
        self.assertFalse(e._signed_gyro_consistent(ref,stamp,fit(gyro+math.radians(2.001))))
        self.assertTrue(e._signed_gyro_consistent(ref,stamp,fit(gyro+math.radians(1.999))))
        self.assertIsNotNone(e._motion_bridge(stamp))

    def test_dense_wrong_signed_candidate_rejected_and_partial_uses_exact_coverage(self):
        start=53_058_706_210_985;e=candidate.ContinuousLocalization()
        with ExitStack() as s:
            s.enter_context(patch.object(candidate,'surfaces',return_value=SURFACE))
            s.enter_context(patch.object(e,'_fit',return_value=None))
            s.enter_context(patch.object(candidate,'registration',return_value={'accepted':False}))
            s.enter_context(patch.object(candidate,'point_support_registration',return_value=fit(-.5)))
            partial=s.enter_context(patch.object(candidate,'proposal',return_value=fit(.7*.099999996,strong=False)))
            s.enter_context(patch.object(e,'_recover',return_value=None))
            s.enter_context(patch.object(e,'_motion_bridge',return_value=None))
            m=imu(start);e.imu(m);e.add(scan(start),m)
            e.imu(imu(start+49_999_998));m=imu(start+99_999_996);e.imu(m)
            r=e.add(scan(start+124_999_995),m)
        partial.assert_called_once();self.assertEqual(r['reason'],'partial_overlap_with_gyro_validation')

if __name__=='__main__':unittest.main()
