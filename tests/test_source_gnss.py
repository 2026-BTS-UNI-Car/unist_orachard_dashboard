import unittest
import numpy as np
from dashboard_server.raw_mapping.gnss_constraints import UniqueGNSS,GNSSFactor,robust_rigid_alignment,smooth_position_constraints

def sample(stamp=10,epoch='2026-10-03T07:23:30+00:00',hacc=4):
    return dict(valid=True,latitude=35.,longitude=129.,h_acc_m=hacc,fix_type='autonomous',gps_time=epoch,stamp=dict(sec=stamp,nanosec=0),course_deg=90.)

class GNSSTests(unittest.TestCase):
    def test_duplicate_receiver_epoch_and_host_utc_offset(self):
        store=UniqueGNSS();first=store.add(sample(100));self.assertEqual(first.stamp,100)
        self.assertIsNone(store.add(sample(101)))
        self.assertEqual(len(store.accepted),1);self.assertEqual(store.rejected['duplicate_receiver_epoch'],1)
        np.testing.assert_allclose(first.xy,[0,0],atol=1e-8)
        self.assertGreater(first.scale_m,4)
    def test_missing_accuracy_and_receiver_epoch_do_not_become_perfect_fixes(self):
        for change in ({'h_acc_m':0},{'h_acc_m':None},{'h_acc_m':True},{'gps_time':None},{'valid':False}):
            s=sample();s.update(change);self.assertIsNone(UniqueGNSS().add(s))
    def test_stationary_noisy_GPS_cannot_establish_heading(self):
        factors=[GNSSFactor(i,str(i),(i*.2,0),4,4,'autonomous') for i in range(10)]
        fit=robust_rigid_alignment(np.zeros((10,2)),factors)
        self.assertFalse(fit['accepted']);self.assertIn('baseline',fit['reason'])
    def test_rigid_fit_keeps_scale_and_graph_dedupes_soft_constraints(self):
        local=np.column_stack((np.linspace(0,100,21),np.sin(np.linspace(0,3,21))*10))
        theta=.5;r=np.array([[np.cos(theta),-np.sin(theta)],[np.sin(theta),np.cos(theta)]])
        target=local@r.T+[20,-30]
        factors=[GNSSFactor(float(i),str(i),tuple(xy),4,4,'autonomous') for i,xy in enumerate(target)]
        fit=robust_rigid_alignment(local,factors);self.assertTrue(fit['accepted'])
        self.assertAlmostEqual(fit['yaw_rad'],theta,places=6)
        np.testing.assert_allclose(local@fit['rotation'].T+fit['translation'],target,atol=1e-8)
        corrected,proof=smooth_position_constraints(np.arange(21),target+[2,1],factors+factors)
        self.assertEqual(proof['used_factors'],21)
        self.assertLess(np.linalg.norm(corrected-target),np.linalg.norm(target+[2,1]-target))
        self.assertTrue(proof['poses_require_map_rebuild']);self.assertFalse(proof['autonomy_eligible'])
    def test_graph_rejects_clock_reset(self):
        with self.assertRaises(ValueError):smooth_position_constraints([10,9],[[0,0],[1,0]],[])
    def test_exact_zero_axis_is_a_converged_solution(self):
        poses=np.column_stack((np.arange(10),np.zeros(10)))
        factors=[GNSSFactor(i,str(i),tuple(xy),4,4,'autonomous') for i,xy in enumerate(poses)]
        corrected,proof=smooth_position_constraints(np.arange(10),poses,factors)
        np.testing.assert_allclose(corrected,poses,atol=1e-7)
        self.assertEqual(proof['used_factors'],10)

if __name__=='__main__':unittest.main()
